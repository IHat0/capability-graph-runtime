# Quantum candidate repair v1

## Purpose and scope

The quantum candidate repair subsystem is a production-oriented controller for bounded repair of rejected hostile quantum candidates. It turns a verified adjudication finding into a sanitized directive, delegates the edit proposal through a provider-neutral interface, validates and applies structured edits to a fresh source tree, then reuses the existing hostile executor and trusted adjudicator. A patch or provider assertion can never authorize a candidate.

This milestone is not a claim that the complete system is production-ready. The subsystem remains limited to the reviewed LiH scientific benchmark until it has broader provider, chemistry, failure-recovery, and operational validation. Real 30-case acceptance must run in the EC2 hostile sandbox; local deterministic unit tests are not a substitute.

## Trust boundaries

The public experiment and candidate source are provider-visible. Trusted reference payloads, exact and VQE energies, trusted Hamiltonians, trusted source, scientific-result fingerprints, prior candidate outputs, credentials, host paths, and repair-run internals are not. Only the existing trusted adjudicator receives the trusted reference. The generic controller receives only its immutable receipt identity.

The `RepairProvider` protocol describes capabilities and accepts a `QuantumRepairDirective`, a source root, and its `SourceManifest`. Deterministic, model-backed, SWE-agent-backed, and human-backed implementations therefore cross the same boundary and return the same `QuantumRepairPatch` contract. Every returned patch follows the identical validator; provider trust classification never weakens policy.

## Contracts and directive sanitization

V1 defines explicit schemas for directives, patches, attempts, run receipts, source manifests, policies, events, and the separate repair benchmark. Each evidence-bearing contract uses canonical serialization and a recomputed SHA-256 identity. Unknown schemas fail validation. Incomplete or legacy runs can be inspected as recovery state but cannot silently authorize.

Directives contain the diagnosed category, public-task-oriented guidance, editable paths and file types, hard quotas, invariants, re-verification gates, attempt budget, and an explicit inventory of withheld information. The leakage scan operates on provider-facing guidance and rejects trusted-answer language or high-precision negative energy-like values. Public task declarations such as the declared bond distance are permitted.

## Source and patch security

Before each attempt, the controller creates a complete manifest containing relative path, byte hash, size, mode, regular-file type, symlink state, and executable state. Source collection rejects traversal, symbolic links, detectable hard links, devices, FIFOs, sockets, and workspace escapes.

Patches are structured exact-text replacements; provider values are never interpolated into a shell command. Validation checks directive and base identities, claimed finding, path and file-type allowlists, prohibited repository areas, changed-file/line/byte quotas, binary content, credential and network-client additions, clean one-time hunk application, no-op output, repeated patches, prior source states, and oscillation. Dependency locks, Docker material, CGR source, trusted evidence, and benchmark manifests are outside the default edit scope.

The validator rejects a `valid-control` mode flip, edits to the candidate identifier, and an output source identity matching a prohibited control tree. The reviewed benchmark provider repairs declarative fields from the public experiment or removes the diagnosed blocking source operation. It does not copy the control fixture or switch a scenario selector.

## Fresh attempts and state machine

Each attempt is built in a new atomic temporary directory. It receives a new source snapshot, output directory, execution evidence, and adjudication receipt. Candidate outputs are never copied into the next attempt; only the newly validated repaired-source tree becomes its input. The existing executor continues to enforce a non-root UID, no network, read-only root, dropped capabilities, `no-new-privileges`, resource limits, bounded temporary storage, and only explicit public-input/source/output mounts.

The persisted state path is:

```text
created -> source_snapshotted -> candidate_executing -> adjudicated
        -> directive_created -> repair_proposed -> patch_validated
        -> patch_applied -> reexecution_pending -> authorized
```

Terminal states cover rejection, human review, provider failure, patch rejection, attempt/time exhaustion, repeated failure, oscillation, and controller failure. Illegal transitions fail. Temporary attempts are renamed atomically only after their immutable attempt receipt is written.

## Persistence, resume, and idempotency

Runs use monotonic `repair-run-NNN` directories. A completed `--resume` performs read-only replay, checks the persisted summary against the run receipt, and returns the same authorization and terminal summary without duplicating or rerunning an attempt. An incomplete run reports the last complete attempt and any corrupt temporary attempt. V1 refuses to infer or reuse missing authorization evidence.

Automatic continuation through an interrupted provider or candidate process is intentionally not performed without the original in-memory trusted/provider context. The recovery status is safe and explicit, but operational continuation of an incomplete run is a remaining limitation for the model-provider milestone. Completed-run replay and idempotency are fully supported.

## Replay verification

`cgr-quantum-candidate-repair-verify` is read-only and never executes candidate code. It verifies the run manifest, policy and provider capability identities; public-input bytes; original and per-attempt source trees; attempt ordering and parent linkage; execution source and evidence pointers; adjudication/public-task/trusted-receipt linkage; directive, patch, validation, and repaired-source identities; final source, authorization, and scientific outcome; and the ordered event stream. Deleted, inserted, reordered, substituted, or cross-linked evidence fails closed.

Future schema versions require an explicit migrator that produces a new receipt and preserves the old evidence. Missing fields are never synthesized into authorization.

## Multi-attempt diagnosis evolution

The repair benchmark is separate from the frozen 27-case diagnosis benchmark. It contains one valid control, all 26 single defects, and three composites. A composite initially exposes only its blocking syntax/protocol/security defect. After the first reviewed patch and fresh execution, trusted adjudication exposes the latent structure/result/mapper defect. A second independent patch is applied and a third attempt may authorize. Intermediate authorization is an acceptance failure.

The reviewed deterministic provider lives in `benchmark_provider.py`, is labeled as benchmark-only, and is absent from general provider registration. Benchmark-specific mappings are acceptance adapter knowledge, not product controller logic. Correct declarative values are derived from the public experiment; trusted output is neither accepted nor available.

## CLI and exit categories

```text
cgr-quantum-candidate-repair --candidate-source ... --public-experiment ... \
  --trusted-reference ... --result-root ... --candidate-image sha256:... \
  --provider reviewed-benchmark
cgr-quantum-candidate-repair --resume <repair-run-directory>
cgr-quantum-candidate-repair-verify <repair-run-directory>
cgr-quantum-candidate-repair-benchmark ...
```

Exit categories are `0` authorized, `2` input, `3` trusted reference, `4` candidate execution, `5` adjudication, `6` directive, `7` provider, `8` patch policy, `9` attempts, `10` total time, `11` persistence/replay, and `12` benchmark expectation.

## Next milestone

The next milestone is a model-backed provider adapter for pristine SWE-agent and Qwen. It must receive the same sanitized directive, operate within a separately sandboxed tool boundary, declare network/tool capabilities, and return the same structured patch. It must not add authorization power or receive trusted answers. Broader scientific cases, true interrupted-run continuation with re-supplied immutable runtime context, provider-process termination, and additional hostile-platform acceptance also remain to be validated.
