#!/usr/bin/env python3
"""Contract tests for the dashboard's durable created-artifact report."""
from __future__ import annotations

import gc
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


class CreatedArtifactReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "Scout"
        self.original_db_path = appmod.DB_PATH
        self.original_document_root = appmod.ONEDRIVE_DOCUMENT_ROOT
        appmod.DB_PATH = pathlib.Path(self.tmp.name) / "daily_flow.db"
        appmod.ONEDRIVE_DOCUMENT_ROOT = self.root
        appmod.init_db()
        now = appmod.utc_now()
        with appmod.connect() as db:
            db.execute(
                "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                ("job-created-report", now, now, "Drew", "employee-work", "Prepare customer pack", "queued"),
            )

    def tearDown(self) -> None:
        appmod.DB_PATH = self.original_db_path
        appmod.ONEDRIVE_DOCUMENT_ROOT = self.original_document_root
        gc.collect()
        self.tmp.cleanup()

    def test_empty_state_has_no_created_artifacts(self) -> None:
        self.assertEqual(appmod.get_state()["createdArtifacts"], [])

    def test_all_created_artifacts_are_reported_with_openable_local_links(self) -> None:
        with appmod.connect() as db:
            appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-created-report",
                    "title": "Customer summary",
                    "filename": "customer-summary",
                    "format": "docx",
                    "content": "Summary",
                    "createdBy": "Drew",
                },
                self.root,
            )
            appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-created-report",
                    "title": "Customer notes",
                    "filename": "customer-notes",
                    "format": "markdown",
                    "content": "# Notes",
                    "createdBy": "Drew",
                },
                self.root,
            )

        artifacts = appmod.get_state()["createdArtifacts"]
        self.assertEqual(len(artifacts), 2)
        self.assertEqual({artifact["format"] for artifact in artifacts}, {"docx", "markdown"})
        self.assertEqual({artifact["job_id"] for artifact in artifacts}, {"job-created-report"})
        self.assertEqual({artifact["job_title"] for artifact in artifacts}, {"Prepare customer pack"})
        self.assertEqual({artifact["employee"] for artifact in artifacts}, {"Drew"})
        for artifact in artifacts:
            self.assertTrue(artifact["created_at"])
            self.assertTrue(artifact["href"].startswith("/api/documents/"))
            self.assertTrue(pathlib.Path(artifact["oneDrivePath"]).is_file())


if __name__ == "__main__":
    unittest.main()
