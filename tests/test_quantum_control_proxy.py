from __future__ import annotations

import socket
import threading

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
