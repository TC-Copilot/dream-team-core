#!/usr/bin/env python3
"""Focused checks for Quality & knowledge metric detail data and dashboard affordances."""
from __future__ import annotations

import gc
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"[ok] {name}")
        return True
    print(f"[FAIL] {name}" + (f" - {detail}" if detail else ""))
    return False


def create_quality_job(db, title: str, *, verdict: str = "", audit: bool = False,
                       redaction_pending: bool = False) -> str:
    job_id = appmod.create_chat_job(db, title, title=title)["jobId"]
    db.execute(
        """
        UPDATE jobs
        SET quality_review = 1, quality_verdict = ?, quality_audit_json = ?,
            redaction_required = ?, redaction_applied = ?
        WHERE id = ?
        """,
        (verdict, '{"status":"complete"}' if audit else "{}", int(redaction_pending),
         0 if redaction_pending else 1, job_id),
    )
    db.commit()
    return job_id


def main() -> int:
    ok = True
    original_db_path = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "daily_flow.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                awaiting_id = create_quality_job(db, "Awaiting review")
                held_id = create_quality_job(db, "Held job", verdict="hold", audit=True)
                redaction_id = create_quality_job(
                    db, "Needs redaction", verdict="pass", redaction_pending=True
                )
                current_entry = appmod.save_knowledge_entry(db, {
                    "type": "decision", "title": "Current decision", "summary": "Still current.",
                })
                overdue_entry = appmod.save_knowledge_entry(db, {
                    "type": "commitment", "title": "Overdue commitment",
                    "summary": "Needs follow-up.", "dueDate": "2000-01-01T00:00:00Z",
                })
                stale_entry = appmod.save_knowledge_entry(db, {
                    "type": "project", "title": "Stale project", "summary": "Needs verification.",
                })
                db.execute(
                    "UPDATE knowledge_entries SET updated_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00Z", stale_entry["id"]),
                )
                db.commit()

                quality = appmod.quality_summary(db)
                knowledge = appmod.knowledge_summary(db)
                awaiting = appmod.dashboard_metric_detail(db, "quality-awaiting")
                held = appmod.dashboard_metric_detail(db, "quality-held")
                reviewed = appmod.dashboard_metric_detail(db, "quality-reviewed")
                audits = appmod.dashboard_metric_detail(db, "content-audits")
                redactions = appmod.dashboard_metric_detail(db, "redaction-pending")
                all_entries = appmod.dashboard_metric_detail(db, "knowledge-entries")
                overdue = appmod.dashboard_metric_detail(db, "knowledge-overdue")
                stale = appmod.dashboard_metric_detail(db, "knowledge-stale")
            finally:
                db.close()

            ok &= check("awaiting detail matches Quinn summary",
                        awaiting["total"] == quality["awaitingReview"] == 1
                        and awaiting["items"][0]["id"] == awaiting_id)
            ok &= check("held detail matches Quinn summary",
                        held["total"] == quality["heldJobs"] == 1
                        and held["items"][0]["id"] == held_id)
            ok &= check("reviewed detail matches Quinn summary",
                        reviewed["total"] == quality["flaggedForReview"] == 3)
            ok &= check("content audit detail includes its audited job",
                        audits["total"] == 1 and audits["items"][0]["id"] == held_id)
            ok &= check("redaction detail includes only pending work",
                        redactions["total"] == 1 and redactions["items"][0]["id"] == redaction_id)
            ok &= check("all Casey entries match knowledge summary",
                        all_entries["total"] == knowledge["totalEntries"] == 3
                        and {item["id"] for item in all_entries["items"]} == {
                            current_entry["id"], overdue_entry["id"], stale_entry["id"],
                        })
            ok &= check("overdue Casey detail matches summary",
                        overdue["total"] == knowledge["overdueCommitments"] == 1
                        and overdue["items"][0]["id"] == overdue_entry["id"])
            ok &= check("stale Casey detail matches summary",
                        stale["total"] == knowledge["staleEntries"] == 1
                        and stale["items"][0]["id"] == stale_entry["id"])
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()

    app_js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    detail_page = (REPO_ROOT / "app" / "static" / "metric-detail.html").read_text(encoding="utf-8")
    ok &= check("zero-valued Quality & knowledge tiles stay inert",
                'if (!numericCount) {\n      return `<div class="g-stat">${content}${infoHtml}</div>`;' in app_js)
    ok &= check("nonzero tiles are accessible links with visible focus styling",
                'class="g-stat-action"' in app_js
                and 'aria-label="View ${numericCount}' in app_js
                and ".g-stat-link:hover, .g-stat-link:focus-within" in (
                    REPO_ROOT / "app" / "static" / "styles.css"
                ).read_text(encoding="utf-8"))
    for metric in sorted(appmod.DASHBOARD_DETAIL_METRICS):
        ok &= check(f"{metric} has a dashboard tile and detail configuration",
                    f'"{metric}"' in app_js and f'"{metric}"' in detail_page)
    ok &= check("detail page loads exact dashboard detail records",
                "/api/dashboard-metric-detail?metric=${encodeURIComponent(metric)}" in detail_page)
    ok &= check("detail endpoint remains protected by the local-token policy",
                "/api/dashboard-metric-detail" in appmod.PRIVATE_GET_PREFIXES)
    ok &= check("detail page sends the saved local token when required",
                'headers: authHeaders()' in detail_page)

    if ok:
        print("\nAll Quality & knowledge metric-detail checks passed.")
        return 0
    print("\nFAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
