#!/usr/bin/env python3
"""Contracts for read-only product updates in Daily Flow private briefs."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def automation(data: dict, name: str) -> dict:
    return next(item for item in data["automations"] if item["name"] == name)


def main() -> int:
    data = json.loads((ROOT / "automations" / "automations.json").read_text(encoding="utf-8-sig"))
    morning = automation(data, "Daily Flow Morning Brief")["prompt"]
    pulse = automation(data, "Daily Flow Continuous Work Pulse")["prompt"]
    skill = (ROOT / "skills" / "daily-flow-team" / "SKILL.md").read_text(encoding="utf-8")

    for text in (morning, pulse, skill):
        assert "Key product updates and fixes" in text
        assert "why it matters" in text
        assert "source" in text.lower()
        assert "explicit ask" in text

    assert "what changed since the last brief" in morning
    assert "do not create a reply draft, approval card, or outbound action" in morning
    assert "Keep update-only mail with no explicit ask out of /api/review-signals" in pulse
    assert "do not create reply drafts, approval cards, or outbound actions" in pulse
    assert "not a fifth section" in skill

    print("[ok] private briefs include material product updates without creating outbound work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
