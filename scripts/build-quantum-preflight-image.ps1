[CmdletBinding()]
param([string]$Image = "cgr-quantum-preflight:1.0.0")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
docker build --pull --file (Join-Path $repoRoot "docker/quantum-preflight/Dockerfile") --tag $Image $repoRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker image inspect --format '{{.Id}}' $Image
exit $LASTEXITCODE
