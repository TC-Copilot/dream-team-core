# The Dream Team for Microsoft Scout - Packager
# Author: Shervin Shaffie
#
# Builds a clean, shareable ZIP in .\dist, EXCLUDING all runtime/local data, and refuses
# to produce the ZIP unless verify-clean.ps1 passes.
#     powershell -ExecutionPolicy Bypass -File .\package-share.ps1
#
# Maintainer auto-publish (off by default so end users never push to the author's repo):
#     powershell -ExecutionPolicy Bypass -File .\package-share.ps1 -Publish
# -Publish commits & pushes source to the repo's default branch and creates a new
# GitHub Release for this version with the new ZIP attached. It refuses to modify an existing
# tag, release, or asset. Requires the GitHub CLI (gh)
# authenticated as a user with push access to -Repo.
#
# Before bumping manifest.json's version for a release, see the "Versioning policy" section
# near the top of CHANGELOG.md: routine fixes and additive improvements are PATCH bumps by
# default; MINOR is reserved for explicitly planned milestones; MAJOR is reserved for breaking
# changes. Already-published releases are never renumbered to match the policy retroactively.

param(
  [switch]$Publish,
  [string]$Repo = 'TC-Copilot/dream-team-core',
  [string]$Branch = 'main'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root 'release-guards.ps1')
$OutDir = Join-Path $Root 'dist'
$Version = '0.0.0'
try {
  $mf = Get-Content -Raw (Join-Path $Root 'manifest.json') | ConvertFrom-Json
  if ($mf.version) { $Version = [string]$mf.version }
} catch {}
$Zip = Join-Path $OutDir ("dream-team-core-v{0}.zip" -f $Version)
# Release notes for the GitHub Release body come from the matching CHANGELOG.md section,
# so CHANGELOG.md is the single source of truth (no separate per-version notes files).
$NotesFile = Join-Path ([System.IO.Path]::GetTempPath()) ("dft-relnotes-{0}.md" -f $Version)
Remove-Item -LiteralPath $NotesFile -Force -ErrorAction SilentlyContinue
try {
  $changelog = Get-Content -Raw (Join-Path $Root 'CHANGELOG.md')
  $section = New-Object System.Collections.Generic.List[string]
  $inSection = $false
  foreach ($line in ($changelog -split "`r?`n")) {
    if ($line -match ('^###\s+' + [regex]::Escape($Version) + '\s*$')) { $inSection = $true; continue }
    elseif ($inSection -and $line -match '^#{1,3}\s') { break }
    if ($inSection) { $section.Add($line) }
  }
  $notesBody = ($section -join "`n").Trim()
  if ($notesBody) { [System.IO.File]::WriteAllText($NotesFile, $notesBody) }
} catch {}

# Release consistency gate. The version lives in manifest.json, but it is also stated in
# README.md and needs a matching CHANGELOG section (which is where the GitHub Release notes
# come from). A version bump that misses one of those ships a package that misreports itself,
# so fail the build here rather than publish it.
$readmePath = Join-Path $Root 'README.md'
if (Test-Path $readmePath) {
  $readmeText = Get-Content -Raw $readmePath
  $readmeMatch = [regex]::Match($readmeText, '(?m)^Version\s+([0-9]+\.[0-9]+\.[0-9]+)\s*\.')
  if (-not $readmeMatch.Success) {
    throw "README.md has no 'Version X.Y.Z.' line to check against manifest.json ($Version)."
  }
  if ($readmeMatch.Groups[1].Value -ne $Version) {
    throw ("Version mismatch: manifest.json says {0} but README.md says {1}. Update README.md before publishing." -f $Version, $readmeMatch.Groups[1].Value)
  }
  Write-Host ("[ok] README.md version matches manifest ({0})." -f $Version) -ForegroundColor Green
}
if (-not (Test-Path $NotesFile)) {
  throw ("CHANGELOG.md has no '### {0}' section, so the release would publish without notes. Add it before publishing." -f $Version)
}
Write-Host ("[ok] CHANGELOG.md has a section for {0}." -f $Version) -ForegroundColor Green

# Allowlist of top-level items to ship. Anything not listed is ignored.
$include = @('INSTALL-WITH-SCOUT.md','install.ps1','compatibility.ps1','preflight.ps1','verify-clean.ps1','package-share.ps1','release-guards.ps1','README.md','CHANGELOG.md','LICENSE','manifest.json','.gitignore','app','skills','automations','docs','test')
# Runtime/local data that must never ship, pruned from the staged copy.
$prunePatterns = @('data','dist','__pycache__','*.pyc','*.db','*.db-wal','*.db-shm','*.db.bak*','*.pid','state.json','impact.json','config.json','profile','.writetest','.install-location','.local-token','install.log','app-start.log','app.err.log')

$Stage = Join-Path $env:TEMP ('dft-pkg-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
try {
  foreach ($item in $include) {
    $src = Join-Path $Root $item
    if (Test-Path $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $Stage $item) -Recurse -Force }
  }
  foreach ($pat in $prunePatterns) {
    Get-ChildItem -Path $Stage -Recurse -Force -Filter $pat -ErrorAction SilentlyContinue |
      ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
  }

  # Clean-room gate: the verifier must pass against the staged copy.
  Write-Host 'Running clean-room verification on the staged package...'
  & powershell -ExecutionPolicy Bypass -File (Join-Path $Stage 'verify-clean.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'verify-clean.ps1 FAILED - package not built. Scrub the flagged files and retry.' }

  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
  Get-ChildItem -Path $OutDir -Filter 'dream-team-core*.zip' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $Zip)

  $count = ([System.IO.Compression.ZipFile]::OpenRead($Zip)).Entries.Count
  $size = [math]::Round((Get-Item $Zip).Length / 1KB, 1)
  Write-Host ''
  Write-Host ("Built shareable package: {0} ({1} KB, {2} files)" -f $Zip, $size, $count) -ForegroundColor Green
} finally {
  Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
}

# -------------------------------------------------------------------------------------------------
# Maintainer auto-publish (opt-in). Commits & pushes source, then creates a new immutable Release.
# -------------------------------------------------------------------------------------------------
if ($Publish) {
  Write-Host ''
  Write-Host '=== Auto-publish to GitHub ===' -ForegroundColor Cyan

  # Native CLI tools (gh/git) legitimately write to stderr for non-error signals (e.g.
  # "release not found"). Under ErrorActionPreference=Stop that stderr is promoted to a
  # terminating error, so relax it to Continue here; explicit $LASTEXITCODE checks + throw
  # statements below still catch real failures.
  $savedEAP = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) { throw 'Auto-publish requested but the GitHub CLI (gh) is not installed. Install it or omit -Publish.' }
    & gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Auto-publish requested but gh is not authenticated. Run: gh auth login' }

    Push-Location $Root
    try {
      $tag = "v$Version"
      $assetName = Split-Path -Leaf $Zip
      Assert-PublishTargetAvailable -Repo $Repo -Tag $tag -AssetName $assetName
      Write-Host ("[ok] Confirmed {0} has no existing tag, release, or asset." -f $tag) -ForegroundColor Green

      # 1) Commit & push source changes (if this folder is a git repo with changes).
      if (Test-Path (Join-Path $Root '.git')) {
        & git add -A 2>&1 | Out-Null
        $pending = (& git status --porcelain)
        if ($pending) {
          & git commit -m ("Release v{0}" -f $Version) | Out-Host
          Write-Host ("[ok] Committed source changes for v{0}." -f $Version) -ForegroundColor Green
        } else {
          Write-Host '[ok] No source changes to commit.' -ForegroundColor DarkGray
        }
        $pushOut = (& git push origin ("HEAD:{0}" -f $Branch) 2>&1 | ForEach-Object { [string]$_ })
        $pushOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) { throw "Failed to push the release commit to origin/$Branch." }
      } else {
        throw 'Auto-publish requires a git repository so the release can be tied to an exact commit.'
      }

      # 2) Atomically reserve the new tag, then create the Release from that exact tag.
      $commitSha = (& git rev-parse HEAD 2>&1 | ForEach-Object { [string]$_ }) -join ''
      if ($LASTEXITCODE -ne 0 -or $commitSha -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'Could not resolve the release commit SHA.'
      }
      New-ImmutableReleaseTag -Repo $Repo -Tag $tag -CommitSha $commitSha
      Write-Host ("[ok] Created immutable tag {0} at {1}." -f $tag, $commitSha) -ForegroundColor Green

      # 3) Create the new GitHub Release for this version, with the ZIP asset.
      $title = "The Dream Team for Microsoft Scout $tag"
      $notesArg = @()
      if (Test-Path $NotesFile) { $notesArg = @('--notes-file', $NotesFile) } else { $notesArg = @('--notes', ("Release {0}" -f $tag)) }

      $releaseOut = (& gh release create $tag $Zip --repo $Repo --verify-tag --title $title @notesArg 2>&1 | ForEach-Object { [string]$_ })
      $releaseExitCode = $LASTEXITCODE
      $releaseOut | Out-Host
      if ($releaseExitCode -ne 0) {
        try {
          Remove-UnpublishedReleaseTag -Repo $Repo -Tag $tag -ExpectedCommitSha $commitSha
        } catch {
          throw "Failed to create new release $tag, and safe unpublished-tag cleanup also failed: $($_.Exception.Message)"
        }
        throw "Failed to create new release $tag. Its unpublished tag was safely removed."
      }
      Write-Host ("[ok] Published {0} to https://github.com/{1}/releases/tag/{2}" -f $tag, $Repo, $tag) -ForegroundColor Green
    } finally {
      Pop-Location
    }
  } finally {
    $ErrorActionPreference = $savedEAP
  }
}