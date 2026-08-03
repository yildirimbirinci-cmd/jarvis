from pathlib import Path


def test_assistant_registers_voice_output_commands_and_routes_speech() -> None:
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    assert 'TtsOutputRouter(self.config, self.voice)' in source
    assert 'tts_output_outside' in source
    assert 'tts_output_inside' in source
    assert 'self.tts_output_router.active_output_index()' in source


def test_config_keeps_separate_input_and_output_profiles() -> None:
    source = (Path(__file__).resolve().parents[1] / "config.py").read_text(encoding="utf-8")
    assert 'voice_output_mode: str = "inside"' in source
    assert 'voice_output_inside_name: str = ""' in source
    assert 'voice_output_outside_name: str = ""' in source
    assert 'voice_microphone_name: str = ""' in source
