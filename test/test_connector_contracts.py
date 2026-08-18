#!/usr/bin/env python3
"""Focused connector snapshot and Casey context contract checks."""
from __future__ import annotations

import gc
import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def snapshot(**overrides):
    now = datetime.now(timezone.utc)
    value = {
        "schemaVersion": "1.0",
        "provider": "example-provider",
        "capability": "context.read",
        "subject": "user:example",
        "observedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=30)).isoformat(),
        "status": "partial",
        "requestedScopes": ["context.read", "files.read"],
        "grantedScopes": ["context.read"],
        "provenance": {"source": "provider-api", "requestId": "request-1"},
        "data": {
            "knowledge": [
                {"type": "custom-extension/type-v9", "title": "Extension survives"}
            ]
        },
        "errors": [{"code": "scope_missing", "message": "files.read was not granted"}],
    }
    value.update(overrides)
    return value


def main() -> int:
    original_db = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "connector.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                first_snapshot = snapshot()
                saved, deduplicated = appmod.save_connector_snapshot(db, first_snapshot)
                first_version = db.execute(
                    "SELECT value FROM app_meta WHERE key = 'version'"
                ).fetchone()[0]
                repeated, repeat_deduplicated = appmod.save_connector_snapshot(db, first_snapshot)
                repeated_version = db.execute(
                    "SELECT value FROM app_meta WHERE key = 'version'"
                ).fetchone()[0]
                changed, changed_deduplicated = appmod.save_connector_snapshot(
                    db, {**first_snapshot, "subject": "user:changed",
                         "status": "available", "errors": []}
                )
                queried = appmod.query_connector_snapshots(
                    db, provider="example-provider", capability="context.read"
                )
                health = appmod.connector_health(db)
            finally:
                db.close()

            assert saved["data"]["knowledge"][0]["type"] == "custom-extension/type-v9"
            assert deduplicated is False
            assert repeat_deduplicated is True
            assert repeated["id"] == saved["id"]
            assert repeated_version == first_version
            assert changed_deduplicated is False
            assert changed["id"] != saved["id"]
            assert len(queried) == 2
            assert queried[0]["requestedScopes"] == ["context.read", "files.read"]
            assert queried[0]["grantedScopes"] == ["context.read"]
            assert health["byStatus"]["partial"] == 1

            expired = snapshot(
                subject="user:stale",
                status="available",
                expiresAt=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            )
            db = appmod.connect()
            try:
                _, expired_deduplicated = appmod.save_connector_snapshot(db, expired)
                health = appmod.connector_health(db)
            finally:
                db.close()
            assert expired_deduplicated is False
            assert health["byStatus"]["stale"] == 1

            for status in appmod.CONNECTOR_STATUSES:
                appmod.normalize_connector_snapshot(snapshot(status=status))

            for secret_name in (
                "accessToken", "refresh_token", "apiKey", "idToken", "bearerToken",
                "privateKey", "credentials", "authorization", "cookie", "cookies",
                "sessionToken", "clientCredentials", "passwords",
            ):
                try:
                    appmod.normalize_connector_snapshot(
                        snapshot(data={secret_name: "do-not-store"})
                    )
                except ValueError as exc:
                    assert "tokens" in str(exc)
                else:
                    raise AssertionError(f"secret-bearing field {secret_name} must be rejected")
            try:
                appmod.normalize_connector_snapshot(
                    {**snapshot(), "accessToken": "discarded-but-still-forbidden"}
                )
            except ValueError as exc:
                assert "tokens" in str(exc)
            else:
                raise AssertionError("unknown top-level secret fields must be rejected")

            try:
                appmod.normalize_connector_snapshot(snapshot(data={"raw": "x" * 300_000}))
            except ValueError as exc:
                assert "exceeds" in str(exc)
            else:
                raise AssertionError("oversized normalized snapshots must be rejected")

            canonical = appmod.normalize_connector_snapshot(snapshot(
                observedAt="2026-08-13T10:00:00-10:00",
                expiresAt="2026-08-13T21:00:00+01:00",
            ))
            assert canonical["observedAt"] == "2026-08-13T20:00:00Z"
            assert canonical["expiresAt"] == "2026-08-13T20:00:00Z"

            contract = appmod.casey_context_contract()
            assert contract["extensionTypesAllowed"] is True
            assert "commitment" in contract["vocabulary"]

            original_root = appmod.APP_ROOT
            original_version = appmod.APP_VERSION
            original_revision = appmod.APP_BUILD_REVISION
            report_root = pathlib.Path(tmp) / "versioned-app"
            report_root.mkdir()
            try:
                appmod.APP_ROOT = report_root
                appmod.APP_VERSION = "4.5.19"
                appmod.APP_BUILD_REVISION = "build.2"
                (report_root / ".version-report.json").write_text(json.dumps({
                    "schemaVersion": 1,
                    "core": {"version": "4.5.19", "buildRevision": "build.1"},
                    "overlay": None,
                    "compatibility": {"status": "core-only"},
                }), encoding="utf-8")
                try:
                    appmod._read_version_report()
                except RuntimeError as exc:
                    assert "build revision" in str(exc)
                else:
                    raise AssertionError("a stamped build mismatch must fail closed")

                appmod.APP_BUILD_REVISION = ""
                (report_root / ".version-report.json").write_text(json.dumps({
                    "schemaVersion": 1,
                    "core": {"version": "4.5.19"},
                    "overlay": None,
                    "compatibility": {"status": "core-only"},
                }), encoding="utf-8")
                assert appmod._read_version_report()["core"]["version"] == "4.5.19"
            finally:
                appmod.APP_ROOT = original_root
                appmod.APP_VERSION = original_version
                appmod.APP_BUILD_REVISION = original_revision

            print("[ok] connector contracts")
            return 0
        finally:
            appmod.DB_PATH = original_db
            gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
