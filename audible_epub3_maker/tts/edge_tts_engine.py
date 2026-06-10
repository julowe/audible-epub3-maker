import asyncio
import logging
import subprocess
import sys
from pathlib import Path

import edge_tts as edge_tts_lib
from edge_tts.exceptions import NoAudioReceived, UnexpectedResponse, UnknownResponse, WebSocketError
from bs4 import BeautifulSoup

from audible_epub3_maker.config import settings
from audible_epub3_maker.tts.base_tts import BaseTTS
from audible_epub3_maker.segmenter import html_segmenter, text_segmenter
from audible_epub3_maker.utils.constants import BEAUTIFULSOUP_PARSER
from audible_epub3_maker.utils.types import WordBoundary, TTSEmptyAudioError, TTSEmptyContentError

logger = logging.getLogger(__name__)
EDGE_TTS_TIME_UNIT_TO_MS = 10000.0


def _parse_edge_tts_voice_output(raw_output: str) -> dict[str, list[str]]:
    """Parse `edge_tts --list-voices` text output into language -> voices map."""
    langs_voices: dict[str, set[str]] = {}
    current_voice_name: str = ""

    def flush_current_voice() -> None:
        nonlocal current_voice_name
        voice_name = current_voice_name.strip()
        current_voice_name = ""
        if not voice_name:
            return

        # edge-tts voice names use the format: <lang>-<region>-<voice>
        # e.g. en-US-AriaNeural
        parts = voice_name.split("-")
        if len(parts) < 3:
            return

        locale = "-".join(parts[:2])
        langs_voices.setdefault(locale, set()).add(voice_name)

    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_current_voice()
            continue

        if stripped.startswith("Name:"):
            current_voice_name = stripped.split(":", 1)[1].strip()

    flush_current_voice()

    if not langs_voices:
        raise ValueError("No usable voices were found in Edge TTS --list-voices output.")

    return {
        lang: sorted(voices)
        for lang, voices in sorted(langs_voices.items())
    }


def get_langs_voices_edge_tts() -> dict[str, list[str]]:
    """Return Edge TTS supported languages and voices using `--list-voices` CLI output."""
    cmd = [sys.executable, "-m", "edge_tts", "--list-voices"]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError("Edge TTS is not installed. Please install edge-tts and try again.") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Failed to load Edge TTS voices. Please check your network connection and try again."
        ) from e

    try:
        return _parse_edge_tts_voice_output(result.stdout)
    except ValueError as e:
        raise RuntimeError("Failed to parse Edge TTS voice list output.") from e


class EdgeTTS(BaseTTS):
    """Edge TTS backend wrapper."""

    @staticmethod
    def _speed_to_rate(speed: float) -> str:
        percent = int(round((speed - 1.0) * 100))
        sign = "+" if percent >= 0 else ""
        return f"{sign}{percent}%"

    @staticmethod
    def _extract_plain_text(html_text: str) -> str:
        soup = BeautifulSoup(html_text, BEAUTIFULSOUP_PARSER)
        break_map = {
            "h1": "_#BRK#",
            "h2": "_#BRK#",
            "h3": "_#BRK#",
            "h4": "_#BRK#",
            "h5": "_#BRK#",
            "h6": "_#BRK#",
            "li": "_#BRK#",
            "p": "_#BRK#",
        }
        html_segmenter.bs_append_suffix_to_tags(soup, break_map)
        body_text = soup.body.get_text() if soup.body else soup.get_text()
        normalized = text_segmenter.normalize_newlines(body_text, settings.newline_mode)
        return normalized.replace("_#BRK#", "\n").strip()

    @staticmethod
    async def _synthesize(text: str) -> tuple[bytes, list[WordBoundary]]:
        communicator = edge_tts_lib.Communicate(
            text=text,
            voice=settings.tts_voice,
            rate=EdgeTTS._speed_to_rate(settings.tts_speed),
        )

        audio = bytearray()
        word_boundaries: list[WordBoundary] = []
        async for chunk in communicator.stream():
            chunk_type = chunk.get("type")
            if chunk_type == "audio":
                audio.extend(chunk.get("data", b""))
            elif chunk_type == "WordBoundary":
                offset = float(chunk.get("offset", 0.0)) / EDGE_TTS_TIME_UNIT_TO_MS
                duration = float(chunk.get("duration", 0.0)) / EDGE_TTS_TIME_UNIT_TO_MS
                text_token = str(chunk.get("text", "")).strip()
                if text_token:
                    word_boundaries.append(
                        WordBoundary(
                            start_ms=offset,
                            end_ms=offset + duration,
                            text=text_token,
                        )
                    )

        return bytes(audio), word_boundaries

    def html_to_speech(self, html_text: str, output_file: Path, metadata: dict | None = None) -> list[WordBoundary]:
        """Convert HTML content to speech audio and return word boundaries."""
        del metadata

        output_file = Path(output_file)
        plain_text = self._extract_plain_text(html_text)
        if not plain_text:
            raise TTSEmptyContentError("Input HTML contains no valid text content.")

        try:
            audio_data, word_boundaries = asyncio.run(self._synthesize(plain_text))
        except (NoAudioReceived, WebSocketError, UnexpectedResponse, UnknownResponse) as e:
            raise RuntimeError(
                "Edge TTS synthesis failed. Please verify the selected language/voice and try again."
            ) from e
        except ValueError as e:
            raise RuntimeError(
                "Edge TTS received invalid synthesis settings. Please verify language, voice, and speed values."
            ) from e

        if not audio_data:
            raise TTSEmptyAudioError("TTS returned empty or invalid audio data.")

        output_file.write_bytes(audio_data)
        return word_boundaries
