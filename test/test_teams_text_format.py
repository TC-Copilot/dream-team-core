#!/usr/bin/env python3
"""Targeted tests for teams_message_to_plain_text and sanitize_review_signal_html (app/app.py).

Microsoft Graph returns Teams chat message bodies as HTML, so summaries/recommendations quoted
from a Teams message -- or any generated prep-brief/job-result delivery content -- can carry raw
<p>/<h2>/<hr>/<b>/<ol>/<li> markup. This converts that into readable plain text -- paragraph and
heading breaks, numbered/bulleted list items, emphasis as plain text, readable source URLs, and
decoded HTML entities -- instead of leaking tags into the dashboard or an outbound Teams send.
sanitize_review_signal_html is the single choke point in upsert_inbox_signals that applies this to
every non-email/calendar review signal regardless of action_type, closing the gap where a
Teams-sourced item classified as e.g. meeting-prep previously skipped cleanup entirely.
Run directly: `python test/test_teams_text_format.py`.
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, actual: str, expected: str) -> bool:
    if actual == expected:
        print(f"[ok] {name}")
        return True
    print(f"[FAIL] {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    fn = appmod.teams_message_to_plain_text
    ok = True

    ok &= check("plain text passes through unchanged", fn("Hello, can you review this by Friday?"),
                "Hello, can you review this by Friday?")

    ok &= check("paragraphs become blank-line breaks", fn("<p>First paragraph.</p><p>Second paragraph.</p>"),
                "First paragraph.\n\nSecond paragraph.")

    ok &= check("br becomes a line break", fn("Line one<br>Line two<br/>Line three"),
                "Line one\nLine two\nLine three")

    ok &= check("bold/italic/span tags are dropped, text kept",
                fn("<b>Bold</b> and <i>italic</i> and <span style=\"color:red\">colored</span> text"),
                "Bold and italic and colored text")

    ok &= check("ordered list becomes numbered lines", fn("<ol><li>One</li><li>Two</li><li>Three</li></ol>"),
                "1. One\n2. Two\n3. Three")

    ok &= check("unordered list becomes dashed lines", fn("<ul><li>Alpha</li><li>Beta</li></ul>"),
                "- Alpha\n- Beta")

    ok &= check("two separate lists each restart numbering",
                fn("<ol><li>A1</li><li>A2</li></ol><ul><li>B1</li></ul>"),
                "1. A1\n2. A2\n\n- B1")

    ok &= check("link becomes readable 'label (url)' text",
                fn('Please see <a href="https://example.com/doc">the doc</a> for details.'),
                "Please see the doc (https://example.com/doc) for details.")

    ok &= check("link with no distinct label just shows the url",
                fn('<a href="https://example.com">https://example.com</a>'),
                "https://example.com")

    ok &= check("html entities are decoded",
                fn("Tom &amp; Jerry said &quot;hi&quot; &mdash; 100&nbsp;%% done"),
                "Tom & Jerry said \"hi\" \u2014 100 %% done")

    ok &= check("combined paragraph + list + emphasis from a realistic Teams message",
                fn("<p>Hi <b>there</b>, quick recap:</p><ol><li>Ship the fix</li>"
                   "<li>Notify the <i>team</i></li></ol><p>Thanks!</p>"),
                "Hi there, quick recap:\n\n1. Ship the fix\n2. Notify the team\nThanks!")

    ok &= check("plain text with only entities still decodes", fn("Q1 &amp; Q2 results"), "Q1 & Q2 results")

    ok &= check("empty/None input returns empty string", fn(""), "")
    ok &= check("None input returns empty string", fn(None), "")

    # Regression coverage for the v4.5.3 "generated prep brief" leak: a real Teams-sourced item can
    # get generated content -- prep briefs, job-result notifications, delivery messages -- with
    # headings, rules, lists, emphasis, and links, not just <p>/<b>/<ol>/<li>. Uses the exact tag
    # set reported as still leaking raw markup: p/h2/h3/hr/b/i/ol/ul/li/link.
    prep_brief = (
        "<h2>Prep Brief: QBR</h2>"
        "<p>Here is what I found <b>so far</b>:</p>"
        "<h3>Key risks</h3>"
        "<ul><li>Budget <i>overrun</i> risk</li><li>Headcount gap</li></ul>"
        "<hr>"
        "<h3>Next steps</h3>"
        "<ol><li>Confirm numbers</li><li>Share with Heather</li></ol>"
        '<p>Source: <a href="https://example.com/qbr">QBR notes</a>.</p>'
    )
    prep_brief_expected = (
        "Prep Brief: QBR\n\n"
        "Here is what I found so far:\n\n"
        "Key risks\n\n"
        "- Budget overrun risk\n"
        "- Headcount gap\n\n"
        "Next steps\n\n"
        "1. Confirm numbers\n"
        "2. Share with Heather\n"
        "Source: QBR notes (https://example.com/qbr)."
    )
    prep_brief_out = fn(prep_brief)
    ok &= check("generated prep-brief markup (h2/h3/hr/p/b/i/ol/ul/li/link) has no raw tags",
                "<" in prep_brief_out, False)
    ok &= check("generated prep-brief markup converts to readable structured text",
                prep_brief_out, prep_brief_expected)
    ok &= check("running the converter twice on the same content is a no-op (idempotent)",
                fn(prep_brief_out), prep_brief_out)

    ok &= check("headings become their own line, not glued to surrounding text",
                fn("<h2>Title</h2><p>Body</p>"), "Title\n\nBody")
    ok &= check("hr becomes a clean paragraph break, not a leaked tag or vanished separator",
                fn("<p>Above</p><hr><p>Below</p>"), "Above\n\nBelow")

    # sanitize_review_signal_html: the centralized choke point every non-email/calendar review
    # signal passes through (upsert_inbox_signals), regardless of action_type -- this is what
    # closes the gap where a Teams-sourced item classified as meeting-prep/commitment/attachment-
    # review (not action_type == "teams") previously skipped cleanup entirely.
    sanitize = appmod.sanitize_review_signal_html
    meeting_prep_raw = {
        "subject": "Prep for QBR",
        "summary": "<p>Please prep me for the <b>QBR</b> tomorrow.</p><h2>Context</h2><ul><li>Budget</li></ul>",
        "recommendation": "<p>Prepare a <i>one-pager</i>.</p>",
    }
    cleaned = sanitize(meeting_prep_raw, "meeting-prep")
    ok &= check("sanitize_review_signal_html cleans summary for non-teams action_type (meeting-prep)",
                "<" in cleaned["summary"], False)
    ok &= check("sanitize_review_signal_html cleans recommendation for non-teams action_type",
                cleaned["recommendation"], "Prepare a one-pager.")
    email_raw = {"subject": "s", "summary": "<p>Raw HTML kept as-is for email</p>", "recommendation": ""}
    email_cleaned = sanitize(email_raw, "email")
    ok &= check("sanitize_review_signal_html leaves email signals untouched",
                email_cleaned["summary"], email_raw["summary"])
    ok &= check("sanitize_review_signal_html is a no-op copy for plain-text fields",
                sanitize({"summary": "Plain text only"}, "attachment-review")["summary"], "Plain text only")

    # Regression coverage for the uncovered leak: a generated prep-brief/delivery message for an
    # ORDINARY job type (teams-action/dashboard-chat/employee-work -- not routed through the
    # document-backed-draft or artifact-creation chains, which already had their own "plain text"
    # prose) could still reach the user as raw markup, because resultSummary/blocker/chat message
    # were stored verbatim at every /api/jobs/{jobId} update regardless of job type or whether the
    # signal it originated from ever passed through sanitize_review_signal_html at ingestion. The
    # fix reuses this exact same function, unconditionally, on resultSummary/blocker/message in
    # handle_job_update -- confirm it produces clean, readable output for a realistic job-result
    # payload shaped like the one that leaked (a delivery notification with headings/lists/links).
    delivery_message = (
        "<p>Draft ready for review.</p>"
        "<h3>Summary</h3>"
        "<ul><li>Reviewed the Cowork document</li><li>Drafted a note to Heather</li></ul>"
        '<p>See <a href="https://example.com/draft">the draft</a>.</p>'
    )
    result_summary_out = fn(delivery_message)
    ok &= check("a job-result-shaped delivery message has no raw tags after conversion",
                "<" in result_summary_out, False)
    ok &= check("a job-result-shaped delivery message reads as clean structured text",
                result_summary_out,
                "Draft ready for review.\n\nSummary\n\n- Reviewed the Cowork document\n"
                "- Drafted a note to Heather\nSee the draft (https://example.com/draft).")
    # blocker and chat `message` reuse the identical function -- one conversion path, three fields.
    blocker_text = "<p>Blocked: needs <b>Heather's</b> confirmation.</p>"
    ok &= check("blocker text converts the same way as resultSummary", fn(blocker_text),
                "Blocked: needs Heather's confirmation.")
    chat_message_text = "<p>Update: <i>in progress</i>.</p>"
    ok &= check("chat message text converts the same way as resultSummary", fn(chat_message_text),
                "Update: in progress.")

    if not ok:
        print("\nFAILED")
        return 1
    print("\nAll teams_message_to_plain_text checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
