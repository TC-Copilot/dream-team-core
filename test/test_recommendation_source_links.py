#!/usr/bin/env python3
"""Targeted safe-source-link and recommendation-card regression checks."""
from __future__ import annotations

import json
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


def main() -> int:
    ok = True

    explicit = appmod.extract_signal_source_link({
        "subject": "Quarterly survey",
        "sourceLinks": [
            {"url": "javascript:alert(1)", "label": "Bad"},
            {"href": "https://forms.example.com/survey?id=7", "label": "Take survey"},
        ],
    }, "email")
    ok &= check("sourceLinks skips unsafe entries and keeps the first safe URL",
                explicit == {"url": "https://forms.example.com/survey?id=7", "label": "Open survey"},
                repr(explicit))
    outlook_id = "AAMk" + "a" * 60
    email_deep_link = appmod.extract_signal_source_link({
        "sourceType": "email",
        "sourceId": outlook_id,
    }, "email")
    ok &= check(
        "email Graph item ID produces an Outlook message deep link",
        email_deep_link == {
            "url": f"https://outlook.office.com/mail/deeplink/read/{outlook_id}",
            "label": "Open email",
        },
        repr(email_deep_link),
    )
    teams_deep_link = appmod.extract_signal_source_link({
        "sourceType": "teams",
        "chatId": "19:chat_123@thread.v2",
        "messageId": "1723000000000",
    }, "teams")
    ok &= check(
        "Teams chat and message IDs produce a Teams message deep link",
        teams_deep_link == {
            "url": "https://teams.microsoft.com/l/message/19%3Achat_123%40thread.v2/"
                   "1723000000000?context=%7B%22chatId%22%3A%2219%3Achat_123%40thread.v2%22%7D",
            "label": "Open Teams message",
        },
        repr(teams_deep_link),
    )
    ok &= check(
        "incomplete Teams identifiers do not invent a source URL",
        appmod.extract_signal_source_link({"sourceType": "teams", "chatId": "19:chat_123@thread.v2"}, "teams") == {},
    )
    preferred_action = appmod.extract_signal_source_link({
        "subject": "Quarterly survey",
        "sourceUrl": "https://outlook.office.com/mail/item/123",
        "sourceLinks": [{"url": "https://forms.example.com/survey/123", "label": "Survey"}],
    }, "email")
    ok &= check("actionable sourceLinks target wins over the container message URL",
                preferred_action.get("url") == "https://forms.example.com/survey/123",
                repr(preferred_action))

    anchored = appmod.extract_signal_source_link({
        "subject": "Please review",
        "body": {
            "contentType": "html",
            "content": '<p>Open <a href="data:text/html,bad">bad</a> or '
                       '<a href="https://example.com/original?x=1&amp;y=2">the original</a>.</p>',
        },
    }, "teams")
    ok &= check("HTML anchors provide a safe fallback without preserving markup",
                anchored == {"url": "https://example.com/original?x=1&y=2", "label": "Open source"},
                repr(anchored))

    plain = appmod.extract_signal_source_link({
        "summary": "Complete this at https://example.com/path?q=1.",
    }, "email")
    ok &= check("plain-text URL extraction trims sentence punctuation",
                plain.get("url") == "https://example.com/path?q=1", repr(plain))
    pixel_only = appmod.extract_signal_source_link({
        "body": {"contentType": "html", "content": '<img src="https://tracker.example.com/pixel?id=1">'},
    }, "email")
    ok &= check("HTML asset and tracking-pixel URLs are not treated as actionable sources",
                pixel_only == {}, repr(pixel_only))

    for unsafe in (
        "javascript:alert(1)", "data:text/html,bad", "file:///C:/secret.txt",
        "https://user:pass@example.com/private", "httpx://example.com",
    ):
        ok &= check(f"unsafe URL rejected: {unsafe.split(':', 1)[0]}",
                    appmod.safe_http_url(unsafe) == "")

    original_db_path = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "daily_flow.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                appmod.upsert_inbox_signals(db, [{
                    "sourceType": "email",
                    "sourceId": "message-123",
                    "subject": "Quarterly survey",
                    "summary": "<p>Please complete the survey.</p>",
                    "recommendation": "<b>Complete it today.</b>",
                    "body": {
                        "contentType": "html",
                        "content": '<p><a href="https://example.com/survey/123">Take the survey</a></p>',
                    },
                    "originalBody": {
                        "contentType": "html",
                        "content": "<p>Original <b>message</b>.</p>",
                    },
                }])
                deadline_base = {
                    "subject": "Quarterly survey",
                    "summary": "Please complete it.",
                    "recommendation": "Complete the survey.",
                    "deadline": "2026-08-14T17:00:00-05:00",
                    "sourceId": "deadline-message-1",
                }
                deadline_id = appmod.create_deadline_block_card_from_signal(
                    db,
                    {**deadline_base, "sourceUrl": "https://outlook.office.com/mail/item/1"},
                    appmod.utc_now(),
                )
                appmod.create_deadline_block_card_from_signal(
                    db,
                    {
                        **deadline_base,
                        "sourceLinks": [{"url": "https://forms.example.com/survey/updated"}],
                    },
                    appmod.utc_now(),
                )
                deadline_details = json.loads(db.execute(
                    "SELECT details_json FROM approvals WHERE id = ?", (deadline_id,)
                ).fetchone()["details_json"])
                appmod.upsert_inbox_signals(db, [
                    {
                        "sourceType": "email",
                        "sourceId": outlook_id,
                        "subject": "Persisted Outlook source",
                        "summary": "Review this email.",
                        "sender": "outlook@example.test",
                    },
                    {
                        "sourceType": "teams",
                        "sourceId": "1723000000001",
                        "chatId": "19:persisted_chat@thread.v2",
                        "messageId": "1723000000001",
                        "subject": "Persisted Teams source",
                        "summary": "Review this Teams message.",
                        "sender": "teams@example.test",
                    },
                ])
                db.commit()
            finally:
                db.close()
            state = appmod.get_state()
            card = next(
                item for item in state["approvals"]
                if item["action_type"] == "email" and item["title"].endswith("Quarterly survey")
            )
            details = json.loads(card["details_json"])
            ok &= check("safe source URL survives ingestion and dashboard state",
                        card["sourceUrl"] == "https://example.com/survey/123", repr(card["sourceUrl"]))
            ok &= check("survey card receives the accessible survey label",
                        card["sourceLabel"] == "Open survey", repr(card["sourceLabel"]))
            ok &= check("stored approval details contain no raw HTML",
                        "<" not in card["preview"] and "<" not in json.dumps(details), card["details_json"])
            ok &= check("recommendation text remains present after HTML removal",
                        "Recommendation: Complete it today." in card["preview"], card["preview"])
            ok &= check("an existing deadline card accepts a newly discovered actionable link",
                        deadline_details.get("sourceUrl") == "https://forms.example.com/survey/updated",
                        repr(deadline_details))
            persisted_email = next(item for item in state["approvals"] if item["title"].endswith("Persisted Outlook source"))
            persisted_teams = next(item for item in state["approvals"] if item["title"].endswith("Persisted Teams source"))
            ok &= check("persisted email approval receives an Outlook source link on read",
                        persisted_email["sourceUrl"] == f"https://outlook.office.com/mail/deeplink/read/{outlook_id}"
                        and persisted_email["sourceLabel"] == "Open email",
                        repr(persisted_email.get("sourceUrl")))
            ok &= check("persisted Teams approval receives a Teams message link on read",
                        persisted_teams["sourceUrl"].startswith(
                            "https://teams.microsoft.com/l/message/19%3Apersisted_chat%40thread.v2/"
                        ) and persisted_teams["sourceLabel"] == "Open Teams message",
                        repr(persisted_teams.get("sourceUrl")))
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()

    app_js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    ok &= check("main dashboard bolds only the literal Recommendation label",
                '<strong class="recommendation-label">$2</strong>' in app_js)
    ok &= check("source link is keyboard/accessibility labeled and isolates its opener",
                'aria-label="${escapeHtml(approval.sourceLabel || "Open source")}"' in app_js
                and 'rel="noopener noreferrer"' in app_js)
    metric_detail = (REPO_ROOT / "app" / "static" / "metric-detail.html").read_text(encoding="utf-8")
    ok &= check("KPI detail approvals render the same safe source link",
                'approval.sourceUrl ? `<a href="${esc(approval.sourceUrl)}"' in metric_detail
                and 'rel="noopener noreferrer"' in metric_detail)

    if ok:
        print("\nAll recommendation source-link checks passed.")
        return 0
    print("\nFAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
