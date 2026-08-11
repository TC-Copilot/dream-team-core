#!/usr/bin/env python3
"""Targeted tests for teams_message_to_plain_text (app/app.py).

Microsoft Graph returns Teams chat message bodies as HTML, so summaries/recommendations quoted
from a Teams message can carry raw <p>/<b>/<ol>/<li> markup. This converts that into readable
plain text -- paragraph breaks, numbered/bulleted list items, emphasis as plain text, readable
source URLs, and decoded HTML entities -- instead of leaking tags into the dashboard or the
outbound Teams reply. Run directly: `python test/test_teams_text_format.py`.
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

    if not ok:
        print("\nFAILED")
        return 1
    print("\nAll teams_message_to_plain_text checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
