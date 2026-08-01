import time
from conftest import STATUS, load_module


def test_status_registry_failure_does_not_stop_worker():
    mod = load_module("svc_status", "core/service_supervisor.py")
    STATUS.raise_all = True
    try:
        supervisor = mod.ServiceSupervisor(check_interval=0.01)
        running = {"value": False}
        supervisor.register(
            "worker",
            is_running=lambda: running["value"],
            restart=lambda: running.__setitem__("value", True),
            enabled=lambda: True,
        )
        supervisor.start()
        time.sleep(0.35)
        assert running["value"] is True
        assert supervisor.is_running
        supervisor.stop()
    finally:
        STATUS.raise_all = False
