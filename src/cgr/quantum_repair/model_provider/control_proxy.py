"""Supervised loopback-only control proxy for an internal SWE-ReX container."""

from __future__ import annotations

import errno
import selectors
import socket
import threading
from dataclasses import dataclass


class ControlProxyError(RuntimeError):
    """Sanitized, stable control-proxy failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ControlProxyEndpoint:
    """A destination already verified against Docker ownership metadata."""

    container_identity: str
    image_identity: str
    network_identity_sha256: str
    ownership_nonce: str
    internal_ipv4: str
    destination_port: int = 8000


def select_loopback_port() -> int:
    """Select an ephemeral IPv4 loopback port for official SWE-ReX and the proxy."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class LoopbackControlProxy:
    """Fixed-destination, invocation-scoped, standard-library TCP relay."""

    def __init__(
        self,
        *,
        source_port: int,
        endpoint: ControlProxyEndpoint,
        bind_address: str = "127.0.0.1",
    ) -> None:
        if bind_address != "127.0.0.1":
            raise ControlProxyError(
                "tool_control_proxy_bind_failure",
                "The control proxy must bind only to IPv4 loopback.",
            )
        if not 1 <= source_port <= 65535:
            raise ControlProxyError(
                "tool_control_proxy_bind_failure", "The control proxy port is invalid."
            )
        try:
            destination = socket.inet_pton(socket.AF_INET, endpoint.internal_ipv4)
        except OSError as exc:
            raise ControlProxyError(
                "tool_control_proxy_destination_invalid",
                "The verified control-proxy destination is not IPv4.",
            ) from exc
        if destination == socket.inet_pton(socket.AF_INET, "0.0.0.0"):
            raise ControlProxyError(
                "tool_control_proxy_destination_invalid",
                "The verified control-proxy destination is unspecified.",
            )
        if endpoint.destination_port != 8000:
            raise ControlProxyError(
                "tool_control_proxy_destination_invalid",
                "The control proxy destination port is outside policy.",
            )
        self.bind_address = bind_address
        self.source_port = source_port
        self.endpoint = endpoint
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._connection_lock = threading.Lock()

    def start(self) -> None:
        if self._listener is not None:
            raise ControlProxyError(
                "tool_control_proxy_startup_failure",
                "The control proxy was started more than once.",
            )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((self.bind_address, self.source_port))
            listener.listen(16)
            listener.settimeout(0.2)
        except OSError as exc:
            listener.close()
            code = (
                "tool_control_proxy_foreign_listener"
                if exc.errno in {errno.EADDRINUSE, 10048}
                else "tool_control_proxy_bind_failure"
            )
            raise ControlProxyError(
                code, "The control proxy could not bind safely."
            ) from exc
        if listener.getsockname() != (self.bind_address, self.source_port):
            listener.close()
            raise ControlProxyError(
                "tool_control_proxy_bind_failure",
                "The control proxy bound an unexpected endpoint.",
            )
        self._listener = listener
        self._thread = threading.Thread(
            target=self._serve, name="cgr-swerex-loopback-proxy", daemon=True
        )
        try:
            self._thread.start()
        except RuntimeError as exc:
            listener.close()
            self._listener = None
            raise ControlProxyError(
                "tool_control_proxy_startup_failure",
                "The control proxy supervisor could not start.",
            ) from exc
        self.assert_healthy()

    def assert_healthy(self) -> None:
        thread = self._thread
        listener = self._listener
        if (
            self._failure is not None
            or thread is None
            or not thread.is_alive()
            or listener is None
            or listener.fileno() < 0
        ):
            raise ControlProxyError(
                "tool_control_proxy_terminated",
                "The supervised control proxy terminated during the invocation.",
            )
        try:
            bound = listener.getsockname()
        except OSError as exc:
            raise ControlProxyError(
                "tool_control_proxy_terminated",
                "The supervised control proxy listener became unavailable.",
            ) from exc
        if bound != (self.bind_address, self.source_port):
            raise ControlProxyError(
                "tool_control_proxy_terminated",
                "The supervised control proxy endpoint changed.",
            )

    def stop(self) -> bool:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._connection_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        with self._connection_lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=3)
        cleaned = (thread is None or not thread.is_alive()) and not any(
            worker.is_alive() for worker in workers
        )
        self._listener = None
        if not cleaned:
            raise ControlProxyError(
                "tool_control_proxy_cleanup_failure",
                "The supervised control proxy did not stop completely.",
            )
        return True

    def _serve(self) -> None:
        try:
            while not self._stop.is_set():
                listener = self._listener
                if listener is None:
                    return
                try:
                    client, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    raise
                thread = threading.Thread(
                    target=self._forward_worker, args=(client,), daemon=True
                )
                with self._connection_lock:
                    self._workers.add(thread)
                try:
                    thread.start()
                except RuntimeError:
                    with self._connection_lock:
                        self._workers.discard(thread)
                    client.close()
                    raise
        except BaseException as exc:
            self._failure = exc

    def _forward_worker(self, client: socket.socket) -> None:
        try:
            self._forward(client)
        finally:
            with self._connection_lock:
                self._workers.discard(threading.current_thread())

    def _forward(self, client: socket.socket) -> None:
        destination: socket.socket | None = None
        try:
            destination = socket.create_connection(
                (self.endpoint.internal_ipv4, self.endpoint.destination_port), timeout=3
            )
            client.setblocking(False)
            destination.setblocking(False)
            with self._connection_lock:
                self._connections.update((client, destination))
            selector = selectors.DefaultSelector()
            try:
                selector.register(client, selectors.EVENT_READ, destination)
                selector.register(destination, selectors.EVENT_READ, client)
                while not self._stop.is_set():
                    events = selector.select(timeout=0.2)
                    for key, _ in events:
                        source = key.fileobj
                        if not isinstance(source, socket.socket):
                            return
                        data = source.recv(64 * 1024)
                        if not data:
                            return
                        target = key.data
                        target.sendall(data)
            finally:
                selector.close()
        except OSError:
            return
        finally:
            with self._connection_lock:
                self._connections.discard(client)
                if destination is not None:
                    self._connections.discard(destination)
            client.close()
            if destination is not None:
                destination.close()
