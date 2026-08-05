from __future__ import annotations

import contextvars
import inspect
import threading
import time
import uuid
import weakref
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

Recorder = Callable[..., object]
WorkspaceProvider = Callable[[], str | Path]

_LOCK = threading.RLock()
_RECORDER_WEAK: weakref.ReferenceType[Any] | weakref.WeakMethod | None = None
_RECORDER_STRONG: Recorder | None = None
_WORKSPACE_WEAK: weakref.ReferenceType[Any] | weakref.WeakMethod | None = None
_WORKSPACE_STRONG: WorkspaceProvider | None = None
_ORIGINALS: dict[tuple[type, str], Callable[..., Any]] = {}
_CORRELATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jarvis_runtime_correlation_id", default=""
)

_EXPECTED_AUDIO_MESSAGES = (
    "konuşma algılanamadı",
    "ses sinyali yok",
    "boş ses verisi",
    "hiç pcm ses bloğu",
    "sabit mikrofon gürültüsü",
    "konuşma dışı ortam sesi",
    "ses kaydı insan konuşması için çok kısa",
    "insan sesi doğrulanamadı",
    "whisper kaynaklı medya/reklam artefaktı",
)


@dataclass(frozen=True, slots=True)
class InstrumentationOutcome:
    # ``None`` deliberately suppresses a high-frequency, expected event such
    # as ordinary microphone silence.  Failures, fallbacks and useful timing
    # samples still reach the bounded RuntimeEventStore.
    status: str | None = "completed"
    message: str = ""
    metadata: Mapping[str, object] | None = None
    error: BaseException | None = None
    error_type: str = ""


@dataclass(frozen=True, slots=True)
class MethodSpec:
    component: str
    action: str
    source_path: str
    symbol: str
    scope: str
    metadata_builder: Callable[[object, tuple[object, ...], dict[str, object]], Mapping[str, object]] | None = None
    outcome_builder: Callable[[object, object, tuple[object, ...], dict[str, object]], InstrumentationOutcome] | None = None
    workspace_builder: Callable[[object, tuple[object, ...], dict[str, object]], str | Path] | None = None


def _make_reference(value: Callable[..., Any] | None):
    if value is None:
        return None, None
    if inspect.ismethod(value) and getattr(value, "__self__", None) is not None:
        return weakref.WeakMethod(value), None
    # Plain functions and callable objects are intentionally held strongly.
    # A short-lived lambda passed during application startup must not vanish
    # before the first worker thread emits an event.
    return None, value


def configure_runtime_instrumentation(
    recorder: Recorder | None,
    *,
    workspace_provider: WorkspaceProvider | None = None,
) -> None:
    """Configure the process-local telemetry sink used by instrumented services.

    The sink is deliberately fail-open: recording must never change the result of
    the observed operation. Bound methods are weakly referenced so an old engine
    instance is not kept alive by the instrumentation layer.
    """

    global _RECORDER_WEAK, _RECORDER_STRONG, _WORKSPACE_WEAK, _WORKSPACE_STRONG
    with _LOCK:
        _RECORDER_WEAK, _RECORDER_STRONG = _make_reference(recorder)
        _WORKSPACE_WEAK, _WORKSPACE_STRONG = _make_reference(workspace_provider)


def _dereference(weak_value, strong_value):
    if strong_value is not None:
        return strong_value
    if weak_value is None:
        return None
    try:
        return weak_value()
    except Exception:
        return None


def _recorder() -> Recorder | None:
    with _LOCK:
        return _dereference(_RECORDER_WEAK, _RECORDER_STRONG)


def _default_workspace() -> str:
    with _LOCK:
        provider = _dereference(_WORKSPACE_WEAK, _WORKSPACE_STRONG)
    if provider is None:
        return ""
    try:
        return str(provider() or "")
    except Exception:
        return ""


def _safe_metadata(values: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(values, Mapping):
        return {}
    result: dict[str, object] = {}
    for raw_key, raw_value in list(values.items())[:32]:
        key = str(raw_key or "").strip()[:80]
        if not key:
            continue
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            result[key] = raw_value if raw_value is not None else ""
        elif isinstance(raw_value, Path):
            result[key] = str(raw_value)
        else:
            result[key] = str(raw_value)[:500]
    return result


def _emit(
    *,
    component: str,
    action: str,
    status: str,
    duration_ms: float,
    workspace: str | Path,
    scope: str,
    source_path: str,
    symbol: str,
    message: str = "",
    error: BaseException | None = None,
    error_type: str = "",
    metadata: Mapping[str, object] | None = None,
    correlation_id: str = "",
) -> bool:
    sink = _recorder()
    if sink is None:
        return False
    try:
        sink(
            component=component,
            action=action,
            status=status,
            duration_ms=max(0.0, float(duration_ms)),
            workspace=str(workspace or ""),
            scope=scope,
            source_path=source_path,
            symbol=symbol,
            message=str(message or "")[:2000],
            error=error,
            error_type=str(error_type or "")[:300],
            metadata=_safe_metadata(metadata),
            correlation_id=str(correlation_id or "")[:64],
        )
        return True
    except TypeError:
        # Compatibility with recorder callables that predate correlation IDs.
        try:
            sink(
                component=component,
                action=action,
                status=status,
                duration_ms=max(0.0, float(duration_ms)),
                workspace=str(workspace or ""),
                scope=scope,
                source_path=source_path,
                symbol=symbol,
                message=str(message or "")[:2000],
                error=error,
                error_type=str(error_type or "")[:300],
                metadata=_safe_metadata(metadata),
            )
            return True
        except TypeError:
            # Oldest callback shape also predates explicit error types.
            try:
                sink(
                    component=component,
                    action=action,
                    status=status,
                    duration_ms=max(0.0, float(duration_ms)),
                    workspace=str(workspace or ""),
                    scope=scope,
                    source_path=source_path,
                    symbol=symbol,
                    message=str(message or "")[:2000],
                    error=error,
                    metadata=_safe_metadata(metadata),
                )
                return True
            except Exception:
                return False
        except Exception:
            return False
    except Exception:
        return False


def _workspace_from_service(
    instance: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> str:
    workspace = getattr(instance, "workspace", None)
    if workspace is not None:
        try:
            return str(workspace.require_root())
        except Exception:
            root = getattr(workspace, "root", "")
            if root:
                return str(root)
    return _default_workspace()


def _workspace_from_filesystem(
    instance: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> str:
    candidates = list(args[:2])
    candidates.extend(
        kwargs.get(key) for key in ("source", "directory", "parent", "destination_directory")
        if kwargs.get(key) is not None
    )
    roots = tuple(getattr(instance, "allowed_roots", ()) or ())
    for candidate in candidates:
        if not isinstance(candidate, (str, Path)):
            continue
        try:
            path = Path(candidate).expanduser().resolve(strict=False)
        except Exception:
            continue
        for root in roots:
            try:
                root_path = Path(root).expanduser().resolve(strict=False)
                if path == root_path or root_path in path.parents:
                    return str(root_path)
            except Exception:
                continue
    if roots:
        return str(roots[0])
    return _default_workspace()


def _workspace_from_backup(
    instance: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> str:
    source = kwargs.get("source_root") or (args[0] if args else "")
    return str(source or _default_workspace())


def _base_metadata(
    instance: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> Mapping[str, object]:
    return {}


def _voice_capture_metadata(instance, args, kwargs):
    device = kwargs.get("device_index", args[0] if args else None)
    max_seconds = kwargs.get(
        "max_seconds", kwargs.get("seconds", args[1] if len(args) > 1 else 0.0)
    )
    try:
        slow_threshold = max(2500.0, float(max_seconds or 0.0) * 1000.0 + 2000.0)
    except (TypeError, ValueError, OverflowError):
        slow_threshold = 9000.0
    return {
        "device_index": -1 if device is None else device,
        "max_seconds": max_seconds,
        "slow_threshold_ms": slow_threshold,
    }


def _voice_recognition_metadata(instance, args, kwargs):
    model = kwargs.get("model_size", args[2] if len(args) > 2 else "small")
    wake_mode = kwargs.get("wake_mode", args[4] if len(args) > 4 else False)
    return {
        "model": str(model or "small")[:80],
        "wake_mode": bool(wake_mode),
        "slow_threshold_ms": 9000.0 if wake_mode else 15000.0,
    }


def _voice_listen_metadata(instance, args, kwargs):
    data = dict(_voice_capture_metadata(instance, args, kwargs))
    data.update(
        {
            "language": str(kwargs.get("language", args[2] if len(args) > 2 else ""))[:40],
            "model": str(kwargs.get("model_size", args[5] if len(args) > 5 else "small"))[:80],
            "wake_mode": bool(kwargs.get("wake_mode", args[7] if len(args) > 7 else False)),
        }
    )
    return data


def _voice_model_metadata(instance, args, kwargs):
    model = kwargs.get("model_size", args[0] if args else "small")
    return {"model": str(model or "small")[:80], "slow_threshold_ms": 12000.0}


def _audio_output_metadata(instance, args, kwargs):
    output_device = kwargs.get(
        "output_device",
        kwargs.get(
            "requested_output",
            args[2] if len(args) > 2 else (args[0] if args else None),
        ),
    )
    source_rate = kwargs.get(
        "source_rate",
        args[1] if len(args) > 1 else 0,
    )
    return {
        "output_device": -1 if output_device is None else output_device,
        "source_rate": source_rate,
        "slow_threshold_ms": 5000.0,
    }


def _tts_dispatch_metadata(instance, args, kwargs):
    text = kwargs.get("text", args[0] if args else "")
    backend = kwargs.get("backend", args[4] if len(args) > 4 else "auto")
    output_device = kwargs.get("output_device", args[7] if len(args) > 7 else None)
    return {
        "text_chars": len(str(text or "")),
        "backend": str(backend or "auto")[:40],
        "output_device": -1 if output_device is None else output_device,
        "slow_threshold_ms": 15000.0,
    }


def _tts_piper_metadata(instance, args, kwargs):
    text = kwargs.get("text", args[0] if args else "")
    output_device = kwargs.get("output_device", args[3] if len(args) > 3 else None)
    return {
        "text_chars": len(str(text or "")),
        "backend": "piper",
        "output_device": -1 if output_device is None else output_device,
        "slow_threshold_ms": 15000.0,
    }


def _tts_windows_metadata(instance, args, kwargs):
    text = kwargs.get("text", args[0] if args else "")
    voice_name = kwargs.get("voice_name", args[1] if len(args) > 1 else "")
    return {
        "text_chars": len(str(text or "")),
        "backend": "windows",
        "voice_configured": bool(str(voice_name or "").strip()),
        "slow_threshold_ms": 15000.0,
    }


def _windows_wake_metadata(instance, args, kwargs):
    aliases = kwargs.get("aliases", args[0] if args else ())
    timeout_seconds = kwargs.get("timeout_seconds", args[1] if len(args) > 1 else 2.0)
    try:
        alias_count = len(aliases) if not isinstance(aliases, str) else 1
    except TypeError:
        alias_count = 0
    try:
        slow_threshold = max(3000.0, float(timeout_seconds or 0.0) * 1000.0 + 8000.0)
    except (TypeError, ValueError, OverflowError):
        slow_threshold = 12000.0
    return {
        "alias_count": max(0, int(alias_count)),
        "timeout_seconds": timeout_seconds,
        "slow_threshold_ms": slow_threshold,
    }


def _dialogue_metadata(instance, args, kwargs):
    text = kwargs.get("text", args[0] if args else "")
    return {
        "model": str(getattr(instance, "model", ""))[:100],
        "input_chars": len(str(text or "")),
        "slow_threshold_ms": 12000.0,
    }


def _model_health_metadata(instance, args, kwargs):
    return {
        "model": str(getattr(instance, "model", ""))[:100],
        "slow_threshold_ms": 5000.0,
    }


def _path_summary(value: object, *, prefix: str) -> dict[str, object]:
    """Return diagnostic path shape without copying full user paths."""

    if not isinstance(value, (str, Path)) or not str(value).strip():
        return {}
    try:
        path = Path(value)
    except (TypeError, ValueError, OSError):
        return {}
    name = path.name or path.anchor or "<root>"
    return {
        f"{prefix}_name": str(name)[:260],
        f"{prefix}_suffix": str(path.suffix)[:40],
        f"{prefix}_absolute": path.is_absolute(),
    }


def _filesystem_metadata(instance, args, kwargs):
    values: list[object] = list(args[:2])
    values.extend(
        kwargs.get(key)
        for key in ("source", "directory", "parent", "destination_directory")
        if kwargs.get(key) is not None
    )
    metadata: dict[str, object] = {"slow_threshold_ms": 10000.0}
    if values:
        metadata.update(_path_summary(values[0], prefix="source"))
    if len(values) > 1:
        metadata.update(_path_summary(values[1], prefix="destination"))
    return metadata


def _backup_metadata(instance, args, kwargs):
    source = kwargs.get("source_root", args[0] if args else "")
    destination = kwargs.get("destination", args[1] if len(args) > 1 else "")
    metadata: dict[str, object] = {
        "zip_output": bool(kwargs.get("zip_output", False)),
        "slow_threshold_ms": 60000.0,
    }
    metadata.update(_path_summary(source, prefix="source"))
    metadata.update(_path_summary(destination, prefix="destination"))
    return metadata


def _build_metadata(instance, args, kwargs):
    profile = kwargs.get("profile", args[0] if args else None)
    return {
        "profile": str(getattr(profile, "name", ""))[:200],
        "slow_threshold_ms": 60000.0,
    }


def _research_metadata(instance, args, kwargs):
    query = kwargs.get("query", args[0] if args else "")
    return {
        "query_chars": len(str(query or "")),
        "max_results": kwargs.get("max_results", args[1] if len(args) > 1 else 6),
        "slow_threshold_ms": 30000.0,
    }


def _research_batch_metadata(instance, args, kwargs):
    queries = kwargs.get("queries", args[0] if args else ())
    try:
        query_count = len(queries) if not isinstance(queries, str) else 1
    except TypeError:
        query_count = 0
    return {
        "query_count": max(0, int(query_count)),
        "max_results_per_query": kwargs.get("max_results_per_query", 4),
        "slow_threshold_ms": 60000.0,
    }


def _project_model_metadata(instance, args, kwargs):
    instruction = kwargs.get("raw_instruction", args[0] if args else "")
    return {
        "instruction_chars": len(str(instruction or "")),
        "model": str(getattr(instance, "_code_model", lambda: "")())[:100],
        "slow_threshold_ms": 45000.0,
    }


def _own_code_metadata(instance, args, kwargs):
    instruction = kwargs.get("raw_instruction", args[0] if args else "")
    return {
        "instruction_chars": len(str(instruction or "")),
        "model": str(getattr(instance, "code_model", ""))[:100],
        "slow_threshold_ms": 45000.0,
    }


def _result_text_outcome(instance, result, args, kwargs) -> InstrumentationOutcome:
    return InstrumentationOutcome(
        metadata={"output_chars": len(str(result or ""))}
    )


def _audio_capture_outcome(instance, result, args, kwargs):
    path = Path(result) if isinstance(result, (str, Path)) else None
    size = 0
    if path is not None:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
    return InstrumentationOutcome(
        metadata={"captured_bytes": size, "audio_file_created": bool(path)}
    )


def _voice_text_outcome(instance, result, args, kwargs):
    return InstrumentationOutcome(
        metadata={"transcript_chars": len(str(result or "")), "speech_detected": bool(str(result or "").strip())}
    )


def _wake_outcome(instance, result, args, kwargs):
    accepted = False
    score = 0.0
    if isinstance(result, tuple) and result:
        accepted = bool(result[0])
        if len(result) > 1:
            try:
                score = float(result[1])
            except (TypeError, ValueError, OverflowError):
                score = 0.0
    return InstrumentationOutcome(
        status="completed" if accepted else None,
        metadata={"candidate": accepted, "confidence": score},
    )


def _wake_confirmation_outcome(instance, result, args, kwargs):
    confirmed = bool(result[0]) if isinstance(result, tuple) and result else False
    heard = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
    return InstrumentationOutcome(
        metadata={"confirmed": confirmed, "transcript_chars": len(str(heard or ""))}
    )


def _owner_outcome(instance, result, args, kwargs):
    accepted = bool(result[0]) if isinstance(result, tuple) and result else False
    score = result[1] if isinstance(result, tuple) and len(result) > 1 else 0.0
    return InstrumentationOutcome(metadata={"accepted": accepted, "confidence": score})


def _tts_outcome(instance, result, args, kwargs):
    text = str(result or "")
    lowered = text.casefold()
    metadata = {
        "result_chars": len(text),
        "used_windows_fallback": "windows tts kullanıldı" in lowered,
        "piper_error": "piper hatası" in lowered,
    }
    if "kullanıcı tarafından kesildi" in lowered or "durduruldu" in lowered:
        metadata.update({"expected_cancellation": True, "health_excluded": True})
        return InstrumentationOutcome(
            status="cancelled",
            message="Seslendirme kullanıcı tarafından kesildi.",
            metadata=metadata,
        )
    if "piper hatası" in lowered:
        return InstrumentationOutcome(status="warning", message=text[:900], metadata=metadata)
    return InstrumentationOutcome(metadata=metadata)


def _dialogue_outcome(instance, result, args, kwargs):
    if result is None:
        # Intent JSON may legitimately be unavailable; the caller then uses
        # the normal chat responder.  Keep the diagnostic event, but do not
        # turn this expected fallback into a maintenance defect.
        return InstrumentationOutcome(
            status="warning",
            message="Yerel sohbet modeli kullanılabilir bir niyet yanıtı üretmedi; güvenli sohbet yoluna geçildi.",
            metadata={
                "output_chars": 0,
                "fallback_expected": True,
                "health_excluded": True,
            },
        )
    kind = str(getattr(result, "kind", ""))
    content = getattr(result, "response", result)
    return InstrumentationOutcome(
        metadata={"output_chars": len(str(content or "")), "decision_kind": kind[:80]}
    )


def _dialogue_health_outcome(instance, result, args, kwargs):
    ready = bool(result[0]) if isinstance(result, tuple) and result else False
    detail = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
    metadata = {
        "model": str(getattr(instance, "model", ""))[:100],
        "ready": ready,
        "detail_chars": len(str(detail or "")),
    }
    if ready:
        return InstrumentationOutcome(metadata=metadata)
    message = str(detail or "Yerel model sağlık kontrolü başarısız oldu.")[:1500]
    return InstrumentationOutcome(
        status="failed",
        message=message,
        error=RuntimeError(message),
        metadata=metadata,
    )


def _filesystem_outcome(instance, result, args, kwargs):
    source = getattr(result, "source", None)
    destination = getattr(result, "destination", None)
    metadata: dict[str, object] = {
        "result_count": len(result) if isinstance(result, tuple) else 1,
    }
    metadata.update(_path_summary(source, prefix="source"))
    metadata.update(_path_summary(destination, prefix="destination"))
    if isinstance(result, tuple):
        metadata["directory_count"] = sum(
            1 for item in result if bool(getattr(item, "is_directory", False))
        )
        metadata["file_count"] = len(result) - int(metadata["directory_count"])
    return InstrumentationOutcome(metadata=metadata)


def _backup_outcome(instance, result, args, kwargs):
    metadata: dict[str, object] = {
        "file_count": getattr(result, "file_count", 0),
        "total_bytes": getattr(result, "total_bytes", 0),
        "archive_created": bool(getattr(result, "archive_path", "")),
    }
    metadata.update(
        _path_summary(getattr(result, "backup_path", ""), prefix="backup")
    )
    metadata.update(
        _path_summary(getattr(result, "archive_path", ""), prefix="archive")
    )
    return InstrumentationOutcome(metadata=metadata)


def _backup_verify_outcome(instance, result, args, kwargs):
    success = bool(getattr(result, "success", False))
    if success:
        return InstrumentationOutcome(metadata={"verified": True})
    message = str(getattr(result, "report", lambda: "Yedek doğrulaması başarısız.")())[:1500]
    return InstrumentationOutcome(
        status="failed",
        message=message,
        error=RuntimeError(message),
        metadata={"verified": False},
    )


def _build_outcome(instance, result, args, kwargs):
    succeeded = bool(getattr(result, "succeeded", False))
    profile = getattr(getattr(result, "profile", None), "name", "")
    return_code = getattr(result, "return_code", -1)
    metadata = {"profile": str(profile)[:200], "return_code": return_code}
    if succeeded:
        return InstrumentationOutcome(metadata=metadata)
    message = f"Build/test görevi başarısız oldu: {profile or 'bilinmeyen profil'}; çıkış kodu={return_code}."
    return InstrumentationOutcome(
        status="failed", message=message, error=RuntimeError(message), metadata=metadata
    )


def _build_pipeline_outcome(instance, result, args, kwargs):
    rows = tuple(getattr(result, "results", ()) or ())
    succeeded = bool(getattr(result, "succeeded", False))
    metadata = {
        "profile_count": len(rows),
        "failed_profile_count": sum(
            1 for row in rows if not bool(getattr(row, "succeeded", False))
        ),
    }
    if succeeded:
        return InstrumentationOutcome(metadata=metadata)
    message = "Build/test zinciri başarıyla tamamlanamadı."
    return InstrumentationOutcome(
        status="failed",
        message=message,
        error=RuntimeError(message),
        metadata=metadata,
    )


def _validation_outcome(label: str, error_type: str):
    def classify(instance, result, args, kwargs):
        succeeded = bool(result[0]) if isinstance(result, tuple) and result else False
        output = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
        metadata = {
            "succeeded": succeeded,
            "output_chars": len(str(output or "")),
        }
        if succeeded:
            return InstrumentationOutcome(metadata=metadata)
        message = str(output or f"{label} başarısız oldu.")[-1800:]
        return InstrumentationOutcome(
            status="failed",
            message=message,
            error_type=error_type,
            metadata=metadata,
        )

    return classify


def _research_outcome(instance, result, args, kwargs):
    if isinstance(result, (list, tuple)):
        source_count = sum(len(getattr(item, "sources", ()) or ()) for item in result)
        return InstrumentationOutcome(
            metadata={"result_count": len(result), "source_count": source_count}
        )
    sources = getattr(result, "sources", ()) or ()
    return InstrumentationOutcome(metadata={"result_count": 1, "source_count": len(sources)})


def _proposal_outcome(instance, result, args, kwargs):
    files = getattr(result, "files", ()) or ()
    if result is None:
        return InstrumentationOutcome(status="warning", message="Kod modeli doğrulanabilir bir taslak üretmedi.")
    if isinstance(result, str):
        lowered = result.casefold()
        if any(marker in lowered for marker in ("yanıt veremedi", "hazırlanamadı", "reddedildi", "doğrulanamadı")):
            message = result[:1800]
            return InstrumentationOutcome(
                status="failed", message=message, error=RuntimeError(message)
            )
        return InstrumentationOutcome(metadata={"output_chars": len(result)})
    return InstrumentationOutcome(metadata={"changed_file_count": len(files)})


def _agent_task_metadata(instance, args, kwargs):
    task_id = kwargs.get("task_id", args[0] if args else "")
    metadata: dict[str, object] = {"task_id": str(task_id)[:80], "slow_threshold_ms": 30000.0}
    try:
        snapshot = instance.status(str(task_id))
        metadata["tool_name"] = str(getattr(snapshot, "tool_name", ""))[:160]
        metadata["operation_id"] = str(getattr(snapshot, "operation_id", ""))[:80]
    except Exception:
        pass
    return metadata


def _agent_task_outcome(instance, result, args, kwargs):
    task_id = str(kwargs.get("task_id", args[0] if args else ""))
    state = ""
    try:
        state = str(getattr(instance.status(task_id), "state", ""))
    except Exception:
        pass
    lowered = state.casefold()
    if "timed_out" in lowered or "timeout" in lowered or "zaman as" in lowered:
        return InstrumentationOutcome(
            status="cancelled",
            message=f"Araç görevi {state} durumunda zaman aşımıyla sona erdi.",
            metadata={"state": state, "cancellation_reason": "timeout"},
        )
    if "cancel" in lowered or "iptal" in lowered:
        return InstrumentationOutcome(
            status="cancelled",
            message=f"Araç görevi {state} durumunda kullanıcı/üst görev isteğiyle sona erdi.",
            metadata={
                "state": state,
                "cancellation_reason": "cooperative",
                "expected_cancellation": True,
                "health_excluded": True,
            },
        )
    if lowered in {"failed", "failure", "basarisiz", "başarısız"}:
        message = f"Araç görevi {state} durumunda sona erdi."
        return InstrumentationOutcome(
            status="failed", message=message, error=RuntimeError(message),
            metadata={"state": state},
        )
    return InstrumentationOutcome(metadata={"state": state})


def _exception_outcome(exc: BaseException) -> InstrumentationOutcome:
    if isinstance(exc, InterruptedError):
        # InterruptedError is the cooperative cancellation contract used by
        # barge-in, a newer speech turn and explicit user stop commands.  It
        # remains observable, but is not a repair-worthy runtime failure.
        return InstrumentationOutcome(
            status="cancelled",
            message=str(exc),
            error=exc,
            metadata={"expected_cancellation": True, "health_excluded": True},
        )
    message = str(exc or "")
    lowered = message.casefold()
    if any(marker in lowered for marker in _EXPECTED_AUDIO_MESSAGES):
        return InstrumentationOutcome(
            status=None,
            metadata={"speech_detected": False, "expected_no_speech": True},
        )
    return InstrumentationOutcome(status="failed", message=message, error=exc)


def _instrument_method(cls: type, method_name: str, spec: MethodSpec) -> None:
    key = (cls, method_name)
    with _LOCK:
        if key in _ORIGINALS:
            return
        original = getattr(cls, method_name, None)
        if not callable(original):
            return
        _ORIGINALS[key] = original

    @wraps(original)
    def observed(instance, *args, **kwargs):
        started = time.perf_counter()
        parent_correlation = _CORRELATION_ID.get()
        correlation_id = parent_correlation or uuid.uuid4().hex
        token = None
        if not parent_correlation:
            token = _CORRELATION_ID.set(correlation_id)
        try:
            try:
                metadata = dict(
                    (spec.metadata_builder or _base_metadata)(instance, args, kwargs)
                )
            except Exception:
                metadata = {}
            try:
                workspace = (
                    spec.workspace_builder(instance, args, kwargs)
                    if spec.workspace_builder is not None
                    else _default_workspace()
                )
            except Exception:
                workspace = _default_workspace()
            try:
                result = original(instance, *args, **kwargs)
            except BaseException as exc:
                outcome = _exception_outcome(exc)
                metadata.update(dict(outcome.metadata or {}))
                if outcome.status is not None:
                    _emit(
                        component=spec.component,
                        action=spec.action,
                        status=outcome.status,
                        duration_ms=(time.perf_counter() - started) * 1000.0,
                        workspace=workspace,
                        scope=spec.scope,
                        source_path=spec.source_path,
                        symbol=spec.symbol,
                        message=outcome.message,
                        error=outcome.error,
                        error_type=outcome.error_type,
                        metadata=metadata,
                        correlation_id=correlation_id,
                    )
                raise
            try:
                outcome = (
                    spec.outcome_builder(instance, result, args, kwargs)
                    if spec.outcome_builder is not None
                    else InstrumentationOutcome()
                )
            except Exception:
                outcome = InstrumentationOutcome()
            metadata.update(dict(outcome.metadata or {}))
            if outcome.status is not None:
                _emit(
                    component=spec.component,
                    action=spec.action,
                    status=outcome.status,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    workspace=workspace,
                    scope=spec.scope,
                    source_path=spec.source_path,
                    symbol=spec.symbol,
                    message=outcome.message,
                    error=outcome.error,
                    error_type=outcome.error_type,
                    metadata=metadata,
                    correlation_id=correlation_id,
                )
            return result
        finally:
            if token is not None:
                _CORRELATION_ID.reset(token)

    setattr(observed, "__jarvis_runtime_instrumented__", True)
    setattr(cls, method_name, observed)


def _instrument_task_wrap(cls: type) -> None:
    method_name = "wrap"
    key = (cls, method_name)
    with _LOCK:
        if key in _ORIGINALS:
            return
        original = getattr(cls, method_name, None)
        if not callable(original):
            return
        _ORIGINALS[key] = original

    @wraps(original)
    def observed_wrap(instance, task_id, token, action):
        action_duration_ms = 0.0
        action_started = False
        action_completed = False

        @wraps(action)
        def observed_action():
            nonlocal action_duration_ms
            nonlocal action_started
            nonlocal action_completed

            action_started = True
            action_started_at = time.perf_counter()

            try:
                return action()
            finally:
                action_duration_ms = (
                    time.perf_counter() - action_started_at
                ) * 1000.0
                action_completed = True

        execute = original(
            instance,
            task_id,
            token,
            observed_action,
        )

        @wraps(execute)
        def observed_execute():
            started = time.perf_counter()
            correlation_id = uuid.uuid4().hex
            context_token = _CORRELATION_ID.set(correlation_id)
            workspace = _default_workspace()
            metadata: dict[str, object] = {
                "task_id": str(task_id)[:80],
                "slow_threshold_ms": 30000.0,
            }
            try:
                active = instance.active
                if active is not None and getattr(active, "task_id", "") == task_id:
                    metadata["task_name_chars"] = len(
                        str(getattr(active, "name", "") or "")
                    )
                    metadata["source"] = str(getattr(active, "source", ""))[:80]
                try:
                    result = execute()
                except BaseException as exc:
                    total_duration_ms = (
                        time.perf_counter() - started
                    ) * 1000.0
                    metadata["action_started"] = action_started
                    metadata["action_completed"] = action_completed
                    metadata["action_duration_ms"] = round(
                        action_duration_ms,
                        3,
                    )
                    metadata["wrapper_overhead_ms"] = round(
                        max(
                            0.0,
                            total_duration_ms - action_duration_ms,
                        ),
                        3,
                    )
                    outcome = _exception_outcome(exc)
                    if outcome.status is not None:
                        _emit(
                            component="TaskOrchestrator",
                            action="execute_task",
                            status=outcome.status,
                            duration_ms=total_duration_ms,
                            workspace=workspace,
                            scope="task",
                            source_path="core/task_orchestrator.py",
                            symbol="TaskOrchestrator.wrap.execute",
                            message=outcome.message,
                            error=outcome.error,
                            error_type=outcome.error_type,
                            metadata=metadata,
                            correlation_id=correlation_id,
                        )
                    raise
                total_duration_ms = (
                    time.perf_counter() - started
                ) * 1000.0
                metadata["action_started"] = action_started
                metadata["action_completed"] = action_completed
                metadata["action_duration_ms"] = round(
                    action_duration_ms,
                    3,
                )
                metadata["wrapper_overhead_ms"] = round(
                    max(
                        0.0,
                        total_duration_ms - action_duration_ms,
                    ),
                    3,
                )
                _emit(
                    component="TaskOrchestrator",
                    action="execute_task",
                    status="completed",
                    duration_ms=total_duration_ms,
                    workspace=workspace,
                    scope="task",
                    source_path="core/task_orchestrator.py",
                    symbol="TaskOrchestrator.wrap.execute",
                    metadata=metadata,
                    correlation_id=correlation_id,
                )
                return result
            finally:
                _CORRELATION_ID.reset(context_token)

        return observed_execute

    setattr(observed_wrap, "__jarvis_runtime_instrumented__", True)
    setattr(cls, method_name, observed_wrap)


def install_runtime_instrumentation() -> int:
    """Install idempotent wrappers around real runtime service entry points."""

    from artmach_assistant.core.agent_task_runtime import AgentTaskRuntime
    from artmach_assistant.core.assistant import AssistantEngine
    from artmach_assistant.core.build_manager import BuildManager
    from artmach_assistant.core.filesystem_tool_service import FileSystemToolService
    from artmach_assistant.core.local_dialogue import LocalDialogueManager
    from artmach_assistant.core.project_backup_service import ProjectBackupService
    from artmach_assistant.core.project_improvement_runtime import ProjectImprovementRuntime
    from artmach_assistant.core.research_manager import ResearchManager
    from artmach_assistant.core.task_orchestrator import TaskOrchestrator
    from artmach_assistant.core.voice_service import VoiceService

    before = len(_ORIGINALS)
    voice_specs = (
        ("record_wav", "audio_capture_fixed", _voice_capture_metadata, _audio_capture_outcome),
        ("record_utterance_wav", "audio_capture", _voice_capture_metadata, _audio_capture_outcome),
        ("_whisper_model", "whisper_model_load", _voice_model_metadata, None),
        ("recognize_wav", "stt_transcription", _voice_recognition_metadata, _voice_text_outcome),
        ("listen_once", "speech_turn_fixed", _voice_listen_metadata, _voice_text_outcome),
        ("listen_utterance", "speech_turn", _voice_listen_metadata, _voice_text_outcome),
        ("listen_for_local_wake", "wake_candidate", _voice_capture_metadata, _wake_outcome),
        ("listen_for_local_stop", "stop_candidate", _voice_capture_metadata, _wake_outcome),
        ("listen_for_windows_wake", "wake_windows", _windows_wake_metadata, _result_text_outcome),
        ("confirm_local_wake", "wake_confirmation", _voice_recognition_metadata, _wake_confirmation_outcome),
        ("verify_owner_voice", "owner_voice_verification", _base_metadata, _owner_outcome),
        ("resolve_working_microphone", "microphone_resolution", _base_metadata, None),
        ("_play_audio_resilient", "audio_output_playback", _audio_output_metadata, None),
        ("play_output_test_tone", "audio_output_test", _audio_output_metadata, None),
        ("probe_output_device", "audio_output_probe", _audio_output_metadata, None),
        ("tts_backend_status", "tts_backend_readiness", _base_metadata, None),
        ("stop_speaking", "tts_interrupt", _base_metadata, None),
        ("_speak_with_piper", "tts_piper", _tts_piper_metadata, _result_text_outcome),
        ("_speak_with_windows", "tts_windows", _tts_windows_metadata, _result_text_outcome),
        ("speak", "tts_dispatch", _tts_dispatch_metadata, _tts_outcome),
    )
    for method_name, action, metadata_builder, outcome_builder in voice_specs:
        _instrument_method(
            VoiceService,
            method_name,
            MethodSpec(
                "VoiceService",
                action,
                "core/voice_service.py",
                f"VoiceService.{method_name}",
                "voice",
                metadata_builder=metadata_builder,
                outcome_builder=outcome_builder,
            ),
        )

    _instrument_method(
        LocalDialogueManager,
        "health",
        MethodSpec(
            "LocalDialogueManager",
            "model_health",
            "core/local_dialogue.py",
            "LocalDialogueManager.health",
            "model",
            metadata_builder=_model_health_metadata,
            outcome_builder=_dialogue_health_outcome,
        ),
    )

    for method_name in ("interpret", "respond"):
        _instrument_method(
            LocalDialogueManager,
            method_name,
            MethodSpec(
                "LocalDialogueManager",
                "intent_model" if method_name == "interpret" else "chat_model",
                "core/local_dialogue.py",
                f"LocalDialogueManager.{method_name}",
                "model",
                metadata_builder=_dialogue_metadata,
                outcome_builder=_dialogue_outcome,
            ),
        )

    _instrument_method(
        ProjectImprovementRuntime,
        "prepare_edit",
        MethodSpec(
            "ProjectImprovementRuntime",
            "code_model_proposal",
            "core/project_improvement_runtime.py",
            "ProjectImprovementRuntime.prepare_edit",
            "code_model",
            metadata_builder=_project_model_metadata,
            outcome_builder=_proposal_outcome,
            workspace_builder=_workspace_from_service,
        ),
    )
    for method_name, action in (
        ("prepare_own_code_proposal", "own_code_model_proposal"),
        ("_request_targeted_validation_repair", "targeted_code_model_repair"),
    ):
        _instrument_method(
            AssistantEngine,
            method_name,
            MethodSpec(
                "AssistantEngine",
                action,
                "core/assistant.py",
                f"AssistantEngine.{method_name}",
                "code_model",
                metadata_builder=_own_code_metadata,
                outcome_builder=_proposal_outcome,
            ),
        )

    for method_name, action, label, error_type, threshold in (
        (
            "_compile_own_code",
            "own_code_compile",
            "Kendi kaynak derleme kontrolü",
            "OwnCodeCompileError",
            90000.0,
        ),
        (
            "_runtime_health_check",
            "own_code_startup_check",
            "Temiz süreç başlangıç kontrolü",
            "OwnCodeStartupError",
            60000.0,
        ),
        (
            "_run_own_tests",
            "own_code_tests",
            "Kendi kaynak testleri",
            "OwnCodeTestError",
            300000.0,
        ),
    ):
        _instrument_method(
            AssistantEngine,
            method_name,
            MethodSpec(
                "AssistantEngine",
                action,
                "core/assistant.py",
                f"AssistantEngine.{method_name}",
                "build",
                metadata_builder=(
                    lambda instance, args, kwargs, limit=threshold: {
                        "slow_threshold_ms": limit,
                    }
                ),
                outcome_builder=_validation_outcome(label, error_type),
            ),
        )

    _instrument_task_wrap(TaskOrchestrator)

    for method_name, action in (
        ("list_directory", "list_directory"),
        ("create_directory", "create_directory"),
        ("copy", "copy"),
        ("move", "move"),
        ("rename", "rename"),
        ("undo_last", "undo_last"),
    ):
        _instrument_method(
            FileSystemToolService,
            method_name,
            MethodSpec(
                "FileSystemToolService",
                action,
                "core/filesystem_tool_service.py",
                f"FileSystemToolService.{method_name}",
                "filesystem",
                metadata_builder=_filesystem_metadata,
                outcome_builder=_filesystem_outcome,
                workspace_builder=_workspace_from_filesystem,
            ),
        )

    for method_name, action, outcome in (
        ("create_backup", "create_backup", _backup_outcome),
        ("verify_backup", "verify_backup", _backup_verify_outcome),
    ):
        _instrument_method(
            ProjectBackupService,
            method_name,
            MethodSpec(
                "ProjectBackupService",
                action,
                "core/project_backup_service.py",
                f"ProjectBackupService.{method_name}",
                "backup",
                metadata_builder=_backup_metadata,
                outcome_builder=outcome,
                workspace_builder=_workspace_from_backup,
            ),
        )

    _instrument_method(
        BuildManager,
        "run",
        MethodSpec(
            "BuildManager",
            "run_profile",
            "core/build_manager.py",
            "BuildManager.run",
            "build",
            metadata_builder=_build_metadata,
            outcome_builder=_build_outcome,
            workspace_builder=_workspace_from_service,
        ),
    )
    _instrument_method(
        BuildManager,
        "run_pipeline",
        MethodSpec(
            "BuildManager",
            "run_pipeline",
            "core/build_manager.py",
            "BuildManager.run_pipeline",
            "build",
            metadata_builder=lambda instance, args, kwargs: {
                "stop_on_failure": bool(
                    kwargs.get("stop_on_failure", args[0] if args else True)
                ),
                "slow_threshold_ms": 300000.0,
            },
            outcome_builder=_build_pipeline_outcome,
            workspace_builder=_workspace_from_service,
        ),
    )

    _instrument_method(
        AgentTaskRuntime,
        "_execute",
        MethodSpec(
            "AgentTaskRuntime",
            "execute_tool",
            "core/agent_task_runtime.py",
            "AgentTaskRuntime._execute",
            "tool",
            metadata_builder=_agent_task_metadata,
            outcome_builder=_agent_task_outcome,
        ),
    )

    for method_name, action, metadata_builder in (
        ("search", "web_search", _research_metadata),
        ("search_many", "web_search_batch", _research_batch_metadata),
    ):
        _instrument_method(
            ResearchManager,
            method_name,
            MethodSpec(
                "ResearchManager",
                action,
                "core/research_manager.py",
                f"ResearchManager.{method_name}",
                "research",
                metadata_builder=metadata_builder,
                outcome_builder=_research_outcome,
            ),
        )

    return len(_ORIGINALS) - before


def runtime_instrumentation_coverage() -> tuple[str, ...]:
    with _LOCK:
        return tuple(
            sorted(f"{cls.__module__}.{cls.__name__}.{name}" for cls, name in _ORIGINALS)
        )


def reset_runtime_instrumentation_for_tests() -> None:
    """Restore original methods and clear global sinks; intended for isolated tests."""

    global _RECORDER_WEAK, _RECORDER_STRONG, _WORKSPACE_WEAK, _WORKSPACE_STRONG
    with _LOCK:
        for (cls, method_name), original in list(_ORIGINALS.items()):
            setattr(cls, method_name, original)
        _ORIGINALS.clear()
        _RECORDER_WEAK = None
        _RECORDER_STRONG = None
        _WORKSPACE_WEAK = None
        _WORKSPACE_STRONG = None
