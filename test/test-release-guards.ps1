$ErrorActionPreference = 'Stop'
$TestRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $TestRoot
. (Join-Path $Root 'release-guards.ps1')

$failures = New-Object System.Collections.Generic.List[string]

function Assert-True([bool]$Condition, [string]$Name) {
  if ($Condition) { Write-Host "[PASS] $Name" -ForegroundColor Green }
  else { $failures.Add($Name); Write-Host "[FAIL] $Name" -ForegroundColor Red }
}

function Invoke-Guard([scriptblock]$Runner) {
  Assert-PublishTargetAvailable -Repo 'TC-Copilot/dream-team-core' -Tag 'v4.5.20' `
    -AssetName 'dream-team-core-v4.5.20.zip' -Runner $Runner
}

$newTargetCalls = New-Object System.Collections.Generic.List[string]
$newTargetRunner = {
  param($Command, $Arguments)
  $newTargetCalls.Add("$Command $($Arguments -join ' ')")
  if ($Command -eq 'git') { return [pscustomobject]@{ ExitCode = 2; Output = @() } }
  return [pscustomobject]@{ ExitCode = 1; Output = @('release not found') }
}
$newTargetAccepted = $true
try { Invoke-Guard $newTargetRunner } catch { $newTargetAccepted = $false }
Assert-True $newTargetAccepted 'A new tag and release target is accepted'
Assert-True ($newTargetCalls.Count -eq 2) 'Both remote tag and GitHub release are checked'

$existingTagRunner = {
  param($Command, $Arguments)
  return [pscustomobject]@{ ExitCode = 0; Output = @('tag exists') }
}
$existingTagRejected = $false
try { Invoke-Guard $existingTagRunner } catch {
  $existingTagRejected = $_.Exception.Message -match 'tag already exists.*immutable'
}
Assert-True $existingTagRejected 'An existing remote tag is rejected as immutable'

$existingAssetRunner = {
  param($Command, $Arguments)
  if ($Command -eq 'git') { return [pscustomobject]@{ ExitCode = 2; Output = @() } }
  return [pscustomobject]@{
    ExitCode = 0
    Output = @('{"assets":[{"name":"dream-team-core-v4.5.20.zip"}]}')
  }
}
$existingAssetRejected = $false
try { Invoke-Guard $existingAssetRunner } catch {
  $existingAssetRejected = $_.Exception.Message -match 'release already exists and already contains asset.*immutable'
}
Assert-True $existingAssetRejected 'An existing release asset is rejected instead of overwritten'

$lookupFailureRunner = {
  param($Command, $Arguments)
  if ($Command -eq 'git') { return [pscustomobject]@{ ExitCode = 2; Output = @() } }
  return [pscustomobject]@{ ExitCode = 1; Output = @('authentication failed') }
}
$lookupFailureRejected = $false
try { Invoke-Guard $lookupFailureRunner } catch {
  $lookupFailureRejected = $_.Exception.Message -match 'Could not verify whether release'
}
Assert-True $lookupFailureRejected 'A release lookup failure fails closed'

$tagCreateCalls = New-Object System.Collections.Generic.List[string]
$tagCreateRunner = {
  param($Command, $Arguments)
  $tagCreateCalls.Add("$Command $($Arguments -join ' ')")
  return [pscustomobject]@{ ExitCode = 0; Output = @('created') }
}
$tagCreated = $true
try {
  New-ImmutableReleaseTag -Repo 'TC-Copilot/dream-team-core' -Tag 'v4.5.20' `
    -CommitSha ('a' * 40) -Runner $tagCreateRunner
} catch { $tagCreated = $false }
Assert-True $tagCreated 'A new immutable tag ref is created atomically'
Assert-True ($tagCreateCalls[0] -match 'gh api .*git/refs --method POST') 'Tag creation uses the GitHub refs API'

$tagConflictRunner = {
  param($Command, $Arguments)
  return [pscustomobject]@{ ExitCode = 1; Output = @('HTTP 422: Reference already exists') }
}
$tagConflictRejected = $false
try {
  New-ImmutableReleaseTag -Repo 'TC-Copilot/dream-team-core' -Tag 'v4.5.20' `
    -CommitSha ('a' * 40) -Runner $tagConflictRunner
} catch {
  $tagConflictRejected = $_.Exception.Message -match 'refusing to continue'
}
Assert-True $tagConflictRejected 'A concurrent or repeated tag creation fails closed'

$cleanupCalls = New-Object System.Collections.Generic.List[string]
$cleanupRunner = {
  param($Command, $Arguments)
  $call = "$Command $($Arguments -join ' ')"
  $cleanupCalls.Add($call)
  if ($call -match 'release view') {
    return [pscustomobject]@{ ExitCode = 1; Output = @('release not found') }
  }
  if ($call -match '--jq') {
    return [pscustomobject]@{ ExitCode = 0; Output = @('a' * 40) }
  }
  return [pscustomobject]@{ ExitCode = 0; Output = @() }
}
$cleanupSucceeded = $true
try {
  Remove-UnpublishedReleaseTag -Repo 'TC-Copilot/dream-team-core' -Tag 'v4.5.20' `
    -ExpectedCommitSha ('a' * 40) -Runner $cleanupRunner
} catch { $cleanupSucceeded = $false }
Assert-True $cleanupSucceeded 'A failed release can clean up only its unpublished tag'
Assert-True ($cleanupCalls[2] -match '--method DELETE') 'Unpublished tag cleanup deletes the verified ref'

$publishedTagRunner = {
  param($Command, $Arguments)
  return [pscustomobject]@{ ExitCode = 0; Output = @('{"tagName":"v4.5.20"}') }
}
$publishedTagPreserved = $false
try {
  Remove-UnpublishedReleaseTag -Repo 'TC-Copilot/dream-team-core' -Tag 'v4.5.20' `
    -ExpectedCommitSha ('a' * 40) -Runner $publishedTagRunner
} catch {
  $publishedTagPreserved = $_.Exception.Message -match 'release.*exists.*refusing to remove'
}
Assert-True $publishedTagPreserved 'Cleanup never removes a tag for a published release'

$movedTagRunner = {
  param($Command, $Arguments)
  $call = "$Command $($Arguments -join ' ')"
  if ($call -match 'release view') {
    return [pscustomobject]@{ ExitCode = 1; Output = @('release not found') }
  }
  return [pscustomobject]@{ ExitCode = 0; Output = @('b' * 40) }
}
$movedTagPreserved = $false
try {
  Remove-UnpublishedReleaseTag -Repo 'TC-Copilot/dream-team-core' -Tag 'v4.5.20' `
    -ExpectedCommitSha ('a' * 40) -Runner $movedTagRunner
} catch {
  $movedTagPreserved = $_.Exception.Message -match 'no longer points.*refusing to remove'
}
Assert-True $movedTagPreserved 'Cleanup never removes a tag that moved to another commit'

$packager = Get-Content -LiteralPath (Join-Path $Root 'package-share.ps1') -Raw
Assert-True ($packager -notmatch '--clobber') 'Publisher contains no clobber path'
Assert-True ($packager -match 'Assert-PublishTargetAvailable') 'Publisher invokes the immutable target guard'
Assert-True ($packager -match 'New-ImmutableReleaseTag') 'Publisher atomically reserves the immutable tag'
Assert-True ($packager -match 'release create.*--verify-tag') 'Release creation requires the pre-created tag'

if ($failures.Count -gt 0) {
  throw "$($failures.Count) release guard test(s) failed: $($failures -join ', ')"
}
