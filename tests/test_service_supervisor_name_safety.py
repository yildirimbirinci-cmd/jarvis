import pytest
from conftest import load_module


class BadText:
    def __str__(self):
        raise RuntimeError("boom")


def test_bad_service_name_is_rejected_without_leaking_str_error():
    mod = load_module("svc_name", "core/service_supervisor.py")
    supervisor = mod.ServiceSupervisor()
    with pytest.raises(ValueError):
        supervisor.register(BadText(), is_running=lambda: True, restart=lambda: None, enabled=lambda: True)


def test_service_name_is_bounded_and_nul_removed():
    mod = load_module("svc_name2", "core/service_supervisor.py")
    supervisor = mod.ServiceSupervisor()
    supervisor.register("a\x00" * 400, is_running=lambda: True, restart=lambda: None, enabled=lambda: True)
    assert len(supervisor._services[0].name) <= 256
    assert "\x00" not in supervisor._services[0].name
