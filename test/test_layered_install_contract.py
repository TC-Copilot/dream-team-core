#!/usr/bin/env python3
"""Provider-neutral compatibility checks for independently versioned core and overlays."""

import importlib.util
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def load_app_module():
    spec = importlib.util.spec_from_file_location("daily_flow_layer_contract", ROOT / "app" / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def update_plan(installed_core, latest_core, installed_overlay, latest_overlay):
    core_newer = installed_core != latest_core
    overlay_newer = installed_overlay != latest_overlay
    return {
        "updateCore": core_newer,
        "updateOverlay": overlay_newer,
        "resetApplicationLayer": core_newer or overlay_newer,
    }


def expect_runtime_error(action, label):
    try:
        action()
    except RuntimeError:
        return
    raise AssertionError(f"{label} did not fail closed")


def main():
    package_manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    contract = package_manifest["layeredInstall"]
    assert contract == {
        "schemaVersion": 1,
        "resetApplicationLayerSwitch": "-ResetApplicationLayer",
        "healthEndpoint": "/api/health",
        "installedOverlayManifest": "app/.installed-overlay.json",
        "overlayIdentityFields": ["id", "version", "coreVersion"],
    }

    cases = [
        ("core-only newer", ("4.5.14", "4.5.15", "2.3.0", "2.3.0"), (True, False, True)),
        ("overlay-only newer", ("4.5.15", "4.5.15", "2.2.0", "2.3.0"), (False, True, True)),
        ("both newer", ("4.5.14", "4.5.15", "2.2.0", "2.3.0"), (True, True, True)),
        ("already current", ("4.5.15", "4.5.15", "2.3.0", "2.3.0"), (False, False, False)),
    ]
    for label, versions, expected in cases:
        plan = update_plan(*versions)
        actual = (plan["updateCore"], plan["updateOverlay"], plan["resetApplicationLayer"])
        assert actual == expected, f"{label}: expected {expected}, got {actual}"

    app = load_app_module()
    with tempfile.TemporaryDirectory() as temp_dir:
        manifest_path = Path(temp_dir) / ".installed-overlay.json"
        assert app._read_overlay_identity(manifest_path, "4.5.15") is None

        valid = {
            "schemaVersion": 1,
            "id": "example-overlay",
            "version": "2.3.0",
            "coreVersion": "4.5.15",
        }
        manifest_path.write_text(json.dumps(valid), encoding="utf-8")
        assert app._read_overlay_identity(manifest_path, "4.5.15") == {
            "id": "example-overlay",
            "version": "2.3.0",
            "coreVersion": "4.5.15",
        }

        mismatched = dict(valid, coreVersion="4.5.14")
        manifest_path.write_text(json.dumps(mismatched), encoding="utf-8")
        expect_runtime_error(
            lambda: app._read_overlay_identity(manifest_path, "4.5.15"),
            "cross-core installed manifest",
        )

        manifest_path.write_text('{"schemaVersion":1,"id":"incomplete"}', encoding="utf-8")
        expect_runtime_error(
            lambda: app._read_overlay_identity(manifest_path, "4.5.15"),
            "incomplete installed manifest",
        )

        manifest_path.write_text(json.dumps(dict(valid, extra="not-allowed")), encoding="utf-8")
        expect_runtime_error(
            lambda: app._read_overlay_identity(manifest_path, "4.5.15"),
            "installed manifest with unknown fields",
        )

    contract_doc = (ROOT / "docs" / "LAYERED-INSTALL-CONTRACT.md").read_text(encoding="utf-8")
    for requirement in (
        "hash-invalid manifests fail closed.",
        "Core newer, overlay current",
        "Core current, overlay newer",
        "Both newer",
        "Both current",
        "Public `/daily-flow-setup` checks only the public",
        "core channel.",
    ):
        assert requirement in contract_doc

    installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
    for preserved in ("config.json", "data", "profile", "state.json", "impact.json", ".local-token"):
        assert f"'{preserved}'" in installer
    assert "[switch]$ResetApplicationLayer" in installer
    assert "$config[$property.Name] = $property.Value" in installer

    print("[PASS] independent core/overlay versions, four update states, manifests, and preservation contract")


if __name__ == "__main__":
    main()
