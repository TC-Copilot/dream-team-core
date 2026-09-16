#!/usr/bin/env python3
"""Contracts preventing forwarded notices from being attributed to the wrong recipient."""
from __future__ import annotations

import gc
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import app as appmod  # noqa: E402


def main() -> int:
    base = {
        "sourceType": "email",
        "subject": "FW: You are now a member of M365 Copilot CAB SG",
        "summary": "You have been added to the M365 Copilot CAB security group.",
        "sender": "Faizan Makhiawala",
    }
    for invalid in (
        base,
        {**base, "appliesToSignedInUser": False},
        {
            **base,
            "appliesToSignedInUser": False,
            "originalRecipients": ["Faizan Makhiawala"],
        },
    ):
        try:
            appmod.validate_forwarded_recipient_attribution(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"misattributed forwarded claim was accepted: {invalid!r}")

    corrected = {
        **base,
        "isForwarded": True,
        "appliesToSignedInUser": False,
        "originalRecipients": ["Faizan Makhiawala"],
        "recipientEvidence": "The embedded membership notice was addressed to Faizan.",
        "summary": (
            "Faizan Makhiawala forwarded confirmation that he was added to the M365 Copilot CAB "
            "security group. The forward does not confirm that Ted was added."
        ),
    }
    appmod.validate_forwarded_recipient_attribution(corrected)
    appmod.validate_forwarded_recipient_attribution(
        {
            **corrected,
            "summary": "Faizan forwarded a CAB notice. The beneficiary is not Ted.",
            "originalRecipients": [],
            "recipientEvidence": "The embedded original recipient was Faizan.",
        }
    )

    original_db_path = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "daily_flow.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                result = appmod.upsert_inbox_signals(db, [corrected])
                assert result["upserted"] == 1
                row = db.execute(
                    "SELECT preview, details_json FROM approvals WHERE status = 'pending'"
                ).fetchone()
                assert row is not None
                assert "Faizan Makhiawala forwarded confirmation that he was added" in row["preview"]
                assert "You have been added" not in row["preview"]
                details = json.loads(row["details_json"])
                assert details["appliesToSignedInUser"] is False
                assert details["originalRecipients"] == ["Faizan Makhiawala"]
            finally:
                db.close()
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()

    attention_prompt = (ROOT / "app" / "prompts" / "attention-major.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "daily-flow-team" / "SKILL.md").read_text(encoding="utf-8")
    automations = (ROOT / "automations" / "automations.json").read_text(encoding="utf-8-sig")
    for contract in (attention_prompt, skill, automations):
        assert "appliesToSignedInUser" in contract
        assert "originalRecipients" in contract

    print("[ok] forwarded personal-status notices require original-recipient evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
