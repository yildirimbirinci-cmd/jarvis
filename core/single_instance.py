from __future__ import annotations

import socket
import threading
from collections.abc import Callable


class SingleInstanceCoordinator:
    PROTOCOL = b"ARTMACH_ASSISTANT/1 SHOW\n"

    def __init__(self, *, port: int, host: str = "127.0.0.1") -> None:
        if not 0 <= int(port) <= 65535:
            raise ValueError("Geçersiz koordinasyon portu.")
        self.host = host
        self.port = int(port)
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._callback: Callable[[], None] | None = None
        self._callback_lock = threading.RLock()

    def acquire(self) -> bool:
        if self._server is not None:
            return True
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.settimeout(0.2)
        try:
            server.bind((self.host, self.port))
            server.listen(4)
        except OSError:
            server.close()
            return False
        self._server = server
        self.port = int(server.getsockname()[1])
        self._thread = threading.Thread(
            target=self._serve,
            name="artmach-single-instance",
            daemon=True,
        )
        self._thread.start()
        return True

    def set_show_callback(self, callback: Callable[[], None]) -> None:
        if not callable(callback):
            raise TypeError("Gösterme callback'i çağrılabilir olmalıdır.")
        with self._callback_lock:
            self._callback = callback

    def request_show(self, *, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=max(0.1, min(float(timeout), 5.0)),
            ) as client:
                client.sendall(self.PROTOCOL)
                client.shutdown(socket.SHUT_WR)
                return client.recv(16).strip() == b"OK"
        except (OSError, ValueError):
            return False

    def close(self) -> None:
        self._stop.set()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _serve(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                client, _address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with client:
                client.settimeout(1.0)
                try:
                    payload = client.recv(128)
                    if payload != self.PROTOCOL:
                        client.sendall(b"INVALID\n")
                        continue
                    with self._callback_lock:
                        callback = self._callback
                    if callback is not None:
                        callback()
                    client.sendall(b"OK\n")
                except OSError:
                    continue

    def __enter__(self) -> "SingleInstanceCoordinator":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
