from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.symbol_navigation_service import SymbolNavigationService


def _service(tmp_path: Path) -> SymbolNavigationService:
    service = object.__new__(SymbolNavigationService)
    service.root = tmp_path.resolve()
    return service


def test_definition_matching_is_case_insensitive(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = SimpleNamespace(
        name="RenderScene",
        qualified_name="Pipeline.RenderScene",
        path="pipeline.py",
    )

    assert service._matches_definition(item, "renderscene")
    assert service._matches_definition(item, "pipeline.renderscene")


def test_canonical_names_are_case_insensitively_unique(tmp_path: Path) -> None:
    service = _service(tmp_path)
    definitions = (
        SimpleNamespace(
            name="RenderScene",
            qualified_name="RenderScene",
            path="pipeline.py",
        ),
        SimpleNamespace(
            name="renderscene",
            qualified_name="renderscene",
            path="PIPELINE.py",
        ),
    )

    names = service._canonical_names("pipeline.renderscene", definitions)

    assert len(names) == 1
    assert names[0].casefold() == "pipeline.renderscene"


def test_reference_dedup_normalizes_context_and_scope_case() -> None:
    first = SimpleNamespace(
        path="src/a.py",
        line=10,
        column=4,
        name="RenderScene",
        context="CALL",
        scope="Pipeline",
    )
    duplicate = SimpleNamespace(
        path="SRC/A.py",
        line=10,
        column=4,
        name="renderscene",
        context="call",
        scope="pipeline",
    )

    result = SymbolNavigationService._unique_references((first, duplicate))

    assert result == (first,)


def test_malformed_definition_is_skipped_safely(tmp_path: Path) -> None:
    service = _service(tmp_path)
    malformed = SimpleNamespace(path=None, qualified_name=None)

    assert service._canonical_name(malformed) == ""
    assert not service._matches_definition(malformed, "anything")
