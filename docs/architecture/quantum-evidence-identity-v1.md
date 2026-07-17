# Quantum Evidence Identity v1

## Three identities, three questions

CGR does not redefine content addressing. `content_sha256` remains the digest
of the exact canonical artifact document written by one run. A duration change
therefore changes an exact- or VQE-result content hash, its run-specific
lineage, and the run receipt. Those differences are useful evidence about what
actually happened and must not be discarded.

`scientific_result_sha256` answers a different question: did two executions
perform the same declared computation and return the same scientific result?
It is computed from `ScientificResultIdentity`, never from a supplied digest.
The projection includes the experiment and model artifacts, Hamiltonians,
solver identity/configuration, compatible environment, unrounded energies,
particle/orbital/qubit counts, convergence, mapper, and verification policy.
It excludes duration, run ID, timestamps, paths, logs, host identity, process
identity, and receipt location.

`scientific_outcome_sha256` answers whether the full verified conclusion is the
same. `AuthorizedScientificOutcome` binds both scientific-result hashes to the
experiment, structure, electronic problem, active space, both Hamiltonians,
the exact/VQE numerical comparison and tolerance, verifier outcomes,
authorization decision, environment compatibility, and warning
classification. It excludes the run ID, receipt hash, duration, path, and
run-specific lineage hash. An unauthorized outcome cannot reuse an authorized
outcome identity because authorization and verifier outcomes are in the
projection.

## Exact and VQE projections

The exact identity records the NumPy minimum-eigensolver version and
configuration, the default particle-sector filtering policy, particle count,
raw eigenvalue auxiliary value, electronic/nuclear/total energy, model counts,
and the common scientific model fingerprints.

The VQE identity additionally records the statevector estimator, UCCSD ansatz
artifact and identifier, Hartree-Fock initial state, optimizer configuration,
initial-point hash, optimized-parameter hash, convergence, and optimization
trace hash. The trace contains deterministic scientific evaluations and no
timestamps or per-evaluation durations, so its complete artifact hash is also
its scientific trace fingerprint. Changing an energy, Hamiltonian, solver
configuration, environment compatibility field, optimized point, trace, or
convergence state changes the scientific-result identity.

## Recomputed authorization and lineage

The result artifact schema is `cgr.quantum-result-artifact/2.0.0`; it wraps the
complete timed execution result, canonical scientific projection, and digest.
The receipt schema is `cgr.quantum-preflight-receipt/2.0.0`; it exposes both
result identities and the scientific-outcome identity while retaining complete
artifact pointers. Validators recompute the digests and compare the projection
back to execution values. Receipt verification also checks the exact full
result pointers. Replacing a full artifact with a scientifically equivalent
artifact therefore still changes and invalidates the run receipt; replacing a
scientific identity invalidates the outcome as well.

Historical flat results and v1 receipts remain identifiable for inspection,
but are marked legacy and cannot silently receive hardened authorization. CGR
does not invent semantic hashes for historical evidence without revalidation.

Run-specific lineage identifies the exact artifacts produced by one
occurrence. It may differ between otherwise identical executions. Scientific
outcome identity is the stable comparison layer; it does not weaken lineage.

## Compatibility warning evidence

`compatibility-warnings.json` aggregates warnings deterministically by stable
code, normalized message, category, dependency/version, action, and first
phase. Known codes include:

- `qiskit_blueprint_circuit_deprecated`
- `qiskit_nlocal_deprecated`
- `scipy_sparse_efficiency_warning`
- `dependency_deprecation_warning`
- `dependency_runtime_warning`

Warning order is irrelevant; count is identity-relevant. Current Qiskit
deprecations and SciPy efficiency notices remain visible but non-blocking.
Changing the validated pins in this evidence-hardening change would mix a
runtime migration with identity design, so the risk is recorded for a later
controlled dependency upgrade.

## LiH acceptance

`cgr-quantum-preflight-acceptance` persists two independent 1.6 angstrom runs
and a runtime-only 1.7 angstrom mutation. Repeat acceptance requires equal
scientific model, result, optimized-point, trace, and outcome identities while
allowing complete result hashes to differ only in duration. Mutation acceptance
requires the experiment, structure, electronic/QCSchema evidence,
Hamiltonians, both result identities, outcome identity, and exact energy to
change without using a hard-coded energy. Cross-link probes prove that 1.6 and
1.7 evidence cannot be substituted and that complete-content substitution is
still detected.

The host script first verifies that UID 10001 can write the output mount, then
runs with no network, a read-only root, bounded CPU/memory/PIDs, no
capabilities, no Docker socket, and no home mount. The portable summary contains
only repository commit, image and lock identities, scientific hashes,
comparison decisions, warning status, authorization, and report hash; the host
path is printed only as an operational line.

This remains a trusted LiH small-system proof. It introduces neither
model-generated Qiskit code nor IBM execution. The next milestone is the
network-disabled untrusted candidate sandbox and CGR/SWE-agent repair loop.
