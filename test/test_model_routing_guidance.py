#!/usr/bin/env python3
"""Focused public-core tiered model-routing guidance checks."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    manifest = json.loads(read("manifest.json"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert "-" not in manifest["version"]
    assert manifest["buildRevision"] == "20260820.1"

    model = manifest["model"]
    assert model["default"] == "auto"
    assert model["routing"] == {
        "routine": "auto",
        "frontier": "setup-selected",
        "frontierUse": [
            "complex reasoning",
            "high-risk review",
            "final synthesis",
        ],
    }

    setup = read("skills/daily-flow-setup/SKILL.md")
    for requirement in (
        "`ROUTINE_MODEL = auto`",
        "`FRONTIER_MODEL` is the setup-selected model",
        "Recommend based on capability and availability, not provider or price tier",
        "If no tier is declared, default to `ROUTINE_MODEL`",
        "Selecting a frontier model must not make every chat or automation use it",
    ):
        assert requirement in setup
    assert "buildRevision = 20260820.1" in setup

    team = read("skills/daily-flow-team/SKILL.md")
    for requirement in (
        "**No model first:**",
        "**Routine tier:**",
        "**Frontier tier:**",
        "Routine work must not be pinned to a premium provider",
        "Quinn's binding verdict",
        "approval requirement",
        "Ground every claim in the real retrieved source",
        "a `hold` stops the send or publish",
        "Quinn's `pass` is not the user's approval",
    ):
        assert requirement in team
    assert "model claude-opus-5" not in team
    assert "Prefer Opus for the critic" not in team

    piper = read("skills/piper-template/SKILL.md")
    for requirement in (
        "provider-neutral `auto`",
        "setup-selected frontier model",
        "Reject a routine automation that pins a premium provider",
        "preserve every existing evidence, Quinn review, and approval gate",
        "Piper never creates, modifies, or deletes an automation without explicit user approval",
        "Piper's own proposals go to Quinn",
    ):
        assert requirement in piper

    print("[ok] public-core tiered model guidance preserves stable release and safeguards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
