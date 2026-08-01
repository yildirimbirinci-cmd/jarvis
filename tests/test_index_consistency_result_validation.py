from test_index_consistency_status_resilience import load_module


def test_boolean_reconcile_result_is_rejected():
    module = load_module()
    service = module.IndexConsistencyService(lambda: True)
    assert service.run_once() == 0


def test_boolean_interval_uses_default():
    module = load_module()
    service = module.IndexConsistencyService(lambda: 0, interval_seconds=True)
    assert service._interval_seconds == service.DEFAULT_INTERVAL_SECONDS
