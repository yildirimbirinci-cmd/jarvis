from __future__ import annotations
import json, tempfile, unittest, sys, types
from pathlib import Path
workspace_stub=types.ModuleType('artmach_assistant.core.workspace')
class WorkspaceError(RuntimeError): pass
class WorkspaceService: pass
workspace_stub.WorkspaceError=WorkspaceError; workspace_stub.WorkspaceService=WorkspaceService
sys.modules['artmach_assistant.core.workspace']=workspace_stub
from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator, RefactoringKind
from artmach_assistant.core.refactoring_preview_service import RefactoringPreviewService
from artmach_assistant.core.workspace import WorkspaceError

class WS:
    def __init__(self, root): self.root=Path(root); self.invalidations=0
    def require_root(self): return self.root
    def safe_path(self,p):
        t=(self.root/p).resolve(); t.relative_to(self.root); return t
    def read_text(self,p,max_chars): return self.safe_path(p).read_text(encoding='utf-8')[:max_chars]
    def invalidate_index(self): self.invalidations += 1
class Valid:
    def validate(self, root, changes): return type('R',(),{'issues':(), 'is_valid':True})()

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name); self.ws=WS(self.root)
        self.editor=EditManager(self.ws); self.c=RefactoringCoordinator(self.editor, Valid()); self.s=RefactoringPreviewService(self.c)
    def tearDown(self): self.t.cleanup()
    def plan(self, content='x = 2\n'):
        (self.root/'a.py').write_text('x = 1\n',encoding='utf-8')
        return self.c.prepare(json.dumps({'summary':'x','files':[{'path':'a.py','reason':'r','content':content}]}),kind=RefactoringKind.OTHER)
    def test_counts_and_token(self):
        p=self.plan(); v=self.s.build(p.plan_id)
        self.assertEqual((v.added_lines,v.removed_lines),(1,1)); self.assertEqual(v.files[0].path,'a.py'); self.s.validate(v)
    def test_stale_workspace_rejected(self):
        p=self.plan(); v=self.s.build(); (self.root/'a.py').write_text('external=1\n',encoding='utf-8')
        with self.assertRaisesRegex(WorkspaceError,'değişen dosyalar'): self.s.validate(v)
    def test_changed_proposal_rejected(self):
        self.plan(); v=self.s.build(); self.editor.pending.files[0].new_content='x = 3\n'
        with self.assertRaisesRegex(WorkspaceError,'taslağı'): self.s.validate(v)
    def test_truncation(self):
        content=''.join(f'x{i}={i}\n' for i in range(100)); self.plan(content); v=self.s.build(max_diff_lines=20)
        self.assertTrue(v.files[0].truncated); self.assertIn('kısaltıldı',v.files[0].diff)
if __name__=='__main__': unittest.main()
