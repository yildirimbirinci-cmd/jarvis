from artmach_assistant.core.unused_code_cleaner import UnusedCodeCleaner

class Candidate:
    path='a.py'; kind='function'; line=1; name='gone'
class Detector:
    def analyze(self, paths, limit):
        return type('Report', (), {'candidates': (Candidate(), Candidate())})()
class Workspace:
    def read_text(self, path, max_chars): return 'def gone():\n    return 1\n\nvalue = 2\n'
class Editor: workspace=Workspace()
class Coordinator: _editor=Editor()

def test_duplicate_candidate_is_removed_once():
    result = UnusedCodeCleaner(Coordinator(), Detector()).analyze(include_imports=False)
    assert len(result) == 1
    assert result[0].removed_symbols == ('gone',)
    assert result[0].content == '\nvalue = 2\n'
