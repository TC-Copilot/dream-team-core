#!/usr/bin/env python3
"""Targeted tests for deadline-driven calendar auto-scheduling (app/app.py).

Covers:
1. extract_signal_deadline: only an EXPLICIT deadline/due field counts (deadline, dueDate,
   dueBy, deadlineAt, dueAt) -- never inferred from subject/summary text, so ordinary signals
   without one of these fields are completely unaffected.
2. deadline_within_autoschedule_window: true only for a future deadline within the configured
   lookahead (default 2 days == due today/tomorrow); false for past deadlines or ones further out.
3. review_signal_action_type: routes to "deadline-block" only when DEADLINE_AUTOSCHEDULE_ENABLED
   is on AND the signal carries a qualifying deadline -- with the feature off (the default), a
   signal with an explicit deadline field still classifies exactly as it always did (email/teams/
   etc.), proving this is a strictly additive, opt-in lane.
4. stable_deadline_block_approval_id: deterministic per sourceId (or subject+deadline fallback),
   so a repeat sweep of the same item reuses the same approval id instead of duplicating cards
   or re-queuing a second scheduling job.
5. build_deadline_block_preview: renders the current event outcome (scheduling/created/blocked/
   cancelled) so the card reflects reality after Tilly's job reports back.

Run directly: `python test/test_deadline_autoschedule.py`.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        print(f"[ok] {name}")
        return True
    print(f"[FAIL] {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    ok = True
    now = datetime.now(appmod.APP_TIMEZONE)

    # --- extract_signal_deadline -------------------------------------------------------------
    tomorrow_iso = (now + timedelta(days=1)).isoformat()
    ok &= check(
        "extract_signal_deadline reads 'deadline' field",
        appmod.extract_signal_deadline({"deadline": tomorrow_iso}) is not None,
        True,
    )
    ok &= check(
        "extract_signal_deadline reads 'dueDate' field",
        appmod.extract_signal_deadline({"dueDate": tomorrow_iso}) is not None,
        True,
    )
    ok &= check(
        "extract_signal_deadline ignores free text, no explicit field -> None",
        appmod.extract_signal_deadline({"subject": "Survey due tomorrow, please complete"}),
        None,
    )
    ok &= check(
        "extract_signal_deadline tolerates non-dict input",
        appmod.extract_signal_deadline("not a dict"),
        None,
    )

    # --- deadline_within_autoschedule_window --------------------------------------------------
    ok &= check(
        "a deadline 1 day out is within the default 2-day window",
        appmod.deadline_within_autoschedule_window(now + timedelta(days=1)),
        True,
    )
    ok &= check(
        "a deadline in the past is never within the window",
        appmod.deadline_within_autoschedule_window(now - timedelta(hours=1)),
        False,
    )
    ok &= check(
        "a deadline 10 days out is outside the default window",
        appmod.deadline_within_autoschedule_window(now + timedelta(days=10)),
        False,
    )

    # --- review_signal_action_type: opt-in gating -------------------------------------------
    original_enabled = appmod.DEADLINE_AUTOSCHEDULE_ENABLED
    try:
        appmod.DEADLINE_AUTOSCHEDULE_ENABLED = False
        ok &= check(
            "feature OFF (default): a signal with an explicit deadline still classifies as email, unaffected",
            appmod.review_signal_action_type({"subject": "Quarterly survey", "summary": "Please complete", "deadline": tomorrow_iso}),
            "email",
        )
        appmod.DEADLINE_AUTOSCHEDULE_ENABLED = True
        ok &= check(
            "feature ON: a signal with a near-term explicit deadline routes to deadline-block",
            appmod.review_signal_action_type({"subject": "Quarterly survey", "summary": "Please complete", "deadline": tomorrow_iso}),
            "deadline-block",
        )
        far_iso = (now + timedelta(days=30)).isoformat()
        ok &= check(
            "feature ON but deadline far outside the lookahead window -> not deadline-block",
            appmod.review_signal_action_type({"subject": "Annual review", "summary": "Prep needed", "deadline": far_iso}),
            "email",
        )
        ok &= check(
            "feature ON but no explicit deadline field -> unaffected, classifies normally",
            appmod.review_signal_action_type({"subject": "Survey due tomorrow", "summary": "Please complete"}),
            "email",
        )
    finally:
        appmod.DEADLINE_AUTOSCHEDULE_ENABLED = original_enabled

    # --- stable_deadline_block_approval_id: deterministic, dedupe-friendly -------------------
    id_a = appmod.stable_deadline_block_approval_id("Quarterly survey", tomorrow_iso, "msg-123")
    id_b = appmod.stable_deadline_block_approval_id("Quarterly survey", tomorrow_iso, "msg-123")
    id_c = appmod.stable_deadline_block_approval_id("Quarterly survey", tomorrow_iso, "msg-456")
    ok &= check("same sourceId -> same stable approval id (repeat sweeps dedupe)", id_a, id_b)
    ok &= check("different sourceId -> different stable approval id", id_a != id_c, True)
    ok &= check("stable id has the expected prefix", id_a.startswith("approval_deadline_"), True)
    id_no_source = appmod.stable_deadline_block_approval_id("Quarterly survey", tomorrow_iso, "")
    id_no_source_2 = appmod.stable_deadline_block_approval_id("Quarterly survey", tomorrow_iso, "")
    ok &= check("no sourceId falls back to subject+deadline, still deterministic", id_no_source, id_no_source_2)

    # --- build_deadline_block_preview: reflects current event outcome -----------------------
    preview_scheduling = appmod.build_deadline_block_preview({
        "about": "Quarterly survey", "deadline": "Tomorrow 5:00 PM", "eventStatus": "scheduling",
    })
    ok &= check("preview (scheduling) mentions Tilly is finding time", "Scheduling" in preview_scheduling, True)

    preview_created = appmod.build_deadline_block_preview({
        "about": "Quarterly survey", "deadline": "Tomorrow 5:00 PM", "eventStatus": "created",
        "eventLink": "https://outlook.office.com/calendar/item/abc123",
    })
    ok &= check("preview (created) mentions Created", "Created" in preview_created, True)
    ok &= check("preview (created) includes the event link", "abc123" in preview_created, True)

    preview_blocked = appmod.build_deadline_block_preview({
        "about": "Quarterly survey", "deadline": "Tomorrow 5:00 PM", "eventStatus": "blocked",
    })
    ok &= check("preview (blocked) mentions no conflict-free slot", "Not created" in preview_blocked, True)

    preview_cancelled = appmod.build_deadline_block_preview({
        "about": "Quarterly survey", "deadline": "Tomorrow 5:00 PM", "eventStatus": "cancelled",
    })
    ok &= check("preview (cancelled) mentions cancellation", "Cancelled" in preview_cancelled, True)

    # --- decision set stays a separate lane from calendar RSVP -------------------------------
    ok &= check("deadline-block decisions are acknowledged/rejected only", appmod.DEADLINE_BLOCK_DECISIONS, {"acknowledged", "rejected"})
    ok &= check("deadline-block decisions never overlap with calendar RSVP decisions",
                appmod.DEADLINE_BLOCK_DECISIONS.isdisjoint(appmod.CALENDAR_DECISIONS), True)

    if not ok:
        print("\nFAILED")
        return 1
    print("\nAll deadline auto-scheduling checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
