#!/usr/bin/env python3
"""Offline coverage for the Calendar meeting-prep feature specification."""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import json
import gc
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import meeting_prep as mp  # noqa: E402
import app as appmod  # noqa: E402


CONFIG = ROOT / "config" / "se-scope.yaml"
NOW = "2026-08-31T14:40:39-05:00"


def database(*, approvals: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    mp.init_schema(db)
    if approvals:
        db.execute(
            """
            CREATE TABLE approvals (
              id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              employee TEXT NOT NULL, action_type TEXT NOT NULL, risk TEXT NOT NULL,
              title TEXT NOT NULL, preview TEXT NOT NULL, destination TEXT NOT NULL,
              status TEXT NOT NULL, details_json TEXT NOT NULL
            )
            """
        )
    return db


def attendee(name: str, email: str = "", external: bool = False) -> dict:
    return {"name": name, "email": email, "external": external}


def address(local: str, domain: str) -> str:
    return f"{local}{chr(64)}{domain}"


def event(event_id: str = "event-1", **overrides) -> dict:
    value = {
        "eventId": event_id,
        "subject": "Customer sync",
        "startLocal": "2026-09-01T09:00:00-05:00",
        "myAttendance": "required",
        "responseStatus": "accepted",
        "attendees": [attendee("Alex Customer", address("alex", "example.test"), True)],
    }
    value.update(overrides)
    return value


def customer_brief(customer: str = "Paychex", claim: str = "Pilot is blocked") -> mp.CustomerBrief:
    return mp.CustomerBrief(
        customer=customer,
        as_of="2026-08-30",
        initiatives=("Copilot pilot",),
        interest_areas=("m365-copilot",),
        open_issues=(claim,),
        adoption_signals=(),
        citations=(mp.Citation(claim, "lynx://account/123"),),
        gaps=("Licensing posture",),
    )


class MeetingPrepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = database()
        self.scope = mp.load_scope(CONFIG, self.db, now=NOW)

    def tearDown(self) -> None:
        self.db.close()

    def scan(self, events: list[dict]):
        return mp.scan_events(
            self.db, self.scope, events, today=date(2026, 8, 31), observed_at=NOW
        )

    def test_config_has_full_scope_and_computed_index(self) -> None:
        self.assertEqual(38, len(self.scope.accounts))
        self.assertEqual(12, len(self.scope.focus_areas))
        self.assertEqual("Anthony Martin", self.scope.normalize_name("Tony Martin"))
        self.assertIn(mp._canonical("John Karolemeas"), self.scope.weak_signals)
        self.assertEqual("config fallback", self.scope.source_used)
        log = self.db.execute("SELECT detail FROM meeting_prep_config_log").fetchone()[0]
        self.assertIn("fallback", log)
        self.assertTrue(mp.domain_run_report(self.db)["bootstrapNeeded"])

    def test_filtering_required_only_and_all_skip_reasons_are_logged(self) -> None:
        values = [
            event("required"),
            event("optional", myAttendance="optional"),
            event("declined", responseStatus="declined"),
            event("cancelled", isCancelled=True),
            event("private", isPrivate=True),
            event("all-day", isAllDay=True),
            event("focus", eventType="focusTime"),
            event("oof", showAs="oof"),
            event("alone", attendees=[]),
            event("outside", startLocal="2026-09-08T09:00:00-05:00"),
        ]
        candidates, skips = self.scan(values)
        self.assertEqual(["required"], [x.event_id for x in candidates])
        reasons = {x["eventId"]: x["reason"] for x in skips}
        self.assertEqual("optional-or-not-required", reasons["optional"])
        self.assertEqual("declined", reasons["declined"])
        self.assertEqual("cancelled", reasons["cancelled"])
        self.assertEqual("private-or-personal", reasons["private"])
        self.assertEqual("all-day", reasons["all-day"])
        self.assertEqual("focus-time", reasons["focus"])
        self.assertEqual("out-of-office", reasons["oof"])
        self.assertEqual("no-other-attendees", reasons["alone"])
        self.assertEqual("outside-lookahead", reasons["outside"])
        self.assertEqual(len(skips), self.db.execute("SELECT COUNT(*) FROM meeting_prep_skips").fetchone()[0])

    def test_tomorrow_only_uses_next_business_day(self) -> None:
        candidates, skips = mp.scan_events(
            self.db, self.scope,
            [event("tomorrow"), event("day-two", startLocal="2026-09-02T09:00:00-05:00")],
            today=date(2026, 8, 31), tomorrow_only=True, observed_at=NOW,
        )
        self.assertEqual(["tomorrow"], [x.event_id for x in candidates])
        self.assertEqual("outside-lookahead", skips[0]["reason"])

    def test_agenda_present_and_absent_branches(self) -> None:
        present, _ = self.scan([event(
            "present", subject="Paychex sync", bodyIsAgenda=True,
            body="- Copilot Chat pilot readiness\n- Pricing negotiation",
        )])
        review = mp.synthesize(self.scope, present[0], customer_brief())
        self.assertEqual("review-existing", review.mode)
        self.assertEqual("m365-copilot", review.my_items[0].focus_area_id)
        self.assertEqual("Jacqueline Hartz", review.not_mine[0].suggested_owner)
        self.assertTrue(any("Agenda gap" not in x for x in review.risks) or review.risks == ())

        absent, _ = self.scan([event("absent", subject="Paychex sync")])
        proposal = mp.synthesize(self.scope, absent[0], customer_brief())
        self.assertEqual("propose-new", proposal.mode)
        self.assertGreaterEqual(len(proposal.proposed_items), 3)
        self.assertLessEqual(len(proposal.proposed_items), 5)
        self.assertIn("[lynx://account/123]", proposal.proposed_items[0].why_now)

    def test_copilot_deep_dive_remains_in_scope(self) -> None:
        values, _ = self.scan([event(
            "deep-dive", subject="Paychex sync", bodyIsAgenda=True,
            body="Copilot Studio agent builder deep dive",
        )])
        recommendation = mp.synthesize(self.scope, values[0], customer_brief())
        self.assertEqual("copilot-studio", recommendation.my_items[0].focus_area_id)
        self.assertEqual((), recommendation.not_mine)

    def test_strong_irrelevant_agenda_produces_one_line(self) -> None:
        values, _ = self.scan([event(
            "irrelevant", subject="Internal planning", bodyIsAgenda=True,
            body="Welcome\nQuarterly staffing update\nClose",
        )])
        recommendation = mp.synthesize(self.scope, values[0], None)
        message = mp.format_teams_message(self.scope, values[0], recommendation, None)
        self.assertEqual("**Meeting prep — Internal planning:** Nothing needed from you.", message)

    def test_scope_owner_degrades_when_role_is_unassigned(self) -> None:
        values, _ = self.scan([event(
            "unassigned", subject="MRI Software",
            bodyIsAgenda=True, body="Renewal and consumption review",
        )])
        recommendation = mp.synthesize(self.scope, values[0], None)
        self.assertIn("CSAM", recommendation.not_mine[0].suggested_owner)
        self.assertIn("confirm coverage", recommendation.not_mine[0].suggested_owner)

    def test_account_team_single_multi_and_weak_inference(self) -> None:
        single, _ = self.scan([event(
            "single", attendees=[attendee("Athena Giles")], organizerName="Unknown",
        )])
        self.assertEqual(("Park Place International",), single[0].customer_signal.matched_accounts)
        self.assertEqual("high", single[0].customer_signal.confidence)

        multi, _ = self.scan([event(
            "multi", attendees=[attendee("Steve Sweet")], organizerName="Unknown",
        )])
        self.assertGreater(len(multi[0].customer_signal.matched_accounts), 1)
        self.assertEqual("medium", multi[0].customer_signal.confidence)

        weak, _ = self.scan([event(
            "weak", attendees=[attendee("John Karolemeas")], organizerName="Unknown",
        )])
        self.assertEqual((), weak[0].customer_signal.matched_accounts)
        self.assertEqual("none", weak[0].customer_signal.resolved_by)

    def test_aliases_resolve_people_and_accounts(self) -> None:
        self.assertEqual("Anthony Martin", self.scope.normalize_name("Tony Martin"))
        for subject in ("WWT roadmap", "Softchoice roadmap"):
            signal = mp.resolve_customer(self.scope, (), subject)
            self.assertEqual(
                ("World Wide Technology / Softchoice",), signal.matched_accounts
            )

    def test_manual_domain_is_high_and_short_circuits_subject(self) -> None:
        signal = mp.resolve_customer(
            self.scope,
            (mp.Attendee("Paychex guest", address("guest", "mail.paychex.com"), True),),
            "iManage quarterly update",
        )
        self.assertEqual(("Paychex",), signal.matched_accounts)
        self.assertEqual(("domain", "high"), (signal.resolved_by, signal.confidence))

    def test_exact_domain_precedes_parent_and_unknown_external_does_not_team_infer(self) -> None:
        imanage = self.scope.account("iManage")
        assert imanage is not None
        imanage.domains.append(mp.DomainEntry(
            "mail.paychex.com", "manual", True
        ))
        exact = mp.resolve_customer(
            self.scope,
            (mp.Attendee("Guest", address("guest", "mail.paychex.com"), True),),
            "Customer sync",
        )
        self.assertEqual(("iManage",), exact.matched_accounts)
        unknown = mp.resolve_customer(
            self.scope,
            (
                mp.Attendee("Guest", address("guest", "unknown.example"), True),
                mp.Attendee("Athena Giles", "", False),
            ),
            "Customer sync",
        )
        self.assertEqual((), unknown.matched_accounts)

    def test_unverified_domain_conflicts_preserve_subject_and_team_candidates(self) -> None:
        self.assertTrue(
            mp.assign_discovered_domain(
                self.db, self.scope, "unverified.test", "Paychex",
                "mailbox", False, NOW,
            )
        )
        conflict_scope = mp.load_scope(CONFIG, self.db, now=NOW)
        external = mp.Attendee(
            "External Person", address("person", "unverified.test"), True
        )
        subject_conflict = mp.resolve_customer(
            conflict_scope, (external,), "Epiq planning"
        )
        self.assertEqual(("subject", "medium"), (
            subject_conflict.resolved_by, subject_conflict.confidence,
        ))
        self.assertEqual(
            {"Epiq Inc (Global)", "Paychex"},
            set(subject_conflict.matched_accounts),
        )

        team_conflict = mp.resolve_customer(
            conflict_scope,
            (external, mp.Attendee("Mike Dube", "", False)),
            "Weekly planning",
        )
        self.assertEqual(("account-team", "medium"), (
            team_conflict.resolved_by, team_conflict.confidence,
        ))
        self.assertEqual(
            {"Epiq Inc (Global)", "Paychex"},
            set(team_conflict.matched_accounts),
        )

    def test_manual_domain_reassignment_is_rejected_and_logged(self) -> None:
        assigned = mp.assign_discovered_domain(
            self.db, self.scope, "paychex.com", "iManage", "mailbox", True, NOW
        )
        self.assertFalse(assigned)
        row = self.db.execute(
            "SELECT action, detail FROM meeting_domain_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual("manual-conflict", row["action"])
        self.assertIn("Paychex", row["detail"])
        self.assertFalse(mp.assign_discovered_domain(
            self.db, self.scope, "mail.paychex.com", "iManage",
            "meeting-learned", True, NOW,
        ))
        child_conflict = self.db.execute(
            "SELECT action FROM meeting_domain_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual("manual-conflict", child_conflict["action"])

    def test_parent_domain_matches_but_shared_suffix_sibling_does_not(self) -> None:
        parent = mp.resolve_customer(
            self.scope, (mp.Attendee("Guest", address("x", "mail.paychex.com"), True),), "Sync"
        )
        sibling = mp.resolve_customer(
            self.scope, (mp.Attendee("Guest", address("x", "unrelated.com"), True),), "Sync"
        )
        self.assertEqual(("Paychex",), parent.matched_accounts)
        self.assertEqual((), sibling.matched_accounts)
        self.assertFalse(mp._domain_matches("one.co.uk", "co.uk"))

    def test_epiq_manual_domain_handles_brand_divergence(self) -> None:
        signal = mp.resolve_customer(
            self.scope, (mp.Attendee("Guest", address("x", "epiqglobal.com"), True),), "Legal sync"
        )
        self.assertEqual(("Epiq Inc (Global)",), signal.matched_accounts)

    def test_discovery_distinguishes_absent_and_empty_domains(self) -> None:
        unknown = self.scope.accounts[0]
        checked = self.scope.accounts[1]
        unknown.domains_known = False
        checked.domains_known = True
        checked.domains.clear()
        report = mp.discover_mailbox_domains(self.db, self.scope, [], NOW)
        self.assertGreaterEqual(report["accountsWithUnknownDomains"], 1)
        self.assertGreaterEqual(report["accountsCheckedWithNone"], 1)

    def test_mailbox_bootstrap_pending_and_confirmed_queue(self) -> None:
        report = mp.discover_mailbox_domains(
            self.db, self.scope,
            [
                {"id": "m1", "participants": [address("a", "paycor.com")], "receivedAt": NOW},
                {"id": "m2", "participants": [address("noreply", "gmail.com")], "receivedAt": NOW},
                {"id": "m3", "participants": [address("x", "unknown-example.xyz")], "receivedAt": NOW},
            ],
            NOW,
        )
        self.assertTrue(any(x["account"] == "Paycor Inc" for x in report["assigned"]))
        self.assertTrue(any(x["domain"] == "unknown-example.xyz" for x in report["pending"]))
        self.assertFalse(any(x["domain"] == "gmail.com" for x in report["pending"]))
        self.assertFalse(mp.domain_run_report(self.db)["bootstrapNeeded"])
        self.assertTrue(mp.confirm_pending_domain(
            self.db, self.scope, "unknown-example.xyz", "Paycor Inc", NOW
        ))
        promoted = self.db.execute(
            "SELECT verified FROM meeting_domains WHERE domain='unknown-example.xyz'"
        ).fetchone()
        self.assertEqual(1, promoted["verified"])

    def test_meeting_learning_requires_independent_confirmations(self) -> None:
        first, _ = self.scan([event(
            "learn-1", subject="Paycor Inc", attendees=[
                attendee("Guest", address("one", "acquired-brand.example"), True)
            ],
        )])
        self.assertEqual("subject", first[0].customer_signal.resolved_by)
        self.assertIsNone(self.db.execute(
            "SELECT * FROM meeting_domains WHERE domain='acquired-brand.example'"
        ).fetchone())
        self.scan([event(
            "learn-2", subject="Paycor Inc", attendees=[
                attendee("Guest", address("two", "acquired-brand.example"), True)
            ],
        )])
        row = self.db.execute(
            "SELECT source, verified FROM meeting_domains WHERE domain='acquired-brand.example'"
        ).fetchone()
        self.assertEqual(("meeting-learned", 1), (row["source"], row["verified"]))

    def test_recurring_fingerprint_suppresses_identical_and_resends_changed_brief(self) -> None:
        db = database(approvals=True)
        try:
            scope = mp.load_scope(CONFIG, db, now=NOW)
            values, _ = mp.scan_events(
                db, scope, [event(
                    "occurrence-1", subject="Paychex sync", isRecurring=True,
                    seriesId="series-1",
                )],
                today=date(2026, 8, 31), observed_at=NOW,
            )
            candidate = values[0]
            brief = customer_brief()
            recommendation = mp.synthesize(scope, candidate, brief)
            message = mp.format_teams_message(scope, candidate, recommendation, brief)
            first = mp.queue_teams_prep(db, candidate, recommendation, message, brief, NOW)
            second_values, _ = mp.scan_events(
                db, scope, [event(
                    "occurrence-2", subject="Paychex sync", isRecurring=True,
                    seriesId="series-1", startLocal="2026-09-08T09:00:00-05:00",
                )],
                today=date(2026, 9, 7), observed_at=NOW,
            )
            second_candidate = second_values[0]
            second_rec = mp.synthesize(scope, second_candidate, brief)
            second_message = mp.format_teams_message(
                scope, second_candidate, second_rec, brief
            )
            duplicate = mp.queue_teams_prep(
                db, second_candidate, second_rec, second_message, brief, NOW
            )
            self.assertTrue(duplicate["queued"])
            self.assertEqual(first["approvalId"], duplicate["approvalId"])
            mp.register_delivery_job(db, "delivery-1", {
                "seriesKey": first["seriesKey"],
                "eventId": candidate.event_id,
                "fingerprint": first["fingerprint"],
                "briefFingerprint": mp.fingerprint(candidate, brief)[1],
            }, NOW)
            self.assertTrue(mp.promote_delivered_fingerprint(db, "delivery-1", NOW))
            delivered_duplicate = mp.queue_teams_prep(
                db, second_candidate, second_rec, second_message, brief, NOW
            )
            changed = customer_brief(claim="Pilot blocker was cleared")
            changed_rec = mp.synthesize(scope, candidate, changed)
            changed_message = mp.format_teams_message(scope, candidate, changed_rec, changed)
            resent = mp.queue_teams_prep(
                db, candidate, changed_rec, changed_message, changed, NOW
            )
            self.assertTrue(first["queued"])
            self.assertEqual(
                "unchanged-recurring-fingerprint", delivered_duplicate["reason"]
            )
            self.assertTrue(resent["queued"])
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0])
            details = db.execute(
                "SELECT details_json FROM approvals ORDER BY created_at LIMIT 1"
            ).fetchone()[0]
            self.assertIn('"outboundAction": "not_performed"', details)
            self.assertIn('"deliveryMode": "teams-self"', details)
            self.assertEqual(
                "meeting-prep",
                db.execute(
                    "SELECT action_type FROM approvals WHERE id = ?", (first["approvalId"],)
                ).fetchone()[0],
            )
        finally:
            db.close()

    def test_scan_rejects_missing_invalid_and_already_ended_times(self) -> None:
        _, skips = self.scan([
            event("missing-time", startLocal=""),
            event("naive-time", startLocal="2026-09-01T09:00:00"),
            event(
                "ended", startLocal="2026-08-31T08:00:00-05:00",
                endLocal="2026-08-31T09:00:00-05:00",
            ),
        ])
        self.assertEqual(
            {
                "missing-time": "missing-start",
                "naive-time": "invalid-start",
                "ended": "already-ended",
            },
            {item["eventId"]: item["reason"] for item in skips},
        )

    def test_series_name_and_world_wide_full_alias_resolve(self) -> None:
        values, _ = self.scan([
            event("series-name", subject="Weekly sync", seriesName="Paychex cadence"),
            event("wwt", subject="World Wide Technology review"),
        ])
        self.assertEqual(("Paychex",), values[0].customer_signal.matched_accounts)
        self.assertEqual(
            ("World Wide Technology / Softchoice",),
            values[1].customer_signal.matched_accounts,
        )

    def test_contract_and_config_validation_rejects_malformed_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "startLocal"):
            mp.MeetingCandidate.from_dict({
                "eventId": "bad", "subject": "Bad candidate",
                "startLocal": "2026-09-01T09:00:00", "isRecurring": False,
                "attendees": [{"name": "A", "email": "", "external": False}],
                "customerSignal": {
                    "matchedAccounts": [], "resolvedBy": "none", "confidence": "low",
                },
            })
        with self.assertRaisesRegex(ValueError, "asOf"):
            mp.CustomerBrief.from_dict({
                "customer": "Paychex", "asOf": "yesterday",
                "initiatives": [], "interestAreas": [], "openIssues": [],
                "adoptionSignals": [], "citations": [], "gaps": [],
            }, self.scope.focus_ids)

        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["runtime"]["agenda_item_min"] = 2
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "se-scope.yaml"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "agenda item bounds"):
                mp.load_scope(path)

    def test_discovery_ignores_missing_invalid_future_old_and_denied_subdomains(self) -> None:
        result = mp.discover_mailbox_domains(
            self.db,
            self.scope,
            [
                {"id": "missing", "participants": [address("a", "paychex.com")]},
                {
                    "id": "invalid", "receivedAt": "not-a-date",
                    "participants": [address("a", "paychex.com")],
                },
                {
                    "id": "future", "receivedAt": "2026-09-02T00:00:00-05:00",
                    "participants": [address("a", "paychex.com")],
                },
                {
                    "id": "old", "receivedAt": "2024-01-01T00:00:00-05:00",
                    "participants": [address("a", "paychex.com")],
                },
                {
                    "id": "denied", "receivedAt": "2026-08-30T10:00:00-05:00",
                    "participants": [address("a", "sub.microsoft.com")],
                },
            ],
            NOW,
        )
        self.assertEqual(0, result["observed"])
        self.assertEqual([], result["assigned"])
        self.assertEqual([], result["pending"])

    def test_directed_decision_does_not_fabricate_focus_area(self) -> None:
        values, _ = self.scan([
            event(
                "directed", subject="Internal planning",
                agendaText="Ted, choose the final launch option",
            )
        ])
        recommendation = mp.synthesize(self.scope, values[0], None)
        self.assertEqual("", recommendation.my_items[0].focus_area_id)
        self.assertIn("decision recommendation", recommendation.my_items[0].what_to_prepare)

    def test_private_domain_report_requires_bearer_even_when_auth_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            original_db, original_auth, original_token = (
                appmod.DB_PATH, appmod.AUTH_REQUIRED, appmod.LOCAL_TOKEN,
            )
            appmod.DB_PATH = pathlib.Path(temp) / "meeting-prep.db"
            appmod.AUTH_REQUIRED = False
            appmod.LOCAL_TOKEN = "meeting-prep-test-token"
            appmod.init_db()
            server = appmod.ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = (
                    f"http://127.0.0.1:{server.server_address[1]}"
                    "/api/meeting-prep/domains"
                )
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(url, timeout=5)
                self.assertEqual(403, denied.exception.code)
                denied.exception.close()
                request = urllib.request.Request(
                    url, headers={"Authorization": "Bearer meeting-prep-test-token"}
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(200, response.status)
                    self.assertTrue(json.loads(response.read())["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                appmod.DB_PATH, appmod.AUTH_REQUIRED, appmod.LOCAL_TOKEN = (
                    original_db, original_auth, original_token,
                )
                gc.collect()

    def test_approval_lifecycle_queues_self_chat_and_records_only_confirmed_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            original_db, original_auth = appmod.DB_PATH, appmod.AUTH_REQUIRED
            appmod.DB_PATH = pathlib.Path(temp) / "meeting-prep-approval.db"
            appmod.AUTH_REQUIRED = False
            appmod.init_db()
            now = appmod.utc_now()
            details = {
                "deliveryMode": "teams-self",
                "seriesKey": "series-safe",
                "eventId": "calendar-event-not-chat",
                "fingerprint": "fingerprint-safe",
                "briefFingerprint": "brief-safe",
            }
            with appmod.connect() as db:
                for approval_id in ("prep-reject", "prep-defer", "prep-approve"):
                    db.execute(
                        """
                        INSERT INTO approvals(
                          id, created_at, updated_at, employee, action_type, risk,
                          title, preview, destination, status, details_json
                        ) VALUES(?, ?, ?, 'Mina', 'meeting-prep', 'low',
                                 'Meeting prep', 'Exact approved preview',
                                 'Teams 1:1 (self)', 'pending', ?)
                        """,
                        (approval_id, now, now, json.dumps(details)),
                    )
            db.close()
            server = appmod.ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def post(path: str, payload: dict) -> dict:
                    request = urllib.request.Request(
                        base + path, data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"}, method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.loads(response.read())

                self.assertEqual(
                    [], post("/api/approvals/prep-reject", {"status": "rejected"})[
                        "createdJobs"
                    ],
                )
                self.assertEqual(
                    [], post("/api/approvals/prep-defer", {"status": "deferred"})[
                        "createdJobs"
                    ],
                )
                approved = post(
                    "/api/approvals/prep-approve", {"status": "approved"}
                )
                self.assertEqual(1, len(approved["createdJobs"]))
                job_id = approved["createdJobs"][0]
                with appmod.connect() as db:
                    job = db.execute(
                        "SELECT type, source, instructions FROM jobs WHERE id = ?",
                        (job_id,),
                    ).fetchone()
                    self.assertEqual("teams-action", job["type"])
                    self.assertEqual("meeting-prep-approval", job["source"])
                    self.assertIn("configured Teams self-chat", job["instructions"])
                    self.assertIn("Do not use the source calendar event ID", job["instructions"])
                    self.assertEqual(
                        0,
                        db.execute(
                            "SELECT COUNT(*) FROM meeting_prep_fingerprints"
                        ).fetchone()[0],
                    )
                db.close()
                post(
                    f"/api/jobs/{job_id}",
                    {
                        "status": "completed", "sendState": "sent",
                        "resultSummary": "Provider confirmed self-chat delivery",
                    },
                )
                with appmod.connect() as db:
                    delivered = db.execute(
                        "SELECT * FROM meeting_prep_fingerprints "
                        "WHERE series_key = 'series-safe'"
                    ).fetchone()
                    self.assertIsNotNone(delivered)
                    self.assertEqual("calendar-event-not-chat", delivered["event_id"])
                db.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                appmod.DB_PATH, appmod.AUTH_REQUIRED = original_db, original_auth
                gc.collect()

    def test_multiple_cited_claims_do_not_duplicate_agenda_topics(self) -> None:
        values, _ = self.scan([event("claims", subject="Paychex sync")])
        brief = mp.CustomerBrief(
            customer="Paychex", as_of="2026-08-30",
            initiatives=("Pilot expansion", "Executive workshop"),
            interest_areas=("m365-copilot",), open_issues=("Adoption blocker",),
            adoption_signals=(),
            citations=(
                mp.Citation("Pilot expansion", "lynx://1"),
                mp.Citation("Executive workshop", "lynx://2"),
                mp.Citation("Adoption blocker", "lynx://3"),
            ),
            gaps=(),
        )
        recommendation = mp.synthesize(self.scope, values[0], brief)
        titles = [item.title for item in recommendation.proposed_items]
        self.assertEqual(len(titles), len(set(titles)))

    def test_major_failure_is_fail_soft_and_delivery_is_approval_gated(self) -> None:
        values, _ = self.scan([event("failure", subject="Paychex sync")])
        recommendation = mp.synthesize(
            self.scope, values[0], None, research_error="lynx unavailable"
        )
        message = mp.format_teams_message(self.scope, values[0], recommendation, None)
        self.assertIn("Customer research unavailable: lynx unavailable", message)
        self.assertGreaterEqual(len(recommendation.proposed_items), 3)
        self.assertLessEqual(len(message.splitlines()), 15)
        self.assertNotIn("|", message)

    def test_customer_claims_are_cited_and_unsourced_claims_are_unverified(self) -> None:
        values, _ = self.scan([event("citations", subject="Paychex sync")])
        brief = mp.CustomerBrief(
            customer="Paychex", as_of="2026-08-30", initiatives=("Uncited initiative",),
            interest_areas=("m365-copilot",), open_issues=("Cited blocker",),
            adoption_signals=(), citations=(mp.Citation("Cited blocker", "lynx://42"),),
            gaps=(),
        )
        recommendation = mp.synthesize(self.scope, values[0], brief)
        message = mp.format_teams_message(self.scope, values[0], recommendation, brief)
        self.assertIn("Cited blocker [lynx://42]", message)
        self.assertIn("Uncited initiative", recommendation.unverified)
        self.assertNotIn("Uncited initiative [", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
