[CmdletBinding()]
param(
  [string]$Image = "cgr-quantum-preflight:1.0.0",
  [string]$ResultRoot = ""
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ResultRoot) { $ResultRoot = Join-Path $repoRoot "quantum-preflight-results" }
$manifest = (Resolve-Path (Join-Path $repoRoot "benchmark-manifests/quantum-preflight/lih-ground-state-v1.json")).Path
$lock = (Resolve-Path (Join-Path $repoRoot "requirements/quantum-preflight.lock")).Path
$output = [System.IO.Path]::GetFullPath($ResultRoot)
[System.IO.Directory]::CreateDirectory($output) | Out-Null
$imageId = docker image inspect --format '{{.Id}}' $Image
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$lockHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $lock).Hash.ToLowerInvariant()
Write-Output "image_identifier=$imageId"
Write-Output "lock_sha256=$lockHash"
docker run --rm `
  --network none --read-only --cpus 2 --memory 4g --pids-limit 256 --stop-timeout 10 `
  --security-opt no-new-privileges --cap-drop ALL --tmpfs /tmp:rw,nosuid,nodev,size=512m `
  --mount "type=bind,src=$manifest,dst=/input/manifest.json,readonly" `
  --mount "type=bind,src=$lock,dst=/input/quantum-preflight.lock,readonly" `
  --mount "type=bind,src=$output,dst=/output" `
  --env "CGR_QUANTUM_IMAGE_ID=$imageId" `
  $Image --manifest /input/manifest.json --lock-file /input/quantum-preflight.lock `
  --result-root /output --max-seconds 180
$exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
  Get-ChildItem -LiteralPath (Join-Path $output "lih-ground-state-v1") -Filter receipt.json -Recurse |
    Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
}
exit $exitCode
