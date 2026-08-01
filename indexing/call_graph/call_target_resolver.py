"""Context-aware, inheritance-aware resolution of Python call expressions."""
from __future__ import annotations

import math
from pathlib import Path

from ..project_symbol_registry import ProjectSymbol, ProjectSymbolRegistry
from ..project_symbol_resolver import ProjectSymbolResolver
from .model import CallSite

_CALLABLE_KINDS = {"function", "async_function", "method", "async_method", "class", "dataclass", "enum"}
_CLASS_KINDS = {"class", "dataclass", "enum"}
_METHOD_KINDS = {"method", "async_method"}
_CONSTRUCTOR_NAMES = ("__new__", "__init__")
_MAX_RESOLUTION_RESULTS = 10_000


class CallTargetResolver:
    """Resolve call-sites using lexical, import, constructor, and dispatch context."""

    def __init__(self, project_root: str | Path, resolver: ProjectSymbolResolver, registry: ProjectSymbolRegistry) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._resolver = resolver
        self._registry = registry

    def resolve(self, call: CallSite, *, limit: int = 25) -> tuple[tuple[ProjectSymbol, ...], bool]:
        normalized_call = self._normalize_call(call)
        if normalized_call is None:
            return (), False
        maximum = _positive_limit(limit, default=25)

        super_targets = self._super_targets(normalized_call)
        if super_targets:
            return super_targets[:maximum], len(super_targets) > 1

        ranked: list[ProjectSymbol] = []
        seen: set[tuple[str, int, int]] = set()
        ambiguous = False

        for query in self._candidate_queries(normalized_call):
            try:
                resolution = self._resolver.resolve(
                    query,
                    source_path=normalized_call.path,
                    scope=normalized_call.scope,
                    limit=max(maximum, 25),
                )
                definitions = getattr(resolution, "definitions", ())
                if definitions is None or isinstance(definitions, (str, bytes, Path)):
                    definitions = ()
                candidates = tuple(
                    item
                    for item in definitions
                    if isinstance(item, ProjectSymbol) and item.kind in _CALLABLE_KINDS
                )
                resolution_ambiguous = bool(getattr(resolution, "ambiguous", False))
            except (AttributeError, LookupError, OSError, RuntimeError, TypeError, ValueError):
                candidates = ()
                resolution_ambiguous = False
            if candidates:
                expanded = self._expand_constructors(candidates)
                ambiguous = ambiguous or resolution_ambiguous or self._is_true_ambiguity(expanded)
                _append_unique(ranked, seen, expanded)
                # Candidate queries are ordered from most contextual to least
                # contextual. Falling through after an exact dotted match can
                # pull unrelated same-name functions into the graph.
                break

        virtual = self._virtual_targets(normalized_call)
        if virtual:
            _append_unique(ranked, seen, virtual)
            ambiguous = ambiguous or len(ranked) > 1

        return tuple(ranked[:maximum]), ambiguous


    def _normalize_call(self, call: object) -> CallSite | None:
        """Validate a public call-site and bind its path to this project root."""
        if not isinstance(call, CallSite):
            return None
        if (
            isinstance(call.line, bool)
            or not isinstance(call.line, int)
            or call.line < 1
            or isinstance(call.column, bool)
            or not isinstance(call.column, int)
            or call.column < 0
            or not isinstance(call.expression, str)
            or not call.expression.strip()
        ):
            return None
        for value in (call.caller_qualified_name, call.scope):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return None
        if not isinstance(call.path, (str, Path)) or not str(call.path).strip() or "\x00" in str(call.path):
            return None
        try:
            candidate = Path(call.path).expanduser()
            if not candidate.is_absolute():
                candidate = self.root / candidate
            absolute = candidate.resolve(strict=False)
            absolute.relative_to(self.root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return CallSite(
            path=str(absolute),
            line=call.line,
            column=call.column,
            expression=call.expression.strip(),
            caller_qualified_name=(
                call.caller_qualified_name.strip()
                if call.caller_qualified_name is not None
                else None
            ),
            scope=call.scope.strip() if call.scope is not None else None,
        )

    def _candidate_queries(self, call: CallSite) -> tuple[str, ...]:
        expression = call.expression.strip()
        values: list[str] = []
        if expression.startswith(("self.", "cls.")):
            member = expression.split(".", 1)[1]
            owner = self._class_for_call(call)
            if owner is not None:
                values.append(f"{owner.qualified_name}.{member}")
            values.append(member)
        elif expression.startswith("super()."):
            values.append(expression.rsplit(".", 1)[-1])
        elif "." in expression:
            values.extend((expression, expression.rsplit(".", 1)[-1]))
        else:
            owner = self._class_for_call(call)
            if owner is not None:
                values.append(f"{owner.qualified_name}.{expression}")
            values.append(expression)
        return tuple(dict.fromkeys(item for item in values if item))

    def _expand_constructors(self, candidates: tuple[ProjectSymbol, ...]) -> tuple[ProjectSymbol, ...]:
        """Map class calls to their effective constructor methods when available."""
        expanded: list[ProjectSymbol] = []
        seen: set[tuple[str, int, int]] = set()
        for candidate in candidates:
            if candidate.kind not in _CLASS_KINDS:
                _append_unique(expanded, seen, (candidate,))
                continue
            constructors = self._constructors_for(candidate)
            _append_unique(expanded, seen, constructors or (candidate,))
        return tuple(expanded)

    def _constructors_for(self, cls: ProjectSymbol) -> tuple[ProjectSymbol, ...]:
        direct = self._methods_on_class(cls, _CONSTRUCTOR_NAMES)
        if direct:
            return direct
        for base in self._base_classes_of(cls):
            inherited = self._methods_on_class(base, _CONSTRUCTOR_NAMES)
            if inherited:
                return inherited
        return ()

    def _super_targets(self, call: CallSite) -> tuple[ProjectSymbol, ...]:
        expression = call.expression.strip()
        if not expression.startswith("super()."):
            return ()
        current = self._class_for_call(call)
        if current is None:
            return ()
        method_name = expression.rsplit(".", 1)[-1]
        targets: list[ProjectSymbol] = []
        seen: set[tuple[str, int, int]] = set()
        for base in self._base_classes_of(current):
            methods = self._methods_on_class(base, (method_name,))
            if methods:
                _append_unique(targets, seen, methods)
                break
        return tuple(targets)

    def _virtual_targets(self, call: CallSite) -> tuple[ProjectSymbol, ...]:
        expression = call.expression.strip()
        if not expression.startswith(("self.", "cls.")):
            return ()
        base = self._class_for_call(call)
        if base is None:
            return ()
        method_name = expression.split(".", 1)[1]
        targets: list[ProjectSymbol] = []
        for cls in (base, *self._descendants_of(base)):
            targets.extend(self._methods_on_class(cls, (method_name,)))
        return tuple(sorted(targets, key=lambda item: (item.path.casefold(), item.line, item.column)))

    def _class_for_call(self, call: CallSite) -> ProjectSymbol | None:
        """Return the innermost class containing the call site.

        Deriving the class by removing the last segment from the lexical caller
        fails for nested functions inside methods (``Class.method.inner``).
        Source ranges are authoritative and also handle nested classes.
        """
        caller = call.caller_qualified_name or ""
        try:
            symbols = self._registry.symbols_for_file(call.path)
            if symbols is None or isinstance(symbols, (str, bytes, Path)):
                symbols = ()
            symbols = tuple(symbols)
        except (LookupError, OSError, RuntimeError, TypeError, ValueError):
            symbols = ()
        classes = [
            item
            for item in symbols
            if isinstance(item, ProjectSymbol) and item.kind in _CLASS_KINDS
            and item.line <= call.line <= item.end_line
            and (
                not caller
                or caller == item.qualified_name
                or caller.startswith(f"{item.qualified_name}.")
            )
        ]
        if not classes:
            return None
        return min(classes, key=lambda item: (item.end_line - item.line, -item.line))

    def _methods_on_class(self, cls: ProjectSymbol, names: tuple[str, ...]) -> tuple[ProjectSymbol, ...]:
        qualified_names = {f"{cls.qualified_name}.{name}" for name in names}
        methods = [
            item
            for item in self._registry.symbols_for_file(cls.path)
            if item.kind in _METHOD_KINDS and item.qualified_name in qualified_names
        ]
        return tuple(sorted(methods, key=lambda item: (names.index(item.name), item.line, item.column)))

    def _base_classes_of(self, cls: ProjectSymbol) -> tuple[ProjectSymbol, ...]:
        all_classes = self._all_classes()
        ordered: list[ProjectSymbol] = []
        seen: set[tuple[str, int, int]] = set()
        frontier = list(cls.bases)
        while frontier:
            base_name = frontier.pop(0)
            matches = self._match_classes(base_name, cls, all_classes)
            for match in matches:
                key = (match.path, match.line, match.column)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(match)
                frontier.extend(match.bases)
        return tuple(ordered)

    @staticmethod
    def _match_classes(base_name: str, owner: ProjectSymbol, classes: list[ProjectSymbol]) -> tuple[ProjectSymbol, ...]:
        normalized = _normalize_base(base_name)
        if not normalized:
            return ()
        matches = [
            item
            for item in classes
            if normalized in {
                item.name.casefold(),
                item.qualified_name.casefold(),
                item.canonical_name.casefold(),
            }
        ]
        matches.sort(
            key=lambda item: (
                0 if item.module == owner.module else 1,
                0 if item.path == owner.path else 1,
                item.path.casefold(),
                item.line,
            )
        )
        return tuple(matches)

    def _descendants_of(self, base: ProjectSymbol) -> tuple[ProjectSymbol, ...]:
        all_classes = self._all_classes()
        descendants: list[ProjectSymbol] = []
        frontier = {base.canonical_name.casefold(), base.qualified_name.casefold(), base.name.casefold()}
        changed = True
        while changed:
            changed = False
            for item in all_classes:
                if item in descendants or item == base:
                    continue
                normalized_bases = {
                    normalized
                    for value in item.bases
                    if (normalized := _normalize_base(value))
                }
                if normalized_bases & frontier:
                    descendants.append(item)
                    frontier.update({item.canonical_name.casefold(), item.qualified_name.casefold(), item.name.casefold()})
                    changed = True
        return tuple(descendants)


    def _all_classes(self) -> list[ProjectSymbol]:
        try:
            values = self._registry.all_symbols()
            if values is None or isinstance(values, (str, bytes, Path)):
                return []
            return [
                item
                for item in values
                if isinstance(item, ProjectSymbol) and item.kind in _CLASS_KINDS
            ]
        except (LookupError, OSError, RuntimeError, TypeError, ValueError):
            return []

    @staticmethod
    def _is_true_ambiguity(candidates: tuple[ProjectSymbol, ...]) -> bool:
        """Multiple constructor hooks on one class are one call path, not ambiguity."""
        if len(candidates) <= 1:
            return False
        owners = {
            (item.path, item.qualified_name.rsplit(".", 1)[0])
            for item in candidates
            if item.kind in _METHOD_KINDS and item.name in _CONSTRUCTOR_NAMES
        }
        return not owners or len(owners) > 1


def _append_unique(
    target: list[ProjectSymbol],
    seen: set[tuple[str, int, int]],
    values: tuple[ProjectSymbol, ...] | list[ProjectSymbol],
) -> None:
    for item in values:
        key = (item.path, item.line, item.column)
        if key not in seen:
            seen.add(key)
            target.append(item)


def _normalize_base(value: object) -> str:
    """Normalize one inheritance token without trusting persisted metadata.

    Project symbols normally carry text-only ``bases`` values, but indexes may
    be loaded from older, partially written, or externally generated data. A
    single malformed base entry must not abort resolution for an entire source
    file. Empty and non-text values therefore behave as non-matches.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    return text.split("[", 1)[0].strip().casefold()


def _positive_limit(value: object, *, default: int) -> int:
    """Return a safe positive result bound for public resolver calls.

    ``bool`` is intentionally rejected even though it is an ``int`` subclass.
    Non-finite floats and malformed values fall back to the documented default
    instead of leaking conversion errors through Call Graph queries.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(numeric):
        return default
    try:
        return min(_MAX_RESOLUTION_RESULTS, max(1, int(numeric)))
    except (TypeError, ValueError, OverflowError):
        return default
