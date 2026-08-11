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

# 0d. Attachment/document review routing: a review-worthy email with an attachment or a linked
# document must route to a staff reviewer (Quinn) instead of a generic inbox skim, with an explicit
# Action-needed-vs-FYI recommendation and automatic filing of high-value material into the epiq
# working folder. This is a source check (not a live click-through) so it can run without a browser.
$attachmentBackendPresent = ($appSrc -match 'def signal_has_reviewable_attachment') `
  -and ($appSrc -match 'return\s+"attachment-review"') `
  -and ($appSrc -match '"attachment-review":\s*\("Quinn"') `
  -and ($appSrc -match 'EPIC_DOCUMENT_ROOT\s*=\s*ONEDRIVE_DOCUMENT_ROOT\s*/\s*EPIC_WORKING_FOLDER_NAME') `
  -and ($appSrc -match 'def classify_attachment_review') `
  -and ($appSrc -match 'def looks_like_high_value_attachment')
$attachmentFrontendPresent = ($appJsSrc -match '"attachment-review"[\s\S]{0,200}icon:\s*"📎"') `
  -and ($appJsSrc -match 'Documents for review')
$attachmentReviewPresent = $attachmentBackendPresent -and $attachmentFrontendPresent
Add-Result 'Attachment/document review routes to Quinn with FYI-vs-action recommendation and epiq filing' $attachmentReviewPresent `
  $(if (-not $attachmentReviewPresent) { "backend=$attachmentBackendPresent frontend=$attachmentFrontendPresent" } else { '' })

# 0e. Work-status progress bar advances in small increments (time-based creep within a status band)
# instead of jumping directly to a fixed width whenever the job status changes.
$progressSmoothingPresent = ($appJsSrc -match 'function jobProgressWidth') `
  -and (Get-Content -LiteralPath (Join-Path $Root 'app\static\styles.css') -Raw) -match 'transition:\s*width'
Add-Result 'Work-status progress bar advances in small increments instead of jumping' $progressSmoothingPresent

# 0f. Evidence Review v1: attachment-review items get a structured evidence dossier (explicit ask,
# importance-to-me/them, urgency/service impact, attachment analysis, ROI deck fields) and a final
# ACT/FYI/REVIEW REQUIRED verdict with subtype + next-best action, persisted in a dedicated
# evidence_json column and handed off through the Riley->Casey->Drew->Quinn->Major review chain.
$evidenceBackendPresent = ($appSrc -match 'evidence_json') `
  -and ($appSrc -match 'def build_evidence_review') `
  -and ($appSrc -match 'def extract_explicit_ask') `
  -and ($appSrc -match 'def evidence_importance') `
  -and ($appSrc -match 'def evidence_urgency_and_impact') `
  -and ($appSrc -match 'def evidence_roi_deck_fields') `
  -and ($appSrc -match 'EVIDENCE_REVIEW_CHAIN') `
  -and ($appSrc -match '"review_required"') `
  -and ($appSrc -match 'Evidence Review item')
$evidenceFrontendPresent = ($appJsSrc -match 'function evidenceVerdictBadge') `
  -and ($appJsSrc -match 'evidence_json')
$evidenceReviewPresent = $evidenceBackendPresent -and $evidenceFrontendPresent
Add-Result 'Evidence Review v1 (dossier + ACT/FYI/REVIEW REQUIRED verdict + hand-off chain) is wired in' $evidenceReviewPresent `
  $(if (-not $evidenceReviewPresent) { "backend=$evidenceBackendPresent frontend=$evidenceFrontendPresent" } else { '' })

# 0g. Evidence Review orchestration + WorkIQ misroute: Major actively computes and auto-stamps the
# next hop in the Riley->Casey->Drew->Quinn->Major chain based on the evidence dossier and job
# stamps accumulated so far (evidence_review_next_hop), and a misroute check compares the email's
# ask against the user's own defined WorkIQ role/responsibilities, short-circuiting straight to an
# ACT: Delegate recommendation when the item is clearly outside scope.
$orchestratorBackendPresent = ($appSrc -match 'def evidence_review_next_hop') `
  -and ($appSrc -match 'def evidence_misroute_check') `
  -and ($appSrc -match 'content_reviewed') `
  -and ($appSrc -match '"delegate_misroute"') `
  -and ($appSrc -match 'ensure_column\(db, "jobs", "evidence_json"') `
  -and ($appSrc -match 'next_hop = evidence_review_next_hop')
$orchestratorFrontendPresent = ($appJsSrc -match 'delegate_misroute')
$orchestratorPresent = $orchestratorBackendPresent -and $orchestratorFrontendPresent
Add-Result 'Major actively orchestrates Evidence Review hand-offs and WorkIQ misroute detection is wired in' $orchestratorPresent `
  $(if (-not $orchestratorPresent) { "backend=$orchestratorBackendPresent frontend=$orchestratorFrontendPresent" } else { '' })

# 0h. Voice dictation on the approval "Optional guidance for Major" textarea: a mic button using the
# browser's Web Speech API (SpeechRecognition / webkitSpeechRecognition) inserts recognized text into
# the existing textarea without overwriting typed guidance, indicates recording state, and degrades
# visibly (not disruptively) when the API is unsupported or errors. No audio is sent to the backend.
$indexSrc = Get-Content -LiteralPath (Join-Path $Root 'app\static\index.html') -Raw
$dictationFrontendPresent = ($appJsSrc -match 'SpeechRecognition\s*\|\|\s*window\.webkitSpeechRecognition') `
  -and ($appJsSrc -match 'function toggleGuidanceDictation') `
  -and ($appJsSrc -match 'function insertGuidanceText') `
  -and ($appJsSrc -match "isn't supported in this browser") `
  -and ($indexSrc -match 'id="approvalFeedbackMicBtn"') `
  -and ($indexSrc -match 'id="approvalFeedbackMicStatus"')
Add-Result 'Voice dictation (Web Speech API) is wired into the approval guidance textarea' $dictationFrontendPresent

# 0i. Teams outbound message formatting: HTML-ish generated content (Microsoft Graph HTML chat
# bodies, generated prep-brief/job-result delivery text -- <p>, <h1-h6>, <hr>, <b>, <ol>, <li>,
# entities, links) must be converted to human-readable plain text before it lands in the
# summary/recommendation shown to the user and echoed into Major's job instructions, so raw markup
# never leaks into an outbound Teams send. Centralized via sanitize_review_signal_html, applied to
# every non-email/calendar review signal regardless of action_type (not just action_type=="teams"),
# so a Teams-sourced item classified as meeting-prep/commitment/attachment-review is covered too.
$teamsFormatPresent = ($appSrc -match 'def teams_message_to_plain_text') `
  -and ($appSrc -match 'def sanitize_review_signal_html') `
  -and ($appSrc -match 'raw = sanitize_review_signal_html\(raw, action_type\)') `
  -and ($appSrc -match 'if action_type == "email":\s*\r?\n\s*return raw')
Add-Result 'Teams/generated HTML bodies are converted to plain text before display/job instructions' $teamsFormatPresent `
  $(if (-not $teamsFormatPresent) { 'teams_message_to_plain_text/sanitize_review_signal_html not found or not wired into upsert_inbox_signals for all non-email/calendar action types' } else { '' })

# 0j. Timestamps display in the browser's local timezone, not a hardcoded one: app.js's
# humanizeTimes/friendlyLocal helper (used to render raw ISO timestamps embedded in approval
# preview text) must not pin a fixed IANA zone like America/Los_Angeles, metric-detail.html must
# apply the same humanization for consistency with the main dashboard, and the backend's
# format_invite_time() must label the "When:" line with the timezone actually in effect instead
# of a hardcoded "PT" suffix (source checks only; the real formatting is exercised live in
# test/test_local_timestamps.py).
$metricDetailSrc = Get-Content -LiteralPath (Join-Path $Root 'app\static\metric-detail.html') -Raw
$localTimeFrontendPresent = ($appJsSrc -match 'function friendlyLocal') `
  -and ($appJsSrc -notmatch 'America/Los_Angeles') `
  -and ($appJsSrc -notmatch 'timeZone:\s*PT_TZ') `
  -and ($metricDetailSrc -match 'function humanizeTimes') `
  -and ($metricDetailSrc -match 'humanizeTimes\(esc\(approval\.preview\)\)')
$localTimeBackendPresent = ($appSrc -match 'def format_invite_time') `
  -and ($appSrc -notmatch "\{dt\.strftime\('%M %p'\)\} PT`"") `
  -and ($appSrc -match 'tz_label = dt\.strftime\("%Z"\) or APP_TIMEZONE_NAME')
$localTimePresent = $localTimeFrontendPresent -and $localTimeBackendPresent
Add-Result 'Timestamps render in the browser local timezone (no hardcoded PT/Los_Angeles label)' $localTimePresent `
  $(if (-not $localTimePresent) { "frontend=$localTimeFrontendPresent backend=$localTimeBackendPresent" } else { '' })

# 0k. Document-backed draft workflow: a request referencing a named/recent source document (e.g.
# "put the Cowork doc I made before the meeting into a draft") must be treated as a discovery task
# before drafting. The server must never accept a fabricated "completed" claim in place of a real
# document -- validate_document_backed_completion forces the job to 'blocked' with the reported
# evidence when documentStatus is not_found/attach_failed, or found without a link, and the
# dashboard-chat instructions must actually tell Major to search first and report honestly. Role
# ownership is explicit (routing/job instructions), not just prose: Major recognizes the pattern and
# seeds handoffTo=Drew at creation; Drew owns discovery/validation; Riley composes the plain-text
# draft only once Drew confirms a real path/link; Quinn verifies before the approval card.
$skillSrc = Get-Content -LiteralPath (Join-Path $Root 'skills\daily-flow-team\SKILL.md') -Raw
$docDiscoveryBackendPresent = ($appSrc -match 'def validate_document_backed_completion') `
  -and ($appSrc -match 'override = validate_document_backed_completion\(data, status\)') `
  -and ($appSrc -match '"jobs", "document_status"') `
  -and ($appSrc -match 'document_evidence_json') `
  -and ($appSrc -match 'SOURCE DOCUMENT') `
  -and ($appSrc -match 'def looks_like_document_backed_draft_request') `
  -and ($appSrc -match 'def document_draft_next_hop') `
  -and ($appSrc -match "document_backed_draft = 1, handoff_to = 'Drew'") `
  -and ($appSrc -match 'draft_composed')
$docDiscoverySkillPresent = ($skillSrc -match 'SOURCE-DOCUMENT-BACKED DRAFTS')
$docDiscoverySkillRolesPresent = $docDiscoverySkillPresent `
  -and ($skillSrc -match 'Document-backed draft routing') `
  -and ($skillSrc -match 'Document-backed drafts \(discovery leg\)') `
  -and ($skillSrc -match 'Document-backed drafts \(composing leg only\)') `
  -and ($skillSrc -match 'Document-backed drafts \(final verification leg\)')
$docDiscoveryPresent = $docDiscoveryBackendPresent -and $docDiscoverySkillRolesPresent
Add-Result 'Document-backed drafts are gated on real discovery evidence (found/not_found/attach_failed)' $docDiscoveryPresent `
  $(if (-not $docDiscoveryPresent) { "backend=$docDiscoveryBackendPresent skillRoles=$docDiscoverySkillRolesPresent" } else { '' })

# 0l. Document/deck creation workflow: a request to CREATE a new document/deck must yield a
# structured package (not just prose) and support two modes -- a real .docx/.pptx draft in the
# permitted workspace, or a Copilot-ready build prompt fallback when direct creation is
# unavailable. The server must never accept a fabricated "completed" claim in either mode --
# validate_artifact_creation_completion forces the job to 'blocked' when a 'created' claim has no
# file link, or a 'copilot_prompt_fallback' claim has no build prompt. Role ownership is explicit:
# Major routes; Casey supplies confirmed context only when Drew flags a need for it; Drew sources
# evidence and creates the artifact; Mina owns the deck narrative/speaker notes (pptx only); Riley
# composes the plain-text cover note; Quinn validates before the approval gate.
$artifactBackendPresent = ($appSrc -match 'def validate_artifact_creation_completion') `
  -and ($appSrc -match 'artifact_override = validate_artifact_creation_completion\(data, status\)') `
  -and ($appSrc -match '"jobs", "artifact_request"') `
  -and ($appSrc -match 'artifact_package_json') `
  -and ($appSrc -match 'artifact_needs_context') `
  -and ($appSrc -match 'DOCUMENT/DECK CREATION') `
  -and ($appSrc -match 'def looks_like_artifact_creation_request') `
  -and ($appSrc -match 'def artifact_creation_next_hop') `
  -and ($appSrc -match "artifact_request = 1, handoff_to = 'Drew'") `
  -and ($appSrc -match 'narrative_reviewed') `
  -and ($appSrc -match 'cover_note_composed')
$artifactSkillPresent = ($skillSrc -match 'DOCUMENT/DECK CREATION\.')
$artifactSkillRolesPresent = $artifactSkillPresent `
  -and ($skillSrc -match 'Document/deck creation routing') `
  -and ($skillSrc -match 'Document/deck creation \(confirmed-context leg\)') `
  -and ($skillSrc -match 'Document/deck creation \(evidence \+ build leg\)') `
  -and ($skillSrc -match 'Document/deck creation \(narrative leg\)') `
  -and ($skillSrc -match 'Document/deck creation \(cover note leg\)') `
  -and ($skillSrc -match 'Document/deck creation \(final verification leg\)')
$artifactCreationPresent = $artifactBackendPresent -and $artifactSkillRolesPresent
Add-Result 'Document/deck creation yields a structured package with a created/Copilot-prompt-fallback gate' $artifactCreationPresent `
  $(if (-not $artifactCreationPresent) { "backend=$artifactBackendPresent skillRoles=$artifactSkillRolesPresent" } else { '' })

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
