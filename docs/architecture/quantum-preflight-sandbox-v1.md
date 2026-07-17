# Quantum Preflight Sandbox and Trusted LiH Reference v1

## Trust boundary

Generated Qiskit programs are untrusted and are deliberately absent from this
milestone. This package creates only the trusted reference side: one versioned
LiH objective is converted to a PySCF electronic-structure problem, solved by
exact diagonalization and independently by statevector VQE, verified, linked,
fingerprinted, and authorized. Successful Python execution is only operational
evidence; it is not scientific authorization.

The scientific identity is the molecule, coordinate unit and order, charge,
multiplicity, basis, reference method, explicit active space, Hamiltonian
mapping, solver declarations, VQE policy, and tolerances. Program correctness
is the separate claim that runtime evidence actually implements that identity.
`cgr.science` supplies canonical models, artifacts, lineage, structured
findings, and fail-closed workflow concepts; `cgr.quantum_preflight` extends
those contracts rather than creating a second evidence system.

## Declared LiH experiment

The committed manifest places Li at `(0, 0, 0)` and H at `(0, 0, 1.6)` in
angstrom, with charge zero, singlet multiplicity, STO-3G, and restricted
Hartree-Fock. Nuclear repulsion, electronic energy, and molecular total energy
are recorded separately; authorization compares unrounded total energies.

The active-space declaration is two electrons in two spatial orbitals with
explicit pre-transform indices `[1, 2]`. Before transformation the driver
records all orbital occupations and ordering. The implementation sums the
alpha and beta occupations selected by those indices and requires exactly two
electrons. Qiskit Nature 0.8.0 has a defect in explicit-list validation (it
compares the active-orbital count directly to the list). CGR therefore derives
the transformer's closed-shell default indices from recorded counts, requires
the derived list to equal `[1, 2]`, records that resolution policy, and only
then invokes the transformer without the broken optional argument. No
library-selected scientific active space is accepted silently.

The fermionic Hamiltonian is mapped with Jordan-Wigner. Both operator formats
use sorted terms and IEEE-754 hexadecimal real/imaginary coefficients, not
opaque `repr()` output. Raw scientific values, canonical identity encoding,
and numerical comparison tolerances remain distinct.

## Independent solvers and verification

`NumPyMinimumEigensolver` applies the electronic problem's default particle
filter and creates the trusted exact value during each run; no expected LiH
energy is stored in source, tests, documentation, or the manifest. VQE uses
`StatevectorEstimator`, Hartree-Fock, UCCSD, an all-zero point, SLSQP capped at
200 iterations, tolerance `1e-9`, and seed `1701`. Its execution function
cannot accept an exact energy, and the runner completes VQE before constructing
the exact result. Only the verifier later computes the absolute total-energy
difference against the declared `1e-5` Hartree limit.

The Scientific Executable Verifier emits existing CGR
`ScientificVerificationResult` contracts for specification, molecular
identity, electronic structure, Hamiltonian/Hermiticity, exact result, VQE,
numerical agreement, lineage, and environment. Authorization requires every
blocking verifier to pass. `execution_completed`,
`scientific_verification_passed`, `artifact_lineage_passed`, and `authorized`
are separate receipt fields.

Lineage binds objective/experiment to structure, QCSchema, electronic problem,
active space, fermionic and qubit Hamiltonians, both solver results,
verification, and receipt parents. Exact and VQE results must reference the
same authorized Hamiltonian hash. Content substitution after verification is
detectable from artifact bytes and receipt pointers.

## Linux isolation and reproducibility

PySCF runs only in the dedicated CPU-only Python 3.12 Linux image. It does not
enter CGR's mandatory dependencies, `.venv-quixbugs`, SWE-agent, or the host
Python installation. The image runs as UID/GID 10001, with a read-only root,
all capabilities dropped, `no-new-privileges`, two CPUs, 4 GiB memory, 256
PIDs, a 512 MiB `/tmp`, bounded evidence/log sizes, and single-thread numerical
environment variables. PySCF scratch and Python caches remain under `/tmp`.
The host mounts only the manifest and lock read-only plus `/output` writable;
it does not mount a home directory or Docker socket.

Runtime uses `--network none`. A socket probe targets the reserved TEST-NET-1
address and must fail; no IBM, PyPI, GitHub, or other service is contacted
during LiH execution. The image receives no IBM or AWS variables, reads no
credential values, and rejects any prohibited credential variable name in its
environment evidence. IBM submission is not implemented.

The base is fixed to `python:3.12.11-slim-bookworm`; because Docker was not
available on the implementation host, a registry digest could not be verified
locally. Each build and receipt records Docker's immutable image ID. Direct and
transitive wheels are hash-locked for CPython 3.12/manylinux x86-64.

The preferred `qiskit==2.5.0` has no Python 3.12-compatible distribution. The
captured resolver failure is in
`requirements/quantum-preflight-resolver-evidence.txt`; the smallest adjustment
is `qiskit==2.3.1`. Qiskit Nature 0.8.0, Qiskit Algorithms 0.4.0, Qiskit Aer
0.17.1, and PySCF 2.13.1 remain at the preferred versions. A compatibility test
locks this exception.

## Evidence and limits

Runs are built in a temporary same-filesystem directory and committed by
atomic rename. Prior runs are never overwritten. Timeouts preserve a failed
directory and cannot authorize. The Bash host wrapper adds a TERM/KILL deadline
around Docker; the Linux CLI also uses a wall-clock alarm. Evidence contains no
pickles, hostnames, usernames, secrets, absolute host paths, or volatile values
in scientific fingerprints.

This is a small-system proof, not quantum advantage, drug binding, general
chemistry validation, or IBM hardware success. It establishes one declared LiH
model. The next milestone is a network-disabled candidate-code sandbox and
CGR/SWE-agent repair loop that compares untrusted candidates only against this
trusted side.

## Operator runbook

Build and inspect the immutable image identifier:

```bash
./scripts/build-quantum-preflight-image.sh
```

```powershell
./scripts/build-quantum-preflight-image.ps1
```

Run the trusted 1.6 angstrom reference with networking disabled:

```bash
./scripts/run-lih-quantum-preflight.sh ./quantum-preflight-results
```

```powershell
./scripts/run-lih-quantum-preflight.ps1 -ResultRoot ./quantum-preflight-results
```

Run container tests after building:

```bash
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint python \
  --env CGR_QUANTUM_INTEGRATION=1 \
  --env CGR_QUANTUM_IMAGE_ID="$(docker image inspect --format '{{.Id}}' cgr-quantum-preflight:1.0.0)" \
  cgr-quantum-preflight:1.0.0 -m pytest -m 'quantum_unit or quantum_integration or quantum_container'
```

CLI exits are: `0` authorized, `2` manifest/specification, `3` execution,
`4` scientific verification, `5` artifact/lineage integrity, `6`
dependency/environment, and `7` timeout.

## Hardened identities and acceptance

Artifact `content_sha256` continues to cover complete run bytes, including
duration. The v2 result wrapper separately exposes a recomputed
`scientific_result_sha256`; the v2 receipt exposes both exact and VQE result
identities plus `scientific_outcome_sha256`. Thus repeat result, lineage, and
receipt hashes may differ while their scientific identities match. Runtime
warnings are retained as deterministic compatibility evidence, with current
Qiskit deprecations and SciPy efficiency notices non-blocking. See
`quantum-evidence-identity-v1.md` for the projections and cross-link rules.

Run the durable two-repeat plus 1.7 angstrom mutation acceptance package:

```bash
mkdir -p "$HOME/cgr-evidence/quantum-preflight"
sudo chown -R 10001:10001 "$HOME/cgr-evidence/quantum-preflight"
./scripts/run-lih-quantum-preflight-acceptance.sh \
  "$HOME/cgr-evidence/quantum-preflight"
./scripts/run-quantum-preflight-integration.sh \
  "$HOME/cgr-evidence/quantum-preflight/integration.log"
```

The acceptance CLI exits are: `0` passed, `2` manifest/specification, `3`
trusted execution, `4` scientific verification, `5` repeat determinism, `6`
mutation sensitivity, `7` evidence integrity, `8` environment/dependency, `9`
timeout, and `10` output persistence.
