from pathlib import Path

import pytest

from indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from indexing.project_symbol_registry import ProjectSymbolRegistry


def test_symbol_resolver_invalidation_is_root_confined_and_reports_change(tmp_path: Path) -> None:
    resolver = CrossFileSymbolResolver(tmp_path, ProjectSymbolRegistry(tmp_path))
    assert resolver.invalidate(tmp_path / "missing.py") is False
    with pytest.raises(ValueError, match="outside project root"):
        resolver.invalidate(tmp_path.parent / "outside.py")
    with pytest.raises(TypeError, match="str or Path"):
        resolver.invalidate(object())
