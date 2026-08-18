#!/usr/bin/env python3
"""Focused provider-neutral cost telemetry and bounded budget checks."""
from __future__ import annotations

import gc
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def main() -> int:
    original_db = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "cost-budget.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                now = appmod.utc_now()
                db.execute(
                    "INSERT INTO jobs(id, created_at, updated_at, employee, type, title) "
                    "VALUES('job-cost', ?, ?, 'Major', 'dashboard-chat', 'Bounded work')",
                    (now, now),
                )
                result = appmod.apply_job_cost_telemetry(db, "job-cost", {
                    "costTelemetry": {
                        "modelUsed": "provider-neutral-auto",
                        "aiPath": "reasoning",
                        "promptTokenEstimate": 1200,
                        "contextBytes": 4096,
                        "sourceCount": 4,
                        "elapsedSteps": 2,
                        "reviewHops": 1,
                        "outcome": "in_progress",
                        "offloadTarget": "local-tool",
                        "estimatedCreditClass": "standard",
                    }
                })
                assert result["blocked"] is False

                for index in range(appmod.JOB_BROAD_SWEEP_LIMIT):
                    started = appmod.record_sweep_start(
                        db,
                        source=f"broad-{index}",
                        model="provider-neutral-auto",
                        job_id="job-cost",
                        broad_sweep=True,
                        telemetry={"contextBytes": 1024 + index, "sourceCount": index + 1},
                    )
                    assert started["ok"] is True

                denied = appmod.record_sweep_start(
                    db,
                    source="one-too-many",
                    job_id="job-cost",
                    broad_sweep=True,
                    high_cost_hop=True,
                )
                assert denied["ok"] is False
                assert denied["status"] == "blocked"

                job = db.execute("SELECT * FROM jobs WHERE id='job-cost'").fetchone()
                assert job["status"] == "blocked"
                assert job["outcome"] == "budget_blocked"
                assert job["broad_sweep_count"] == appmod.JOB_BROAD_SWEEP_LIMIT
                assert "Cost budget stopped further work" in job["blocker"]
                assert appmod.job_cost_telemetry(job) == {
                    "modelUsed": "provider-neutral-auto",
                    "aiPath": "reasoning",
                    "outcome": "budget_blocked",
                    "offloadTarget": "local-tool",
                    "estimatedCreditClass": "standard",
                    "promptTokenEstimate": 1200,
                    "contextBytes": 4096,
                    "sourceCount": 4,
                    "elapsedSteps": 2,
                    "reviewHops": 1,
                    "broadSweeps": appmod.JOB_BROAD_SWEEP_LIMIT,
                    "highCostHops": 0,
                }
                audit = db.execute(
                    "SELECT * FROM sweep_runs WHERE id=?", (denied["sweepId"],)
                ).fetchone()
                assert audit["status"] == "blocked"
                assert audit["outcome"] == "budget_blocked"
                assert audit["job_id"] == "job-cost"
                assert audit["broad_sweep"] == 1
                assert audit["high_cost_hop"] == 1

                replay = appmod.apply_job_cost_telemetry(
                    db, "job-cost", {"outcome": "completed"}
                )
                assert replay["blocked"] is True
                job = db.execute("SELECT * FROM jobs WHERE id='job-cost'").fetchone()
                assert job["status"] == "blocked"
                assert job["outcome"] == "budget_blocked"

                try:
                    appmod.apply_job_cost_telemetry(
                        db, "job-cost", {"costTelemetry": {"reviewHops": 0}}
                    )
                except ValueError as exc:
                    assert "cannot decrease" in str(exc)
                else:
                    raise AssertionError("monotonic cost counters must fail closed")

                safeguards = db.execute(
                    "SELECT quality_review, evidence_json, knowledge_links_json "
                    "FROM jobs WHERE id='job-cost'"
                ).fetchone()
                assert dict(safeguards) == {
                    "quality_review": 0,
                    "evidence_json": "",
                    "knowledge_links_json": "[]",
                }

                orphan_id = appmod.record_sweep_finish(
                    db,
                    None,
                    telemetry={
                        "modelUsed": "provider-neutral-auto",
                        "contextBytes": 256,
                        "broadSweeps": 1,
                        "highCostHops": 1,
                    },
                    job_id="job-cost",
                )
                orphan = db.execute(
                    "SELECT * FROM sweep_runs WHERE id=?", (orphan_id,)
                ).fetchone()
                assert orphan["job_id"] == "job-cost"
                assert orphan["model_used"] == "provider-neutral-auto"
                assert orphan["context_bytes"] == 256
                db.commit()
            finally:
                db.close()

            print("[ok] cost telemetry and job/sweep budget guards")
            return 0
        finally:
            appmod.DB_PATH = original_db
            gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
