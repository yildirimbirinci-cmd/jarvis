from pathlib import Path
import math
import pytest
from artmach_assistant.indexing.semantic_graph_builder import SemanticGraphBuilder
from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.project_symbol_resolver import ProjectSymbolResolver
from artmach_assistant.indexing.symbol_parser import SymbolRecord

class BrokenResolver:
    def resolve(self, *a, **k): raise RuntimeError('boom')
    def invalidate(self, path): raise RuntimeError('boom')

class WrongResolver:
    def resolve(self, *a, **k): return object()
    def invalidate(self, path): pass

class Exploding:
    def __iter__(self):
        yield SymbolRecord('ok','ok','function','',1,1,0)
        raise RuntimeError('boom')

def test_builder_non_text_is_result():
    result = SemanticGraphBuilder().parse_source(b'abc')
    assert result.parse_error and result.nodes == () and result.edges == ()

def test_resolver_backend_failure_is_closed(tmp_path):
    r=ProjectSymbolResolver(tmp_path, BrokenResolver())
    out=r.resolve('name', source_path=tmp_path/'a.py')
    assert out.query=='name' and out.definitions==()

def test_resolver_wrong_backend_result_is_closed(tmp_path):
    out=ProjectSymbolResolver(tmp_path, WrongResolver()).resolve('name')
    assert out.definitions==() and out.types==()

def test_registry_replace_is_atomic_on_exploding_iterable(tmp_path):
    source=tmp_path/'a.py'; source.write_text('pass')
    reg=ProjectSymbolRegistry(tmp_path)
    seed=SymbolRecord('seed','seed','function',str(source),1,1,0)
    reg.replace_file(source,(seed,))
    with pytest.raises(ValueError): reg.replace_file(source,Exploding())
    assert reg.symbols_for_file(source)[0].name=='seed'

def test_registry_limits_are_safe(tmp_path):
    source=tmp_path/'a.py'; source.write_text('pass')
    reg=ProjectSymbolRegistry(tmp_path)
    reg.replace_file(source,(SymbolRecord('seed','seed','function',str(source),1,1,0),))
    assert len(reg.exact('seed',limit=float('nan')))==1
    assert len(reg.search('seed',limit='bad'))==1
    assert len(reg.canonical('a.seed',limit=None))==1
