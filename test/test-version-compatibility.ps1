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

function Write-OverlayManifest([string]$Path, [string]$CoreMin, [string]$CoreMax, [int]$ContractSchema = 1) {
  [ordered]@{
    schemaVersion = 1
    id = 'example.external-overlay'
    displayName = 'Example external overlay'
    version = '2.3.4'
    requiresCore = [ordered]@{
      contractSchemaVersion = $ContractSchema
      contractVersion = [ordered]@{ minInclusive = '1.0.0'; maxExclusive = '2.0.0' }
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
  Write-OverlayManifest $compatiblePath '4.5.0' '5.0.0'
  $compatible = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath $compatiblePath -OverlayRequested
  Assert-True ($compatible.Mode -eq 'overlay' -and $compatible.Overlay.Id -eq 'example.external-overlay') 'Compatible overlay metadata is accepted'

  $incompatiblePath = Join-Path $temp 'incompatible.json'
  Write-OverlayManifest $incompatiblePath '5.0.0' '6.0.0'
  $incompatibleFailed = $false
  try { $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath $incompatiblePath -OverlayRequested }
  catch { $incompatibleFailed = ($_.Exception.Message -match 'does not support core v') }
  Assert-True $incompatibleFailed 'Incompatible overlay metadata fails closed'

  $missingFailed = $false
  try { $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir -OverlayManifestPath (Join-Path $temp 'missing.json') -OverlayRequested }
  catch { $missingFailed = ($_.Exception.Message -match 'required but missing') }
  Assert-True $missingFailed 'Requested overlay with missing metadata fails closed'

  $malformedSchemaPath = Join-Path $temp 'malformed-schema.json'
  Write-OverlayManifest $malformedSchemaPath '4.5.0' '5.0.0'
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
