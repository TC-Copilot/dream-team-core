# Provider-neutral core/overlay compatibility contract.
#
# External overlays may dot-source this file to validate their metadata before changing an install.
# The public installer uses the same functions and remains core-only unless an overlay manifest is
# explicitly supplied or was registered by an earlier overlay-aware install.

$script:CoreContractSchemaVersion = 1
$script:OverlayManifestSchemaVersion = 2
$script:OverlayIntegritySchemaVersion = 1

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

function ConvertTo-CompatibilitySha256 {
  param(
    [Parameter(Mandatory = $true)]$Value,
    [Parameter(Mandatory = $true)][string]$Field
  )
  $text = ([string]$Value).Trim()
  if ($text -notmatch '^[0-9a-fA-F]{64}$') {
    throw "$Field must be a 64-character SHA-256 hexadecimal digest."
  }
  return $text.ToLowerInvariant()
}

function Get-CompatibilityFileSha256 {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Field
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Field is missing or is not a file: $Path"
  }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}

function Assert-CompatibilityFileSha256 {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Expected,
    [Parameter(Mandatory = $true)][string]$Field
  )
  $expectedHash = ConvertTo-CompatibilitySha256 $Expected "$Field.sha256"
  $actualHash = Get-CompatibilityFileSha256 $Path $Field
  if ($actualHash -ne $expectedHash) {
    throw "$Field SHA-256 does not match its declared digest."
  }
  return $actualHash
}

function ConvertTo-OverlayIdentifier {
  param(
    [Parameter(Mandatory = $true)]$Value,
    [Parameter(Mandatory = $true)][string]$Field
  )
  $id = ([string]$Value).Trim()
  if ($id -notmatch '^[a-z0-9][a-z0-9._-]{1,63}$') {
    throw "$Field must be 2-64 lowercase letters, numbers, dots, underscores, or hyphens; got '$id'."
  }
  return $id
}

function Assert-OverlayPayloadIntegrity {
  param(
    [Parameter(Mandatory = $true)]$Integrity,
    [Parameter(Mandatory = $true)][string]$PayloadRoot
  )

  if (-not (Test-Path -LiteralPath $PayloadRoot -PathType Container)) {
    throw "overlay payload root is missing or is not a directory: $PayloadRoot"
  }
  $payloadRootRelative = ([string](Get-CompatibilityProperty $Integrity 'root' 'overlay.integrity')).Trim().Replace('\', '/')
  if (-not $payloadRootRelative -or $payloadRootRelative.StartsWith('/') -or $payloadRootRelative -match '^[a-zA-Z]:') {
    throw "overlay.integrity.root must be a relative directory path: '$payloadRootRelative'."
  }
  $rootSegments = $payloadRootRelative -split '/'
  if ($rootSegments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' }) {
    throw "overlay.integrity.root must not contain empty, dot, or parent segments: '$payloadRootRelative'."
  }
  $packageRoot = [System.IO.Path]::GetFullPath($PayloadRoot)
  $packageRootPrefix = $packageRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
  $root = [System.IO.Path]::GetFullPath((Join-Path $packageRoot ($payloadRootRelative.Replace('/', [System.IO.Path]::DirectorySeparatorChar))))
  if (-not $root.StartsWith($packageRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "overlay.integrity.root escapes its payload root: '$payloadRootRelative'."
  }
  if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "overlay.integrity.root is missing or is not a directory: '$payloadRootRelative'."
  }
  if (([System.IO.File]::GetAttributes($root) -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw "overlay.integrity.root must not be a reparse point: '$payloadRootRelative'."
  }

  $payload = Get-CompatibilityProperty $Integrity 'payload' 'overlay.integrity'
  if ($payload -is [string] -or (($payload -isnot [System.Collections.IEnumerable]) -and ($payload -isnot [pscustomobject]))) {
    throw 'overlay.integrity.payload must be a non-empty JSON array.'
  }
  $entries = @($payload)
  if ($entries.Count -eq 0) { throw 'overlay.integrity.payload must not be empty.' }

  $rootPrefix = $root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
  $seenPaths = New-Object System.Collections.Generic.HashSet[string]([System.StringComparer]::OrdinalIgnoreCase)
  $verified = New-Object System.Collections.Generic.List[object]
  foreach ($entry in $entries) {
    $relativePath = ([string](Get-CompatibilityProperty $entry 'path' 'overlay.integrity.payload entry')).Trim().Replace('\', '/')
    if (-not $relativePath -or $relativePath.StartsWith('/') -or $relativePath -match '^[a-zA-Z]:') {
      throw "overlay.integrity.payload path must be a relative file path: '$relativePath'."
    }
    $segments = $relativePath -split '/'
    if ($segments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' }) {
      throw "overlay.integrity.payload path must not contain empty, dot, or parent segments: '$relativePath'."
    }
    if (-not $seenPaths.Add($relativePath)) {
      throw "overlay.integrity.payload contains a duplicate path: '$relativePath'."
    }
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $root ($relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))))
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "overlay.integrity.payload path escapes its payload root: '$relativePath'."
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      throw "overlay.integrity.payload file is missing: '$relativePath'."
    }
    if (([System.IO.File]::GetAttributes($candidate) -band [System.IO.FileAttributes]::ReparsePoint)) {
      throw "overlay.integrity.payload file must not be a reparse point: '$relativePath'."
    }
    $hash = Assert-CompatibilityFileSha256 $candidate `
      (Get-CompatibilityProperty $entry 'sha256' "overlay.integrity.payload[$relativePath]") `
      "overlay.integrity.payload[$relativePath]"
    [void]$verified.Add([pscustomobject]@{ Path = $relativePath; Sha256 = $hash })
  }
  $actualFiles = @(Get-ChildItem -LiteralPath $root -File -Recurse -Force)
  foreach ($actualFile in $actualFiles) {
    if (($actualFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
      throw "overlay.integrity.payload file must not be a reparse point: '$($actualFile.FullName)'."
    }
    $actualRelativePath = $actualFile.FullName.Substring($rootPrefix.Length).Replace('\', '/')
    if (-not $seenPaths.Contains($actualRelativePath)) {
      throw "overlay.integrity.payload contains an undeclared file: '$actualRelativePath'."
    }
  }
  return $verified.ToArray()
}

function Read-RegisteredOverlayIntegrity {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Registered overlay integrity metadata is required but missing: $Path"
  }
  try {
    $record = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Registered overlay integrity metadata could not be read: $($_.Exception.Message)"
  }
  $schema = ConvertTo-CompatibilitySchemaVersion `
    (Get-CompatibilityProperty $record 'schemaVersion' 'overlay integrity') 'overlay integrity.schemaVersion'
  if ($schema -ne $script:OverlayIntegritySchemaVersion) {
    throw "Registered overlay integrity schema $schema is unsupported; expected $script:OverlayIntegritySchemaVersion."
  }
  return [pscustomobject]@{
    Id = ConvertTo-OverlayIdentifier (Get-CompatibilityProperty $record 'id' 'overlay integrity') 'overlay integrity.id'
    ManifestSha256 = ConvertTo-CompatibilitySha256 `
      (Get-CompatibilityProperty $record 'manifestSha256' 'overlay integrity') 'overlay integrity.manifestSha256'
  }
}

function Write-RegisteredOverlayIntegrity {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Overlay
  )

  [ordered]@{
    schemaVersion = $script:OverlayIntegritySchemaVersion
    id = $Overlay.Id
    manifestSha256 = $Overlay.ManifestSha256
  } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Path -Encoding UTF8
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
  $buildRevision = ''
  if ($manifest.PSObject.Properties['buildRevision']) {
    $buildRevision = ([string]$manifest.buildRevision).Trim()
    if (-not $buildRevision -or $buildRevision.Length -gt 64 -or $buildRevision -notmatch '^[A-Za-z0-9._-]+$') {
      throw 'manifest.buildRevision must be 1-64 letters, digits, dots, underscores, or hyphens.'
    }
  }
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
    BuildRevision = $buildRevision
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
    [Parameter(Mandatory = $true)][string]$OverlayManifestPath,
    [Parameter(Mandatory = $true)][string]$ExpectedOverlayId,
    [Parameter(Mandatory = $true)][string]$ExpectedManifestSha256,
    [Parameter(Mandatory = $true)][string]$PayloadRoot
  )

  if (-not (Test-Path -LiteralPath $OverlayManifestPath -PathType Leaf)) {
    throw "Overlay metadata is required but missing: $OverlayManifestPath"
  }
  $expectedId = ConvertTo-OverlayIdentifier $ExpectedOverlayId 'expected overlay id'
  $manifestHash = Assert-CompatibilityFileSha256 $OverlayManifestPath $ExpectedManifestSha256 'overlay manifest'
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
  $id = ConvertTo-OverlayIdentifier (Get-CompatibilityProperty $overlay 'id' 'overlay') 'overlay.id'
  if ($id -ne $expectedId) {
    throw "Overlay identity '$id' does not match expected overlay identity '$expectedId'."
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
  $requiredContractVersionText = ([string](Get-CompatibilityProperty $requires 'contractVersion' 'overlay.requiresCore')).Trim()
  $requiredContractVersion = ConvertTo-CompatibilityVersion $requiredContractVersionText 'overlay.requiresCore.contractVersion'
  if ($requiredContractVersion -ne $Core.ContractVersion) {
    throw "Overlay '$id' v$overlayVersionText does not support core contract v$($Core.ContractVersionText)."
  }
  $coreRange = Get-CompatibilityProperty $requires 'coreVersionRange' 'overlay.requiresCore'
  if (-not (Test-VersionInCompatibilityRange $Core.Version $coreRange 'overlay.requiresCore.coreVersionRange')) {
    throw "Overlay '$id' v$overlayVersionText does not support core v$($Core.VersionText)."
  }
  $integrity = Get-CompatibilityProperty $overlay 'integrity' 'overlay'
  $verifiedPayload = @(Assert-OverlayPayloadIntegrity -Integrity $integrity -PayloadRoot $PayloadRoot)

  return [pscustomobject]@{
    Id = $id
    DisplayName = $displayName
    VersionText = $overlayVersionText
    ManifestSchemaVersion = $schema
    ManifestPath = (Resolve-Path -LiteralPath $OverlayManifestPath).Path
    ManifestSha256 = $manifestHash
    PayloadFileCount = $verifiedPayload.Count
  }
}

function Resolve-OverlayCompatibility {
  param(
    [Parameter(Mandatory = $true)]$Core,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$OverlayManifestPath,
    [string]$OverlayManifestSha256,
    [string]$OverlayPayloadRoot,
    [string]$ExpectedOverlayId,
    [switch]$OverlayRequested
  )

  $registeredPath = Join-Path $InstallDir 'overlay-manifest.json'
  $registeredIntegrityPath = Join-Path $InstallDir 'overlay-integrity.json'
  $hasExplicitOverlayArgument = -not [string]::IsNullOrWhiteSpace($OverlayManifestPath) `
    -or -not [string]::IsNullOrWhiteSpace($OverlayManifestSha256) `
    -or -not [string]::IsNullOrWhiteSpace($OverlayPayloadRoot) `
    -or -not [string]::IsNullOrWhiteSpace($ExpectedOverlayId)
  if (-not $OverlayRequested -and $hasExplicitOverlayArgument) {
    throw 'Overlay integration arguments were supplied without an explicit overlay request.'
  }
  if ($OverlayRequested) {
    if ([string]::IsNullOrWhiteSpace($OverlayManifestPath)) {
      throw 'Overlay metadata was requested but no -OverlayManifestPath value was supplied.'
    }
    if ([string]::IsNullOrWhiteSpace($OverlayManifestSha256)) {
      throw 'Overlay metadata was requested but no -OverlayManifestSha256 value was supplied.'
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedOverlayId)) {
      throw 'Overlay metadata was requested but no -ExpectedOverlayId value was supplied.'
    }
    if ([string]::IsNullOrWhiteSpace($OverlayPayloadRoot)) {
      $OverlayPayloadRoot = Split-Path -Parent $OverlayManifestPath
    }
    $overlay = Assert-OverlayCompatibility -Core $Core -OverlayManifestPath $OverlayManifestPath `
      -ExpectedOverlayId $ExpectedOverlayId -ExpectedManifestSha256 $OverlayManifestSha256 -PayloadRoot $OverlayPayloadRoot
    return [pscustomobject]@{
      Mode = 'overlay'
      Source = 'explicit'
      Overlay = $overlay
      RegisteredPath = $registeredPath
      RegisteredIntegrityPath = $registeredIntegrityPath
    }
  }
  $hasRegisteredManifestArtifact = Test-Path -LiteralPath $registeredPath
  $hasRegisteredIntegrityArtifact = Test-Path -LiteralPath $registeredIntegrityPath
  if ($hasRegisteredManifestArtifact -or $hasRegisteredIntegrityArtifact) {
    if (-not (Test-Path -LiteralPath $registeredPath -PathType Leaf) -or -not (Test-Path -LiteralPath $registeredIntegrityPath -PathType Leaf)) {
      throw 'Registered overlay metadata is incomplete or is not a regular file; refusing to bypass overlay validation.'
    }
    $registeredIntegrity = Read-RegisteredOverlayIntegrity -Path $registeredIntegrityPath
    $overlay = Assert-OverlayCompatibility -Core $Core -OverlayManifestPath $registeredPath `
      -ExpectedOverlayId $registeredIntegrity.Id -ExpectedManifestSha256 $registeredIntegrity.ManifestSha256 -PayloadRoot $InstallDir
    return [pscustomobject]@{
      Mode = 'overlay'
      Source = 'registered'
      Overlay = $overlay
      RegisteredPath = $registeredPath
      RegisteredIntegrityPath = $registeredIntegrityPath
    }
  }
  return [pscustomobject]@{
    Mode = 'core-only'
    Source = 'none'
    Overlay = $null
    RegisteredPath = $registeredPath
    RegisteredIntegrityPath = $registeredIntegrityPath
  }
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
      manifestSha256 = $Compatibility.Overlay.ManifestSha256
      payloadFileCount = $Compatibility.Overlay.PayloadFileCount
    }
  }
  $report = [ordered]@{
    schemaVersion = 1
    core = [ordered]@{
      name = $Core.Name
      version = $Core.VersionText
      buildRevision = $(if ($Core.PSObject.Properties['BuildRevision']) { $Core.BuildRevision } else { '' })
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
