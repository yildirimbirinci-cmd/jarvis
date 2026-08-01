from __future__ import annotations

from pathlib import Path
from threading import Event, RLock, Thread, current_thread

from artmach_assistant.core.call_graph_store import CallGraphStore
from artmach_assistant.core.dependency_index_store import DependencyIndexStore
from artmach_assistant.core.global_symbol_graph_store import GlobalSymbolGraphStore
from artmach_assistant.core.language_symbol_mapper import LanguageSymbolMapper, MappedSymbol
from artmach_assistant.core.project_index import IGNORED_DIRS
from artmach_assistant.core.service_status import service_status_registry
from artmach_assistant.core.symbol_call_hierarchy_service import (
    SymbolCallHierarchyResult,
    SymbolCallHierarchyService,
)
from artmach_assistant.core.symbol_impact_analysis_service import (
    SymbolImpactAnalysisService,
    SymbolImpactResult,
)
from artmach_assistant.core.symbol_navigation_service import (
    SymbolNavigationResult,
    SymbolNavigationService,
)
from artmach_assistant.core.workspace_watch import WorkspaceChange
from artmach_assistant.indexing import (
    CallGraph,
    CallGraphBuilder,
    CallGraphTraversalResult,
    CallGraphDiagnosticsReport,
    CallGraphHotspotReport,
    CallGraphRecursionReport,
    CallGraphReachabilityReport,
    CrossFileReferenceResolver,
    GlobalSymbolGraph,
    CrossFileSymbolResolver,
    ReferenceBindingResult,
    DependencyResolver,
    DependencyScanResult,
    ProjectSymbolIndex,
    ProjectSymbolResolution,
    ProjectSymbolResolver,
    SemanticGraph,
    SymbolIndex,
    SymbolReferenceIndex,
    ResolvedType,
    SymbolGraphUpdatePlanner,
    TypeIndex,
)


class DependencyIndexService:
    """Maintains the Python dependency graph without blocking UI startup."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._operation_lock = RLock()
        self._lifecycle_lock = RLock()
        self._generation = 0
        self._resolver: DependencyResolver | None = None
        self._root: Path | None = None
        self._build_thread: Thread | None = None
        self._stop_event = Event()
        self._ready = Event()
        self._last_results: tuple[DependencyScanResult, ...] = ()
        self._last_affected: tuple[Path, ...] = ()
        self._store = DependencyIndexStore()
        self._call_graph_store = CallGraphStore()
        self._global_symbol_store = GlobalSymbolGraphStore()
        self._semantic_graph: SemanticGraph | None = None
        self._symbol_index: SymbolIndex | None = None
        self._symbol_reference_index: SymbolReferenceIndex | None = None
        self._type_index: TypeIndex | None = None
        self._project_symbol_index: ProjectSymbolIndex | None = None
        self._project_symbol_resolver: ProjectSymbolResolver | None = None
        self._cross_file_reference_resolver: CrossFileReferenceResolver | None = None
        self._global_symbol_graph: GlobalSymbolGraph | None = None
        self._call_graph: CallGraph | None = None
        self._call_graph_builder: CallGraphBuilder | None = None
        self._symbol_navigation: SymbolNavigationService | None = None
        self._symbol_call_hierarchy: SymbolCallHierarchyService | None = None
        self._symbol_impact_analysis: SymbolImpactAnalysisService | None = None
        self._loaded_from_cache = False
        self._global_symbol_repairs = 0
        self._language_symbol_mapper = LanguageSymbolMapper()
        service_status_registry.ensure("dependency_index")

    @property
    def is_running(self) -> bool:
        thread = self._build_thread
        return bool(thread and thread.is_alive())

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def last_affected(self) -> tuple[Path, ...]:
        with self._lock:
            return self._last_affected

    def start(self, root: Path) -> None:
        with self._lifecycle_lock:
            self._stop_locked()
            # A previous authoritative rebuild may still be unwinding after the
            # bounded stop timeout. Starting another worker in that window would
            # allow two generations to write the same persistent stores.
            if self.is_running:
                service_status_registry.set_state(
                    "dependency_index",
                    "stopping",
                    "Önceki bağımlılık indeksleme görevi durduruluyor.",
                )
                return
            try:
                resolved_root = Path(root).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, TypeError, ValueError):
                return
            if not resolved_root.is_dir():
                return
            self._start_locked(resolved_root)

    def _start_locked(self, resolved_root: Path) -> None:
        """Create one authoritative generation while lifecycle changes are serialized."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._root = resolved_root
            self._resolver = DependencyResolver(resolved_root)
            self._semantic_graph = SemanticGraph(resolved_root)
            self._symbol_index = SymbolIndex(resolved_root)
            self._symbol_reference_index = SymbolReferenceIndex(resolved_root)
            self._type_index = TypeIndex(resolved_root)
            self._project_symbol_index = ProjectSymbolIndex(resolved_root, self._symbol_index)
            self._project_symbol_resolver = ProjectSymbolResolver(
                resolved_root,
                CrossFileSymbolResolver(resolved_root, self._project_symbol_index.registry),
                self._type_index,
            )
            self._cross_file_reference_resolver = CrossFileReferenceResolver(
                resolved_root, self._project_symbol_resolver
            )
            self._global_symbol_graph = GlobalSymbolGraph(
                resolved_root, self._project_symbol_index.registry
            )
            self._call_graph = CallGraph(resolved_root)
            self._call_graph_builder = CallGraphBuilder(
                resolved_root,
                self._project_symbol_resolver,
                self._project_symbol_index.registry,
            )
            self._symbol_navigation = SymbolNavigationService(
                resolved_root,
                self._symbol_index,
                self._symbol_reference_index,
                self._cross_file_reference_resolver,
            )
            self._symbol_call_hierarchy = SymbolCallHierarchyService(
                resolved_root,
                self._symbol_index,
                self._symbol_reference_index,
                self._cross_file_reference_resolver,
                self._call_graph,
            )
            self._symbol_impact_analysis = SymbolImpactAnalysisService(
                resolved_root,
                self._symbol_index,
                self._symbol_reference_index,
                self._cross_file_reference_resolver,
                self._call_graph,
            )
            self._last_results = ()
            self._last_affected = ()
            self._loaded_from_cache = False
        self._stop_event.clear()
        self._ready.clear()

        # Restore the persisted symbol graph immediately so navigation and impact
        # queries have a warm startup view while the authoritative background
        # rebuild refreshes all indexes. Corrupt snapshots are discarded.
        cached_global = self._global_symbol_store.load(resolved_root)
        if cached_global is not None:
            with self._lock:
                global_symbol_graph = self._global_symbol_graph
            if global_symbol_graph is None or not global_symbol_graph.load_snapshot(cached_global):
                self._global_symbol_store.remove(resolved_root)

        cached_calls = self._call_graph_store.load(resolved_root)
        if cached_calls is not None:
            with self._lock:
                call_graph = self._call_graph
            if call_graph is None or not call_graph.load_snapshot(cached_calls):
                self._call_graph_store.remove(resolved_root)
            else:
                repaired = call_graph.validate(repair=True)
                if repaired:
                    # Persist the repaired snapshot immediately. Otherwise stale
                    # edges and diagnostics are loaded and repaired again on every
                    # application start until the background rebuild completes.
                    try:
                        self._call_graph_store.save(resolved_root, call_graph.snapshot())
                    except OSError:
                        # A cache write failure must not prevent the live graph from
                        # being used; the authoritative rebuild can save it later.
                        pass

        cached = self._store.load(resolved_root)
        if cached is not None:
            with self._lock:
                resolver = self._resolver
            if resolver is not None:
                resolver.load_graph(cached)
                self._loaded_from_cache = True
                self._ready.set()
                service_status_registry.set_state(
                    "dependency_index",
                    "idle",
                    "Bağımlılık grafiği kalıcı cache üzerinden hazır.",
                )
        self._build_thread = Thread(
            target=self._build_initial_graph,
            args=(generation, resolved_root),
            name="JarvisDependencyIndex",
            daemon=True,
        )
        self._build_thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        """Stop the active generation while preventing concurrent restarts."""
        self._stop_event.set()
        with self._lock:
            self._generation += 1
            thread = self._build_thread
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=3.0)
        if thread and thread.is_alive():
            # Keep the worker reference and all objects it may still be using.
            # The worker's ``finally`` block performs the definitive cleanup.
            self._ready.clear()
            service_status_registry.set_state(
                "dependency_index",
                "stopping",
                "Bağımlılık indeksleme görevi güvenli biçimde durduruluyor.",
            )
            return
        self._finalize_stopped_state(expected_thread=thread)

    def _finalize_stopped_state(self, *, expected_thread: Thread | None) -> None:
        """Clear runtime state only after the authoritative worker has exited."""
        with self._lock:
            if expected_thread is not None and self._build_thread not in (None, expected_thread):
                return
            self._build_thread = None
            self._ready.clear()
            self._resolver = None
            self._semantic_graph = None
            self._symbol_index = None
            self._symbol_reference_index = None
            self._type_index = None
            self._project_symbol_index = None
            self._project_symbol_resolver = None
            self._cross_file_reference_resolver = None
            self._global_symbol_graph = None
            self._call_graph = None
            self._call_graph_builder = None
            self._symbol_navigation = None
            self._symbol_call_hierarchy = None
            self._symbol_impact_analysis = None
            self._root = None
            self._last_results = ()
            self._last_affected = ()
            self._loaded_from_cache = False
            self._global_symbol_repairs = 0
        self._language_symbol_mapper = LanguageSymbolMapper()
        service_status_registry.set_state(
            "dependency_index", "stopped", "Bağımlılık indeksi durduruldu."
        )

    def apply_changes(self, changes: list[WorkspaceChange]) -> tuple[Path, ...]:
        with self._operation_lock:
            with self._lock:
                resolver = self._resolver
                semantic_graph = self._semantic_graph
                symbol_index = self._symbol_index
                symbol_reference_index = self._symbol_reference_index
                type_index = self._type_index
                project_symbol_index = self._project_symbol_index
                global_symbol_graph = self._global_symbol_graph
                reference_resolver = self._cross_file_reference_resolver
                project_symbol_resolver = self._project_symbol_resolver
                call_graph = self._call_graph
                call_graph_builder = self._call_graph_builder
                root = self._root
            if resolver is None or root is None:
                return ()
    
            python_changes: list[WorkspaceChange] = []
            for change in changes:
                if not isinstance(change, WorkspaceChange):
                    continue
                relative = self._normalize_workspace_path(root, change.path)
                if relative is None or not self._is_python_source(relative):
                    continue
                python_changes.append(
                    WorkspaceChange(change.kind, relative, change.previous_path)
                )

            if not python_changes:
                with self._lock:
                    self._last_affected = ()
                return ()

            planner = SymbolGraphUpdatePlanner(root, resolver)
            planner.capture_before((root / change.path for change in python_changes))
    
            service_status_registry.set_state(
                "dependency_index",
                "running",
                f"{len(python_changes)} Python değişikliği bağımlılık grafiğine uygulanıyor.",
            )
            try:
                for change in python_changes:
                    if self._stop_event.is_set():
                        break
                    absolute = (root / change.path).resolve(strict=False)
                    if change.kind == "deleted":
                        planner.mark_removed(absolute)
                        resolver.remove_file(absolute)
                        if semantic_graph is not None:
                            semantic_graph.remove_file(absolute)
                        if symbol_index is not None:
                            symbol_index.remove_file(absolute)
                        if symbol_reference_index is not None:
                            symbol_reference_index.remove_file(absolute)
                        if type_index is not None:
                            type_index.remove_file(absolute)
                        if project_symbol_index is not None:
                            project_symbol_index.remove_file(absolute)
                    else:
                        resolver.update_file(absolute)
                        if semantic_graph is not None:
                            semantic_graph.update_file(absolute)
                        if symbol_index is not None:
                            symbol_index.update_file(absolute)
                        if symbol_reference_index is not None:
                            symbol_reference_index.update_file(absolute)
                        if type_index is not None:
                            type_index.update_file(absolute)
                        if project_symbol_index is not None:
                            project_symbol_index.refresh_file(absolute)
    
                    if project_symbol_resolver is not None:
                        project_symbol_resolver.invalidate(absolute)
                    if reference_resolver is not None:
                        reference_resolver.invalidate(absolute)
    
                plan = planner.finalize()
                if reference_resolver is not None and symbol_reference_index is not None:
                    # Invalidate every old/new dependent before rebinding so no stale
                    # resolution survives an import removal or package-edge rewrite.
                    for candidate in plan.rebind:
                        reference_resolver.invalidate(candidate)
                    rebound = reference_resolver.rebind_files(
                        plan.rebind,
                        symbol_reference_index.references_for_file,
                    )
                    if global_symbol_graph is not None:
                        global_symbol_graph.remove_files(plan.removed)
                        global_symbol_graph.replace_files(rebound)
                        validation = global_symbol_graph.validate(repair=True)
                        self._global_symbol_repairs += validation.removed_edges
                    if call_graph is not None:
                        for removed_path in plan.removed:
                            call_graph.remove_file(removed_path)
                        if call_graph_builder is not None:
                            call_graph.replace_files(
                                call_graph_builder.build_file(candidate)
                                for candidate in plan.rebind
                                if candidate.is_file()
                            )
    
                affected: set[Path] = set()
                for candidate in (*plan.rebind, *plan.removed):
                    try:
                        affected.add(candidate.relative_to(root))
                    except ValueError:
                        continue
                ordered = tuple(sorted(affected, key=lambda item: str(item).casefold()))
                with self._lock:
                    self._last_affected = ordered
                self._store.save(root, resolver.graph_snapshot())
                if global_symbol_graph is not None and (plan.rebind or plan.removed):
                    self._global_symbol_store.save(root, global_symbol_graph.snapshot())
                if call_graph is not None and (plan.rebind or plan.removed):
                    self._call_graph_store.save(root, call_graph.snapshot())
                service_status_registry.completed(
                    "dependency_index",
                    len(ordered),
                    f"{len(ordered)} eski/yeni bağımlı modül yeniden bağlandı.",
                )
                return ordered
            except Exception as exc:
                service_status_registry.failed("dependency_index", exc, len(python_changes))
                return ()
    def locate_symbol(self, name: str, *, limit: int = 500) -> SymbolNavigationResult:
        """Return indexed definitions and references for a symbol name."""
        with self._lock:
            navigation = self._symbol_navigation
        if navigation is None:
            return SymbolNavigationResult(name.strip(), (), ())
        return navigation.locate(name, limit=limit)

    def symbol_definitions(self, name: str, *, limit: int = 100):
        return self.locate_symbol(name, limit=limit).definitions

    def symbol_references(self, name: str, *, limit: int = 500):
        return self.locate_symbol(name, limit=limit).references

    def symbol_callers(self, name: str, *, limit: int = 500) -> SymbolCallHierarchyResult:
        """Return definitions and indexed call sites for a symbol."""
        with self._lock:
            hierarchy = self._symbol_call_hierarchy
        if hierarchy is None:
            return SymbolCallHierarchyResult(name.strip(), (), ())
        return hierarchy.callers(name, limit=limit)

    def symbol_callees(self, name: str, *, limit: int = 500) -> SymbolCallHierarchyResult:
        """Return resolved outgoing calls for a symbol."""
        with self._lock:
            hierarchy = self._symbol_call_hierarchy
        if hierarchy is None:
            return SymbolCallHierarchyResult(name.strip(), (), ())
        return hierarchy.callees(name, limit=limit)

    def symbol_call_hierarchy(self, name: str, *, limit: int = 500) -> SymbolCallHierarchyResult:
        """Return definitions plus incoming and outgoing resolved call edges."""
        with self._lock:
            hierarchy = self._symbol_call_hierarchy
        if hierarchy is None:
            return SymbolCallHierarchyResult(name.strip(), (), ())
        return hierarchy.hierarchy(name, limit=limit)

    def symbol_impact(self, name: str, *, limit: int = 2000) -> SymbolImpactResult:
        """Return a deterministic file-level impact summary for a symbol."""
        with self._lock:
            analysis = self._symbol_impact_analysis
        if analysis is None:
            return SymbolImpactResult(name.strip(), (), ())
        return analysis.analyze(name, limit=limit)

    def resolve_file_references(
        self, path: str | Path, *, limit_per_reference: int = 25
    ) -> ReferenceBindingResult:
        """Bind all indexed references in one source file to project definitions."""
        absolute = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            reference_index = self._symbol_reference_index
            resolver = self._cross_file_reference_resolver
        if reference_index is None or resolver is None:
            return ReferenceBindingResult(str(absolute), ())
        return resolver.bind_file(
            absolute,
            reference_index.references_for_file(absolute),
            limit_per_reference=limit_per_reference,
        )

    def map_language_symbol(
        self,
        *,
        path: str | Path,
        name: object,
        qualified_name: object = "",
        namespace: object = "",
        kind: object = "unknown",
    ) -> MappedSymbol:
        """Map language-specific symbol metadata into the common symbol model."""
        return self._language_symbol_mapper.map_symbol(
            path=path,
            name=name,
            qualified_name=qualified_name,
            namespace=namespace,
            kind=kind,
        )

    def resolve_type(self, symbol: str, *, limit: int = 100) -> tuple[ResolvedType, ...]:
        """Return explicit and safely inferred type observations for a symbol."""
        with self._lock:
            type_index = self._type_index
        if type_index is None:
            return ()
        return type_index.resolve(symbol, limit=limit)


    def resolve_project_symbol(
        self,
        name: str,
        *,
        source_path: str | Path | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> ProjectSymbolResolution:
        """Resolve a symbol across modules using project and lexical context."""
        with self._lock:
            resolver = self._project_symbol_resolver
        if resolver is None:
            return ProjectSymbolResolution(name.strip(), (), (), None, scope, False)
        return resolver.resolve(
            name, source_path=source_path, scope=scope, limit=limit
        )


    def global_symbol_incoming(self, canonical_name: str, *, limit: int = 1000):
        """Return project-wide resolved references targeting one canonical symbol."""
        with self._lock:
            graph = self._global_symbol_graph
        return graph.incoming(canonical_name, limit=limit) if graph is not None else ()

    def global_symbol_related(self, canonical_name: str, *, limit: int = 200):
        """Return symbols declared in files that reference the requested symbol."""
        with self._lock:
            graph = self._global_symbol_graph
        return graph.related_symbols(canonical_name, limit=limit) if graph is not None else ()

    def call_graph_callers(self, canonical_name: str, *, limit: int = 1000):
        """Return resolved call edges targeting one canonical symbol."""
        with self._lock:
            graph = self._call_graph
        return graph.callers(canonical_name, limit=limit) if graph is not None else ()

    def call_graph_callees(self, canonical_name: str, *, limit: int = 1000):
        """Return resolved call edges originating from one canonical symbol."""
        with self._lock:
            graph = self._call_graph
        return graph.callees(canonical_name, limit=limit) if graph is not None else ()

    def call_graph_callee_paths(
        self, canonical_name: str, *, max_depth: int = 5, max_paths: int = 1000
    ) -> CallGraphTraversalResult:
        """Return bounded transitive outgoing call paths and recursion cycles."""
        with self._lock:
            graph = self._call_graph
        if graph is None:
            return CallGraphTraversalResult(
                _safe_query_symbol(canonical_name),
                "callees",
                _safe_positive_int(max_depth, default=5),
                (),
            )
        return graph.traverse_callees(
            canonical_name, max_depth=max_depth, max_paths=max_paths
        )

    def call_graph_caller_paths(
        self, canonical_name: str, *, max_depth: int = 5, max_paths: int = 1000
    ) -> CallGraphTraversalResult:
        """Return bounded transitive incoming call paths and recursion cycles."""
        with self._lock:
            graph = self._call_graph
        if graph is None:
            return CallGraphTraversalResult(
                _safe_query_symbol(canonical_name),
                "callers",
                _safe_positive_int(max_depth, default=5),
                (),
            )
        return graph.traverse_callers(
            canonical_name, max_depth=max_depth, max_paths=max_paths
        )

    def call_graph_recursions(self, *, limit: int = 1000) -> CallGraphRecursionReport:
        """Return project-wide direct and mutual recursion components."""
        with self._lock:
            graph = self._call_graph
        if graph is None:
            return CallGraphRecursionReport((), 0, 0, False)
        return graph.recursion_report(limit=limit)


    def call_graph_hotspots(
        self, *, limit: int = 100, min_score: float = 1.0
    ) -> CallGraphHotspotReport:
        """Return high-impact symbols ranked by fan-in, fan-out and file spread."""
        with self._lock:
            graph = self._call_graph
        if graph is None:
            return CallGraphHotspotReport((), 0, False)
        return graph.hotspot_report(limit=limit, min_score=min_score)

    def call_graph_reachability(
        self, entry_points: tuple[str, ...] | list[str] | None = None, *, limit: int = 5000
    ) -> CallGraphReachabilityReport:
        """Return reachable and disconnected symbols without rebuilding indexes."""
        with self._lock:
            graph = self._call_graph
        if graph is None:
            return CallGraphReachabilityReport((), (), (), 0, False)
        return graph.reachability_report(entry_points, limit=limit)

    def call_graph_diagnostics(self, *, limit: int = 5000) -> CallGraphDiagnosticsReport:
        """Return current call-resolution coverage without rebuilding indexes."""
        with self._lock:
            graph = self._call_graph
        if graph is None:
            return CallGraphDiagnosticsReport((), 0, 0, 0, 0, 0)
        return graph.diagnostics(limit=limit)

    def stats(self) -> dict[str, int | bool]:
        with self._lock:
            resolver = self._resolver
        graph_stats = resolver.graph.stats() if resolver is not None else None
        with self._lock:
            semantic_graph = self._semantic_graph
            symbol_index = self._symbol_index
            type_index = self._type_index
            project_symbol_index = self._project_symbol_index
            global_symbol_graph = self._global_symbol_graph
            call_graph = self._call_graph
        semantic_stats = semantic_graph.stats() if semantic_graph is not None else {"semantic_nodes": 0, "semantic_edges": 0, "semantic_files": 0}
        symbol_stats = symbol_index.stats() if symbol_index is not None else {"symbols": 0, "files": 0}
        type_stats = type_index.stats() if type_index is not None else {"resolved_types": 0, "type_files": 0}
        global_symbol_stats = global_symbol_graph.stats() if global_symbol_graph is not None else {"global_symbol_nodes": 0, "global_symbol_edges": 0, "global_symbol_files": 0, "global_symbol_revision": 0}
        call_graph_stats = call_graph.stats() if call_graph is not None else {"call_graph_edges": 0, "call_graph_files": 0, "call_graph_revision": 0, "call_graph_call_sites": 0, "call_graph_resolved_calls": 0, "call_graph_unresolved_calls": 0, "call_graph_ambiguous_calls": 0, "call_graph_parse_errors": 0, "call_graph_recursion_components": 0, "call_graph_recursive_symbols": 0, "call_graph_hotspot_symbols": 0, "call_graph_reachable_symbols": 0, "call_graph_unreachable_symbols": 0}
        project_symbol_stats = (
            project_symbol_index.stats()
            if project_symbol_index is not None
            else {"project_symbols": 0, "project_symbol_files": 0}
        )
        with self._lock:
            symbol_reference_index = self._symbol_reference_index
        with self._lock:
            reference_resolver = self._cross_file_reference_resolver
        reference_binding_stats = (
            reference_resolver.stats()
            if reference_resolver is not None
            else {
                "bound_references": 0,
                "resolved_references": 0,
                "ambiguous_references": 0,
                "unresolved_references": 0,
                "reference_binding_files": 0,
            }
        )
        reference_stats = (
            symbol_reference_index.stats()
            if symbol_reference_index is not None
            else {"references": 0, "reference_files": 0}
        )
        return {
            "ready": self.is_ready,
            "nodes": graph_stats.nodes if graph_stats else 0,
            "edges": graph_stats.edges if graph_stats else 0,
            "affected": len(self.last_affected),
            "cached": self._loaded_from_cache,
            "symbols": symbol_stats["symbols"],
            "symbol_files": symbol_stats["files"],
            "symbol_references": reference_stats["references"],
            "symbol_reference_files": reference_stats["reference_files"],
            "semantic_nodes": semantic_stats["semantic_nodes"],
            "semantic_edges": semantic_stats["semantic_edges"],
            "semantic_files": semantic_stats["semantic_files"],
            "resolved_types": type_stats["resolved_types"],
            "type_files": type_stats["type_files"],
            "project_symbols": project_symbol_stats["project_symbols"],
            "project_symbol_files": project_symbol_stats["project_symbol_files"],
            "bound_references": reference_binding_stats["bound_references"],
            "resolved_references": reference_binding_stats["resolved_references"],
            "ambiguous_references": reference_binding_stats["ambiguous_references"],
            "unresolved_references": reference_binding_stats["unresolved_references"],
            "reference_binding_files": reference_binding_stats["reference_binding_files"],
            "global_symbol_nodes": global_symbol_stats["global_symbol_nodes"],
            "global_symbol_edges": global_symbol_stats["global_symbol_edges"],
            "global_symbol_files": global_symbol_stats["global_symbol_files"],
            "global_symbol_revision": global_symbol_stats["global_symbol_revision"],
            "global_symbol_repairs": self._global_symbol_repairs,
            "call_graph_edges": call_graph_stats["call_graph_edges"],
            "call_graph_files": call_graph_stats["call_graph_files"],
            "call_graph_revision": call_graph_stats["call_graph_revision"],
            "call_graph_call_sites": call_graph_stats["call_graph_call_sites"],
            "call_graph_resolved_calls": call_graph_stats["call_graph_resolved_calls"],
            "call_graph_unresolved_calls": call_graph_stats["call_graph_unresolved_calls"],
            "call_graph_ambiguous_calls": call_graph_stats["call_graph_ambiguous_calls"],
            "call_graph_parse_errors": call_graph_stats["call_graph_parse_errors"],
            "call_graph_recursion_components": call_graph_stats["call_graph_recursion_components"],
            "call_graph_recursive_symbols": call_graph_stats["call_graph_recursive_symbols"],
            "call_graph_hotspot_symbols": call_graph_stats["call_graph_hotspot_symbols"],
            "call_graph_reachable_symbols": call_graph_stats["call_graph_reachable_symbols"],
            "call_graph_unreachable_symbols": call_graph_stats["call_graph_unreachable_symbols"],
        }

    def _is_active_generation(self, generation: int, root: Path) -> bool:
        with self._lock:
            return (
                generation == self._generation
                and self._root == root
                and not self._stop_event.is_set()
            )

    def _build_initial_graph(self, generation: int, expected_root: Path) -> None:
        with self._operation_lock:
            with self._lock:
                resolver = self._resolver
                semantic_graph = self._semantic_graph
                symbol_index = self._symbol_index
                symbol_reference_index = self._symbol_reference_index
                type_index = self._type_index
                project_symbol_index = self._project_symbol_index
                global_symbol_graph = self._global_symbol_graph
                call_graph = self._call_graph
                call_graph_builder = self._call_graph_builder
            if resolver is None or not self._is_active_generation(generation, expected_root):
                return
            service_status_registry.set_state(
                "dependency_index", "running", "Python bağımlılık grafiği arka planda hazırlanıyor."
            )
            try:
                results = resolver.rebuild()
                if semantic_graph is not None and self._is_active_generation(generation, expected_root):
                    semantic_graph.rebuild(resolver.source_paths())
                if symbol_index is not None and self._is_active_generation(generation, expected_root):
                    symbol_index.rebuild(resolver.source_paths())
                if symbol_reference_index is not None and self._is_active_generation(generation, expected_root):
                    symbol_reference_index.rebuild(resolver.source_paths())
                if type_index is not None and self._is_active_generation(generation, expected_root):
                    type_index.rebuild(resolver.source_paths())
                if project_symbol_index is not None and self._is_active_generation(generation, expected_root):
                    project_symbol_index.rebuild(resolver.source_paths())
                with self._lock:
                    reference_resolver = self._cross_file_reference_resolver
                if (
                    reference_resolver is not None
                    and symbol_reference_index is not None
                    and self._is_active_generation(generation, expected_root)
                ):
                    reference_resolver.clear()
                    if global_symbol_graph is not None:
                        global_symbol_graph.clear()
                    initial_bindings: list[ReferenceBindingResult] = []
                    for source_path in resolver.source_paths():
                        if not self._is_active_generation(generation, expected_root):
                            break
                        binding = reference_resolver.bind_file(
                            source_path, symbol_reference_index.references_for_file(source_path)
                        )
                        initial_bindings.append(binding)
                    if global_symbol_graph is not None:
                        global_symbol_graph.replace_files(initial_bindings)
                        validation = global_symbol_graph.validate(repair=True)
                        self._global_symbol_repairs += validation.removed_edges
                    if call_graph is not None and call_graph_builder is not None:
                        call_graph.clear()
                        call_graph.replace_files(
                            call_graph_builder.build_file(source_path)
                            for source_path in resolver.source_paths()
                            if self._is_active_generation(generation, expected_root)
                        )
                if not self._is_active_generation(generation, expected_root):
                    return
                with self._lock:
                    self._last_results = results
                    root = self._root
                    self._loaded_from_cache = False
                if root is not None:
                    self._store.save(root, resolver.graph_snapshot())
                    if global_symbol_graph is not None:
                        self._global_symbol_store.save(root, global_symbol_graph.snapshot())
                    if call_graph is not None:
                        self._call_graph_store.save(root, call_graph.snapshot())
                self._ready.set()
                parse_errors = sum(1 for item in results if item.parse_error)
                service_status_registry.completed(
                    "dependency_index",
                    len(results),
                    f"Bağımlılık grafiği hazır. {len(results)} dosya, {parse_errors} ayrıştırma uyarısı.",
                )
            except Exception as exc:
                service_status_registry.failed("dependency_index", exc, 0)
            finally:
                worker = current_thread()
                with self._lock:
                    is_current_worker = self._build_thread is worker
                    stopping = self._stop_event.is_set()
                if is_current_worker:
                    if stopping:
                        self._finalize_stopped_state(expected_thread=worker)
                    else:
                        with self._lock:
                            if self._build_thread is worker:
                                self._build_thread = None
    @staticmethod
    def _normalize_workspace_path(root: Path, value: object) -> Path | None:
        if not isinstance(value, (str, Path)):
            return None
        try:
            candidate = Path(value)
            absolute = (
                candidate.expanduser().resolve(strict=False)
                if candidate.is_absolute()
                else (root / candidate).resolve(strict=False)
            )
            relative = absolute.relative_to(root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            return None
        return relative

    @staticmethod
    def _is_python_source(relative: Path) -> bool:
        return relative.suffix.casefold() in {".py", ".pyi"} and not any(
            part in IGNORED_DIRS for part in relative.parts
        )


def _safe_query_symbol(value: object) -> str:
    """Normalize an external symbol query without coercing arbitrary objects."""
    return value.strip() if isinstance(value, str) else ""


def _safe_positive_int(value: object, *, default: int) -> int:
    """Return a positive integer for service fallbacks before graph startup."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else 1
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return default
        integer = int(value)
        return integer if integer > 0 else 1
    return default
