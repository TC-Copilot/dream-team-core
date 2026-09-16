#!/usr/bin/env python3
"""Completed PowerPoint files must use the native pptx workflow."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import app as appmod  # noqa: E402


def main() -> int:
    validate = appmod.validate_presentation_skill_completion
    job = {
        "result_link_json": "",
        "skill": "",
        "artifact_type": "",
        "narrative_reviewed": 0,
        "quality_verdict": "",
    }
    link = r"C:\Users\Example\Scout\briefing.pptx"

    result = validate(job, {"link": link, "skill": "docx"}, "completed")
    assert result and result[0] == "blocked"
    assert "PowerPoint skill required" in result[1]

    result = validate(
        job,
        {"link": link, "skill": "pptx", "artifactType": "pptx"},
        "completed",
    )
    assert result and "narrative review" in result[1]

    result = validate(
        job,
        {
            "link": link,
            "skill": "pptx",
            "artifactType": "pptx",
            "narrativeReviewed": "true",
            "qualityVerdict": "pass",
        },
        "completed",
    )
    assert result and "narrative review" in result[1]

    result = validate(
        job,
        {
            "link": link,
            "skill": "pptx",
            "artifactType": "pptx",
            "narrativeReviewed": True,
        },
        "completed",
    )
    assert result and "quality review" in result[1]

    assert validate(
        job,
        {
            "link": link,
            "skill": "pptx",
            "artifactType": "pptx",
            "narrativeReviewed": True,
            "qualityVerdict": "pass",
        },
        "completed",
    ) is None

    assert validate(
        job,
        {"link": r"C:\Users\Example\Scout\briefing.docx", "skill": "docx"},
        "completed",
    ) is None
    assert validate(job, {"link": link, "skill": "docx"}, "in_progress") is None

    print("[ok] completed .pptx files require the native PowerPoint review chain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
