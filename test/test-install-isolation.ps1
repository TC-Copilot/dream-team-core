$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tempRoot = Join-Path $env:TEMP ('daily-flow-install-isolation-' + [guid]::NewGuid().ToString('N'))
$oldProfile = $env:USERPROFILE
$oldHome = $env:HOME

try {
  $profile = Join-Path $tempRoot 'profile'
  $skillRoot = Join-Path $profile '.copilot\m-skills'
  $pointerRoot = Join-Path $skillRoot 'daily-flow-setup'
  $decoy = Join-Path $tempRoot 'decoy-install'
  $target = Join-Path $tempRoot 'explicit-target'
  New-Item -ItemType Directory -Force -Path $pointerRoot,(Join-Path $decoy 'app') | Out-Null
  Set-Content -LiteralPath (Join-Path $profile '.copilot\config.json') -Value '{}' -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $pointerRoot '.install-location') -Value $decoy -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $decoy 'app\config.json') -Value '{ invalid json' -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $decoy 'app\sentinel.txt') -Value 'untouched' -Encoding ASCII

  $env:USERPROFILE = $profile
  $env:HOME = $profile
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'install.ps1') `
    -InstallDir $target -NoBrowser
  if ($LASTEXITCODE -ne 0) { throw "Explicit isolated install exited $LASTEXITCODE." }
  if (-not (Test-Path (Join-Path $target 'app\app.py'))) {
    throw 'Explicit install target was not populated.'
  }
  if ((Get-Content -LiteralPath (Join-Path $decoy 'app\sentinel.txt') -Raw).Trim() -ne 'untouched') {
    throw 'Installer modified the pointer-selected decoy despite an explicit InstallDir.'
  }
  if ((Get-Content -LiteralPath (Join-Path $decoy 'app\config.json') -Raw).Trim() -ne '{ invalid json') {
    throw 'Installer read or rewrote the pointer-selected decoy configuration.'
  }
  Write-Host '[PASS] explicit InstallDir isolates discovery and lifecycle from global Scout pointers'
} finally {
  $env:USERPROFILE = $oldProfile
  $env:HOME = $oldHome
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
