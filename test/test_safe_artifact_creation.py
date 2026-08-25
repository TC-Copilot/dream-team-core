#!/usr/bin/env python3
from __future__ import annotations

import gc
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT / "app"))
import app as appmod  # noqa: E402


class SafeArtifactCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "Scout"
        self.original_db_path = appmod.DB_PATH
        self.original_document_root = appmod.ONEDRIVE_DOCUMENT_ROOT
        self.original_auth_required = appmod.AUTH_REQUIRED
        self.original_local_token = appmod.LOCAL_TOKEN
        appmod.DB_PATH = pathlib.Path(self.tmp.name) / "daily_flow.db"
        appmod.ONEDRIVE_DOCUMENT_ROOT = self.root
        appmod.AUTH_REQUIRED = False
        appmod.init_db()

    def tearDown(self):
        appmod.DB_PATH = self.original_db_path
        appmod.ONEDRIVE_DOCUMENT_ROOT = self.original_document_root
        appmod.AUTH_REQUIRED = self.original_auth_required
        appmod.LOCAL_TOKEN = self.original_local_token
        gc.collect()
        self.tmp.cleanup()

    def _blocked_artifact_job(
        self, job_id: str = "job-artifact", artifact_type: str = "docx"
    ) -> None:
        now = appmod.utc_now()
        with appmod.connect() as db:
            db.execute(
                "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, blocker, "
                "artifact_request, artifact_type, artifact_creation_mode, send_state) "
                "VALUES(?, ?, ?, 'Drew', 'employee-work', 'Create one-page draft', 'blocked', ?, 1, "
                "?, '', 'open_to_send')",
                (
                    job_id,
                    now,
                    now,
                    "File-write/file-creation actions are blocked in this automated background run.",
                    artifact_type,
                ),
            )

    def test_docx_created_under_safe_root_and_non_overwriting(self):
        first = appmod.create_review_artifact(
            {
                "title": "Customer one-pager",
                "filename": "customer-note",
                "format": "docx",
                "content": "Decision summary\nNext step",
            },
            self.root,
        )
        second = appmod.create_review_artifact(
            {
                "title": "Customer one-pager",
                "filename": "customer-note",
                "format": "docx",
                "content": "Updated decision summary",
            },
            self.root,
        )
        self.assertEqual(first["label"], "customer-note.docx")
        self.assertEqual(second["label"], "customer-note-2.docx")
        self.assertEqual(pathlib.Path(first["path"]).parent, self.root.resolve())
        with zipfile.ZipFile(first["path"]) as package:
            self.assertIn("word/document.xml", package.namelist())
            self.assertIn(b"Decision summary", package.read("word/document.xml"))
        with self.assertRaisesRegex(ValueError, "valid Open XML"):
            appmod.create_review_artifact(
                {"title": "Broken", "format": "docx", "content": "bad\u0000content"}, self.root
            )

    def test_text_and_markdown_are_supported(self):
        text = appmod.create_review_artifact(
            {"title": "Email draft", "format": "text", "content": "Hello there"}, self.root
        )
        markdown = appmod.create_review_artifact(
            {"title": "Review notes", "format": "markdown", "content": "# Notes"}, self.root
        )
        self.assertEqual(pathlib.Path(text["path"]).read_text(encoding="utf-8"), "Hello there")
        self.assertEqual(pathlib.Path(markdown["path"]).suffix, ".md")

    def test_pptx_created_from_structured_slides_and_non_overwriting(self):
        request = {
            "title": "Customer decision deck",
            "filename": "customer-decision",
            "format": "pptx",
            "slides": [
                {"title": "Decision", "bullets": ["Approve the pilot", "Start September 1"]},
                {"title": "Next steps", "bullets": ["Confirm owners", "Schedule kickoff"]},
            ],
        }
        first = appmod.create_review_artifact(request, self.root)
        second = appmod.create_review_artifact(request, self.root)
        self.assertEqual(first["label"], "customer-decision.pptx")
        self.assertEqual(second["label"], "customer-decision-2.pptx")
        self.assertEqual(pathlib.Path(first["path"]).parent, self.root.resolve())
        with zipfile.ZipFile(first["path"]) as package:
            names = set(package.namelist())
            self.assertIn("ppt/presentation.xml", names)
            self.assertIn("ppt/slides/slide1.xml", names)
            self.assertIn("ppt/slides/slide2.xml", names)
            self.assertIn(b"Approve the pilot", package.read("ppt/slides/slide1.xml"))
            for name in names:
                if name.endswith(".xml"):
                    ET.fromstring(package.read(name))
        title_only = appmod.create_review_artifact(
            {
                "title": "Title-only deck",
                "format": "pptx",
                "slides": [{"title": "Decision", "bullets": []}],
            },
            self.root,
        )
        with zipfile.ZipFile(title_only["path"]) as package:
            slide_xml = package.read("ppt/slides/slide1.xml")
        self.assertGreaterEqual(slide_xml.count(b"<a:p>"), 2)

    def test_invalid_pptx_slide_payload_is_rejected(self):
        invalid_payloads = [
            None,
            [],
            ["not-an-object"],
            [{"title": "", "bullets": []}],
            [{"title": "Decision", "bullets": "not-an-array"}],
            [{"title": "Decision", "bullets": [42]}],
        ]
        for slides in invalid_payloads:
            with self.subTest(slides=slides):
                with self.assertRaisesRegex(ValueError, "slide"):
                    appmod.create_review_artifact(
                        {"title": "Invalid deck", "format": "pptx", "slides": slides}, self.root
                    )

    def test_traversal_and_unsafe_root_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "plain filename"):
            appmod.create_review_artifact(
                {"title": "Unsafe", "filename": "..\\outside.docx", "format": "docx", "content": "x"},
                self.root,
            )
        with self.assertRaisesRegex(ValueError, "plain filename"):
            appmod.create_review_artifact(
                {"title": "Unsafe", "filename": "report:hidden", "format": "text", "content": "x"},
                self.root,
            )
        with self.assertRaisesRegex(ValueError, "plain filename"):
            appmod.create_review_artifact(
                {"title": "Unsafe", "filename": "bad*name", "format": "text", "content": "x"},
                self.root,
            )
        filesystem_root = pathlib.Path(self.root.anchor)
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            appmod.create_review_artifact(
                {"title": "Unsafe", "format": "text", "content": "x"}, filesystem_root
            )

    def test_registering_artifact_resolves_only_write_blocker_without_send(self):
        self._blocked_artifact_job()
        with appmod.connect() as db:
            result = appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-artifact",
                    "title": "Customer one-pager",
                    "format": "docx",
                    "content": "Review-only content",
                    "createdBy": "Drew",
                },
                self.root,
            )
            job = db.execute("SELECT * FROM jobs WHERE id = 'job-artifact'").fetchone()
            event = db.execute(
                "SELECT * FROM events WHERE summary LIKE 'Review artifact created:%'"
            ).fetchone()

        self.assertTrue(result["blockerResolved"])
        self.assertEqual(result["outboundAction"], "not_performed")
        self.assertTrue(result["requiresApprovalToSend"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["blocker"], "")
        self.assertEqual(job["send_state"], "open_to_send")
        self.assertEqual(job["review_artifact_only"], 1)
        self.assertEqual(job["handoff_to"], "Riley")
        link = json.loads(job["result_link_json"])
        self.assertTrue(link["href"].startswith("/api/documents/"))
        self.assertIsNone(
            appmod.validate_artifact_creation_completion(
                {"creationMode": "created", "link": link["href"]}, "completed"
            )
        )
        self.assertIsNotNone(event)
        self.assertIn("No outbound action was performed", event["detail"])

    def test_redaction_blocker_is_not_cleared(self):
        self._blocked_artifact_job("job-redaction")
        with appmod.connect() as db:
            db.execute(
                "UPDATE jobs SET redaction_required = 1, blocker = 'Redaction required before delivery.' "
                "WHERE id = 'job-redaction'"
            )
            result = appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-redaction",
                    "title": "Redacted draft",
                    "format": "docx",
                    "content": "Review-only content",
                },
                self.root,
            )
            status = db.execute(
                "SELECT status FROM jobs WHERE id = 'job-redaction'"
            ).fetchone()["status"]
        self.assertFalse(result["blockerResolved"])
        self.assertEqual(status, "blocked")

    def test_review_artifact_downgrades_stale_send_state(self):
        self._blocked_artifact_job("job-send-state")
        with appmod.connect() as db:
            db.execute("UPDATE jobs SET send_state = 'sent' WHERE id = 'job-send-state'")
            appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-send-state",
                    "title": "Fresh review draft",
                    "format": "docx",
                    "content": "This content has not been sent.",
                },
                self.root,
            )
            send_state = db.execute(
                "SELECT send_state FROM jobs WHERE id = 'job-send-state'"
            ).fetchone()["send_state"]
        self.assertEqual(send_state, "open_to_send")
        with appmod.connect() as db:
            guarded_job = db.execute(
                "SELECT * FROM jobs WHERE id = 'job-send-state'"
            ).fetchone()
        guard = appmod.job_update_guard(
            guarded_job,
            {"status": "completed", "sendState": "sent"},
            "completed",
        )
        self.assertIsNotNone(guard)
        self.assertEqual(int(guard[1]), 400)

    def test_wrong_artifact_type_cannot_clear_blocked_deck(self):
        self._blocked_artifact_job("job-deck")
        with appmod.connect() as db:
            db.execute("UPDATE jobs SET artifact_type = 'pptx' WHERE id = 'job-deck'")
            with self.assertRaisesRegex(ValueError, "must match the job artifact type pptx"):
                appmod.create_and_register_review_artifact(
                    db,
                    {
                        "jobId": "job-deck",
                        "title": "Deck notes",
                        "format": "markdown",
                        "content": "# Not a deck",
                    },
                    self.root,
                )
            job = db.execute("SELECT * FROM jobs WHERE id = 'job-deck'").fetchone()
        self.assertEqual(job["status"], "blocked")
        self.assertEqual(job["result_link_json"], "")

    def test_pptx_attaches_to_job_requeues_and_remains_review_only(self):
        self._blocked_artifact_job("job-pptx", "pptx")
        with appmod.connect() as db:
            db.execute(
                "UPDATE jobs SET narrative_reviewed = 1, cover_note_composed = 1, "
                "quality_review = 1, quality_verdict = 'pass' WHERE id = 'job-pptx'"
            )
            result = appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-pptx",
                    "title": "Customer decision deck",
                    "format": "pptx",
                    "slides": [
                        {"title": "Decision", "bullets": ["Approve the pilot"]},
                        {"title": "Next steps", "bullets": ["Confirm owners"]},
                    ],
                    "createdBy": "Drew",
                },
                self.root,
            )
            job = db.execute("SELECT * FROM jobs WHERE id = 'job-pptx'").fetchone()
            event = db.execute(
                "SELECT * FROM events WHERE summary = 'Review artifact created: Customer-decision-deck.pptx'"
            ).fetchone()
        link = json.loads(job["result_link_json"])
        self.assertTrue(result["blockerResolved"])
        self.assertEqual(result["outboundAction"], "not_performed")
        self.assertTrue(result["requiresApprovalToSend"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["artifact_type"], "pptx")
        self.assertEqual(job["artifact_creation_mode"], "created")
        self.assertEqual(job["review_artifact_only"], 1)
        self.assertEqual(job["send_state"], "open_to_send")
        self.assertEqual(job["handoff_to"], "Mina")
        self.assertEqual(job["narrative_reviewed"], 0)
        self.assertEqual(job["cover_note_composed"], 0)
        self.assertEqual(job["quality_review"], 0)
        self.assertEqual(job["quality_verdict"], "")
        self.assertTrue(link["href"].endswith(".pptx"))
        self.assertTrue(pathlib.Path(link["oneDrivePath"]).is_file())
        self.assertIsNotNone(event)

    def test_replacing_queued_pptx_rewinds_review_and_routes_to_mina(self):
        self._blocked_artifact_job("job-pptx-revision", "pptx")
        with appmod.connect() as db:
            db.execute(
                "UPDATE jobs SET status = 'queued', blocker = '', handoff_to = 'Major', "
                "narrative_reviewed = 1, cover_note_composed = 1, quality_review = 1, "
                "quality_verdict = 'pass' WHERE id = 'job-pptx-revision'"
            )
            result = appmod.create_and_register_review_artifact(
                db,
                {
                    "jobId": "job-pptx-revision",
                    "title": "Replacement deck",
                    "format": "pptx",
                    "slides": [{"title": "Revised decision", "bullets": ["Review again"]}],
                },
                self.root,
            )
            job = db.execute(
                "SELECT * FROM jobs WHERE id = 'job-pptx-revision'"
            ).fetchone()
        self.assertFalse(result["blockerResolved"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["handoff_to"], "Mina")
        self.assertEqual(job["narrative_reviewed"], 0)
        self.assertEqual(job["cover_note_composed"], 0)
        self.assertEqual(job["quality_verdict"], "")

    def test_http_endpoint_requires_local_auth(self):
        appmod.AUTH_REQUIRED = False
        appmod.LOCAL_TOKEN = "test-local-token"
        server = appmod.ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps(
                {"title": "Authenticated note", "format": "markdown", "content": "# Draft"}
            ).encode("utf-8")
            url = f"http://127.0.0.1:{server.server_address[1]}/api/artifacts"
            unauthenticated = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(unauthenticated, timeout=5)
            self.assertEqual(denied.exception.code, 403)

            authenticated = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-local-token",
                },
                method="POST",
            )
            with urllib.request.urlopen(authenticated, timeout=5) as response:
                payload = json.loads(response.read())
                self.assertEqual(response.status, 201)
            self.assertTrue(payload["ok"])
            self.assertTrue(pathlib.Path(payload["artifact"]["path"]).is_file())
            self.assertEqual(payload["outboundAction"], "not_performed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
