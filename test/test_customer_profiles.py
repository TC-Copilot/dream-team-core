#!/usr/bin/env python3
"""Customer customization contract tests.

Covers profile CRUD, server-enforced field caps, brand-asset rejection/dedupe/sanitization, the
proposed-vs-confirmed integrity boundary, brief resolution, and the seed migration from the
existing owned-account list.
"""
from __future__ import annotations

import base64
import gc
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def check(name: str, condition: bool) -> bool:
    print(f"[{'ok' if condition else 'FAIL'}] {name}")
    return bool(condition)


def raises_value_error(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def main() -> int:
    ok = True
    original_db = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "customers.db"
            # Seed the owned-account list BEFORE init_db so the seed migration has something to do.
            appmod.init_db()
            db = appmod.connect()
            try:
                appmod.save_owned_accounts(db, "Contoso Ltd, Fabrikam Inc\nNorthwind Traders")
                db.execute("DELETE FROM app_meta WHERE key = 'customer_profile_seed_version'")
                seeded = appmod.seed_customer_profiles_from_owned_accounts(db)
                db.commit()
            finally:
                db.close()

            db = appmod.connect()
            try:
                # ---- migration from owned_accounts ---------------------------------------
                ok &= check("owned accounts seed one profile each", seeded == 3)
                profiles = appmod.query_customer_profiles(db)
                ok &= check(
                    "seeded profiles keep human casing and default to standard tier",
                    [p["accountName"] for p in profiles] == ["Contoso Ltd", "Fabrikam Inc", "Northwind Traders"]
                    and all(p["tier"] == "standard" for p in profiles),
                )
                ok &= check(
                    "seed migration runs only once",
                    appmod.seed_customer_profiles_from_owned_accounts(db) == 0,
                )
                ok &= check(
                    "owned_accounts is left intact for existing readers",
                    appmod.get_owned_accounts(db)["names"] == ["Contoso Ltd", "Fabrikam Inc", "Northwind Traders"],
                )

                # ---- profile CRUD --------------------------------------------------------
                profile = appmod.upsert_customer_profile(db, {
                    "accountName": "Contoso Ltd",
                    "aliases": ["Contoso", "Contoso Limited"],
                    "domains": ["contoso.example.com"],
                    "tier": "strategic",
                    "summary": "Flagship account.",
                    "brand": {"colors": ["#0b5cab", "#f2b134"], "fonts": ["Segoe UI"], "tone": "formal",
                              "templates": {"deck": "contoso-deck-v3"}},
                    "engagement": {"summarizationStyle": "three bullets then the ask",
                                   "routing": "Dash", "escalation": "Call the account lead."},
                    "compliance": {"bannedTerms": ["cheap"], "requiredDisclaimers": ["Estimates are not a quote."],
                                   "redactionLevel": "high"},
                })
                ok &= check("upsert matches the seeded profile instead of duplicating",
                            len(appmod.query_customer_profiles(db)) == 3)
                ok &= check("upsert updates tier and brand", profile["tier"] == "strategic"
                            and profile["brand"]["colors"][0] == "#0b5cab")
                ok &= check("profile responses are advisory only", profile["automaticAction"] is False)

                ok &= check("account key is normalized", profile["accountKey"] == "contoso ltd")
                ok &= check("profile resolves by exact name",
                            appmod.resolve_customer_profile(db, "CONTOSO  LTD.")["id"] == profile["id"])
                ok &= check("profile resolves by alias",
                            appmod.resolve_customer_profile(db, "Contoso Limited")["id"] == profile["id"])
                ok &= check("profile resolves by email domain",
                            appmod.resolve_customer_profile(db, "priya@contoso.example.com")["id"] == profile["id"])
                ok &= check("unknown account resolves to nothing, never a guess",
                            appmod.resolve_customer_profile(db, "Some Unknown Co") is None)

                ok &= check("filters by tier", len(appmod.query_customer_profiles(db, tier="strategic")) == 1)
                ok &= check("search matches by name", len(appmod.query_customer_profiles(db, q="fabrikam")) == 1)

                # ---- server-enforced field caps ------------------------------------------
                ok &= check("accountName is required", raises_value_error(
                    lambda: appmod.upsert_customer_profile(db, {"accountName": "   "})))
                ok &= check("accountName is capped", raises_value_error(
                    lambda: appmod.upsert_customer_profile(db, {"accountName": "x" * 201})))
                ok &= check("summary is capped", raises_value_error(
                    lambda: appmod.update_customer_profile(db, profile["id"], {"summary": "x" * 2001})))
                ok &= check("notes is capped", raises_value_error(
                    lambda: appmod.update_customer_profile(db, profile["id"], {"notes": "x" * 4001})))
                ok &= check("tier vocabulary is enforced", raises_value_error(
                    lambda: appmod.update_customer_profile(db, profile["id"], {"tier": "platinum"})))
                ok &= check("brand object is capped", raises_value_error(
                    lambda: appmod.update_customer_profile(db, profile["id"], {"brand": {"x": "y" * 20000}})))
                ok &= check("brand must be an object", raises_value_error(
                    lambda: appmod.update_customer_profile(db, profile["id"], {"brand": ["nope"]})))
                ok &= check("aliases must be strings", raises_value_error(
                    lambda: appmod.update_customer_profile(db, profile["id"], {"aliases": [{"a": 1}]})))

                other = appmod.upsert_customer_profile(db, {"accountName": "Fabrikam Inc"})
                ok &= check("account keys stay unique", raises_value_error(
                    lambda: appmod.update_customer_profile(db, other["id"], {"accountKey": "contoso ltd"})))

                # ---- brand assets --------------------------------------------------------
                asset = appmod.add_customer_asset(db, profile["id"], {
                    "kind": "logo-primary", "mime": "image/png",
                    "filename": "contoso.png", "dataBase64": b64(PNG_1PX),
                })
                ok &= check("asset stores metadata and a fetch url",
                            asset["mime"] == "image/png" and asset["url"] == f"/api/customer-assets/{asset['id']}")
                ok &= check("asset bytes are never inlined in metadata", "dataBase64" not in asset and "data_b64" not in asset)
                duplicate = appmod.add_customer_asset(db, profile["id"], {
                    "kind": "logo-primary", "mime": "image/png", "dataBase64": b64(PNG_1PX)})
                ok &= check("identical bytes are deduped by sha256", duplicate["id"] == asset["id"]
                            and len(appmod.list_customer_assets(db, profile["id"])) == 1)

                ok &= check("mime allowlist is enforced", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "logo-primary", "mime": "image/gif", "dataBase64": b64(b"GIF89a")})))
                ok &= check("asset kind vocabulary is enforced", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "billboard", "mime": "image/png", "dataBase64": b64(PNG_1PX)})))
                ok &= check("declared mime must match the bytes", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "icon", "mime": "image/png", "dataBase64": b64(b"not a png at all")})))
                ok &= check("invalid base64 is rejected", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "icon", "mime": "image/png", "dataBase64": "!!!not base64!!!"})))
                oversize = PNG_1PX + b"\x00" * (appmod.CUSTOMER_ASSET_MAX_BYTES + 1)
                ok &= check("per-asset size cap is enforced", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "icon", "mime": "image/png", "dataBase64": b64(oversize)})))
                ok &= check("upload to a missing profile is refused", raises_value_error(
                    lambda: appmod.add_customer_asset(db, "custprofile-nope", {
                        "kind": "icon", "mime": "image/png", "dataBase64": b64(PNG_1PX)})))

                # SVG sanitization: script, handlers, and external refs must not survive ingest.
                hostile = (
                    "<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'>"
                    "<script>alert(2)</script>"
                    "<image xlink:href='https://evil.example.com/pixel.png'/>"
                    "<rect width='10' height='10' fill='#0b5cab'/></svg>"
                )
                svg_asset = appmod.add_customer_asset(db, profile["id"], {
                    "kind": "logo-mono", "mime": "image/svg+xml", "dataBase64": b64(hostile.encode("utf-8"))})
                served = appmod.read_customer_asset(db, svg_asset["id"])
                stored = served[0].decode("utf-8").lower()
                ok &= check("svg script element is stripped", "<script" not in stored)
                ok &= check("svg event handler is stripped", "onload" not in stored)
                ok &= check("svg external reference is stripped", "evil.example.com" not in stored)
                ok &= check("svg drawing content survives sanitization", "rect" in stored)
                ok &= check("svg with an entity declaration is refused", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "icon", "mime": "image/svg+xml",
                        "dataBase64": b64(b"<!DOCTYPE svg [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><svg>&x;</svg>")})))
                ok &= check("non-svg content declared as svg is refused", raises_value_error(
                    lambda: appmod.add_customer_asset(db, profile["id"], {
                        "kind": "icon", "mime": "image/svg+xml", "dataBase64": b64(b"just some text")})))

                ok &= check("asset soft delete removes it from the roster",
                            appmod.delete_customer_asset(db, svg_asset["id"])
                            and svg_asset["id"] not in {a["id"] for a in appmod.list_customer_assets(db, profile["id"])})
                ok &= check("deleted asset is no longer served",
                            appmod.read_customer_asset(db, svg_asset["id"]) is None)

                # ---- contacts: the proposed / confirmed integrity boundary ----------------
                confirmed_prefs = {
                    "channel": "teams", "tone": "direct", "length": "3 bullets max",
                    "greeting": "Hi Priya", "avoid": ["acronyms"], "wantsSummaryFirst": True,
                }
                priya = appmod.create_customer_contact(db, profile["id"], {
                    "displayName": "Priya Patel", "role": "Program lead",
                    "email": "priya@contoso.example.com", "provenance": "user",
                    "prefs": confirmed_prefs,
                })
                ok &= check("a user-supplied preference is confirmed on write",
                            priya["prefsStatus"] == "confirmed" and priya["prefsUsable"] is True)

                sam = appmod.create_customer_contact(db, profile["id"], {
                    "displayName": "Sam Okafor", "email": "sam@contoso.example.com",
                    "provenance": "observed",
                    "prefs": {"channel": "email", "length": "one page"},
                    "evidence": [{"observedAt": "2026-09-01T10:00:00Z", "note": "Replied only to email threads."}],
                })
                ok &= check("an observed preference starts as a proposal",
                            sam["prefsStatus"] == "proposed" and sam["prefsUsable"] is False)
                ok &= check("an observation carries its evidence", len(sam["evidence"]) == 1)
                ok &= check("an observation cannot confirm itself", raises_value_error(
                    lambda: appmod.create_customer_contact(db, profile["id"], {
                        "displayName": "Self Confirmer", "provenance": "observed", "prefsStatus": "confirmed"})))
                ok &= check("an agent cannot promote a proposal via update", raises_value_error(
                    lambda: appmod.update_customer_contact(
                        db, sam["id"], {"prefsStatus": "confirmed"}, actor="agent")))
                ok &= check("an agent editing prefs re-opens them for confirmation",
                            appmod.update_customer_contact(
                                db, priya["id"], {"prefs": {"channel": "email"}}, actor="agent"
                            )["prefsStatus"] == "proposed")
                # Put Priya back the way the user confirmed her.
                appmod.update_customer_contact(db, priya["id"], {"prefs": confirmed_prefs}, actor="user")
                appmod.confirm_customer_contact_prefs(db, priya["id"])

                # ---- one row per person, not one per sighting -----------------------------
                # A second write for the same address must refine the person we already know,
                # or the brief has two answers for one recipient and picks one arbitrarily.
                reobserved = appmod.create_customer_contact(db, profile["id"], {
                    "displayName": "Priya Patel", "email": "PRIYA@contoso.example.com",
                    "provenance": "observed", "prefs": {"channel": "email"},
                })
                ok &= check("re-observing a known address updates that contact instead of duplicating",
                            reobserved["id"] == priya["id"])
                ok &= check("the roster still holds one row for that person",
                            len([c for c in appmod.list_customer_contacts(db, profile["id"])
                                 if (c["email"] or "").lower() == "priya@contoso.example.com"]) == 1)
                # The dedupe path must not become a way to launder an agent write into a user one.
                ok &= check("an agent re-observation re-opens the preference for confirmation",
                            reobserved["prefsStatus"] == "proposed" and reobserved["prefsUsable"] is False)
                ok &= check("an agent cannot confirm through the dedupe path", raises_value_error(
                    lambda: appmod.create_customer_contact(db, profile["id"], {
                        "displayName": "Priya Patel", "email": "priya@contoso.example.com",
                        "provenance": "observed", "prefsStatus": "confirmed"})))
                ok &= check("a contact with no email is never deduped against another",
                            appmod.create_customer_contact(db, profile["id"], {
                                "displayName": "Anonymous One", "provenance": "user"})["id"]
                            != appmod.create_customer_contact(db, profile["id"], {
                                "displayName": "Anonymous Two", "provenance": "user"})["id"])
                # Put Priya back the way the user confirmed her.
                appmod.update_customer_contact(db, priya["id"], {"prefs": confirmed_prefs}, actor="user")
                appmod.confirm_customer_contact_prefs(db, priya["id"])

                ok &= check("contact display name is required", raises_value_error(
                    lambda: appmod.create_customer_contact(db, profile["id"], {"displayName": ""})))
                ok &= check("contact display name is capped", raises_value_error(
                    lambda: appmod.create_customer_contact(db, profile["id"], {"displayName": "x" * 201})))
                ok &= check("preference channel vocabulary is enforced", raises_value_error(
                    lambda: appmod.create_customer_contact(db, profile["id"], {
                        "displayName": "Bad Channel", "prefs": {"channel": "carrier-pigeon"}})))
                ok &= check("preference text fields are capped", raises_value_error(
                    lambda: appmod.create_customer_contact(db, profile["id"], {
                        "displayName": "Long Tone", "prefs": {"tone": "x" * 201}})))
                ok &= check("pending confirmations are counted",
                            appmod.pending_customer_preference_count(db) == 1)

                # ---- brief resolution ----------------------------------------------------
                brief = appmod.customer_brief(db, {
                    "account": "Contoso",
                    "recipients": ["priya@contoso.example.com", "sam@contoso.example.com", "nobody@contoso.example.com"],
                    "artifact": "email",
                })
                by_query = {r["query"]: r for r in brief["recipients"]}
                ok &= check("brief resolves the account through an alias", brief["resolved"] is True
                            and brief["profile"]["accountName"] == "Contoso Ltd")
                ok &= check("brief never acts", brief["automaticAction"] is False)
                ok &= check("confirmed preferences are resolved",
                            by_query["priya@contoso.example.com"]["prefs"]["channel"] == "teams")
                ok &= check(
                    "PROPOSED preferences never reach the usable prefs block",
                    by_query["sam@contoso.example.com"]["prefs"] == {}
                    and by_query["sam@contoso.example.com"]["proposedPrefs"]["channel"] == "email"
                    and by_query["sam@contoso.example.com"]["proposedPrefsAreUnconfirmed"] is True,
                )
                ok &= check("an unconfirmed preference is reported as a gap",
                            any("PROPOSED" in gap for gap in brief["gaps"]))
                ok &= check("an unknown recipient is reported as a gap",
                            by_query["nobody@contoso.example.com"]["matched"] is False
                            and any("nobody@contoso.example.com" in gap for gap in brief["gaps"]))
                ok &= check("guidance renders confirmed preferences as prose",
                            "prefers teams" in brief["guidance"] and "avoid: acronyms" in brief["guidance"])
                ok &= check("guidance never quotes an unconfirmed observation",
                            "one page" not in brief["guidance"])
                ok &= check("brand assets are referenced by url, never inlined",
                            brief["brand"]["assetUrls"]["logo-primary"] == f"/api/customer-assets/{asset['id']}"
                            and "dataBase64" not in str(brief["brand"]))
                ok &= check("compliance constraints reach the guidance block",
                            "cheap" in brief["guidance"] and "Estimates are not a quote." in brief["guidance"])

                unknown = appmod.customer_brief(db, {"account": "Nobody Corp", "recipients": []})
                ok &= check("an unknown account resolves to explicit gaps, not invention",
                            unknown["resolved"] is False and unknown["profile"] is None
                            and len(unknown["gaps"]) >= 3)
                ok &= check("brief requires an account", raises_value_error(
                    lambda: appmod.customer_brief(db, {"recipients": []})))
                ok &= check("brief recipients must be strings", raises_value_error(
                    lambda: appmod.customer_brief(db, {"account": "Contoso", "recipients": [{"x": 1}]})))

                # ---- prompt block --------------------------------------------------------
                block = appmod.customer_profile_block(db, "Contoso")
                ok &= check("prompt block names the account", "Contoso Ltd" in block)
                ok &= check("prompt block flags unconfirmed observations",
                            "UNCONFIRMED" in block and "Sam Okafor" in block)
                ok &= check("prompt block does not apply an unconfirmed preference",
                            "one page" not in block)
                ok &= check("prompt block is empty for an unknown account",
                            appmod.customer_profile_block(db, "Nobody Corp") == "")
                sweep = appmod.customer_customization_block(db)
                ok &= check("sweep block points at the brief endpoint", "/api/customer-brief" in sweep)

                # ---- content-pass scoring against the customer's own rules ----------------
                rules = appmod.customer_voice_rules(db, "Contoso")
                audit = appmod.audit_content(
                    "This is a cheap option for your team.", audience="email", customer_voice=rules)
                kinds = {f["kind"] for f in audit["findings"]}
                ok &= check("customer banned term is a voice finding",
                            any("cheap" in f["detail"] for f in audit["findings"]) and "voice" in kinds)
                ok &= check("missing required disclaimer is a compliance finding", "compliance" in kinds)
                ok &= check("audit reports which account it scored against",
                            audit["accountName"] == "Contoso Ltd" and audit["customerVoiceApplied"] is True)
                generic = appmod.audit_content("This is a cheap option for your team.", audience="email")
                ok &= check("an unscoped audit is unaffected by customer rules",
                            generic["customerVoiceApplied"] is False
                            and not any("do-not-use" in f["detail"] for f in generic["findings"]))
                ok &= check("unknown account falls back to the generic register",
                            appmod.customer_voice_rules(db, "Nobody Corp") == {})

                # ---- archive / delete ----------------------------------------------------
                archived = appmod.archive_customer_profile(db, other["id"])
                ok &= check("archive is a soft delete", archived["status"] == "archived"
                            and other["id"] not in {p["id"] for p in appmod.query_customer_profiles(db)})
                ok &= check("archived profiles remain listable",
                            other["id"] in {p["id"] for p in appmod.query_customer_profiles(db, status="all")})
                ok &= check("an archived profile no longer resolves",
                            appmod.resolve_customer_profile(db, "Fabrikam Inc") is None)
                # Re-adding an account you archived is you asking for it back. Without this, the
                # upsert would update a row that nothing can ever resolve to, and the account
                # would look silently broken.
                revived = appmod.upsert_customer_profile(db, {"accountName": "Fabrikam Inc"})
                ok &= check("re-adding an archived account reactivates it instead of updating a dead row",
                            revived["id"] == other["id"] and revived["status"] == "active"
                            and appmod.resolve_customer_profile(db, "Fabrikam Inc") is not None)
                ok &= check("reactivation does not duplicate the profile",
                            len([p for p in appmod.query_customer_profiles(db, status="all")
                                 if p["accountKey"] == other["accountKey"]]) == 1)
                ok &= check("an explicit status in the payload still wins over reactivation",
                            appmod.upsert_customer_profile(
                                db, {"accountName": "Fabrikam Inc", "status": "archived"}
                            )["status"] == "archived")
                ok &= check("contact soft delete leaves the roster",
                            appmod.delete_customer_contact(db, sam["id"])["status"] == "deleted"
                            and sam["id"] not in {c["id"] for c in appmod.list_customer_contacts(db, profile["id"])})

                # ---- state summary carries counts only, never asset bytes ----------------
                summary = appmod.customer_profiles_summary(db)
                ok &= check("state summary reports counts only",
                            summary["readable"] is True and summary["active"] == 2
                            and summary["pendingPreferences"] == 0
                            and "assets" not in summary)
                db.commit()
            finally:
                db.close()

            # ---- clean-room / packaging boundary -----------------------------------------
            ok &= check(
                "customer tables are excluded from the export zip like career_profile",
                {"customer_profiles", "customer_assets", "customer_contacts"}.issubset(
                    appmod.EXPORT_LOCAL_ONLY_TABLES
                )
                and {"career_profile", "owned_accounts"}.issubset(appmod.EXPORT_LOCAL_ONLY_TABLES),
            )
            ok &= check(
                "customer tables are cleared by an explicit user reset",
                {"customer_profiles", "customer_assets", "customer_contacts"}.issubset(
                    set(appmod.RESETTABLE_TABLES)
                ),
            )
            return 0 if ok else 1
        finally:
            appmod.DB_PATH = original_db
            gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
