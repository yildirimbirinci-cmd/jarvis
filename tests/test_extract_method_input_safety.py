import importlib.util, sys, types
from pathlib import Path

def load(name):
    pkg=types.ModuleType('artmach_assistant'); core=types.ModuleType('artmach_assistant.core')
    rc=types.ModuleType('artmach_assistant.core.refactoring_coordinator')
    class C: pass
    class K: EXTRACT_METHOD='x'; INLINE_METHOD='i'; MOVE_CLASS='c'; MOVE_FUNCTION='f'
    class P: pass
    rc.RefactoringCoordinator=C; rc.RefactoringKind=K; rc.RefactoringPlan=P
    ws=types.ModuleType('artmach_assistant.core.workspace')
    class E(Exception): pass
    ws.WorkspaceError=E
    sys.modules.update({'artmach_assistant':pkg,'artmach_assistant.core':core,'artmach_assistant.core.refactoring_coordinator':rc,'artmach_assistant.core.workspace':ws})
    spec=importlib.util.spec_from_file_location(name, Path(__file__).parents[1]/'core'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def test_safe_text_survives_broken_string():
    m=load('extract_method_refactoring')
    class Bad:
        def __str__(self): raise RuntimeError('boom')
    assert m._safe_text(Bad()) == ''
