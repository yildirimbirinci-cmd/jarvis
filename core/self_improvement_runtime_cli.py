from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from artmach_assistant.core.autonomous_improvement_loop import (
    ImprovementTrigger,
)
from artmach_assistant.core.ollama_changeset_model import OllamaChangesetModel
from artmach_assistant.core.self_improvement_loop_runtime import (
    SelfImprovementLoopRuntime,
)


def _normalise_text(
    value: object,
    *,
    limit: int = 4000,
) -> str:
    return " ".join(str(value or "").split())[:limit]


def _source_digest(journal_path: Path) -> str:
    if not journal_path.is_file():
        raise FileNotFoundError(journal_path)

    digest = hashlib.sha256()

    related = (
        journal_path,
        journal_path.with_name(
            f"{journal_path.stem}_tasks.json"
        ),
        journal_path.with_name(
            f"{journal_path.stem}_history.json"
        ),
        journal_path.with_name(
            f"{journal_path.stem}_experiment_requests.json"
        ),
    )

    found = False

    for path in related:
        if not path.is_file():
            continue

        found = True
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    if not found:
        raise FileNotFoundError(journal_path)

    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeCommandResult:
    command: str
    exit_code: int
    status: str
    output: str


def _render_loop_result(result: object) -> str:
    stages = getattr(result, "stages", ())
    lines = [
        f"Run: {getattr(result, 'run_id', '')}",
        f"Durum: {getattr(result, 'status', '')}",
        (
            "Tamamlanan aşama: "
            f"{getattr(result, 'completed_stage_count', 0)}"
        ),
    ]

    for stage in stages:
        message = _normalise_text(
            getattr(stage, "message", "")
        )
        line = (
            f"- {getattr(stage, 'stage', '')}: "
            f"{getattr(stage, 'status', '')}"
        )

        if message:
            line += f" — {message}"

        lines.append(line)

    return "\n".join(lines)


def _load_state(runtime_root: Path) -> dict[str, object] | None:
    path = runtime_root / "autonomous_loop_state.json"

    if not path.exists():
        return None

    if not path.is_file():
        raise ValueError(
            "autonomous improvement state path is not a file"
        )

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "invalid autonomous improvement state"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "autonomous improvement state must be an object"
        )

    runs = payload.get("runs")

    if not isinstance(runs, list):
        raise ValueError(
            "autonomous improvement runs must be a list"
        )

    return payload


def run_self_improvement_runtime(
    command: str,
    *,
    project_root: str | Path,
    journal_path: str | Path,
    runtime_root: str | Path,
    candidate_id: str | None = None,
    experiment_result_paths: Sequence[str | Path] = (),
    trigger_id: str = "manual-self-improvement",
    model_config: object | None = None,
    changeset_timeout_seconds: float = 120.0,
    cancel_check: Callable[[], bool] | None = None,
) -> RuntimeCommandResult:
    action = _normalise_text(
        command,
        limit=32,
    ).casefold()

    if action not in {
        "status",
        "run",
        "prepare",
        "complete",
    }:
        return RuntimeCommandResult(
            command=action or "invalid",
            exit_code=2,
            status="invalid",
            output=(
                "Bilinmeyen komut. Geçerli komutlar: "
                "status, run, prepare, complete."
            ),
        )

    project = (
        Path(project_root)
        .expanduser()
        .resolve(strict=False)
    )
    journal = (
        Path(journal_path)
        .expanduser()
        .resolve(strict=False)
    )
    runtime = (
        Path(runtime_root)
        .expanduser()
        .resolve(strict=False)
    )

    if action == "status":
        try:
            state = _load_state(runtime)
        except Exception as exc:
            return RuntimeCommandResult(
                command=action,
                exit_code=1,
                status="failed",
                output=(
                    f"Durum okunamadı: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        if state is None or not state["runs"]:
            return RuntimeCommandResult(
                command=action,
                exit_code=0,
                status="empty",
                output=(
                    "Henüz autonomous improvement "
                    "çalıştırması yok."
                ),
            )

        last = state["runs"][-1]

        if not isinstance(last, dict):
            return RuntimeCommandResult(
                command=action,
                exit_code=1,
                status="failed",
                output="Son çalışma kaydı geçersiz.",
            )

        lines = [
            f"Run: {last.get('run_id', '')}",
            f"Durum: {last.get('status', '')}",
            (
                "Tamamlanan aşama: "
                f"{last.get('completed_stage_count', 0)}"
            ),
        ]

        for row in last.get("stages", []):
            if not isinstance(row, dict):
                continue

            line = (
                f"- {row.get('stage', '')}: "
                f"{row.get('status', '')}"
            )
            message = _normalise_text(
                row.get("message")
            )

            if message:
                line += f" — {message}"

            lines.append(line)

        return RuntimeCommandResult(
            command=action,
            exit_code=0,
            status=str(last.get("status", "")),
            output="\n".join(lines),
        )

    try:
        digest = _source_digest(journal)
    except Exception as exc:
        return RuntimeCommandResult(
            command=action,
            exit_code=1,
            status="blocked",
            output=(
                f"Research Journal okunamadı: "
                f"{type(exc).__name__}: {exc}"
            ),
        )

    if action == "complete" and not experiment_result_paths:
        return RuntimeCommandResult(
            command=action,
            exit_code=2,
            status="invalid",
            output=(
                "Complete komutu en az bir doğrulanmış "
                "experiment result dosyası gerektirir."
            ),
        )

    allow_experiment = action in {
        "prepare",
        "complete",
    }

    # Aynı journal için farklı açık işlemlerin duplicate-run
    # kimliğiyle çakışmaması amacıyla komut kimliğe dahildir.
    resolved_trigger_id = (
        f"{_normalise_text(trigger_id, limit=120)}-"
        f"{action}"
    )

    trigger = ImprovementTrigger(
        trigger_id=resolved_trigger_id,
        reason=(
            "Manual guarded self-improvement "
            f"runtime command: {action}"
        ),
        source_digest=digest,
        enabled=True,
        allow_experiment=allow_experiment,
    )

    try:
        changeset_model = (
            OllamaChangesetModel(
                model_config,
                timeout_seconds=changeset_timeout_seconds,
                cancel_check=cancel_check,
            )
            if model_config is not None and action in {"prepare", "complete"}
            else None
        )
        result = SelfImprovementLoopRuntime(
            project,
            journal,
            runtime,
            candidate_id=candidate_id,
            experiment_result_paths=(
                experiment_result_paths
                if action == "complete"
                else ()
            ),
            changeset_model=changeset_model,
        ).run(trigger)
    except Exception as exc:
        return RuntimeCommandResult(
            command=action,
            exit_code=1,
            status="failed",
            output=(
                f"Self-improvement runtime çalıştırılamadı: "
                f"{type(exc).__name__}: {exc}"
            ),
        )

    status = str(result.status)
    exit_code = (
        0
        if status in {
            "completed",
            "blocked",
            "skipped",
        }
        else 1
    )

    return RuntimeCommandResult(
        command=action,
        exit_code=exit_code,
        status=status,
        output=_render_loop_result(result),
    )
