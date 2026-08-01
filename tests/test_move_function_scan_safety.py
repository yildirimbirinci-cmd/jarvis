from pathlib import Path
from test_extract_method_input_safety import load

def test_python_scan_skips_symlinks(tmp_path: Path):
    m=load('move_function_refactoring')
    real=tmp_path/'real.py'; real.write_text('x=1')
    link=tmp_path/'link.py'
    try: link.symlink_to(real)
    except OSError: return
    assert m.MoveFunctionRefactoring._python_files(tmp_path) == (real,)
