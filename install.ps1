# Daily Flow Team (Dream Team) - Installer / Bootstrap
# Author: Shervin Shaffie
#
# Normally Scout runs this for you (see INSTALL-WITH-SCOUT.md). To run it yourself:
#     powershell -ExecutionPolicy Bypass -File .\install.ps1          (mechanical install only)
#     powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto    (full hands-off setup)
# When Scout runs this inline and then finishes setup in the same chat, it passes -AgentInline so the
# closing message does not tell the user to restart Scout or paste a command (Scout handles it in chat).
#
# It copies the team skills into Microsoft Scout and places the app. With -Auto it also writes
# sensible defaults, starts the dashboard, opens Scout, and copies the finishing command for you.
# It NEVER changes your Scout model or automations - the in-Scout wizard does that, with your ok.

param(
  [string]$InstallDir = (Join-Path $env:USERPROFILE 'Daily Flow Team'),
  [int]$BasePort = 8787,
  [switch]$Auto,
  [switch]$AgentInline,
  [switch]$NoBrowser,
  [string]$OverlayManifestPath,
  [switch]$ResetApplicationLayer
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$PkgRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillsSrc = Join-Path $PkgRoot 'skills'
if ($NoBrowser) { $env:DAILY_FLOW_NO_BROWSER = '1' }

# Everything printed also lands in $InstallDir\install.log, so a failed hands-off install can be
# diagnosed after the window has closed. Write-Host is wrapped rather than using Start-Transcript
# because the transcript would miss output from the child scripts this installer dot-sources.
$script:LogPath = $null
function Initialize-InstallLog([string]$Dir) {
  try {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $script:LogPath = Join-Path $Dir 'install.log'
    "=== Daily Flow Team install - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
      Out-File -LiteralPath $script:LogPath -Encoding UTF8 -Append
  } catch { $script:LogPath = $null }
}
function Write-Log([string]$Text) {
  if ($script:LogPath) {
    try { $Text | Out-File -LiteralPath $script:LogPath -Encoding UTF8 -Append } catch {}
  }
}
function Write-Host {
  # Shadow Write-Host so every existing call in this script is tee'd to the log for free.
  [CmdletBinding()]
  param(
    [Parameter(Position = 0, ValueFromPipeline = $true)] $Object,
    [System.ConsoleColor] $ForegroundColor,
    [System.ConsoleColor] $BackgroundColor,
    [switch] $NoNewline
  )
  $params = @{}
  if ($PSBoundParameters.ContainsKey('ForegroundColor')) { $params['ForegroundColor'] = $ForegroundColor }
  if ($PSBoundParameters.ContainsKey('BackgroundColor')) { $params['BackgroundColor'] = $BackgroundColor }
  if ($NoNewline) { $params['NoNewline'] = $true }
  Microsoft.PowerShell.Utility\Write-Host $Object @params
  Write-Log ([string]$Object)
}
Initialize-InstallLog $InstallDir

. (Join-Path $PkgRoot 'compatibility.ps1')
$CoreCompatibility = Get-CoreCompatibilityInfo -PackageRoot $PkgRoot
$NewVersion = $CoreCompatibility.VersionText
. (Join-Path $PkgRoot 'app\app-lifecycle.ps1')
function Get-ScoutSkillRoots {
  # Scout's per-user data directory name varies by build: .scout (newer), .copilot,
  # .copilot-cloud, or .copilot-dev. We never hardcode one - we detect every root that
  # actually holds Scout data and install to all of them, so the skills land wherever
  # THIS machine's Scout reads from. (This is the fix for skills landing in the wrong folder.)
  $homeDir = $env:USERPROFILE
  $candidates = @('.scout','.copilot','.copilot-cloud','.copilot-dev')
  $markers = @('m-skills','m-sessions','m-automations','m-settings.json','config.json','session-store.db')
  $roots = @()
  foreach ($name in $candidates) {
    $root = Join-Path $homeDir $name
    if (-not (Test-Path $root)) { continue }
    $isScout = $false
    foreach ($m in $markers) { if (Test-Path (Join-Path $root $m)) { $isScout = $true; break } }
    if ($isScout) { $roots += (Join-Path $root 'm-skills') }
  }
  if ($roots.Count -eq 0) {
    # Nothing detected (rare). Install to both common names so Scout cannot miss it.
    $roots = @((Join-Path $homeDir '.scout\m-skills'), (Join-Path $homeDir '.copilot\m-skills'))
  }
  return $roots
}
$SkillRoots = @(Get-ScoutSkillRoots)

function Test-PortFree([int]$Port) {
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',$Port); $c.Close(); return $false } catch { return $true }
}

Write-Host ''
Write-Host '=== Daily Flow Team - Setup ===' -ForegroundColor Cyan
Write-Host ''

# 1. Python check (hardened): reject the Microsoft Store stub, require 3.9+, and self-heal via winget.
. (Join-Path $PkgRoot 'preflight.ps1')   # dot-source for Get-PythonInfo / Install-PythonViaWinget (no report when dot-sourced)
$py = Get-PythonInfo
if (-not $py.Ok) {
  if ($py.IsStub) { Write-Host '[warn] Found the Microsoft Store Python stub (it opens the Store instead of running Python).' -ForegroundColor Yellow }
  elseif ($py.Found) { Write-Host "[warn] $($py.Reason)" -ForegroundColor Yellow }
  else { Write-Host '[warn] Python 3.9+ was not found on PATH.' -ForegroundColor Yellow }
  $doInstall = $true
  $ans = Read-Host 'Install Python 3 now with winget? (recommended, user-scope, no admin) [Y/n]'
  if ($ans -and $ans.Trim().ToLower().StartsWith('n')) { $doInstall = $false }
  if ($doInstall) {
    $res = Install-PythonViaWinget
    if ($res.Ok) { Write-Host "[ok] $($res.Reason)" -ForegroundColor Green; $py = Get-PythonInfo }
    else { Write-Host "[info] $($res.Reason)" -ForegroundColor Yellow }
  }
  if (-not $py.Ok) {
    Write-Host '[STOP] Python 3.9+ is required to run the app.' -ForegroundColor Red
    Write-Host '       Install it from https://www.python.org/downloads/ (tick "Add Python to PATH"),' -ForegroundColor Yellow
    Write-Host '       then run the install again. (Tip: run preflight.ps1 to re-check your setup.)' -ForegroundColor Yellow
    if ($Auto) { Write-Host ''; Read-Host 'Press Enter to close' }
    return
  }
}
$pyLabel = if ($py.Version) { "Python $($py.Version)" } else { 'Python' }
Write-Host "[ok] $pyLabel ready."

# 1b. Microsoft Scout check. The app is only the cockpit — Major, the automations, and the AI all run
#     inside Scout. We do NOT block the install (the dashboard still works and skills are pre-placed for
#     when Scout arrives), but we warn loudly and switch off the "open Scout / run /daily-flow-setup"
#     finish so the user isn't told to use an app they don't have.
$scout = Get-ScoutInfo
$ScoutMissing = -not $scout.Found
if ($ScoutMissing) {
  Write-Host ''
  Write-Host '  ============================================================' -ForegroundColor Yellow
  Write-Host '   HEADS UP: Microsoft Scout was not found on this machine.' -ForegroundColor Yellow
  Write-Host '   The dashboard will install and run, but your TEAM STAYS' -ForegroundColor Yellow
  Write-Host '   INACTIVE until Scout is installed — Major, the background' -ForegroundColor Yellow
  Write-Host '   automations, and all the AI live inside Microsoft Scout.' -ForegroundColor Yellow
  Write-Host '   Install Microsoft Scout, then run the install again.' -ForegroundColor Yellow
  Write-Host '  ============================================================' -ForegroundColor Yellow
  if (-not $Auto) {
    $go = Read-Host 'Continue placing the local app anyway? [Y/n]'
    if ($go -and $go.Trim().ToLower().StartsWith('n')) { Write-Host '[stop] Setup cancelled. Install Microsoft Scout first.' -ForegroundColor Yellow; return }
  }
} else {
  Write-Host "[ok] Microsoft Scout detected."
}

# 2. Detect an existing install so this can UPGRADE in place (preserving the local DB + settings).
$existingDir = $null
foreach ($root in $SkillRoots) {
  $ptr = Join-Path ([string]$root) 'daily-flow-setup\.install-location'
  if (Test-Path $ptr) {
    $cand = (Get-Content -LiteralPath $ptr -Raw).Trim()
    if ($cand -and (Test-Path (Join-Path $cand 'app'))) { $existingDir = $cand; break }
  }
}
if (-not $existingDir -and (Test-Path (Join-Path $InstallDir 'app'))) { $existingDir = $InstallDir }
$IsUpgrade = [bool]$existingDir
$OldVersion = $null
$ExistingConfig = $null
$UpgradePort = $BasePort
$UpgradeWasRunning = $false
if ($IsUpgrade) {
  if (-not $PSBoundParameters.ContainsKey('InstallDir')) { $InstallDir = $existingDir; Initialize-InstallLog $InstallDir }  # upgrade in place
}

# Resolve compatibility only after the final install location is known. An explicit overlay manifest
# or a manifest registered by a previous overlay install must validate before the running app is
# stopped or any package file is replaced. No manifest means the normal public core-only path.
$OverlayCompatibility = Resolve-OverlayCompatibility -Core $CoreCompatibility -InstallDir $InstallDir `
  -OverlayManifestPath $OverlayManifestPath -OverlayRequested:$PSBoundParameters.ContainsKey('OverlayManifestPath')
if ($OverlayCompatibility.Overlay) {
  Write-Host ("[ok] Verified overlay {0} v{1} against core v{2} / contract v{3}." -f `
    $OverlayCompatibility.Overlay.Id, $OverlayCompatibility.Overlay.VersionText, $NewVersion, $CoreCompatibility.ContractVersionText) -ForegroundColor Green
} else {
  Write-Host ("[ok] Core-only install: core v{0}, contract v{1}." -f $NewVersion, $CoreCompatibility.ContractVersionText) -ForegroundColor Green
}

if ($IsUpgrade) {
  $verFile = Join-Path $existingDir 'app\.installed-version'
  if (Test-Path $verFile) { $OldVersion = (Get-Content -LiteralPath $verFile -Raw).Trim() }
  $verLabel = if ($OldVersion) { "v$OldVersion" } else { 'an earlier version' }
  $existingConfigPath = Join-Path $existingDir 'app\config.json'
  if (Test-Path $existingConfigPath) {
    try {
      $ExistingConfig = Get-Content -LiteralPath $existingConfigPath -Raw | ConvertFrom-Json
      if ($ExistingConfig.port) { $UpgradePort = [int]$ExistingConfig.port }
    } catch {
      Write-Host "[STOP] Existing config.json could not be read: $($_.Exception.Message)" -ForegroundColor Red
      exit 1
    }
  }
  Write-Host ("[info] Found an existing install ({0}) at {1}" -f $verLabel, $existingDir) -ForegroundColor Cyan
  Write-Host ("       Upgrading {0} -> v{1}. Your local database, settings, and any employees you added are kept; the database migrates automatically on first launch." -f $verLabel, $NewVersion) -ForegroundColor Cyan
  $UpgradeWasRunning = [bool](Get-PortOwningProcessId -Port $UpgradePort)
  if ($UpgradeWasRunning) {
    $stopResult = Stop-DailyFlowAppOnPort -Port $UpgradePort -AppRoot (Join-Path $existingDir 'app')
    if (-not $stopResult.Ok) {
      Write-Host "[STOP] Upgrade cannot safely replace the running app: $($stopResult.Reason)" -ForegroundColor Red
      Write-Host '       No unrelated process was stopped. Close the process or choose another configured port, then retry.' -ForegroundColor Yellow
      exit 1
    }
    Write-Host "[ok] $($stopResult.Reason)"
  }
}

# 3. Install/refresh skills into EVERY detected Scout skills root.
#    Fresh install: keep any same-named skill the user already has (don't clobber). daily-flow-setup
#    is always refreshed. UPGRADE: refresh ALL of this package's bundled skills to the new version.
#    *-template folders are OPTIONAL employees (Atlas, Piper). They ship in the package so the user
#    can read and copy them, but they are never installed automatically - adding an employee is the
#    user's decision, not the installer's.
$installed = @(); $kept = @(); $updated = @()
foreach ($root in $SkillRoots) {
  $MSkills = [string]$root
  New-Item -ItemType Directory -Force -Path $MSkills | Out-Null
  Get-ChildItem -Directory $SkillsSrc | Where-Object { $_.Name -notlike '*-template' } | ForEach-Object {
    $name = $_.Name; $dest = Join-Path $MSkills $name
    $exists = Test-Path $dest
    if ($name -eq 'daily-flow-setup' -or $IsUpgrade -or -not $exists) {
      # Remove first so Copy-Item -Recurse overwrites cleanly instead of nesting (dest\name\name).
      if ($exists) { Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue }
      Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force
      if ($exists -and $name -ne 'daily-flow-setup') { if ($updated -notcontains $name) { $updated += $name } }
      elseif (-not $exists) { if ($installed -notcontains $name) { $installed += $name } }
    }
    elseif ($exists) { if ($kept -notcontains $name) { $kept += $name } }
  }
}
$rootLabels = $SkillRoots | ForEach-Object { $_.Replace($env:USERPROFILE, '~') }
if ($IsUpgrade) {
  Write-Host "[ok] Refreshed team skills to v$NewVersion ($($SkillRoots.Count) skills folder(s))."
  if ($updated.Count -gt 0) { Write-Host "[info] Updated: $($updated -join ', ')" -ForegroundColor DarkGray }
} else {
  Write-Host "[ok] Installed team skills into Scout ($($SkillRoots.Count) skills folder(s)):"
  foreach ($rl in $rootLabels) { Write-Host "      $rl" -ForegroundColor DarkGray }
  if ($kept.Count -gt 0) { Write-Host "[info] Kept your existing version of: $($kept -join ', ')" -ForegroundColor DarkGray }
}

# 4. Place the app + automation templates. A layered installer can request a clean public app
#    baseline before applying its own separately validated files. Runtime state is staged into the
#    replacement, while the default install path remains the existing in-place copy.
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if ($ResetApplicationLayer -and $IsUpgrade) {
  $installedApp = Join-Path $InstallDir 'app'
  $stagedApp = Join-Path $InstallDir ('.core-app-' + [guid]::NewGuid().ToString('N'))
  $backupApp = Join-Path $InstallDir ('.previous-app-' + [guid]::NewGuid().ToString('N'))
  try {
    Copy-Item -LiteralPath (Join-Path $PkgRoot 'app') -Destination $stagedApp -Recurse -Force
    foreach ($runtimeItem in @('config.json','data','profile','state.json','impact.json','.local-token')) {
      $source = Join-Path $installedApp $runtimeItem
      if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $stagedApp $runtimeItem) -Recurse -Force
      }
    }
    Move-Item -LiteralPath $installedApp -Destination $backupApp
    try {
      Move-Item -LiteralPath $stagedApp -Destination $installedApp
    } catch {
      $swapError = $_
      try {
        Move-Item -LiteralPath $backupApp -Destination $installedApp -ErrorAction Stop
      } catch {
        throw "Core-layer swap failed and the previous app could not be restored automatically. It remains at $backupApp. Original error: $($swapError.Exception.Message). Restore error: $($_.Exception.Message)"
      }
      throw $swapError
    }
    Remove-Item -LiteralPath $backupApp -Recurse -Force
    Write-Host '[ok] Reset the application layer to a clean public-core baseline (runtime state preserved).'
  } finally {
    Remove-Item -LiteralPath $stagedApp -Recurse -Force -ErrorAction SilentlyContinue
  }
} else {
  Copy-Item -LiteralPath (Join-Path $PkgRoot 'app') -Destination $InstallDir -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $PkgRoot 'automations') -Destination $InstallDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PkgRoot 'compatibility.ps1') -Destination (Join-Path $InstallDir 'compatibility.ps1') -Force
# Place the setup doctor beside the app so start-app.ps1 can reuse the same Python checks later.
Copy-Item -LiteralPath (Join-Path $PkgRoot 'preflight.ps1') -Destination (Join-Path $InstallDir 'app\preflight.ps1') -Force -ErrorAction SilentlyContinue
# Stamp the installed version so a future run can tell new-install from upgrade and show X -> Y.
# ASCII (not -Encoding UTF8) on purpose: Windows PowerShell 5.1 writes a BOM for UTF8, and the BOM
# would end up inside the version string the app reads back.
Set-Content -LiteralPath (Join-Path $InstallDir 'app\.installed-version') -Value $NewVersion -Encoding ASCII -ErrorAction SilentlyContinue
if ($OverlayCompatibility.Source -eq 'explicit') {
  $sourcePath = [System.IO.Path]::GetFullPath($OverlayCompatibility.Overlay.ManifestPath)
  $registeredPath = [System.IO.Path]::GetFullPath($OverlayCompatibility.RegisteredPath)
  if ($sourcePath -ne $registeredPath) {
    Copy-Item -LiteralPath $sourcePath -Destination $registeredPath -Force
  }
}
$VersionReportPath = Write-InstalledVersionReport -InstallDir $InstallDir -Core $CoreCompatibility -Compatibility $OverlayCompatibility
if ($IsUpgrade) { Write-Host "[ok] Updated the app in: $InstallDir (database & settings preserved)" }
else { Write-Host "[ok] Placed the app in: $InstallDir" }

# 5. Record install location for the wizard in EVERY root, so the wizard finds it
#    no matter which folder Scout loaded the skill from.
foreach ($root in $SkillRoots) {
  $MSkills = [string]$root
  $ptrDir = Join-Path $MSkills 'daily-flow-setup'
  New-Item -ItemType Directory -Force -Path $ptrDir | Out-Null
  Set-Content -LiteralPath (Join-Path $ptrDir '.install-location') -Value $InstallDir -Encoding UTF8
}

if (-not $Auto) {
  if ($IsUpgrade -and $UpgradeWasRunning) {
    $env:DAILY_FLOW_NO_BROWSER = '1'
    & (Join-Path $InstallDir 'app\start-app.ps1')
    if ($LASTEXITCODE -ne 0) {
      Write-Host "[STOP] Files were updated, but Daily Flow v$NewVersion did not restart on port $UpgradePort." -ForegroundColor Red
      exit 1
    }
    Write-Host "[ok] Restarted and verified Daily Flow v$NewVersion on port $UpgradePort." -ForegroundColor Green
  }
  Write-Host ''
  if ($ScoutMissing) {
    Write-Host '=== Local app placed — but Microsoft Scout is required ===' -ForegroundColor Yellow
    Write-Host '  1) Install Microsoft Scout on this machine. Microsoft employees: get it from your internal aka.ms site, not the public link.'
    Write-Host '  2) Run the install again so the team skills load into Scout.'
    Write-Host '  3) Then, in a new Scout chat, type:  /daily-flow-setup'
  } else {
    Write-Host '=== Almost done! Two steps left ===' -ForegroundColor Green
    Write-Host '  1) Open Microsoft Scout.'
    Write-Host '  2) In a new chat, type:  /daily-flow-setup'
    Write-Host ''
    Write-Host 'If Scout does not recognize /daily-flow-setup, fully restart Scout so it loads the new skills.'
  }
  return
}

# ---------- -Auto: finish hands-off ----------
# 5. Preserve the configured port on upgrade; fresh installs choose the first free port.
$port = if ($IsUpgrade) { $UpgradePort } else { $BasePort }
if (-not $IsUpgrade -and -not (Test-PortFree $BasePort)) {
  foreach ($p in ($BasePort+1)..($BasePort+12)) { if (Test-PortFree $p) { $port = $p; break } }
}
if (-not (Test-PortFree $port)) {
  Write-Host "[STOP] Port $port is occupied; the new Daily Flow app cannot be started safely." -ForegroundColor Red
  exit 1
}

# 6. Choose a document folder (prefer OneDrive - Microsoft, then OneDrive, else Documents)
$docRoot = if ($IsUpgrade -and $ExistingConfig -and $ExistingConfig.documentRoot) { [string]$ExistingConfig.documentRoot } else { $null }
if (-not $docRoot) {
  foreach ($cand in @(
    (Join-Path $env:USERPROFILE 'OneDrive - Microsoft\Scout'),
    (Join-Path $env:USERPROFILE 'OneDrive\Scout'),
    (Join-Path $env:USERPROFILE 'Documents\Daily Flow')
  )) {
    $parent = Split-Path $cand -Parent
    if (Test-Path $parent) { $docRoot = $cand; break }
  }
}
if (-not $docRoot) { $docRoot = (Join-Path $env:USERPROFILE 'Documents\Daily Flow') }
New-Item -ItemType Directory -Force -Path $docRoot | Out-Null

# 7. Write config.json beside the app
$config = [ordered]@{}
if ($IsUpgrade -and $ExistingConfig) {
  foreach ($property in $ExistingConfig.PSObject.Properties) {
    $config[$property.Name] = $property.Value
  }
}
$config['port'] = $port
$config['documentRoot'] = $docRoot
$cfgPath = Join-Path $InstallDir 'app\config.json'
($config | ConvertTo-Json) | Set-Content -LiteralPath $cfgPath -Encoding UTF8
Write-Host "[ok] Configured: port $port, documents -> $docRoot"

# 8. Start the app, wait for it to be live, then open the dashboard ourselves
$prevNoBrowser = $env:DAILY_FLOW_NO_BROWSER
$env:DAILY_FLOW_NO_BROWSER = '1'   # we open the browser ourselves once the app confirms live
$appLog = Join-Path $InstallDir 'app\app-start.log'
Remove-Item -LiteralPath $appLog -Force -ErrorAction SilentlyContinue
& (Join-Path $InstallDir 'app\start-app.ps1') *>&1 | Tee-Object -FilePath $appLog | Out-Null
$env:DAILY_FLOW_NO_BROWSER = $prevNoBrowser
# 20 second budget: the app either answers or we have a real failure worth showing, not a
# "it will appear in a moment" that leaves a hands-off install silently broken.
$live = $false
for ($i = 0; $i -lt 40; $i++) {
  if (Get-DailyFlowHealth -Port $port -ExpectedVersion $NewVersion) { $live = $true; break }
  Start-Sleep -Milliseconds 500
}
if ($live) {
  if (-not $env:DAILY_FLOW_NO_BROWSER) { Start-Process "http://127.0.0.1:$port/" }
  Write-Host "[ok] Dashboard v$NewVersion is live and verified at http://127.0.0.1:$port/" -ForegroundColor Green
} else {
  Write-Host ''
  Write-Host '[STOP] The app did not answer within 20 seconds. It is not running.' -ForegroundColor Red
  # Show the actual Python error rather than a vague "still starting" message. Try the app's own
  # start log first, then reproduce the failure in the foreground so the traceback is captured.
  $detail = @()
  foreach ($candidate in @($appLog, (Join-Path $InstallDir 'app\app.err.log'), (Join-Path $InstallDir 'app\app.log'))) {
    if (Test-Path $candidate) {
      $tail = Get-Content -LiteralPath $candidate -Tail 25 -ErrorAction SilentlyContinue
      if ($tail) { $detail += "--- $candidate ---"; $detail += $tail }
    }
  }
  if (-not $detail) {
    $pyExe = if ($py.Path) { $py.Path } else { 'python' }
    $probe = & $pyExe (Join-Path $InstallDir 'app\app.py') '--init' 2>&1 | ForEach-Object { [string]$_ }
    if ($probe) { $detail += '--- python app.py --init ---'; $detail += $probe }
  }
  if ($detail) {
    Write-Host '       The error was:' -ForegroundColor Yellow
    foreach ($line in $detail) { Write-Host "         $line" -ForegroundColor DarkGray }
  } else {
    Write-Host "       No Python output was captured. Run it by hand to see the error:" -ForegroundColor Yellow
    Write-Host ("         python `"{0}`"" -f (Join-Path $InstallDir 'app\app.py')) -ForegroundColor Yellow
  }
  Write-Host ("       Full install log: {0}" -f (Join-Path $InstallDir 'install.log')) -ForegroundColor Yellow
  exit 1
}

# 8b. Put a "The Dream Team" shortcut on the desktop so the user can reopen the dashboard anytime.
#     It points at start-app.ps1, which starts the app only if it is not already running, then opens
#     the dashboard - so one click always lands on a live board, even after a reboot.
try {
  $desktop = [Environment]::GetFolderPath('Desktop')
  if ($desktop -and (Test-Path $desktop)) {
    $lnkPath = Join-Path $desktop 'The Dream Team.lnk'
    $startApp = Join-Path $InstallDir 'app\start-app.ps1'
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startApp`""
    $sc.WorkingDirectory = (Join-Path $InstallDir 'app')
    $sc.WindowStyle = 7
    $sc.Description = 'Open The Dream Team dashboard (starts it first if needed)'
    # Use Microsoft Scout's icon when we can find it, so the shortcut looks the part; otherwise leave the default.
    $iconExe = @(
      (Join-Path $env:ProgramFiles 'Microsoft Scout\Clawpilot\Microsoft Scout.exe'),
      (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Scout\Clawpilot\Microsoft Scout.exe')
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($iconExe) { $sc.IconLocation = "$iconExe,0" }
    $sc.Save()
    Write-Host "[ok] Added a 'The Dream Team' shortcut to your desktop."
  }
} catch {
  Write-Host "[info] Could not create a desktop shortcut (skipped, not a problem): $($_.Exception.Message)" -ForegroundColor Yellow
}

# 9. Print a factual summary of what this run actually did, before any next-steps messaging.
$modelLabel = 'chosen during /daily-flow-setup'
try {
  $autoFile = Join-Path $PkgRoot 'automations\automations.json'
  if (Test-Path $autoFile) {
    $autoJson = Get-Content -Raw $autoFile | ConvertFrom-Json
    if ($autoJson._meta.defaultModel) { $modelLabel = [string]$autoJson._meta.defaultModel + ' (default; confirmed at setup)' }
  }
} catch {}
Write-Host ''
Write-Host '--- Install summary ---' -ForegroundColor Cyan
Write-Host ("  Action:          {0}" -f $(if ($IsUpgrade) { "upgrade $(if ($OldVersion) { "v$OldVersion" } else { 'earlier' }) -> v$NewVersion" } else { "fresh install of v$NewVersion" }))
Write-Host ("  Core contract:   schema {0}, v{1}" -f $CoreCompatibility.ContractSchemaVersion, $CoreCompatibility.ContractVersionText)
Write-Host ("  Overlay:         {0}" -f $(if ($OverlayCompatibility.Overlay) { "$($OverlayCompatibility.Overlay.Id) v$($OverlayCompatibility.Overlay.VersionText) (compatible)" } else { 'none (core-only)' }))
Write-Host ("  Version report:  {0}" -f $VersionReportPath)
Write-Host ("  Install folder:  {0}" -f $InstallDir)
Write-Host ("  Skills:          {0} into {1} Scout skills folder(s)" -f (($installed + $updated | Sort-Object -Unique) -join ', '), $SkillRoots.Count)
Write-Host ("  Dashboard:       http://127.0.0.1:{0}/" -f $port)
Write-Host ("  Documents:       {0}" -f $docRoot)
Write-Host ("  Model:           {0}" -f $modelLabel)
Write-Host ("  Automations:     installed and switched on during /daily-flow-setup")
Write-Host ("  Install log:     {0}" -f (Join-Path $InstallDir 'install.log'))
Write-Host '-----------------------' -ForegroundColor Cyan

# 10. Tell the user what is next. If Scout is finishing setup inline in the same chat (-AgentInline),
#    do NOT tell them to restart or paste a command - Scout handles it and talks to them in chat.
$cmd = '/daily-flow-setup'
if ($AgentInline -and -not $ScoutMissing) {
  Write-Host ''
  Write-Host '===========================================================' -ForegroundColor Green
  Write-Host '  APP INSTALLED - Scout is finishing setup in your chat' -ForegroundColor Green
  Write-Host '===========================================================' -ForegroundColor Green
  Write-Host ''
  Write-Host '   Your dashboard is open. Scout is now switching on your team and'
  Write-Host '   running your first sweep (about 5 to 10 minutes). The board fills'
  Write-Host '   in as it goes. You do not need to restart Scout or type any command.'
  Write-Host ''
  Write-Host '   Tip: a "The Dream Team" shortcut is on your desktop to reopen the dashboard.'
  Write-Host '==========================================================='  -ForegroundColor Green
  return
}
# Otherwise (manual -Auto run, or Scout missing): guide the user through finishing in Scout.
if ($ScoutMissing) {
  # No Scout on this machine: do not pretend to open it or push /daily-flow-setup yet.
  $step1 = 'Install Microsoft Scout on this machine (the dashboard is open, but the team needs Scout). Microsoft employees: get it from your internal aka.ms site, not the public link.'
  $step2 = 'After Scout is installed and open, run the install again so the team skills load into Scout.'
  $step3 = 'Then open Scout, click the chat box, type /daily-flow-setup and press Enter.'
  $copied = $false
} else {
  try { Set-Clipboard -Value $cmd; $copied = $true } catch { $copied = $false }
  $scoutRunning = [bool](Get-Process -Name 'Microsoft Scout' -ErrorAction SilentlyContinue)
  $scoutExe = @(
    (Join-Path $env:ProgramFiles 'Microsoft Scout\Clawpilot\Microsoft Scout.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Scout\Clawpilot\Microsoft Scout.exe')
  ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  $scoutLnk = @(
    (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Microsoft Scout.lnk'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Microsoft Scout.lnk')
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  $pasteOrType = if ($copied) { 'press Ctrl+V, then Enter  (the command is already copied for you)' } else { 'type the command shown below, then press Enter' }
  if ($scoutRunning) {
    # Scout was already open, so it has not loaded the new skills yet - it must be restarted.
    $step1 = 'Close Microsoft Scout completely (quit it), then open it again.'
    $step2 = 'Restarting is required so Scout loads your new team skills.'
    $step3 = "In Scout, click the chat box and $pasteOrType."
  } else {
    # Scout is closed: launch it fresh so it loads the new skills on startup (no restart needed).
    if ($scoutExe) { Start-Process -FilePath $scoutExe } elseif ($scoutLnk) { Start-Process -FilePath $scoutLnk }
    $step1 = 'Microsoft Scout is opening now (it loads your new team skills as it starts).'
    $step2 = 'Wait a moment for it to finish opening.'
    $step3 = "Click the chat box and $pasteOrType."
  }
}
$headline = if ($ScoutMissing) { '  DASHBOARD READY - install Microsoft Scout to switch on the team' } else { '  SETUP IS COMPLETE - one quick step left in Microsoft Scout' }
$headColor = if ($ScoutMissing) { 'Yellow' } else { 'Green' }
Write-Host ''
Write-Host '===========================================================' -ForegroundColor $headColor
Write-Host $headline -ForegroundColor $headColor
Write-Host '===========================================================' -ForegroundColor $headColor
Write-Host ''
Write-Host "   1) $step1"
Write-Host "   2) $step2"
Write-Host "   3) $step3"
Write-Host ''
Write-Host '   The command to run is:'
Write-Host '       /daily-flow-setup' -ForegroundColor Cyan
Write-Host '===========================================================' -ForegroundColor $headColor
if (-not $env:DAILY_FLOW_NO_POPUP) {
  $popupHead = if ($ScoutMissing) { 'Your Daily Flow dashboard is open — but Microsoft Scout is required to switch on the team.' } else { 'Your Daily Flow dashboard is open in your browser.' }
  $popupBody = @"
$popupHead

ONE LAST STEP to switch on your team:

  1) $step1
  2) $step2
  3) $step3

The command to run is:   /daily-flow-setup
"@
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    $owner = New-Object System.Windows.Forms.Form
    $owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Width = 1; $owner.Height = 1; $owner.Opacity = 0; $owner.FormBorderStyle = 'None'; $owner.StartPosition = 'CenterScreen'
    [void]$owner.Show(); [void]$owner.Activate()
    $popupTitle = if ($ScoutMissing) { 'Daily Flow Team - install Microsoft Scout to finish' } else { 'Daily Flow Team - almost done!' }
    [void][System.Windows.Forms.MessageBox]::Show($owner, $popupBody, $popupTitle, [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
    $owner.Close(); $owner.Dispose()
  } catch { }
}