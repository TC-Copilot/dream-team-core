#!/usr/bin/env python3
"""Focused provider-neutral OOO register contract tests."""
from __future__ import annotations

import gc
import io
import pathlib
import sys
import tempfile
import unittest
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def entry(**overrides):
    value = {
        "personName": "Zoe Partner",
        "startDate": "2026-09-10",
        "endDate": "2026-09-12",
        "sourceType": "calendar",
        "sourceId": "calendar-event-1",
        "sourceLabel": "Calendar: Out of office",
        "sourceUrl": "https://calendar.example/events/1",
        "confidence": 0.95,
        "status": "confirmed",
        "observedAt": "2026-08-21T17:00:00Z",
        "metadata": {"provider": "example", "calendarName": "Availability"},
    }
    value.update(overrides)
    return value


class OooRegisterTests(unittest.TestCase):
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

    def test_repeated_source_upsert_is_idempotent(self):
        first = appmod.upsert_ooo_entries(self.db, [entry()])
        second = appmod.upsert_ooo_entries(
            self.db,
            [entry(confidence=0.98, observedAt="2026-08-22T17:00:00Z")],
        )

        self.assertEqual(first["periodIds"], second["periodIds"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ooo_periods").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ooo_evidence").fetchone()[0], 1)
        self.assertEqual(
            self.db.execute("SELECT confidence FROM ooo_periods").fetchone()[0],
            0.98,
        )

    def test_calendar_and_email_evidence_merge_for_same_period(self):
        appmod.upsert_ooo_entries(
            self.db,
            [
                entry(status="tentative", confidence=0.7),
                entry(
                    sourceType="email",
                    sourceId="email-reply-44",
                    sourceLabel="Email: automatic reply",
                    sourceUrl="https://mail.example/messages/44",
                    receivedAt="2026-08-21T16:45:00Z",
                    confidence=0.9,
                    status="confirmed",
                    metadata={"sender": "zoe@example.com", "subject": "Automatic reply"},
                ),
            ],
        )

        register = appmod.query_ooo_register(self.db)
        period = register["people"][0]["periods"][0]
        self.assertEqual(period["status"], "confirmed")
        self.assertEqual(period["confidence"], 0.9)
        self.assertEqual(
            [item["sourceType"] for item in period["evidence"]],
            ["calendar", "email"],
        )

    def test_distinct_periods_filter_by_overlap_and_people_sort_alphabetically(self):
        appmod.upsert_ooo_entries(
            self.db,
            [
                entry(),
                entry(
                    personName="Amy Customer",
                    startDate="2026-09-01",
                    endDate="2026-09-03",
                    sourceType="email",
                    sourceId="amy-email-1",
                ),
                entry(
                    sourceId="calendar-event-2",
                    startDate="2026-10-01",
                    endDate="2026-10-02",
                ),
            ],
        )

        all_items = appmod.query_ooo_register(self.db)
        september = appmod.query_ooo_register(self.db, "2026-09-02", "2026-09-11")

        self.assertEqual(
            [person["personName"] for person in all_items["people"]],
            ["Amy Customer", "Zoe Partner"],
        )
        self.assertEqual(all_items["totalPeriods"], 3)
        self.assertEqual(september["totalPeriods"], 2)
        self.assertEqual(
            [person["personName"] for person in september["people"]],
            ["Amy Customer", "Zoe Partner"],
        )

    def test_validation_rejects_unsafe_or_ambiguous_payloads(self):
        invalid = [
            entry(endDate="2026-09-09"),
            entry(sourceType="chat"),
            entry(sourceId=""),
            entry(confidence=1.1),
            entry(metadata={"accessToken": "secret"}),
            entry(metadata={"rawBody": "full private email"}),
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    appmod.normalize_ooo_entry(payload)

    def test_private_data_is_omitted_from_export_and_cleared_by_reset(self):
        appmod.upsert_ooo_entries(self.db, [entry()])
        self.db.commit()
        with zipfile.ZipFile(io.BytesIO(appmod.build_export_zip())) as archive:
            names = set(archive.namelist())
        self.assertNotIn("database/ooo_periods.json", names)
        self.assertNotIn("database/ooo_evidence.json", names)

        self.db.close()
        appmod.reset_private_data()
        self.db = appmod.connect()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ooo_periods").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ooo_evidence").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
