# The Dream Team for Microsoft Scout - smoke test
#
#     powershell -ExecutionPolicy Bypass -File .\test\smoke-test.ps1
#     powershell -ExecutionPolicy Bypass -File .\test\smoke-test.ps1 -Port 8999 -Auth
#
# Starts the app on a scratch port, checks the endpoints the dashboard and the automations depend
# on, then stops it again. Prints PASS/FAIL per check and exits 1 if anything failed, so CI can gate
# on it. It never touches an install: it runs app\app.py straight out of this working tree.

param(
  [int]$Port = 8999,
  [switch]$Auth,
  [int]$StartupTimeoutSec = 15
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$TestRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $TestRoot
$AppPy = Join-Path $Root 'app\app.py'
$StaticDir = Join-Path $Root 'app\static'
if (-not (Test-Path $AppPy)) { Write-Host "[FAIL] app\app.py not found at $AppPy" -ForegroundColor Red; exit 1 }

$script:Results = @()
function Add-Result([string]$Name, [bool]$Ok, [string]$Detail = '') {
  $script:Results += [pscustomobject]@{ Name = $Name; Ok = $Ok; Detail = $Detail }
  if ($Ok) { Write-Host ("[PASS] {0}" -f $Name) -ForegroundColor Green }
  else { Write-Host ("[FAIL] {0}{1}" -f $Name, $(if ($Detail) { " - $Detail" } else { '' })) -ForegroundColor Red }
}

function Get-Python {
  foreach ($name in @('python', 'python3')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandType -eq 'Application' -and $_.Source -and $_.Source -notmatch 'WindowsApps' } |
      Select-Object -First 1
    if ($cmd) { return $cmd.Source }
  }
  return $null
}

$python = Get-Python
if (-not $python) { Write-Host '[FAIL] No usable Python 3 on PATH (the Microsoft Store stub does not count).' -ForegroundColor Red; exit 1 }

$base = "http://127.0.0.1:$Port"
$headers = @{}

function Invoke-Api([string]$Path) {
  # Returns @{ Ok; Status; Json; Error }. Never throws, so one bad endpoint cannot abort the run.
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri ($base + $Path) -Headers $headers -TimeoutSec 10
    $json = $null
    try { $json = $r.Content | ConvertFrom-Json } catch {}
    return @{ Ok = ($r.StatusCode -eq 200); Status = $r.StatusCode; Json = $json; Error = '' }
  } catch {
    $status = 0
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    return @{ Ok = $false; Status = $status; Json = $null; Error = $_.Exception.Message }
  }
}

Write-Host ''
Write-Host "=== Dream Team smoke test (port $Port) ===" -ForegroundColor Cyan
Write-Host ''

# 0. Stale-job watchdog: static check that the requeue logic is present in app.py and wired into
# /api/state, and that it respects Quinn's redaction gate. This is a source check (not a live
# 2-hour wait) so CI can verify the safety net exists without a multi-hour smoke test.
$appSrc = Get-Content -LiteralPath $AppPy -Raw
$watchdogPresent = ($appSrc -match 'def requeue_stale_jobs') `
  -and ($appSrc -match "status IN \('in_progress', 'queued'\)") `
  -and ($appSrc -match 'staleJobTimeoutHours') `
  -and ($appSrc -match 'redaction_required.*redaction_applied') `
  -and ($appSrc -match 'requeue_stale_jobs\(db\)')
Add-Result 'Stale-job watchdog (requeue_stale_jobs) is present and wired into /api/state' $watchdogPresent `
  $(if (-not $watchdogPresent) { 'requeue_stale_jobs / staleJobTimeoutHours / redaction guard not found in app.py' } else { '' })

# 0b. Calendar RSVP UI: the calendar-invite approval group must use the 4-state RSVP scheme
# (Accept/Tentative/Follow/Decline) end to end — decision constants + follow-up job in app.py,
# and the button labels/actions wired into the calendar group in app.js. This is a source check
# (not a live click-through) so it can run without a browser.
$appJsPath = Join-Path $Root 'app\static\app.js'
$appJsSrc = if (Test-Path $appJsPath) { Get-Content -LiteralPath $appJsPath -Raw } else { '' }
$rsvpBackendPresent = ($appSrc -match 'CALENDAR_DECISIONS\s*=\s*\{[^\}]*"accept"[^\}]*"tentative"[^\}]*"follow"[^\}]*"decline"') `
  -and ($appSrc -match 'def create_follow_invite_job') `
  -and ($appSrc -match 'def create_rsvp_job')
$rsvpFrontendPresent = ($appJsSrc -match 'accept:\s*"Accept"') `
  -and ($appJsSrc -match 'tentative:\s*"Tentative"') `
  -and ($appJsSrc -match 'follow:\s*"Follow"') `
  -and ($appJsSrc -match 'decline:\s*"Decline"') `
  -and ($appJsSrc -match 'CALENDAR_ACTIONS\s*=\s*\[.*"accept".*"tentative".*"follow".*"decline".*\]')
$rsvpUiPresent = $rsvpBackendPresent -and $rsvpFrontendPresent
Add-Result 'Calendar RSVP UI has 4 states (Accept/Tentative/Follow/Decline) wired in app.py and app.js' $rsvpUiPresent `
  $(if (-not $rsvpUiPresent) { "backend=$rsvpBackendPresent frontend=$rsvpFrontendPresent" } else { '' })

# 0c. Calendar approval freshness check: before queuing an RSVP/follow job, the app must re-fetch
# the approval row (and expire any newly-time-bound ones) so an invite that was already responded
# to, expired, superseded, or double-decided cannot be double-acted-on. This is a source check
# (not a live race simulation) so it can run without a browser.
$freshnessPresent = ($appSrc -match 'def calendar_invite_freshness_check') `
  -and ($appSrc -match 'expire_time_bound_approvals\(db\)') `
  -and ($appSrc -match '"alreadyHandled":\s*True') `
  -and ($appSrc -match 'calendar_invite_freshness_check\(db, approval_id\)')
$freshnessFrontendPresent = ($appJsSrc -match 'alreadyHandled')
$freshnessCheckPresent = $freshnessPresent -and $freshnessFrontendPresent
Add-Result 'Calendar approval freshness check (re-fetch before queuing) is present and wired in' $freshnessCheckPresent `
  $(if (-not $freshnessCheckPresent) { "backend=$freshnessPresent frontend=$freshnessFrontendPresent" } else { '' })

$appArgs = @($AppPy, '--port', "$Port")
if ($Auth) { $appArgs += '--auth' } else { $appArgs += '--no-auth' }
$outLog = Join-Path ([System.IO.Path]::GetTempPath()) ("dft-smoke-out-{0}.log" -f $PID)
$errLog = Join-Path ([System.IO.Path]::GetTempPath()) ("dft-smoke-err-{0}.log" -f $PID)
Remove-Item -LiteralPath $outLog, $errLog -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $python -ArgumentList $appArgs -PassThru -WindowStyle Hidden `
  -RedirectStandardOutput $outLog -RedirectStandardError $errLog

try {
  # 1. Health check: the app must answer within the startup budget.
  $healthy = $false
  $deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $h = Invoke-Api '/api/health'
    if ($h.Ok -and $h.Json -and $h.Json.ok) { $healthy = $true; break }
    if ($proc.HasExited) { break }
    Start-Sleep -Milliseconds 400
  }
  if (-not $healthy) {
    $why = (Get-Content -LiteralPath $errLog -Raw -ErrorAction SilentlyContinue)
    Add-Result 'GET /api/health returns 200' $false ("app did not come up in ${StartupTimeoutSec}s. " + $why)
  } else {
    Add-Result 'GET /api/health returns 200' $true
  }

  if ($healthy -and $Auth) {
    # With --auth the app writes its token beside app.py; load it so the private GETs can pass.
    $tokenFile = Join-Path $Root 'app\.local-token'
    if (Test-Path $tokenFile) { $headers['Authorization'] = 'Bearer ' + (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
    Add-Result 'Local token file written for --auth' (Test-Path $tokenFile)
  }

  if ($healthy) {
    # 2-4. The three JSON endpoints the dashboard and the automations read on every cycle.
    foreach ($check in @(
      @{ Path = '/api/state';        Key = 'workLedgerToday' },
      @{ Path = '/api/gate';         Key = 'hasWork' },
      @{ Path = '/api/activity-log'; Key = 'events' }
    )) {
      $r = Invoke-Api $check.Path
      $has = $r.Ok -and $r.Json -and ($null -ne $r.Json.PSObject.Properties[$check.Key])
      Add-Result ("GET {0} returns JSON with '{1}'" -f $check.Path, $check.Key) $has `
        $(if (-not $r.Ok) { "HTTP $($r.Status) $($r.Error)" } elseif (-not $has) { 'key missing from the response' } else { '' })
    }

    # 5. Every static asset is reachable, including the dashboard at "/".
    $rootPage = Invoke-Api '/'
    Add-Result 'GET / serves the dashboard' ($rootPage.Ok) $(if (-not $rootPage.Ok) { "HTTP $($rootPage.Status)" } else { '' })
    $staticFiles = @(Get-ChildItem -Path $StaticDir -File)
    $missing = @()
    foreach ($file in $staticFiles) {
      $r = Invoke-Api ('/' + $file.Name)
      if (-not $r.Ok) { $missing += ("{0} (HTTP {1})" -f $file.Name, $r.Status) }
    }
    Add-Result ("All {0} files in app\static\ are served" -f $staticFiles.Count) ($missing.Count -eq 0) ($missing -join ', ')

    # 6. Path traversal must be refused, not served.
    $trav = Invoke-Api '/..%2Fapp.py'
    Add-Result 'Path traversal is refused' (-not $trav.Ok) $(if ($trav.Ok) { 'served a file outside app\static' } else { '' })

    # 7. /api/state?since= in the future returns a trimmed delta, not the full history.
    $future = (Get-Date).ToUniversalTime().AddDays(1).ToString('yyyy-MM-ddTHH:mm:ssZ')
    $delta = Invoke-Api ('/api/state?since=' + [uri]::EscapeDataString($future))
    $trimmed = $delta.Ok -and $delta.Json -and (@($delta.Json.events).Count -eq 0)
    Add-Result 'GET /api/state?since= filters history' $trimmed `
      $(if (-not $delta.Ok) { "HTTP $($delta.Status)" } elseif (-not $trimmed) { 'events were not filtered' } else { '' })

    # 8. The knowledge graph round-trips: create, find, summarise, soft-delete. This is Casey's
    # only storage, so a silent failure here would mean the team quietly forgets everything.
    $knOk = $false; $knNote = ''
    try {
      $body = @{ type = 'commitment'; title = 'Smoke test commitment'; summary = 'created by smoke-test.ps1' } | ConvertTo-Json
      $created = (Invoke-WebRequest -UseBasicParsing -Uri ($base + '/api/knowledge') -Method Post `
        -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 10).Content | ConvertFrom-Json
      $found = Invoke-Api '/api/knowledge?type=commitment&q=smoke'
      $state = Invoke-Api '/api/state'
      $deleted = (Invoke-WebRequest -UseBasicParsing -Uri ($base + '/api/knowledge/' + $created.id) `
        -Method Delete -Headers $headers -TimeoutSec 10).Content | ConvertFrom-Json
      $after = Invoke-Api '/api/knowledge?type=commitment&q=smoke'
      $knOk = $created.ok -and $created.id -and (@($found.Json.entries).Count -ge 1) `
        -and ($null -ne $state.Json.knowledgeSummary) -and ($null -ne $state.Json.qualitySummary) `
        -and $deleted.ok -and (@($after.Json.entries).Count -eq 0)
      if (-not $knOk) { $knNote = "created=$($created.ok) found=$(@($found.Json.entries).Count) deleted=$($deleted.ok) remaining=$(@($after.Json.entries).Count)" }
    } catch { $knNote = $_.Exception.Message }
    Add-Result 'Knowledge graph create/query/delete round-trips' $knOk $knNote

    # 9. The capability endpoints answer and are guarded. Two things are checked together here
    # because they fail differently: an endpoint that 404s was never wired up, and an endpoint
    # that answers a request it should have refused is a security regression. Both are silent
    # unless something asks.
    $capOk = $false; $capNote = ''
    try {
      $inv = Invoke-Api '/api/runtime-inventory'
      $posts = [ordered]@{
        '/api/content-pass'    = @{ text = 'Contact bob@example.com about this.'; redact = $true }
        '/api/skill-lint'      = @{ text = "# A skill`nDo the thing." }
        '/api/format-list'     = @{ rows = @(@{ Name = 'Ada'; Age = '36' }) }
        '/api/document-flow'   = @{ name = 'F'; actions = @{ Send = @{ type = 'ApiConnection' } } }
        '/api/chart-spec'      = @{ rows = @(@{ m = 'Jan'; s = 10 }, @{ m = 'Feb'; s = 14 }) }
        '/api/conference-pack' = @{ topic = 'Local-first AI' }
        '/api/talk-track'      = @{ slides = @('Intro', 'Body', 'Close'); durationMinutes = 15 }
      }
      $bad = @()
      foreach ($path in $posts.Keys) {
        $r = (Invoke-WebRequest -UseBasicParsing -Uri ($base + $path) -Method Post -Headers $headers `
          -ContentType 'application/json' -Body ($posts[$path] | ConvertTo-Json -Depth 6) -TimeoutSec 10).Content | ConvertFrom-Json
        if (-not $r.ok) { $bad += $path }
      }
      # The redaction gate is the one whose *content* matters: if it stops removing the address,
      # every downstream claim about blocking a send becomes false.
      $red = (Invoke-WebRequest -UseBasicParsing -Uri ($base + '/api/content-pass') -Method Post -Headers $headers `
        -ContentType 'application/json' -Body (@{ text = 'Mail bob@example.com'; redact = $true } | ConvertTo-Json) -TimeoutSec 10).Content | ConvertFrom-Json
      $redacted = $red.redactedText -and ($red.redactedText -notmatch 'bob@example\.com')
      if (-not $redacted) { $bad += 'redaction' }
      # A path outside the skills folder must be refused rather than read.
      $trav = 403
      try {
        Invoke-WebRequest -UseBasicParsing -Uri ($base + '/api/skill-lint') -Method Post -Headers $headers `
          -ContentType 'application/json' -Body (@{ path = '../../app/app.py' } | ConvertTo-Json) -TimeoutSec 10 | Out-Null
        $trav = 200
      } catch { $trav = [int]$_.Exception.Response.StatusCode }
      if ($trav -ne 403) { $bad += "traversal-not-refused($trav)" }
      $capOk = $inv.Ok -and $inv.Json.ok -and ($bad.Count -eq 0) -and ($null -ne (Invoke-Api '/api/state').Json.capabilitySummary)
      if (-not $capOk) { $capNote = "inventory=$($inv.Status) failing=$($bad -join ',')" }
    } catch { $capNote = $_.Exception.Message }
    Add-Result 'Capability endpoints answer, redact, and refuse traversal' $capOk $capNote
  }
} finally {
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    $proc.WaitForExit(5000) | Out-Null
  }
}

$failed = @($script:Results | Where-Object { -not $_.Ok })
Write-Host ''
Write-Host ("=== {0} passed, {1} failed ===" -f ($script:Results.Count - $failed.Count), $failed.Count) `
  -ForegroundColor $(if ($failed.Count) { 'Red' } else { 'Green' })
if ($failed.Count) {
  Write-Host ''
  Write-Host 'App stderr:' -ForegroundColor Yellow
  Get-Content -LiteralPath $errLog -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
  exit 1
}
exit 0
