from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.self_repair_session import SelfRepairSessionStore


def test_worktree_failure_replan_is_new_evidence_revision(tmp_path):
    store = SelfRepairSessionStore(tmp_path / "repair.json")
    first = store.create(
        finding_id="RUN-06578E9EDE",
        instruction="repair runtime finding",
        approved_paths=("core/assistant.py",),
        approved_symbols=("AssistantEngine._auto_research_world_fact",),
        evidence="runtime evidence",
        max_attempts=1,
        max_replans=1,
    )
    generating = store.transition("generating", expected={"planned"}, increment_attempt=True)
    assert generating.attempts == 1
    failed = store.transition("proposal_failed", expected={"generating"}, last_error="first failed")
    replanned = store.replan_from_failure(failure_evidence="FAILED test_contract")
    assert replanned.state == "planned"
    assert replanned.attempts == 0
    assert replanned.replan_count == 1
    assert "WORKTREE_FAILURE_EVIDENCE" in replanned.evidence
    assert "FAILED test_contract" in replanned.evidence
    assert "EVIDENCE_BASED_REPLAN" in replanned.instruction


def test_replan_is_bounded_to_one_evidence_revision(tmp_path):
    store = SelfRepairSessionStore(tmp_path / "repair.json")
    store.create(
        finding_id="RUN-06578E9EDE",
        instruction="repair runtime finding",
        approved_paths=("core/assistant.py",),
        evidence="runtime evidence",
        max_replans=1,
    )
    store.replan_from_failure(failure_evidence="failure one")
    try:
        store.replan_from_failure(failure_evidence="failure two")
    except ValueError as exc:
        assert "yeniden planlama siniri" in str(exc)
    else:
        raise AssertionError("second evidence replan must be rejected")


def test_apply_failure_replans_instead_of_retrying_same_proposal():
    engine = AssistantEngine.__new__(AssistantEngine)
    session = SimpleNamespace(
        finding_id="RUN-06578E9EDE",
        policy_status="AUTO_ALLOWED",
        risk="LOW",
        max_attempts=1,
        attempts=1,
        approved_paths=("core/assistant.py",),
        approved_symbols=("AssistantEngine.handle",),
        approval_granted=True,
        proposal_fingerprint="",
        replan_count=0,
        max_replans=1,
    )
    pending = SimpleNamespace(files=(SimpleNamespace(path="core/assistant.py"),))
    engine.editor = SimpleNamespace(pending=pending, reject=lambda: None)
    engine._find_runtime_finding = lambda _finding_id: SimpleNamespace()
    decision = SimpleNamespace(status="AUTO_ALLOWED", risk="LOW", max_attempts=1)
    engine._assess_runtime_repair_with_target_refresh = lambda finding: (finding, decision, "")

    # Keep policy enforcement out of this focused state-machine contract.
    globals_map = AssistantEngine._apply_active_self_repair_proposal.__globals__
    original_enforcement = globals_map["validate_runtime_repair_enforcement"]
    original_fingerprint = globals_map["proposal_fingerprint"]
    globals_map["validate_runtime_repair_enforcement"] = lambda *a, **k: SimpleNamespace(allowed=True, reason="")
    globals_map["proposal_fingerprint"] = lambda _p: ""

    class Store:
        def __init__(self):
            self.current = session
            self.replanned = False
        def transition(self, state, **kwargs):
            if state == "applying":
                return self.current
            if state == "proposal_failed":
                self.current = SimpleNamespace(**{**self.current.__dict__, "state": state})
                return self.current
            raise AssertionError(state)
        def replan_from_failure(self, **kwargs):
            self.replanned = True
            self.current = SimpleNamespace(**{**self.current.__dict__, "state": "planned", "replan_count": 1})
            return self.current
        def load(self):
            return self.current

    store = Store()
    engine._self_repair_store = lambda: store
    engine._clear_own_code_pending_proposal_store = lambda: None
    engine.apply_pending_own_code_proposal = lambda: "taslak geçici Git worktree doğrulamasından geçmedi: FAILED x"
    engine._prepare_active_self_repair_proposal = lambda replanned: "no safe revised proposal"

    try:
        rendered = engine._apply_active_self_repair_proposal(session)
    finally:
        globals_map["validate_runtime_repair_enforcement"] = original_enforcement
        globals_map["proposal_fingerprint"] = original_fingerprint

    assert store.replanned is True
    assert "ayni taslagi retry" not in rendered.casefold()
    assert "yeni guvenli transformation uretemedi" in rendered
