import pytest
from test_extract_method_input_safety import load

def test_move_class_rejects_parent_escape():
    m=load('move_class_refactoring')
    with pytest.raises(m.WorkspaceError):
        m.MoveClassRefactoring._python_path('../x.py', 'Kaynak')
