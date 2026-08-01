from __future__ import annotations
import sys, types, unittest
workspace_stub=types.ModuleType('artmach_assistant.core.workspace')
class WorkspaceError(RuntimeError): pass
class WorkspaceService: pass
workspace_stub.WorkspaceError=WorkspaceError; workspace_stub.WorkspaceService=WorkspaceService
sys.modules['artmach_assistant.core.workspace']=workspace_stub
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.refactoring_conflict_resolver import ConflictChoice, RefactoringConflictResolver

def proposal(summary, old, new, path='a.py'):
    return EditProposal(summary,[ProposedFileChange(path,summary,old,new,True)])

class Tests(unittest.TestCase):
    def setUp(self): self.r=RefactoringConflictResolver()
    def test_non_overlapping_changes_merge(self):
        base='a=1\nb=2\nc=3\n'
        result=self.r.analyze(proposal('l',base,'a=10\nb=2\nc=3\n'),proposal('r',base,'a=1\nb=2\nc=30\n'))
        self.assertTrue(result.is_resolved); self.assertEqual(result.proposal.files[0].new_content,'a=10\nb=2\nc=30\n')
    def test_overlapping_change_reported(self):
        base='a=1\n'; result=self.r.analyze(proposal('l',base,'a=2\n'),proposal('r',base,'a=3\n'))
        self.assertFalse(result.is_resolved); self.assertEqual(result.conflicts[0].path,'a.py')
    def test_choose_right(self):
        base='a=1\n'; left=proposal('l',base,'a=2\n'); right=proposal('r',base,'a=3\n')
        result=self.r.resolve(left,right,{'a.py':ConflictChoice.RIGHT})
        self.assertEqual(result.files[0].new_content,'a=3\n')
    def test_manual_content_required(self):
        base='a=1\n'; left=proposal('l',base,'a=2\n'); right=proposal('r',base,'a=3\n')
        with self.assertRaisesRegex(WorkspaceError,'Elle çözüm'):
            self.r.resolve(left,right,{'a.py':'manual'})
        result=self.r.resolve(left,right,{'a.py':'manual'},manual_contents={'a.py':'a=4\n'})
        self.assertEqual(result.files[0].new_content,'a=4\n')
    def test_different_base_is_conflict(self):
        left=proposal('l','a=1\n','a=2\n'); right=proposal('r','a=0\n','a=3\n')
        self.assertIn('farklı başlangıç',self.r.analyze(left,right).conflicts[0].reason)
if __name__=='__main__': unittest.main()
