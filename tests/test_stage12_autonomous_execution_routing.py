from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine


ACCEPTANCE_COMMAND = """
Kendi kod tabanını mühendislik açısından incele. Birden fazla dosya veya
bileşeni etkileyen gerçek ve doğrulanabilir bir teknik problem bul. Problemi
kendin seç ve kök nedenini kanıtla. Mevcut doğrulanmış davranışları ve Trust
Core kurallarını koruyarak çözüm planını kendin oluştur. Gerekli değişiklikleri
izole çalışma alanında hazırla ve test et. Başarısız doğrulamada aynı çözümü
tekrar deneme; yeni kanıta göre yeniden planla. Tüm doğrulamalar geçmeden ana
kaynaklara uygulama. Uygulama sonrası yeniden test et; başarısızlıkta güvenli
rollback yap. Sonucu, kanıtları ve öğrendiklerini kalıcı olarak kaydet.
"""


def test_stage12_composite_autonomous_command_is_not_truncated_to_read_only() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.run_one_shot_autonomous_maintenance = lambda: "AUTONOMOUS-E2E"

    result = engine._reserved_self_repair_request(ACCEPTANCE_COMMAND)

    assert result == "AUTONOMOUS-E2E"


def test_stage12_plain_source_review_remains_read_only() -> None:
    assert not AssistantEngine._asks_for_autonomous_engineering_execution(
        "Kendi kaynak kodunu muhendislik acisindan incele ve sorunlari goster."
    )


def test_stage12_execution_contract_requires_isolation_validation_and_safe_closeout() -> None:
    assert not AssistantEngine._asks_for_autonomous_engineering_execution(
        "Kendi kod tabaninda bir problem bul, kok nedenini kanitla ve duzelt."
    )
