from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine(tmp_path: Path):
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {
        "kind": "runtime_research_plan",
        "finding_id": "RUN-LOCALREVIEW",
    }
    engine.last_action_context = {}
    finding = SimpleNamespace(
        finding_id="RUN-LOCALREVIEW",
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )
    engine._find_runtime_finding = lambda finding_id: finding
    engine._targeted_own_code_review_request = lambda text: (
        "SALT-OKUNUR KAYNAK INCELEMESI\n"
        "Hedef: core/assistant.py - AssistantEngine.handle\n"
        "Kanit: kaynak ve test dosyalari diskten salt-okunur okundu."
    )
    return engine


def test_local_review_execution_runs_existing_review_instead_of_replanning(tmp_path):
    engine = _engine(tmp_path)
    result = engine._runtime_local_review_execution_request(
        "Bu LOCAL_REVIEW planini simdi uygula. "
        "Kaynak kodunu ve ilgili testleri gercekten incele. "
        "Henuz patch uretme ve hicbir kodu degistirme."
    )
    assert result is not None
    assert "LOCAL_REVIEW YURUTME SONUCU" in result
    assert "SALT-OKUNUR KAYNAK INCELEMESI" in result
    assert "KOK NEDEN KARARI: YETERSIZ" in result
    assert "Patch izni: hayir" in result
    assert "Yeni teknik plan olusturulmadi" in result


def test_local_review_execution_requires_active_runtime_research_context(tmp_path):
    engine = _engine(tmp_path)
    engine.active_runtime_research_context = {}
    engine.last_action_context = {}
    assert engine._runtime_local_review_execution_request(
        "LOCAL_REVIEW planini simdi uygula"
    ) is None


def test_local_review_execution_never_guesses_missing_target(tmp_path):
    engine = _engine(tmp_path)
    finding = SimpleNamespace(
        finding_id="RUN-LOCALREVIEW",
        affected_paths=(),
        affected_symbols=(),
    )
    engine._find_runtime_finding = lambda finding_id: finding
    result = engine._runtime_local_review_execution_request(
        "LOCAL_REVIEW planini simdi uygula"
    )
    assert "hedef tahmin edilmedi" in result.casefold()
