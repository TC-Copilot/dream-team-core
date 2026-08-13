# Daily Flow Team - safe local app process lifecycle helpers

function Get-DailyFlowHealth {
  param([int]$Port, [string]$ExpectedVersion)
  try {
    $health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/api/health" -f $Port) -TimeoutSec 2
    if ($health.ok -ne $true -or -not $health.version) { return $null }
    if ($ExpectedVersion -and ([string]$health.version).Trim() -ne $ExpectedVersion.Trim()) { return $null }
    return $health
  } catch {
    return $null
  }
}

function Get-PortOwningProcessId {
  param([int]$Port)
  $ids = @()
  try {
    $ids = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
      Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::', '::1') } |
      Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    try {
      $pattern = '^\s*TCP\s+\S+:' + [regex]::Escape([string]$Port) + '\s+\S+\s+LISTENING\s+(\d+)\s*$'
      $ids = @(& netstat -ano -p tcp 2>$null | ForEach-Object {
        if ($_ -match $pattern) { [int]$Matches[1] }
      } | Select-Object -Unique)
    } catch {
      $ids = @()
    }
  }
  if ($ids.Count -eq 1) { return [int]$ids[0] }
  if ($ids.Count -gt 1) { return -1 }
  return $null
}

function Wait-DailyFlowPortFree {
  param([int]$Port, [int]$TimeoutSeconds = 15)
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    if (-not (Get-PortOwningProcessId -Port $Port)) { return $true }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Test-DailyFlowPortOwner {
  param([int]$Port, [string]$AppRoot)
  $pidOnPort = Get-PortOwningProcessId -Port $Port
  if (-not $pidOnPort) {
    return [pscustomobject]@{ IsOurApp = $false; ProcessId = $null; Reason = "No process is listening on port $Port." }
  }
  if ($pidOnPort -eq -1) {
    return [pscustomobject]@{ IsOurApp = $false; ProcessId = $null; Reason = "Multiple processes appear to own port $Port; ownership is ambiguous." }
  }
  $health = Get-DailyFlowHealth -Port $Port
  if (-not $health) {
    return [pscustomobject]@{ IsOurApp = $false; ProcessId = $pidOnPort; Reason = "Port $Port is owned by a process that is not a responsive Daily Flow app." }
  }
  $process = Get-Process -Id $pidOnPort -ErrorAction SilentlyContinue
  if (-not $process -or $process.ProcessName -notmatch '^python(w)?$') {
    return [pscustomobject]@{ IsOurApp = $false; ProcessId = $pidOnPort; Reason = "Port $Port answers like Daily Flow, but its owner is not a Python app process." }
  }

  $pidFileMatches = $false
  $pidFile = Join-Path $AppRoot 'daily-flow-app.pid'
  if (Test-Path $pidFile) {
    $recordedPid = 0
    $pidFileMatches = [int]::TryParse(([string](Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)).Trim(), [ref]$recordedPid) -and $recordedPid -eq $pidOnPort
  }

  $commandMatches = $false
  try {
    $commandLine = [string](Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $pidOnPort) -ErrorAction Stop).CommandLine
    $appScript = (Join-Path $AppRoot 'app.py').ToLowerInvariant()
    $commandMatches = $commandLine.ToLowerInvariant().Contains($appScript)
  } catch {}

  if (-not $pidFileMatches -and -not $commandMatches) {
    return [pscustomobject]@{ IsOurApp = $false; ProcessId = $pidOnPort; Reason = "Port $Port responds like Daily Flow, but process ownership could not be tied to $AppRoot." }
  }
  return [pscustomobject]@{ IsOurApp = $true; ProcessId = $pidOnPort; Reason = "Daily Flow v$($health.version) owns port $Port." }
}

function Stop-DailyFlowAppOnPort {
  param([int]$Port, [string]$AppRoot, [int]$TimeoutSeconds = 15)
  $pidOnPort = Get-PortOwningProcessId -Port $Port
  if (-not $pidOnPort) {
    Remove-Item -LiteralPath (Join-Path $AppRoot 'daily-flow-app.pid') -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Ok = $true; Stopped = $false; ProcessId = $null; Reason = "Port $Port is already free." }
  }
  $owner = Test-DailyFlowPortOwner -Port $Port -AppRoot $AppRoot
  if (-not $owner.IsOurApp) {
    return [pscustomobject]@{ Ok = $false; Stopped = $false; ProcessId = $owner.ProcessId; Reason = $owner.Reason }
  }
  try {
    Stop-Process -Id $owner.ProcessId -Force -ErrorAction Stop
  } catch {
    return [pscustomobject]@{ Ok = $false; Stopped = $false; ProcessId = $owner.ProcessId; Reason = "Could not stop Daily Flow PID $($owner.ProcessId): $($_.Exception.Message)" }
  }
  if (-not (Wait-DailyFlowPortFree -Port $Port -TimeoutSeconds $TimeoutSeconds)) {
    return [pscustomobject]@{ Ok = $false; Stopped = $true; ProcessId = $owner.ProcessId; Reason = "Stopped PID $($owner.ProcessId), but port $Port was not released within $TimeoutSeconds seconds." }
  }
  Remove-Item -LiteralPath (Join-Path $AppRoot 'daily-flow-app.pid') -Force -ErrorAction SilentlyContinue
  return [pscustomobject]@{ Ok = $true; Stopped = $true; ProcessId = $owner.ProcessId; Reason = "Stopped Daily Flow PID $($owner.ProcessId) and released port $Port." }
}
