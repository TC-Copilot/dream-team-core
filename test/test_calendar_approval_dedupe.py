#!/usr/bin/env python3
"""Focused regression tests for durable calendar approval de-duplication."""
from __future__ import annotations

import gc
import json
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def invite(**overrides):
    value = {
        "subject": "ModusLink x Microsoft EA Discussions Cont'd",
        "organizer": "organizer@example.com",
        "when": "2026-08-24T16:00:00-05:00",
        "conflictSummary": "No conflicts.",
        "recommendation": "Accept.",
    }
    value.update(overrides)
    return value


class CalendarApprovalDedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = appmod.DB_PATH
        appmod.DB_PATH = pathlib.Path(self.tmp.name) / "daily_flow.db"
        appmod.init_db()
        gc.collect()
        self.db = appmod.connect()

    def tearDown(self):
        self.db.close()
        appmod.DB_PATH = self.original_db_path
        gc.collect()
        self.tmp.cleanup()

    def pending(self):
        return self.db.execute(
            "SELECT * FROM approvals WHERE action_type='calendar' AND status='pending' ORDER BY id"
        ).fetchall()

    def test_exact_duplicate_records_are_durably_superseded(self):
        details = json.dumps({
            "about": invite()["subject"],
            "organizer": invite()["organizer"],
            "rawMeetingTime": invite()["when"],
            "sourceId": "message-a",
        })
        for approval_id in ("legacy-a", "legacy-b"):
            self.db.execute(
                """
                INSERT INTO approvals(
                  id, created_at, updated_at, employee, action_type, risk, title,
                  preview, destination, status, details_json
                ) VALUES(?, '2026-08-21T12:00:00Z', '2026-08-21T12:00:00Z',
                  'Mina', 'calendar', 'medium', ?, '', '', 'pending', ?)
                """,
                (approval_id, f"Inbox calendar decision needed: {invite()['subject']}", details),
            )

        self.db.commit()
        state = appmod.get_state()

        self.assertEqual(
            len([item for item in state["approvals"] if item["action_type"] == "calendar"]),
            1,
        )
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM approvals WHERE action_type='calendar' AND status='superseded'"
            ).fetchone()[0],
            1,
        )

    def test_repeated_sync_and_cross_pipeline_upsert_stay_single(self):
        first = invite(
            id="message-a",
            eventId="provider-event-a",
            recommendation="Accept after the initial check.",
        )
        appmod.replace_inbox_invite_approvals(self.db, [first], reconcile=False)
        appmod.replace_inbox_invite_approvals(self.db, [first], reconcile=False)
        appmod.create_calendar_card_from_signal(
            self.db,
            invite(
                sourceId="different-message-id",
                sourceType="email",
                recommendation="Decline after a newer conflict check.",
            ),
            appmod.utc_now(),
        )

        self.assertEqual(len(self.pending()), 1)
        self.assertIn("Decline after a newer conflict check.", self.pending()[0]["preview"])
        self.assertEqual(
            json.loads(self.pending()[0]["details_json"])["eventId"],
            "provider-event-a",
        )

    def test_distinct_provider_event_ids_are_not_collapsed(self):
        appmod.replace_inbox_invite_approvals(
            self.db,
            [
                invite(id="message-a", eventId="provider-event-a"),
                invite(id="message-b", eventId="provider-event-b"),
            ],
            reconcile=False,
        )

        self.assertEqual(len(self.pending()), 2)

    def test_distinct_occurrences_and_terminal_decisions_are_preserved(self):
        appmod.replace_inbox_invite_approvals(
            self.db,
            [
                invite(id="message-a", when="2026-08-24T16:00:00-05:00"),
                invite(id="message-b", when="2026-08-31T16:00:00-05:00"),
            ],
            reconcile=False,
        )
        first = self.pending()[0]
        self.db.execute("UPDATE approvals SET status='tentative' WHERE id=?", (first["id"],))
        appmod.replace_inbox_invite_approvals(
            self.db,
            [invite(id="message-c", when="2026-08-24T16:00:00-05:00")],
            reconcile=False,
        )

        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM approvals WHERE action_type='calendar' AND status='tentative'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(len(self.pending()), 1)


if __name__ == "__main__":
    unittest.main()
