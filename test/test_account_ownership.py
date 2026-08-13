#!/usr/bin/env python3
"""Targeted tests for the owned-account editor and account-ownership scoping (app/app.py).

Covers:
1. _split_account_names: parses CSV commas, newlines, and whitespace-run-separated pastes,
   trims each entry, and de-duplicates case-insensitively while preserving first-seen casing.
2. classify_account_scope:
   - No confirmed customer/account context -> account_neutral (owner list irrelevant).
   - Confirmed customer matches the owned list -> owned_account, normal importance.
   - Confirmed customer does not match a non-empty owned list -> unowned_account, defaulting to
     "lowest" importance.
   - ...unless a priority-raise signal (assignment, explicit mention, deadline, customer impact,
     safety/compliance/security) appears in the item's own text -> unowned_account with importance
     "raised" and the matched reason recorded.
   - Confirmed customer present but owned list is empty/unconfigured -> uncertain_account, with
     no ownership-based suppression or boost either way.
3. get_owned_accounts / save_owned_accounts round-trip through a real SQLite connection.
4. build_impact_ledger annotates every highlight with an accountScope, and never drops/suppresses
   an unowned or uncertain item from the highlights list.

Run directly: `python test/test_account_ownership.py`.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        print(f"[ok] {name}")
        return True
    print(f"[FAIL] {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def make_test_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE owned_accounts (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            raw_text TEXT NOT NULL DEFAULT '',
            names_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            employee TEXT NOT NULL,
            summary TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            sensitivity TEXT NOT NULL DEFAULT 'private',
            status TEXT NOT NULL DEFAULT 'logged'
        );
        CREATE TABLE app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    return db


def main() -> int:
    ok = True

    # --- _split_account_names ----------------------------------------------------------------
    ok &= check(
        "_split_account_names splits CSV commas",
        appmod._split_account_names("Contoso Ltd, Fabrikam Inc"),
        ["Contoso Ltd", "Fabrikam Inc"],
    )
    ok &= check(
        "_split_account_names splits newlines",
        appmod._split_account_names("Contoso Ltd\nFabrikam Inc\n"),
        ["Contoso Ltd", "Fabrikam Inc"],
    )
    ok &= check(
        "_split_account_names splits whitespace-run-separated single line",
        appmod._split_account_names("Contoso Ltd   Fabrikam Inc   Northwind Traders"),
        ["Contoso Ltd", "Fabrikam Inc", "Northwind Traders"],
    )
    ok &= check(
        "_split_account_names de-duplicates case-insensitively, keeps first casing",
        appmod._split_account_names("Contoso Ltd, contoso ltd, CONTOSO LTD"),
        ["Contoso Ltd"],
    )
    ok &= check(
        "_split_account_names drops empties/blank lines",
        appmod._split_account_names("Contoso Ltd,,\n\n  ,Fabrikam Inc"),
        ["Contoso Ltd", "Fabrikam Inc"],
    )
    ok &= check(
        "_split_account_names handles mixed commas+newlines",
        appmod._split_account_names("Contoso Ltd, Fabrikam Inc\nNorthwind Traders"),
        ["Contoso Ltd", "Fabrikam Inc", "Northwind Traders"],
    )

    # --- classify_account_scope ---------------------------------------------------------------
    owned = {"contoso ltd", "fabrikam inc"}

    r = appmod.classify_account_scope(owned, "", "Some internal-only task with no account tag")
    ok &= check("classify_account_scope: no customer -> account_neutral", r["scope"], "account_neutral")

    r = appmod.classify_account_scope(owned, "Contoso Ltd", "Drafted a summary for Contoso Ltd")
    ok &= check("classify_account_scope: owned customer -> owned_account", r["scope"], "owned_account")
    ok &= check("classify_account_scope: owned_account keeps normal importance", r["importance"], "normal")

    r = appmod.classify_account_scope(owned, "Acme Corp", "Drafted a routine note for Acme Corp")
    ok &= check("classify_account_scope: unowned customer, no raise signal -> unowned_account", r["scope"], "unowned_account")
    ok &= check("classify_account_scope: unowned_account defaults to lowest importance", r["importance"], "lowest")

    r = appmod.classify_account_scope(owned, "Acme Corp", "This item is assigned to you and due tomorrow for Acme Corp")
    ok &= check("classify_account_scope: unowned + deadline/assignment -> still unowned_account", r["scope"], "unowned_account")
    ok &= check("classify_account_scope: unowned + raise signal -> importance raised", r["importance"], "raised")
    ok &= check("classify_account_scope: raised reason mentions the matched signal", "assigned to you" in r["reason"], True)

    r = appmod.classify_account_scope(owned, "Acme Corp", "Security incident affecting Acme Corp production")
    ok &= check("classify_account_scope: unowned + security/incident signal -> raised", r["importance"], "raised")

    r = appmod.classify_account_scope(set(), "Acme Corp", "Some note for Acme Corp")
    ok &= check("classify_account_scope: customer present but no owned list configured -> uncertain_account", r["scope"], "uncertain_account")
    ok &= check("classify_account_scope: uncertain_account importance is not suppressed/boosted", r["importance"], "normal")

    # --- get_owned_accounts / save_owned_accounts round-trip ---------------------------------
    db = make_test_db()
    empty = appmod.get_owned_accounts(db)
    ok &= check("get_owned_accounts: empty on fresh db", empty["names"], [])

    saved = appmod.save_owned_accounts(db, "Contoso Ltd, Fabrikam Inc\nNorthwind Traders")
    ok &= check("save_owned_accounts: names parsed and returned", saved["names"], ["Contoso Ltd", "Fabrikam Inc", "Northwind Traders"])

    reloaded = appmod.get_owned_accounts(db)
    ok &= check("get_owned_accounts: round-trips names after save", reloaded["names"], ["Contoso Ltd", "Fabrikam Inc", "Northwind Traders"])
    ok &= check("get_owned_accounts: round-trips rawText after save", reloaded["rawText"], "Contoso Ltd, Fabrikam Inc\nNorthwind Traders")

    updated = appmod.save_owned_accounts(db, "Only One Account")
    ok &= check("save_owned_accounts: overwrites (single-row) on second save", updated["names"], ["Only One Account"])

    owned_keys = appmod._owned_account_keys(db)
    ok &= check("_owned_account_keys: lowercased set from stored names", owned_keys, {"only one account"})

    # No content of the account list itself is ever logged, only the count -- confirm the event
    # text doesn't leak the raw account name.
    event_summaries = [row["summary"] for row in db.execute("SELECT summary FROM events").fetchall()]
    ok &= check(
        "save_owned_accounts: event log never contains the raw account name",
        any("Only One Account" in s for s in event_summaries),
        False,
    )

    # --- build_impact_ledger wires accountScope onto every highlight, never suppresses ---------
    work_entries = [
        {
            "id": "we_owned", "occurred_at": "2026-01-01T10:00:00Z", "employee": "Riley",
            "category": "document-or-draft", "title": "Drafted a note", "summary": "Drafted a note for Contoso Ltd",
            "people_json": "[]", "customer": "Contoso Ltd", "evidence_json": "", "impact_level": "supporting",
            "impact_summary": "", "source_type": "manual", "source_id": "we_owned", "status": "active",
        },
        {
            "id": "we_unowned", "occurred_at": "2026-01-01T11:00:00Z", "employee": "Riley",
            "category": "document-or-draft", "title": "Drafted a note", "summary": "Drafted a note for Acme Corp",
            "people_json": "[]", "customer": "Acme Corp", "evidence_json": "", "impact_level": "supporting",
            "impact_summary": "", "source_type": "manual", "source_id": "we_unowned", "status": "active",
        },
        {
            "id": "we_neutral", "occurred_at": "2026-01-01T12:00:00Z", "employee": "Riley",
            "category": "work-completed", "title": "Internal task", "summary": "Completed an internal-only task",
            "people_json": "[]", "customer": "", "evidence_json": "", "impact_level": "supporting",
            "impact_summary": "", "source_type": "manual", "source_id": "we_neutral", "status": "active",
        },
    ]
    ledger = appmod.build_impact_ledger([], [], [], work_entries, {"contoso ltd"})
    by_id = {item["id"]: item for item in ledger["highlights"]}
    ok &= check("build_impact_ledger: owned item scoped owned_account", by_id["we_owned"]["accountScope"]["scope"], "owned_account")
    ok &= check("build_impact_ledger: unowned item scoped unowned_account", by_id["we_unowned"]["accountScope"]["scope"], "unowned_account")
    ok &= check("build_impact_ledger: unowned item defaults to lowest importance", by_id["we_unowned"]["accountScope"]["importance"], "lowest")
    ok &= check("build_impact_ledger: neutral item scoped account_neutral", by_id["we_neutral"]["accountScope"]["scope"], "account_neutral")
    ok &= check(
        "build_impact_ledger: never suppresses an unowned/uncertain item from highlights",
        all(item["id"] in by_id for item in work_entries),
        True,
    )

    print("\nAll account-ownership checks passed." if ok else "\nSome account-ownership checks FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
