import sys
import threading
import time

from artmach_assistant.core import voice_service


def test_low_acoustic_wake_score_reaches_word_confirmation(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "wake.json"
    profile.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(voice_service, "WAKE_WORD_PROFILE_FILE", profile)
    service = voice_service.VoiceService()
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"sample")
    service.record_utterance_wav = lambda *args, **kwargs: sample
    service._wake_signature = lambda _path: service._numpy().asarray([1.0, 0.0])
    monkeypatch.setattr(
        voice_service,
        "_read_voice_profile",
        lambda _path: {
            "threshold": 0.75,
            "templates": [[0.47, 0.88], [0.47, -0.88], [0.47, 0.20]],
        },
    )

    accepted, score = service.listen_for_local_wake(None, 1.5)

    assert accepted is True
    assert score < 0.54


def test_whisper_model_load_is_shared_between_threads(monkeypatch) -> None:
    created = []

    class Model:
        def __init__(self, *_args, **_kwargs):
            time.sleep(0.02)
            created.append(self)

        def transcribe(self, *_args, **_kwargs):
            return [], None

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        type("Module", (), {"WhisperModel": Model})(),
    )
    service = voice_service.VoiceService()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(service._whisper_model("base")))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert len({id(item) for item in results}) == 1


def test_strong_enrolled_wake_does_not_depend_on_whisper(tmp_path) -> None:
    service = voice_service.VoiceService()
    sample = tmp_path / "wake.wav"
    sample.write_bytes(b"sample")
    service.last_utterance_path = sample
    service._last_wake_score = 0.72
    service._last_wake_strong = True
    service.recognize_wav = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("strong enrolled wake must not call Whisper")
    )

    accepted, heard = service.confirm_local_wake(("jarvis",), "tr-TR", "base")

    assert accepted is True
    assert heard == "jarvis"


def test_windows_wdm_ks_outputs_are_hidden() -> None:
    rows = [
        voice_service.OutputDeviceInfo(1, "Hoparlör", 2, "Windows WDM-KS"),
        voice_service.OutputDeviceInfo(2, "Hoparlör", 2, "Windows WASAPI"),
    ]

    selected = voice_service.VoiceService._unique_audio_devices(rows)

    assert [item.index for item in selected] == [2]
