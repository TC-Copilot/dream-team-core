#!/usr/bin/env python3
"""Contracts preventing stale or misattributed work from entering private briefs."""
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
        lowered = text.lower()
        assert "complete source" in lowered or "complete live source" in lowered
        assert "later replies" in lowered
        assert "sent items" in lowered
        assert "completed" in lowered
        assert "superseded" in lowered
        assert "owner" in lowered

    assert "user was not merely copied on an answer" in morning
    assert "freshly verified as open in this run" in morning
    assert "unverified inventory count" in morning
    assert "do not quote it unless" in morning

    assert "Being To/Cc on a thread" in pulse
    assert "does not make that person the owner" in pulse
    assert "do not leave a commitment active" in pulse.lower()

    for text in (morning, pulse, skill):
        lowered = text.lower()
        assert "stale" in lowered
        assert "as-of time" in lowered
        assert "partial" in lowered
        assert "current queue" in lowered

    print("[ok] briefs require live commitment and queue reconciliation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
