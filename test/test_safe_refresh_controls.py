#!/usr/bin/env python3
from __future__ import annotations

import gc
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


class SafeRefreshControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = appmod.DB_PATH
        appmod.DB_PATH = pathlib.Path(self.tmp.name) / "daily_flow.db"
        appmod.init_db()
        self.db = appmod.connect()

    def tearDown(self):
        self.db.close()
        appmod.DB_PATH = self.original_db_path
        gc.collect()
        self.tmp.cleanup()

    def test_history_window_accepts_only_integer_one_through_five(self):
        for value in range(1, 6):
            self.assertEqual(appmod.validate_history_window_days(value), value)
        for value in (0, 6, -1, 1.5, "3", True, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    appmod.validate_history_window_days(value)

    def test_queue_is_bounded_explicit_and_idempotent_while_active(self):
        first = appmod.queue_fresh_history_sweep(self.db, 3)
        second = appmod.queue_fresh_history_sweep(self.db, 3)
        self.assertTrue(first["queued"])
        self.assertFalse(second["queued"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["jobId"], second["jobId"])

        job = self.db.execute("SELECT * FROM jobs WHERE id = ?", (first["jobId"],)).fetchone()
        self.assertEqual(job["employee"], "Scout")
        self.assertEqual(job["history_window_days"], 3)
        self.assertEqual(job["type"], "fresh-history-sweep")
        for required in (
            "Outlook email",
            "Teams chats",
            "Teams channels",
            "authorized Microsoft 365 tools/connectors",
            "Do not fabricate",
            "Never delete",
        ):
            self.assertIn(required, job["instructions"])

        status = appmod.refresh_control_status(self.db)
        self.assertEqual(status["jobs"][0]["progress"], 0)
        self.db.execute(
            "UPDATE jobs SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?",
            (appmod.utc_now(), appmod.utc_now(), first["jobId"]),
        )
        third = appmod.queue_fresh_history_sweep(self.db, 3)
        self.assertTrue(third["queued"])
        self.assertNotEqual(first["jobId"], third["jobId"])

        appmod.reset_processing_cache(self.db)
        fourth = appmod.queue_fresh_history_sweep(self.db, 3)
        self.assertTrue(fourth["queued"])
        self.assertEqual(fourth["generation"], 1)
        prior = self.db.execute(
            "SELECT status, blocker FROM jobs WHERE id = ?", (third["jobId"],)
        ).fetchone()
        self.assertEqual(prior["status"], "blocked")
        self.assertIn("Superseded by processing reset generation 1", prior["blocker"])

    def test_concurrent_resets_and_queue_clicks_are_serialized(self):
        def reset_once():
            with appmod.connect() as db:
                return appmod.reset_processing_cache(db)["generation"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            generations = sorted(pool.map(lambda _: reset_once(), range(2)))
        self.assertEqual(generations, [1, 2])

        def queue_once():
            with appmod.connect() as db:
                return appmod.queue_fresh_history_sweep(db, 5)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: queue_once(), range(4)))
        self.assertEqual(sum(1 for result in results if result["queued"]), 1)
        self.assertEqual(len({result["jobId"] for result in results}), 1)
        self.assertTrue(all(result["generation"] == 2 for result in results))

        job = self.db.execute(
            "SELECT * FROM jobs WHERE id = ?", (results[0]["jobId"],)
        ).fetchone()
        self.assertEqual(
            appmod.fresh_history_update_error(self.db, job, {"resetGeneration": 2}),
            "",
        )
        self.assertIn(
            "must match",
            appmod.fresh_history_update_error(self.db, job, {"resetGeneration": 1}),
        )
        appmod.reset_processing_cache(self.db)
        self.assertIn(
            "superseded",
            appmod.fresh_history_update_error(self.db, job, {"resetGeneration": 2}),
        )

    def test_cache_reset_preserves_durable_rows_and_never_touches_files(self):
        now = appmod.utc_now()
        self.db.execute(
            "INSERT INTO approvals(id, created_at, updated_at, employee, action_type, risk, "
            "title, preview, destination, status) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("approval-safe", now, now, "Riley", "email", "low", "Keep", "Keep", "Inbox", "pending"),
        )
        self.db.execute(
            "INSERT INTO decision_memory(content_key, action_type, subject, sender, decision, "
            "created_at, updated_at, ttl_until, status) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("handled-safe", "email", "Handled", "sender@example.com", "approved",
             now, now, "2099-01-01T00:00:00Z", "active"),
        )
        document = pathlib.Path(self.tmp.name) / "Documents" / "ScoutTeam" / "keep.txt"
        document.parent.mkdir(parents=True)
        document.write_text("keep", encoding="utf-8")

        with (
            mock.patch.object(pathlib.Path, "unlink", side_effect=AssertionError("file deletion")),
            mock.patch.object(pathlib.Path, "rmdir", side_effect=AssertionError("directory deletion")),
            mock.patch.object(appmod.shutil, "rmtree", side_effect=AssertionError("tree deletion")),
        ):
            first = appmod.reset_processing_cache(self.db)
            second = appmod.reset_processing_cache(self.db)

        self.assertEqual(first["generation"], 1)
        self.assertEqual(second["generation"], 2)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM approvals WHERE id='approval-safe'").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM decision_memory WHERE content_key='handled-safe'"
            ).fetchone()[0],
            1,
        )
        self.assertTrue(document.exists())
        self.assertEqual(document.read_text(encoding="utf-8"), "keep")
        self.assertIn("OneDrive Documents/ScoutTeam", second["preserved"])

    def test_dashboard_contract_contains_warning_and_bounded_selector(self):
        html = (REPO_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        script = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        for value in range(1, 6):
            self.assertIn(f'<option value="{value}"', html)
        self.assertNotIn('<option value="0"', html)
        self.assertNotIn('<option value="6"', html)
        self.assertIn("Documents/ScoutTeam", html)
        self.assertIn("/api/processing-cache/reset", script)
        self.assertIn("/api/history-sweeps", script)
        self.assertIn("window.DreamTeamPwa.refreshCaches()", script)


if __name__ == "__main__":
    unittest.main()
