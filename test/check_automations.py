#!/usr/bin/env python3
"""Fail the build when automations/automations.json metadata drifts from its contents.

The _meta block is what the setup skill and the dashboard's "automation missing"
warning are written against, so a stale count silently teaches Scout to look for
the wrong number of automations.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
AUTOMATIONS = REPO_ROOT / "automations" / "automations.json"
VALID_STATUS = {"required", "recommended", "optional"}
VALID_MODEL_TIERS = {"reasoning", "routine"}
LEAN_STATE_PATH = "/api/state?view=agent"


def validate(data: dict[str, Any]) -> list[str]:
    meta = data.get("_meta", {})
    entries = data.get("automations", [])
    enabled = [a for a in entries if a.get("recommendedEnabled")]

    problems: list[str] = []
    if meta.get("count") != len(entries):
        problems.append(f"_meta.count is {meta.get('count')} but there are {len(entries)} automations")
    if meta.get("enabledByDefault") != len(enabled):
        problems.append(
            f"_meta.enabledByDefault is {meta.get('enabledByDefault')} but "
            f"{len(enabled)} automations have recommendedEnabled=true"
        )
    configured_tiers = meta.get("modelTiers", {})
    if set(configured_tiers) != VALID_MODEL_TIERS:
        problems.append(
            f"_meta.modelTiers defines {sorted(configured_tiers)}, "
            f"expected {sorted(VALID_MODEL_TIERS)}"
        )
    for automation in entries:
        name = automation.get("name", "<unnamed>")
        status = automation.get("recommendedStatus")
        if status not in VALID_STATUS:
            problems.append(f"{name}: recommendedStatus is {status!r}, expected one of {sorted(VALID_STATUS)}")
        if not automation.get("recommendedStatusReason"):
            problems.append(f"{name}: missing recommendedStatusReason")
        for required_key in ("description", "schedule", "model", "prompt"):
            if not automation.get(required_key):
                problems.append(f"{name}: missing {required_key}")
        model_tier = automation.get("modelTier")
        if model_tier not in VALID_MODEL_TIERS:
            problems.append(
                f"{name}: modelTier is {model_tier!r}, expected one of {sorted(VALID_MODEL_TIERS)}"
            )
        if model_tier == "routine" and automation.get("model") != "auto":
            problems.append(f"{name}: routine work must use provider-neutral model='auto'")

        prompt = automation.get("prompt", "")
        if "{{APP_URL}}" not in prompt:
            problems.append(f"{name}: prompt must use the {{{{APP_URL}}}} placeholder")
        if "SAFETY AND PRIVACY:" not in prompt:
            problems.append(f"{name}: prompt is missing the standard safety and privacy block")

        state_paths = re.findall(
            r"GET\s+(?:\{\{APP_URL\}\})?(/api/state(?:\?[^\s),.;]*)?)",
            prompt,
        )
        non_lean_paths = [path for path in state_paths if path != LEAN_STATE_PATH]
        if non_lean_paths:
            problems.append(
                f"{name}: prompt reads non-lean state path(s): {sorted(set(non_lean_paths))}"
            )

        if model_tier == "routine":
            gate_position = prompt.find("/api/gate")
            if gate_position < 0:
                problems.append(f"{name}: routine worker is missing the /api/gate no-work check")
            else:
                work_markers = (
                    "/api/jobs/{jobId}",
                    "/api/attention-major",
                    "/api/state",
                    "/api/sweep/start",
                )
                early_markers = [
                    marker for marker in work_markers
                    if 0 <= prompt.find(marker) < gate_position
                ]
                if early_markers:
                    problems.append(
                        f"{name}: work appears before /api/gate: {sorted(early_markers)}"
                    )

    return problems


def main() -> int:
    # utf-8-sig: PowerShell tooling can leave a BOM on this file.
    data = json.loads(AUTOMATIONS.read_text(encoding="utf-8-sig"))
    problems = validate(data)

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1

    entries = data["automations"]
    enabled = [a for a in entries if a.get("recommendedEnabled")]
    print(
        "[ok] automation contracts are consistent: "
        f"{len(entries)} entries, {len(enabled)} enabled by default"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
