# Refresh an installed Daily Flow package from its stable GitHub release.
param(
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [string]$Repository = 'TC-Copilot/dream-team-core'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Stop-Refresh([string]$Message) {
  throw "Release refresh stopped: $Message"
}

function Read-JsonFile([string]$Path, [string]$Label) {
  try {
    return Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch {
    Stop-Refresh "$Label is unreadable: $($_.Exception.Message)"
  }
}

$configPath = Join-Path $InstallDir 'app\config.json'
$config = Read-JsonFile $configPath 'Installed config.json'
if (-not $config.port) { Stop-Refresh 'Installed config.json has no port.' }
$port = [int]$config.port
$healthUri = "http://127.0.0.1:$port/api/health"
try {
  $before = Invoke-RestMethod -Uri $healthUri -TimeoutSec 4
} catch {
  Stop-Refresh "the installed app is not healthy at $healthUri."
}
$installedVersion = ([string]$before.version).Trim()
if ($installedVersion -notmatch '^\d+\.\d+\.\d+$') {
  Stop-Refresh "the running app reported invalid semantic version '$installedVersion'."
}
$installedBuild = ([string]$before.buildRevision).Trim()

try {
  $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest" -TimeoutSec 20
} catch {
  Stop-Refresh "the stable release metadata could not be fetched: $($_.Exception.Message)"
}
$releaseVersion = ([string]$release.tag_name).Trim() -replace '^v', ''
if ($releaseVersion -notmatch '^\d+\.\d+\.\d+$') {
  Stop-Refresh "the latest release tag '$($release.tag_name)' is not a stable semantic version."
}
if ([version]$releaseVersion -lt [version]$installedVersion) {
  Stop-Refresh "the published stable release v$releaseVersion is older than installed v$installedVersion; downgrade refused."
}

$assetName = "dream-team-core-v$releaseVersion.zip"
$assets = @($release.assets | Where-Object { $_.name -eq $assetName })
if ($assets.Count -ne 1) {
  Stop-Refresh "expected exactly one release asset named $assetName, found $($assets.Count)."
}
$asset = $assets[0]
$publishedDigest = ([string]$asset.digest).Trim().ToLowerInvariant()
if ($publishedDigest -and $publishedDigest -notmatch '^sha256:[0-9a-f]{64}$') {
  Stop-Refresh "release asset $assetName has an unsupported published digest."
}

$tempRoot = Join-Path $env:TEMP ('daily-flow-refresh-' + [guid]::NewGuid().ToString('N'))
$zipPath = Join-Path $tempRoot $assetName
$extractRoot = Join-Path $tempRoot 'package'
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -TimeoutSec 120
  $assetSha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($publishedDigest -and $publishedDigest -ne "sha256:$assetSha256") {
    Stop-Refresh "downloaded asset SHA-256 does not match the digest published by GitHub."
  }

  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force
  $manifests = @(Get-ChildItem -LiteralPath $extractRoot -Filter manifest.json -File -Recurse)
  if ($manifests.Count -ne 1) {
    Stop-Refresh "expected one manifest.json in $assetName, found $($manifests.Count)."
  }
  $packageRoot = Split-Path -Parent $manifests[0].FullName
  $manifest = Read-JsonFile $manifests[0].FullName 'Downloaded manifest.json'
  $packageVersion = ([string]$manifest.version).Trim()
  $packageBuild = ([string]$manifest.buildRevision).Trim()
  if ($packageVersion -ne $releaseVersion) {
    Stop-Refresh "asset manifest version '$packageVersion' does not match stable tag '$releaseVersion'."
  }
  if ($packageBuild -notmatch '^[0-9]{8}\.[0-9]+$') {
    Stop-Refresh "asset manifest buildRevision '$packageBuild' is invalid."
  }
  foreach ($required in @('install.ps1', 'compatibility.ps1', 'app\app.py', 'skills\daily-flow-setup\SKILL.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $required) -PathType Leaf)) {
      Stop-Refresh "asset is missing required file $required."
    }
  }

  $fingerprintPath = Join-Path $InstallDir 'app\.installed-release-asset.sha256'
  $installedFingerprint = ''
  if (Test-Path -LiteralPath $fingerprintPath -PathType Leaf) {
    $installedFingerprint = ([string](Get-Content -LiteralPath $fingerprintPath -Raw)).Trim().ToLowerInvariant()
  }
  $semanticUpdate = [version]$releaseVersion -gt [version]$installedVersion
  $sameReleaseRefresh = $releaseVersion -eq $installedVersion -and (
    $packageBuild -ne $installedBuild -or $assetSha256 -ne $installedFingerprint
  )
  if (-not $semanticUpdate -and -not $sameReleaseRefresh) {
    Set-Content -LiteralPath $fingerprintPath -Value $assetSha256 -Encoding ASCII
    Write-Host "Daily Flow v$installedVersion build $installedBuild already matches the published stable asset."
    exit 0
  }

  & powershell -ExecutionPolicy Bypass -File (Join-Path $packageRoot 'install.ps1') `
    -Auto -AgentInline -NoBrowser -InstallDir $InstallDir
  if ($LASTEXITCODE -ne 0) {
    Stop-Refresh "install.ps1 exited with code $LASTEXITCODE. See $(Join-Path $InstallDir 'install.log')."
  }

  try {
    $after = Invoke-RestMethod -Uri $healthUri -TimeoutSec 4
  } catch {
    Stop-Refresh "the refreshed app did not answer at $healthUri."
  }
  if (([string]$after.version).Trim() -ne $packageVersion -or
      ([string]$after.buildRevision).Trim() -ne $packageBuild) {
    Stop-Refresh "the restarted app reported v$($after.version) build $($after.buildRevision), expected v$packageVersion build $packageBuild."
  }
  Set-Content -LiteralPath $fingerprintPath -Value $assetSha256 -Encoding ASCII
  Write-Host "Refreshed Daily Flow v$installedVersion build $installedBuild -> v$packageVersion build $packageBuild."
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
