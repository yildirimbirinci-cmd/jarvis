"""Incremental call-graph engine."""

from .builder import CallGraphBuilder
from .call_target_resolver import CallTargetResolver
from .graph import CallGraph
from .model import (
    CallGraphBuildResult,
    CallGraphEdge,
    CallGraphDiagnosticsReport,
    CallGraphFileDiagnostics,
    CallGraphHotspot,
    CallGraphHotspotReport,
    CallGraphPath,
    CallGraphRecursionComponent,
    CallGraphRecursionReport,
    CallGraphReachabilityReport,
    CallGraphTraversalResult,
    CallSite,
)
from .parser import CallSiteParser

__all__ = [
    "CallGraph",
    "CallGraphBuilder",
    "CallGraphBuildResult",
    "CallGraphEdge",
    "CallGraphDiagnosticsReport",
    "CallGraphFileDiagnostics",
    "CallGraphHotspot",
    "CallGraphHotspotReport",
    "CallGraphPath",
    "CallGraphRecursionComponent",
    "CallGraphRecursionReport",
    "CallGraphReachabilityReport",
    "CallGraphTraversalResult",
    "CallSite",
    "CallSiteParser",
    "CallTargetResolver",
]
