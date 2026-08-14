# Layered install contract

This document covers the reset/payload transaction used by a separately distributed wrapper. The
canonical metadata and version-range rules are in
[OVERLAY-COMPATIBILITY.md](OVERLAY-COMPATIBILITY.md). Public installs never discover, fetch, copy, or
execute overlays.

The provisional v4.5.15 `app/.installed-overlay.json` identity is **superseded**. Starting with
v4.5.18, wrappers must use schema-2 `overlay-manifest.json`, `overlay-integrity.json`, and
`app/.version-report.json`. The retained `-ResetApplicationLayer` switch is now governed by that
canonical fail-closed compatibility check.

## Wrapper transaction

1. Resolve public core and overlay releases independently. Public `/daily-flow-setup` checks only the
   public core channel.
2. Stage both packages. Obtain the trusted manifest SHA-256 from the overlay release metadata. The
   public installer validates that manifest digest, the declared expected overlay identity, exact
   contract schema/version, the bounded `coreVersionRange`, and every payload file SHA-256; missing,
   malformed, mismatched, duplicate, escaping, reparse-point, or hash-invalid files fail closed.
3. Run the public installer with both integration switches:

   ```powershell
   .\install.ps1 -Auto -AgentInline -InstallDir <dir> `
     -ResetApplicationLayer -OverlayManifestPath <overlay-manifest.json> `
     -OverlayManifestSha256 <trusted-manifest-sha256> `
     -OverlayPayloadRoot <staged-overlay-root> `
     -ExpectedOverlayId <overlay-id>
   ```

   Compatibility is checked before the old app is stopped. The reset swaps in a clean public app
   while preserving `config.json`, `data/`, `profile/`, `state.json`, `impact.json`, and
   `.local-token`. The normal public install path remains in-place and core-only.
4. Require health to return the expected `version`/`coreVersion` and
   `versions.compatibility.status = "compatible"` with the expected overlay ID/version. This confirms
   metadata compatibility only.
5. Stop only through `app/stop-app.ps1`, apply the independently verified payload, and restart through
   `app/start-app.ps1`. The overlay must perform its own payload-presence/integrity verification
   before reporting success.

## Update decision matrix

| State | Core action | Overlay action | Required final verification |
| --- | --- | --- | --- |
| Core newer, overlay current and in range | Update and verify core | Reapply the current overlay because core reset removes it | New compatible core + current overlay |
| Core newer, overlay current but out of range | Stop before replacing the app | Acquire an overlay that declares the new core range | Compatible core + overlay pair |
| Core current, overlay newer | Reset and verify the current core from its public package | Update overlay after range and hash checks | Current compatible core + new overlay |
| Both newer | Update and verify core | Apply new overlay | New core + new overlay |
| Both current | No fetch | No fetch | Current core + current overlay |

Core-first is mandatory whenever core changes. A wrapper must not force an overlay to target the
latest public core version or a particular public ZIP digest: a correctly bounded
`coreVersionRange` is authoritative. The wrapper must not report success unless the canonical
compatibility report and payload-integrity verification both pass.
