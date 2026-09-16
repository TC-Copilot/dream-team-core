#!/usr/bin/env python3
"""Targeted tests for the document-backed draft-email workflow (app/app.py).

Covers two things:
1. validate_document_backed_completion: a request like "put the Cowork doc I made just before the
   meeting with Heather into a draft email" must be treated as a discovery task that finds the real
   source file before drafting -- never a fabricated standalone summary in its place. This is the
   server-side gate applied in handle_job_update that refuses to let a worker's "completed" claim
   stand when the reported documentStatus does not hold up as real evidence:
     - documentStatus="found" without a non-empty `link` -> forced to blocked (claims found, no proof).
     - documentStatus="not_found" -> always forced to blocked, evidence preserved in the blocker text.
     - documentStatus="attach_failed" -> always forced to blocked, failure/path preserved.
     - documentStatus="capability_blocked" -> always forced to blocked, with blocker text that names
       the missing capability and points at an interactive re-run. This is NOT a verdict about the
       document (it was never ruled out); it is a statement about the execution context, so it is
       additionally marked retryable (document_retryable / document_status_is_retryable) while the
       other non-found verdicts stay terminal.
     - documentStatus="found" WITH a non-empty `link` -> no override; the completed status stands.
     - no documentStatus at all (not a document-backed request) -> no override, existing behavior
       untouched for every other job type (email/Teams/calendar/suggestions).
2. Explicit role ownership, not just prose: looks_like_document_backed_draft_request detects the
   pattern so Major can seed handoffTo=Drew at job creation, and document_draft_next_hop is Major's
   active routing decision through the Drew (discovery) -> Riley (plain-text draft, only once found)
   -> Quinn (final verification) -> Major chain, driven by the document_status/draft_composed/
   quality_verdict stamps.
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

    # Case: capability_blocked -> always blocked, and the blocker text must name the missing
    # capability and point at an interactive re-run WITHOUT implying the document is missing or
    # that an attachment failed. This is the reported failure mode: a background automation session
    # where workiq_resolve_m365_link is not permitted, so the OneDrive sharing link cannot be
    # resolved at all -- the document was never ruled out.
    result = fn(
        {
            "documentStatus": "capability_blocked",
            "documentEvidence": {
                "missingCapability": "workiq_resolve_m365_link",
                "executionContext": "this background automation session",
                "sourceRef": "onedrive-sharing-link:nda-roadmap-deck",
                "reason": "tool is blocked for background sessions",
            },
        },
        "completed",
    )
    ok &= check("capability_blocked overrides status to blocked", result[0] if result else None, "blocked")
    blocker_text = result[1] if result else ""
    ok &= check("capability_blocked blocker names the missing capability",
                "workiq_resolve_m365_link" in blocker_text, True)
    ok &= check("capability_blocked blocker names the execution context",
                "background automation session" in blocker_text, True)
    ok &= check("capability_blocked blocker preserves the source reference",
                "nda-roadmap-deck" in blocker_text, True)
    ok &= check("capability_blocked blocker preserves the reason",
                "blocked for background sessions" in blocker_text, True)
    ok &= check("capability_blocked blocker says the document was not ruled out",
                "not ruled out" in blocker_text, True)
    ok &= check("capability_blocked blocker points at an interactive re-run",
                "re-run" in blocker_text and "interactive" in blocker_text, True)
    ok &= check("capability_blocked blocker does not claim the document is missing",
                "not found" in blocker_text.lower(), False)
    ok &= check("capability_blocked blocker does not claim an attachment failure",
                "attachment preparation failed" in blocker_text.lower(), False)

    # Case: capability_blocked with no evidence at all -> still blocked, still honest, never
    # completes. A worker that reports nothing does not get a free pass.
    result = fn({"documentStatus": "capability_blocked"}, "completed")
    ok &= check("capability_blocked without evidence still overrides to blocked",
                result[0] if result else None, "blocked")
    ok &= check("capability_blocked without evidence still says not ruled out",
                "not ruled out" in (result[1] if result else ""), True)

    # Case: capability_blocked WITH a link present -> a link is never a substitute for the
    # capability; only 'found' can complete, so this is still forced to blocked.
    result = fn({"documentStatus": "capability_blocked", "link": "https://example/x.pptx"}, "completed")
    ok &= check("capability_blocked never completes even with a link present",
                result[0] if result else None, "blocked")

    # Case: capability_blocked reported on a non-completed status -> no override, same as the
    # other statuses (nothing is being falsely marked done yet).
    ok &= check(
        "capability_blocked with non-completed status is not overridden",
        fn({"documentStatus": "capability_blocked"}, "in_progress"),
        None,
    )

    # Retryability must be explicitly represented, not inferred from blocker prose: this is what
    # separates capability_blocked from the terminal verdicts for both the dashboard and an agent.
    retryable = appmod.document_status_is_retryable
    ok &= check("capability_blocked is marked retryable", retryable("capability_blocked"), True)
    ok &= check("not_found is not retryable", retryable("not_found"), False)
    ok &= check("attach_failed is not retryable", retryable("attach_failed"), False)
    ok &= check("found is not retryable", retryable("found"), False)
    ok &= check("empty documentStatus is not retryable", retryable(""), False)
    ok &= check("None documentStatus is not retryable (defensive)", retryable(None), False)
    ok &= check("retryability is case/whitespace tolerant", retryable("  Capability_Blocked "), True)

    # classify_blocker must give the capability block its own actionable code/title, offer retry,
    # and expose retryability -- without disturbing how the terminal document verdicts classify.
    classify = appmod.classify_blocker

    def blocker_detail(**fields):
        row = {
            "blocker": "", "type": "chat", "document_status": "", "artifact_creation_mode": "",
            "result_link_json": "", "artifact_package_json": "{}", "artifact_type": "",
            "redaction_required": 0, "redaction_applied": 0, "outcome": "",
        }
        row.update(fields)
        return classify(row)

    detail = blocker_detail(document_status="capability_blocked")
    ok &= check("capability_blocked classifies as capability_unavailable", detail["code"], "capability_unavailable")
    ok &= check("capability_blocked blocker detail is marked retryable", detail["retryable"], True)
    ok &= check("capability_blocked offers retry as a resolution",
                "retry" in {item["id"] for item in detail["resolutions"]}, True)
    ok &= check("capability_blocked title does not read as a document failure",
                detail["title"], "Needs an interactive run")
    ok &= check("not_found still classifies as document_not_found (unchanged)",
                blocker_detail(document_status="not_found")["code"], "document_not_found")
    ok &= check("not_found is not marked retryable",
                blocker_detail(document_status="not_found")["retryable"], False)
    ok &= check("attach_failed still classifies as attachment_link_failure (unchanged)",
                blocker_detail(document_status="attach_failed")["code"], "attachment_link_failure")
    ok &= check("attach_failed is not marked retryable",
                blocker_detail(document_status="attach_failed")["retryable"], False)
    ok &= check("found without link still classifies as found_without_link (unchanged)",
                blocker_detail(document_status="found")["code"], "found_without_link")

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

    # --- looks_like_document_backed_draft_request: detects the pattern so Major can seed the
    # explicit Drew -> Riley -> Quinn routing at job creation instead of leaving it to prose. ---
    detect = appmod.looks_like_document_backed_draft_request
    ok &= check(
        "detects the reported failure case (Cowork doc + draft email)",
        detect("Created a Cowork document just before the meeting with Heather regarding all of "
               "my accounts. Put it in a draft email I can review and, if I like it, forward to Heather."),
        True,
    )
    ok &= check("detects 'attach the deck to a draft'", detect("Attach the deck to a draft for Sam"), True)
    ok &= check("does not fire on a plain draft request with no document reference",
                detect("Draft a reply to Sam about tomorrow's meeting"), False)
    ok &= check("does not fire on a document reference with no draft/send intent",
                detect("What's in the OneDrive document about Q3 planning?"), False)
    ok &= check("empty message does not fire", detect(""), False)

    # --- document_draft_next_hop: Major's active routing decision through the explicit chain. ---
    next_hop = appmod.document_draft_next_hop
    ok &= check("no document_status yet -> routes to Drew for discovery",
                next_hop({"document_status": "", "draft_composed": 0, "quality_verdict": ""}), "Drew")
    ok &= check("document_status not_found -> routes back to Major (already blocked)",
                next_hop({"document_status": "not_found", "draft_composed": 0, "quality_verdict": ""}), "Major")
    ok &= check("document_status attach_failed -> routes back to Major (already blocked)",
                next_hop({"document_status": "attach_failed", "draft_composed": 0, "quality_verdict": ""}), "Major")
    ok &= check("document_status capability_blocked -> routes back to Major (nothing for Riley/Quinn)",
                next_hop({"document_status": "capability_blocked", "draft_composed": 0, "quality_verdict": ""}),
                "Major")
    ok &= check("found but draft not composed yet -> routes to Riley",
                next_hop({"document_status": "found", "draft_composed": 0, "quality_verdict": ""}), "Riley")
    ok &= check("found and draft composed, no quality verdict yet -> routes to Quinn",
                next_hop({"document_status": "found", "draft_composed": 1, "quality_verdict": ""}), "Quinn")
    ok &= check("found, draft composed, and quality verdict in -> routes back to Major (done)",
                next_hop({"document_status": "found", "draft_composed": 1, "quality_verdict": "pass"}), "Major")
    ok &= check("next_hop tolerates a None job (defensive)", next_hop(None), "Drew")

    # --- schema: retryability is a real, queryable column on jobs, not something the dashboard or
    # an agent has to infer from blocker prose. ---
    import gc
    import tempfile

    original_db_path = appmod.DB_PATH
    # ignore_cleanup_errors: init_db leaves pooled connections that Windows may still hold open;
    # the assertion below is about the schema, not about temp-file teardown.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        appmod.DB_PATH = pathlib.Path(tmp) / "daily_flow.db"
        try:
            appmod.init_db()
            db = appmod.connect()
            try:
                columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            finally:
                db.close()
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()
    ok &= check("jobs table carries document_retryable", "document_retryable" in columns, True)
    ok &= check("jobs table still carries document_status", "document_status" in columns, True)
    ok &= check("jobs table still carries document_evidence_json", "document_evidence_json" in columns, True)

    if not ok:
        print("\nFAILED")
        return 1
    print("\nAll document-backed draft workflow checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
