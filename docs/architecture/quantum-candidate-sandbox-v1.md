# Untrusted quantum candidate sandbox v1

The quantum-candidate benchmark treats every submitted Python workflow and every value it emits as hostile. Candidate execution and authorization are separate operations: a container may finish successfully, but only the trusted host adjudicator can authorize its result.

## Trust boundary

The candidate image is `cgr-quantum-candidate:1.0.0`. It contains only the pinned quantum dependency stack and runs as UID 10002. It does not contain the CGR package, tests, benchmark expectations, trusted LiH manifests, trusted artifacts, receipts, or acceptance summaries. Candidate source is mounted at runtime.

Each fresh container receives exactly three mounts:

| Container path | Access | Contents |
| --- | --- | --- |
| `/input/experiment.json` | read-only | Public declared experiment and protocol versions |
| `/candidate` | read-only | One candidate source bundle |
| `/output` | writable | Candidate-produced evidence only |

The controller explicitly uses no network, a read-only root, dropped capabilities, no-new-privileges, a bounded no-exec `/tmp`, two CPUs, 4096 MiB memory, 128 processes, bounded logs and output, and a 90-second maximum wall clock. There is no Docker socket, home, repository-root, or trusted-reference mount.

## Evidence flow

The public input contains the declared scientific experiment but no trusted energy, trusted Hamiltonian, expected diagnosis, benchmark case metadata, or authorization state. A candidate must emit `candidate-summary.json` plus reconstructable molecular, electronic, active-space, Hamiltonian, ansatz, trace, result, and environment artifacts.

After execution, the host walks the output tree without following links. It rejects symbolic links, special files, traversal, URLs, absolute claims, quota violations, malformed JSON, missing evidence, and claimed hashes that do not match collected bytes. Candidate authorization and identity claims are never accepted as facts.

The trusted adjudicator first verifies the hardened trusted-reference receipt and every required artifact's full-content hash. It then independently compares molecular structure, units, charge, multiplicity, basis, active space, mapper, fermionic and qubit Hamiltonians, total-energy decomposition, convergence, exact-reference agreement, content identities, scientific identity, and lineage. No expected LiH energy is embedded in the candidate benchmark code.

Every decision is a self-hashed, fail-closed receipt. Rejections retain execution evidence, candidate output, all findings, one deterministic primary failure, and a deterministic repair directive. Directives name the candidate source or protocol evidence that must be rebuilt; v1 does not run an LLM, repair tool, or SWE-agent.

## Benchmark and EC2 handoff

The manifest enumerates one independent valid control and 26 deliberately broken workflows. A pass requires the control to authorize, all negatives to reject with their expected primary diagnosis, zero missing or skipped cases, zero false accepts/rejects, network isolation for every case, and no trusted-evidence exposure.

Build the candidate image with `scripts/build-quantum-candidate-image.sh`. Run the benchmark with:

```bash
scripts/run-quantum-candidate-benchmark.sh \
  /path/to/verified/trusted-reference \
  /path/to/quantum-candidate-results
```

The run script resolves immutable image IDs, checks UID 10002 output access without performing root `chown`, preserves host logs separately, propagates controller and `tee` failures independently, and verifies the compact summary. Real container execution is intentionally an EC2 validation step when Docker is unavailable locally; pure tests still verify the generated security policy and arguments.
