from __future__ import annotations

import socket
import threading
import time

import pytest

from cgr.quantum_repair.model_provider.control_proxy import (
    ControlProxyEndpoint,
    ControlProxyError,
    LoopbackControlProxy,
    select_loopback_port,
)


def _endpoint() -> ControlProxyEndpoint:
    return ControlProxyEndpoint(
        container_identity="owned-container",
        image_identity="sha256:" + "a" * 64,
        network_identity_sha256="b" * 64,
        ownership_nonce="c" * 32,
        internal_ipv4="127.0.0.2",
        destination_port=8000,
    )


class _EchoServer:
    def __init__(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.2", 8000))
        self.listener.listen()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> _EchoServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)

    def _serve(self) -> None:
        self.listener.settimeout(0.1)
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except (OSError, TimeoutError):
                continue
            with connection:
                while data := connection.recv(4096):
                    connection.sendall(data)


class _HttpHealthServer:
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.listener: socket.socket | None = None

    def __enter__(self) -> _HttpHealthServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        listener = self.listener
        if listener is not None:
            listener.close()
        self.thread.join(timeout=2)

    def _serve(self) -> None:
        if self.stop.wait(self.delay_seconds):
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.2", 8000))
        listener.listen()
        listener.settimeout(0.05)
        self.listener = listener
        self.ready.set()
        while not self.stop.is_set():
            try:
                connection, _ = listener.accept()
            except (OSError, TimeoutError):
                continue
            with connection:
                connection.settimeout(0.5)
                try:
                    connection.recv(4096)
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
                    )
                except OSError:
                    continue


def test_proxy_binds_ipv4_loopback_and_forwards_bidirectionally() -> None:
    with _EchoServer():
        port = select_loopback_port()
        proxy = LoopbackControlProxy(source_port=port, endpoint=_endpoint())
        proxy.start()
        proxy.assert_healthy()
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"no-credentials-or-docker-socket")
            assert client.recv(4096) == b"no-credentials-or-docker-socket"
        assert proxy.stop() is True
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "::1", "192.0.2.1"])
def test_proxy_rejects_every_non_ipv4_loopback_bind(address: str) -> None:
    with pytest.raises(ControlProxyError) as raised:
        LoopbackControlProxy(
            source_port=select_loopback_port(),
            endpoint=_endpoint(),
            bind_address=address,
        )
    assert raised.value.code == "tool_control_proxy_bind_failure"


def test_proxy_rejects_foreign_listener_on_selected_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        proxy = LoopbackControlProxy(
            source_port=int(foreign.getsockname()[1]), endpoint=_endpoint()
        )
        with pytest.raises(ControlProxyError) as raised:
            proxy.start()
    assert raised.value.code == "tool_control_proxy_foreign_listener"


def test_proxy_rejects_non_policy_destination_port() -> None:
    with pytest.raises(ControlProxyError) as raised:
        LoopbackControlProxy(
            source_port=select_loopback_port(),
            endpoint=ControlProxyEndpoint(
                container_identity="owned-container",
                image_identity="sha256:" + "a" * 64,
                network_identity_sha256="b" * 64,
                ownership_nonce="c" * 32,
                internal_ipv4="127.0.0.2",
                destination_port=9000,
            ),
        )
    assert raised.value.code == "tool_control_proxy_destination_invalid"


def test_proxy_termination_is_detected() -> None:
    with _EchoServer():
        proxy = LoopbackControlProxy(
            source_port=select_loopback_port(), endpoint=_endpoint()
        )
        proxy.start()
        proxy.stop()
        with pytest.raises(ControlProxyError) as raised:
            proxy.assert_healthy()
    assert raised.value.code == "tool_control_proxy_terminated"


def test_reserved_listener_precedes_first_official_request_and_activation() -> None:
    with _HttpHealthServer() as server:
        assert server.ready.wait(1)
        proxy = LoopbackControlProxy(source_port=0)
        proxy.start()
        observed: list[bytes] = []

        def official_health_request() -> None:
            with socket.create_connection(
                ("127.0.0.1", proxy.source_port), timeout=1
            ) as client:
                client.settimeout(1)
                client.sendall(b"GET /is_alive HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                observed.append(client.recv(64))

        official = threading.Thread(target=official_health_request)
        official.start()
        proxy.wait_for_first_client(deadline_seconds=1)
        proxy.activate(_endpoint())
        proxy.wait_until_destination_ready(deadline_seconds=1)
        official.join(timeout=1)
        proxy.assert_startup_order()
        assert observed and observed[0].startswith(b"HTTP/")
        assert proxy.listener_ready_monotonic is not None
        assert proxy.first_client_connection_monotonic is not None
        assert (
            proxy.listener_ready_monotonic
            <= proxy.first_client_connection_monotonic
            <= proxy.destination_ready_monotonic
        )
        assert proxy.stop() is True


def test_delayed_destination_readiness_retries_with_observable_budget() -> None:
    with _HttpHealthServer(delay_seconds=0.12):
        proxy = LoopbackControlProxy(source_port=0, endpoint=_endpoint())
        proxy.start()
        with socket.create_connection(("127.0.0.1", proxy.source_port), timeout=1):
            pass
        proxy.wait_for_first_client(deadline_seconds=1)
        proxy.wait_until_destination_ready(deadline_seconds=1, poll_seconds=0.01)
        assert proxy.startup_polling_attempts > 1
        assert proxy.startup_polling_elapsed_seconds >= 0.08
        proxy.assert_startup_order()
        assert proxy.stop() is True


def test_destination_that_never_becomes_ready_fails_at_bounded_deadline() -> None:
    proxy = LoopbackControlProxy(source_port=0, endpoint=_endpoint())
    proxy.start()
    started = time.monotonic()
    with pytest.raises(ControlProxyError) as raised:
        proxy.wait_until_destination_ready(deadline_seconds=0.08, poll_seconds=0.005)
    elapsed = time.monotonic() - started
    assert raised.value.code == "tool_runtime_destination_not_ready"
    assert proxy.startup_polling_attempts >= 1
    assert proxy.startup_polling_elapsed_seconds >= 0.08
    assert elapsed < 0.5
    assert proxy.stop() is True


def test_readiness_guard_stops_immediately_for_container_exit() -> None:
    proxy = LoopbackControlProxy(source_port=0, endpoint=_endpoint())
    proxy.start()

    def container_exited() -> None:
        raise ControlProxyError(
            "tool_container_terminated_during_startup",
            "The verified container exited.",
        )

    with pytest.raises(ControlProxyError) as raised:
        proxy.wait_until_destination_ready(
            deadline_seconds=1,
            readiness_guard=container_exited,
        )
    assert raised.value.code == "tool_container_terminated_during_startup"
    assert proxy.startup_polling_attempts == 0
    assert proxy.stop() is True


def test_destination_identity_substitution_fails_immediately() -> None:
    proxy = LoopbackControlProxy(source_port=0, endpoint=_endpoint())
    replacement = ControlProxyEndpoint(
        container_identity="substituted-container",
        image_identity=_endpoint().image_identity,
        network_identity_sha256=_endpoint().network_identity_sha256,
        ownership_nonce=_endpoint().ownership_nonce,
        internal_ipv4=_endpoint().internal_ipv4,
    )
    with pytest.raises(ControlProxyError) as raised:
        proxy.activate(replacement)
    assert raised.value.code == "tool_container_identity_mismatch"


def test_invalid_reserved_port_fails_closed() -> None:
    with pytest.raises(ControlProxyError) as raised:
        LoopbackControlProxy(source_port=65536)
    assert raised.value.code == "tool_control_proxy_bind_failure"


def test_prebound_listener_startup_order_does_not_flap() -> None:
    with _HttpHealthServer() as server:
        assert server.ready.wait(1)
        for _ in range(25):
            proxy = LoopbackControlProxy(source_port=0, endpoint=_endpoint())
            proxy.start()
            with socket.create_connection(
                ("127.0.0.1", proxy.source_port), timeout=1
            ) as client:
                client.sendall(b"GET /is_alive HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                assert client.recv(64).startswith(b"HTTP/")
            proxy.wait_for_first_client(deadline_seconds=1)
            proxy.assert_startup_order()
            assert proxy.stop() is True
