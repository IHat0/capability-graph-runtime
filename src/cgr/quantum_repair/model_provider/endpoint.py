"""Loopback-only OpenAI-compatible model endpoint verification."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .contracts import (
    ModelEndpointDescriptor,
    ProviderBudget,
    SamplingParameters,
    seal_contract,
)


class EndpointPolicyError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise EndpointPolicyError("Model endpoint redirects are prohibited in v1.")


def verify_model_endpoint(
    *,
    base_url: str,
    requested_model: str,
    api_key: str,
    request_timeout_seconds: int,
    sampling: SamplingParameters,
    budget: ProviderBudget,
) -> ModelEndpointDescriptor:
    identity, tls_policy = normalize_loopback_base_url(base_url)
    request = urllib.request.Request(
        identity.rstrip("/") + "/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=request_timeout_seconds) as response:
            if response.status != 200:
                raise EndpointPolicyError(
                    "Model endpoint health response was not successful."
                )
            body = response.read(2 * 1024 * 1024 + 1)
    except EndpointPolicyError:
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise EndpointPolicyError(
            f"Model endpoint is unavailable: {type(exc).__name__}."
        ) from exc
    if len(body) > 2 * 1024 * 1024:
        raise EndpointPolicyError("Model endpoint response exceeded its bound.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EndpointPolicyError("Model endpoint returned malformed JSON.") from exc
    model = _find_requested_model(payload, requested_model)
    context_length = _context_length(model, payload)
    values = {
        "base_url_identity": identity,
        "requested_model_identifier": requested_model,
        "observed_model_identifier": model["id"],
        "observed_context_length": context_length,
        "api_compatibility_version": "openai-v1",
        "tls_policy": tls_policy,
        "loopback_only": True,
        "sampling": sampling,
        "request_timeout_seconds": request_timeout_seconds,
        "maximum_total_tokens": budget.maximum_total_tokens,
    }
    return seal_contract(ModelEndpointDescriptor, values, "descriptor_sha256")


def normalize_loopback_base_url(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise EndpointPolicyError("Model endpoint must use HTTP or HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EndpointPolicyError(
            "Model endpoint URL cannot contain credentials or query data."
        )
    if not parsed.hostname:
        raise EndpointPolicyError("Model endpoint hostname is missing.")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
        }
    except socket.gaierror as exc:
        raise EndpointPolicyError(
            "Model endpoint hostname could not be resolved."
        ) from exc
    if not addresses or any(
        not ipaddress.ip_address(item).is_loopback for item in addresses
    ):
        raise EndpointPolicyError(
            "Model endpoint must resolve only to loopback interfaces."
        )
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host += f":{parsed.port}"
    path = parsed.path.rstrip("/") or "/v1"
    identity = urlunsplit((parsed.scheme, host, path, "", ""))
    policy = "loopback-http" if parsed.scheme == "http" else "verified-https"
    return identity, policy


def _find_requested_model(payload: Any, requested: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise EndpointPolicyError("Model endpoint response lacks a model list.")
    models = [item for item in payload["data"] if isinstance(item, dict)]
    selected = next((item for item in models if item.get("id") == requested), None)
    if selected is None:
        raise EndpointPolicyError(
            "Requested model identity was not observed at the endpoint."
        )
    return selected


def _context_length(model: dict[str, Any], payload: dict[str, Any]) -> int:
    names = (
        "max_model_len",
        "max_context_length",
        "context_length",
        "max_position_embeddings",
    )
    for source in (model, payload):
        for name in names:
            value = source.get(name)
            if isinstance(value, int) and value > 0:
                return value
    raise EndpointPolicyError("Model endpoint did not advertise a context length.")
