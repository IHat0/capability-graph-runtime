# Pulsate enterprise-readiness evidence v1

`cgr-pulsate-enterprise-gate` loads canonical same-commit evidence, verifies
referenced artifact hashes, and rejects stale, duplicate, contradictory,
malformed, or unexpected evidence. `PASS` means the operation ran, exited zero,
and skipped no mandatory check. `FAIL` means it ran nonzero. `BLOCKED` means it
could not run, or an explicitly registered policy check returned its real
unavailable status. `PENDING` means required evidence
has not completed. Missing evidence is pending; precedence is fail, blocked,
pending, then pass. Evidence is integrity-checked, not tamper-proof.

Local gates are compile, Ruff 0.15.20, focused E5-E7 and enterprise-core
pytest, complete Vitest, ESLint, both TypeScript checks, Vite build, and
`git diff --check`. Docker, scanning, recovery-drill, and performance evidence
remain separate and cannot be replaced by static review.

`.github/workflows/enterprise-readiness.yml` depends on existing CI,
deployment/recovery, and supply-chain workflows, downloads same-run artifacts,
and runs the gate for `${GITHUB_SHA}`. Missing evidence is nonzero; there is no
hard-coded PASS.

## Canonical evidence production

`cgr-pulsate-evidence-run` is the only workflow evidence writer. It validates
the gate and registered workflow producer, records a real UTC interval,
executes the supplied argv directly with `shell=False`, inherits stdout and
stderr, and records the child return code. Exit zero is `PASS`; a nonzero child
is `FAIL` except for registered exit 78 when an approved performance threshold
does not exist, which is `BLOCKED`. A missing executable is also `BLOCKED`.
The runner never emits `PENDING` and cannot emit `PASS` without a child command.

Declared reports are mandatory unless explicitly marked optional. They are
checked after execution, parsed when JSON, SARIF, XML, or JUnit, bounded to one
GiB, and recorded with actual byte length and SHA-256. Missing or malformed
mandatory reports make the gate fail. Records are canonical, fingerprinted,
and exclusively created under a validated task-owned `records/` directory.
Existing outputs, traversal, symlinks, junctions, reparse points, special files,
and unknown producers are rejected. Command argv and environment values are
not copied into records.

## Authoritative producer map

The 67 mandatory gates are derived from `ENTERPRISE_GATE_PRODUCERS`; there is
no independent gate list.

| Workflow job | Actual command families | Mandatory evidence |
| --- | --- | --- |
| `ci.yml:backend-evidence` | Focused pytest with JUnit | audit, authorization, catalogue, durable security, molecular integrity/publication/concurrency, planning, JWT/JWKS, readiness-failure, scientific-contract, redaction, tenant, and unassigned-plan gates |
| `ci.yml:frontend-evidence` | Vitest, ESLint, both TypeScript checks, Vite | all frontend and molecular-workspace gates |
| `supply-chain-security.yml:dependency-consistency` | Hashed installs, `npm ci`, `pip check`, final lock diff | lock consistency |
| `supply-chain-security.yml:sbom-and-licenses` | CycloneDX/license tools and JSON validation | Python/Node SBOM and license gates |
| `supply-chain-security.yml:vulnerability-and-static-security` | Pinned pip-audit, npm audit, Bandit, Gitleaks, Trivy config | vulnerability, secret, and static-security gates |
| `supply-chain-security.yml:container-supply-chain` | Pinned Syft and Trivy against the current image and image ID | container SBOM and vulnerability gates |
| `enterprise-readiness.yml:deployment-and-recovery` | Docker/Compose, authenticated smoke, and focused recovery operations | authentication, deployment, probe, recovery, lifecycle, concurrency, and performance gates |

Every invocation names its gate and producer. The runner rejects mismatches and
the aggregator rechecks producer and operation identity. Reusable CI and
supply-chain jobs upload uniquely named bundles to the same top-level workflow
run. The final job uses `if: always()`, downloads only
`enterprise-evidence-*` bundles from that run, detects duplicates, verifies the
source commit and artifact hashes, and invokes the installed
`cgr-pulsate-enterprise-gate`. Its nonzero status remains authoritative;
`if: always()` is used only to preserve evidence and the decision artifact.

Current decision: `PENDING_REMOTE_EVIDENCE`.

This is not enterprise readiness. Container/runtime, current vulnerability
databases, SBOMs, scans, licenses, recovery drill, repeated lifecycle/recovery,
and justified performance evidence have not completed for this uncommitted
implementation. Without a justified threshold, performance acceptance is
`BLOCKED`, not an invented SLA.
