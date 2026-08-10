#!/usr/bin/env python3
"""Fail the build when automations/automations.json metadata drifts from its contents.

The _meta block is what the setup skill and the dashboard's "automation missing"
warning are written against, so a stale count silently teaches Scout to look for
the wrong number of automations.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
AUTOMATIONS = REPO_ROOT / "automations" / "automations.json"
VALID_STATUS = {"required", "recommended", "optional"}


def main() -> int:
    # utf-8-sig: PowerShell tooling can leave a BOM on this file.
    data = json.loads(AUTOMATIONS.read_text(encoding="utf-8-sig"))
    meta = data["_meta"]
    entries = data["automations"]
    enabled = [a for a in entries if a.get("recommendedEnabled")]

    problems: list[str] = []
    if meta.get("count") != len(entries):
        problems.append(f"_meta.count is {meta.get('count')} but there are {len(entries)} automations")
    if meta.get("enabledByDefault") != len(enabled):
        problems.append(
            f"_meta.enabledByDefault is {meta.get('enabledByDefault')} but "
            f"{len(enabled)} automations have recommendedEnabled=true"
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

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1

    print(f"[ok] automations metadata is consistent: {len(entries)} entries, {len(enabled)} enabled by default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
