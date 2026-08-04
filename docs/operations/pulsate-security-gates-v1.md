# Pulsate supply-chain and security gates v1

`.github/workflows/supply-chain-security.yml` implements the connected-CI
gates. A workflow definition is not evidence; before a reviewed-commit run,
every remote result is `PENDING`.

Mandatory jobs verify hash-locked Python installation, editable compatibility,
`pip check`, `npm ci`, and unchanged locks. They generate real CycloneDX Python,
Node, and container SBOMs plus Python/Node license inventories. They run
`pip-audit` 2.9.0, `npm audit`, Bandit 1.8.6, Gitleaks 8.24.2, Syft 1.18.1, and
Trivy 0.58.2 against the actual dependency graphs, source/history, or exact
built image. Actual reports and hashes are uploaded. Mandatory scans do not use
`continue-on-error`; tool/database/build failure is never called clean.

Connected CI executes each mandatory scanner through
`cgr-pulsate-evidence-run`. The scanner remains the child process, its actual
exit status remains authoritative, and its JSON report is mandatory evidence.
A missing binary, database-update failure, missing/malformed report, or hash
mismatch cannot become clean. Container records include the locally inspected
image ID while Syft and Trivy operate on the image tag built for the same
`${GITHUB_SHA}`. `if: always()` uploads preserve genuine failure evidence; they
do not alter scanner or job status.

There is no blanket ignore policy. An exception must name its advisory,
package/version and scope, owner, expiry, justification, and compensating
control. Unknown/incompatible licenses require the same explicit review.

Local scans run only if exact tools and current data already exist without an
unapproved install/network operation. Otherwise local scanning is `BLOCKED` and
connected CI stays `PENDING`. Ruff 0.15.20 is quality evidence, not a substitute
for a security scanner.
