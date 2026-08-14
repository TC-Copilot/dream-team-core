$ErrorActionPreference = 'Stop'
$TestRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $TestRoot
. (Join-Path $Root 'compatibility.ps1')

$temp = Join-Path $env:TEMP ('dream-team-compat-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $temp | Out-Null
$failures = New-Object System.Collections.Generic.List[string]

function Assert-True([bool]$Condition, [string]$Name) {
  if ($Condition) { Write-Host "[PASS] $Name" -ForegroundColor Green }
  else { $failures.Add($Name); Write-Host "[FAIL] $Name" -ForegroundColor Red }
}

function Write-OverlayManifest(
  [string]$Path,
  [string]$CoreMin,
  [string]$CoreMax,
  [int]$ContractSchema = 1,
  [string]$ContractMin = '1.0.0',
  [string]$ContractMax = '2.0.0'
) {
  [ordered]@{
    schemaVersion = 1
    id = 'example.external-overlay'
    displayName = 'Example external overlay'
    version = '2.3.4'
    requiresCore = [ordered]@{
      contractSchemaVersion = $ContractSchema
      contractVersion = [ordered]@{ minInclusive = $ContractMin; maxExclusive = $ContractMax }
      coreVersion = [ordered]@{ minInclusive = $CoreMin; maxExclusive = $CoreMax }
    }
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

try {
  $core = Get-CoreCompatibilityInfo -PackageRoot $Root
  $installDir = Join-Path $temp 'install'
  New-Item -ItemType Directory -Force -Path (Join-Path $installDir 'app') | Out-Null

  $coreOnly = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir
  Assert-True ($coreOnly.Mode -eq 'core-only' -and $null -eq $coreOnly.Overlay) 'Public install resolves to core-only when no overlay is registered'

  $compatiblePath = Join-Path $temp 'compatible.json'
  Write-OverlayManifest $compatiblePath '4.5.16' '4.6.0'
  $compatible = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath $compatiblePath -OverlayRequested
  Assert-True ($compatible.Mode -eq 'overlay' -and $compatible.Overlay.Id -eq 'example.external-overlay') 'Overlay metadata accepts the released >=4.5.16,<4.6.0 core range'

  $nextMinorCore = $core | Select-Object *
  $nextMinorCore.Version = [version]'4.6.0'
  $nextMinorCore.VersionText = '4.6.0'
  $exclusiveUpperBoundFailed = $false
  try { $null = Resolve-OverlayCompatibility -Core $nextMinorCore -InstallDir $installDir -OverlayManifestPath $compatiblePath -OverlayRequested }
  catch { $exclusiveUpperBoundFailed = ($_.Exception.Message -match 'does not support core v4\.6\.0') }
  Assert-True $exclusiveUpperBoundFailed 'Overlay metadata rejects the 4.6.0 exclusive core-version upper bound'

  $contractMismatchPath = Join-Path $temp 'contract-mismatch.json'
  Write-OverlayManifest $contractMismatchPath '4.5.16' '4.6.0' 1 '1.0.1' '2.0.0'
  $contractMismatchFailed = $false
  try { $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath $contractMismatchPath -OverlayRequested }
  catch { $contractMismatchFailed = ($_.Exception.Message -match 'does not support core contract v1\.0\.0') }
  Assert-True $contractMismatchFailed 'Overlay metadata requiring a different core contract version fails closed'

  $missingFailed = $false
  try { $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath (Join-Path $temp 'missing.json') -OverlayRequested }
  catch { $missingFailed = ($_.Exception.Message -match 'required but missing') }
  Assert-True $missingFailed 'Requested overlay with missing metadata fails closed'

  $malformedSchemaPath = Join-Path $temp 'malformed-schema.json'
  Write-OverlayManifest $malformedSchemaPath '4.5.16' '4.6.0'
  $malformed = Get-Content -LiteralPath $malformedSchemaPath -Raw | ConvertFrom-Json
  $malformed.schemaVersion = $true
  $malformed | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $malformedSchemaPath -Encoding UTF8
  $malformedSchemaFailed = $false
  try { $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath $malformedSchemaPath -OverlayRequested }
  catch { $malformedSchemaFailed = ($_.Exception.Message -match 'must be an integer JSON number') }
  Assert-True $malformedSchemaFailed 'Malformed overlay schema values fail closed instead of coercing to an integer'

  Copy-Item -LiteralPath $compatiblePath -Destination (Join-Path $installDir 'overlay-manifest.json')
  $registered = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir
  Assert-True ($registered.Source -eq 'registered') 'Registered overlay metadata is automatically rechecked on core update'

  $reportPath = Write-InstalledVersionReport -InstallDir $installDir -Core $core -Compatibility $compatible
  $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
  Assert-True ($report.core.version -eq $core.VersionText -and $report.overlay.version -eq '2.3.4' -and $report.compatibility.status -eq 'compatible') 'Version report includes verified core and overlay versions'
} finally {
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) {
  throw "$($failures.Count) compatibility test(s) failed: $($failures -join ', ')"
}
