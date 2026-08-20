#!/usr/bin/env python3
"""Focused tests for provenance-safe tentative conflict lifecycle changes."""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE approvals (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          employee TEXT NOT NULL, action_type TEXT NOT NULL, risk TEXT NOT NULL,
          title TEXT NOT NULL, preview TEXT NOT NULL, destination TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', details_json TEXT NOT NULL DEFAULT '{}',
          user_guidance TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE calendar_conflict_decision_changes (
          id TEXT PRIMARY KEY, approval_id TEXT NOT NULL,
          conflict_source_id TEXT NOT NULL DEFAULT '', tentative_meeting_id TEXT NOT NULL,
          prior_decision TEXT NOT NULL, applied_decision TEXT NOT NULL,
          actor TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, restored_at TEXT NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX idx_calendar_conflict_changes_active
          ON calendar_conflict_decision_changes(approval_id, tentative_meeting_id)
          WHERE status = 'active';
        CREATE TABLE events (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, employee TEXT NOT NULL,
          summary TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
          sensitivity TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'logged'
        );
        """
    )
    return db


def add_approval(db: sqlite3.Connection, approval_id: str, status: str, source_id: str) -> None:
    db.execute(
        """
        INSERT INTO approvals(
          id, created_at, updated_at, employee, action_type, risk, title, preview,
          destination, status, details_json
        ) VALUES(?, 'now', 'now', 'Mina', 'calendar', 'medium', ?, '', '', ?, ?)
        """,
        (
            approval_id,
            f"Inbox calendar decision needed: {approval_id}",
            status,
            json.dumps({"sourceId": source_id, "calendarEventId": f"event-{source_id}"}),
        ),
    )


def check(name: str, condition: bool) -> bool:
    print(f"[{'ok' if condition else 'FAIL'}] {name}")
    return condition


def status(db: sqlite3.Connection, approval_id: str) -> str:
    return db.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()[0]


def main() -> int:
    db = database()
    add_approval(db, "conflict-app", "tentative", "conflict-1")
    add_approval(db, "conflict-user", "follow", "conflict-2")
    ok = True

    followed_result = appmod.replace_inbox_invite_approvals(
        db,
        [],
        reconcile=False,
        conflict_transitions=[
            {"approvalId": "conflict-app", "tentativeMeetingId": "meeting-a"}
        ],
    )
    ok &= check(
        "tentative conflict becomes follow through the sweep lifecycle",
        followed_result["conflictsFollowed"] == 1 and status(db, "conflict-app") == "follow",
    )
    provenance = db.execute(
        "SELECT * FROM calendar_conflict_decision_changes WHERE approval_id = 'conflict-app'"
    ).fetchone()
    ok &= check(
        "core follow provenance is durable and correlated",
        provenance["actor"] == "dream-team-core"
        and provenance["prior_decision"] == "tentative"
        and provenance["tentative_meeting_id"] == "meeting-a",
    )

    restored_result = appmod.replace_inbox_invite_approvals(
        db,
        [],
        reconcile=False,
        cancelled_meetings=[{"calendarEventId": "meeting-a"}],
    )
    ok &= check(
        "cancellation restores core-created follow to tentative",
        restored_result["conflictsRestored"] == 1 and status(db, "conflict-app") == "tentative",
    )
    repeated_result = appmod.replace_inbox_invite_approvals(
        db,
        [],
        reconcile=False,
        cancelled_meetings=[{"calendarEventId": "meeting-a"}],
    )
    ok &= check(
        "repeated cancellation is idempotent",
        repeated_result["conflictsRestored"] == 0 and status(db, "conflict-app") == "tentative",
    )

    user_changed = appmod.apply_tentative_conflict_follow(
        db,
        {"approvalId": "conflict-user", "tentativeMeetingId": "meeting-b"},
    )
    user_restored = appmod.restore_tentative_conflicts_for_cancellation(
        db, {"calendarEventId": "meeting-b"}
    )
    ok &= check(
        "intentional pre-existing follow is never claimed or restored",
        not user_changed and user_restored == 0 and status(db, "conflict-user") == "follow",
    )

    missing_transition = appmod.apply_tentative_conflict_follow(
        db, {"approvalId": "missing", "tentativeMeetingId": "meeting-c"}
    )
    missing_cancellation = appmod.restore_tentative_conflicts_for_cancellation(db, {})
    ok &= check(
        "missing correlation or provenance is safe",
        not missing_transition and missing_cancellation == 0,
    )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
