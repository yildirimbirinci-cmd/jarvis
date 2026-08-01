from artmach_assistant.core.query_validation import bounded_positive_int, normalized_query


def test_normalized_query_rejects_nul_and_bounds_length():
    assert normalized_query('abc\x00def') == ''
    assert normalized_query('  abcdef  ', maximum=3) == 'abc'


def test_bounded_positive_int_rejects_invalid_configuration():
    try:
        bounded_positive_int(1, default=True, maximum=10)
    except TypeError:
        pass
    else:
        raise AssertionError('boolean default must be rejected')
