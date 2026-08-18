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
  [string]$PayloadRoot,
  [string]$CoreMin,
  [string]$CoreMax,
  [int]$ContractSchema = 1,
  [string]$ContractVersion = '1.0.0'
) {
  $declaredPayloadRoot = 'overlay'
  $payloadRelativePath = 'provider.txt'
  $payloadPath = Join-Path (Join-Path $PayloadRoot $declaredPayloadRoot) $payloadRelativePath
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $payloadPath) | Out-Null
  Set-Content -LiteralPath $payloadPath -Value 'verified overlay payload' -Encoding ASCII
  $payloadHash = (Get-FileHash -LiteralPath $payloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
  [ordered]@{
    schemaVersion = 2
    id = 'example.external-overlay'
    displayName = 'Example external overlay'
    version = '1.0.0'
    requiresCore = [ordered]@{
      contractSchemaVersion = $ContractSchema
      contractVersion = $ContractVersion
      coreVersionRange = [ordered]@{ minInclusive = $CoreMin; maxExclusive = $CoreMax }
    }
    integrity = [ordered]@{
      root = $declaredPayloadRoot
      payload = @(
        [ordered]@{
          path = $payloadRelativePath
          sha256 = $payloadHash
        }
      )
    }
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Copy-CoreWithVersion($Core, [string]$VersionText) {
  return [pscustomobject]@{
    Name = $Core.Name
    Version = [version]$VersionText
    VersionText = $VersionText
    ContractSchemaVersion = $Core.ContractSchemaVersion
    ContractVersion = $Core.ContractVersion
    ContractVersionText = $Core.ContractVersionText
    OverlayManifestSchemaVersion = $Core.OverlayManifestSchemaVersion
  }
}

try {
  $core = Get-CoreCompatibilityInfo -PackageRoot $Root
  Assert-True ($core.BuildRevision -eq '20260818.6') 'Core build revision is parsed separately from semantic compatibility version'
  $installDir = Join-Path $temp 'install'
  New-Item -ItemType Directory -Force -Path (Join-Path $installDir 'app') | Out-Null

  $coreOnly = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir
  Assert-True ($coreOnly.Mode -eq 'core-only' -and $null -eq $coreOnly.Overlay) 'Public install resolves to core-only when no overlay is registered'

  $compatibleRoot = Join-Path $temp 'compatible'
  New-Item -ItemType Directory -Force -Path $compatibleRoot | Out-Null
  $compatiblePath = Join-Path $compatibleRoot 'overlay-manifest.json'
  Write-OverlayManifest $compatiblePath $compatibleRoot '4.5.16' '4.6.0'
  $compatibleHash = (Get-FileHash -LiteralPath $compatiblePath -Algorithm SHA256).Hash
  $compatible = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
    -OverlayManifestPath $compatiblePath -OverlayManifestSha256 $compatibleHash `
    -OverlayPayloadRoot $compatibleRoot -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  $compatibleAccepted = $compatible.Mode -eq 'overlay' -and $compatible.Overlay.Id -eq 'example.external-overlay' `
    -and $compatible.Overlay.PayloadFileCount -eq 1
  Assert-True $compatibleAccepted 'Overlay 1.0.0 accepts the current compatible 4.5.x core and verified payload'

  $minimumCore = Copy-CoreWithVersion $core '4.5.16'
  $latestPatchCore = Copy-CoreWithVersion $core '4.5.99'
  $minimumCompatible = Assert-OverlayCompatibility -Core $minimumCore -OverlayManifestPath $compatiblePath `
    -ExpectedOverlayId 'example.external-overlay' -ExpectedManifestSha256 $compatibleHash -PayloadRoot $compatibleRoot
  $latestPatchCompatible = Assert-OverlayCompatibility -Core $latestPatchCore -OverlayManifestPath $compatiblePath `
    -ExpectedOverlayId 'example.external-overlay' -ExpectedManifestSha256 $compatibleHash -PayloadRoot $compatibleRoot
  Assert-True ($minimumCompatible.Id -eq 'example.external-overlay' -and $latestPatchCompatible.Id -eq 'example.external-overlay') `
    'Overlay 1.0.0 supports its declared v4.5.16 through latest 4.5.x range without an exact core ZIP pairing'

  $outsideRangeFailed = $false
  try {
    $outsideRangeCore = Copy-CoreWithVersion $core '4.6.0'
    $null = Assert-OverlayCompatibility -Core $outsideRangeCore -OverlayManifestPath $compatiblePath `
      -ExpectedOverlayId 'example.external-overlay' -ExpectedManifestSha256 $compatibleHash -PayloadRoot $compatibleRoot
  } catch { $outsideRangeFailed = ($_.Exception.Message -match 'does not support core v4\.6\.0') }
  Assert-True $outsideRangeFailed 'Overlay coreVersionRange rejects a core outside its exclusive upper bound'

  $wrongIdentityFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath $compatiblePath -OverlayManifestSha256 $compatibleHash `
      -OverlayPayloadRoot $compatibleRoot -ExpectedOverlayId 'wrong.provider' -OverlayRequested
  } catch { $wrongIdentityFailed = ($_.Exception.Message -match 'does not match expected overlay identity') }
  Assert-True $wrongIdentityFailed 'Wrong overlay provider identity fails closed'

  $wrongContractVersionRoot = Join-Path $temp 'wrong-contract-version'
  New-Item -ItemType Directory -Force -Path $wrongContractVersionRoot | Out-Null
  $wrongContractVersionPath = Join-Path $wrongContractVersionRoot 'overlay-manifest.json'
  Write-OverlayManifest $wrongContractVersionPath $wrongContractVersionRoot '4.5.16' '4.6.0' 1 '1.0.1'
  $wrongContractVersionHash = (Get-FileHash -LiteralPath $wrongContractVersionPath -Algorithm SHA256).Hash
  $wrongContractVersionFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath $wrongContractVersionPath -OverlayManifestSha256 $wrongContractVersionHash `
      -OverlayPayloadRoot $wrongContractVersionRoot -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  } catch { $wrongContractVersionFailed = ($_.Exception.Message -match 'does not support core contract') }
  Assert-True $wrongContractVersionFailed 'Overlay contract version must exactly match the supported core contract version'

  $tamperedManifestRoot = Join-Path $temp 'tampered-manifest'
  New-Item -ItemType Directory -Force -Path $tamperedManifestRoot | Out-Null
  $tamperedManifestPath = Join-Path $tamperedManifestRoot 'overlay-manifest.json'
  Write-OverlayManifest $tamperedManifestPath $tamperedManifestRoot '4.5.16' '4.6.0'
  $untamperedManifestHash = (Get-FileHash -LiteralPath $tamperedManifestPath -Algorithm SHA256).Hash
  Add-Content -LiteralPath $tamperedManifestPath -Value ' '
  $tamperedManifestFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath $tamperedManifestPath -OverlayManifestSha256 $untamperedManifestHash `
      -OverlayPayloadRoot $tamperedManifestRoot -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  } catch { $tamperedManifestFailed = ($_.Exception.Message -match 'overlay manifest SHA-256 does not match') }
  Assert-True $tamperedManifestFailed 'Overlay manifest digest tampering fails closed'

  $tamperedPayloadRoot = Join-Path $temp 'tampered-payload'
  New-Item -ItemType Directory -Force -Path $tamperedPayloadRoot | Out-Null
  $tamperedPayloadPath = Join-Path $tamperedPayloadRoot 'overlay-manifest.json'
  Write-OverlayManifest $tamperedPayloadPath $tamperedPayloadRoot '4.5.16' '4.6.0'
  $tamperedPayloadHash = (Get-FileHash -LiteralPath $tamperedPayloadPath -Algorithm SHA256).Hash
  Set-Content -LiteralPath (Join-Path $tamperedPayloadRoot 'overlay\provider.txt') -Value 'tampered payload' -Encoding ASCII
  $tamperedPayloadFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath $tamperedPayloadPath -OverlayManifestSha256 $tamperedPayloadHash `
      -OverlayPayloadRoot $tamperedPayloadRoot -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  } catch { $tamperedPayloadFailed = ($_.Exception.Message -match 'payload\[provider\.txt\] SHA-256 does not match') }
  Assert-True $tamperedPayloadFailed 'Overlay payload digest tampering fails closed'

  $undeclaredPayloadRoot = Join-Path $temp 'undeclared-payload'
  New-Item -ItemType Directory -Force -Path $undeclaredPayloadRoot | Out-Null
  $undeclaredPayloadPath = Join-Path $undeclaredPayloadRoot 'overlay-manifest.json'
  Write-OverlayManifest $undeclaredPayloadPath $undeclaredPayloadRoot '4.5.16' '4.6.0'
  $undeclaredPayloadHash = (Get-FileHash -LiteralPath $undeclaredPayloadPath -Algorithm SHA256).Hash
  Set-Content -LiteralPath (Join-Path $undeclaredPayloadRoot 'overlay\extra.txt') -Value 'undeclared payload' -Encoding ASCII
  $undeclaredPayloadFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath $undeclaredPayloadPath -OverlayManifestSha256 $undeclaredPayloadHash `
      -OverlayPayloadRoot $undeclaredPayloadRoot -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  } catch { $undeclaredPayloadFailed = ($_.Exception.Message -match 'contains an undeclared file') }
  Assert-True $undeclaredPayloadFailed 'Undeclared overlay payload files fail closed'

  $malformedSchemaRoot = Join-Path $temp 'malformed-schema'
  New-Item -ItemType Directory -Force -Path $malformedSchemaRoot | Out-Null
  $malformedSchemaPath = Join-Path $malformedSchemaRoot 'overlay-manifest.json'
  Write-OverlayManifest $malformedSchemaPath $malformedSchemaRoot '4.5.16' '4.6.0'
  $malformed = Get-Content -LiteralPath $malformedSchemaPath -Raw | ConvertFrom-Json
  $malformed.schemaVersion = $true
  $malformed | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $malformedSchemaPath -Encoding UTF8
  $malformedSchemaHash = (Get-FileHash -LiteralPath $malformedSchemaPath -Algorithm SHA256).Hash
  $malformedSchemaFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath $malformedSchemaPath -OverlayManifestSha256 $malformedSchemaHash `
      -OverlayPayloadRoot $malformedSchemaRoot -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  } catch { $malformedSchemaFailed = ($_.Exception.Message -match 'must be an integer JSON number') }
  Assert-True $malformedSchemaFailed 'Malformed overlay schema values fail closed instead of coercing to an integer'

  $missingFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestPath (Join-Path $temp 'missing.json') -OverlayManifestSha256 $compatibleHash `
      -ExpectedOverlayId 'example.external-overlay' -OverlayRequested
  } catch { $missingFailed = ($_.Exception.Message -match 'required but missing') }
  Assert-True $missingFailed 'Requested overlay with missing metadata fails closed'

  $partialArgumentsFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir `
      -OverlayManifestSha256 $compatibleHash
  } catch { $partialArgumentsFailed = ($_.Exception.Message -match 'arguments were supplied without an explicit overlay request') }
  Assert-True $partialArgumentsFailed 'Partial overlay integration arguments cannot bypass the compatibility gate'

  Copy-Item -LiteralPath $compatiblePath -Destination (Join-Path $installDir 'overlay-manifest.json')
  Copy-Item -LiteralPath (Join-Path $compatibleRoot 'overlay') -Destination (Join-Path $installDir 'overlay') -Recurse
  Write-RegisteredOverlayIntegrity -Path (Join-Path $installDir 'overlay-integrity.json') -Overlay $compatible.Overlay
  $registered = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir
  Assert-True ($registered.Source -eq 'registered' -and $registered.Overlay.ManifestSha256 -eq $compatible.Overlay.ManifestSha256) `
    'Registered overlay metadata, identity, manifest hash, and payload hashes are rechecked on core update'

  Remove-Item -LiteralPath (Join-Path $installDir 'overlay-manifest.json') -Force
  $orphanedIntegrityFailed = $false
  try {
    $null = Resolve-OverlayCompatibility -Core $core -InstallDir $installDir
  } catch { $orphanedIntegrityFailed = ($_.Exception.Message -match 'Registered overlay metadata is incomplete') }
  Assert-True $orphanedIntegrityFailed 'Orphaned registered integrity metadata cannot bypass overlay revalidation'

  $reportPath = Write-InstalledVersionReport -InstallDir $installDir -Core $core -Compatibility $compatible
  $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
  Assert-True ($report.core.version -eq $core.VersionText -and $report.overlay.version -eq '1.0.0' `
    -and $report.overlay.manifestSha256 -eq $compatible.Overlay.ManifestSha256 -and $report.compatibility.status -eq 'compatible') `
    'Version report includes verified core, overlay, and manifest integrity details'
} finally {
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) {
  throw "$($failures.Count) compatibility test(s) failed: $($failures -join ', ')"
}
