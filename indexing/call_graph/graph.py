"""Thread-safe incremental project call graph."""
from __future__ import annotations

from collections.abc import Iterable as IterableABC
from math import isfinite
from pathlib import Path
from threading import RLock

try:
    from artmach_assistant.core.path_normalizer import is_within_root, normalize_project_root, path_key, project_path
except ModuleNotFoundError:
    from core.path_normalizer import is_within_root, normalize_project_root, path_key, project_path
from typing import Any, Iterable

from .model import (
    CallGraphBuildResult,
    CallGraphDiagnosticsReport,
    CallGraphFileDiagnostics,
    CallGraphHotspot,
    CallGraphHotspotReport,
    CallGraphEdge,
    CallSite,
    CallGraphPath,
    CallGraphRecursionComponent,
    CallGraphRecursionReport,
    CallGraphReachabilityReport,
    CallGraphTraversalResult,
)


class CallGraph:
    def __init__(self, project_root: str | Path) -> None:
        self.root = normalize_project_root(project_root)
        self._edges_by_file: dict[str, tuple[CallGraphEdge, ...]] = {}
        self._diagnostics_by_file: dict[str, CallGraphFileDiagnostics] = {}
        self._incoming: dict[str, tuple[CallGraphEdge, ...]] = {}
        self._outgoing: dict[str, tuple[CallGraphEdge, ...]] = {}
        self._revision = 0
        self._lock = RLock()

    def replace_file(self, result: CallGraphBuildResult) -> bool:
        # Keep the last known-good edges while a file is temporarily invalid
        # during editing. A deleted file is removed explicitly via remove_file.
        prepared = self._prepare_update(result)
        if prepared is None:
            return False
        path, edges = prepared
        with self._lock:
            diagnostics = self._diagnostics_for_update(result, path)
            diagnostics_changed = self._diagnostics_by_file.get(path) != diagnostics
            self._diagnostics_by_file[path] = diagnostics
            if result.parse_error is not None:
                if diagnostics_changed:
                    self._revision += 1
                return diagnostics_changed
            edges_changed = self._edges_by_file.get(path) != edges
            if not edges_changed and not diagnostics_changed:
                return False
            self._edges_by_file[path] = edges
            self._revision += 1
            if edges_changed:
                self._rebuild()
            return True

    def replace_files(self, results: Iterable[CallGraphBuildResult]) -> bool:
        if isinstance(results, (str, bytes, Path)) or not isinstance(results, IterableABC):
            return False
        # A coalesced watcher/index queue can legitimately emit the same source
        # more than once in one batch. Only the final observation for a file is
        # authoritative. Applying intermediate records can otherwise replace a
        # last-known-good graph with stale edges just before a later parse error
        # for the same file asks us to preserve the previous valid state.
        prepared_by_path: dict[
            str, tuple[CallGraphBuildResult, str, tuple[CallGraphEdge, ...]]
        ] = {}
        try:
            for result in results:
                update = self._prepare_update(result)
                if update is None:
                    # Batch replacement is atomic: one invalid runtime record must
                    # not leave the graph partially updated.
                    return False
                path, edges = update
                prepared_by_path[path] = (result, path, edges)
        except (MemoryError, OSError, OverflowError, RecursionError, RuntimeError, TypeError, ValueError):
            # Iterables supplied by watcher/index integrations may fail while
            # being consumed. No graph state has been mutated at this point, so
            # fail closed and preserve the previous atomic snapshot.
            return False

        if not prepared_by_path:
            return False

        changed = False
        with self._lock:
            graph_changed = False
            for result, path, edges in prepared_by_path.values():
                diagnostics = self._diagnostics_for_update(result, path)
                if self._diagnostics_by_file.get(path) != diagnostics:
                    self._diagnostics_by_file[path] = diagnostics
                    changed = True
                if result.parse_error is not None:
                    continue
                if self._edges_by_file.get(path) != edges:
                    self._edges_by_file[path] = edges
                    changed = True
                    graph_changed = True
            if changed:
                self._revision += 1
                if graph_changed:
                    self._rebuild()
        return changed

    def _prepare_update(
        self, result: CallGraphBuildResult
    ) -> tuple[str, tuple[CallGraphEdge, ...]] | None:
        """Validate one live update before it can mutate graph state."""
        if not isinstance(result, CallGraphBuildResult):
            return None
        path = self._project_path(result.path)
        if not self._is_inside_root(path):
            return None
        if not self._valid_build_metadata(result, path):
            return None
        if result.parse_error is not None:
            return (path, ())

        edges: list[CallGraphEdge] = []
        seen: set[tuple[str, str, int, str, str, int, str, int, int]] = set()
        for edge in result.edges:
            if not isinstance(edge, CallGraphEdge):
                return None
            caller_path = self._project_path(edge.caller_path)
            callee_path = self._project_path(edge.callee_path)
            if caller_path != path or not self._is_inside_root(callee_path):
                return None
            if edge.caller_canonical_name is not None and (
                not isinstance(edge.caller_canonical_name, str)
                or not edge.caller_canonical_name.strip()
            ):
                return None
            if not isinstance(edge.callee_canonical_name, str) or not edge.callee_canonical_name.strip():
                return None
            if not isinstance(edge.call_expression, str) or not edge.call_expression.strip():
                return None
            numeric_lines = (edge.caller_line, edge.callee_line, edge.call_line, edge.call_column)
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in numeric_lines):
                return None
            if (
                isinstance(edge.confidence, bool)
                or not isinstance(edge.confidence, (int, float))
                or not isfinite(float(edge.confidence))
                or not 0.0 <= float(edge.confidence) <= 1.0
            ):
                return None

            normalized = CallGraphEdge(
                caller_canonical_name=(
                    edge.caller_canonical_name.strip()
                    if edge.caller_canonical_name is not None
                    else None
                ),
                caller_path=caller_path,
                caller_line=edge.caller_line,
                callee_canonical_name=edge.callee_canonical_name.strip(),
                callee_path=callee_path,
                callee_line=edge.callee_line,
                call_expression=edge.call_expression.strip(),
                call_line=edge.call_line,
                call_column=edge.call_column,
                confidence=float(edge.confidence),
            )
            key = _edge_key(normalized)
            if key not in seen:
                seen.add(key)
                edges.append(normalized)
        return path, tuple(sorted(edges, key=_edge_key))

    def _valid_build_metadata(self, result: CallGraphBuildResult, path: str) -> bool:
        """Reject malformed live diagnostics before they can alter graph state."""
        if not isinstance(result.call_sites, tuple) or not isinstance(result.edges, tuple):
            return False
        counters = (result.unresolved_calls, result.ambiguous_calls)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
            return False
        if result.parse_error is not None:
            return (
                isinstance(result.parse_error, str)
                and bool(result.parse_error.strip())
                and not result.call_sites
                and not result.edges
                and result.unresolved_calls == 0
                and result.ambiguous_calls == 0
            )

        for call in result.call_sites:
            if not isinstance(call, CallSite):
                return False
            call_path = self._project_path(call.path)
            if call_path != path:
                return False
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
                return False
            for value in (call.caller_qualified_name, call.scope):
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    return False

        call_count = len(result.call_sites)
        if result.unresolved_calls > call_count:
            return False
        resolved = call_count - result.unresolved_calls
        return result.ambiguous_calls <= resolved

    def remove_file(self, path: str | Path) -> bool:
        absolute = self._project_path(path)
        if absolute is None or not self._is_inside_root(absolute):
            return False
        with self._lock:
            changed = self._edges_by_file.pop(absolute, None) is not None
            changed = self._diagnostics_by_file.pop(absolute, None) is not None or changed
            for source, values in tuple(self._edges_by_file.items()):
                filtered = tuple(edge for edge in values if edge.callee_path != absolute)
                if filtered != values:
                    self._edges_by_file[source] = filtered
                    changed = True
            if changed:
                self._revision += 1
                self._rebuild()
            return changed

    def clear(self) -> None:
        with self._lock:
            self._edges_by_file.clear()
            self._diagnostics_by_file.clear()
            self._incoming.clear()
            self._outgoing.clear()
            self._revision += 1

    def callers(self, canonical_name: str, *, limit: int = 1000) -> tuple[CallGraphEdge, ...]:
        key = _symbol_key(canonical_name)
        if key is None:
            return ()
        with self._lock:
            return self._incoming.get(key, ())[:_bounded_positive_int(limit, default=1000, maximum=10_000)]

    def callees(self, canonical_name: str, *, limit: int = 1000) -> tuple[CallGraphEdge, ...]:
        key = _symbol_key(canonical_name)
        if key is None:
            return ()
        with self._lock:
            return self._outgoing.get(key, ())[:_bounded_positive_int(limit, default=1000, maximum=10_000)]

    def edges_for_file(self, path: str | Path, *, limit: int = 2000) -> tuple[CallGraphEdge, ...]:
        absolute = self._project_path(path)
        if absolute is None or not self._is_inside_root(absolute):
            return ()
        with self._lock:
            return self._edges_by_file.get(absolute, ())[:_bounded_positive_int(limit, default=2000, maximum=10_000)]

    def traverse_callees(
        self,
        canonical_name: str,
        *,
        max_depth: int = 5,
        max_paths: int = 1000,
    ) -> CallGraphTraversalResult:
        """Return bounded outgoing call paths with cycle detection."""
        return self._traverse(
            canonical_name, direction="callees", max_depth=max_depth, max_paths=max_paths
        )

    def traverse_callers(
        self,
        canonical_name: str,
        *,
        max_depth: int = 5,
        max_paths: int = 1000,
    ) -> CallGraphTraversalResult:
        """Return bounded incoming call paths with cycle detection."""
        return self._traverse(
            canonical_name, direction="callers", max_depth=max_depth, max_paths=max_paths
        )

    def _traverse(
        self,
        canonical_name: str,
        *,
        direction: str,
        max_depth: int,
        max_paths: int,
    ) -> CallGraphTraversalResult:
        root = canonical_name.strip() if isinstance(canonical_name, str) else ""
        depth_limit = _bounded_positive_int(max_depth, default=5, maximum=1_000)
        path_limit = _bounded_positive_int(max_paths, default=1000, maximum=10_000)
        if not root:
            return CallGraphTraversalResult(root, direction, depth_limit, ())

        with self._lock:
            adjacency = self._outgoing if direction == "callees" else self._incoming
            snapshot = dict(adjacency)

        paths: list[CallGraphPath] = []
        truncated = False

        # Traverse with explicit frames instead of recursive calls. Workspace
        # call chains can exceed Python's recursion limit even when max_depth
        # is intentionally configured in the thousands.
        stack: list[dict[str, Any]] = [{
            "current": root,
            "symbols": (root,),
            "edges": (),
            "visited": frozenset({root.casefold()}),
            "candidates": None,
            "index": 0,
            "advanced": False,
        }]
        while stack:
            if len(paths) >= path_limit:
                truncated = True
                break

            frame = stack[-1]
            frame_edges = frame["edges"]
            if frame["candidates"] is None:
                if len(frame_edges) >= depth_limit:
                    if frame_edges:
                        paths.append(CallGraphPath(frame["symbols"], frame_edges, False))
                    stack.pop()
                    continue
                frame["candidates"] = snapshot.get(frame["current"].casefold(), ())

            candidates = frame["candidates"]
            if frame["index"] >= len(candidates):
                if not frame["advanced"] and frame_edges:
                    paths.append(CallGraphPath(frame["symbols"], frame_edges, False))
                stack.pop()
                continue

            edge = candidates[frame["index"]]
            frame["index"] += 1
            next_symbol = (
                edge.callee_canonical_name
                if direction == "callees"
                else edge.caller_canonical_name
            )
            if not next_symbol:
                continue

            frame["advanced"] = True
            next_key = next_symbol.casefold()
            next_symbols = frame["symbols"] + (next_symbol,)
            next_edges = frame_edges + (edge,)
            if next_key in frame["visited"]:
                paths.append(CallGraphPath(next_symbols, next_edges, True))
                continue

            stack.append({
                "current": next_symbol,
                "symbols": next_symbols,
                "edges": next_edges,
                "visited": frame["visited"] | {next_key},
                "candidates": None,
                "index": 0,
                "advanced": False,
            })
        ordered = tuple(
            sorted(
                paths,
                key=lambda item: (
                    item.is_cycle,
                    tuple(value.casefold() for value in item.symbols),
                    tuple(_edge_key(edge) for edge in item.edges),
                ),
            )
        )
        return CallGraphTraversalResult(root, direction, depth_limit, ordered, truncated)


    def recursion_report(self, *, limit: int = 1000) -> CallGraphRecursionReport:
        """Return strongly connected recursive symbol groups deterministically."""
        component_limit = _bounded_positive_int(limit, default=1000, maximum=10_000)
        with self._lock:
            all_edges = tuple(
                edge
                for values in self._edges_by_file.values()
                for edge in values
                if edge.caller_canonical_name
            )

        names: dict[str, str] = {}
        adjacency: dict[str, set[str]] = {}
        edges_by_pair: dict[tuple[str, str], list[CallGraphEdge]] = {}
        for edge in all_edges:
            caller = edge.caller_canonical_name
            if not caller:
                continue
            caller_key = caller.casefold()
            callee_key = edge.callee_canonical_name.casefold()
            names.setdefault(caller_key, caller)
            names.setdefault(callee_key, edge.callee_canonical_name)
            adjacency.setdefault(caller_key, set()).add(callee_key)
            adjacency.setdefault(callee_key, set())
            edges_by_pair.setdefault((caller_key, callee_key), []).append(edge)

        # Compute strongly connected components without recursive calls.
        # Large workspaces can contain call chains longer than Python's call
        # stack, so recursive Tarjan traversal can fail with RecursionError.
        reverse: dict[str, set[str]] = {node: set() for node in adjacency}
        for source, targets in adjacency.items():
            for target in targets:
                reverse.setdefault(target, set()).add(source)

        visited: set[str] = set()
        finish_order: list[str] = []
        for start_node in sorted(adjacency):
            if start_node in visited:
                continue
            visited.add(start_node)
            stack: list[tuple[str, bool]] = [(start_node, False)]
            while stack:
                node, expanded = stack.pop()
                if expanded:
                    finish_order.append(node)
                    continue
                stack.append((node, True))
                for target in sorted(adjacency.get(node, ()), reverse=True):
                    if target not in visited:
                        visited.add(target)
                        stack.append((target, False))

        assigned: set[str] = set()
        raw_components: list[tuple[str, ...]] = []
        for start_node in reversed(finish_order):
            if start_node in assigned:
                continue
            assigned.add(start_node)
            component: list[str] = []
            stack = [start_node]
            while stack:
                node = stack.pop()
                component.append(node)
                for source in sorted(reverse.get(node, ()), reverse=True):
                    if source not in assigned:
                        assigned.add(source)
                        stack.append(source)
            raw_components.append(tuple(sorted(component)))

        components: list[CallGraphRecursionComponent] = []
        for keys in raw_components:
            direct = len(keys) == 1 and keys[0] in adjacency.get(keys[0], set())
            if len(keys) == 1 and not direct:
                continue
            key_set = set(keys)
            component_edges = tuple(sorted(
                (
                    edge
                    for (source, target), values in edges_by_pair.items()
                    if source in key_set and target in key_set
                    for edge in values
                ),
                key=_edge_key,
            ))
            components.append(CallGraphRecursionComponent(
                symbols=tuple(names[key] for key in keys),
                edges=component_edges,
                is_direct=direct,
            ))

        ordered = tuple(sorted(
            components,
            key=lambda item: (tuple(value.casefold() for value in item.symbols), item.is_direct),
        ))
        return CallGraphRecursionReport(
            components=ordered[:component_limit],
            total_components=len(ordered),
            recursive_symbols=sum(len(item.symbols) for item in ordered),
            truncated=len(ordered) > component_limit,
        )


    def hotspot_report(
        self,
        *,
        limit: int = 100,
        min_score: float = 1.0,
    ) -> CallGraphHotspotReport:
        """Rank symbols by call pressure without mutating the graph."""
        result_limit = _bounded_positive_int(limit, default=100, maximum=10_000)
        threshold = _non_negative_float(min_score, default=1.0)
        with self._lock:
            incoming = dict(self._incoming)
            outgoing = dict(self._outgoing)

        recursive_symbols = {
            symbol.casefold()
            for component in self.recursion_report(limit=1_000_000).components
            for symbol in component.symbols
        }
        keys = set(incoming) | set(outgoing)
        hotspots: list[CallGraphHotspot] = []
        for key in keys:
            incoming_edges = incoming.get(key, ())
            outgoing_edges = outgoing.get(key, ())
            display_name = (
                incoming_edges[0].callee_canonical_name
                if incoming_edges
                else outgoing_edges[0].caller_canonical_name
            )
            if not display_name:
                continue
            caller_files = len({edge.caller_path.casefold() for edge in incoming_edges})
            callee_files = len({edge.callee_path.casefold() for edge in outgoing_edges})
            incoming_count = len(incoming_edges)
            outgoing_count = len(outgoing_edges)
            recursive = key in recursive_symbols
            score = (
                incoming_count * 2.0
                + outgoing_count
                + caller_files * 1.5
                + callee_files * 0.5
                + (2.0 if recursive else 0.0)
            )
            if score < threshold:
                continue
            hotspots.append(CallGraphHotspot(
                canonical_name=display_name,
                incoming_calls=incoming_count,
                outgoing_calls=outgoing_count,
                caller_files=caller_files,
                callee_files=callee_files,
                score=score,
                recursive=recursive,
            ))

        ordered = tuple(sorted(
            hotspots,
            key=lambda item: (
                -item.score,
                -item.incoming_calls,
                -item.outgoing_calls,
                item.canonical_name.casefold(),
            ),
        ))
        return CallGraphHotspotReport(
            hotspots=ordered[:result_limit],
            total_symbols=len(ordered),
            truncated=len(ordered) > result_limit,
        )

    def reachability_report(
        self,
        entry_points: Iterable[str] | None = None,
        *,
        limit: int = 5000,
    ) -> CallGraphReachabilityReport:
        """Return symbols reachable from explicit or safely inferred entry points.

        Inferred roots include module-level calls and symbols that have outgoing
        calls but no incoming calls. Disconnected recursive components therefore
        remain visible as unreachable instead of being silently treated as roots.
        """
        result_limit = _bounded_positive_int(limit, default=5000, maximum=10_000)
        with self._lock:
            all_edges = tuple(
                edge for values in self._edges_by_file.values() for edge in values
            )

        names: dict[str, str] = {}
        adjacency: dict[str, set[str]] = {}
        incoming: set[str] = set()
        inferred_roots: set[str] = set()
        for edge in all_edges:
            callee_key = edge.callee_canonical_name.casefold()
            names.setdefault(callee_key, edge.callee_canonical_name)
            adjacency.setdefault(callee_key, set())
            incoming.add(callee_key)
            if edge.caller_canonical_name:
                caller_key = edge.caller_canonical_name.casefold()
                names.setdefault(caller_key, edge.caller_canonical_name)
                adjacency.setdefault(caller_key, set()).add(callee_key)
            else:
                inferred_roots.add(callee_key)

        if entry_points is None:
            roots = inferred_roots | {key for key in adjacency if key not in incoming}
        else:
            # A canonical symbol name is itself iterable. Treating a direct
            # string argument as a generic iterable silently turns ``main``
            # into the entry points ``m``, ``a``, ``i`` and ``n``. Public
            # callers commonly pass a single symbol, so normalize it to a
            # one-item sequence before filtering the collection.
            values: Iterable[object]
            if isinstance(entry_points, str):
                values = (entry_points,)
            elif isinstance(entry_points, IterableABC):
                values = entry_points
            else:
                values = ()
            try:
                roots = {
                    value.strip().casefold()
                    for value in values
                    if isinstance(value, str) and value.strip()
                } & set(adjacency)
            except (OSError, RuntimeError, TypeError, ValueError):
                roots = set()

        reachable: set[str] = set()
        stack = sorted(roots, reverse=True)
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            stack.extend(
                target
                for target in sorted(adjacency.get(current, ()), reverse=True)
                if target not in reachable
            )

        unreachable = set(adjacency) - reachable
        ordered_roots = tuple(names[key] for key in sorted(roots))
        ordered_reachable = tuple(names[key] for key in sorted(reachable))
        ordered_unreachable = tuple(names[key] for key in sorted(unreachable))
        truncated = (
            len(ordered_reachable) > result_limit
            or len(ordered_unreachable) > result_limit
        )
        return CallGraphReachabilityReport(
            entry_points=ordered_roots,
            reachable_symbols=ordered_reachable[:result_limit],
            unreachable_symbols=ordered_unreachable[:result_limit],
            total_symbols=len(adjacency),
            truncated=truncated,
        )

    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic JSON-serializable graph snapshot."""
        with self._lock:
            return {
                "revision": self._revision,
                "diagnostics_by_file": {
                    path: {
                        "call_sites": item.call_sites,
                        "resolved_calls": item.resolved_calls,
                        "unresolved_calls": item.unresolved_calls,
                        "ambiguous_calls": item.ambiguous_calls,
                        "parse_error": item.parse_error,
                    }
                    for path, item in sorted(
                        self._diagnostics_by_file.items(), key=lambda pair: pair[0].casefold()
                    )
                },
                "edges_by_file": {
                    path: [
                        {
                            "caller_canonical_name": edge.caller_canonical_name,
                            "caller_path": edge.caller_path,
                            "caller_line": edge.caller_line,
                            "callee_canonical_name": edge.callee_canonical_name,
                            "callee_path": edge.callee_path,
                            "callee_line": edge.callee_line,
                            "call_expression": edge.call_expression,
                            "call_line": edge.call_line,
                            "call_column": edge.call_column,
                            "confidence": edge.confidence,
                        }
                        for edge in values
                    ]
                    for path, values in sorted(
                        self._edges_by_file.items(), key=lambda item: item[0].casefold()
                    )
                },
            }

    def load_snapshot(self, payload: dict[str, Any]) -> bool:
        """Restore a snapshot only when all paths and edge records are valid."""
        if not isinstance(payload, dict):
            return False
        raw_files = payload.get("edges_by_file")
        if not isinstance(raw_files, dict):
            return False

        restored: dict[str, tuple[CallGraphEdge, ...]] = {}
        restored_path_keys: set[str] = set()
        try:
            revision = _snapshot_non_negative_int(payload.get("revision", 0))
            for raw_path, raw_edges in raw_files.items():
                if not isinstance(raw_path, str) or not isinstance(raw_edges, list):
                    return False
                path = self._snapshot_path(raw_path)
                if path is None:
                    return False
                path_key = _path_key(path)
                if path_key in restored_path_keys:
                    return False
                restored_path_keys.add(path_key)

                edges: list[CallGraphEdge] = []
                seen: set[tuple[str, str, int, str, str, int, str, int, int]] = set()
                for item in raw_edges:
                    if not isinstance(item, dict):
                        return False

                    caller_name = item.get("caller_canonical_name")
                    if caller_name is not None and (
                        not isinstance(caller_name, str) or not caller_name.strip()
                    ):
                        return False
                    callee_name = item.get("callee_canonical_name")
                    call_expression = item.get("call_expression")
                    if not isinstance(callee_name, str) or not callee_name.strip():
                        return False
                    if not isinstance(call_expression, str) or not call_expression.strip():
                        return False

                    caller_path = self._snapshot_path(item.get("caller_path"))
                    callee_path = self._snapshot_path(item.get("callee_path"))
                    if caller_path is None or callee_path is None or caller_path != path:
                        return False

                    confidence = _snapshot_probability(item.get("confidence"))
                    caller_line = _snapshot_non_negative_int(item.get("caller_line"))
                    callee_line = _snapshot_non_negative_int(item.get("callee_line"))
                    call_line = _snapshot_non_negative_int(item.get("call_line"))
                    call_column = _snapshot_non_negative_int(item.get("call_column"))
                    if callee_line <= 0 or call_line <= 0:
                        return False

                    edge = CallGraphEdge(
                        caller_canonical_name=(
                            caller_name.strip() if caller_name is not None else None
                        ),
                        caller_path=caller_path,
                        caller_line=caller_line,
                        callee_canonical_name=callee_name.strip(),
                        callee_path=callee_path,
                        callee_line=callee_line,
                        call_expression=call_expression.strip(),
                        call_line=call_line,
                        call_column=call_column,
                        confidence=confidence,
                    )
                    key = _edge_key(edge)
                    if key not in seen:
                        seen.add(key)
                        edges.append(edge)
                restored[path] = tuple(sorted(edges, key=_edge_key))
        except (MemoryError, OSError, OverflowError, RecursionError, RuntimeError, TypeError, ValueError):
            return False

        restored_diagnostics: dict[str, CallGraphFileDiagnostics] = {}
        restored_diagnostic_keys: set[str] = set()
        raw_diagnostics = payload.get("diagnostics_by_file", {})
        if not isinstance(raw_diagnostics, dict):
            return False
        try:
            for raw_path, item in raw_diagnostics.items():
                if not isinstance(raw_path, str) or not isinstance(item, dict):
                    return False
                path = self._snapshot_path(raw_path)
                if path is None:
                    return False
                path_key = _path_key(path)
                if path_key in restored_diagnostic_keys:
                    return False
                restored_diagnostic_keys.add(path_key)

                parse_error = item.get("parse_error")
                if parse_error is not None and (
                    not isinstance(parse_error, str) or not parse_error.strip()
                ):
                    return False
                call_sites = _snapshot_non_negative_int(item.get("call_sites", 0))
                resolved = _snapshot_non_negative_int(item.get("resolved_calls", 0))
                unresolved = _snapshot_non_negative_int(item.get("unresolved_calls", 0))
                ambiguous = _snapshot_non_negative_int(item.get("ambiguous_calls", 0))
                if resolved + unresolved != call_sites or ambiguous > resolved:
                    return False
                restored_diagnostics[path] = CallGraphFileDiagnostics(
                    path=path,
                    call_sites=call_sites,
                    resolved_calls=resolved,
                    unresolved_calls=unresolved,
                    ambiguous_calls=ambiguous,
                    parse_error=parse_error or None,
                )
        except (MemoryError, OSError, OverflowError, RecursionError, RuntimeError, TypeError, ValueError):
            return False

        with self._lock:
            self._edges_by_file = restored
            self._diagnostics_by_file = restored_diagnostics
            self._revision = revision
            self._rebuild()
        return True

    def _snapshot_path(self, value: object) -> str | None:
        """Normalize one persisted path without accepting relative or coerced values."""
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return None
        resolved = str(candidate.resolve(strict=False))
        return resolved if self._is_inside_root(resolved) else None

    def validate(self, *, repair: bool = False) -> int:
        """Return invalid edge count and optionally remove stale file edges."""
        with self._lock:
            invalid = 0
            cleaned: dict[str, tuple[CallGraphEdge, ...]] = {}
            for source_path, values in self._edges_by_file.items():
                source_valid = Path(source_path).is_file() and self._is_inside_root(source_path)
                kept: list[CallGraphEdge] = []
                seen: set[tuple[str, str, int, str, str, int, str, int, int]] = set()
                for edge in values:
                    key = _edge_key(edge)
                    valid = (
                        source_valid
                        and edge.caller_path == source_path
                        and Path(edge.callee_path).is_file()
                        and self._is_inside_root(edge.callee_path)
                        and key not in seen
                    )
                    seen.add(key)
                    if valid:
                        kept.append(edge)
                    else:
                        invalid += 1
                if source_valid:
                    cleaned[source_path] = tuple(sorted(kept, key=_edge_key))
            cleaned_diagnostics = {
                path: item
                for path, item in self._diagnostics_by_file.items()
                if Path(path).is_file() and self._is_inside_root(path)
            }
            invalid_diagnostics = len(self._diagnostics_by_file) - len(cleaned_diagnostics)
            diagnostics_changed = invalid_diagnostics > 0
            if repair and (cleaned != self._edges_by_file or diagnostics_changed):
                self._edges_by_file = cleaned
                self._diagnostics_by_file = cleaned_diagnostics
                self._revision += 1
                self._rebuild()
            return invalid + invalid_diagnostics

    def diagnostics(self, *, limit: int = 5000) -> CallGraphDiagnosticsReport:
        """Return bounded file details plus complete aggregate coverage counts."""
        with self._lock:
            all_files = tuple(sorted(
                self._diagnostics_by_file.values(), key=lambda item: item.path.casefold()
            ))
        return CallGraphDiagnosticsReport(
            files=all_files[:_bounded_positive_int(limit, default=5000, maximum=10_000)],
            total_call_sites=sum(item.call_sites for item in all_files),
            resolved_calls=sum(item.resolved_calls for item in all_files),
            unresolved_calls=sum(item.unresolved_calls for item in all_files),
            ambiguous_calls=sum(item.ambiguous_calls for item in all_files),
            parse_errors=sum(1 for item in all_files if item.parse_error),
        )

    def _diagnostics_for_update(
        self, result: CallGraphBuildResult, path: str
    ) -> CallGraphFileDiagnostics:
        """Preserve last-known-good coverage while a file is temporarily invalid."""
        if result.parse_error is not None:
            previous = self._diagnostics_by_file.get(path)
            if previous is not None:
                return CallGraphFileDiagnostics(
                    path=path,
                    call_sites=previous.call_sites,
                    resolved_calls=previous.resolved_calls,
                    unresolved_calls=previous.unresolved_calls,
                    ambiguous_calls=previous.ambiguous_calls,
                    parse_error=result.parse_error,
                )
        return self._diagnostics_from_result(result, path)

    @staticmethod
    def _diagnostics_from_result(
        result: CallGraphBuildResult, path: str
    ) -> CallGraphFileDiagnostics:
        unresolved = max(0, int(result.unresolved_calls))
        ambiguous = max(0, int(result.ambiguous_calls))
        call_sites = len(result.call_sites)
        # Ambiguous calls are resolved to multiple candidates; keep them in
        # resolved_calls and report ambiguity as an overlapping quality flag.
        unresolved = min(call_sites, unresolved)
        resolved = call_sites - unresolved
        ambiguous = min(resolved, ambiguous)
        return CallGraphFileDiagnostics(
            path=path,
            call_sites=call_sites,
            resolved_calls=resolved,
            unresolved_calls=unresolved,
            ambiguous_calls=ambiguous,
            parse_error=result.parse_error,
        )

    def _project_path(self, value: object) -> str | None:
        try:
            return str(project_path(self.root, value, require_inside=True))
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _is_inside_root(self, path: str | Path) -> bool:
        return is_within_root(self.root, path)

    def stats(self) -> dict[str, int]:
        report = self.diagnostics()
        recursion = self.recursion_report()
        hotspots = self.hotspot_report(limit=1_000_000)
        reachability = self.reachability_report(limit=1_000_000)
        with self._lock:
            return {
                "call_graph_edges": sum(len(values) for values in self._edges_by_file.values()),
                "call_graph_files": len(self._edges_by_file),
                "call_graph_revision": self._revision,
                "call_graph_call_sites": report.total_call_sites,
                "call_graph_resolved_calls": report.resolved_calls,
                "call_graph_unresolved_calls": report.unresolved_calls,
                "call_graph_ambiguous_calls": report.ambiguous_calls,
                "call_graph_parse_errors": report.parse_errors,
                "call_graph_recursion_components": recursion.total_components,
                "call_graph_recursive_symbols": recursion.recursive_symbols,
                "call_graph_hotspot_symbols": hotspots.total_symbols,
                "call_graph_reachable_symbols": len(reachability.reachable_symbols),
                "call_graph_unreachable_symbols": len(reachability.unreachable_symbols),
            }

    def _rebuild(self) -> None:
        incoming: dict[str, list[CallGraphEdge]] = {}
        outgoing: dict[str, list[CallGraphEdge]] = {}
        for values in self._edges_by_file.values():
            for edge in values:
                incoming.setdefault(edge.callee_canonical_name.casefold(), []).append(edge)
                if edge.caller_canonical_name:
                    outgoing.setdefault(edge.caller_canonical_name.casefold(), []).append(edge)
        self._incoming = {key: tuple(sorted(values, key=_edge_key)) for key, values in incoming.items()}
        self._outgoing = {key: tuple(sorted(values, key=_edge_key)) for key, values in outgoing.items()}



def _bounded_positive_int(value: object, *, default: int, maximum: int) -> int:
    """Return a finite positive integer constrained to a safe upper bound."""
    if isinstance(value, bool):
        result = default
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not isfinite(value):
            result = default
        else:
            result = int(value)
    else:
        result = default
    return min(max(1, result), max(1, maximum))


def _non_negative_float(value: object, *, default: float) -> float:
    """Return a finite non-negative report threshold."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    result = float(value)
    return max(0.0, result) if isfinite(result) else default


def _path_key(value: str | Path) -> str:
    """Return a platform-stable key for one normalized project path."""
    return path_key(value)


def _symbol_key(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def _absolute_path(value: object) -> str | None:
    try:
        return str(project_path(Path.cwd(), value, require_inside=False))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

def _snapshot_non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Snapshot integer field is invalid.")
    if value < 0:
        raise ValueError("Snapshot integer field must be non-negative.")
    return value


def _snapshot_probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Snapshot probability field is invalid.")
    result = float(value)
    if not isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("Snapshot probability field must be between 0 and 1.")
    return result


def _edge_key(
    edge: CallGraphEdge,
) -> tuple[str, str, int, str, str, int, str, int, int]:
    """Return the complete deterministic identity of a resolved call edge.

    A single call site can legitimately resolve to multiple definitions that
    share a canonical name and file but live on different source lines (for
    example overloads or generated/redeclared symbols). Omitting definition
    and caller identity fields would silently collapse those distinct edges.
    Confidence is intentionally excluded because it describes an edge rather
    than identifying a different target.
    """
    return (
        (edge.caller_canonical_name or "").casefold(),
        edge.caller_path.casefold(),
        edge.caller_line,
        edge.callee_canonical_name.casefold(),
        edge.callee_path.casefold(),
        edge.callee_line,
        edge.call_expression.casefold(),
        edge.call_line,
        edge.call_column,
    )
