#!/usr/bin/env python3
"""Focused incoming-work priority checks for owned-account scoping."""
from __future__ import annotations

import gc
import json
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


def attachment_signal(source_id: str, *, customer: str = "CentralSquare",
                      summary: str = "Please review the attached proposal.",
                      priority: str = "high") -> dict[str, object]:
    return {
        "sourceType": "email",
        "sourceId": source_id,
        "subject": f"{customer} attachment review ({source_id})",
        "summary": summary,
        "sender": f"{source_id}@example.test",
        "hasAttachments": True,
        "attachmentNames": ["proposal.pdf"],
        "customer": customer,
        "priority": priority,
        "needsAction": True,
    }


def approval_by_source(db, source_id: str):
    for row in db.execute("SELECT * FROM approvals WHERE status = 'pending'").fetchall():
        details = json.loads(row["details_json"])
        if details.get("sourceId") == source_id:
            return row, details
    raise AssertionError(f"approval not found for {source_id}")


def main() -> int:
    ok = True
    original_db_path = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "daily_flow.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                appmod.save_owned_accounts(db, "Contoso")
                appmod.upsert_inbox_signals(db, [
                    attachment_signal("central-low"),
                    attachment_signal(
                        "central-raised",
                        summary="This is assigned to you and due tomorrow; please review the attachment.",
                    ),
                    attachment_signal("neutral", customer="", priority="normal"),
                ])
                db.commit()
                central_low, low_details = approval_by_source(db, "central-low")
                central_raised, raised_details = approval_by_source(db, "central-raised")
                neutral, neutral_details = approval_by_source(db, "neutral")
                state = appmod.get_state()
            finally:
                db.close()

            low_scope = low_details["accountScope"]
            low_recommendation = low_details["evidence"]["recommendation"]
            ok &= check("unowned CentralSquare attachment remains visible",
                        central_low["status"] == "pending")
            ok &= check("unowned CentralSquare attachment is lowest priority",
                        central_low["risk"] == "low" and low_details["priority"] == "low")
            ok &= check("unowned CentralSquare attachment records an explainable scope",
                        low_scope["scope"] == "unowned_account"
                        and low_scope["importance"] == "lowest"
                        and "still shown" in low_scope["reason"])
            ok &= check("unowned CentralSquare attachment is not promoted to ACT",
                        low_recommendation["verdict"] == "fyi"
                        and low_recommendation["subtype"] == "unowned_account_lowest_priority")

            raised_scope = raised_details["accountScope"]
            ok &= check("direct assignment raises an unowned attachment",
                        central_raised["risk"] == "high"
                        and raised_scope["importance"] == "raised"
                        and "assigned to you" in raised_scope["reason"])
            ok &= check("raised attachment retains its evidence ACT verdict",
                        raised_details["evidence"]["recommendation"]["verdict"] == "act")

            ok &= check("account-neutral attachment keeps its normal priority",
                        neutral["risk"] == "medium"
                        and neutral_details["accountScope"]["scope"] == "account_neutral")
            approval_ids = [approval["id"] for approval in state["approvals"]]
            ok &= check("lowest-priority unowned work is ordered after other pending cards",
                        approval_ids.index(central_low["id"]) > approval_ids.index(central_raised["id"])
                        and approval_ids.index(central_low["id"]) > approval_ids.index(neutral["id"]))
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()

    app_js = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    ok &= check("approval cards show the persisted ownership classification",
                "${accountScopeBadge(approval)}" in app_js)
    ok &= check("approval groups keep lowest-priority unowned work last",
                "accountScopeForItem(a)?.importance === \"lowest\"" in app_js)

    if ok:
        print("\nAll owned-account priority checks passed.")
        return 0
    print("\nFAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
