from __future__ import annotations
import json
from pathlib import Path
import pytest
from artmach_assistant.core.own_code_command_router import OwnCodeAction, classify_own_code_command
from artmach_assistant.core.own_code_language_intelligence import jargon_terms, learn_user_phrase, load_language_corpus, match_language_intent

@pytest.mark.parametrize(("text","expected"),[
("Bir proposal hazirla fakat uygulama.",OwnCodeAction.CREATE_PROPOSAL),
("Taslagi cikar, onayimi bekle.",OwnCodeAction.CREATE_PROPOSAL),
("Kaynaklara dokunmadan degisikligi cikar.",OwnCodeAction.CREATE_PROPOSAL),
("Once ne degistirecegini goster.",OwnCodeAction.CREATE_PROPOSAL),
("Degisikligi once goster.",OwnCodeAction.CREATE_PROPOSAL),
("Nasil yapacagini planla.",OwnCodeAction.CREATE_PLAN),
("Hazir taslagi uygula.",OwnCodeAction.APPLY_PENDING),
("Yarim kalmis self-development oturumu var mi, raporla.",OwnCodeAction.REPORT_ENGINEERING_STATE),
("Head commit goster.",OwnCodeAction.REPORT_GIT_STATE),
])
def test_corpus_expands_language_without_new_router_branches(text,expected):
    assert classify_own_code_command(text).action is expected

@pytest.mark.parametrize("text",[
"proposal hazirla ama uygulama","taslagi cikar, kaynaklara dokunma",
"degisikligi once goster ve dosyaya yazma","patch onerisi hazirla, onayimi bekle"])
def test_negative_apply_never_becomes_apply(text):
    c=classify_own_code_command(text); assert c.action is OwnCodeAction.CREATE_PROPOSAL; assert c.apply is False

def test_jargon_loaded():
    v=jargon_terms("proposal"); assert "taslak" in v and "patch onerisi" in v

def test_corpus_versioned():
    c=load_language_corpus(); assert c["version"]==1 and "CREATE_PROPOSAL" in c["intents"]

def test_confirmed_user_phrase_separate(tmp_path:Path):
    p=tmp_path/"user_language.json"
    assert learn_user_phrase(p,phrase="taslagi bir cikar",intent="CREATE_PROPOSAL",confirmed=True)
    assert json.loads(p.read_text())["confirmed"]["CREATE_PROPOSAL"]==["taslagi bir cikar"]

def test_unconfirmed_not_learned(tmp_path:Path):
    p=tmp_path/"u.json"; assert not learn_user_phrase(p,phrase="belki uygula",intent="APPLY_PENDING",confirmed=False); assert not p.exists()

def test_unknown_does_not_guess_apply():
    c=classify_own_code_command("Bununla bir seyler yap bakalim."); assert c.action is OwnCodeAction.NONE and not c.apply

def test_match_exposes_evidence():
    m=match_language_intent("Taslagi cikar ve onayimi bekle."); assert m.intent=="CREATE_PROPOSAL" and m.matched_phrase and m.score>=.70
