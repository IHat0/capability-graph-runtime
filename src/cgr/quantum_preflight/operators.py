"""Canonical, finite operator serialization independent of Python repr output."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from cgr.science import sha256_fingerprint

from .errors import QuantumIntegrityError


def encode_float(value: float) -> str:
    """Encode IEEE-754 values deterministically without comparison rounding."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise QuantumIntegrityError("Operator coefficients must be finite.")
    if numeric == 0.0:
        numeric = 0.0
    return numeric.hex()


def encode_complex(value: complex | float) -> dict[str, str]:
    numeric = complex(value)
    return {"real_hex": encode_float(numeric.real), "imag_hex": encode_float(numeric.imag)}


def _ordered_terms(
    terms: Mapping[str, complex | float] | Iterable[tuple[str, complex | float]],
) -> list[dict[str, Any]]:
    items = terms.items() if isinstance(terms, Mapping) else terms
    combined: dict[str, complex] = {}
    for label, coefficient in items:
        if not isinstance(label, str):
            raise QuantumIntegrityError("Operator labels must be strings.")
        numeric = complex(coefficient)
        encode_complex(numeric)
        combined[label] = combined.get(label, 0j) + numeric
    return [
        {"label": label, "coefficient": encode_complex(combined[label])}
        for label in sorted(combined)
    ]


def serialize_fermionic_operator(
    terms: Mapping[str, complex | float] | Iterable[tuple[str, complex | float]],
    *,
    register_length: int,
) -> dict[str, Any]:
    if register_length <= 0:
        raise QuantumIntegrityError("Fermionic register length must be positive.")
    return {
        "schema_version": "cgr.fermionic-operator/1.0.0",
        "coefficient_encoding": "ieee754-binary64-hex",
        "register_length": register_length,
        "terms": _ordered_terms(terms),
    }


def serialize_qubit_operator(
    terms: Mapping[str, complex | float] | Iterable[tuple[str, complex | float]],
    *,
    number_of_qubits: int,
    mapper: str,
) -> dict[str, Any]:
    if number_of_qubits <= 0:
        raise QuantumIntegrityError("Qubit count must be positive.")
    return {
        "schema_version": "cgr.qubit-operator/1.0.0",
        "coefficient_encoding": "ieee754-binary64-hex",
        "number_of_qubits": number_of_qubits,
        "mapper": mapper,
        "terms": _ordered_terms(terms),
    }


def operator_fingerprint(serialized: dict[str, Any]) -> str:
    return sha256_fingerprint(serialized)


def maximum_antihermitian_coefficient(operator: Any) -> float:
    """Measure ||H-H†|| coefficient-wise at the guarded Qiskit boundary."""
    try:
        residual = (operator - operator.adjoint()).simplify()
        coefficients = residual.coeffs
    except (AttributeError, TypeError, ValueError) as exc:
        raise QuantumIntegrityError("Unable to evaluate Hamiltonian Hermiticity.") from exc
    if len(coefficients) == 0:
        return 0.0
    maximum = max(abs(complex(value)) for value in coefficients)
    if not math.isfinite(maximum):
        raise QuantumIntegrityError("Hermiticity residual is non-finite.")
    return float(maximum)
