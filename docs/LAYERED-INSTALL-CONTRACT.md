# Layered install contract

This contract lets a separately distributed wrapper install optional content over the public core
without coupling core to a provider, organization, or private release channel. Public installs do
not discover, request, or fetch overlays.

## Independent identities and channels

- `manifest.json.version` and the public release are the **core version** and core update channel.
- An overlay has its own manifest, version, and update channel. Its version never replaces or
  extends the core version.
- `GET /api/health` keeps `version` as a compatibility alias and reports `coreVersion`. When a valid
  `app/.installed-overlay.json` exists, health also reports its `overlay` identity.
- Public `/daily-flow-setup` checks only the public core channel. A wrapper may check both channels,
  but core must never receive private channel locations, credentials, names, or payloads.

The installed identity file has this exact provider-neutral shape:

```json
{
  "schemaVersion": 1,
  "id": "example-overlay",
  "version": "2.3.0",
  "coreVersion": "4.5.15"
}
```

All three string fields are required, non-empty, and at most 128 characters. `coreVersion` must
exactly equal the running core. A malformed or mismatched installed manifest prevents app startup,
so health cannot claim that a broken combination is ready. No installed manifest means a normal
core-only install and omits `overlay` from health.

## Wrapper transaction

A conforming wrapper performs these steps in order:

1. Resolve the latest public release from the public core channel. Independently resolve the latest
   overlay release from the wrapper's channel.
2. If either layer needs an update, download the latest public core package and the needed overlay
   package to staging. Before changing the install, require an overlay source
   manifest with schema version, overlay ID/version, exact target core version, a normalized unique
   relative path for every payload file, and a SHA-256 for every payload file. Missing, malformed,
   mismatched, duplicate, escaping, or hash-invalid manifests fail closed.
3. If either layer needs updating, run the fetched public `install.ps1 -Auto -AgentInline
   -InstallDir <dir> -ResetApplicationLayer`, even when the fetched core version equals the running
   core. The switch replaces only the application layer with a clean public baseline while carrying
   forward `config.json`, `data/`, `profile/`, legacy state files, and `.local-token`. This reset is
   what removes stale overlay files deterministically. The default installer path remains an
   in-place public install.
4. Require public health to return `ok: true`, `coreVersion` equal to the fetched public manifest,
   legacy `version` equal to `coreVersion`, and no overlay identity. Never proceed from a file stamp
   alone.
5. Stop the app only through `app/stop-app.ps1`. Its port-owner, PID/path ownership, and port-release
   checks must succeed. Never use a broad process-name kill.
6. Apply the already validated overlay from staging to the clean core baseline. Runtime paths
   preserved by core (`config.json`, `data/`, `profile/`, state files, token, PID, and logs) are
   forbidden overlay targets. Write `app/.installed-overlay.json` last.
7. Start through `app/start-app.ps1`. Require health to report the exact expected `coreVersion` and
   exact expected overlay `id`, `version`, and `coreVersion`. Any mismatch is failure, not partial
   success.

If only the overlay is newer, its target core version must equal the latest public core version.
The wrapper still refreshes the clean baseline from that public package before applying the overlay.
If neither layer is newer, it performs no fetch but still verifies both installed identities and
reports them separately.

## Update decision matrix

| State | Core action | Overlay action | Required final verification |
| --- | --- | --- | --- |
| Core newer, overlay current | Update and verify core | Reapply the current overlay because core reset removes it | New core + current overlay |
| Core current, overlay newer | Reset and verify the current core from its public package | Update overlay | Current core + new overlay |
| Both newer | Update and verify core | Apply new overlay | New core + new overlay |
| Both current | No fetch | No fetch | Current core + current overlay |

Core-first is mandatory whenever core changes. Overlay compatibility is an exact core-version match,
not a minimum-version range. The wrapper must not report success unless both independently expected
versions appear in final health.
