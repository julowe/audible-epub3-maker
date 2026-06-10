import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from edge_tts.exceptions import WebSocketError

from audible_epub3_maker.tts.edge_tts_engine import EdgeTTS, get_langs_voices_edge_tts
from audible_epub3_maker.utils.types import TTSEmptyContentError


def test_get_langs_voices_edge_tts_happy_path() -> None:
    voice_output = (
        "Name: en-US-AriaNeural\n"
        "Gender: Female\n\n"
        "Name: en-US-GuyNeural\n"
        "Gender: Male\n\n"
        "Name: fr-FR-DeniseNeural\n"
        "Gender: Female\n"
    )
    with patch("audible_epub3_maker.tts.edge_tts_engine.subprocess.run", return_value=Mock(stdout=voice_output)):
        langs_voices = get_langs_voices_edge_tts()

    assert langs_voices == {
        "en-US": ["en-US-AriaNeural", "en-US-GuyNeural"],
        "fr-FR": ["fr-FR-DeniseNeural"],
    }


def test_get_langs_voices_edge_tts_handles_cli_error() -> None:
    with patch(
        "audible_epub3_maker.tts.edge_tts_engine.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "edge_tts"),
    ):
        with pytest.raises(RuntimeError, match="Failed to load Edge TTS voices"):
            get_langs_voices_edge_tts()


def test_get_langs_voices_edge_tts_handles_malformed_cli_output() -> None:
    with patch("audible_epub3_maker.tts.edge_tts_engine.subprocess.run", return_value=Mock(stdout="Gender: Female\n")):
        with pytest.raises(RuntimeError, match="Failed to parse Edge TTS voice list output"):
            get_langs_voices_edge_tts()


def test_edge_tts_html_to_speech_happy_path(tmp_path: Path) -> None:
    class FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str):
            self._text = text
            self._voice = voice
            self._rate = rate

        async def stream(self):
            yield {"type": "audio", "data": b"MP3-DATA"}
            yield {"type": "WordBoundary", "offset": 10000, "duration": 5000, "text": "Hello"}

    output_file = tmp_path / "out.mp3"
    with patch("audible_epub3_maker.tts.edge_tts_engine.edge_tts_lib.Communicate", new=FakeCommunicate):
        wbs = EdgeTTS().html_to_speech("<p>Hello world</p>", output_file)

    assert output_file.read_bytes() == b"MP3-DATA"
    assert len(wbs) == 1
    assert wbs[0].text == "Hello"
    assert wbs[0].start_ms == 1.0
    assert wbs[0].end_ms == 1.5


def test_edge_tts_html_to_speech_raises_for_empty_text(tmp_path: Path) -> None:
    with pytest.raises(TTSEmptyContentError):
        EdgeTTS().html_to_speech("<p>   </p>", tmp_path / "empty.mp3")


def test_edge_tts_html_to_speech_handles_websocket_failure(tmp_path: Path) -> None:
    class FailingAsyncIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise WebSocketError("connection failed")

    class FailingCommunicate:
        def __init__(self, text: str, voice: str, rate: str):
            self._text = text
            self._voice = voice
            self._rate = rate

        def stream(self):
            return FailingAsyncIterator()

    with patch("audible_epub3_maker.tts.edge_tts_engine.edge_tts_lib.Communicate", new=FailingCommunicate):
        with pytest.raises(RuntimeError, match="Edge TTS synthesis failed"):
            EdgeTTS().html_to_speech("<p>Hello world</p>", tmp_path / "fail.mp3")
