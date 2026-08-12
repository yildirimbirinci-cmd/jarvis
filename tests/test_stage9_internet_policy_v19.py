from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.internet_policy import InternetPolicy
from artmach_assistant.core.research_manager import ResearchManager


def test_startup_probe_checks_connectivity_without_opening_research(monkeypatch):
    calls = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_create_connection(address, timeout):
        calls.append((address, timeout))
        return FakeSocket()

    monkeypatch.setattr("socket.create_connection", fake_create_connection)
    policy = InternetPolicy()
    status = policy.prepare_startup_connection(timeout=0.1)

    assert status.checked is True
    assert status.ready is True
    assert calls == [(('1.1.1.1', 443), 0.1)]
    assert policy.current_reason() == ""


def test_research_is_denied_outside_two_allowed_scopes():
    policy = InternetPolicy()
    manager = ResearchManager(policy)

    with pytest.raises(PermissionError):
        manager.search("Anitkabir nerededir")


def test_only_explicit_and_unknown_answer_scopes_are_allowed():
    policy = InternetPolicy()

    for reason in ("explicit_user_research", "answer_unknown"):
        with policy.research_scope(reason):
            policy.require_research_access()
            assert policy.current_reason() == reason
        assert policy.current_reason() == ""

    with pytest.raises(PermissionError):
        with policy.research_scope("background_research"):
            pass


def test_assistant_source_routes_only_two_user_research_reasons():
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    assert 'research_scope("explicit_user_research")' in source
    assert 'research_scope("answer_unknown")' in source
    assert 'ResearchManager(self.internet_policy)' in source
    assert 'self.internet_policy.prepare_startup_connection()' in source
