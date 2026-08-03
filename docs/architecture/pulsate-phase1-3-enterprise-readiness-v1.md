# Pulsate Phase 1–3 Enterprise Readiness Gate v1

## Decision record

Audit date: 2026-08-02

Starting commit: `39136886b4472d86fa0bca970fca02e07cbb7215`

Scope: Universal scientific contracts, universal molecular project/workspace,
and the non-executing capability/resource planner.

| Phase | Gate status | Decision |
| --- | --- | --- |
| Phase 1 — universal scientific contracts | BLOCKED | Not enterprise-ready. Local dependency-vulnerability evidence remains incomplete. |
| Phase 2 — universal molecular project and workspace | BLOCKED | Not enterprise-ready. Caller authorization, production recovery/deployment evidence, and production-scale performance evidence are absent. |
| Phase 3 — capability and resource planner | BLOCKED | Not enterprise-ready. Caller authorization, operational observability, and production catalogue provisioning remain incomplete. |

No phase is labelled enterprise-ready while an applicable control is `FAIL` or
`BLOCKED`.

## Enterprise Foundation E2 security and operations boundary

E2 adds a single centralized boundary around every implemented Phase 1-3 API
surface. It does not change scientific behavior or declare any phase
enterprise-ready.

### Authentication status

Protected requests use a standard `Authorization: Bearer` header through an
injectable authenticator. Authentication produces an immutable, versioned
principal containing bounded subject, tenant, authentication-method, scope,
role, and audit identities. Raw bearer credentials are never part of the
principal, request context, audit schema, log fields, or metrics.

E3A found PyJWT 2.13.0 and cryptography 49.0.0 already hash-pinned in the
IBM Runtime extension lock, but not installed by the production HTTP lock or
the local validation environment. The production authenticator is implemented
against PyJWT and fails closed when that declared verifier is unavailable.
No custom signature, JWK, ASN.1, RSA, ECDSA, or EdDSA implementation is used.
An explicitly enabled development bearer authenticator exists only when
`PULSATE_ENVIRONMENT=development`; enabling it in production is a configuration
error and emits a startup warning when used. Startup-loaded OIDC key material,
issuer/audience/algorithm/claim verification, and key rotation remain E3
deployment blockers.

### Authorization and enumeration policy

One route inventory maps every protected route to distinct `project.read`,
`artifact.read`, `scene.read`, `planning.evaluate`, `run.read`,
`experiment.read`, `execution.request`, or `execution.approve` actions. A
central policy requires both the action scope and a current same-tenant subject
or role grant for the exact resource. Artifact reads require authorized parent
project access; planning requires project read plus planning permission.
Execution request and approval remain separate permissions.

Authentication and authorization run before endpoint handlers and repository
resolvers. An authenticated caller without a grant receives the same typed 403
response for an existing or missing identifier, without private metadata or a
repository lookup. An authorized caller retains the existing controlled 404.
Production grants are injectable; wildcard grants are accepted only by an
explicitly configured development/test provider.

### Audit, logging, metrics, and health

Security-relevant decisions use immutable canonical audit records with safe
digests rather than raw resource or tenant identifiers. Authentication failure,
authorization denial/allow, protected reads, planning, execution requests, and
approval actions are append-oriented. Mandatory audit failure blocks planning,
execution, and approval before the operation. Read audit failure is explicitly
configurable; the production default fails closed. The in-memory sink is test
evidence only and is not represented as tamper-proof storage. A durable,
retained, independently protected audit backend remains an E3 blocker.

The standard logging stack emits bounded JSON events for lifecycle,
authentication, authorization, planning, repository/service failure, and
request completion. Fields exclude credentials, molecular payloads, topology,
absolute paths, and raw internal exceptions. A dependency-free collector keeps
bounded-cardinality request, duration, authentication, authorization, planning,
capability-count, and repository-failure metrics for tests. Exporter and
retention integration remain E3 concerns; no public metrics endpoint is added.

Every response carries a validated or generated `X-Correlation-ID`; an
independent unpredictable request identifier is held in request-local context.
`/live` proves process responsiveness. `/ready` also requires successful
application lifecycle, configured repositories/services, authenticator, grant
provider, and audit sink. An explicitly empty scientific catalogue remains a
valid ready scientific configuration.

### Frontend boundary

The TypeScript API client accepts an asynchronous access-token provider and
places a bearer credential only in headers for protected relative Pulsate API
paths. It never adds credentials to URLs or the public health endpoint. HTTP
401, 403, 404, and 503 map to distinct controlled authentication-required,
access-denied, authorized-not-found, and service-unavailable states while the
existing molecular and planning components remain unchanged. A real browser
OIDC session provider and login/logout UX remain E3 blockers.

### E2 validation gate

`tests/test_pulsate_security.py` is explicit `core` and `backend_core` gate
membership alongside the established 16 Phase 1-3 files. It covers the
contracts, production/development authentication behavior, tenant-aware grants,
route completeness, enumeration resistance, correlation identity, safe logs,
audit policy, metrics, readiness, and concurrent context isolation. The
frontend API-client regression covers token placement and distinct controlled
HTTP states. Exact post-change local execution results must be recorded in the
change review; this document does not treat an unexecuted command as evidence.

E3 must still provision the declared verifier in the HTTP runtime and complete
deployment evidence, metrics export/alerting, browser session integration,
operational recovery, and production load/concurrency evidence. E4 remains the
explicit final readiness gate.

## Enterprise Foundation E3A production security providers

E3A converts the injectable E2 boundary into a validated production composition
without changing scientific behavior. It does not declare E3, a scientific
phase, or Pulsate enterprise-ready.

### Configuration and secret model

`PulsateRuntimeConfiguration` is frozen and extra-forbid. Production must be
selected explicitly and validates service identity, authentication trust,
authorization/audit stores, observability identity, trusted roots, resource
limits, and readiness requirements before provider construction. Relative key
and database paths resolve only beneath their explicit trusted configuration or
application-data root. Roots must already be real non-symlink directories;
key files must be bounded ordinary non-symlink files whose resolved identity
remains under the trusted root. Configuration failures use a path-free field
category and never reproduce the rejected value.

The supported production environment names, without values, are:

```text
PULSATE_ENVIRONMENT
PULSATE_SERVICE_IDENTITY
PULSATE_APPLICATION_DATA_ROOT
PULSATE_TRUSTED_CONFIGURATION_ROOT
PULSATE_AUTH_ISSUER
PULSATE_AUTH_AUDIENCES
PULSATE_AUTH_ALGORITHMS
PULSATE_AUTH_JWKS_FILE
PULSATE_AUTH_SUBJECT_CLAIM
PULSATE_AUTH_TENANT_CLAIM
PULSATE_AUTH_SCOPE_CLAIM
PULSATE_AUTH_ROLES_CLAIM
PULSATE_AUTH_CLOCK_SKEW_SECONDS
PULSATE_AUTH_MAXIMUM_TOKEN_BYTES
PULSATE_GRANT_DATABASE_FILE
PULSATE_GRANT_BUSY_TIMEOUT_MS
PULSATE_GRANT_ALLOW_WILDCARDS
PULSATE_AUDIT_DATABASE_FILE
PULSATE_AUDIT_MANDATORY
PULSATE_AUDIT_READ_FAIL_CLOSED
PULSATE_AUDIT_BUSY_TIMEOUT_MS
PULSATE_AUDIT_VERIFICATION_MAXIMUM_RECORDS
```

Secrets use a bounded redacting wrapper when a secret-bearing deployment
adapter needs one. Its `str` and `repr` never reveal the value. Runtime
configuration canonical serialization contains no bearer credentials, private
key material, or environment secret values; only its safe deterministic
SHA-256 fingerprint may enter startup logs. Environment secrets are never
written to disk by E3A.

### JWT and offline JWKS trust

The production authenticator accepts only configured asymmetric RS/ES
algorithms and validates compact-token size, signature, exact algorithm, key
identifier, issuer, audience, subject, tenant, expiration, issued-at sanity,
optional not-before, and configured clock skew. It rejects `alg=none`, unknown
algorithms or keys, malformed/oversized tokens, empty identities, invalid claim
shapes, and algorithm/key-type or EC-curve conflicts. Scope and role mapping is
sorted and deterministic. The audit identity is derived only after successful
verification and no token or raw claim set is logged or audited.

JWKS is loaded only at startup from the configured local file. The document is
bounded to 256 KiB and 32 signing keys. Duplicate/missing key identifiers,
non-signing use or operations, unsupported key types, and inconsistent
algorithms fail startup. Requests use an immutable in-memory snapshot and never
perform key discovery or network retrieval. Explicit internal reload validates
the complete replacement before atomically swapping it, preserves the last
valid snapshot on failure, and emits only a safe success/failure event. E3A
adds no administrative reload endpoint.

### Durable grants and audit

`SQLiteGrantProvider` implements the E2 provider contract with a versioned,
transactionally initialized schema. It stores explicit subject-or-role grants,
tenant and resource identities, sorted actions, validity interval, creation
time, and revocation state. Revocation is retained rather than deleted.
Database busy timeouts are bounded, foreign keys are enabled, writes are
transactional, duplicate identifiers are idempotent only for identical
evidence, and connections are serialized for deterministic thread-safe access.
Provider or schema failure is a controlled path-free authorization
unavailability and readiness failure. No default production users or grants
exist.

`SQLiteAuditSink` appends immutable canonical E2 audit records with a monotonic
sequence, previous-record fingerprint, record fingerprint, schema version, and
persisted head. Startup and explicit bounded verification detect content
modification, sequence gaps/deletion, invalid links, and head mismatch.
Appending a record and advancing the head occur in one immediate transaction;
the sink API exposes no update/delete operation. Mandatory security audit
failure remains fail-closed, and read-audit behavior remains explicitly
configurable.

A local hash-chained SQLite audit store is integrity-evident against accidental
or unsophisticated mutation, but is not equivalent to externally anchored WORM
or independently administered audit storage. External anchoring, independent
retention, backup, and recovery remain deployment blockers.

### Lifecycle, readiness, and configuration check

`ProductionSecurityServices` composes the offline authenticator, SQLite grants,
SQLite audit, bounded metrics, and structured logger from one validated
configuration. Authentication, authorization, and audit must all initialize
before readiness. A partial startup closes providers already opened; repeated
startup is idempotent and shutdown is safe. Tests may still inject the E2
deterministic providers. Development authentication remains explicitly enabled
only in `development`; production rejects it.

`/live` remains independent. `/ready` returns only the coarse application,
security, and repository categories and requires lifecycle, production security,
and configured repository services. Detailed provider readiness and the safe
configuration fingerprint are available only from the injected internal
diagnostic snapshot, not a public endpoint.

`cgr-pulsate-config-check` loads production configuration, validates roots and
limits, initializes offline keys and both durable schemas, reports only coarse
component status plus the safe configuration fingerprint, closes every provider,
and exits nonzero on any failure. It does not start an HTTP listener or invoke
scientific or IBM execution.

### Remaining E3B blockers

- Deployment artifact and hardened runtime, including provision of the declared
  PyJWT/cryptography verifier in the HTTP image.
- TLS termination and network controls.
- Externally anchored or WORM audit retention.
- Backup, restore, rollback, and disaster-recovery evidence.
- Production load, soak, latency, and resource evidence.
- Metrics exporter and alerting integration.
- SBOM, vulnerability, dependency, and container scanning gates.
- Secret-manager integration.
- Browser OIDC session integration.
- Operational runbooks and remote CI evidence.

## Implemented planning boundary

`POST /api/v1/molecular/projects/{project_identifier}/planning/evaluate`
accepts a strict `MolecularPlanningRequest` and returns a complete canonical
`MolecularPlanningResponse`. The endpoint reads a persisted project and its
content-addressed topology artifacts, projects declared evidence into generic
planning facts, and invokes the generic deterministic feasibility planner. It
does not persist a plan or create, authorize, recover, or execute a workflow.

Feasibility statuses have exact meanings:

- `feasible`: all evaluated declarations support selection.
- `conditional`: selection is possible, but declared prerequisites, approvals,
  or verifications remain unresolved.
- `unknown`: required evidence is absent or insufficient; the capability is not
  selectable.
- `infeasible`: a declared hard requirement conflicts with the request or
  evidence; the capability is not selectable.

An empty catalogue is valid and returns a deterministic unassigned plan. It is
not evidence that a scientific capability is available. Required approvals are
reported as required, never granted. Required verifications are reported as
required, never completed.

### Input and resource ceilings

The public planning request is streamed through a 2 MiB ceiling before JSON or
Pydantic domain validation can retain an unbounded body. It accepts JSON only.
The API edge additionally limits:

- 4,096 referenced systems, structures, or structural regions per collection;
- 512 objective declarations, constraints, targets, approvals, verifications,
  or policy notes per collection;
- 1,024 declared resources per collection;
- 256 exact selected or configured capabilities.

Confidence remains bounded to `[0, 1]`, non-finite values are rejected, and the
public wall-time ceiling cannot exceed 315,576,000 seconds (ten Julian years).
Generic resource quantities retain their declared units and strict scalar types;
the request-byte ceiling bounds their encoded magnitude without imposing a
molecule-specific scientific range.

Topology artifacts remain immutable and artifact-backed. Planning validates
their declared byte sizes before reads and accepts at most 128 MiB of topology
JSON across one request. This does not limit native coordinate artifacts or
Mol* rendering; a larger topology requires a future streaming fact-projection
capability instead of unbounded API-process allocation.

### Controlled errors

| HTTP status | Code | Meaning |
| --- | --- | --- |
| 404 | `molecular_project_not_found` | The configured resolver did not find the requested project. This distinction must be hidden behind authorization before production exposure. |
| 404 | `scientific_capability_not_found` | An exact requested catalogue identity is absent. |
| 409 | `molecular_planning_evidence_invalid` | Persisted project or topology evidence is missing, malformed, conflicting, non-canonical, or over its planning ceiling. |
| 413 | `molecular_planning_request_too_large` | The request exceeded the byte ceiling. |
| 415/422 | `invalid_molecular_planning_request` | Media type, JSON, schema, identity, count, or policy validation failed. |
| 503 | `molecular_planning_service_unavailable` | A configured dependency or unexpected planner operation failed safely. |

Public errors do not echo request bodies, filesystem paths, repository
exceptions, tokens, or topology contents. A failed read-only evaluation has no
partially persisted plan to recover.

### Configuration and lifecycle

Native molecular scenes and planning require both
`PULSATE_MOLECULAR_PROJECT_ROOT` and `PULSATE_MOLECULAR_ARTIFACT_ROOT`; setting
only one or setting an empty value fails configuration. Repository `start()`
validates the controlled roots and shutdown closes planning, scene, project,
run, interpretation, and experiment services in nested `finally` blocks.

The default scientific capability catalogue is deliberately empty. The current
repository has no production catalogue loader, signed catalogue source, tenant
policy, deployment manifest, service-level objective, or backup/restore
procedure. Those are blockers, not implicit development defaults.

## Validation classifications

The Phase 1-3 release gate is a frontend-independent backend-core category. Its
membership is an exact filename manifest in `tests/conftest.py`; collected items
receive both `core` and `backend_core`. The deterministic gate command also
names every file explicitly so an unrelated integration module cannot fail
during collection or enter the gate by accident:

```powershell
python -m pytest `
    tests/test_scientific_foundation.py `
    tests/test_scientific_capabilities.py `
    tests/test_scientific_resources.py `
    tests/test_scientific_planning.py `
    tests/test_scientific_feasibility.py `
    tests/test_molecular_contracts.py `
    tests/test_molecular_ingestion.py `
    tests/test_molecular_artifact_repository.py `
    tests/test_molecular_project_repository.py `
    tests/test_molecular_scene_projection.py `
    tests/test_molecular_planning.py `
    tests/test_pulsate_api.py `
    tests/test_pulsate_runs.py `
    tests/test_pulsate_experiments.py `
    tests/test_pulsate_molecular_scenes.py `
    tests/test_pulsate_molecular_planning.py `
    tests/test_pulsate_security.py `
    -m "core and backend_core" `
    --basetemp=".pytest-tmp-e1-phase1-3-core" `
    -q
```

The focused Phase 3C gate is:

```powershell
python -m pytest `
    tests/test_pulsate_molecular_planning.py `
    tests/test_scientific_planning.py `
    tests/test_scientific_feasibility.py `
    tests/test_molecular_planning.py `
    --basetemp=".pytest-tmp-e1-phase3c" `
    -q
```

Existing `quantum_unit` tests remain pure preflight tests. Provisioned
scientific, container, official SWE-agent, swerex, and pinned-checkout tests use
`quantum_integration`, `quantum_container`, `swe_agent_integration`, or
`external_infrastructure`. A provisioned environment can run the classified
integration gate explicitly:

```powershell
python -m pytest `
    tests/test_quantum_preflight_integration.py `
    tests/test_pulsate_approved_execution_integration.py `
    tests/test_pulsate_runs_integration.py `
    tests/test_quantum_model_provider.py `
    tests/test_quantum_tool_templates.py `
    tests/test_swe_agent_qwen_compat.py `
    -m "quantum_integration or quantum_container or swe_agent_integration or external_infrastructure" `
    -q
```

The separation does not ignore integration failures or redefine unit failures.
It makes their infrastructure preconditions explicit while keeping them
executable in their pinned environment. Tests that use fakes or task-owned
fixtures remain ordinary unit tests even when they share a module with a
provisioned integration test.

Frontend validation uses the lockfile and the repository's own tools:

```powershell
Set-Location frontend
npm ci
npm test -- --run src/api/client.test.ts src/hooks/useExistingRun.test.tsx src/hooks/useMolecularProjectScene.test.tsx src/components/MolecularPlanningPanel.test.tsx src/hooks/useMolecularPlanning.test.tsx src/App.test.tsx
npm test -- --run
npm run lint
npx tsc --noEmit -p tsconfig.app.json
npx tsc --noEmit -p tsconfig.node.json
npm run build -- --outDir <task-owned-output-directory>
```

`.github/workflows/ci.yml` now implements the backend-core and frontend gates.
It installs backend runtime/test dependencies from the existing hash-pinned
locks, installs the repository itself without dependency resolution, uses Ruff,
uses `npm ci`, and never configures IBM credentials or invokes external
scientific engines. Provisioned quantum/SWE-agent integration remains a
separate command because its immutable images, checkout, executable, and Docker
boundary are not safely available on a generic hosted runner.

## Control results

| Control category | Status | Evidence | Concrete defect or blocker | Required remediation |
| --- | --- | --- | --- | --- |
| 1. Architecture and dependency boundaries | PASS | Source dependency audit; mechanical AST import-boundary regression; Phase 3 API delegates to `cgr.molecular.planning` and `cgr.science.feasibility`. | None found in implemented scope. Phase 4 graph execution is absent. | Keep the boundary regression in the required backend suite. |
| 2. Contract stability and compatibility | PASS | Frozen extra-forbid canonical models, explicit versions, canonical UTF-8 JSON, stable SHA-256 identities, non-finite rejection, deterministic ordering, OpenAPI request/response schemas, and the 507-test backend-core gate. | None found in implemented Phase 1–3 scope. | Retain the classified gate in required CI. |
| 3. Input and abuse resistance | PASS | Bounded streaming body, collection/catalogue ceilings, bounded topology reads, strict identifiers/text/numbers, path-free typed errors, and executed oversize/malformed/media-type regressions. | None found in implemented Phase 1–3 scope. | Add production-like ASGI abuse testing when a deployment environment exists. |
| 4. Authentication and authorization | BLOCKED | E2 adds versioned principals/security contexts, injectable bearer authentication, centralized tenant/grant authorization, exact route coverage, enumeration-safe denials, and OpenAPI security declarations. | No external OIDC verifier, production identity-provider configuration, or durable production grant repository is provisioned. | Add startup-loaded/cached OIDC cryptographic verification, production grant persistence, key rotation, and deployment acceptance. |
| 5. Read-only and side-effect guarantees | PASS | Persisted project/artifact snapshots, repeated-response equality, no-execution spies, GET-only scene access, and absence of planner persistence APIs all ran in the backend-core gate. | None found in implemented Phase 1–3 scope. | Retain repository/run/store before-and-after checks in required CI. |
| 6. Integrity and provenance | PASS | Content hashes, canonical topology bytes, exact structure/topology identity and counts, immutable pointers, lineage, project/objective/resource/capability/evidence fingerprints, distinct structural/electronic-region contracts, and tamper regressions all ran. | None found in implemented Phase 1–3 scope. | Retain tamper and atomic-publication regressions in required CI. |
| 7. Concurrency and lifecycle | BLOCKED | `RLock`-guarded service lifecycle, nested application shutdown, context-managed clients, explicit repository closure, deterministic concurrency regressions, and ten repeated Windows publication runs. | Production-duration ASGI concurrency, Linux parity, and handle-leak evidence remain absent. | Add sustained Windows/Linux ASGI concurrency and handle-leak evidence. |
| 8. Failure recovery | BLOCKED | Typed missing/corrupt evidence failures, controlled resolver/startup/unexpected-planner errors, fail-then-success regression, atomic temporary cleanup, and path-free internal publication diagnostics all ran. | Repository backup/restore and corruption-isolation operations remain unspecified. | Define and validate immutable repository backup, restore, and corruption isolation procedures. |
| 9. Observability and auditability | BLOCKED | E2 adds correlation/request identity, bounded JSON events, canonical append-oriented audit records, mandatory write-audit failure policy, and bounded-cardinality in-process metrics. | No durable tamper-evident audit backend, metrics exporter/alerting, production retention, or production operations evidence exists. | Provision and validate durable audit storage, metrics export, alerts, retention, access controls, and recovery. |
| 10. Performance and scalability | BLOCKED | Local deterministic benchmark documented below; repeated fact fingerprints are now computed once per plan. | No production load, memory, latency percentile, large-topology streaming, or service-level evidence exists. | Establish supported project/catalogue profiles, memory budgets, percentile targets, load tests, and streaming fact projection for topologies above the API-process ceiling. |
| 11. Frontend reliability and accessibility | BLOCKED | Native labelled controls, keyboard-operable HTML, visible focus, textual status labels, loading/error/empty/no-assignment states, expandable findings, read-only execution notice, Mol* remains mounted. ESLint and both TypeScript checks passed. | Vitest and the Vite production stage were blocked by transient-file sandbox denial. No manual assistive-technology evidence exists. | Complete package validation and perform keyboard/screen-reader/zoom acceptance against production assets. |
| 12. Configuration and deployment safety | BLOCKED | Paired root validation, controlled repository roots, empty safe catalogue default, no hard-coded Windows production path, no planning credentials, and real Phase 1-3 backend/frontend CI jobs. | Production catalogue/deployment/runtime policy, backup, rollback, and disaster recovery remain absent; the new CI workflow has not yet completed remotely. | Require the CI gate, define the production configuration schema, deployment manifests, health/readiness checks, backup/restore, rollback, and environment promotion evidence. |
| 13. Security and dependency evidence | BLOCKED | JSON/Pydantic only, no planning subprocess, content-addressed repository paths, React escaping, no raw HTML rendering, secret-key rejection in portable metadata and public responses. | No installed offline vulnerability scanner, SBOM/provenance gate, or current vulnerability report is available. | Add pinned offline-capable Python/npm vulnerability and SBOM checks, dependency provenance policy, and remediation SLA. |
| 14. Documentation and operability | PASS | This record documents boundaries, endpoint semantics, status meanings, empty catalogue, errors, limits, configuration, lifecycle, non-execution, limitations, blockers, and gate decision. | No in-scope documentation gap remains for the checkpoint itself. | Keep this record versioned and update it only from a completed gate run. |

## Gate execution evidence

The inherited pre-hardening evidence remains useful history but is not treated
as post-change validation.

- Enterprise Foundation E1 separates deterministic Phase 1-3 core validation
  from tests that require pinned quantum/SWE-agent/container infrastructure.
  The latter remain collected and runnable under explicit registered markers;
  their failures are not suppressed or counted as core successes.

- The focused Phase 3C backend gate completed with 89 passed tests before the
  publication repair.
- The final Phase 1–3 backend-core gate completed with 507 passed and 25 skipped
  tests after the publication repair.
- The final focused artifact/project/scene repository gate completed with 141
  passed and 12 skipped tests.
- Ten additional focused publication runs completed successfully with the same
  141 passed and 12 skipped result in each run.
- Ruff completed successfully across every Python file changed by Phase 3C,
  E1 classification, and the publication correction.
- Focused Vitest could not load its configuration because the sandbox denied
  creation of Vite's transient timestamp module. No Vitest result is claimed.
- `npm run lint` completed successfully.
- Application and Node TypeScript checks both completed with exit code zero.
- The production build completed both TypeScript stages, then Vite failed at
  transient configuration-module creation with `EPERM`; no production-build
  success is claimed.
- The uncommitted E2 focused backend gate completed with 70 passed tests after
  two narrow pre-execution corrections: the direct audit test now supplies the
  full Bearer header and resource authorization accepts the repository's
  bounded namespaced identifier grammar rather than the correlation-ID grammar.
- The final classified Phase 1-3 plus E2 backend-core gate completed with 527
  passed and 25 skipped tests. Ruff passed across every Python file changed by
  E2.
- Targeted authentication/API/workspace Vitest completed with 78 passed tests;
  the complete frontend suite completed with 164 passed tests. ESLint and both
  application and Node TypeScript checks passed.
- The E2 production Vite build completed after transforming 1,209 modules into
  a task-owned external output directory. Vite reported the existing large
  JavaScript chunk warning (2,885.87 kB, 819.69 kB gzip), which remains
  performance evidence to address before an enterprise-readiness decision.
- `git diff --check` passed after the final implementation correction.
- `git diff --check` completed successfully for tracked changes. New-file
  whitespace was checked separately because unstaged untracked files are not
  included by ordinary `git diff --check`.

## E1 Windows atomic-publication correction

An environment-gated test diagnostic reproduced the intermittent failure on
the seventh diagnostic run. Windows returned `PermissionError`, `errno=13`,
and `winerror=5` (`ERROR_ACCESS_DENIED`) while renaming a fully written
temporary directory. The source still existed, the destination did not exist,
both expected child files were present, and all repository-owned writer handles
had closed. A later stability attempt reproduced the same `WinError 5` in a
plain test-setup `Path.rename`, outside repository publication. This rules out
an open repository file/directory handle and demonstrates transient Windows
filesystem/filter contention; the external process holding the transient
access restriction is not observable from the repository process.

Artifact and project repositories now use one shared atomic no-replace
primitive. Linux retains `renameat2(RENAME_NOREPLACE)`. Windows retains the
documented no-replace `os.rename` operation and permits exactly three total
attempts only for the observed `WinError 5`. Before each retry it requires the
source to remain a normal directory and the destination to remain absent. A
new destination causes an immediate `FileExistsError`; an unsafe or missing
source, any other Windows error, or exhaustion fails closed immediately. There
is no sleep, copy/delete fallback, overwrite flag, or broad retry loop.

Final failures retain the existing path-free public repository messages and
chain a bounded path-free internal diagnostic containing only exception type,
`errno`, `winerror`, existence state, expected temporary child basenames, and
the closed-writer state. Tests cover retry success, exhaustion, non-observed
error rejection, destination-race refusal, idempotency, conflicts, repeated
independent publications, concurrent calls, temporary cleanup, partial-output
absence, and public-message path safety.

## Local performance evidence

Environment: bundled CPython 3.12.13 on Windows, local process, no network or
scientific engines. Inputs were deterministic scalar planning facts and
declarative capabilities. These measurements are diagnostic evidence, not an
SLA.

| Facts | Capabilities | Repeats | Before (seconds/request) | After (seconds/request) |
| ---: | ---: | ---: | ---: | ---: |
| 20 | 4 | 5 | 0.001933 | 0.001074 |
| 1,000 | 32 | 2 | 0.152752 | 0.017324 |
| 5,000 | 64 | 1 | 1.504050 | 0.096369 |

Every repeated result had the same fingerprint. The correction reuses the
objective, constraints, fact-set, and resource fingerprints within one plan;
it does not cache across requests or change canonical identities.

## Non-execution and known limitations

Planning evaluation cannot create a run, grant approval, complete
verification, invoke Qiskit/PySCF/RDKit/OpenMM, contact IBM, or submit work.
The completed legacy execution loop remains separate and unchanged.

This gate does not claim production authentication, tenant isolation,
operational monitoring, disaster recovery, vulnerability clearance,
production scalability, or enterprise readiness. Phase 4 workflow graphs,
scientific-engine adapters, candidate generation, and discovery loops remain
outside this checkpoint.
