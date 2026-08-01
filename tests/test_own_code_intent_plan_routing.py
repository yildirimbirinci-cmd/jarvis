from artmach_assistant.core.own_code_intent import (
    OwnCodeIntentKind,
    classify_own_code_intent,
)


def test_explicit_analysis_remains_read_only() -> None:
    intent = classify_own_code_intent(
        "Kendi kaynak kodunu analiz et. Hiçbir dosyayı değiştirme. Sadece sorunları listele."
    )
    assert intent.kind is OwnCodeIntentKind.REVIEW
    assert intent.read_only is True


def test_development_plan_is_not_downgraded_to_review() -> None:
    intent = classify_own_code_intent(
        "Kendi kaynak kodunu geliştirmek istiyorum. Önce bir geliştirme planı hazırla. Hiçbir dosyayı değiştirme."
    )
    assert intent.kind is OwnCodeIntentKind.CHANGE
    assert intent.read_only is False


def test_show_before_change_routes_to_change_plan() -> None:
    intent = classify_own_code_intent(
        "Kendi kodunda küçük ve güvenli bir geliştirme önerisi hazırla. Dosyaları değiştirmeden önce bana göster."
    )
    assert intent.kind is OwnCodeIntentKind.CHANGE
    assert intent.read_only is False


def test_plain_review_stays_review() -> None:
    intent = classify_own_code_intent("Kendi kodunu incele ve sorunları göster")
    assert intent.kind is OwnCodeIntentKind.REVIEW
