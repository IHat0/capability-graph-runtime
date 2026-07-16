"""The only CGR boundary coupled to qiskit-algorithms' detailed API."""

from __future__ import annotations

from typing import Any, Callable

from ..errors import QuantumDependencyError


def _api() -> tuple[Any, Any, Any, Any]:
    try:
        from qiskit_algorithms import NumPyMinimumEigensolver, VQE  # type: ignore[import-not-found]
        from qiskit_algorithms.optimizers import SLSQP  # type: ignore[import-not-found]
        from qiskit_algorithms.utils import algorithm_globals  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError) as exc:
        raise QuantumDependencyError(
            "qiskit-algorithms is unavailable in the dedicated preflight runtime."
        ) from exc
    return NumPyMinimumEigensolver, VQE, SLSQP, algorithm_globals


def exact_eigensolver(*, filter_criterion: Callable[..., bool] | None) -> Any:
    numpy_solver, _, _, _ = _api()
    return numpy_solver(filter_criterion=filter_criterion)


def deterministic_vqe(
    *,
    estimator: Any,
    ansatz: Any,
    maximum_iterations: int,
    convergence_threshold: float,
    initial_point: Any,
    random_seed: int,
    callback: Callable[..., None],
) -> Any:
    _, vqe_type, optimizer_type, globals_ = _api()
    globals_.random_seed = random_seed
    optimizer = optimizer_type(
        maxiter=maximum_iterations,
        ftol=convergence_threshold,
        disp=False,
    )
    return vqe_type(
        estimator=estimator,
        ansatz=ansatz,
        optimizer=optimizer,
        initial_point=initial_point,
        callback=callback,
    )
