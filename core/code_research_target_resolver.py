from __future__ import annotations

from dataclasses import dataclass

from artmach_assistant.core.code_research_contracts import CodeTarget


@dataclass(frozen=True, slots=True)
class CodeTargetResolution:
    target: CodeTarget
    source: str
    reason: str

    @property
    def resolved(self) -> bool:
        return self.target.resolved


def resolve_code_target(
    finding: object,
    *,
    promoted_path: str = "",
    promoted_symbol: str = "",
) -> CodeTargetResolution:
    """Resolve a code target from structured evidence only.

    Free-form titles and issue wording are intentionally not parsed. A promoted
    runtime target may override the original finding when it was produced by a
    separate evidence-backed target-promotion step.
    """
    override_path = str(promoted_path or "").strip().replace("\\", "/")
    override_symbol = str(promoted_symbol or "").strip()
    if override_path:
        return CodeTargetResolution(
            CodeTarget(override_path, override_symbol),
            "promoted_runtime_target",
            "Using evidence-backed promoted runtime target.",
        )

    path = str(getattr(finding, "path", "") or "").strip().replace("\\", "/")
    symbol = str(getattr(finding, "symbol", "") or "").strip()
    if path:
        return CodeTargetResolution(
            CodeTarget(path, symbol),
            "finding",
            "Using structured target carried by the finding.",
        )

    return CodeTargetResolution(
        CodeTarget("", ""),
        "unresolved",
        "No structured source target is available; title-based guessing is forbidden.",
    )
