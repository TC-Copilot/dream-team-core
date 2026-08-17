#!/usr/bin/env python3
"""Focused offline checks for checkpointed, review-gated How sync."""
from __future__ import annotations

import gc
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


NOW = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)


def how_record(record_id: str = "how-report", **overrides):
    value = {
        "schemaVersion": "1.0",
        "id": record_id,
        "fingerprint": "caller-value-is-ignored",
        "title": "Prepare a status report",
        "intent": "Create a source-backed weekly status report.",
        "procedure": "Collect confirmed outcomes, risks, and next actions.",
        "applicability": "Weekly project reporting.",
        "owner": "reporting",
        "links": [
            {"kind": "source", "ref": "document-1"},
            {"kind": "template", "ref": "template-1"},
        ],
        "createdAt": "2026-08-17T10:00:00-05:00",
        "updatedAt": "2026-08-17T10:00:00-05:00",
        "reviewAt": "2026-08-24T10:00:00-05:00",
        "expiresAt": "",
        "provenance": [
            {"source": "document", "sourceId": "document-1"},
            {"source": "calendar", "sourceId": "event-1"},
        ],
        "confidence": 0.8,
        "sensitivity": "internal",
        "status": "active",
    }
    value.update(overrides)
    return value


def request(records, checkpoint, **overrides):
    value = {
        "schemaVersion": "1.0",
        "full": False,
        "since": "",
        "sources": ["document"],
        "dryRun": False,
        "review": False,
        "batches": [{
            "source": "document",
            "records": records,
            "checkpoint": checkpoint,
        }],
    }
    value.update(overrides)
    return value


def expect_error(fragment, callback):
    try:
        callback()
    except ValueError as exc:
        assert fragment in str(exc), str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {fragment!r}")


def main() -> int:
    parsed = appmod.parse_how_sync_command(
        "/dream-team how sync --full --sources Teams,calendar,teams --dry-run --review"
    )
    assert parsed == {
        "full": True,
        "since": "",
        "sources": ["calendar", "teams"],
        "dryRun": True,
        "review": True,
    }
    expect_error("mutually exclusive", lambda: appmod.parse_how_sync_command(
        "/dream-team how sync --full --since 2026-08-17T00:00:00Z"
    ))

    first = how_record()
    reordered = how_record(
        fingerprint="different-caller-value",
        updatedAt="2026-08-17T15:30:00Z",
        status="rejected",
        links=list(reversed(first["links"])),
        provenance=list(reversed(first["provenance"])),
    )
    assert appmod.stable_how_fingerprint(first) == appmod.stable_how_fingerprint(reordered)
    assert appmod.stable_how_fingerprint(
        how_record(confidence=1, expiresAt="2026-08-18T00:00:00Z")
    ) == appmod.stable_how_fingerprint(
        how_record(confidence=1.0, expiresAt="2026-08-18T01:00:00+01:00")
    )
    assert appmod.stable_how_fingerprint(first) != appmod.stable_how_fingerprint(
        how_record(procedure="Use a newly reviewed procedure.")
    )

    original_db_path = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "daily_flow.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                initial = appmod.sync_how_records(
                    db,
                    request([first], "cursor-1", full=True, review=True),
                    now=NOW,
                )
                assert initial["mode"] == "full"
                assert initial["counts"]["new"] == 1
                assert initial["checkpoints"] == {"document": "cursor-1"}
                assert initial["review"][0]["status"] == "pending"
                assert not initial["review"][0]["active"]

                stored = appmod.query_how_records(db)
                assert len(stored) == 1
                fingerprint = stored[0]["fingerprint"]
                assert fingerprint.startswith("sha256:")
                assert stored[0]["status"] == "pending"

                approved = appmod.review_how_record(
                    db, first["id"], fingerprint, "approve", "user-1"
                )
                assert approved["status"] == "active"
                assert approved["active"]

                retry = appmod.sync_how_records(
                    db,
                    request([reordered], "cursor-1", full=True),
                    now=NOW,
                )
                assert retry["runId"] == initial["runId"]
                assert retry["idempotent"]
                assert retry["counts"]["new"] == 1
                assert appmod.query_how_records(db)[0]["status"] == "active"

                changed_record = how_record(
                    procedure="Use a newly reviewed procedure.",
                    updatedAt="2026-08-17T16:30:00Z",
                )
                changed = appmod.sync_how_records(
                    db,
                    request([changed_record], "cursor-2", review=True),
                    now=NOW,
                )
                assert changed["counts"]["changed"] == 1
                assert len(changed["review"]) == 1
                delayed_retry = appmod.sync_how_records(
                    db,
                    request([first], "cursor-1", full=True),
                    now=NOW,
                )
                assert delayed_retry["idempotent"]
                assert delayed_retry["checkpoints"] == {"document": "cursor-2"}
                versions = appmod.query_how_records(db)
                assert len(versions) == 2
                assert sum(item["active"] for item in versions) == 1
                changed_candidate = next(
                    item for item in versions if item["classification"] == "changed"
                )
                assert changed_candidate["status"] == "pending"
                activated = appmod.review_how_record(
                    db,
                    changed_candidate["id"],
                    changed_candidate["fingerprint"],
                    "approve",
                    "user-1",
                )
                assert activated["active"]
                old = next(
                    item for item in appmod.query_how_records(db)
                    if item["fingerprint"] == fingerprint
                )
                assert old["status"] == "stale"
                assert not old["active"]

                merged_sources = appmod.sync_how_records(
                    db,
                    {
                        "schemaVersion": "1.0",
                        "sources": ["calendar", "document"],
                        "batches": [
                            {
                                "source": "calendar",
                                "checkpoint": "calendar-1",
                                "records": [how_record(
                                    "how-shared",
                                    createdAt="2026-08-17T14:00:00Z",
                                    updatedAt="2026-08-17T15:00:00Z",
                                )],
                            },
                            {
                                "source": "document",
                                "checkpoint": "cursor-shared",
                                "records": [how_record(
                                    "how-shared",
                                    createdAt="2026-08-17T13:00:00Z",
                                    updatedAt="2026-08-17T15:30:00Z",
                                )],
                            },
                        ],
                    },
                    now=NOW,
                )
                assert merged_sources["counts"]["new"] == 1
                shared = next(
                    item for item in appmod.query_how_records(db)
                    if item["id"] == "how-shared"
                )
                assert shared["sources"] == ["calendar", "document"]
                assert shared["createdAt"] == "2026-08-17T13:00:00Z"
                assert shared["updatedAt"] == "2026-08-17T15:30:00Z"
                appmod.sync_how_records(
                    db,
                    request([how_record(
                        "how-shared",
                        createdAt="2026-08-17T12:59:59.500000Z",
                        updatedAt="2026-08-17T15:30:00.500000Z",
                    )], "cursor-shared-retry"),
                    now=NOW,
                )
                shared = next(
                    item for item in appmod.query_how_records(db)
                    if item["id"] == "how-shared"
                )
                assert shared["createdAt"] == "2026-08-17T12:59:59.500000Z"
                assert shared["updatedAt"] == "2026-08-17T15:30:00.500000Z"
                source_expansion = appmod.sync_how_records(
                    db,
                    {
                        "schemaVersion": "1.0",
                        "sources": ["calendar", "document"],
                        "batches": [
                            {
                                "source": "calendar",
                                "checkpoint": "calendar-1",
                                "records": [how_record(
                                    "how-shared",
                                    createdAt="2026-08-17T14:30:00Z",
                                    updatedAt="2026-08-17T15:15:00Z",
                                )],
                            },
                            {
                                "source": "document",
                                "checkpoint": "cursor-shared",
                                "records": [],
                            },
                        ],
                    },
                    now=NOW,
                )
                assert not source_expansion["idempotent"]
                assert next(
                    item for item in appmod.query_how_records(db)
                    if item["id"] == "how-shared"
                )["sources"] == ["calendar", "document"]

                special = appmod.sync_how_records(
                    db,
                    request([
                        how_record(
                            "how-sensitive",
                            sensitivity="confidential",
                            updatedAt="2026-08-17T16:31:00Z",
                        ),
                        how_record(
                            "how-stale",
                            reviewAt="2026-08-17T15:00:00Z",
                            updatedAt="2026-08-17T16:32:00Z",
                        ),
                        how_record(
                            "how-conflict",
                            procedure="Conflicting procedure A.",
                            updatedAt="2026-08-17T16:33:00Z",
                        ),
                        how_record(
                            "how-conflict",
                            procedure="Conflicting procedure B.",
                            updatedAt="2026-08-17T16:34:00Z",
                        ),
                    ], "cursor-3"),
                    now=NOW,
                )
                assert special["counts"]["sensitive"] == 1
                assert special["counts"]["stale"] == 1
                assert special["counts"]["conflicting"] == 2
                sensitive_retry = appmod.sync_how_records(
                    db,
                    request([
                        how_record(
                            "how-sensitive",
                            sensitivity="confidential",
                            updatedAt="2026-08-17T16:31:00Z",
                        ),
                    ], "cursor-sensitive-retry"),
                    now=NOW,
                )
                assert sensitive_retry["counts"]["sensitive"] == 1
                assert appmod.query_how_records(
                    db, classification="sensitive"
                )[0]["id"] == "how-sensitive"
                sensitive = appmod.query_how_records(
                    db, classification="sensitive"
                )[0]
                appmod.review_how_record(
                    db, sensitive["id"], sensitive["fingerprint"], "reject", "user-1"
                )
                appmod.sync_how_records(
                    db,
                    request([
                        how_record(
                            "how-sensitive",
                            sensitivity="confidential",
                            updatedAt="2026-08-17T16:31:00Z",
                        ),
                    ], "cursor-sensitive-after-reject"),
                    now=NOW,
                )
                sensitive = appmod.query_how_records(
                    db, classification="sensitive"
                )[0]
                assert sensitive["status"] == "rejected"

                stale = next(
                    item for item in appmod.query_how_records(db)
                    if item["id"] == "how-stale"
                )
                appmod.review_how_record(
                    db, stale["id"], stale["fingerprint"], "approve", "user-1"
                )
                stale_retry = appmod.sync_how_records(
                    db,
                    request([
                        how_record(
                            "how-stale",
                            reviewAt="2026-08-17T15:00:00Z",
                            updatedAt="2026-08-17T16:32:00Z",
                        ),
                    ], "cursor-stale-review", review=True),
                    now=NOW,
                )
                stale_review = next(
                    item for item in stale_retry["review"] if item["id"] == "how-stale"
                )
                assert stale_review["active"]
                assert stale_review["status"] == "active"
                assert stale_review["classification"] == "stale"
                refreshed_review = appmod.sync_how_records(
                    db,
                    request([
                        how_record(
                            "how-stale",
                            reviewAt="2026-08-24T15:00:00Z",
                            updatedAt="2026-08-17T17:00:00Z",
                        ),
                    ], "cursor-stale-refreshed", review=True),
                    now=NOW,
                )
                assert all(
                    item["id"] != "how-stale" for item in refreshed_review["review"]
                )
                refreshed_stale = next(
                    item for item in appmod.query_how_records(db)
                    if item["id"] == "how-stale"
                )
                assert refreshed_stale["active"]
                assert refreshed_stale["classification"] == "unchanged"
                assert all(
                    item["status"] == "pending"
                    for item in appmod.query_how_records(
                        db, classification="conflicting", source="document"
                    )
                    if item["id"] == "how-conflict"
                )
                active_conflict = appmod.sync_how_records(
                    db,
                    request([
                        changed_record,
                        how_record(
                            procedure="A competing procedure.",
                            updatedAt="2026-08-17T16:35:00Z",
                        ),
                    ], "cursor-conflict"),
                    now=NOW,
                )
                assert active_conflict["counts"]["conflicting"] == 2
                still_active = next(
                    item for item in appmod.query_how_records(db)
                    if item["id"] == changed_candidate["id"] and item["active"]
                )
                assert still_active["status"] == "active"

                before_rows = db.execute("SELECT COUNT(*) FROM how_records").fetchone()[0]
                before_runs = db.execute("SELECT COUNT(*) FROM how_sync_runs").fetchone()[0]
                dry_run = appmod.sync_how_records(
                    db,
                    request(
                        [how_record("how-dry-run", updatedAt="2026-08-17T16:35:00Z")],
                        "cursor-dry",
                        dryRun=True,
                        review=True,
                    ),
                    now=NOW,
                )
                assert dry_run["counts"]["new"] == 1
                assert dry_run["review"][0]["id"] == "how-dry-run"
                assert db.execute("SELECT COUNT(*) FROM how_records").fetchone()[0] == before_rows
                assert db.execute("SELECT COUNT(*) FROM how_sync_runs").fetchone()[0] == before_runs
                assert appmod.how_sync_checkpoints(db) == {
                    "calendar": "calendar-1",
                    "document": "cursor-conflict",
                }

                expect_error("title is required", lambda: appmod.sync_how_records(
                    db,
                    request([how_record("how-invalid", title="")], "cursor-bad"),
                    now=NOW,
                ))
                failed_batch = request([], "cursor-failed")
                failed_batch["batches"][0]["success"] = False
                expect_error("source batch failed", lambda: appmod.sync_how_records(
                    db, failed_batch, now=NOW
                ))
                assert appmod.how_sync_checkpoints(db) == {
                    "calendar": "calendar-1",
                    "document": "cursor-conflict",
                }
                assert db.execute(
                    "SELECT COUNT(*) FROM how_records WHERE record_id = 'how-invalid'"
                ).fetchone()[0] == 0

                since = appmod.sync_how_records(
                    db,
                    request(
                        [how_record("how-old", updatedAt="2026-08-17T15:00:00Z")],
                        "cursor-4",
                        since="2026-08-17T16:00:00Z",
                    ),
                    now=NOW,
                )
                assert since["mode"] == "since"
                assert since["total"] == 0
                assert appmod.how_sync_checkpoints(db) == {
                    "calendar": "calendar-1",
                    "document": "cursor-4",
                }
            finally:
                db.close()
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()

    print("[ok] checkpointed provider-neutral How sync lifecycle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
