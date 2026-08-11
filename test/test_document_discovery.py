#!/usr/bin/env python3
"""Targeted tests for validate_document_backed_completion (app/app.py).

Covers the document-backed draft-email discovery workflow: a request like "put the Cowork doc I
made just before the meeting with Heather into a draft email" must be treated as a discovery task
that finds the real source file before drafting -- never a fabricated standalone summary in its
place. validate_document_backed_completion is the server-side gate applied in handle_job_update
that refuses to let a worker's "completed" claim stand when the reported documentStatus does not
hold up as real evidence:
  - documentStatus="found" without a non-empty `link` -> forced to blocked (claims found, no proof).
  - documentStatus="not_found" -> always forced to blocked, evidence preserved in the blocker text.
  - documentStatus="attach_failed" -> always forced to blocked, failure/path preserved.
  - documentStatus="found" WITH a non-empty `link` -> no override; the completed status stands.
  - no documentStatus at all (not a document-backed request) -> no override, existing behavior
    untouched for every other job type (email/Teams/calendar/suggestions).
Run directly: `python test/test_document_discovery.py`.
"""
from __future__ import annotations

import pathlib
import sys

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
    fn = appmod.validate_document_backed_completion
    ok = True

    # Case: found + real link -> stays completed, no override.
    ok &= check(
        "found with link is not overridden",
        fn({"documentStatus": "found", "link": "/api/documents/cowork-accounts.docx"}, "completed"),
        None,
    )

    # Case: found but no link at all -> fabrication guard fires.
    result = fn({"documentStatus": "found"}, "completed")
    ok &= check("found without link overrides status", result[0] if result else None, "blocked")
    ok &= check("found without link blocker mentions no attachment/link",
                "no attachment or link" in (result[1] if result else ""), True)

    # Case: found but link is blank/whitespace -> still guarded.
    result = fn({"documentStatus": "found", "link": "   "}, "completed")
    ok &= check("found with blank link overrides status", result[0] if result else None, "blocked")

    # Case: not_found -> always blocked, with evidence preserved in the blocker text.
    result = fn(
        {
            "documentStatus": "not_found",
            "documentEvidence": {
                "searchedLocations": ["OneDrive/Documents", "Cowork recents"],
                "searchTerms": "Heather accounts",
                "reason": "no matching document located",
            },
        },
        "completed",
    )
    ok &= check("not_found overrides status to blocked", result[0] if result else None, "blocked")
    blocker_text = result[1] if result else ""
    ok &= check("not_found blocker names searched locations", "OneDrive/Documents" in blocker_text, True)
    ok &= check("not_found blocker names search terms", "Heather accounts" in blocker_text, True)

    # Case: attach_failed -> always blocked, with the source path/reason preserved.
    result = fn(
        {
            "documentStatus": "attach_failed",
            "documentEvidence": {
                "sourcePath": "C:/Users/mina/OneDrive/Cowork/accounts-summary.docx",
                "reason": "upload API returned 413 (file too large)",
            },
        },
        "completed",
    )
    ok &= check("attach_failed overrides status to blocked", result[0] if result else None, "blocked")
    blocker_text = result[1] if result else ""
    ok &= check("attach_failed blocker names the source path", "accounts-summary.docx" in blocker_text, True)
    ok &= check("attach_failed blocker names the failure reason", "413" in blocker_text, True)

    # Case: no documentStatus at all -> not a document-backed request, no override, other job types
    # (email/Teams/calendar/suggestions completions) are completely unaffected.
    ok &= check(
        "no documentStatus means no override (email/Teams/calendar unaffected)",
        fn({"resultSummary": "Replied to Jordan about the Q3 roadmap"}, "completed"),
        None,
    )

    # Case: documentStatus reported but job status is not "completed" (e.g. in_progress) -> no
    # override needed since nothing is being falsely marked done yet.
    ok &= check(
        "documentStatus with non-completed status is not overridden",
        fn({"documentStatus": "not_found"}, "in_progress"),
        None,
    )

    # Case: unrecognised documentStatus value is ignored (defensive parsing).
    ok &= check(
        "unrecognised documentStatus value is ignored",
        fn({"documentStatus": "maybe"}, "completed"),
        None,
    )

    if not ok:
        print("\nFAILED")
        return 1
    print("\nAll validate_document_backed_completion checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
