# Core and overlay compatibility contract

The public package is always complete and installs as **core-only** by default. It does not discover,
download, or install private/provider-specific code. An external overlay can participate in the
install/update safety check by supplying one provider-neutral metadata file.

## Core contract

`manifest.json` is the source of truth:

```json
{
  "version": "4.5.20",
  "coreContract": {
    "schemaVersion": 1,
    "version": "1.0.0",
    "overlayManifestSchemaVersion": 2
  }
}
```

- `version` is the product/core release.
- `coreContract.schemaVersion` identifies this document's core metadata shape.
- `coreContract.version` versions the behavior offered to external overlays independently of the
  product release.
- `overlayManifestSchemaVersion` is the overlay metadata shape accepted by this installer.

All versions in this contract are strict three-part numeric versions. `coreVersionRange` uses an
inclusive lower bound and exclusive upper bound. Contract schema and contract version are exact
matches, not ranges.

## Overlay manifest schema 2

An external repository owns this file and its payload. The public repository contains neither.

```json
{
  "schemaVersion": 2,
  "id": "example.external-overlay",
  "displayName": "Example external overlay",
  "version": "1.0.0",
  "requiresCore": {
    "contractSchemaVersion": 1,
    "contractVersion": "1.0.0",
    "coreVersionRange": {
      "minInclusive": "4.5.16",
      "maxExclusive": "4.6.0"
    }
  },
  "integrity": {
    "root": "overlay",
    "payload": [
      {
        "path": "provider.txt",
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      }
    ]
  }
}
```

The external installer passes the manifest, its trusted release digest, the payload root, and the
expected provider-neutral overlay identity explicitly:

```powershell
$manifestPath = "C:\path\to\overlay-manifest.json"
$manifestHash = "<SHA-256 from trusted overlay release metadata>"
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto `
  -OverlayManifestPath $manifestPath `
  -OverlayManifestSha256 $manifestHash `
  -OverlayPayloadRoot "C:\path\to" `
  -ExpectedOverlayId "example.external-overlay"
```

The core installer validates the trusted manifest SHA-256 before parsing it, requires the expected
overlay ID, requires exact `contractSchemaVersion` and `contractVersion`, and validates every
declared payload file SHA-256 under `integrity.root`. It rejects empty, duplicate, absolute, escaping,
reparse-point, and undeclared payload files. The public installer still never copies, imports, fetches,
or executes the payload.

On success it registers `overlay-manifest.json` and a separate `overlay-integrity.json` containing the
validated overlay identity and manifest SHA-256. Every later core update automatically revalidates the
registered manifest, identity, and installed payload hashes before stopping the app or replacing package
files. Missing, malformed, unsupported-schema, out-of-range, wrong-identity, or hash-invalid metadata
fails closed; the old running install and its files are left untouched.

`<install folder>\app\.version-report.json` records the verified core, contract, overlay versions,
manifest SHA-256, and payload-file count. `GET /api/health` exposes the same non-sensitive report in
`versions`. A core-only install reports `overlay: null` and `compatibility.status: "core-only"`.

### Range behavior

Overlay `1.0.0` with `coreVersionRange` `>=4.5.16 <4.6.0` is compatible with any validated 4.5.x
core, including the latest compatible patch; it does **not** need to target the latest public core
version or one particular public ZIP digest. `4.6.0` is rejected by that range. A core behavior or
metadata-shape change that breaks overlays must still advance the core contract or overlay manifest
schema.

## External/private repository requirements

The external repository must:

1. Ship schema-2 `overlay-manifest.json`, a complete payload hash list, and a trusted SHA-256 for the
   raw manifest. Update the overlay `version` for each overlay release.
2. Set a deliberate bounded `coreVersionRange` and exact contract schema/version for every release.
   Do not use an unbounded or success-on-error fallback.
3. Invoke the core installer with `-OverlayManifestPath`, `-OverlayManifestSha256`,
   `-OverlayPayloadRoot`, and `-ExpectedOverlayId` for initial install and overlay updates. Any
   missing value must remain a hard failure.
4. Install its payload only after the core compatibility check succeeds. The core installer validates
   and registers metadata and hashes; it does not copy, import, fetch, or execute overlay payloads.
5. Read `app\.version-report.json` or `/api/health`. Treat any status other than `compatible` as a
   failed overlay install/update.
6. Test at least: the minimum supported core, the newest supported core, the exclusive upper bound,
   an unsupported contract schema/version, wrong identity, and manifest/payload tampering.

When core behavior needed by overlays changes incompatibly, increment `coreContract.version` (or its
schema version for a metadata-shape break) and publish a new core release. Product version bumps alone
do not imply a contract break.
