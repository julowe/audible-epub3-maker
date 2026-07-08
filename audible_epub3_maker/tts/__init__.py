def create_tts_engine(tts_name: str):
    tts_name = tts_name.lower()
    
    if "azure" == tts_name:
        from audible_epub3_maker.tts.azure_tts import AzureTTS
        return AzureTTS()
    
    elif "kokoro" == tts_name:
        from audible_epub3_maker.tts.kokoro_tts import KokoroTTS
        return KokoroTTS()
    
    elif "edge_tts" == tts_name:
        from audible_epub3_maker.tts.edge_tts_engine import EdgeTTS
        return EdgeTTS()
    
    else:
        raise ValueError(f"Unsupported TTS engine: {tts_name}")