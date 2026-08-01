from test_extract_method_input_safety import load

def test_inline_safe_text_strips_nul_and_caps():
    m=load('inline_method_refactoring')
    assert m._safe_text('a\x00b', max_chars=2) == 'ab'
