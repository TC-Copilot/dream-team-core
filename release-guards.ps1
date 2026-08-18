$ErrorActionPreference = 'Stop'

function Invoke-ReleaseGuardCommand {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string[]]$Arguments
  )

  $output = (& $Command @Arguments 2>&1 | ForEach-Object { [string]$_ })
  return [pscustomobject]@{
    ExitCode = $LASTEXITCODE
    Output = @($output)
  }
}

function Assert-PublishTargetAvailable {
  param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$AssetName,
    [scriptblock]$Runner = ${function:Invoke-ReleaseGuardCommand}
  )

  $tagResult = & $Runner 'git' @('ls-remote', '--exit-code', '--tags', "https://github.com/$Repo.git", "refs/tags/$Tag")
  if ($tagResult.ExitCode -eq 0) {
    throw "Refusing to publish $Tag because that tag already exists. Published tags are immutable; increment SemVer."
  }
  if ($tagResult.ExitCode -ne 2) {
    throw "Could not verify whether tag $Tag exists: $($tagResult.Output -join [Environment]::NewLine)"
  }

  $releaseResult = & $Runner 'gh' @('release', 'view', $Tag, '--repo', $Repo, '--json', 'assets')
  if ($releaseResult.ExitCode -eq 0) {
    $assetDetail = ''
    try {
      $release = ($releaseResult.Output -join [Environment]::NewLine) | ConvertFrom-Json
      if (@($release.assets.name) -contains $AssetName) {
        $assetDetail = " and already contains asset $AssetName"
      }
    } catch {}
    throw "Refusing to publish $Tag because that release already exists$assetDetail. Published releases and assets are immutable; increment SemVer."
  }

  $releaseError = $releaseResult.Output -join [Environment]::NewLine
  if ($releaseError -notmatch '(?i)release not found|HTTP 404') {
    throw "Could not verify whether release $Tag exists: $releaseError"
  }
}

function New-ImmutableReleaseTag {
  param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$CommitSha,
    [scriptblock]$Runner = ${function:Invoke-ReleaseGuardCommand}
  )

  $result = & $Runner 'gh' @(
    'api', "repos/$Repo/git/refs", '--method', 'POST',
    '-f', "ref=refs/tags/$Tag",
    '-f', "sha=$CommitSha"
  )
  if ($result.ExitCode -ne 0) {
    throw "Failed to create immutable tag $Tag. It may already exist; refusing to continue: $($result.Output -join [Environment]::NewLine)"
  }
}

function Remove-UnpublishedReleaseTag {
  param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$ExpectedCommitSha,
    [scriptblock]$Runner = ${function:Invoke-ReleaseGuardCommand}
  )

  $releaseResult = & $Runner 'gh' @('release', 'view', $Tag, '--repo', $Repo, '--json', 'tagName')
  if ($releaseResult.ExitCode -eq 0) {
    throw "Release $Tag now exists; refusing to remove its immutable tag."
  }
  $releaseError = $releaseResult.Output -join [Environment]::NewLine
  if ($releaseError -notmatch '(?i)release not found|HTTP 404') {
    throw "Could not verify that release $Tag is absent; refusing to remove its tag: $releaseError"
  }

  $refPath = "repos/$Repo/git/ref/tags/$Tag"
  $refResult = & $Runner 'gh' @('api', $refPath, '--jq', '.object.sha')
  if ($refResult.ExitCode -ne 0) {
    throw "Could not verify tag $Tag before cleanup: $($refResult.Output -join [Environment]::NewLine)"
  }
  $actualCommitSha = ($refResult.Output -join '').Trim()
  if ($actualCommitSha -ne $ExpectedCommitSha) {
    throw "Tag $Tag no longer points to the release commit; refusing to remove it."
  }

  $deleteResult = & $Runner 'gh' @('api', $refPath, '--method', 'DELETE')
  if ($deleteResult.ExitCode -ne 0) {
    throw "Could not remove unpublished tag ${Tag}: $($deleteResult.Output -join [Environment]::NewLine)"
  }
}
