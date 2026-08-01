from pathlib import Path

import pytest

from artmach_assistant.indexing.project_symbol_index import ProjectSymbolIndex


class _SymbolIndex:
    def symbols_for_file(self, path: Path):
        return ()


def test_rebuild_rejects_scalar_and_invalid_path_items(tmp_path: Path) -> None:
    index = ProjectSymbolIndex(tmp_path, _SymbolIndex())
    with pytest.raises(TypeError, match="iterable"):
        index.rebuild("a.py")
    with pytest.raises(TypeError, match="str or Path"):
        index.rebuild((object(),))
