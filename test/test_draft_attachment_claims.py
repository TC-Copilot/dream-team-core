#!/usr/bin/env python3
"""Outlook drafts cannot claim an attachment without provider-confirmed evidence."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import app as appmod  # noqa: E402


def main() -> int:
    validate = appmod.validate_outlook_draft_attachment_completion
    job = {
        "result_link_json": (
            '{"href":"https://outlook.office.com/mail/deeplink/draft/example",'
            '"label":"Open Outlook draft"}'
        )
    }

    result = validate(job, {"draftBody": "Research summary attached.", "draftAttachmentStatus": "none"}, "completed")
    assert result and result[0] == "blocked"
    assert "no verified attachment" in result[1]

    result = validate(job, {"draftBody": "Research summary attached.", "draftAttachmentStatus": "attached"}, "completed")
    assert result and result[0] == "blocked"
    assert "provider-verified" in result[1]

    assert validate(
        job,
        {
            "draftBody": "I've attached the research summary covering the requested topics.",
            "draftAttachmentStatus": "attached",
            "attachmentVerified": True,
            "providerAttachmentCount": 1,
            "draftAttachmentNames": ["MCP-connector-research.docx"],
        },
        "completed",
    ) is None

    assert validate(
        job,
        {
            "draftBody": "I've included a link to the research summary below.",
            "draftAttachmentStatus": "linked",
        },
        "completed",
    ) is None

    assert validate(
        job,
        {
            "draftBody": "The research summary is not attached; the findings are included below.",
            "draftAttachmentStatus": "none",
        },
        "completed",
    ) is None

    result = validate(job, {"draftAttachmentStatus": "none"}, "completed")
    assert result and result[0] == "blocked"
    assert "draftBody" in result[1]

    assert validate(
        {"result_link_json": '{"href":"/api/documents/research.docx"}'},
        {"draftBody": "Research summary attached.", "draftAttachmentStatus": "none"},
        "completed",
    ) is None

    print("[ok] Outlook attachment claims require provider-confirmed draft evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
