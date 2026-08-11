#!/usr/bin/env python3
"""Targeted tests for local-timezone timestamp formatting (app/app.py).

Locally generated timestamps (created_at/updated_at, via utc_now()) already carry the
resolved APP_TIMEZONE offset, and the frontend (app.js) renders them with the browser's own
default toLocaleString()/toLocaleDateString() (no explicit timeZone override) -- so those
already display correctly in whatever timezone the user's browser/system is in.

format_invite_time() is the one place that bakes a *label* into backend-generated preview
text (e.g. "Wed Jun 19, 2026, 11:40 AM PT" for the calendar "When:" line), and it used to
hardcode the "PT" abbreviation regardless of the timezone actually being displayed in. This
verifies the abbreviation now reflects the real resolved timezone, and that parse_display_time
can still parse whatever abbreviation comes out (so calendar RSVP round-tripping is unaffected).

Run directly: `python test/test_local_timestamps.py`.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"[ok] {name}")
        return True
    print(f"[FAIL] {name}" + (f" - {detail}" if detail else ""))
    return False


def main() -> int:
    ok = True
    original_tz = appmod.APP_TIMEZONE
    original_tz_name = appmod.APP_TIMEZONE_NAME
    try:
        # A non-Pacific timezone must not be mislabeled "PT".
        appmod.APP_TIMEZONE = timezone(timedelta(hours=1), "CET")
        appmod.APP_TIMEZONE_NAME = "CET"
        cet_display = appmod.format_invite_time("2026-08-11T10:00:00Z")
        ok &= check("non-Pacific timezone is not mislabeled 'PT'", "PT" not in cet_display, cet_display)
        ok &= check("non-Pacific timezone shows its own abbreviation", "CET" in cet_display, cet_display)

        # The formatted string must still be parseable back into a datetime (calendar RSVP
        # round-tripping depends on this), regardless of which abbreviation is present.
        parsed_back = appmod.parse_display_time(cet_display)
        ok &= check("formatted time with a non-PT abbreviation still parses back", parsed_back is not None, cet_display)

        # A Pacific timezone must still show a real Pacific abbreviation (PST/PDT), not a
        # generic/empty label -- this is a regression guard, not just a "not PT" check.
        appmod.APP_TIMEZONE = timezone(timedelta(hours=-8), "PST")
        appmod.APP_TIMEZONE_NAME = "America/Los_Angeles"
        pst_display = appmod.format_invite_time("2026-01-11T18:00:00Z")
        ok &= check("Pacific timezone still shows a Pacific abbreviation", "PST" in pst_display or "PDT" in pst_display, pst_display)

        # Non-ISO / unparseable input is returned unchanged rather than raising.
        ok &= check("unparseable input is returned unchanged", appmod.format_invite_time("not a date") == "not a date")
        ok &= check("empty input is returned unchanged", appmod.format_invite_time("") == "")
    finally:
        appmod.APP_TIMEZONE = original_tz
        appmod.APP_TIMEZONE_NAME = original_tz_name

    if ok:
        print("\nAll local-timestamp formatting checks passed.")
        return 0
    print("\nFAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
