#!/usr/bin/env python3
"""Focused offline regression tests for the durable handled-item ledger."""
from __future__ import annotations

import json
import gc
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


HANDLED_AT = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def email_signal(message_id: str, received_at: str, **overrides):
    value = {
        "sourceType": "email",
        "sourceId": message_id,
        "internetMessageId": f"<{message_id}@example.test>",
        "conversationId": "voucher-thread",
        "receivedAt": received_at,
        "subject": "M365 Copilot voucher for Ascend Learning",
        "sender": "Drew Peer <drew@example.test>",
        "summary": "Voucher details for review.",
        "latestMessageDelta": "",
        "explicitAsk": "",
    }
    value.update(overrides)
    return value


def teams_signal(message_id: str, received_at: str):
    return {
        "sourceType": "teams",
        "sourceId": "19:stable-person-chat@unq.gbl.spaces",
        "chatId": "19:stable-person-chat@unq.gbl.spaces",
        "messageId": message_id,
        "receivedAt": received_at,
        "subject": "Message from Alex",
        "sender": "Alex",
        "summary": "Can you review this?",
        "explicitAsk": "Can you review this?",
    }


class HandledItemLedgerTests(unittest.TestCase):
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

    def handle(self, signal, decision="approved"):
        appmod.record_decision_memory(
            self.db,
            appmod.review_signal_action_type(signal),
            signal["subject"],
            signal["sender"],
            signal["sourceId"],
            decision,
            raw=signal,
            now=HANDLED_AT,
        )

    def test_thread_continuity_across_changed_sender_stays_muted(self):
        first = email_signal("message-1", "2026-08-19T12:00:00Z")
        self.handle(first)
        changed_sender = email_signal(
            "message-2",
            "2026-08-20T12:00:00Z",
            sender="Steve Sweet <steve@example.test>",
            summary="Following up with no new request.",
            latestMessageDelta="Friendly follow-up with no requested change.",
        )

        result = appmod.upsert_inbox_signals(self.db, [changed_sender])

        self.assertEqual(result["mutedByMemory"], 1)
        self.assertEqual(
            self.db.execute(
                "SELECT status FROM inbox_signals WHERE source_id='message-2'"
            ).fetchone()["status"],
            "suppressed",
        )

    def test_approved_same_message_stays_muted(self):
        signal = email_signal("message-1", "2026-08-19T12:00:00Z")
        self.handle(signal, "approved")

        result = appmod.upsert_inbox_signals(self.db, [signal])

        self.assertEqual(result["mutedByMemory"], 1)
        self.assertEqual(
            self.db.execute("SELECT mute_reason FROM decision_memory").fetchone()["mute_reason"],
            "Same message as the handled item.",
        )

    def test_new_reply_with_explicit_ask_reopens(self):
        first = email_signal("message-1", "2026-08-19T12:00:00Z")
        self.handle(first)
        reply = email_signal(
            "message-2",
            "2026-08-20T12:00:00Z",
            explicitAsk="Can you approve the revised voucher amount?",
            latestMessageDelta="The voucher amount changed to $2,500.",
        )

        result = appmod.upsert_inbox_signals(self.db, [reply])

        self.assertEqual(result["mutedByMemory"], 0)
        self.assertEqual(
            self.db.execute("SELECT status FROM decision_memory").fetchone()["status"],
            "reopened",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT status FROM approvals WHERE details_json LIKE '%message-2%'"
            ).fetchone()["status"],
            "pending",
        )

    def test_new_reply_without_new_ask_stays_muted(self):
        first = email_signal("message-1", "2026-08-19T12:00:00Z")
        self.handle(first)
        reply = email_signal(
            "message-2",
            "2026-08-20T12:00:00Z",
            summary="Thanks again.",
            latestMessageDelta="Thanks again.",
        )

        result = appmod.upsert_inbox_signals(self.db, [reply])

        self.assertEqual(result["mutedByMemory"], 1)

    def test_negated_change_delta_stays_muted(self):
        first = email_signal("message-1", "2026-08-19T12:00:00Z")
        self.handle(first)
        reply = email_signal(
            "message-2",
            "2026-08-20T12:00:00Z",
            summary="Nothing changed.",
            latestMessageDelta="No new request; the amount and owner are unchanged.",
        )

        result = appmod.upsert_inbox_signals(self.db, [reply])

        self.assertEqual(result["mutedByMemory"], 1)

    def test_retention_tier_expiry_boundaries(self):
        for index, (decision, days) in enumerate(appmod.DECISION_MEMORY_TTL_DAYS.items()):
            with self.subTest(decision=decision):
                signal = email_signal(
                    f"tier-{index}",
                    "2026-08-19T12:00:00Z",
                    conversationId=f"thread-{index}",
                )
                self.handle(signal, decision)
                key = appmod.handled_item_key("email", signal, signal["subject"], signal["sender"])
                before = appmod.active_decision_memory(
                    self.db, HANDLED_AT + timedelta(days=days, microseconds=-1)
                )
                self.assertIn(key, before)
                at_boundary = appmod.active_decision_memory(
                    self.db, HANDLED_AT + timedelta(days=days)
                )
                self.assertNotIn(key, at_boundary)

    def test_restore_returns_item_to_inbox_immediately(self):
        signal = email_signal("message-1", "2026-08-19T12:00:00Z")
        self.handle(signal, "rejected")
        appmod.upsert_inbox_signals(self.db, [signal])
        key = appmod.handled_item_key("email", signal, signal["subject"], signal["sender"])
        self.db.execute(
            "UPDATE decision_memory SET status='cleared' WHERE content_key=?",
            (key,),
        )

        restored = appmod.restore_muted_items(self.db, {key})

        self.assertGreaterEqual(restored, 1)
        self.assertEqual(
            self.db.execute(
                "SELECT status FROM inbox_signals WHERE source_id='message-1'"
            ).fetchone()["status"],
            "active",
        )
        self.assertEqual(
            self.db.execute("SELECT status FROM approvals").fetchone()["status"],
            "pending",
        )

    def test_teams_handling_is_per_message_not_per_person(self):
        first = teams_signal("teams-message-1", "2026-08-19T12:00:00Z")
        later = teams_signal("teams-message-2", "2026-08-20T12:00:00Z")
        self.handle(first, "rejected")

        result = appmod.upsert_inbox_signals(self.db, [later])

        self.assertEqual(result["mutedByMemory"], 0)
        self.assertNotEqual(
            appmod.handled_item_key("teams", first, first["subject"], first["sender"]),
            appmod.handled_item_key("teams", later, later["subject"], later["sender"]),
        )

    def test_teams_without_message_discriminator_is_not_recorded(self):
        signal = teams_signal("teams-message-1", "2026-08-19T12:00:00Z")
        signal.pop("messageId")
        signal.pop("receivedAt")

        self.handle(signal, "rejected")

        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM decision_memory").fetchone()[0],
            0,
        )

    def test_existing_rows_migrate_without_data_loss(self):
        self.db.close()
        gc.collect()
        appmod.DB_PATH.unlink()
        legacy = sqlite3.connect(appmod.DB_PATH)
        legacy.execute(
            """
            CREATE TABLE decision_memory (
                content_key TEXT PRIMARY KEY, action_type TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ttl_until TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )
        legacy.execute(
            "INSERT INTO decision_memory VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "email|old@example.test|voucher", "email", "Voucher", "old@example.test",
                "old-message", "rejected", HANDLED_AT.isoformat(), HANDLED_AT.isoformat(),
                (HANDLED_AT + timedelta(days=10)).isoformat(), "active",
            ),
        )
        legacy.commit()
        legacy.close()

        appmod.init_db()
        gc.collect()
        self.db = appmod.connect()

        row = self.db.execute("SELECT * FROM decision_memory").fetchone()
        self.assertEqual(row["content_key"], "email|old@example.test|voucher")
        self.assertIn("handled_message_id", row.keys())
        self.assertEqual(row["status"], "active")

    def test_existing_row_backfills_thread_and_watermark(self):
        original = email_signal("message-1", "2026-08-19T12:00:00Z")
        legacy_key = appmod.approval_content_key(
            "email", original["subject"], original["sender"]
        )
        self.db.execute(
            """
            INSERT INTO approvals(
                id, created_at, updated_at, employee, action_type, risk, title, preview,
                destination, status, details_json
            ) VALUES('legacy-approval', ?, ?, 'Riley', 'email', 'medium', 'Voucher', '',
                'Email', 'approved', ?)
            """,
            (HANDLED_AT.isoformat(), HANDLED_AT.isoformat(), json.dumps(original)),
        )
        self.db.execute(
            """
            INSERT INTO decision_memory(
                content_key, action_type, subject, sender, source_id, decision,
                created_at, updated_at, ttl_until, status
            ) VALUES(?, 'email', ?, ?, 'message-1', 'approved', ?, ?, ?, 'active')
            """,
            (
                legacy_key, original["subject"], original["sender"], HANDLED_AT.isoformat(),
                HANDLED_AT.isoformat(), (HANDLED_AT + timedelta(days=45)).isoformat(),
            ),
        )

        migrated = appmod.migrate_decision_memory_rows(self.db)
        row = self.db.execute("SELECT * FROM decision_memory").fetchone()

        self.assertEqual(migrated, 1)
        self.assertEqual(row["content_key"], "email|thread:voucher-thread")
        self.assertEqual(row["handled_message_id"], "<message-1@example.test>")
        self.assertEqual(row["handled_received_at"], "2026-08-19T12:00:00Z")

    def test_expired_purge_is_bounded(self):
        expired = (HANDLED_AT - timedelta(days=1)).isoformat()
        for index in range(appmod.DECISION_MEMORY_PURGE_LIMIT + 5):
            self.db.execute(
                """
                INSERT INTO decision_memory(
                    content_key, action_type, decision, created_at, updated_at, ttl_until, status
                ) VALUES(?, 'email', 'approved', ?, ?, ?, 'active')
                """,
                (f"expired-{index}", HANDLED_AT.isoformat(), HANDLED_AT.isoformat(), expired),
            )

        deleted = appmod.purge_expired_decision_memory(
            self.db, HANDLED_AT, appmod.DECISION_MEMORY_PURGE_LIMIT
        )

        self.assertEqual(deleted, appmod.DECISION_MEMORY_PURGE_LIMIT)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM decision_memory").fetchone()[0],
            5,
        )

    def test_export_excludes_local_ledger_and_preferences(self):
        self.handle(email_signal("message-1", "2026-08-19T12:00:00Z"))
        payload = appmod.build_export_zip()
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("export-manifest.json"))
        for table in ("decision_memory", "career_profile", "owned_accounts"):
            self.assertNotIn(f"database/{table}.json", names)
            self.assertNotIn(table, manifest["tables"])


if __name__ == "__main__":
    unittest.main()
