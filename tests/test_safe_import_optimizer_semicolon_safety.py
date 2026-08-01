from artmach_assistant.core.safe_import_optimizer import SafeImportOptimizer

class Coordinator: pass

def test_semicolon_import_is_preserved_as_risky():
    result = SafeImportOptimizer(Coordinator()).analyze_content('a.py', 'import os; value = 1\n')
    assert result.content == 'import os; value = 1\n'
    assert result.preserved_risky_imports == 1
