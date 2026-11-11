<#
.SYNOPSIS
    Build a portable Clio image and package it for a machine that cannot
    build one (air gap, customer site, appliance). Windows counterpart of
    scripts/package_image.sh — same output, same instructions.

.EXAMPLE
    .\scripts\package_image.ps1
    .\scripts\package_image.ps1 -OfflineBundle -Tag 1.4.0 -Out D:\transfer
    .\scripts\package_image.ps1 -Slim -WithProxyImage -Tag 1.4.0 -Out D:\transfer
    .\scripts\package_image.ps1 -EmbeddingModel BAAI/bge-small-en-v1.5 -OfflineBundle -Tag 1.4.0-bge
    (--embedding-model works too, spelled as in package_image.sh)
#>
[CmdletBinding()]
param(
    [string]$Tag = "local",
    [string]$Out = "dist-image",
    [switch]$OfflineBundle,
    [switch]$WithDocling,
    # 1.5 GB: no PyTorch, OCR or audio.
    [switch]$Slim,
    # Also carry nginx, for a host with no registry.
    [switch]$WithProxyImage,
    [string]$WhisperModel = "base",
    # The model the image bakes and a container uses unless EMBEDDING_MODEL is
    # set at run time. With -OfflineBundle the prefetch fetches it too. Keep the
    # default in step with the Dockerfile's ARG EMBEDDING_MODEL and config.py.
    [Alias("embedding-model")]
    [string]$EmbeddingModel = "all-MiniLM-L6-v2",
    # Build for the target's architecture when it differs from this machine's.
    [string]$Platform = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$image = "clio:$Tag"
$vcsRef = "unknown"
try { $vcsRef = (git rev-parse --short HEAD).Trim() } catch { }

$offline = "0"; if ($OfflineBundle) { $offline = "1" }
$docling = "0"; if ($WithDocling) { $docling = "1" }
$slimArg = "0"; if ($Slim) { $slimArg = "1" }
$proxyIncluded = "0"; if ($WithProxyImage) { $proxyIncluded = "1" }
# Keep in step with the default in docker-compose.proxy.yml.
$proxyImage = "nginx:1.29-alpine"

if (-not $EmbeddingModel.Trim()) { throw "-EmbeddingModel needs a model id" }

Write-Host "==> Building $image (slim=$slimArg, offline_bundle=$offline, docling=$docling, embedding_model=$EmbeddingModel)"
$buildArgs = @(
    "build",
    "--build-arg", "OFFLINE_BUNDLE=$offline",
    "--build-arg", "WITH_DOCLING=$docling",
    "--build-arg", "EMBEDDING_MODEL=$EmbeddingModel",
    "--build-arg", "SLIM=$slimArg",
    "--build-arg", "WHISPER_MODEL=$WhisperModel",
    "--build-arg", "APP_VERSION=$Tag",
    "--build-arg", "VCS_REF=$vcsRef"
)
if ($Platform) { $buildArgs += @("--platform", $Platform) }
$iidFile = [System.IO.Path]::GetTempFileName()
$buildArgs += @("--iidfile", $iidFile, "-t", $image, ".")
& docker @buildArgs
if ($LASTEXITCODE -ne 0) { Remove-Item $iidFile -ErrorAction SilentlyContinue; throw "docker build failed" }

# Save only the image this run built. Another build of the same tag (one whose
# client was stopped keeps running in BuildKit) can finish later and take the
# tag over, and the bundle would then carry that image under this README.
$builtId = (Get-Content $iidFile -Raw).Trim()
Remove-Item $iidFile -ErrorAction SilentlyContinue
$taggedId = (& docker image inspect -f "{{.Id}}" $image).Trim()
if ($taggedId -ne $builtId) { throw "$image no longer points at the image this run built (another build re-tagged it?). Not saving." }

if (-not (Test-Path $Out)) { New-Item -ItemType Directory -Path $Out | Out-Null }

# `docker save -o` rather than a pipeline: PowerShell pipes text, and would
# corrupt the tar stream on its way to a file.
function Save-Image([string]$Name, [string]$Base) {
    $saved = Join-Path $Out "$Base.tar"
    $zipped = "$saved.gz"
    Write-Host "==> Saving $Name"
    & docker save -o $saved $Name
    if ($LASTEXITCODE -ne 0) { throw "docker save failed" }

    Write-Host "==> Compressing"
    $in = [System.IO.File]::OpenRead($saved)
    $outStream = [System.IO.File]::Create($zipped)
    $gzip = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionMode]::Compress)
    try { $in.CopyTo($gzip) } finally { $gzip.Dispose(); $outStream.Dispose(); $in.Dispose() }
    Remove-Item $saved

    Write-Host "==> Checksumming"
    $hash = (Get-FileHash $zipped -Algorithm SHA256).Hash.ToLower()
    # sha256sum format, so the receiving Linux host can verify with `sha256sum -c`.
    # Not Out-File: its CRLF becomes part of the filename there, and the check fails.
    [System.IO.File]::WriteAllText((Join-Path (Resolve-Path $Out) "$Base.tar.gz.sha256"), "$hash  $Base.tar.gz`n", [System.Text.Encoding]::ASCII)
}

Save-Image $image "clio-$Tag"

if ($WithProxyImage) {
    $pullArgs = @("pull")
    if ($Platform) { $pullArgs += @("--platform", $Platform) }
    & docker @pullArgs $proxyImage
    if ($LASTEXITCODE -ne 0) { throw "docker pull failed" }
    Save-Image $proxyImage "proxy-image"
}

Write-Host "==> Copying deployment files"
foreach ($entry in Get-Content scripts\bundle_files.txt) {
    $entry = $entry.Trim()
    if (-not $entry -or $entry.StartsWith("#")) { continue }
    $target = Join-Path $Out $entry
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item $entry $target
}
New-Item -ItemType Directory -Force -Path (Join-Path $Out "certs\tls"), (Join-Path $Out "certs\ca") | Out-Null

$built = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
@"
Clio $Tag - offline install bundle
built $built from $vcsRef
slim: $slimArg   offline model bundle: $offline   docling OCR: $docling
embedding model: $EmbeddingModel
proxy image included: $proxyIncluded

The embedding model above is the one this image uses when EMBEDDING_MODEL is
not set; leave it unset in .env to use it. Collections indexed earlier keep the
model they were built with (docs/AIRGAP.md: "Changing the embedding model").
Check a loaded image with:
  docker inspect -f '{{ index .Config.Labels "dev.clio.embedding-model" }}' clio:$Tag

Follow docs/ENTERPRISE.md. In this bundle the image is clio:$Tag.
An agent doing this for you should read skills/clio-deploy/SKILL.md first.

No firewall or TLS proxy to think about? The short way:

  sha256sum -c clio-$Tag.tar.gz.sha256
  docker load < clio-$Tag.tar.gz
  cp .env.onprem.example .env      # then edit: model endpoint, AUTH_PASSWORD
  # set CLIO_IMAGE=clio:$Tag in .env
  docker compose -f docker-compose.onprem.yml up -d

Then open http://<host>:8473. See docs/ONPREM.md (air gap: docs/AIRGAP.md).
"@ | Out-File -FilePath (Join-Path $Out "README.txt") -Encoding utf8

Write-Host ""
Write-Host "Bundle ready in ${Out}:"
Get-ChildItem $Out | Select-Object Name, Length
