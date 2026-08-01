"""Resolve parsed Python calls into project call-graph edges."""
from __future__ import annotations

from pathlib import Path

try:
    from artmach_assistant.core.path_normalizer import normalize_project_root, project_path
    from artmach_assistant.core.source_file_guard import SourceFileError, project_file
except ModuleNotFoundError:
    from core.path_normalizer import normalize_project_root, project_path
    from core.source_file_guard import SourceFileError, project_file

from ..project_symbol_registry import ProjectSymbolRegistry
from ..project_symbol_resolver import ProjectSymbolResolver
from .call_target_resolver import CallTargetResolver
from .model import CallGraphBuildResult, CallGraphEdge, CallSite
from .parser import CallSiteParser


class CallGraphBuilder:
    def __init__(
        self,
        project_root: str | Path,
        resolver: ProjectSymbolResolver,
        registry: ProjectSymbolRegistry,
    ) -> None:
        self.root = normalize_project_root(project_root)
        self._registry = registry
        self._parser = CallSiteParser()
        self._target_resolver = CallTargetResolver(self.root, resolver, registry)

    def build_file(self, path: str | Path) -> CallGraphBuildResult:
        try:
            absolute = self._project_path(path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return CallGraphBuildResult(
                str(path),
                (),
                (),
                parse_error=f"{type(exc).__name__}: {exc}",
            )
        call_sites, parse_error = self._parser.parse_file(absolute)
        if parse_error:
            return CallGraphBuildResult(str(absolute), (), (), parse_error=parse_error)

        edges: list[CallGraphEdge] = []
        unresolved = ambiguous = 0
        for call in call_sites:
            try:
                candidates, is_ambiguous = self._target_resolver.resolve(call, limit=25)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                unresolved += 1
                continue
            valid_candidates = []
            for target in candidates or ():
                try:
                    canonical_name = str(target.canonical_name).strip()
                    target_path = str(target.path).strip()
                    target_line = int(target.line)
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue
                if not canonical_name or not target_path or target_line < 1:
                    continue
                valid_candidates.append(target)
            candidates = tuple(valid_candidates)
            if not candidates:
                unresolved += 1
                continue
            if is_ambiguous:
                ambiguous += 1

            caller = self._resolve_caller(call)
            confidence = 1.0 if len(candidates) == 1 and not is_ambiguous else 0.5
            for target in candidates:
                edges.append(
                    CallGraphEdge(
                        caller_canonical_name=caller.canonical_name if caller else None,
                        caller_path=str(absolute),
                        caller_line=caller.line if caller else 0,
                        callee_canonical_name=target.canonical_name,
                        callee_path=target.path,
                        callee_line=target.line,
                        call_expression=call.expression,
                        call_line=call.line,
                        call_column=call.column,
                        confidence=confidence,
                    )
                )

        unique = {_edge_identity(edge): edge for edge in edges}
        ordered = tuple(
            sorted(
                unique.values(),
                key=lambda edge: (
                    edge.call_line,
                    edge.call_column,
                    edge.callee_canonical_name.casefold(),
                    edge.callee_path.casefold(),
                ),
            )
        )
        return CallGraphBuildResult(str(absolute), call_sites, ordered, unresolved, ambiguous)

    def _project_path(self, path: str | Path) -> Path:
        try:
            return project_file(self.root, path, must_exist=True)
        except SourceFileError:
            return project_path(self.root, path, require_inside=True)

    def _resolve_caller(self, call: CallSite):
        if not call.caller_qualified_name:
            return None
        try:
            symbols = self._registry.symbols_for_file(call.path)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        exact = [item for item in symbols if item.qualified_name == call.caller_qualified_name]
        if exact:
            return min(exact, key=lambda item: (item.end_line - item.line, -item.line))
        containing = [
            item
            for item in symbols
            if item.kind in {"function", "async_function", "method", "async_method"}
            and item.line <= call.line <= item.end_line
        ]
        return min(containing, key=lambda item: (item.end_line - item.line, -item.line)) if containing else None


def _edge_identity(
    edge: CallGraphEdge,
) -> tuple[str | None, str, int, int, str, int, str, int, str]:
    """Return a lossless identity for one resolved call edge.

    Multiple definitions can share the same canonical name and file.  The
    definition line (and the caller/call expression metadata) must therefore
    participate in deduplication or distinct targets collapse before they ever
    reach :class:`CallGraph`.
    """
    return (
        edge.caller_canonical_name,
        edge.caller_path,
        edge.caller_line,
        edge.call_line,
        edge.call_column,
        edge.callee_canonical_name,
        edge.callee_line,
        edge.callee_path,
        edge.call_expression,
    )
