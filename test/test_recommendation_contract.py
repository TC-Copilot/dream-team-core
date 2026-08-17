#!/usr/bin/env python3
"""Focused provider-neutral recommendation and How contract checks."""
from __future__ import annotations

import pathlib
import hashlib
import json
import sys
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def recommendation(**overrides):
    value = {
        "seriesId": "monthly-series-1",
        "occurrenceId": "occurrence-a",
        "title": "Monthly conversation call",
        "when": "2026-09-10T09:00:00Z",
        "recommendation": "Choose this occurrence.",
        "coverage": {
            "status": "complete",
            "checkedSources": ["calendar", "work-hours"],
            "missingSources": [],
        },
        "provenance": [{"source": "calendar", "sourceId": "event-a"}],
    }
    value.update(overrides)
    return value


def how_record(**overrides):
    value = {
        "schemaVersion": "1.0",
        "id": "how-report-1",
        "fingerprint": "sha256:example",
        "title": "Prepare a status report",
        "intent": "Create a source-backed weekly status report.",
        "procedure": "Collect confirmed outcomes, risks, and next actions.",
        "applicability": "Weekly project reporting.",
        "owner": "reporting",
        "links": [{"kind": "source", "ref": "document-1"}],
        "createdAt": "2026-08-17T10:00:00-05:00",
        "updatedAt": "2026-08-17T10:00:00-05:00",
        "reviewAt": "2026-08-24T10:00:00-05:00",
        "expiresAt": "",
        "provenance": [{"source": "document", "sourceId": "document-1"}],
        "confidence": 0.8,
        "sensitivity": "internal",
        "status": "pending",
    }
    value.update(overrides)
    return value


def expect_error(fragment, callback):
    try:
        callback()
    except ValueError as exc:
        assert fragment in str(exc), str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {fragment!r}")


def execution_contract(**overrides):
    payload = {"recipient": "person-1", "subject": "Draft update"}
    value = {
        "schemaVersion": "1.0",
        "id": "execution-1",
        "action": "message.send",
        "target": {"type": "message", "id": "person-1"},
        "payload": payload,
        "payloadHash": hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest(),
        "approvalState": "approved",
        "approver": "user-1",
        "policy": {"decision": "allow", "rule": "explicit-user-approval"},
        "preconditions": [{"name": "draft-reviewed", "satisfied": True}],
        "idempotencyKey": "send-message-1",
        "expiresAt": "2026-08-18T00:00:00Z",
        "audit": {"createdAt": "2026-08-17T15:00:00Z"},
        "rollback": {"mode": "none", "reason": "external send cannot be recalled"},
    }
    value.update(overrides)
    return value


def main() -> int:
    first = recommendation()
    second = recommendation(
        occurrenceId="occurrence-b",
        when="2026-09-11T15:00:00Z",
        recommendation="Prefer this occurrence if the earlier slot is unavailable.",
    )
    grouped = appmod.normalize_recommendation_contract({
        "schemaVersion": "1.0",
        "recommendations": [second, first, first],
    })
    assert grouped["schemaVersion"] == "1.0"
    assert len(grouped["groups"]) == 1
    group = grouped["groups"][0]
    assert group["groupBy"] == "seriesId"
    assert len(group["options"]) == 2
    assert [option["number"] for option in group["options"]] == [1, 2]

    reordered = appmod.normalize_recommendation_contract({
        "schemaVersion": "1.0",
        "recommendations": [first, second],
    })
    assert reordered == grouped

    incomplete = appmod.normalize_recommendation_contract({
        "schemaVersion": "1.0",
        "recommendations": [
            recommendation(coverage={
                "status": "incomplete",
                "checkedSources": ["calendar"],
                "missingSources": ["work-hours"],
            }),
        ],
    })
    assert incomplete["groups"][0]["coverage"] == {
        "status": "incomplete",
        "checkedSources": ["calendar"],
        "missingSources": ["work-hours"],
    }

    expect_error("schemaVersion", lambda: appmod.normalize_recommendation_contract({
        "schemaVersion": "2.0", "recommendations": []
    }))
    expect_error("incomplete coverage", lambda: appmod.normalize_recommendation_contract({
        "schemaVersion": "1.0",
        "recommendations": [recommendation(coverage={
            "status": "incomplete", "checkedSources": [], "missingSources": []
        })],
    }))
    expect_error("conflicting recommendation", lambda: appmod.normalize_recommendation_contract({
        "schemaVersion": "1.0",
        "recommendations": [
            first,
            recommendation(recommendation="A conflicting recommendation."),
        ],
    }))
    expect_error("seriesId", lambda: appmod.normalize_recommendation_contract({
        "schemaVersion": "1.0",
        "recommendations": [recommendation(
            seriesId="", occurrenceId="", sourceType="", sourceId=""
        )],
    }))

    normalized_how = appmod.normalize_how_record(how_record())
    assert normalized_how["status"] == "pending"
    assert normalized_how["createdAt"] == "2026-08-17T15:00:00Z"
    assert appmod.recommendation_contract()["howRecord"]["activationRule"].endswith(
        "pending review."
    )
    expect_error("confidence", lambda: appmod.normalize_how_record(
        how_record(confidence=1.5)
    ))
    expect_error("status", lambda: appmod.normalize_how_record(
        how_record(status="promoted")
    ))

    now = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    normalized_execution = appmod.normalize_execution_contract(
        execution_contract(), now=now
    )
    assert normalized_execution["expiresAt"] == "2026-08-18T00:00:00Z"
    expect_error("approvalState", lambda: appmod.normalize_execution_contract(
        execution_contract(approvalState="pending"), now=now
    ))
    expect_error("does not match", lambda: appmod.normalize_execution_contract(
        execution_contract(payloadHash="0" * 64), now=now
    ))
    expect_error("expired", lambda: appmod.normalize_execution_contract(
        execution_contract(expiresAt="2026-08-17T15:00:00Z"), now=now
    ))
    expect_error("already been consumed", lambda: appmod.normalize_execution_contract(
        execution_contract(), now=now, consumed_idempotency_keys={"send-message-1"}
    ))
    expect_error("rollback.mode", lambda: appmod.normalize_execution_contract(
        execution_contract(rollback={}), now=now
    ))

    print("[ok] recommendation and How contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
