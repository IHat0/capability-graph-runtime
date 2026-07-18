"""Supervised loopback-only control proxy for an internal SWE-ReX container."""

from __future__ import annotations

import errno
import selectors
import socket
import threading
import time
from collections.abc import Callable
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
        endpoint: ControlProxyEndpoint | None = None,
        bind_address: str = "127.0.0.1",
    ) -> None:
        if bind_address != "127.0.0.1":
            raise ControlProxyError(
                "tool_control_proxy_bind_failure",
                "The control proxy must bind only to IPv4 loopback.",
            )
        if not 0 <= source_port <= 65535:
            raise ControlProxyError(
                "tool_control_proxy_bind_failure", "The control proxy port is invalid."
            )
        self.bind_address = bind_address
        self.source_port = source_port
        self._endpoint: ControlProxyEndpoint | None = None
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._connection_lock = threading.Lock()
        self._endpoint_ready = threading.Event()
        self.bind_started_monotonic: float | None = None
        self.listener_ready_monotonic: float | None = None
        self.destination_ready_monotonic: float | None = None
        self.first_client_connection_monotonic: float | None = None
        self.startup_polling_attempts = 0
        self.startup_polling_elapsed_seconds = 0.0
        if endpoint is not None:
            self.activate(endpoint)

    @property
    def endpoint(self) -> ControlProxyEndpoint | None:
        return self._endpoint

    @staticmethod
    def _validate_endpoint(endpoint: ControlProxyEndpoint) -> None:
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

    def activate(self, endpoint: ControlProxyEndpoint) -> None:
        """Attach the verified immutable destination to an already-bound listener."""
        self._validate_endpoint(endpoint)
        if self._endpoint is not None and self._endpoint != endpoint:
            raise ControlProxyError(
                "tool_container_identity_mismatch",
                "The control proxy destination identity changed during startup.",
            )
        self._endpoint = endpoint
        self._endpoint_ready.set()

    def start(self) -> None:
        if self._listener is not None:
            raise ControlProxyError(
                "tool_control_proxy_startup_failure",
                "The control proxy was started more than once.",
            )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bind_started_monotonic = time.monotonic()
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
            if self.source_port != 0:
                listener.close()
                raise ControlProxyError(
                    "tool_control_proxy_bind_failure",
                    "The control proxy bound an unexpected endpoint.",
                )
            self.source_port = int(listener.getsockname()[1])
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
        self.listener_ready_monotonic = time.monotonic()
        self.assert_healthy()

    def wait_for_first_client(self, *, deadline_seconds: float) -> float:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            observed = self.first_client_connection_monotonic
            if observed is not None:
                return observed
            self.assert_healthy()
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        raise ControlProxyError(
            "tool_control_proxy_startup_race",
            "No official control request reached the reserved proxy listener.",
        )

    def assert_startup_order(self) -> None:
        listener_ready = self.listener_ready_monotonic
        first_request = self.first_client_connection_monotonic
        if (
            listener_ready is None
            or first_request is None
            or first_request < listener_ready
        ):
            raise ControlProxyError(
                "tool_control_proxy_startup_race",
                "Proxy readiness did not precede the first official control request.",
            )

    def wait_until_destination_ready(
        self,
        *,
        deadline_seconds: float,
        poll_seconds: float = 0.05,
        readiness_guard: Callable[[], None] | None = None,
    ) -> None:
        """Poll the activated HTTP endpoint through loopback with a fixed deadline."""
        if self._endpoint is None:
            raise ControlProxyError(
                "tool_runtime_destination_not_ready",
                "The control proxy destination was not activated.",
            )
        started = time.monotonic()
        deadline = started + deadline_seconds
        attempts = 0
        while time.monotonic() < deadline:
            if readiness_guard is not None:
                readiness_guard()
            attempts += 1
            if self._http_probe(deadline):
                self.destination_ready_monotonic = time.monotonic()
                self.startup_polling_attempts = attempts
                self.startup_polling_elapsed_seconds = (
                    self.destination_ready_monotonic - started
                )
                return
            self.assert_healthy()
            if readiness_guard is not None:
                readiness_guard()
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        self.startup_polling_attempts = attempts
        self.startup_polling_elapsed_seconds = time.monotonic() - started
        raise ControlProxyError(
            "tool_runtime_destination_not_ready",
            "The verified SWE-ReX destination did not become ready before deadline.",
        )

    def _http_probe(self, deadline: float) -> bool:
        try:
            remaining = max(0.001, deadline - time.monotonic())
            with socket.create_connection(
                (self.bind_address, self.source_port), timeout=min(0.25, remaining)
            ) as client:
                client.settimeout(max(0.001, min(0.25, deadline - time.monotonic())))
                client.sendall(
                    b"GET /is_alive HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
                )
                return client.recv(16).startswith(b"HTTP/")
        except OSError:
            return False

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
                with self._connection_lock:
                    if self.first_client_connection_monotonic is None:
                        self.first_client_connection_monotonic = time.monotonic()
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
            if not self._endpoint_ready.wait(timeout=0.2):
                return
            endpoint = self._endpoint
            if endpoint is None:
                return
            destination = socket.create_connection(
                (endpoint.internal_ipv4, endpoint.destination_port), timeout=0.25
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
