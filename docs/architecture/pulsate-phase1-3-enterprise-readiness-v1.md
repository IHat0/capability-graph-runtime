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
npm test -- --run src/api/client.test.ts src/components/MolecularPlanningPanel.test.tsx src/hooks/useMolecularPlanning.test.tsx src/App.test.tsx
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
| 4. Authentication and authorization | BLOCKED | No FastAPI authentication dependency, principal, tenant, or project/artifact authorization policy exists. | A project identifier currently implies read/planning access and 404 responses permit project enumeration. | Add an external-identity-backed principal and authorize project, artifact, scene, and planning access before resolution or not-found disclosure. |
| 5. Read-only and side-effect guarantees | PASS | Persisted project/artifact snapshots, repeated-response equality, no-execution spies, GET-only scene access, and absence of planner persistence APIs all ran in the backend-core gate. | None found in implemented Phase 1–3 scope. | Retain repository/run/store before-and-after checks in required CI. |
| 6. Integrity and provenance | PASS | Content hashes, canonical topology bytes, exact structure/topology identity and counts, immutable pointers, lineage, project/objective/resource/capability/evidence fingerprints, distinct structural/electronic-region contracts, and tamper regressions all ran. | None found in implemented Phase 1–3 scope. | Retain tamper and atomic-publication regressions in required CI. |
| 7. Concurrency and lifecycle | BLOCKED | `RLock`-guarded service lifecycle, nested application shutdown, context-managed clients, explicit repository closure, deterministic concurrency regressions, and ten repeated Windows publication runs. | Production-duration ASGI concurrency, Linux parity, and handle-leak evidence remain absent. | Add sustained Windows/Linux ASGI concurrency and handle-leak evidence. |
| 8. Failure recovery | BLOCKED | Typed missing/corrupt evidence failures, controlled resolver/startup/unexpected-planner errors, fail-then-success regression, atomic temporary cleanup, and path-free internal publication diagnostics all ran. | Repository backup/restore and corruption-isolation operations remain unspecified. | Define and validate immutable repository backup, restore, and corruption isolation procedures. |
| 9. Observability and auditability | BLOCKED | Deterministic project/objective/plan/fact fingerprints can act as safe correlation identities. | The Pulsate API has no operational logging/metrics foundation for request identity, safe project identity, duration, result counts, failure category, or lifecycle failures. | Add centrally configured structured audit logging and metrics with redaction and retention policy; do not log molecular payloads or secrets. |
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
