from __future__ import annotations

from tests.test_runtime_finding_research_routing import _engine


def test_explicit_insufficient_local_evidence_promotes_to_rs() -> None:
    engine = _engine()

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE yerel inceleme yetersiz. "
        "Dis arastirma onayi olustur."
    )

    assert rendered is not None
    assert "DIS ARASTIRMA ONAYI" in rendered
    assert "RS-" in rendered
    assert "Internet arastirmasi henuz baslatilmadi" in rendered


def test_plain_research_request_stays_local() -> None:
    engine = _engine()

    rendered = engine._reserved_self_repair_request(
        "RUN-06578E9EDE bulgusunu arastir. "
        "Hicbir kodu degistirme."
    )

    assert rendered is not None
    assert "KANITA DAYALI ARASTIRMA PLANI" in rendered
    assert "Durum: LOCAL_REVIEW" in rendered
