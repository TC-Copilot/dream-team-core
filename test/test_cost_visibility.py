#!/usr/bin/env python3
"""Cost visibility and provider-neutral model-routing enforcement checks.

Covers the guarantees that make Daily Flow's credit consumption answerable from the product
instead of by forensics on the SQLite file:

  * a sweep that closes without honest cost telemetry is RECORDED and FLAGGED, never rejected
  * estimatedCreditClass is validated against a defined allowlist
  * a scheduled sweep pinned to a frontier model with no escalation reason is a routing violation
  * /api/cost-summary rolls consumption up by day, model, and source with correct arithmetic
  * sweeps stuck in status='running' are surfaced, never auto-closed
  * the new page ships inside the packaged boundary and the clean-room denylist stays satisfied
"""
from __future__ import annotations

import gc
import json
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


COMPLETE_TELEMETRY = {
    "modelUsed": "provider-neutral-auto",
    "aiPath": "classification",
    "estimatedCreditClass": "low",
    "promptTokenEstimate": 900,
    "contextBytes": 2048,
    "sourceCount": 5,
}


def sweep_row(db, sweep_id):
    return db.execute("SELECT * FROM sweep_runs WHERE id = ?", (sweep_id,)).fetchone()


def check_model_tiers() -> None:
    assert appmod.classify_model_tier("auto") == "routine"
    assert appmod.classify_model_tier("provider-neutral-auto") == "routine"
    assert appmod.classify_model_tier("gpt-5-mini") == "routine"
    assert appmod.classify_model_tier("claude-opus-5") == "frontier"
    assert appmod.classify_model_tier("some-premium-tier") == "frontier"
    assert appmod.classify_model_tier("opus-fast") == "frontier", "frontier markers win over routine"
    assert appmod.classify_model_tier("") == "unspecified"
    assert appmod.classify_model_tier("house-brand-7") == "unknown"

    assert appmod.is_automated_sweep_source("automation") is True
    assert appmod.is_automated_sweep_source("scheduled-work-pulse") is True
    assert appmod.is_automated_sweep_source("dashboard-chat") is False
    assert appmod.is_automated_sweep_source("") is False


def check_credit_class_allowlist() -> None:
    assert "low" in appmod.COST_CREDIT_CLASSES
    for klass in appmod.COST_CREDIT_CLASSES:
        assessment = appmod.assess_sweep_cost_telemetry("automation", {
            "model_used": "auto", "ai_path": "classification",
            "estimated_credit_class": klass, "prompt_token_estimate": 10,
        })
        assert assessment["telemetryComplete"] is True, klass
    rejected = appmod.assess_sweep_cost_telemetry("automation", {
        "model_used": "auto", "ai_path": "classification",
        "estimated_credit_class": "cheap-ish", "prompt_token_estimate": 10,
    })
    assert rejected["telemetryComplete"] is False
    assert "estimatedCreditClass:unrecognized" in rejected["gaps"]


def check_telemetry_enforcement(db) -> None:
    # An empty close is still recorded — losing the sweep would be worse than flagging it.
    bare = appmod.record_sweep_finish(db, None, source="automation")
    row = sweep_row(db, bare)
    assert row is not None, "a sweep that closes without telemetry must still be recorded"
    assert row["telemetry_complete"] == 0
    gaps = row["telemetry_gaps"]
    for required in ("modelUsed", "aiPath", "estimatedCreditClass", "promptTokenEstimate"):
        assert required in gaps, f"{required} must be reported as a telemetry gap ({gaps})"

    # A zero token estimate is not honest telemetry: the live install had 977 sweeps summing to 0.
    zeroed = appmod.record_sweep_finish(db, None, source="automation", telemetry={
        **COMPLETE_TELEMETRY, "promptTokenEstimate": 0,
    })
    assert sweep_row(db, zeroed)["telemetry_complete"] == 0
    assert "promptTokenEstimate" in sweep_row(db, zeroed)["telemetry_gaps"]

    # A complete close is clean.
    good = appmod.record_sweep_finish(db, None, source="automation", telemetry=COMPLETE_TELEMETRY)
    row = sweep_row(db, good)
    assert row["telemetry_complete"] == 1
    assert row["telemetry_gaps"] == ""
    assert row["model_tier"] == "routine"
    assert row["routing_violation"] == 0

    # Telemetry reported at start must count at finish: a two-call sweep is not penalized twice.
    started = appmod.record_sweep_start(
        db, source="automation", model="provider-neutral-auto", telemetry=COMPLETE_TELEMETRY
    )
    assert started["ok"] is True
    split = appmod.record_sweep_finish(db, started["sweepId"], summary="split close")
    assert sweep_row(db, split)["telemetry_complete"] == 1


def check_routing_violations(db) -> None:
    violating = appmod.record_sweep_finish(db, None, source="automation", telemetry={
        **COMPLETE_TELEMETRY, "modelUsed": "claude-opus-5", "estimatedCreditClass": "premium",
    })
    row = sweep_row(db, violating)
    assert row["model_tier"] == "frontier"
    assert row["routing_violation"] == 1, "a scheduled frontier sweep with no reason is a violation"
    assert row["telemetry_complete"] == 1, "a routing violation must not be conflated with a telemetry gap"
    assert row["status"] == "completed", "the app surfaces the violation, it never blocks the close"

    excused = appmod.record_sweep_finish(db, None, source="automation", telemetry={
        **COMPLETE_TELEMETRY, "modelUsed": "claude-opus-5", "estimatedCreditClass": "premium",
    }, escalation_reason="Cross-model review of an external customer commitment.")
    excused_row = sweep_row(db, excused)
    assert excused_row["routing_violation"] == 0
    assert excused_row["escalation_reason"].startswith("Cross-model review")

    # A user-initiated sweep may use the frontier tier without an escalation reason.
    interactive = appmod.record_sweep_finish(db, None, source="dashboard-chat", telemetry={
        **COMPLETE_TELEMETRY, "modelUsed": "claude-opus-5", "estimatedCreditClass": "premium",
    })
    assert sweep_row(db, interactive)["routing_violation"] == 0


def check_stuck_sweeps(db) -> None:
    stale = appmod.record_sweep_start(db, source="automation", model="claude-opus-5")
    stale_id = stale["sweepId"]
    long_ago = (datetime.now(appmod.APP_TIMEZONE)
                - timedelta(minutes=appmod.STUCK_SWEEP_MINUTES + 30)).isoformat()
    db.execute("UPDATE sweep_runs SET started_at=?, created_at=? WHERE id=?",
               (long_ago, long_ago, stale_id))

    fresh = appmod.record_sweep_start(db, source="automation", model="auto")
    summary = appmod.cost_summary(db)
    stuck = summary["guardrails"]["stuckSweeps"]
    stuck_ids = {row["id"] for row in stuck["sweeps"]}
    assert stale_id in stuck_ids, "a sweep running past the threshold must surface"
    assert fresh["sweepId"] not in stuck_ids, "a fresh running sweep is not stuck"
    assert stuck["count"] == summary["totals"]["stuckSweeps"]
    assert stuck["thresholdMinutes"] == appmod.STUCK_SWEEP_MINUTES

    # Surfacing only: the app must never close a stuck sweep for the user.
    assert sweep_row(db, stale_id)["status"] == "running"
    assert sweep_row(db, stale_id)["finished_at"] in (None, "")


def check_rollup_math(db) -> None:
    summary = appmod.cost_summary(db, days=30)
    assert summary["automaticAction"] is False, "the app never acts on cost"
    assert summary["creditClasses"] == list(appmod.COST_CREDIT_CLASSES)

    totals = summary["totals"]
    raw = db.execute("SELECT COUNT(*) AS c, COALESCE(SUM(prompt_token_estimate), 0) AS t "
                     "FROM sweep_runs").fetchone()
    jobs = db.execute("SELECT COUNT(*) AS c, COALESCE(SUM(prompt_token_estimate), 0) AS t "
                      "FROM jobs").fetchone()
    assert totals["sweeps"] == raw["c"]
    assert totals["jobs"] == jobs["c"]
    assert totals["promptTokenEstimate"] == raw["t"] + jobs["t"]

    # Each rollup must partition the same population, not double count it.
    assert sum(row["sweeps"] for row in summary["byDay"]) == totals["sweeps"]
    assert sum(row["sweeps"] for row in summary["bySource"]) == totals["sweeps"]
    assert sum(row["sweeps"] for row in summary["byModel"]) == totals["sweeps"]
    assert sum(row["jobs"] for row in summary["byDay"]) == totals["jobs"]
    assert sum(row["promptTokenEstimate"] for row in summary["byDay"]) == totals["promptTokenEstimate"]

    incomplete = db.execute(
        "SELECT COUNT(*) AS c FROM sweep_runs WHERE telemetry_complete = 0").fetchone()["c"]
    violations = db.execute(
        "SELECT COUNT(*) AS c FROM sweep_runs WHERE routing_violation = 1").fetchone()["c"]
    assert totals["incompleteTelemetry"] == incomplete
    assert totals["routingViolations"] == violations
    assert summary["guardrails"]["incompleteTelemetry"]["count"] == incomplete
    assert summary["guardrails"]["routingViolations"]["count"] == violations
    assert incomplete >= 2 and violations >= 1, "the fixtures must actually exercise both findings"

    # Budget blocks are countable exactly the way the existing outcome='budget_blocked' rows are.
    blocked = db.execute(
        "SELECT COUNT(*) AS c FROM sweep_runs WHERE outcome = 'budget_blocked'").fetchone()["c"]
    blocked += db.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE outcome = 'budget_blocked'").fetchone()["c"]
    assert totals["budgetBlocked"] == blocked
    assert summary["guardrails"]["budgetBlocked"]["count"] == blocked

    # Frontier consumption is attributable to a named model with its tier.
    frontier = [row for row in summary["byModel"] if row.get("tier") == "frontier"]
    assert frontier, "premium-pinned work must be attributable to a frontier-tier model row"
    assert all(row["creditClasses"]["premium"] >= 0 for row in frontier)

    # The window is honored rather than silently unbounded.
    narrow = appmod.cost_summary(db, days=1)
    assert narrow["windowDays"] == 1
    assert appmod.cost_summary(db, days=9999)["windowDays"] == 365


def check_packaging_and_clean_room() -> None:
    page = REPO_ROOT / "app" / "static" / "cost-summary.html"
    assert page.exists(), "the cost page must exist"
    text = page.read_text(encoding="utf-8")
    assert "/api/cost-summary" in text
    assert "index.html" in text, "the page must link back to the dashboard"

    index = (REPO_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="cost-summary.html"' in index, "the dashboard must link the cost page"

    sw = (REPO_ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    assert '"/cost-summary.html"' in sw, "the page must be precached"
    version = re.search(r'CACHE_VERSION\s*=\s*"(v\d+)"', sw)
    assert version and int(version.group(1)[1:]) >= 14, "CACHE_VERSION must be bumped"

    # Packaging boundary: the page ships under app/, which package-share.ps1 already includes,
    # and must not drag in a runtime artifact or a dependency (the app is stdlib only).
    include = (REPO_ROOT / "package-share.ps1").read_text(encoding="utf-8")
    assert "'app'" in include, "app/ must stay in the packaged include list"
    for forbidden in ("config.json", ".local-token", "app/data"):
        assert forbidden not in text, f"the page must not reference the runtime artifact {forbidden}"
    assert "http://" not in text and "https://" not in text, "the page must stay local-only"

    # Clean-room denylist: no personal identifier may ship in the new surfaces.
    denied = ("s" + "haffie", "@micro" + "soft.com", "microsoft-my" + ".sharepoint")
    for path in (page, REPO_ROOT / "test" / "test_cost_visibility.py"):
        body = path.read_text(encoding="utf-8").lower()
        for token in denied:
            assert token not in body, f"{path.name} contains a denied identifier"

    # The routing rule the server enforces must be the one the skill documents.
    skill = (REPO_ROOT / "skills" / "daily-flow-team" / "SKILL.md").read_text(encoding="utf-8")
    assert "Record the reason for escalation" in skill
    assert "estimatedCreditClass" in skill, "the worker must be told to report the credit class"
    api = (REPO_ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    assert "/api/cost-summary" in api

    # The root cause of the empty columns in the wild was that the credit-class vocabulary existed
    # only as an untyped string in two test fixtures, so no caller could know what to send. Both
    # the API reference and the worker skill must name every accepted value, or it recurs.
    for value in appmod.COST_CREDIT_CLASSES:
        assert value in api, f"docs/API.md does not document the credit class {value!r}"
        assert value in skill, f"SKILL.md does not document the credit class {value!r}"


def check_api_response_shape(db) -> None:
    """The finish response must tell the caller it was flagged, without refusing the close."""
    sweep_id = appmod.record_sweep_finish(db, None, source="automation", telemetry={
        "modelUsed": "claude-opus-5", "estimatedCreditClass": "premium",
    })
    row = sweep_row(db, sweep_id)
    payload = {
        "ok": True,
        "sweepId": sweep_id,
        "telemetryComplete": bool(row["telemetry_complete"]),
        "telemetryGaps": [gap for gap in str(row["telemetry_gaps"] or "").split(", ") if gap],
        "modelTier": row["model_tier"],
        "routingViolation": bool(row["routing_violation"]),
        "automaticAction": False,
    }
    assert json.loads(json.dumps(payload))["ok"] is True
    assert payload["telemetryComplete"] is False
    assert "aiPath" in payload["telemetryGaps"]
    assert payload["routingViolation"] is True
    assert payload["automaticAction"] is False

    exposed = appmod.recent_sweeps(db, 5)[0]
    for key in ("telemetryComplete", "telemetryGaps", "modelTier", "escalationReason", "routingViolation"):
        assert key in exposed, f"/api/sweeps must expose {key}"


def main() -> int:
    check_model_tiers()
    check_credit_class_allowlist()
    check_packaging_and_clean_room()

    original_db = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "cost-visibility.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                now = appmod.utc_now()
                db.execute(
                    "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, "
                    "model_used, prompt_token_estimate, estimated_credit_class, outcome) "
                    "VALUES('job-cost-vis', ?, ?, 'Major', 'dashboard-chat', 'Bounded work', "
                    "'provider-neutral-auto', 640, 'standard', 'budget_blocked')",
                    (now, now),
                )
                check_telemetry_enforcement(db)
                check_routing_violations(db)
                check_api_response_shape(db)
                check_stuck_sweeps(db)
                check_rollup_math(db)
                db.commit()
            finally:
                db.close()

            print("[ok] cost visibility, telemetry enforcement, and model-routing guardrails")
            return 0
        finally:
            appmod.DB_PATH = original_db
            gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
