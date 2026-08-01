from __future__ import annotations

import socket
import threading

import pytest

from artmach_assistant.core.single_instance import SingleInstanceCoordinator


def test_second_instance_requests_primary_window() -> None:
    primary = SingleInstanceCoordinator(port=0)
    assert primary.acquire()
    shown = threading.Event()
    primary.set_show_callback(shown.set)
    secondary = SingleInstanceCoordinator(port=primary.port)

    assert secondary.acquire() is False
    assert secondary.request_show() is True
    assert shown.wait(1.0)

    primary.close()


def test_invalid_protocol_does_not_trigger_callback() -> None:
    primary = SingleInstanceCoordinator(port=0)
    assert primary.acquire()
    shown = threading.Event()
    primary.set_show_callback(shown.set)

    with socket.create_connection(("127.0.0.1", primary.port), timeout=1) as client:
        client.sendall(b"INVALID\n")
        client.shutdown(socket.SHUT_WR)
        assert client.recv(32).strip() == b"INVALID"

    assert not shown.is_set()
    primary.close()


def test_coordinator_can_be_reacquired_after_close() -> None:
    first = SingleInstanceCoordinator(port=0)
    assert first.acquire()
    port = first.port
    first.close()

    second = SingleInstanceCoordinator(port=port)
    assert second.acquire()
    second.close()


def test_coordinator_validates_inputs() -> None:
    with pytest.raises(ValueError, match="portu"):
        SingleInstanceCoordinator(port=70_000)
    coordinator = SingleInstanceCoordinator(port=0)
    with pytest.raises(TypeError, match="callback"):
        coordinator.set_show_callback(None)  # type: ignore[arg-type]
