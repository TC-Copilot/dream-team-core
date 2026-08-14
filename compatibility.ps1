# Provider-neutral core/overlay compatibility contract.
#
# External overlays may dot-source this file to validate their metadata before changing an install.
# The public installer uses the same functions and remains core-only unless an overlay manifest is
# explicitly supplied or was registered by an earlier overlay-aware install.

$script:CoreContractSchemaVersion = 1
$script:OverlayManifestSchemaVersion = 1

function Get-CompatibilityProperty {
  param(
    [Parameter(Mandatory = $true)]$InputObject,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Context
  )
  if ($null -eq $InputObject) { throw "$Context is missing." }
  $property = $InputObject.PSObject.Properties[$Name]
  if ($null -eq $property -or $null -eq $property.Value) { throw "$Context.$Name is required." }
  return $property.Value
}

function ConvertTo-CompatibilityVersion {
  param(
    [Parameter(Mandatory = $true)]$Value,
    [Parameter(Mandatory = $true)][string]$Field
  )
  $text = ([string]$Value).Trim()
  if ($text -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "$Field must be a three-part numeric version (for example, 1.2.3); got '$text'."
  }
  return [version]$text
}

function ConvertTo-CompatibilitySchemaVersion {
  param(
    [Parameter(Mandatory = $true)]$Value,
    [Parameter(Mandatory = $true)][string]$Field
  )
  $integerTypes = @([byte], [sbyte], [int16], [uint16], [int32], [uint32], [int64], [uint64])
  if ($integerTypes -notcontains $Value.GetType()) {
    throw "$Field must be an integer JSON number."
  }
  $numericValue = [decimal]$Value
  if ($numericValue -lt 0 -or $numericValue -gt [int]::MaxValue) {
    throw "$Field is outside the supported integer range."
  }
  return [int]$Value
}

function Get-CoreCompatibilityInfo {
  param([Parameter(Mandatory = $true)][string]$PackageRoot)

  $manifestPath = Join-Path $PackageRoot 'manifest.json'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Core manifest is missing: $manifestPath"
  }
  try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Core manifest could not be read: $($_.Exception.Message)"
  }

  $name = ([string](Get-CompatibilityProperty $manifest 'name' 'manifest')).Trim()
  if (-not $name) { throw 'manifest.name must not be empty.' }
  $versionText = ([string](Get-CompatibilityProperty $manifest 'version' 'manifest')).Trim()
  $version = ConvertTo-CompatibilityVersion $versionText 'manifest.version'
  $contract = Get-CompatibilityProperty $manifest 'coreContract' 'manifest'
  $contractSchema = ConvertTo-CompatibilitySchemaVersion `
    (Get-CompatibilityProperty $contract 'schemaVersion' 'manifest.coreContract') 'manifest.coreContract.schemaVersion'
  if ($contractSchema -ne $script:CoreContractSchemaVersion) {
    throw "Unsupported core contract schema version $contractSchema; this installer supports $script:CoreContractSchemaVersion."
  }
  $contractVersionText = ([string](Get-CompatibilityProperty $contract 'version' 'manifest.coreContract')).Trim()
  $contractVersion = ConvertTo-CompatibilityVersion $contractVersionText 'manifest.coreContract.version'
  $overlaySchema = ConvertTo-CompatibilitySchemaVersion `
    (Get-CompatibilityProperty $contract 'overlayManifestSchemaVersion' 'manifest.coreContract') 'manifest.coreContract.overlayManifestSchemaVersion'
  if ($overlaySchema -ne $script:OverlayManifestSchemaVersion) {
    throw "Unsupported overlay manifest schema version $overlaySchema; this installer supports $script:OverlayManifestSchemaVersion."
  }

  return [pscustomobject]@{
    Name = $name
    Version = $version
    VersionText = $versionText
    ContractSchemaVersion = $contractSchema
    ContractVersion = $contractVersion
    ContractVersionText = $contractVersionText
    OverlayManifestSchemaVersion = $overlaySchema
  }
}

function Test-VersionInCompatibilityRange {
  param(
    [Parameter(Mandatory = $true)][version]$Actual,
    [Parameter(Mandatory = $true)]$Range,
    [Parameter(Mandatory = $true)][string]$Field
  )
  $minimum = ConvertTo-CompatibilityVersion (Get-CompatibilityProperty $Range 'minInclusive' $Field) "$Field.minInclusive"
  $maximum = ConvertTo-CompatibilityVersion (Get-CompatibilityProperty $Range 'maxExclusive' $Field) "$Field.maxExclusive"
  if ($minimum -ge $maximum) { throw "$Field must have minInclusive lower than maxExclusive." }
  return ($Actual -ge $minimum -and $Actual -lt $maximum)
}

function Assert-OverlayCompatibility {
  param(
    [Parameter(Mandatory = $true)]$Core,
    [Parameter(Mandatory = $true)][string]$OverlayManifestPath
  )

  if (-not (Test-Path -LiteralPath $OverlayManifestPath -PathType Leaf)) {
    throw "Overlay metadata is required but missing: $OverlayManifestPath"
  }
  try {
    $overlay = Get-Content -LiteralPath $OverlayManifestPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Overlay metadata could not be read: $($_.Exception.Message)"
  }

  $schema = ConvertTo-CompatibilitySchemaVersion `
    (Get-CompatibilityProperty $overlay 'schemaVersion' 'overlay') 'overlay.schemaVersion'
  if ($schema -ne $Core.OverlayManifestSchemaVersion) {
    throw "Overlay manifest schema $schema is incompatible with required schema $($Core.OverlayManifestSchemaVersion)."
  }
  $id = ([string](Get-CompatibilityProperty $overlay 'id' 'overlay')).Trim()
  if ($id -notmatch '^[a-z0-9][a-z0-9._-]{1,63}$') {
    throw "overlay.id must be 2-64 lowercase letters, numbers, dots, underscores, or hyphens; got '$id'."
  }
  $displayName = ([string](Get-CompatibilityProperty $overlay 'displayName' 'overlay')).Trim()
  if (-not $displayName) { throw 'overlay.displayName must not be empty.' }
  $overlayVersionText = ([string](Get-CompatibilityProperty $overlay 'version' 'overlay')).Trim()
  $null = ConvertTo-CompatibilityVersion $overlayVersionText 'overlay.version'
  $requires = Get-CompatibilityProperty $overlay 'requiresCore' 'overlay'
  $requiredSchema = ConvertTo-CompatibilitySchemaVersion `
    (Get-CompatibilityProperty $requires 'contractSchemaVersion' 'overlay.requiresCore') 'overlay.requiresCore.contractSchemaVersion'
  if ($requiredSchema -ne $Core.ContractSchemaVersion) {
    throw "Overlay '$id' requires core contract schema $requiredSchema, but this core provides $($Core.ContractSchemaVersion)."
  }
  $contractRange = Get-CompatibilityProperty $requires 'contractVersion' 'overlay.requiresCore'
  if (-not (Test-VersionInCompatibilityRange $Core.ContractVersion $contractRange 'overlay.requiresCore.contractVersion')) {
    throw "Overlay '$id' v$overlayVersionText does not support core contract v$($Core.ContractVersionText)."
  }
  $coreRange = Get-CompatibilityProperty $requires 'coreVersion' 'overlay.requiresCore'
  if (-not (Test-VersionInCompatibilityRange $Core.Version $coreRange 'overlay.requiresCore.coreVersion')) {
    throw "Overlay '$id' v$overlayVersionText does not support core v$($Core.VersionText)."
  }

  return [pscustomobject]@{
    Id = $id
    DisplayName = $displayName
    VersionText = $overlayVersionText
    ManifestSchemaVersion = $schema
    ManifestPath = (Resolve-Path -LiteralPath $OverlayManifestPath).Path
  }
}

function Resolve-OverlayCompatibility {
  param(
    [Parameter(Mandatory = $true)]$Core,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$OverlayManifestPath,
    [switch]$OverlayRequested
  )

  $registeredPath = Join-Path $InstallDir 'overlay-manifest.json'
  if ($OverlayRequested) {
    if ([string]::IsNullOrWhiteSpace($OverlayManifestPath)) {
      throw 'Overlay metadata was requested but no -OverlayManifestPath value was supplied.'
    }
    $overlay = Assert-OverlayCompatibility -Core $Core -OverlayManifestPath $OverlayManifestPath
    return [pscustomobject]@{ Mode = 'overlay'; Source = 'explicit'; Overlay = $overlay; RegisteredPath = $registeredPath }
  }
  if (Test-Path -LiteralPath $registeredPath -PathType Leaf) {
    $overlay = Assert-OverlayCompatibility -Core $Core -OverlayManifestPath $registeredPath
    return [pscustomobject]@{ Mode = 'overlay'; Source = 'registered'; Overlay = $overlay; RegisteredPath = $registeredPath }
  }
  return [pscustomobject]@{ Mode = 'core-only'; Source = 'none'; Overlay = $null; RegisteredPath = $registeredPath }
}

function Write-InstalledVersionReport {
  param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)]$Core,
    [Parameter(Mandatory = $true)]$Compatibility
  )

  $overlayReport = $null
  if ($Compatibility.Overlay) {
    $overlayReport = [ordered]@{
      id = $Compatibility.Overlay.Id
      displayName = $Compatibility.Overlay.DisplayName
      version = $Compatibility.Overlay.VersionText
      manifestSchemaVersion = $Compatibility.Overlay.ManifestSchemaVersion
    }
  }
  $report = [ordered]@{
    schemaVersion = 1
    core = [ordered]@{
      name = $Core.Name
      version = $Core.VersionText
      contractSchemaVersion = $Core.ContractSchemaVersion
      contractVersion = $Core.ContractVersionText
    }
    overlay = $overlayReport
    compatibility = [ordered]@{
      status = $(if ($Compatibility.Overlay) { 'compatible' } else { 'core-only' })
      verifiedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
  }
  $reportPath = Join-Path $InstallDir 'app\.version-report.json'
  $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
  return $reportPath
}
