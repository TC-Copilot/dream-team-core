# Core and overlay compatibility contract

The public package is always complete and installs as **core-only** by default. It does not discover,
download, or install private/provider-specific code. An external overlay can participate in the
install/update safety check by supplying one provider-neutral metadata file.

## Core contract

`manifest.json` is the source of truth:

```json
{
  "version": "4.5.15",
  "coreContract": {
    "schemaVersion": 1,
    "version": "1.0.0",
    "overlayManifestSchemaVersion": 1
  }
}
```

- `version` is the product/core release.
- `coreContract.schemaVersion` identifies this document's core metadata shape.
- `coreContract.version` versions the behavior offered to external overlays independently of the
  product release.
- `overlayManifestSchemaVersion` is the overlay metadata shape accepted by this installer.

All versions in this contract are strict three-part numeric versions. Compatibility ranges use an
inclusive lower bound and exclusive upper bound.

## Overlay manifest schema 1

An external repository owns this file and its payload. The public repository contains neither.

```json
{
  "schemaVersion": 1,
  "id": "example.external-overlay",
  "displayName": "Example external overlay",
  "version": "2.3.4",
  "requiresCore": {
    "contractSchemaVersion": 1,
    "contractVersion": {
      "minInclusive": "1.0.0",
      "maxExclusive": "2.0.0"
    },
    "coreVersion": {
      "minInclusive": "4.5.15",
      "maxExclusive": "5.0.0"
    }
  }
}
```

The external installer passes the file explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto `
  -OverlayManifestPath "C:\path\to\overlay-manifest.json"
```

The core installer validates metadata before stopping the running app or replacing package files.
On success it registers the metadata as `<install folder>\overlay-manifest.json`. Every later core
update automatically revalidates that registered metadata, including an ordinary public-core update
that does not pass the switch. Incompatible, malformed, unsupported-schema, and explicitly requested
but missing metadata all stop the update. The old running install and its files are left untouched.

`<install folder>\app\.version-report.json` records the verified core, contract, and overlay versions.
`GET /api/health` exposes the same non-sensitive report in `versions`. A core-only install reports
`overlay: null` and `compatibility.status: "core-only"`.

## External/private repository requirements

The external repository must:

1. Ship `overlay-manifest.json` using the exact schema above and update its overlay `version` for each
   overlay release.
2. Set both compatibility ranges deliberately for every release. Do not use an unbounded or
   success-on-error fallback.
3. Invoke the core installer with `-OverlayManifestPath` for initial install and overlay updates.
   A missing file must remain a hard failure.
4. Install its payload only after the core compatibility check succeeds. The core installer validates
   and registers metadata; it does not copy, import, fetch, or execute overlay payloads.
5. Read `app\.version-report.json` or `/api/health`. Treat any status other than `compatible` as a
   failed overlay install/update.
6. Test at least: the minimum supported core, the newest supported core, the exclusive upper bound,
   an unsupported contract schema/version, and missing/malformed metadata.

When core behavior needed by overlays changes incompatibly, increment `coreContract.version` (or its
schema version for a metadata-shape break) and publish a new core release. Product version bumps alone
do not imply a contract break.
