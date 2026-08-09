from __future__ import annotations
import itertools
from artmach_assistant.core.own_code_command_router import OwnCodeAction, classify_own_code_command

def test_proposal_language_gauntlet():
    starts=("Kendi kodunda","Jarvis kodunda","core/assistant.py icin","")
    requests=("proposal hazirla","taslak olustur","taslagi cikar","patch onerisi hazirla","degisiklik onerisi olustur")
    guards=("ama uygulama","ve onayimi bekle","kaynaklara dokunma","dosyaya yazma","once goster")
    cases=0
    for start,request,guard in itertools.product(starts,requests,guards):
        text=" ".join(x for x in (start,request,guard) if x).strip()
        c=classify_own_code_command(text); assert c.action is OwnCodeAction.CREATE_PROPOSAL,text; assert not c.apply,text; cases+=1
    assert cases==100

def test_apply_language_gauntlet():
    for text in ("taslagi uygula","proposal uygula","bekleyen taslagi uygula","patchi uygula","hazir taslagi uygula"):
        c=classify_own_code_command(text); assert c.action is OwnCodeAction.APPLY_PENDING,text; assert c.apply,text
