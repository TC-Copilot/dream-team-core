# Daily Flow Team - app launcher
# Author: Shervin Shaffie
# Starts the local Daily Flow app (Python standard library only) and opens the dashboard.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root 'daily-flow-app.pid'
. (Join-Path $Root 'app-lifecycle.ps1')

$Port = 8787
$cfgPath = Join-Path $Root 'config.json'
if (Test-Path $cfgPath) {
  try { $cfg = Get-Content -Raw $cfgPath | ConvertFrom-Json; if ($cfg.port) { $Port = [int]$cfg.port } } catch {}
}
$Url = "http://127.0.0.1:$Port/"
$ExpectedVersion = ''
$versionFile = Join-Path $Root '.installed-version'
if (Test-Path $versionFile) { $ExpectedVersion = ([string](Get-Content -LiteralPath $versionFile -Raw)).Trim() }
$ExpectedBuildRevision = ''
$buildRevisionFile = Join-Path $Root '.installed-build-revision'
if (Test-Path $buildRevisionFile) {
  $ExpectedBuildRevision = ([string](Get-Content -LiteralPath $buildRevisionFile -Raw)).Trim()
}

function Get-AppHealth {
  return Get-DailyFlowHealth -Port $Port -ExpectedVersion $ExpectedVersion `
    -ExpectedBuildRevision $ExpectedBuildRevision
}

$appHealth = Get-AppHealth
if (-not $appHealth) {
  $pidOnPort = Get-PortOwningProcessId -Port $Port
  if ($pidOnPort) {
    $stopped = Stop-DailyFlowAppOnPort -Port $Port -AppRoot $Root
    if (-not $stopped.Ok) {
      Write-Error "Daily Flow cannot start safely: $($stopped.Reason)"
      exit 1
    }
  }
  $existingPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
  if ($existingPid) {
    if (-not (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
      Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
  }
  # Resolve a real Python via the shared doctor when it's available (rejects the Store stub, checks 3.9+).
  $preflight = Join-Path $Root 'preflight.ps1'
  $python = $null
  if (Test-Path $preflight) {
    . $preflight
    $info = Get-PythonInfo
    if ($info.Ok -and $info.Path) {
      $dir = Split-Path -Parent $info.Path
      $pw = Join-Path $dir 'pythonw.exe'
      $python = if (Test-Path $pw) { $pw } else { $info.Path }
    } else {
      Write-Warning "Daily Flow can't start: $($info.Reason)"
      Write-Host '   Fix it, then run this again. Run preflight.ps1 for a full setup check.' -ForegroundColor Yellow
      exit 1
    }
  } else {
    $pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    $python  = if ($pythonw) { $pythonw.Source } else { (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
    if (-not $python) {
      Write-Warning 'Daily Flow can''t start: Python 3.9+ was not found. Install it from https://www.python.org/downloads/ (tick "Add Python to PATH").'
      exit 1
    }
  }
  # Capture the app's own stderr so a failure to start leaves a readable Python traceback behind
  # instead of vanishing with the hidden window. install.ps1 reads this file when the app is silent.
  $errLog = Join-Path $Root 'app.err.log'
  Remove-Item -LiteralPath $errLog -Force -ErrorAction SilentlyContinue
  $proc = Start-Process -FilePath $python -ArgumentList ('"{0}"' -f (Join-Path $Root 'app.py')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardError $errLog
  Set-Content -LiteralPath $PidFile -Value $proc.Id -Encoding ASCII
  $deadline = (Get-Date).AddSeconds(25)
  while ((Get-Date) -lt $deadline) {
    $appHealth = Get-AppHealth
    if ($appHealth) { break }
    Start-Sleep -Milliseconds 500
  }
}

if ($appHealth) {
  Write-Host "Daily Flow app v$($appHealth.version) is running at $Url"
  if (-not $env:DAILY_FLOW_NO_BROWSER) { Start-Process $Url }
  exit 0
}
else {
  Write-Error "Daily Flow app v$ExpectedVersion did not respond on port $Port. See app.err.log and run preflight.ps1."
  exit 1
}