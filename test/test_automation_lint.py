#!/usr/bin/env python3
"""Regression coverage for provider-neutral automation optimization lint."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
AUTOMATIONS = REPO_ROOT / "automations" / "automations.json"
CHECKER = REPO_ROOT / "test" / "check_automations.py"

spec = importlib.util.spec_from_file_location("check_automations", CHECKER)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def mutated(data, name):
    candidate = copy.deepcopy(data)
    return next(item for item in candidate["automations"] if item["name"] == name), candidate


def expect_problem(data, fragment):
    problems = checker.validate(data)
    assert any(fragment in problem for problem in problems), problems


def main() -> int:
    data = json.loads(AUTOMATIONS.read_text(encoding="utf-8-sig"))
    assert checker.validate(data) == []

    entry, candidate = mutated(data, "Daily Flow Attention Major Trigger")
    entry["prompt"] = entry["prompt"].replace(
        "GET {{APP_URL}}/api/gate",
        "GET {{APP_URL}}/api/state, then GET {{APP_URL}}/api/gate",
        1,
    )
    expect_problem(candidate, "non-lean state")
    expect_problem(candidate, "work appears before /api/gate")

    entry, candidate = mutated(data, "Daily Flow Morning Brief")
    entry["prompt"] = entry["prompt"].replace("/api/state?view=agent", "/api/state", 1)
    expect_problem(candidate, "non-lean state")

    entry, candidate = mutated(data, "Daily Flow Attention Major Trigger")
    entry["model"] = "premium-provider-model"
    expect_problem(candidate, "provider-neutral model='auto'")

    entry, candidate = mutated(data, "Daily Flow Evening Wrap-up")
    entry["prompt"] = entry["prompt"].replace("SAFETY AND PRIVACY:", "SAFETY:", 1)
    expect_problem(candidate, "standard safety and privacy block")

    print("[ok] automation optimization lint rejects unsafe and costly prompt regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
