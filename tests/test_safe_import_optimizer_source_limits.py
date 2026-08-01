import pytest
from artmach_assistant.core.safe_import_optimizer import SafeImportOptimizer
from artmach_assistant.core.workspace import WorkspaceError

class Coordinator: pass

def test_rejects_non_text_and_nul_source():
    optimizer = SafeImportOptimizer(Coordinator())
    with pytest.raises(WorkspaceError):
        optimizer.analyze_content('a.py', b'import os')
    with pytest.raises(WorkspaceError):
        optimizer.analyze_content('a.py', 'import os\x00')
