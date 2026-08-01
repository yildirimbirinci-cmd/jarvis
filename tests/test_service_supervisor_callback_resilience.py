import time
from conftest import load_module


def test_baseexception_from_enabled_is_isolated_and_backed_off():
    mod = load_module("svc_callbacks", "core/service_supervisor.py")
    supervisor = mod.ServiceSupervisor(check_interval=0.01, max_backoff=2)
    calls = {"enabled": 0}

    def enabled():
        calls["enabled"] += 1
        raise KeyboardInterrupt()

    supervisor.register("worker", is_running=lambda: False, restart=lambda: None, enabled=enabled)
    supervisor.start()
    time.sleep(0.40)
    assert supervisor.is_running
    assert calls["enabled"] == 1
    supervisor.stop()
