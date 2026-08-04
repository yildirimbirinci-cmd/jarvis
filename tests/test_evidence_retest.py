from __future__ import annotations

from artmach_assistant.core.evidence_lifecycle import NEEDS_RETEST
from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    BLOCKED,
    NO_TEST_FOUND,
    build_retest_plan,
)


def _finding(
    *,
    path: str = "core/voice_service.py",
    symbol: str = "VoiceService.recognize_wav",
    title: str = "Tekrarlanan hata: VoiceService.stt_transcription",
    score: int = 100,
) -> EvidenceMaintenanceFinding:
    return EvidenceMaintenanceFinding(
        classification="A",
        score=score,
        source="runtime",
        title=title,
        path=path,
        symbol=symbol,
        evidence="23 tekrar",
        repair_candidate=False,
        lifecycle=NEEDS_RETEST,
    )


def test_retest_planner_finds_related_tests(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_voice_recognition.py").write_text(
        "def test_recognize_wav():\n    pass\n",
        encoding="utf-8",
    )

    plan = build_retest_plan(
        (_finding(),),
        source_root=tmp_path,
    )

    assert len(plan.items) == 1
    item = plan.items[0]

    assert item.status == AUTOMATED
    assert item.test_paths == (
        "tests/test_voice_recognition.py",
    )
    assert item.command[:3] == (
        "python",
        "-m",
        "pytest",
    )
    assert item.command[-1] == "-q"


def test_retest_planner_reports_missing_test(tmp_path) -> None:
    (tmp_path / "tests").mkdir()

    plan = build_retest_plan(
        (
            _finding(
                path="core/unknown.py",
                symbol="Unknown.run",
                title="Tekrarlanan hata: Unknown.run",
            ),
        ),
        source_root=tmp_path,
    )

    assert plan.items[0].status == NO_TEST_FOUND
    assert plan.missing_count == 1


def test_hardware_retest_is_blocked(tmp_path) -> None:
    (tmp_path / "tests").mkdir()

    plan = build_retest_plan(
        (
            _finding(
                symbol="VoiceService.audio_hardware_acceptance",
                title="Physical device audio hardware failure",
            ),
        ),
        source_root=tmp_path,
    )

    assert plan.items[0].status == BLOCKED
    assert plan.blocked_count == 1


def test_active_and_static_findings_are_ignored(tmp_path) -> None:
    finding = _finding()

    active = EvidenceMaintenanceFinding(
        classification=finding.classification,
        score=finding.score,
        source="runtime",
        title=finding.title,
        path=finding.path,
        symbol=finding.symbol,
        evidence=finding.evidence,
        repair_candidate=True,
        lifecycle="ACTIVE",
    )
    static = EvidenceMaintenanceFinding(
        classification="C",
        score=10,
        source="static",
        title="[COMPLEXITY] run",
        path="core/example.py",
        lifecycle="STATIC",
    )

    plan = build_retest_plan(
        (active, static),
        source_root=tmp_path,
    )

    assert plan.items == ()


def test_same_path_and_symbol_are_merged(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_piper_tts_cache.py").write_text(
        "def test_speak_with_piper():\n    pass\n",
        encoding="utf-8",
    )

    plan = build_retest_plan(
        (
            _finding(
                symbol="VoiceService._speak_with_piper",
                title="Tekrarlanan yavas islem: VoiceService.tts_piper",
                score=68,
            ),
            _finding(
                symbol="VoiceService._speak_with_piper",
                title=(
                    "Tekrarlanan yavas islem: "
                    "VoiceService.tts_piper_discovery"
                ),
                score=64,
            ),
        ),
        source_root=tmp_path,
    )

    assert len(plan.items) == 1
    assert len(plan.items[0].finding_titles) == 2


def test_plan_selects_at_most_five_tests(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()

    for index in range(8):
        (tests_root / f"test_recognize_wav_{index}.py").write_text(
            "def test_recognize_wav():\n    pass\n",
            encoding="utf-8",
        )

    plan = build_retest_plan(
        (_finding(),),
        source_root=tmp_path,
    )

    assert len(plan.items[0].test_paths) == 5


def test_broad_voice_name_alone_is_not_enough(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_voice_unrelated.py").write_text(
        "def test_unrelated():\n    pass\n",
        encoding="utf-8",
    )

    plan = build_retest_plan(
        (
            _finding(
                path="core/unknown_module.py",
                symbol="Unknown.special_operation",
                title="Tekrarlanan hata: Unknown.special_operation",
            ),
        ),
        source_root=tmp_path,
    )

    assert plan.items[0].status == NO_TEST_FOUND


def test_warning_requires_strong_direct_match(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_targeted_validation.py").write_text(
        "def test_request_targeted_validation_repair():\n"
        "    pass\n",
        encoding="utf-8",
    )

    plan = build_retest_plan(
        (
            _finding(
                path="core/assistant.py",
                symbol=(
                    "AssistantEngine."
                    "_request_targeted_validation_repair"
                ),
                title=(
                    "Tekrarlanan uyari: "
                    "AssistantEngine.targeted_code_model_repair"
                ),
            ),
        ),
        source_root=tmp_path,
    )

    assert plan.items[0].status == AUTOMATED


def test_report_contains_command_and_merged_titles(tmp_path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_piper_tts_cache.py").write_text(
        "def test_speak_with_piper():\n    pass\n",
        encoding="utf-8",
    )

    rendered = build_retest_plan(
        (
            _finding(
                symbol="VoiceService._speak_with_piper",
                title="tts_piper",
            ),
            _finding(
                symbol="VoiceService._speak_with_piper",
                title="tts_piper_discovery",
            ),
        ),
        source_root=tmp_path,
    ).report()

    assert "Birlestirilen bulgular:" in rendered
    assert "Komut:" in rendered
    assert "python -m pytest" in rendered
    assert "test_piper_tts_cache.py" in rendered
