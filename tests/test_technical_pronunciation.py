from artmach_assistant.core.technical_pronunciation import render_technical_terms


def test_terms_are_replaced_only_at_word_boundaries() -> None:
    rendered = render_technical_terms("API çalışıyor; apiary değişmemeli.")
    assert rendered.startswith("ey pi ay")
    assert "apiary" in rendered


def test_longer_terms_win_before_shorter_terms() -> None:
    rendered = render_technical_terms("HTTPS ve HTTP")
    assert rendered == "eyç ti ti pi es ve eyç ti ti pi"


def test_visible_source_text_is_not_mutated() -> None:
    source = "Python backend"
    rendered = render_technical_terms(source)
    assert source == "Python backend"
    assert rendered == "Paytın bek end"
