import importlib.util
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).parents[1] / "core" / "language_symbol_mapper.py"
    spec = importlib.util.spec_from_file_location("language_symbol_mapper_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BrokenText:
    def __str__(self):
        raise RuntimeError("boom")


def test_broken_text_values_are_isolated():
    module = load_module()
    mapped = module.LanguageSymbolMapper().map_symbol(path=BrokenText(), name=BrokenText(), kind=BrokenText())
    assert mapped.language is module.SourceLanguage.UNKNOWN
    assert mapped.name == ""
    assert mapped.kind is module.CommonSymbolKind.UNKNOWN


def test_nul_and_oversized_values_are_bounded():
    module = load_module()
    value = "A\x00" + ("B" * 30_000)
    normalized = module.LanguageSymbolMapper.normalize_qualified_name(value)
    assert "\x00" not in normalized
    assert len(normalized) <= module.LanguageSymbolMapper.MAX_TEXT_LENGTH
