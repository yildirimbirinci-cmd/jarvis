"""Bounded, cooperative streaming client for a local Ollama chat endpoint.

The local model is deliberately read as JSONL even when the caller only needs
one final answer. That lets an active conversation turn close an obsolete
request while prompt evaluation or generation is still in progress, instead
of waiting for a complete blocking response that may later overwrite a newer
user turn.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping


class OllamaProtocolError(RuntimeError):
    """The local model returned malformed or unbounded protocol data."""


@dataclass(frozen=True, slots=True)
class OllamaChatResult:
    content: str
    done_reason: str
    total_bytes: int
    chunks: int

    @property
    def truncated(self) -> bool:
        return self.done_reason == "length"


def _cancelled(cancel_check: Callable[[], bool] | None) -> bool:
    try:
        return bool(cancel_check and cancel_check())
    except Exception:
        # A broken cancellation observer must fail safe for the model call.
        return True


def _close_quietly(response: object | None) -> None:
    if response is None:
        return
    try:
        response.close()  # type: ignore[attr-defined]
    except Exception:
        pass


def chat(
    base_url: str,
    payload: Mapping[str, Any],
    *,
    timeout: float,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int], None] | None = None,
    max_response_bytes: int = 2 * 1024 * 1024,
) -> OllamaChatResult:
    """Return one local-model answer while allowing turn cancellation.

    ``payload`` is copied and ``stream`` is forced to true. A small daemon
    reader owns the potentially blocking HTTP ``readline`` call while the
    caller thread continues checking the conversation token. Closing the
    response on cancellation also asks Ollama to abandon the obsolete stream.
    Model content is parsed as bounded JSONL and is never executed.
    """

    clean_url = str(base_url).rstrip("/")
    if not clean_url:
        raise ValueError("Ollama adresi boş olamaz.")
    limit = max(1024, int(max_response_bytes))
    timeout_seconds = max(1.0, float(timeout))
    deadline = time.monotonic() + timeout_seconds
    request_payload = dict(payload)
    request_payload["stream"] = True
    encoded = json.dumps(
        request_payload,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{clean_url}/api/chat",
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if _cancelled(cancel_check):
        raise InterruptedError("Yerel model çağrısı kullanıcı tarafından iptal edildi.")

    response = None
    reader: threading.Thread | None = None
    reader_stop = threading.Event()
    rows: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=32)
    total_bytes = 0
    chunk_count = 0
    content_parts: list[str] = []
    content_chars = 0
    done_reason = ""

    def publish(kind: str, value: object) -> bool:
        while not reader_stop.is_set():
            try:
                rows.put((kind, value), timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def read_stream() -> None:
        try:
            while not reader_stop.is_set():
                try:
                    # ``HTTPResponse.readline`` accepts a byte limit. Reading
                    # at most one byte beyond the configured ceiling prevents
                    # a peer from forcing an unbounded single JSONL record
                    # into memory. Lightweight test doubles may expose the
                    # no-argument form only, so retain a safe compatibility
                    # fallback; the aggregate limit below still rejects it.
                    line = response.readline(limit + 1)  # type: ignore[union-attr]
                except TypeError:
                    line = response.readline()  # type: ignore[union-attr]
                if not publish("line", line):
                    return
                if not line:
                    return
        except Exception as exc:  # transfer the I/O failure to caller thread
            publish("error", exc)
        finally:
            publish("eof", None)

    try:
        # A modest connection timeout is enough for a loopback service. The
        # overall generation deadline is enforced independently below.
        response = urllib.request.urlopen(request, timeout=min(timeout_seconds, 10.0))
        reader = threading.Thread(
            target=read_stream,
            name="jarvis-ollama-stream-reader",
            daemon=True,
        )
        reader.start()

        stream_finished = False
        while not stream_finished:
            if _cancelled(cancel_check):
                reader_stop.set()
                _close_quietly(response)
                raise InterruptedError("Yerel model çağrısı kullanıcı tarafından iptal edildi.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reader_stop.set()
                _close_quietly(response)
                raise TimeoutError("Yerel model yanıtı zaman aşımına uğradı.")
            try:
                kind, value = rows.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue

            if kind == "eof":
                stream_finished = True
                continue
            if kind == "error":
                if _cancelled(cancel_check):
                    raise InterruptedError(
                        "Yerel model çağrısı kullanıcı tarafından iptal edildi."
                    )
                if isinstance(value, Exception):
                    raise RuntimeError("Yerel model akışı okunamadı.") from value
                raise OllamaProtocolError("Yerel model akışı okunamadı.")
            if kind != "line":
                continue
            line = value
            if not isinstance(line, (bytes, bytearray)):
                raise OllamaProtocolError("Yerel model akışı bayt verisi döndürmedi.")
            if not line:
                stream_finished = True
                continue

            total_bytes += len(line)
            if total_bytes > limit:
                raise OllamaProtocolError(
                    f"Yerel model yanıtı güvenli boyut sınırını aştı ({limit} bayt)."
                )
            try:
                row = json.loads(bytes(line).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OllamaProtocolError("Yerel model geçersiz JSONL döndürdü.") from exc
            if not isinstance(row, dict):
                raise OllamaProtocolError("Yerel model JSON nesnesi döndürmedi.")
            if row.get("error"):
                raise RuntimeError(str(row.get("error"))[:1000])
            message = row.get("message", {})
            piece = str(message.get("content", "")) if isinstance(message, dict) else ""
            if piece:
                content_parts.append(piece)
                content_chars += len(piece)
            chunk_count += 1
            if progress_callback is not None:
                try:
                    progress_callback(content_chars)
                except Exception:
                    pass
            if bool(row.get("done")):
                done_reason = str(row.get("done_reason", "") or "stop")
                stream_finished = True

        if _cancelled(cancel_check):
            raise InterruptedError("Yerel model çağrısı kullanıcı tarafından iptal edildi.")
        if chunk_count == 0:
            raise OllamaProtocolError("Yerel model boş akış döndürdü.")
        return OllamaChatResult(
            content="".join(content_parts).strip(),
            done_reason=done_reason,
            total_bytes=total_bytes,
            chunks=chunk_count,
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Yerel model HTTP hatası: {exc.code}") from exc
    finally:
        reader_stop.set()
        _close_quietly(response)
        if reader is not None and reader.is_alive():
            reader.join(timeout=0.25)
