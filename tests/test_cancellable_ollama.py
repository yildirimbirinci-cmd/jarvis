from __future__ import annotations

import json
import threading
import time

import pytest

from artmach_assistant.core import cancellable_ollama
from artmach_assistant.core.cancellable_ollama import OllamaProtocolError, chat


class _Response:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = list(lines)
        self.closed = False

    def readline(self) -> bytes:
        if self.lines:
            return self.lines.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


class _BlockingResponse:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()
        self.closed = False

    def readline(self) -> bytes:
        self.started.set()
        self.released.wait(5.0)
        return b""

    def close(self) -> None:
        self.closed = True
        self.released.set()


class _SizedResponse:
    def __init__(self, line: bytes) -> None:
        self.line = line
        self.requested_sizes: list[int] = []
        self.closed = False

    def readline(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        if not self.line:
            return b""
        line, self.line = self.line[:size], self.line[size:]
        return line

    def close(self) -> None:
        self.closed = True


class _FailingResponse:
    def __init__(self) -> None:
        self.closed = False

    def readline(self, _size: int = -1) -> bytes:
        raise OSError("connection reset")

    def close(self) -> None:
        self.closed = True


def _line(content: str, *, done: bool = False, reason: str = "") -> bytes:
    row = {"message": {"content": content}, "done": done}
    if reason:
        row["done_reason"] = reason
    return (json.dumps(row) + "\n").encode("utf-8")


def test_streaming_chat_combines_chunks_and_reports_progress(monkeypatch) -> None:
    response = _Response([_line("Mer"), _line("haba", done=True, reason="stop")])
    captured = {}
    progress: list[int] = []

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(cancellable_ollama.urllib.request, "urlopen", urlopen)
    result = chat(
        "http://127.0.0.1:11434",
        {"model": "local", "messages": []},
        timeout=20,
        progress_callback=progress.append,
    )

    sent = json.loads(captured["request"].data.decode("utf-8"))
    assert sent["stream"] is True
    assert result.content == "Merhaba"
    assert result.done_reason == "stop"
    assert result.chunks == 2
    assert progress == [3, 7]
    assert response.closed is True


def test_cancellation_closes_blocked_stream_before_first_token(monkeypatch) -> None:
    response = _BlockingResponse()
    cancelled = threading.Event()
    monkeypatch.setattr(
        cancellable_ollama.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )

    def cancel_soon() -> None:
        assert response.started.wait(1.0)
        cancelled.set()

    thread = threading.Thread(target=cancel_soon, daemon=True)
    thread.start()
    started = time.monotonic()
    with pytest.raises(InterruptedError, match="iptal"):
        chat(
            "http://127.0.0.1:11434",
            {"model": "local", "messages": []},
            timeout=20,
            cancel_check=cancelled.is_set,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert response.closed is True
    thread.join(timeout=1.0)


def test_invalid_jsonl_is_rejected(monkeypatch) -> None:
    response = _Response([b"not-json\n"])
    monkeypatch.setattr(
        cancellable_ollama.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )
    with pytest.raises(OllamaProtocolError, match="JSONL"):
        chat(
            "http://127.0.0.1:11434",
            {"model": "local", "messages": []},
            timeout=5,
        )


def test_response_byte_limit_is_enforced(monkeypatch) -> None:
    response = _Response([_line("x" * 1500, done=True)])
    monkeypatch.setattr(
        cancellable_ollama.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )
    with pytest.raises(OllamaProtocolError, match="boyut sınırını"):
        chat(
            "http://127.0.0.1:11434",
            {"model": "local", "messages": []},
            timeout=5,
            max_response_bytes=1024,
        )


def test_single_jsonl_record_is_read_with_a_hard_byte_ceiling(monkeypatch) -> None:
    response = _SizedResponse(_line("x" * 1500, done=True))
    monkeypatch.setattr(
        cancellable_ollama.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(OllamaProtocolError, match="boyut sınırını"):
        chat(
            "http://127.0.0.1:11434",
            {"model": "local", "messages": []},
            timeout=5,
            max_response_bytes=1024,
        )

    assert response.requested_sizes[0] == 1025
    assert response.closed is True


def test_reader_io_error_is_wrapped_without_leaking_backend_exception(monkeypatch) -> None:
    response = _FailingResponse()
    monkeypatch.setattr(
        cancellable_ollama.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )

    with pytest.raises(RuntimeError, match="akışı okunamadı") as error:
        chat(
            "http://127.0.0.1:11434",
            {"model": "local", "messages": []},
            timeout=5,
        )

    assert isinstance(error.value.__cause__, OSError)
    assert response.closed is True
