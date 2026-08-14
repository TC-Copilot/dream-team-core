#!/usr/bin/env python3
"""Provider-neutral compatibility checks for independently versioned core and overlays."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def update_plan(installed_core, latest_core, installed_overlay, latest_overlay):
    core_newer = installed_core != latest_core
    overlay_newer = installed_overlay != latest_overlay
    return {
        "updateCore": core_newer,
        "updateOverlay": overlay_newer,
        "resetApplicationLayer": core_newer or overlay_newer,
    }


def main():
    package_manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    contract = package_manifest["layeredInstall"]
    assert contract == {
        "schemaVersion": 2,
        "resetApplicationLayerSwitch": "-ResetApplicationLayer",
        "compatibilityContract": "coreContract",
        "registeredOverlayManifest": "overlay-manifest.json",
        "versionReport": "app/.version-report.json",
    }

    cases = [
        ("core-only newer", ("4.5.15", "4.5.16", "2.3.0", "2.3.0"), (True, False, True)),
        ("overlay-only newer", ("4.5.16", "4.5.16", "2.2.0", "2.3.0"), (False, True, True)),
        ("both newer", ("4.5.15", "4.5.16", "2.2.0", "2.3.0"), (True, True, True)),
        ("already current", ("4.5.16", "4.5.16", "2.3.0", "2.3.0"), (False, False, False)),
    ]
    for label, versions, expected in cases:
        plan = update_plan(*versions)
        actual = (plan["updateCore"], plan["updateOverlay"], plan["resetApplicationLayer"])
        assert actual == expected, f"{label}: expected {expected}, got {actual}"

    contract_doc = (ROOT / "docs" / "LAYERED-INSTALL-CONTRACT.md").read_text(encoding="utf-8")
    for requirement in (
        "hash-invalid manifests fail closed.",
        "Core newer, overlay current",
        "Core current, overlay newer",
        "Both newer",
        "Both current",
        "Public `/daily-flow-setup` checks only",
        "canonical compatibility report",
        "superseded",
    ):
        assert requirement.lower() in contract_doc.lower()

    installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
    for preserved in ("config.json", "data", "profile", "state.json", "impact.json", ".local-token"):
        assert f"'{preserved}'" in installer
    assert "[switch]$ResetApplicationLayer" in installer
    assert "$config[$property.Name] = $property.Value" in installer

    assert "-OverlayManifestPath" in installer
    print("[PASS] canonical compatibility gate retains reset, four update states, and runtime preservation")


if __name__ == "__main__":
    main()
