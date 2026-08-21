#!/usr/bin/env python3
from __future__ import annotations

import gc
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT / "app"))
import app as appmod  # noqa: E402


class BlockerResolutionTests(unittest.TestCase):
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

    def job(self, **overrides):
        base = {
            "id": "job-test",
            "title": "Test blocked work",
            "employee": "Major",
            "type": "employee-work",
            "status": "blocked",
            "blocker": "",
            "document_status": "",
            "artifact_creation_mode": "",
            "artifact_package_json": "{}",
            "artifact_type": "",
            "result_link_json": "",
            "redaction_required": 0,
            "redaction_applied": 0,
            "outcome": "",
        }
        base.update(overrides)
        return base

    def insert_job(self, **overrides):
        job = self.job(**overrides)
        now = appmod.utc_now()
        self.db.execute(
            "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, blocker, "
            "document_status, artifact_creation_mode, artifact_package_json, artifact_type, "
            "result_link_json, redaction_required, redaction_applied, outcome) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job["id"], now, now, job["employee"], job["type"], job["title"], job["status"],
                job["blocker"], job["document_status"], job["artifact_creation_mode"],
                job["artifact_package_json"], job["artifact_type"], job["result_link_json"],
                job["redaction_required"], job["redaction_applied"], job["outcome"],
            ),
        )
        self.db.commit()
        return job["id"]

    def test_classifies_every_known_blocker_and_unknown_fallback(self):
        cases = [
            ("document_not_found", {"document_status": "not_found"}),
            ("attachment_link_failure", {"document_status": "attach_failed"}),
            ("found_without_link", {"document_status": "found"}),
            ("artifact_without_link", {"artifact_creation_mode": "created"}),
            ("copilot_prompt_missing", {"artifact_creation_mode": "copilot_prompt_fallback"}),
            ("worker_reported", {"blocker": "Worker could not complete this provider-neutral action."}),
            ("unresolved_target", {"blocker": "Cannot resolve the recipient for this message."}),
            ("calendar_no_slot", {"blocker": "No available slot matched the requested window."}),
            ("rsvp_blocked", {"type": "calendar-rsvp", "blocker": "Calendar action failed."}),
            ("safety_boundary", {"outcome": "budget_blocked", "blocker": "Cost budget exhausted."}),
            ("redaction_required", {"redaction_required": 1}),
            ("stale_job", {"blocker": "auto-cancelled: queued but never picked up within the stale timeout"}),
            ("stale_job", {"blocker": "Superseded by processing reset generation 4; no source scan was run."}),
            ("blocked_work_approval", {"type": "blocked-work"}),
            ("unknown", {}),
        ]
        for expected, fields in cases:
            with self.subTest(expected=expected, fields=fields):
                detail = appmod.classify_blocker(self.job(**fields))
                self.assertEqual(detail["code"], expected)
                self.assertTrue(detail["title"])
                self.assertTrue(detail["explanation"])
                self.assertTrue(detail["resolutions"])
                self.assertIn("artifact", detail)

    def test_structured_fields_win_over_worker_text(self):
        detail = appmod.classify_blocker(self.job(
            document_status="not_found",
            blocker="Cannot resolve the recipient.",
        ))
        self.assertEqual(detail["code"], "document_not_found")
        boundary = appmod.classify_blocker(self.job(
            document_status="not_found",
            outcome="budget_blocked",
            blocker="Cost budget exhausted while looking for the document.",
            redaction_required=1,
        ))
        self.assertEqual(boundary["code"], "safety_boundary")
        self.assertNotIn("retry", {item["id"] for item in boundary["resolutions"]})

    def test_resolution_validation_cas_and_idempotency(self):
        job_id = self.insert_job(document_status="not_found", blocker="Source document not found: missing.")
        with self.assertRaisesRegex(ValueError, "not offered"):
            appmod.resolve_blocker(self.db, job_id, {
                "resolution": "provide-link", "link": "https://example.test/doc",
                "idempotencyKey": "invalid-resolution",
            })

        result, status = appmod.resolve_blocker(self.db, job_id, {
            "resolution": "retry", "idempotencyKey": "retry-once",
        })
        self.assertEqual(int(status), 200)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(
            self.db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"],
            "queued",
        )

        repeat, _ = appmod.resolve_blocker(self.db, job_id, {
            "resolution": "retry", "idempotencyKey": "retry-once",
        })
        self.assertTrue(repeat["idempotent"])
        conflict, conflict_status = appmod.resolve_blocker(self.db, job_id, {
            "resolution": "cancel", "idempotencyKey": "retry-once",
        })
        self.assertEqual(int(conflict_status), 409)
        self.assertFalse(conflict["ok"])

        stale, stale_status = appmod.resolve_blocker(self.db, job_id, {
            "resolution": "retry", "idempotencyKey": "retry-twice",
        })
        self.assertEqual(int(stale_status), 409)
        self.assertFalse(stale["ok"])

    def test_cancel_requeue_link_and_audit(self):
        cancel_id = self.insert_job(id="job-cancel", blocker="Worker blocked.")
        cancelled, _ = appmod.resolve_blocker(self.db, cancel_id, {
            "resolution": "cancel", "idempotencyKey": "cancel",
        })
        self.assertEqual(cancelled["status"], "cancelled")

        stale_id = self.insert_job(
            id="job-stale",
            blocker="Superseded by processing reset generation 1; no source scan was run.",
            type="fresh-history-sweep",
        )
        requeued, _ = appmod.resolve_blocker(self.db, stale_id, {
            "resolution": "requeue", "idempotencyKey": "requeue",
        })
        self.assertEqual(requeued["status"], "queued")

        link_id = self.insert_job(
            id="job-link", document_status="found",
            blocker="Reported the source document as found, but no attachment or link was provided.",
        )
        linked, _ = appmod.resolve_blocker(self.db, link_id, {
            "resolution": "provide-link",
            "link": "https://example.test/review",
            "idempotencyKey": "link",
        })
        self.assertEqual(linked["status"], "queued")
        stored_link = json.loads(self.db.execute(
            "SELECT result_link_json FROM jobs WHERE id = ?", (link_id,)
        ).fetchone()["result_link_json"])
        self.assertEqual(stored_link["href"], "https://example.test/review")
        trimmed_id = self.insert_job(
            id="job-trimmed-link", document_status="found", blocker="Missing link.",
        )
        appmod.resolve_blocker(self.db, trimmed_id, {
            "resolution": "provide-link",
            "link": " https://example.test/trimmed ",
            "idempotencyKey": "trimmed-link",
        })
        trimmed_link = json.loads(self.db.execute(
            "SELECT result_link_json FROM jobs WHERE id = ?", (trimmed_id,)
        ).fetchone()["result_link_json"])
        self.assertEqual(trimmed_link["href"], "https://example.test/trimmed")
        invalid_link_id = self.insert_job(
            id="job-invalid-link", document_status="found", blocker="Missing link.",
        )
        with self.assertRaisesRegex(ValueError, "usable"):
            appmod.resolve_blocker(self.db, invalid_link_id, {
                "resolution": "provide-link",
                "link": "https://",
                "idempotencyKey": "invalid-link",
            })
        self.assertGreaterEqual(
            self.db.execute("SELECT COUNT(*) FROM events WHERE summary LIKE 'Resolved blocker:%'").fetchone()[0],
            3,
        )

    def test_redaction_cannot_be_bypassed_and_creates_scoped_follow_up(self):
        job_id = self.insert_job(
            id="job-redaction",
            redaction_required=1,
            blocker="Redaction required before delivery.",
        )
        with self.assertRaisesRegex(ValueError, "not offered"):
            appmod.resolve_blocker(self.db, job_id, {
                "resolution": "retry", "idempotencyKey": "unsafe-retry",
            })
        result, status = appmod.resolve_blocker(self.db, job_id, {
            "resolution": "redact-and-retry",
            "note": "Remove the customer identifiers and preserve the substance.",
            "idempotencyKey": "safe-redaction",
        })
        self.assertEqual(int(status), 202)
        follow_up = self.db.execute(
            "SELECT * FROM jobs WHERE id = ?", (result["followUpJobId"],)
        ).fetchone()
        self.assertEqual(follow_up["source"], "blocker-resolution")
        self.assertIn("Do not clear or bypass the gate", follow_up["instructions"])
        self.assertEqual(follow_up["redaction_required"], 1)
        self.assertEqual(follow_up["redaction_applied"], 0)
        self.assertEqual(follow_up["handoff_to"], "Quinn")
        original = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        self.assertEqual(original["redaction_applied"], 0)
        self.assertEqual(original["status"], "cancelled")
        self.assertTrue(appmod.redaction_completion_blocker(follow_up, "completed"))
        self.assertFalse(appmod.redaction_completion_blocker(
            {**dict(follow_up), "redaction_applied": 1}, "completed"
        ))
        guarded = appmod.job_update_guard(
            follow_up,
            {"redactionApplied": True, "qualityVerdict": "pass"},
            "completed",
        )
        self.assertEqual(int(guarded[1]), 400)
        self.assertIsNone(appmod.job_update_guard(
            follow_up,
            {"redactionApplied": True, "qualityVerdict": "pass"},
            "",
        ))

    def test_direction_requires_note_and_creates_major_follow_up(self):
        job_id = self.insert_job(id="job-direction", blocker="Cannot resolve the target.")
        with self.assertRaisesRegex(ValueError, "note is required"):
            appmod.resolve_blocker(self.db, job_id, {
                "resolution": "provide-direction", "idempotencyKey": "missing-note",
            })
        result, status = appmod.resolve_blocker(self.db, job_id, {
            "resolution": "provide-direction",
            "note": "Use the recipient from the original thread only.",
            "idempotencyKey": "with-note",
        })
        self.assertEqual(int(status), 202)
        follow_up = self.db.execute(
            "SELECT * FROM jobs WHERE id = ?", (result["followUpJobId"],)
        ).fetchone()
        self.assertEqual(follow_up["employee"], "Major")
        self.assertIn(job_id, follow_up["instructions"])
        self.assertIn("Never fabricate completion", follow_up["instructions"])
        cancelled = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        guard = appmod.job_update_guard(cancelled, {"status": "completed"}, "completed")
        self.assertEqual(int(guard[1]), 409)

    def test_state_and_job_detail_include_blocker_detail(self):
        job_id = self.insert_job(id="job-detail", document_status="not_found")
        state = appmod.get_state()
        state_job = next(job for job in state["jobs"] if job["id"] == job_id)
        self.assertEqual(state_job["blockerDetail"]["code"], "document_not_found")
        detail = appmod.get_job_detail(job_id)
        self.assertEqual(detail["job"]["blockerDetail"]["code"], "document_not_found")
        self.assertIn("activityTrail", detail)

    def test_http_resolution_endpoint_dispatches(self):
        job_id = self.insert_job(id="job-http", blocker="Worker blocked.")
        server = appmod.ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps({
                "resolution": "cancel",
                "idempotencyKey": "http-cancel",
            }).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/jobs/{job_id}/resolve-blocker",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "cancelled")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
