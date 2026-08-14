#!/usr/bin/env python3
"""Focused model tests for direct and investigative watch/follow-up items."""
from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, condition: bool) -> bool:
    print(f"[{'ok' if condition else 'FAIL'}] {name}")
    return condition


def main() -> int:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE watches (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          subject TEXT NOT NULL, thread_ref TEXT NOT NULL DEFAULT '',
          source_type TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '',
          source_url TEXT NOT NULL DEFAULT '', watch_instruction TEXT NOT NULL,
          trigger_condition TEXT NOT NULL DEFAULT '', proposed_action TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active', provenance_json TEXT NOT NULL DEFAULT '{}',
          owner TEXT NOT NULL DEFAULT '', triggered_at TEXT NOT NULL DEFAULT '',
          completed_at TEXT NOT NULL DEFAULT '', dismissed_at TEXT NOT NULL DEFAULT '',
          mode TEXT NOT NULL DEFAULT 'direct', item_kind TEXT NOT NULL DEFAULT 'watch',
          parent_watch_id TEXT NOT NULL DEFAULT '', origin_item_type TEXT NOT NULL DEFAULT '',
          origin_item_id TEXT NOT NULL DEFAULT '', origin_item_url TEXT NOT NULL DEFAULT '',
          evaluation TEXT NOT NULL DEFAULT '', proposed_next_step TEXT NOT NULL DEFAULT '',
          last_observed_at TEXT NOT NULL DEFAULT '', freshness_at TEXT NOT NULL DEFAULT '',
          evaluated_at TEXT NOT NULL DEFAULT '', removed_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    ok = True

    direct = appmod.create_watch(
        db,
        {
            "subject": "Contract response",
            "watchInstruction": "Watch for the signed response and remind me to review it.",
            "triggerCondition": "the signed response arrives",
            "proposedAction": "remind me to review it",
            "sourceType": "thread",
            "sourceId": "thread-1",
            "mode": "direct",
            "provenance": {"capturedBy": "test"},
        },
    )
    ok &= check("direct mode is persisted", direct["mode"] == "direct")
    ok &= check("direct proposed action is advisory", direct["automaticAction"] is False)

    investigative = appmod.create_watch(
        db,
        {
            "subject": "Pricing question",
            "watchInstruction": "Watch for more detail, then investigate what it means.",
            "triggerCondition": "a response adds pricing detail",
            "mode": "investigative",
            "originItemType": "message",
            "originItemId": "message-9",
            "freshnessAt": "2026-08-14T16:00:00Z",
            "provenance": {"capturedBy": "test", "sourceRevision": "1"},
        },
    )
    pending = appmod.update_watch(
        db, investigative["id"], {"status": "pending_investigation", "lastObservedAt": "2026-08-14T17:00:00Z"}
    )
    evaluated = appmod.update_watch(
        db,
        investigative["id"],
        {
            "status": "evaluated",
            "evaluation": "The response changes the original cost assumption.",
            "proposedNextStep": "Review the revised estimate before replying.",
        },
    )
    ok &= check("investigative mode enters pending investigation", pending["status"] == "pending_investigation")
    ok &= check("investigative mode stores evaluation", evaluated["status"] == "evaluated" and bool(evaluated["evaluation"]))
    ok &= check("evaluated next step remains advisory", evaluated["automaticAction"] is False)

    child = appmod.create_watch(
        db,
        {
            "subject": "Review revised estimate",
            "watchInstruction": "Review the revised estimate.",
            "itemKind": "action-item",
            "parentWatchId": investigative["id"],
            "originItemType": "message",
            "originItemId": "message-9",
        },
    )
    ok &= check(
        "spawned action item links to parent and original item",
        child["item_kind"] == "action-item"
        and child["parent_watch_id"] == investigative["id"]
        and child["origin_item_id"] == "message-9",
    )
    ok &= check("open list includes direct, evaluated, and action item", len(appmod.query_watches(db)) == 3)
    viewed = appmod.watch_to_dict(db.execute("SELECT * FROM watches WHERE id = ?", (direct["id"],)).fetchone())
    ok &= check("individual watch can be viewed", viewed["id"] == direct["id"])
    removed = appmod.update_watch(db, direct["id"], {"status": "removed"})
    ok &= check("remove is persisted as an audited lifecycle state", removed["status"] == "removed" and bool(removed["removed_at"]))
    ok &= check("removed watch is absent from open list", direct["id"] not in {w["id"] for w in appmod.query_watches(db)})
    ok &= check("removed watch remains in history", direct["id"] in {w["id"] for w in appmod.query_watches(db, "all")})

    chat = appmod.watch_from_chat(
        "Watch for more information on this response, then investigate what it means for the original item.",
        "thread-2",
        "message-2",
    )
    ok &= check("chat classifier recognizes investigative follow-up", chat["mode"] == "investigative")
    ok &= check("chat classifier does not fabricate an origin relationship", "originItemId" not in chat)
    direct_chat = appmod.watch_from_chat(
        "When the signed response arrives, remind me to review it.", "thread-3", "message-3"
    )
    ok &= check("ordinary response reminder remains direct", direct_chat["mode"] == "direct")
    linked_chat = appmod.watch_from_chat(
        "Watch for more detail, then investigate what it means.", "thread-4", "message-4",
        {"type": "message", "id": "origin-4", "url": "https://example.test/item/4"},
    )
    ok &= check("supplied origin relationship is preserved", linked_chat["originItemId"] == "origin-4")
    ok &= check(
        "direct watch cannot enter investigative lifecycle",
        _raises_value_error(lambda: appmod.update_watch(db, child["id"], {"status": "evaluated"})),
    )
    ok &= check(
        "evaluated state requires evaluation and next step",
        _raises_value_error(lambda: appmod.update_watch(
            db, appmod.create_watch(db, {
                "subject": "Needs evaluation", "watchInstruction": "Investigate", "mode": "investigative",
                "status": "pending_investigation",
            })["id"], {"status": "evaluated"}
        )),
    )
    ok &= check(
        "arbitrary text is bounded",
        _raises_value_error(lambda: appmod.create_watch(
            db, {"subject": "x" * 301, "watchInstruction": "watch"}
        )),
    )
    return 0 if ok else 1


def _raises_value_error(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
