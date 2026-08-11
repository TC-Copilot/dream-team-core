# Daily Flow Team - local-first command center for a team of digital employees.
# Author: Shervin Shaffie
# Internal shareable package. Author attribution is intentional; it is not shown in the app UI.
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import sqlite3
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, tzinfo
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


for _stream in (sys.stdout, sys.stderr):
    # Windows consoles still default to cp1252, which turns any non-ASCII character in a startup
    # banner or traceback into a UnicodeEncodeError that kills the app before it ever binds a port.
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DATA_ROOT = APP_ROOT / "data"
DB_PATH = DATA_ROOT / "daily_flow.db"
HOST = "127.0.0.1"


def _load_local_config() -> dict:
    """Optional config.json beside app.py (written by the setup wizard). Environment variables win over it."""
    cfg_path = APP_ROOT / "config.json"
    if cfg_path.exists():
        try:
            # utf-8-sig: PowerShell's Set-Content -Encoding UTF8 writes a BOM, and a BOM makes
            # json.loads fail, which would silently discard the whole config.
            data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


_LOCAL_CONFIG = _load_local_config()


def _read_app_version() -> str:
    """Single source of truth for the version: manifest.json when the package sits beside the app,
    otherwise the version stamp install.ps1 writes into app/.installed-version. Never hardcoded."""
    manifest = APP_ROOT.parent / "manifest.json"
    try:
        if manifest.exists():
            value = json.loads(manifest.read_text(encoding="utf-8-sig")).get("version")
            if value:
                return str(value).strip()
    except Exception:
        pass
    stamp = APP_ROOT / ".installed-version"
    try:
        if stamp.exists():
            value = stamp.read_text(encoding="utf-8-sig").strip()
            if value:
                return value
    except Exception:
        pass
    return "0.0.0"


APP_VERSION = _read_app_version()


def _setting(config_key: str, env_key: str, default):
    value = os.environ.get(env_key)
    if value not in (None, ""):
        return value
    value = _LOCAL_CONFIG.get(config_key)
    if value not in (None, ""):
        return value
    return default


PORT = int(_setting("port", "DAILY_FLOW_PORT", 8787))
LOG_REQUESTS = str(_setting("logRequests", "DAILY_FLOW_LOG_REQUESTS", "")).strip().lower() in {"1", "true", "yes", "on"}
ATTENTION_MAJOR_COOLDOWN_MINUTES = 25

# ---------------------------------------------------------------------------------------------
# Local auth (P1-A). The app binds to 127.0.0.1 only, but any process or browser tab on the
# machine can still reach it, so mutating and private-data endpoints can require a local bearer
# token. Auth is OPT-IN for backward compatibility with already-installed automations: run with
# --auth (or set "requireLocalToken": true in config.json) to turn it on. --no-auth is the default.
# ---------------------------------------------------------------------------------------------
LOCAL_TOKEN_PATH = APP_ROOT / ".local-token"
LOCAL_TOKEN = ""
AUTH_REQUIRED = str(_setting("requireLocalToken", "DAILY_FLOW_REQUIRE_TOKEN", "")).strip().lower() in {"1", "true", "yes", "on"}
MAX_REQUEST_BYTES = 10_485_760  # 10 MB (P1-C)


def load_or_create_local_token() -> str:
    """Read app/.local-token, creating a fresh 32-byte hex token on first run. The file stays inside
    the install folder so the dashboard and the setup wizard can both find it via config.json."""
    global LOCAL_TOKEN
    try:
        if LOCAL_TOKEN_PATH.exists():
            existing = LOCAL_TOKEN_PATH.read_text(encoding="utf-8-sig").strip()
            if existing:
                LOCAL_TOKEN = existing
                return LOCAL_TOKEN
    except Exception:
        pass
    LOCAL_TOKEN = secrets.token_hex(32)
    try:
        LOCAL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_TOKEN_PATH.write_text(LOCAL_TOKEN, encoding="utf-8")
    except Exception:
        pass  # keep the in-memory token even if the file cannot be written
    return LOCAL_TOKEN


def record_local_token_path() -> None:
    """Publish the token file location into config.json so the dashboard and wizard can load it
    without guessing. Never writes the token itself into config.json."""
    cfg_path = APP_ROOT / "config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig")) if cfg_path.exists() else {}
        if not isinstance(data, dict):
            return
        if data.get("localTokenPath") == str(LOCAL_TOKEN_PATH):
            return
        data["localTokenPath"] = str(LOCAL_TOKEN_PATH)
        cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# Endpoints that return the user's private data. Static assets and /api/health stay open so the
# dashboard can load and installers can health-check without a token.
PRIVATE_GET_PREFIXES = (
    "/api/state",
    "/api/gate",
    "/api/impact-ledger",
    "/api/activity-log",
    "/api/jobs/",
    "/api/sweeps",
    "/api/documents/",
    "/api/export",
    "/api/events",
    "/api/knowledge",
    "/api/runtime-inventory",
)

# Idempotency keys seen recently for POST /api/attention-major (P1-F). Deliberately in-memory and
# short-lived: these guard against a double-click / retried automation, not against history.
IDEMPOTENCY_TTL_SECONDS = 60
_idempotency_seen: dict[str, tuple[float, dict[str, Any]]] = {}
_idempotency_lock = threading.Lock()


def idempotency_lookup(key: str) -> dict[str, Any] | None:
    if not key:
        return None
    now = time.time()
    with _idempotency_lock:
        for stale in [k for k, (seen_at, _) in _idempotency_seen.items() if now - seen_at > IDEMPOTENCY_TTL_SECONDS]:
            _idempotency_seen.pop(stale, None)
        hit = _idempotency_seen.get(key)
        return dict(hit[1]) if hit else None


def idempotency_store(key: str, result: dict[str, Any]) -> None:
    if not key:
        return
    with _idempotency_lock:
        _idempotency_seen[key] = (time.time(), dict(result))


class RequestTooLarge(Exception):
    """Raised by read_json() when Content-Length exceeds MAX_REQUEST_BYTES."""


def _default_document_root() -> Path:
    """Resolve the best OneDrive 'Scout' folder for this machine.

    Works for any Microsoft 365 user regardless of how their OneDrive folder is
    named: a business OneDrive ('OneDrive - <Org>'), a personal OneDrive
    ('OneDrive'), or — if neither is synced — a local '~/Scout' fallback. An
    explicit documentRoot setting / DAILY_FLOW_DOCUMENT_ROOT env var always wins.
    """
    home = Path.home()
    business = sorted(p for p in home.glob("OneDrive - *") if p.is_dir())
    # Prefer a business OneDrive that ALREADY has a Scout/ folder: on machines with more than one
    # synced business tenant, alphabetical order would otherwise pick an empty tenant and strand the
    # user's existing documents. Only fall back to the first tenant when none has Scout/ yet.
    for candidate in business:
        if (candidate / "Scout").is_dir():
            return candidate / "Scout"
    if business:
        return business[0] / "Scout"
    if (home / "OneDrive").is_dir():
        return home / "OneDrive" / "Scout"
    return home / "Scout"


ONEDRIVE_DOCUMENT_ROOT = Path(str(_setting("documentRoot", "DAILY_FLOW_DOCUMENT_ROOT", str(_default_document_root()))))
LEGACY_ONEDRIVE_DOCUMENT_ROOT = Path.home() / "OneDrive" / "Scout" / "Daily Flow Documents"
# Working folder for high-value reference material (ROI decks, proposals, roadmaps, etc.) that
# Quinn files during attachment/document review — see review_signal_action_type("attachment-review").
EPIC_WORKING_FOLDER_NAME = "epiq"
EPIC_DOCUMENT_ROOT = ONEDRIVE_DOCUMENT_ROOT / EPIC_WORKING_FOLDER_NAME
ONEDRIVE_WEB_ROOT = str(_setting("oneDriveWebRoot", "DAILY_FLOW_ONEDRIVE_WEB_ROOT", "")).rstrip("/")
SKILL_ROOTS = [
    Path.home() / _root_dir / _skills_sub
    for _root_dir in (".scout", ".copilot", ".copilot-cloud", ".copilot-dev")
    for _skills_sub in ("m-skills", "skills")
]
ARCHITECTURE_SKILLS = {
    "daily-flow-team",
    "daily-flow-setup",
    "docx",
    "pptx",
    "xlsx",
    "excalidraw",
    "web-artifacts",
}
class LosAngelesFallbackTz(tzinfo):
    standard_offset = timedelta(hours=-8)
    daylight_offset = timedelta(hours=-7)
    daylight_delta = timedelta(hours=1)

    @staticmethod
    def first_sunday_on_or_after(value: datetime) -> datetime:
        days_to_go = 6 - value.weekday()
        if days_to_go:
            value += timedelta(days=days_to_go)
        return value

    @classmethod
    def dst_start(cls, year: int) -> datetime:
        return cls.first_sunday_on_or_after(datetime(year, 3, 8, 2))

    @classmethod
    def dst_end(cls, year: int) -> datetime:
        return cls.first_sunday_on_or_after(datetime(year, 11, 1, 2))

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self.standard_offset + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        naive = dt.replace(tzinfo=None)
        return self.daylight_delta if self.dst_start(naive.year) <= naive < self.dst_end(naive.year) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "PDT" if self.dst(dt) else "PST"

    def fromutc(self, dt: datetime) -> datetime:
        standard = (dt.replace(tzinfo=None) + self.standard_offset).replace(tzinfo=self)
        if self.dst(standard):
            return (dt.replace(tzinfo=None) + self.daylight_offset).replace(tzinfo=self)
        return standard


def _timezone_from_windows_registry() -> str:
    """Best-effort IANA zone for this Windows machine, read from the registry. Empty when unavailable
    (non-Windows, locked-down registry, or a Windows zone with no IANA mapping we know)."""
    try:
        import winreg  # Windows-only; the package is Windows-only but keep this import local.
    except Exception:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation") as key:
            windows_name = str(winreg.QueryValueEx(key, "TimeZoneKeyName")[0]).strip()
    except Exception:
        return ""
    return WINDOWS_TO_IANA.get(windows_name, "")


# Windows time-zone key names -> IANA zones. Covers the zones this package realistically runs in;
# anything unmapped falls through to the Los Angeles default, which is the historical behaviour.
WINDOWS_TO_IANA = {
    "Dateline Standard Time": "Etc/GMT+12",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Alaskan Standard Time": "America/Anchorage",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Chihuahua",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "Eastern Standard Time": "America/New_York",
    "US Eastern Standard Time": "America/Indianapolis",
    "Atlantic Standard Time": "America/Halifax",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Eastern Standard Time": "America/Cayenne",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris",
    "Central European Standard Time": "Europe/Warsaw",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "GTB Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Kiev",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Israel Standard Time": "Asia/Jerusalem",
    "E. Africa Standard Time": "Africa/Nairobi",
    "Turkey Standard Time": "Europe/Istanbul",
    "Russian Standard Time": "Europe/Moscow",
    "Arabian Standard Time": "Asia/Dubai",
    "Arab Standard Time": "Asia/Riyadh",
    "Iran Standard Time": "Asia/Tehran",
    "Pakistan Standard Time": "Asia/Karachi",
    "India Standard Time": "Asia/Kolkata",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "SE Asia Standard Time": "Asia/Bangkok",
    "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore",
    "Taipei Standard Time": "Asia/Taipei",
    "W. Australia Standard Time": "Australia/Perth",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "AUS Central Standard Time": "Australia/Darwin",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "E. Australia Standard Time": "Australia/Brisbane",
    "Tasmania Standard Time": "Australia/Hobart",
    "New Zealand Standard Time": "Pacific/Auckland",
    "UTC": "UTC",
}


def _resolve_app_timezone() -> tuple[tzinfo, str]:
    """Resolve the timezone the app reports "today" in, in priority order:

    1. config.json "timezone" / DAILY_FLOW_TIMEZONE (an IANA name, e.g. "Europe/London")
    2. this Windows machine's own time zone, mapped from the registry
    3. America/Los_Angeles (the historical default), with a no-tzdata fallback class

    Returns (tzinfo, resolved-name) so startup can print what it picked.
    """
    configured = str(_setting("timezone", "DAILY_FLOW_TIMEZONE", "")).strip()
    for name in (configured, _timezone_from_windows_registry(), "America/Los_Angeles"):
        if not name:
            continue
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            continue
    return LosAngelesFallbackTz(), "America/Los_Angeles (built-in fallback)"


APP_TIMEZONE, APP_TIMEZONE_NAME = _resolve_app_timezone()

EMPLOYEES = [
    ("Major", "Chief of Staff", "Routes work, enforces approval policy, and proactively surfaces what you should know."),
    ("Riley", "Inbox Agent", "Triage, draft replies, route urgent asks."),
    ("Mina", "Meeting Agent", "Prep, notes, summaries, follow-ups."),
    ("Reese", "Research Agent", "Cited findings and customer/account context."),
    ("Tilly", "Scheduling Agent", "Availability, RSVP risks, scheduling drafts."),
    ("Dash", "Dashboard Agent", "Status, metrics, approval queue, check-ins."),
    ("Drew", "Content Creator Agent", "Docs, decks, proposals, demo packs."),
    ("Logan", "Web Agent", "Internal site, reports, demo web artifacts."),
    ("Quinn", "Quality & Risk Agent", "Pre-send/pre-publish reviews, claim verification, sensitivity classification, automation testing, risk register."),
    ("Casey", "Knowledge & Commitments Agent", "Local knowledge graph: people, projects, commitments, decisions, files, preferences."),
]

# Real trust ladder (low -> high). A level does exactly what it says; it is enforced in both the
# roster and the work flow. "Autonomous" is bounded by each employee's role and still pauses for you
# on Confidential / Highly-Confidential content.
TRUST_LEVELS = ["draft", "assist", "autonomous"]
TRUST_LABELS = {
    "draft": "Draft — prepares everything for you; you send it",
    "assist": "Assist — does the safe internal work itself; sends to others only after your approval",
    "autonomous": "Autonomous — completes its whole job on its own; classified still pauses for you",
}

# The floor that holds at EVERY level. Replaces the old "never external" rule now that Autonomous
# can send. Surfaced to Major so the agent enforces it.
CARDINAL_OUTPUT_RULE = (
    "Honor each employee's trust level exactly and never exceed it. DRAFT = prepare drafts only; you "
    "send them. ASSIST = do reversible internal housekeeping (mark read, label, file/move) "
    "automatically, but send to others only after you approve the prepared item in Results. "
    "AUTONOMOUS = complete the employee's own job end to end, including outward sends, with two hard "
    "exceptions that ALWAYS pause for you: external sends of Confidential / Highly-Confidential "
    "content, and anything whose sensitivity cannot be determined (treat unknown as classified). "
    "Autonomous is bounded by the employee's role — it does its defined job, never anything outside it. "
    "Decision memory is always on at every level. Address the owner as 'you' in everything surfaced."
)

# Per-employee configuration: role lane, mode (adjustable dial vs fixed), default level, and an
# optional note shown where a fixed level needs explaining.
EMPLOYEE_CONFIG = {
    "Major": {"lane": "coordinator", "mode": "fixed", "default": "autonomous",
              "note": "Always-on coordinator — routes work and runs sweeps for you. Any outward action happens through a specialist at that specialist's level."},
    "Riley": {"lane": "inbox", "mode": "adjustable", "default": "draft"},
    "Mina": {"lane": "meetings", "mode": "adjustable", "default": "draft"},
    "Reese": {"lane": "research", "mode": "fixed", "default": "autonomous",
              "note": "Research only — no outward action. Proactively researches and posts cited findings to Results for you; nothing is ever sent to others."},
    "Tilly": {"lane": "scheduling", "mode": "adjustable", "default": "draft"},
    "Dash": {"lane": "visibility", "mode": "fixed", "default": "autonomous",
             "note": "Always keeps the cockpit current — metrics, approvals, blockers, status. Takes no mailbox or outward actions."},
    "Drew": {"lane": "content", "mode": "adjustable", "default": "draft"},
    "Logan": {"lane": "web", "mode": "adjustable", "default": "draft"},
    "Quinn": {"lane": "quality", "mode": "fixed", "default": "autonomous",
              "note": "Internal reviewer only — reviews drafts, verifies claims, classifies sensitivity. Never sends to others."},
    "Casey": {"lane": "knowledge", "mode": "fixed", "default": "autonomous",
              "note": "Local knowledge store only — reads/writes the knowledge graph. Never sends to others."},
}

# Role lanes for the adjustable employees: what they always do, plus the internal-housekeeping verb
# and the outward verb used to derive the Always/Ask/Never protocol per level.
ROLE_LANES = {
    "inbox": {"always": ["Triage your Inbox and separate signal from noise", "Draft replies to routine mail", "Flag VIP, urgent, or time-sensitive items at the top"],
              "internal": "file, label, and mark your mail read", "outward": "send email replies"},
    "meetings": {"always": ["Prep each meeting — who, why, and what's needed", "Surface conflicts and unprepped meetings", "Draft notes, summaries, and follow-ups"],
                 "internal": "organize your meeting notes and prep", "outward": "RSVP and send meeting follow-ups"},
    "scheduling": {"always": ["Check your availability, conflicts, and RSVP risk", "Draft scheduling options and follow-up timing"],
                   "internal": "flag conflicts and hold your focus time", "outward": "book meetings and send scheduling messages"},
    "content": {"always": ["Create docs, decks, and proposals as drafts in Results", "Match your voice and cite sources"],
                "internal": "save and organize your draft artifacts", "outward": "share or publish finished artifacts"},
    "web": {"always": ["Build web artifacts and reports as drafts", "Keep your activity and impact evidence current"],
            "internal": "update your internal logs and evidence", "outward": "publish or share web artifacts"},
    "quality": {
        "always": [
            "Review outbound drafts for accuracy, tone, and sensitivity before they leave",
            "Verify claims and citations in research outputs",
            "Maintain the risk register and flag stale automations",
        ],
        "internal": "update the risk register and review queue",
        "outward": "block or approve sends (internal review only — Quinn never sends externally)",
    },
    "knowledge": {
        "always": [
            "Maintain the local knowledge graph: people, projects, commitments, decisions",
            "Surface relevant context to other employees on request",
            "Flag overdue commitments and stale knowledge entries",
        ],
        "internal": "update knowledge graph entries and commitment tracking",
        "outward": "share knowledge summaries with other employees (internal only)",
    },
}

# DB-backed config for CUSTOM employees (origin='custom'), refreshed from the employees table by
# refresh_custom_employee_config(). Built-ins live in EMPLOYEE_CONFIG above; this holds the rest so
# the per-name helpers (employee_mode/employee_note/derive_protocol) work for added employees too.
_CUSTOM_CONFIG: dict[str, dict[str, Any]] = {}

# Plain-English POLICY shown in the guardrails ledger (level-aware framing).
POLICY = {
    "alwaysAutomatic": [
        "Surface what you should know and prepare drafts (every level)",
        "Reversible internal housekeeping — mark read, label, file/move (Assist and Autonomous)",
        "Keep the cockpit, logs, and ledgers current",
    ],
    "yourLevelControls": [
        "Whether an employee sends to others itself, only after your approval, or not at all",
        "Set per employee: Draft (you send) · Assist (you approve, it sends) · Autonomous (it sends)",
    ],
    "alwaysPausesForYou": [
        "External sends of Confidential / Highly-Confidential content — at every level, including Autonomous",
        "Anything whose sensitivity can't be determined (treated as classified)",
        "Anything outside an employee's defined role",
    ],
}


def employee_mode(name: str) -> str:
    return _employee_config(name).get("mode", "adjustable")


def employee_note(name: str) -> str:
    return _employee_config(name).get("note", "")


def _employee_config(name: str) -> dict[str, Any]:
    """Merged per-employee config: built-ins from the code constant, custom employees from the
    DB-backed cache (refresh_custom_employee_config keeps it current)."""
    if name in EMPLOYEE_CONFIG:
        return EMPLOYEE_CONFIG[name]
    return _CUSTOM_CONFIG.get(name, {})


def derive_protocol(name: str, level: str) -> dict[str, list[str]]:
    """Build Always-Do / Ask-First / Never-Do from (role x level) so the card always tells the truth.
    Written in the agent's own first-person voice ("I" / "my")."""
    cfg = _employee_config(name)
    lane = cfg.get("lane")
    if lane == "coordinator":
        return {"alwaysDo": ["Route work to the right specialist and run sweeps for you",
                             "Surface what you should know — today/tomorrow meetings and directed Teams messages",
                             "Keep the cockpit, approvals, and status current"],
                "askFirst": ["Nothing directly — outward actions go through a specialist at that specialist's level"],
                "neverDo": ["Send or share with anyone but you on my own"]}
    if lane == "visibility":
        return {"alwaysDo": ["Keep your metrics, approvals, blockers, and status current",
                             "Remember context so nothing is dropped"],
                "askFirst": ["Nothing — I only reflect your cockpit"],
                "neverDo": ["Touch your mailbox, calendar, or files", "Contact anyone"]}
    if lane == "research":
        return {"alwaysDo": ["Proactively research relevant topics with citations and account context",
                             "Post cited findings to Results for you on my own"],
                "askFirst": ["Nothing — research has no outward action"],
                "neverDo": ["Send, publish, or contact anyone"]}
    # Quinn and Casey are fixed-autonomous, but neither has an outward action at all, so they get
    # explicit protocols. Falling through to the generic autonomous path below would generate
    # "... on my own as part of my job" from their outward verb and tell the user they can send.
    if lane == "quality":
        return {"alwaysDo": ["Review outbound drafts for accuracy, tone, and sensitivity before they leave",
                             "Verify claims and citations, and classify how sensitive the content is",
                             "Keep the risk register current and flag automations that have gone stale"],
                "askFirst": ["Nothing — I only review; a hold from me stops the team, not you"],
                "neverDo": ["Send, publish, or contact anyone", "Approve anything on your behalf"]}
    if lane == "knowledge":
        return {"alwaysDo": ["Remember people, projects, commitments, and decisions so nothing is rediscovered",
                             "Give the rest of the team the context they need on request",
                             "Flag overdue commitments and knowledge that has gone stale"],
                "askFirst": ["Nothing — I only read and write your local knowledge store"],
                "neverDo": ["Send, publish, or contact anyone", "Let anything I remember leave this machine"]}
    # Built-in role lanes use ROLE_LANES; custom employees carry their own verbs/always in the cache.
    if lane in ROLE_LANES:
        rl = ROLE_LANES[lane]
    else:
        rl = {"always": list(cfg.get("always", [])),
              "internal": cfg.get("internal") or "tidy up",
              "outward": cfg.get("outward") or "act outward"}
    always = list(rl["always"])
    internal = rl["internal"]
    outward = rl["outward"]
    outward_cap = outward[0].upper() + outward[1:] if outward else outward
    internal_cap = internal[0].upper() + internal[1:] if internal else internal
    if level == "draft":
        return {"alwaysDo": always,
                "askFirst": ["Everything waits for you — I leave prepared items in Results for you to send"],
                "neverDo": [f"{internal_cap} on my own", f"{outward_cap}, or contact anyone but you"]}
    if level == "assist":
        return {"alwaysDo": always + [f"Automatically {internal}"],
                "askFirst": [f"Before I {outward} — you approve the prepared item in Results, then I send"],
                "neverDo": ["Send to others without your approval", "Delete or archive anything you haven't handled"]}
    return {"alwaysDo": always + [f"Automatically {internal}", f"{outward_cap} on my own as part of my job"],
            "askFirst": ["Before any external send of Confidential or Highly-Confidential content — that always pauses for you"],
            "neverDo": ["Act outside my job — my role bounds what I do", "Send classified content externally without you"]}


def refresh_custom_employee_config(db: sqlite3.Connection) -> None:
    """Reload the custom-employee config cache from the DB so the per-name helpers work for added
    employees. Called at startup and after any write to the employees table."""
    _CUSTOM_CONFIG.clear()
    try:
        rowset = db.execute(
            "SELECT name, lane, internal_verb, outward_verb, always_json, note FROM employees WHERE origin = 'custom'"
        ).fetchall()
    except sqlite3.OperationalError:
        return  # columns not migrated yet (first run ordering)
    for r in rowset:
        _CUSTOM_CONFIG[r["name"]] = {
            "lane": r["lane"] or "custom",
            "mode": "adjustable",
            "default": "draft",
            "internal": r["internal_verb"] or "organize their own work",
            "outward": r["outward_verb"] or "share or send their output",
            "always": decode_json_list(r["always_json"]),
            "note": r["note"] or "",
        }


PROMPTS_ROOT = APP_ROOT / "prompts"
_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(name: str) -> str:
    """Load app/prompts/<name>.md and substitute the runtime markers.

    The long sweep prompts live as Markdown next to the app instead of as multi-kilobyte string
    literals, so they can be reviewed, diffed, and edited without touching Python. Only the explicit
    markers below are substituted - literal JSON braces in the prompt are left untouched.
    """
    cached = _PROMPT_CACHE.get(name)
    if cached is not None:
        return cached
    text = (PROMPTS_ROOT / f"{name}.md").read_text(encoding="utf-8")
    for marker, value in (
        ("{ONEDRIVE_DOCUMENT_ROOT}", str(ONEDRIVE_DOCUMENT_ROOT)),
        ("{PORT}", str(PORT)),
        ("{CARDINAL_OUTPUT_RULE}", CARDINAL_OUTPUT_RULE),
    ):
        text = text.replace(marker, value)
    _PROMPT_CACHE[name] = text
    return text


def attention_major_sweep_instructions() -> str:
    """The base Attention Major sweep prompt, loaded lazily from app/prompts/attention-major.md."""
    return _load_prompt("attention-major")


OPERATING_LOOP = [
    {
        "time": "Every 30 min",
        "title": "Signal sweep",
        "detail": "Riley, Mina, Tilly, Dash, and Reese check email, Inbox-resident calendar invites, Teams, WorkIQ context, approvals, and open research threads.",
    },
    {
        "time": "Every 30 min",
        "title": "Major coordination loop",
        "detail": "Major classifies new signals, assigns owners, creates mini-swarms, and updates the dashboard without interrupting unless action is needed.",
    },
    {
        "time": "Event-triggered",
        "title": "Employee swarm",
        "detail": "Cross-domain items pull multiple employees together, such as Riley + Reese + Drew for a researched customer reply.",
    },
    {
        "time": "Continuous",
        "title": "Trust gate",
        "detail": "Each employee acts at the level you set — Draft (you send), Assist (you approve, it sends), or Autonomous (it sends). Confidential / Highly-Confidential external sends always pause for you.",
    },
    {
        "time": "After meaningful action",
        "title": "Impact capture",
        "detail": "Logan records boss-ready impact highlights instead of every scan or piece of busy work.",
    },
    {
        "time": "7:00 AM",
        "title": "Morning brief",
        "detail": "Major sets the day plan from overnight signals and recommends the first moves.",
    },
    {
        "time": "1:00 PM",
        "title": "Course correction",
        "detail": "Dash and Major surface urgency changes, stuck work, and priority shifts.",
    },
    {
        "time": "5:00 PM",
        "title": "Evening wrap-up",
        "detail": "Logan and Dash produce the private daily impact summary and carryover list.",
    },
]


def utc_now() -> str:
    return datetime.now(APP_TIMEZONE).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=APP_TIMEZONE)
    return parsed.astimezone(APP_TIMEZONE)


def local_date_key(value: str | None = None) -> str:
    parsed = parse_timestamp(value)
    return (parsed or datetime.now(APP_TIMEZONE)).date().isoformat()


def _after(value: str | None, cutoff: datetime) -> bool:
    """True when an ISO timestamp column is strictly newer than `cutoff`. Rows with an unparseable
    timestamp are kept, so a bad value can never silently hide real data from a ?since= caller."""
    parsed = parse_timestamp(value)
    return True if parsed is None else parsed > cutoff


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def connect() -> sqlite3.Connection:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


def rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def parse_link_json(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        href = str(value.get("href") or value.get("url") or value.get("path") or "")
        label = str(value.get("label") or value.get("title") or "")
        return {
            "href": href,
            "label": label,
            "downloadHref": str(value.get("downloadHref") or ""),
            "oneDrivePath": str(value.get("oneDrivePath") or ""),
        }
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"href": value, "label": ""}
        return parse_link_json(parsed)
    return {}


def looks_like_outlook_item_id(value: str) -> bool:
    text = value.strip()
    if len(text) < 60 or any(char.isspace() for char in text):
        return False
    if text.startswith(("http://", "https://", "/", "file:")) or is_local_file_path(text):
        return False
    if not text.startswith(("AAMk", "AMk", "AQMk")):
        return False
    return all(char.isalnum() or char in {"+", "/", "-", "_", "="} for char in text)


def outlook_draft_link(item_id: str) -> str:
    return f"https://outlook.office.com/mail/deeplink/compose/{quote(item_id.strip(), safe='')}"


def normalize_result_link(link: dict[str, str]) -> dict[str, str] | None:
    href = (link.get("href") or "").strip()
    if not href:
        return None
    if looks_like_outlook_item_id(href):
        return {
            "label": link.get("label") or "Open Outlook draft",
            "href": outlook_draft_link(href),
            "draftId": href,
        }
    normalized = dict(link)
    if not normalized.get("label"):
        normalized["label"] = "Open Outlook draft" if "outlook.office.com/mail" in href else "Open result"
    return normalized


def is_local_file_path(value: str) -> bool:
    return len(value) > 2 and value[1:3] == ":\\" and Path(value).exists()


def office_web_link(filename: str) -> str:
    return f"{ONEDRIVE_WEB_ROOT}/{quote(filename, safe='')}?web=1"


def markdown_to_html(text: str) -> str:
    """Minimal, dependency-free Markdown -> HTML for the in-app document viewer. Not a full parser —
    handles headings, bold/italic, inline code, links, blockquotes, lists, rules and paragraphs.
    Every piece of document text is HTML-escaped first, so file content can never inject markup."""
    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
        return s

    out: list[str] = []
    in_ul = in_ol = in_code = False
    code_buf: list[str] = []

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.strip().startswith("```"):
            if in_code:
                out.append("<pre class='code'>" + html.escape("\n".join(code_buf)) + "</pre>")
                code_buf = []
                in_code = False
            else:
                close_lists()
                in_code = True
            continue
        if in_code:
            code_buf.append(raw)
            continue
        line = raw.rstrip()
        if not line.strip():
            close_lists()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line.strip()):
            close_lists()
            out.append("<hr>")
            continue
        m = re.match(r"^\s*>\s?(.*)$", line)
        if m:
            close_lists()
            out.append(f"<blockquote>{inline(m.group(1))}</blockquote>")
            continue
        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{inline(m.group(1))}</li>")
            continue
        close_lists()
        out.append(f"<p>{inline(line)}</p>")
    if in_code:
        out.append("<pre class='code'>" + html.escape("\n".join(code_buf)) + "</pre>")
    close_lists()
    return "\n".join(out)


def render_markdown_page(title: str, body_md: str) -> bytes:
    """Wrap rendered Markdown in a clean, self-contained HTML page for the document viewer."""
    body = markdown_to_html(body_md)
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>"
        ":root{color-scheme:light dark;}"
        "body{margin:0;background:#f3f3f3;color:#1b1b1b;"
        "font-family:'Segoe UI',system-ui,-apple-system,sans-serif;line-height:1.6;}"
        "main{max-width:820px;margin:0 auto;padding:48px 28px 96px;}"
        "h1,h2,h3,h4{line-height:1.25;margin:1.4em 0 .5em;}"
        "h1{font-size:1.8rem;border-bottom:1px solid #d9d9d9;padding-bottom:.3em;}"
        "h2{font-size:1.4rem;}h3{font-size:1.15rem;}"
        "code{background:#e7e7e7;padding:.12em .4em;border-radius:4px;font-size:.92em;}"
        "pre.code{background:#1e1e1e;color:#e6e6e6;padding:14px 16px;border-radius:8px;overflow:auto;}"
        "pre.code code{background:none;padding:0;}"
        "blockquote{margin:.6em 0;padding:.2em 1em;border-left:3px solid #b9b9b9;color:#555;}"
        "a{color:#0f6cbd;}hr{border:none;border-top:1px solid #d9d9d9;margin:1.6em 0;}"
        "ul,ol{padding-left:1.5em;}li{margin:.2em 0;}"
        "@media(prefers-color-scheme:dark){body{background:#1b1b1b;color:#e6e6e6;}"
        "h1{border-color:#3a3a3a;}code{background:#333;}blockquote{color:#aaa;border-color:#444;}"
        "a{color:#4aa3e8;}hr{border-color:#3a3a3a;}}"
        "</style></head><body><main>"
        f"{body}"
        "</main></body></html>"
    )
    return page.encode("utf-8")


_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>")
_HTML_LINK_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


def teams_message_to_plain_text(raw: str) -> str:
    """Convert an HTML-ish message body into human-readable plain text for Teams.

    Microsoft Graph returns Teams chat message bodies as HTML (body.contentType == "html"), so a
    summary/recommendation quoted or derived from a Teams message -- or any generated prep-brief/
    delivery content built from headings, rules, and lists -- can carry raw <p>/<h2>/<hr>/<b>/<ol>/
    <li> markup straight through into the dashboard preview and the outbound job instructions Major
    uses to compose the actual Teams send. This converts that markup into readable text instead of
    leaking tags: paragraphs, headings (<h1>-<h6>), <blockquote>, <hr>, and <br> become line/
    paragraph breaks, <ol>/<ul> items become "1. "/"- " lines (nested lists tracked with a stack so
    numbering restarts per list), emphasis/inline tags (<b>, <i>, <span>, ...) are dropped while
    keeping their text, links become "label (url)" so the source URL stays readable, and HTML
    entities are decoded. Plain text with no markup at all is returned unchanged (after entity
    decoding), so this is safe to run unconditionally and repeatedly on any Teams-bound text -- it
    never mangles a message that was already plain or already converted.
    """
    text = str(raw or "")
    if "<" not in text or ">" not in text:
        return html.unescape(text).replace("\xa0", " ").strip()

    def _link(m: re.Match) -> str:
        url = html.unescape(m.group(1)).strip()
        label = html.unescape(_HTML_TAG_RE.sub("", m.group(2))).strip()
        return url if (not label or label == url) else f"{label} ({url})"

    text = _HTML_LINK_RE.sub(_link, text)

    out: list[str] = []
    list_stack: list[dict[str, Any]] = []  # each: {"ordered": bool, "count": int}
    pos = 0
    for m in _HTML_TAG_RE.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        closing = bool(m.group(1))
        tag = m.group(2).lower()
        if tag in ("ol", "ul"):
            if closing:
                if list_stack:
                    list_stack.pop()
                out.append("\n")
            else:
                list_stack.append({"ordered": tag == "ol", "count": 0})
        elif tag == "li":
            if not closing:
                if list_stack:
                    top = list_stack[-1]
                    top["count"] += 1
                    out.append(f"\n{top['count']}. " if top["ordered"] else "\n- ")
                else:
                    out.append("\n- ")
            # No output on the closing </li> -- the next <li>'s (or the list's) leading "\n"
            # already separates items, so emitting one here would double the blank line between
            # list entries once the blank-line collapse below runs.
        elif tag == "br":
            out.append("\n")
        elif tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"):
            out.append("\n\n" if closing else "")
        elif tag == "div":
            out.append("\n")
        elif tag == "hr":
            out.append("\n\n")
        # Every other tag (b, strong, i, em, u, span, font, table tags, etc.) is simply dropped --
        # its inner text was already captured between tags -- so no markup ever survives.
    out.append(text[pos:])
    joined = html.unescape("".join(out)).replace("\xa0", " ")

    lines = [ln.strip() for ln in joined.splitlines()]
    cleaned: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln:
            cleaned.append(ln)
            blank_run = 0
        else:
            blank_run += 1
            if blank_run <= 1:
                cleaned.append("")
    return "\n".join(cleaned).strip()


# Free-text fields on a review signal that can carry HTML if the underlying item originated from
# a Teams conversation -- regardless of how the signal ends up *classified*. A Teams-sourced
# request can land as action_type == "meeting-prep" (a prep-brief job), "commitment",
# "attachment-review", etc., not just action_type == "teams", so gating cleanup on that one label
# missed every other workflow category built from the same HTML-ish source text (the "generated
# prep brief" leak). Centralizing the cleanup here, keyed only on excluding email/calendar, means
# every current and future job-instruction path built from these fields (create_review_follow_up_job,
# the Evidence Review dossier, etc.) gets the same guarantee for free.
_REVIEW_SIGNAL_TEXT_FIELDS = (
    "summary", "preview", "recommendation", "explicitAsk", "attachmentAnalysis",
    "latestMessageDelta", "threadDelta", "threadSummary", "misrouteReason",
)


def sanitize_review_signal_html(raw: dict[str, Any], action_type: str) -> dict[str, Any]:
    """Return a copy of `raw` with its known free-text fields converted from HTML-ish markup to
    plain text via teams_message_to_plain_text -- for every action_type except "email" (its
    summary/body handling is intentionally untouched). Calendar signals never reach this (they
    `continue` before it's called). Safe to call unconditionally: teams_message_to_plain_text
    no-ops on text that is already plain, so this never double-mangles a clean field."""
    if action_type == "email":
        return raw
    cleaned = dict(raw)
    for field in _REVIEW_SIGNAL_TEXT_FIELDS:
        value = cleaned.get(field)
        if isinstance(value, str) and value:
            cleaned[field] = teams_message_to_plain_text(value)
    return cleaned


def document_source_path(link: dict[str, str]) -> Path | None:
    source = link.get("href", "")
    if is_local_file_path(source):
        return Path(source)
    one_drive_path = link.get("oneDrivePath", "")
    if is_local_file_path(one_drive_path):
        return Path(one_drive_path)
    if source.startswith("/api/documents/"):
        name = unquote(source.removeprefix("/api/documents/"))
        if not name or "/" in name or "\\" in name:
            return None
        for root in (ONEDRIVE_DOCUMENT_ROOT, LEGACY_ONEDRIVE_DOCUMENT_ROOT):
            candidate = root / name
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def validate_document_backed_completion(data: dict, status: str) -> tuple[str, str] | None:
    """Refuse a fabricated 'completed' claim for a document-backed draft request.

    Returns (override_status, blocker_text) when the worker's reported documentStatus does not
    hold up as real evidence for a completed job -- 'not_found'/'attach_failed' can never complete,
    and 'found' must carry a non-empty link (a real attachment or, if upload is unavailable, a clear
    link/path to the source). Returns None when no override is needed (not a document-backed
    request, or the evidence is sufficient). Kept as a standalone, HTTP-independent function so it
    is directly unit-testable.
    """
    document_status = str(data.get("documentStatus", "")).strip().lower()
    if document_status not in {"found", "not_found", "attach_failed"} or status != "completed":
        return None
    doc_evidence = data.get("documentEvidence") if isinstance(data.get("documentEvidence"), dict) else {}
    link_present = bool(str(data.get("link", "")).strip())
    if document_status == "found" and link_present:
        return None
    if document_status == "not_found":
        reason = (
            f"Source document not found: {doc_evidence.get('reason') or 'no matching document located'}. "
            f"Searched: {', '.join(doc_evidence.get('searchedLocations') or []) or 'not reported'}; "
            f"terms: {doc_evidence.get('searchTerms') or 'not reported'}."
        )
    elif document_status == "attach_failed":
        reason = (
            f"Attachment preparation failed: {doc_evidence.get('reason') or 'reason not reported'}. "
            f"Source document: {doc_evidence.get('sourcePath') or 'not reported'}."
        )
    else:
        reason = (
            "Reported the source document as found, but no attachment or link was "
            "provided, so this cannot be marked completed."
        )
    return "blocked", reason


def publish_document_link(value: Any) -> dict[str, str] | None:
    link = parse_link_json(value)
    source_path = document_source_path(link)
    if not source_path:
        return normalize_result_link(link)

    ONEDRIVE_DOCUMENT_ROOT.mkdir(parents=True, exist_ok=True)
    target = ONEDRIVE_DOCUMENT_ROOT / source_path.name
    if source_path.resolve() != target.resolve():
        shutil.copy2(source_path, target)
    label = link.get("label") or source_path.name
    # Open via the app's own document server: the file is verified to exist locally, so this link
    # ALWAYS resolves. A constructed OneDrive/SharePoint "?web=1" URL is unreliable — it 404s until
    # (and unless) the file has synced to the web and is web-renderable (e.g. .md never is) — so it
    # must NOT be the click target. We still record the synced OneDrive path for reference.
    local_href = f"/api/documents/{quote(target.name)}"
    return {
        "label": label,
        "href": local_href,
        "downloadHref": local_href,
        "oneDrivePath": str(target),
    }


def publish_existing_result_documents() -> None:
    with connect() as db:
        jobs = rows(db.execute("SELECT id, result_link_json FROM jobs WHERE result_link_json != ''"))
        for job in jobs:
            published = publish_document_link(job["result_link_json"])
            if not published:
                continue
            if published == parse_link_json(job["result_link_json"]):
                continue
            db.execute(
                "UPDATE jobs SET result_link_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(published), utc_now(), job["id"]),
            )
        touch_version(db)


def touch_version(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO app_meta(key, value, updated_at) VALUES('version', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (utc_now(), utc_now()),
    )


def ensure_column(db: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Additive, idempotent migration: add a column only if it does not already exist."""
    existing = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    if column not in existing:
        db.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employees (
                name TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                employee TEXT NOT NULL,
                action_type TEXT NOT NULL,
                risk TEXT NOT NULL,
                title TEXT NOT NULL,
                preview TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                details_json TEXT NOT NULL DEFAULT '{}',
                user_guidance TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS inbox_signals (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL,
                sender TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL DEFAULT '',
                importance TEXT NOT NULL DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                has_attachments INTEGER NOT NULL DEFAULT 0,
                signal_type TEXT NOT NULL DEFAULT 'action',
                priority TEXT NOT NULL DEFAULT 'normal',
                summary TEXT NOT NULL,
                recommendation TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                details_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                employee TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                priority TEXT NOT NULL DEFAULT 'normal',
                source TEXT NOT NULL DEFAULT '',
                thread_id TEXT,
                user_message_id TEXT,
                instructions TEXT NOT NULL DEFAULT '',
                result_summary TEXT NOT NULL DEFAULT '',
                result_link_json TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(thread_id) REFERENCES chat_threads(id)
            );

            CREATE TABLE IF NOT EXISTS chat_threads (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                employee TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                employee TEXT NOT NULL,
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                job_id TEXT,
                link_json TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(thread_id) REFERENCES chat_threads(id),
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                employee TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                sensitivity TEXT NOT NULL DEFAULT 'private',
                status TEXT NOT NULL DEFAULT 'logged'
            );

            CREATE TABLE IF NOT EXISTS work_ledger_entries (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                employee TEXT NOT NULL DEFAULT 'Daily Flow Team',
                category TEXT NOT NULL DEFAULT 'work-completed',
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                people_json TEXT NOT NULL DEFAULT '[]',
                customer TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '',
                impact_level TEXT NOT NULL DEFAULT 'supporting',
                impact_summary TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS sweep_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                source TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                channels_json TEXT NOT NULL DEFAULT '[]',
                counts_json TEXT NOT NULL DEFAULT '{}',
                passes_json TEXT NOT NULL DEFAULT '[]',
                verify_json TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS decision_memory (
                content_key TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                decision TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ttl_until TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );

            -- Private, single-row career profile (current/target role + how the user's
            -- company measures performance). Local-only: never shipped in a package, and
            -- user-editable so it intentionally has NO preserve-forever trigger.
            CREATE TABLE IF NOT EXISTS career_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_role TEXT NOT NULL DEFAULT '',
                target_role TEXT NOT NULL DEFAULT '',
                review_rubric TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            -- Casey's local knowledge graph: people, projects, commitments, decisions, files,
            -- preferences, filing rules, research dossiers, templates. Soft-deleted only
            -- (status='deleted'), so a mistaken delete never loses what the team learned.
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                owner TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                related_ids_json TEXT NOT NULL DEFAULT '[]',
                last_verified_at TEXT NOT NULL DEFAULT '',
                stale INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge_entries(type);
            CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);            CREATE INDEX IF NOT EXISTS idx_jobs_thread ON jobs(thread_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_thread ON chat_messages(thread_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_inbox_signals_status ON inbox_signals(status, received_at);
            CREATE INDEX IF NOT EXISTS idx_work_ledger_date ON work_ledger_entries(status, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_sweep_runs_started ON sweep_runs(started_at);

            CREATE TRIGGER IF NOT EXISTS preserve_approvals_delete
            BEFORE DELETE ON approvals
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: approval history is preserved. Update status instead of deleting.');
            END;

            CREATE TRIGGER IF NOT EXISTS preserve_inbox_signals_delete
            BEFORE DELETE ON inbox_signals
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: inbox signal history is preserved. Update status instead of deleting.');
            END;

            CREATE TRIGGER IF NOT EXISTS preserve_jobs_delete
            BEFORE DELETE ON jobs
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: job, result, and content history is preserved forever.');
            END;

            CREATE TRIGGER IF NOT EXISTS preserve_chat_threads_delete
            BEFORE DELETE ON chat_threads
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: chat thread history is preserved forever.');
            END;

            CREATE TRIGGER IF NOT EXISTS preserve_chat_messages_delete
            BEFORE DELETE ON chat_messages
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: chat message history is preserved forever.');
            END;

            CREATE TRIGGER IF NOT EXISTS preserve_events_delete
            BEFORE DELETE ON events
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: activity log history is preserved forever.');
            END;

            CREATE TRIGGER IF NOT EXISTS preserve_work_ledger_entries_delete
            BEFORE DELETE ON work_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'Retention policy: work and impact ledger history is preserved forever.');
            END;
            """
        )
        # --- Additive migrations (3.0.0): progressive trust + per-agent protocol ---
        ensure_column(db, "employees", "trust_level", "TEXT NOT NULL DEFAULT 'draft'")
        ensure_column(db, "employees", "enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(db, "employees", "protocol_json", "TEXT NOT NULL DEFAULT '{}'")
        # v3.1.0: outward-draft send state + per-job skill stamp (accurate usage going forward).
        ensure_column(db, "jobs", "send_state", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "skill", "TEXT NOT NULL DEFAULT ''")
        # v3.3.0: composable team — custom employees + soft-delete lifecycle.
        ensure_column(db, "employees", "origin", "TEXT NOT NULL DEFAULT 'builtin'")
        ensure_column(db, "employees", "status", "TEXT NOT NULL DEFAULT 'active'")
        ensure_column(db, "employees", "lane", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "employees", "internal_verb", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "employees", "outward_verb", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "employees", "always_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(db, "employees", "triggers", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "employees", "skills_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(db, "employees", "source_text", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "employees", "note", "TEXT NOT NULL DEFAULT ''")
        # Phase 2 role expansion: Quinn's review verdicts, Casey's knowledge links, and Major's
        # delegation/SLA tracking all hang off the job row.
        ensure_column(db, "jobs", "quality_review", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "knowledge_links_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(db, "jobs", "risk_level", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "source_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(db, "jobs", "handoff_to", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "eta", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "sla_breached", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "quality_verdict", "TEXT NOT NULL DEFAULT ''")
        # Phase 3 capability surfaces. Each one is a payload an employee attaches to the job it
        # belongs to, so a job row stays the single place you look to see everything about a piece
        # of work. All additive and all defaulted, so existing rows and older clients are unaffected.
        ensure_column(db, "jobs", "quality_audit_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "redaction_required", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "redaction_applied", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "talk_track_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "conference_pack_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "chart_spec_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "runtime_inventory_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "flow_doc_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "brand_voice_profile", "TEXT NOT NULL DEFAULT ''")
        # Evidence Review v1: structured dossier (thread delta, explicit ask, importance-to-me vs
        # importance-to-them, urgency/service impact, attachment/ROI-deck analysis, and the final
        # ACT/FYI/REVIEW REQUIRED recommendation + subtype + next-best action) for attachment/
        # document review cards. Kept on the approval row itself so it survives independently of
        # whatever job is currently acting on the card.
        ensure_column(db, "approvals", "evidence_json", "TEXT NOT NULL DEFAULT '{}'")
        # Evidence Review v1 orchestration: the evidence dossier is also copied onto the follow-up
        # job itself (not just the approval) so Major's active routing logic can read it directly
        # from the job being updated, and content_reviewed is Drew's completion stamp (parallel to
        # Casey's knowledge_links_json and Quinn's quality_verdict) so the auto hand-off knows Drew's
        # leg is done.
        ensure_column(db, "jobs", "evidence_json", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "content_reviewed", "INTEGER NOT NULL DEFAULT 0")
        # Document-backed draft workflow: when a request references a named/recent source document
        # (e.g. "put the Cowork doc I just made into a draft email"), the worker must treat locating
        # that real file as a discovery task before drafting -- never fabricate content in its place.
        # document_status is one of '' (not a document-backed request), 'found', 'not_found', or
        # 'attach_failed'; document_evidence_json carries sourcePath/searchedLocations/searchTerms/
        # reason so a miss is traceable instead of silent. See handle_job_update for the enforcement
        # that refuses to accept a fabricated "completed" claim when the source document was never
        # actually found and attached/linked.
        ensure_column(db, "jobs", "document_status", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "document_evidence_json", "TEXT NOT NULL DEFAULT ''")
        # Explicit role ownership for the document-backed draft chain (Major routes -> Drew
        # discovers/validates the source document -> Riley composes the plain-text draft only once
        # Drew has confirmed a real path/id -> Quinn verifies the confirmed source + attachment/link
        # before the approval card is presented -> Major). document_backed_draft flags a chat job as
        # belonging to this chain at creation time (seeds handoff_to='Drew' immediately); draft_composed
        # is Riley's completion stamp, parallel to Casey's knowledge_links_json and Drew's
        # content_reviewed, so document_draft_next_hop can tell Riley's leg is done.
        ensure_column(db, "jobs", "document_backed_draft", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "draft_composed", "INTEGER NOT NULL DEFAULT 0")
        # Document/deck creation workflow (two-mode: primary real .docx/.pptx draft, fallback a
        # complete Word/PowerPoint Copilot build prompt). artifact_request flags a chat job as
        # belonging to this chain at creation (seeds handoff_to='Drew'); artifact_type is 'docx' or
        # 'pptx'; artifact_creation_mode is Drew's stamp of which mode was used
        # ('created'/'copilot_prompt_fallback'); artifact_package_json carries the full structured
        # package (audience/objective, evidence + gaps, outline/storyboard, content body/speaker
        # notes, design guidance, the Copilot prompt, and destination/status); artifact_needs_context
        # lets Drew request Casey's confirmed customer/project/commitment context before finalizing;
        # narrative_reviewed is Mina's stamp (deck storyline/speaker notes, pptx only); and
        # cover_note_composed is Riley's stamp (plain-text cover note). See
        # validate_artifact_creation_completion for the enforcement that refuses a fabricated
        # "completed" claim in either mode, and artifact_creation_next_hop for Major's active
        # routing through Casey (conditional) -> Drew -> Mina (pptx only) -> Riley -> Quinn -> Major.
        ensure_column(db, "jobs", "artifact_request", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "artifact_type", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "artifact_creation_mode", "TEXT NOT NULL DEFAULT ''")
        ensure_column(db, "jobs", "artifact_package_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(db, "jobs", "artifact_needs_context", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "narrative_reviewed", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "jobs", "cover_note_composed", "INTEGER NOT NULL DEFAULT 0")
        db.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES('history_retention_policy', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (
                json.dumps(
                    {
                        "mode": "preserve-forever",
                        "protectedTables": [
                            "approvals",
                            "inbox_signals",
                            "jobs",
                            "chat_threads",
                            "chat_messages",
                            "events",
                            "work_ledger_entries",
                        ],
                        "calendarHistorySurfaces": [
                            "Activity Log",
                            "Impact Ledger",
                            "Results and drafts prepared",
                        ],
                        "rule": "Do not delete historical logs, body-of-work entries, impact outcomes, content links, jobs/results, approval records, inbox signals, or chat history. Hide transient cards by status update only.",
                    }
                ),
                utc_now(),
            ),
        )
        for name, role, detail in EMPLOYEES:
            db.execute(
                "INSERT OR IGNORE INTO employees(name, role, detail, created_at) VALUES(?, ?, ?, ?)",
                (name, role, detail, utc_now()),
            )
        # Re-seed trust levels to the v3.1.0 model (Draft default for adjustable employees; fixed
        # Autonomous for Major/Dash/Reese). Bumped to version 2 so existing installs migrate once.
        seed_flag = db.execute("SELECT value FROM app_meta WHERE key = 'employee_trust_seed_version'").fetchone()
        if not seed_flag or str(seed_flag[0]) < "2":
            for emp_name, cfg in EMPLOYEE_CONFIG.items():
                db.execute(
                    "UPDATE employees SET trust_level = ?, protocol_json = ? WHERE name = ?",
                    (cfg["default"], json.dumps(derive_protocol(emp_name, cfg["default"])), emp_name),
                )
            db.execute(
                "INSERT INTO app_meta(key, value, updated_at) VALUES('employee_trust_seed_version', '2', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (utc_now(),),
            )
        refresh_custom_employee_config(db)
        # Indexes for the hot paths the dashboard and the sweep automations hit on every load.
        # They matter more and more as the local database accumulates history. jobs(status) and
        # inbox_signals(status) are already covered by the composite indexes created above.
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_jobs_employee ON jobs(employee)",
            "CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_work_ledger_occurred ON work_ledger_entries(occurred_at)",
            "CREATE INDEX IF NOT EXISTS idx_decision_memory_key ON decision_memory(content_key)",
        ):
            try:
                db.execute(index_sql)
            except sqlite3.OperationalError:
                pass  # table/column not present on a partially migrated database
        touch_version(db)


def add_event(db: sqlite3.Connection, employee: str, summary: str, detail: str = "") -> None:
    db.execute(
        "INSERT INTO events(id, created_at, employee, summary, detail) VALUES(?, ?, ?, ?, ?)",
        (new_id("event"), utc_now(), employee, summary, detail),
    )
    touch_version(db)


def title_from_text(text: str) -> str:
    first = " ".join(text.strip().splitlines()[0:1]).strip()
    return first[:96] if first else "User work request"


def dashboard_chat_instructions(db: sqlite3.Connection, message: str) -> str:
    """Instructions for a free-form dashboard 'Chat With Major' request. Unlike the old bare default,
    this (1) hands Major the live team roster — including custom employees and their ENGAGE WHEN
    triggers — and tells him to ROUTE to the best match and stamp who did it, and (2) requires any
    outward deliverable (a LinkedIn/social post, an email/Teams reply, a doc/deck, etc.) to be SAVED as
    a real file and reported with a link + send_state so it actually lands in 'Results and drafts
    prepared' instead of living only inside the chat reply. This is the fix for 'Major said done but I
    can't find the post' and for custom employees never being engaged or attributed."""
    roster = team_protocol_block(db)
    return (
        f"User asked Major (from the dashboard chat): {message}\n\n"
        "ROUTING — engage the right owner (this is mandatory): read the TEAM TRUST + PROTOCOL roster "
        "below and route this to the single best-matching employee using their ENGAGE WHEN triggers and "
        "lane. This INCLUDES any custom employees the user has added — if one matches (e.g. a LinkedIn / "
        "social employee for a post), engage THAT employee; do not silently do it yourself as Major. "
        "Stamp who actually did the work: when you update or complete the job, POST /api/jobs/{jobId} "
        "with employee=<the employee who did it> so the cockpit shows the correct owner and live status. "
        "Do the work itself (or route internally) — never just acknowledge.\n\n"
        "DELIVERABLE — make it land where the user can find it (this is mandatory for outward content): "
        "if this produces an OUTWARD deliverable — a LinkedIn or social post, an email or Teams reply, a "
        "document, deck, or any content meant to be reviewed, posted, or sent — you MUST save it as a "
        f"real file under {ONEDRIVE_DOCUMENT_ROOT} (a .md for a post/message/reply; .docx/.pptx/.xlsx for "
        "documents) and report that file's path in the link field of /api/jobs/{jobId}, so it appears in "
        "'Results and drafts prepared'. Do NOT leave the deliverable only in your chat message — a chat "
        "reply alone does not show up in Results. Also set send_state to the owning employee's trust "
        "level: draft -> 'open_to_send' (the user posts/sends it themselves), assist -> 'ready' "
        "(one-click Send), autonomous -> 'sent' ONLY for a channel you can actually send (email/Teams). "
        "For a channel with no send tool (e.g. LinkedIn/social), always use 'open_to_send' so the user "
        "posts it manually. Confidential/Highly-Confidential or unknown-sensitivity external sends hold "
        "as 'held_classified'. Include skill=<the primary skill id used> when you complete the job.\n\n"
        "SOURCE DOCUMENT (mandatory whenever the request references an existing, named, or "
        "just-created document, e.g. 'the Cowork doc I made before the meeting with Heather'): "
        "this is an explicit ROLE CHAIN, not a single generic step. Major recognizes this pattern "
        "and actively routes the job through it in order using handoffTo — do not let one employee "
        "silently do every step:\n"
        "  1) Major recognizes the request and routes to Drew first (this job is already "
        "handed off to Drew when created for exactly this reason).\n"
        "  2) Drew owns document discovery/validation: search the accessible OneDrive/Scout/Cowork "
        "locations for the real file, using the meeting title/time and subject keywords to "
        "disambiguate if multiple candidates exist. Drew must report the outcome honestly via "
        "POST /api/jobs/{jobId}: documentStatus='found' with the real file attached/linked in the "
        "link field; documentStatus='not_found' if no matching document exists, with "
        "documentEvidence={searchedLocations, searchTerms, reason}; documentStatus='attach_failed' "
        "if the file was found but could not be attached/linked, with "
        "documentEvidence={sourcePath, reason} naming the specific failure. Never claim found/"
        "attached without a real, stable path/id and a successful attach/link.\n"
        "  3) Riley owns composing the human-readable, plain-text draft — and ONLY once Drew has "
        "reported documentStatus='found' with a real link. Riley must NOT emit HTML markup and must "
        "NOT substitute a generated summary for a missing/unfound source; if Drew has not yet "
        "confirmed the document, Riley has nothing to draft yet. Riley reports "
        "draftComposed=true via POST /api/jobs/{jobId} once the draft is ready for review.\n"
        "  4) Quinn verifies the confirmed source and the attachment/link are actually correct "
        "before the approval card is presented to the user (qualityVerdict), same as any other "
        "pre-send review.\n"
        "  5) Major reports the final result/location back to you.\n"
        "A 'not_found' or 'attach_failed' report from Drew — or a 'found' report with no link — is "
        "automatically held as blocked/review-required instead of completed, so you always see an "
        "honest state rather than a fabricated deliverable.\n\n"
        "DOCUMENT/DECK CREATION (mandatory whenever the request asks for a NEW document or deck to "
        "be created, e.g. 'build a proposal deck for the Contoso renewal' or 'put together a "
        "one-pager on Q3 results' — this is distinct from SOURCE DOCUMENT above, which is about an "
        "EXISTING/prior document): this is also an explicit ROLE CHAIN. Major routes to Drew first "
        "(this job is already handed off to Drew when created for exactly this reason). Every "
        "request must yield a structured package, not only prose — artifact type (docx/pptx), "
        "audience/objective, a verified source/evidence list with any gaps explicitly called out, a "
        "Word section outline OR a slide-by-slide storyboard, the content body/speaker notes, "
        "design/visual guidance for a deck, a complete Word/PowerPoint Copilot build prompt with "
        "explicit no-invention/evidence constraints, and the output destination + draft/approval "
        "status. Report this as artifactPackage via POST /api/jobs/{jobId}.\n"
        "  1) Primary mode: Drew creates a real, reviewable .docx or .pptx draft in the permitted "
        f"Scout/OneDrive workspace ({ONEDRIVE_DOCUMENT_ROOT}) — never externally shared or sent "
        "without approval. Report artifactType, creationMode='created', and the real file in the "
        "link field.\n"
        "  2) Fallback mode: when direct creation is unavailable, Drew instead produces the "
        "complete Copilot-ready build prompt (the artifactPackage.copilotPrompt field) the user can "
        "paste into Word Copilot or PowerPoint Copilot, and reports creationMode="
        "'copilot_prompt_fallback'. Never claim 'created' without a real file actually saved and "
        "linked.\n"
        "  3) If the package needs confirmed customer/project/commitment specifics, Drew reports "
        "artifactNeedsContext=true and Major routes to Casey first — Casey supplies confirmed facts "
        "from the knowledge graph only, never speculative ones.\n"
        "  4) For a pptx artifact, Mina owns the narrative structure, slide storyline, and speaker "
        "notes for the storyboard, and reports narrativeReviewed=true once done.\n"
        "  5) Riley composes the human-readable, plain-text cover note (never HTML) once the "
        "package is confirmed, and reports coverNoteComposed=true.\n"
        "  6) Quinn validates the evidence, sensitivity, and the no-invention constraint before the "
        "approval card is presented (qualityVerdict), same as any other pre-send review.\n"
        "  7) Major reports the final package and location back to you.\n"
        "A 'created' claim with no file link, or a 'copilot_prompt_fallback' claim with no build "
        "prompt, is automatically held as blocked/review-required instead of completed.\n\n"
        "Report live progress (status=in_progress with a real one-line update of what you're doing and "
        "who is doing it) and the final result/location back in THIS same Major thread.\n"
        f"{roster}"
    )


def create_chat_job(
    db: sqlite3.Connection,
    message: str,
    thread_id: str | None = None,
    *,
    title: str | None = None,
    instructions: str | None = None,
) -> dict[str, str]:
    now = utc_now()
    title = (title or title_from_text(message)).strip()[:96] or "User work request"
    employee = "Major"
    if not thread_id:
        thread_id = new_id("thread")
        db.execute(
            "INSERT INTO chat_threads(id, created_at, updated_at, employee, title, status) VALUES(?, ?, ?, ?, ?, 'open')",
            (thread_id, now, now, employee, title),
        )
    else:
        db.execute(
            "UPDATE chat_threads SET updated_at = ?, employee = ? WHERE id = ?",
            (now, employee, thread_id),
        )
        if db.total_changes == 0:
            db.execute(
                "INSERT INTO chat_threads(id, created_at, updated_at, employee, title, status) VALUES(?, ?, ?, ?, ?, 'open')",
                (thread_id, now, now, employee, title),
            )

    message_id = new_id("msg")
    job_id = new_id("job")
    db.execute(
        "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, thread_id, user_message_id, instructions) "
        "VALUES(?, ?, ?, ?, 'employee-work', ?, 'queued', 'high', 'dashboard-chat', ?, ?, ?)",
        (
            job_id,
            now,
            now,
            employee,
            title,
            thread_id,
            message_id,
            instructions
            or f"User asked Major: {message}\n\nMajor should decide which employee, if any, does the work. Major must report live progress and final result/location back in this same thread.",
        ),
    )
    db.execute(
        "INSERT INTO chat_messages(id, created_at, thread_id, employee, sender, message, status, job_id) "
        "VALUES(?, ?, ?, ?, 'user', ?, 'queued', ?)",
        (message_id, now, thread_id, employee, message, job_id),
    )
    add_event(db, "Major", f"Major chat request queued: {title}")
    return {"threadId": thread_id, "messageId": message_id, "jobId": job_id}


def approval_details(approval: sqlite3.Row) -> dict[str, Any]:
    if not approval["details_json"]:
        return {}
    try:
        parsed = json.loads(approval["details_json"])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def approval_meeting_title(approval: sqlite3.Row, details: dict[str, Any] | None = None) -> str:
    details = details or {}
    return str(details.get("about") or approval["title"].removeprefix("Inbox calendar decision needed: ")).strip() or approval["title"]


def create_approval_follow_up_job(
    db: sqlite3.Connection,
    approval: sqlite3.Row,
    decision: str,
    user_guidance: str,
) -> str:
    details = approval_details(approval)
    meeting_title = approval_meeting_title(approval, details)
    when = str(details.get("meetingTime") or "time not captured").strip()
    organizer = str(details.get("organizer") or approval["employee"]).strip()
    location = str(details.get("location") or approval["destination"] or "location not captured").strip()
    current_status = str(details.get("currentStatus") or "status not captured").strip()
    conflict_summary = str(details.get("conflictSummary") or approval["preview"]).strip()
    recommendation = str(details.get("recommendation") or "No recommendation captured").strip()
    roster = ", ".join(active_roster_names(db)) or ", ".join(name for name, _, _ in EMPLOYEES)
    visible_message = (
        f"Approved follow-up for {meeting_title} ({when}) with {organizer}: {user_guidance}\n\n"
        f"Major owns this request and should route the work to the right Daily Flow employee. "
        f"If this creates a deck, document, or prep artifact, it will be saved in {ONEDRIVE_DOCUMENT_ROOT} and linked here as an Office web document."
    )
    instructions = (
        f"The user selected {decision} for the meeting approval and gave a specific follow-up request.\n\n"
        f"Meeting: {meeting_title}\n"
        f"When: {when}\n"
        f"Organizer: {organizer}\n"
        f"Location: {location}\n"
        f"Current calendar status: {current_status}\n"
        f"Conflict summary visible to user: {conflict_summary}\n"
        f"Recommendation shown to user: {recommendation}\n\n"
        f"User's requested deliverable or prep: {user_guidance}\n\n"
        f"Use only configured Daily Flow employees when naming an owner: {roster}. "
        "For decks, documents, proposals, and customer-facing narratives, route content creation to Drew unless another configured employee is more appropriate. "
        "Do not invent employee names. Do not show backend API instructions to the user. "
        f"If an artifact is created, save or copy it into {ONEDRIVE_DOCUMENT_ROOT}. "
        "Report completion via /api/jobs/{jobId} with resultSummary summarizing what the artifact contains and the local file path only in the link field so Daily Flow publishes an Office web link in Results and drafts prepared. "
        "Report progress and final result/location back in this same Major thread."
    )
    result = create_chat_job(
        db,
        visible_message,
        title=f"{meeting_title}: {user_guidance}",
        instructions=instructions,
    )
    return result["jobId"]


def guidance_requests_draft(guidance: str) -> bool:
    """True only when the user's instruction on an approved inbox item explicitly asks Major to
    DRAFT / hold it rather than carry it out. The default on any explicit instruction is to ACT
    (send / post / react / forward as told); this flips to a draft only when the user clearly says so."""
    g = (guidance or "").lower().strip()
    if not g:
        return False
    draft_signals = (
        "don't send", "do not send", "dont send", "don't actually send", "do not actually send",
        "don't reply yet", "do not reply yet", "hold off", "hold it", "wait before",
        "let me review", "let me see it first", "let me look", "for my review", "review first",
        "review it first", "before sending", "before you send", "run it by me", "check with me first",
        "get my ok", "get my approval", "prepare a draft", "just draft", "only draft", "draft only",
        "create a draft", "make a draft", "leave a draft", "draft it", "draft this", "draft a reply",
        "draft the reply", "draft for review", "as a draft", "don't send it yet", "do not send it yet",
    )
    return any(sig in g for sig in draft_signals)


def create_review_follow_up_job(
    db: sqlite3.Connection,
    approval: sqlite3.Row,
    decision: str,
    user_guidance: str,
) -> str:
    details = approval_details(approval)
    action_type = approval["action_type"]
    subject = str(details.get("subject") or approval["title"]).strip()
    sender = str(details.get("sender") or details.get("from") or "Unknown sender").strip()
    received_at = str(details.get("receivedAt") or details.get("receivedDateTime") or "time not captured").strip()
    summary = str(details.get("summary") or approval["preview"]).strip()
    recommendation = str(details.get("recommendation") or "No recommendation captured").strip()
    source_id = str(details.get("sourceId") or "").strip()
    owner_hint, _, _ = review_signal_metadata(action_type)
    evidence = details.get("evidence") if isinstance(details.get("evidence"), dict) else {}

    if action_type == "impact-highlight":
        if decision == "approved":
            job_id = new_id("job")
            now = utc_now()
            db.execute(
                "INSERT INTO jobs(id, created_at, updated_at, started_at, completed_at, employee, type, title, status, priority, source, instructions, result_summary) "
                "VALUES(?, ?, ?, ?, ?, 'Logan', 'impact-highlight', ?, 'completed', 'normal', 'approval-inbox', ?, ?)",
                (
                    job_id,
                    now,
                    now,
                    now,
                    now,
                    f"Impact highlight accepted: {subject}",
                    f"User approved this impact highlight candidate from Approval inbox. Source summary: {summary}",
                    user_guidance or summary,
                ),
            )
            add_event(db, "Logan", f"Impact highlight accepted: {subject}", user_guidance or summary)
            return job_id
        add_event(db, "Logan", f"Impact highlight rejected: {subject}", summary)
        return ""

    if decision == "rejected" and action_type in ("email", "attachment-review"):
        job_id = new_id("job")
        now = utc_now()
        instructions = (
            "The user rejected this email review item from the Approval inbox and expects the source email to be deleted.\n\n"
            f"Subject: {subject}\nFrom: {sender}\nReceived: {received_at}\nSource email message ID: {source_id or 'not captured'}\nSummary shown to user: {summary}\n\n"
            "Delete only this source email from the Inbox using the source message ID when present; if the ID is missing, match by exact subject/sender/received time and delete only that email. "
            "Do not send a reply, contact anyone, or delete any other message. Report completed or blocked via /api/jobs/{jobId}."
        )
        db.execute(
            "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, instructions) "
            "VALUES(?, ?, ?, 'Riley', 'email-action', ?, 'queued', 'urgent', 'approval-inbox', ?)",
            (job_id, now, now, f"Delete rejected email: {subject}", instructions),
        )
        add_event(db, "Riley", f"Email deletion queued: {subject}", summary)
        return job_id

    if decision == "rejected":
        add_event(db, "Major", f"Review item rejected: {subject}", summary)
        return ""

    action_label = "approved" if decision == "approved" else "deferred"
    requested = user_guidance or recommendation or "Handle this per the summary and your best judgment."
    # THE APPROVAL INBOX IS THE HUMAN GATE, AND YOUR INSTRUCTION IS A COMMAND.
    # When the user reviews an OUTWARD item (email/teams/outbound-draft) and tells Major what to do,
    # Major DOES it for real — sends the email, posts the Teams reply, adds the 👍 reaction, forwards
    # it, whatever was said. It is NOT a draft. The ONLY time Major prepares a draft instead of acting
    # is when the user's own instruction explicitly asks for one (guidance_requests_draft). Trust levels
    # (Draft/Assist/Autonomous) govern PROACTIVE, unattended sweep work only (see team_protocol_block) —
    # they never turn an explicit instruction into a draft.
    is_outward = action_type in OUTWARD_REVIEW_TYPES  # email, teams, outbound-draft
    draft_only = is_outward and decision == "approved" and guidance_requests_draft(user_guidance)
    execute_now = is_outward and decision == "approved" and not draft_only
    send_state = "ready" if draft_only else ("sent" if execute_now else "")
    workflow_guidance = {
        "meeting-prep": "Create the requested private meeting prep artifact, brief, talk track, or deck for review.",
        "commitment": "Create a trackable follow-up task, draft, or plan for the commitment; do not send anything externally.",
        "blocked-work": "Apply the user's unblock direction, retry or reroute the work, and report the result or blocker.",
        "outbound-draft": "Carry out the user's instruction on this outbound item against its intended recipient — actually send/post it (draft only if they asked). Report send_state with the link.",
        "research": "Run the private research, cite/source findings, and prepare a concise summary or artifact for review.",
        "stale-thread": "Refresh the stale thread with a real status update, next action, blocker, or completion result.",
        "teams": ("Do EXACTLY what the user told you to this Teams message, against the ORIGINAL chat "
                  "(use the chatId/messageId in the Source ID; never start a new chat). Their instruction "
                  "is the command and it may be any action — a reply, a reaction such as a 👍 thumbs-up, a "
                  "forward, or a send. Perform that exact action for real. If a specific action isn't "
                  "supported by your tools (e.g. native reactions), do the closest faithful equivalent "
                  "(such as a short '👍' reply) or report status='blocked' with why — never silently leave a draft."),
        "email": ("Do EXACTLY what the user told you to this email, against the original sender/thread "
                  "(Source ID below). Their instruction is the command — reply, send, forward, etc. "
                  "Carry it out for real: compose per their guidance and SEND it, then file/delete the "
                  "source email from the Inbox (if the Source ID is missing, match by exact "
                  "subject/sender/received time — only that email)."),
        "attachment-review": (
            "This is an Evidence Review item — do not skim it. Major actively orchestrates this: "
            "the job's handoffTo is set (and re-computed after every update) by the app based on the "
            "evidence dossier and the stamps completed so far — you are being routed on purpose, not "
            "following a fixed script. If the dossier's misroute check fired (WorkIQ scope check in "
            "the evidence shows isMisroute=true), this item is outside your role/responsibilities: "
            "the full review chain is skipped, report the suggested owner back to the user as an "
            "ACT: Delegate recommendation, and stop — do not route it through Casey/Drew/Quinn. "
            "Otherwise work it through the staff hand-off chain, stamping your leg's field as you "
            "finish it so Major can advance handoffTo to the next stage: (1) Riley already flagged it "
            "out of the inbox; (2) Casey checks the knowledge graph for related people/projects/prior "
            "commitments and stamps knowledgeLinks; (3) Drew weighs in and stamps contentReviewed=true "
            "ONLY if the attachment/document is a deck, proposal, or authored content requiring "
            "content judgment (the app skips this stage otherwise); (4) Quinn inspects BOTH the email "
            "body and the attachment/linked document content (not just the subject line) and stamps "
            "qualityVerdict with the final quality/risk verdict — ACT, FYI, or REVIEW REQUIRED — with "
            "a subtype and a concrete next-best action; (5) Major reports the completed review back "
            "to the user. "
            f"If the material is important reference content worth keeping (an ROI deck, proposal, "
            f"roadmap, or similar), Quinn files it under {EPIC_DOCUMENT_ROOT} and reports the exact "
            "local path via the link field; if it is not worth keeping, say so instead of filing it. "
            "Report the final verdict (ACT/FYI/REVIEW REQUIRED), subtype, and next-best action in "
            "resultSummary so the recommendation shown to the user stays accurate and traceable."
        ),
    }.get(action_type, "Prepare the requested private follow-up for review.")
    action_clause = ""
    if draft_only:
        action_clause = (
            "\nDRAFT REQUESTED: the user's own instruction asks you to prepare this for their review "
            "rather than send it. Compose it per their guidance and leave it in Results with "
            "send_state='ready' (a one-click Send for the user). Do NOT send it yourself this time."
        )
    elif execute_now:
        action_clause = (
            "\nDO IT NOW — DO NOT DRAFT: the user reviewed this exact item and gave you an instruction, "
            "which is their explicit go-ahead to carry it out now. Actually perform the action they "
            "described (send / post / react / forward). Report send_state='sent' with the link when done. "
            "The only acceptable reason not to act is a genuine inability to (you cannot resolve the "
            "recipient/chat, or your tools cannot perform that action) — then report status='blocked' "
            "with the reason. Never park an instructed action as a silent draft."
        )
    visible_message = (
        f"{action_label.title()} {action_type} follow-up for {subject} from {sender}: {requested}\n\n"
        + ("You told me to draft this, so I'll prepare it for your review." if draft_only
           else "You gave me an instruction on this, so I'll carry it out now." if execute_now
           else "Major will prepare this for you.")
    )
    instructions = (
        f"The user selected {decision} for this {action_type} review item in the Approval inbox.\n\n"
        f"Subject/title: {subject}\n"
        f"Sender/source: {sender}\n"
        f"Received: {received_at}\n"
        f"Source ID: {source_id or 'not captured'}\n"
        f"Summary shown to user: {summary}\n"
        f"Recommendation shown to user: {recommendation}\n"
        + (
            f"Evidence Review dossier already captured — verify/refine rather than starting cold:\n"
            f"  Explicit ask: {evidence.get('explicitAsk') or 'none surfaced yet'}\n"
            f"  Importance to me: {evidence.get('importanceToMe', 'n/a')} · Importance to them: {evidence.get('importanceToThem', 'n/a')}\n"
            f"  Urgency: {evidence.get('urgency', 'n/a')} · Service impact: {evidence.get('serviceImpact', 'n/a')}\n"
            f"  Attachment analysis so far: {evidence.get('attachmentAnalysis', 'n/a')}\n"
            f"  Preliminary verdict: {evidence.get('recommendation', {}).get('verdict', 'n/a')} "
            f"({evidence.get('recommendation', {}).get('subtype', 'n/a')}) — {evidence.get('recommendation', {}).get('nextBestAction', 'n/a')}\n\n"
            if evidence else ""
        )
        + f"THE USER'S INSTRUCTION (this is your command — follow it literally): {requested}\n\n"
        f"Route to {owner_hint} or the appropriate configured Daily Flow employee. "
        f"Workflow instruction: {workflow_guidance}{action_clause} "
        f"If a document/artifact is needed, save it under {ONEDRIVE_DOCUMENT_ROOT} and report the local path. "
        "Report progress and final result/blocker back in this same Major thread via /api/jobs/{jobId}, "
        f"and report send_state='{send_state or 'open_to_send'}' so it shows correctly in Results and drafts prepared."
    )
    result = create_chat_job(
        db,
        visible_message,
        title=f"{subject}: {requested}",
        instructions=instructions,
    )
    if send_state:
        db.execute("UPDATE jobs SET send_state = ? WHERE id = ?", (send_state, result["jobId"]))
    if action_type == "attachment-review" and evidence:
        # Copy the evidence dossier onto the job itself so Major's active routing logic
        # (evidence_review_next_hop, invoked from handle_job_update) can read it directly from
        # the job being updated, and seed the initial handoff so the chain starts moving right away
        # instead of waiting for the external automation to make the first move.
        db.execute("UPDATE jobs SET evidence_json = ? WHERE id = ?", (json.dumps(evidence), result["jobId"]))
        initial_hop = evidence_review_next_hop(evidence, None)
        db.execute("UPDATE jobs SET handoff_to = ? WHERE id = ?", (initial_hop, result["jobId"]))
        add_event(db, "Major", f"Evidence Review routed to {initial_hop}: {subject}",
                  evidence.get("recommendation", {}).get("nextBestAction", ""))
    return result["jobId"]


def active_roster_names(db: sqlite3.Connection) -> list[str]:
    """Names of currently active employees (built-in + custom), for Major's routing roster.
    DB-driven so added/removed employees are reflected everywhere."""
    return [r["name"] for r in db.execute(
        "SELECT name FROM employees WHERE status = 'active' ORDER BY rowid"
    )]


def team_protocol_block(db: sqlite3.Connection) -> str:
    """Render each employee's current level + derived Always/Ask/Never for Major to honor exactly."""
    lines = [
        "",
        "TEAM TRUST + PROTOCOL (current state — honor each employee's level exactly; never exceed it):",
        f"FLOOR: {CARDINAL_OUTPUT_RULE}",
    ]
    for row in db.execute(
        "SELECT name, role, trust_level, enabled, origin, triggers, skills_json, note "
        "FROM employees WHERE status = 'active' ORDER BY rowid"
    ):
        name, role, trust_level, enabled = row["name"], row["role"], row["trust_level"], row["enabled"]
        proto = derive_protocol(name, trust_level)
        mode = employee_mode(name)
        fixed = " (fixed)" if mode == "fixed" else ""
        state = "ENABLED" if enabled else "PAUSED — skip this employee entirely this sweep"
        always = "; ".join(proto.get("alwaysDo", [])) or "—"
        ask = "; ".join(proto.get("askFirst", [])) or "—"
        never = "; ".join(proto.get("neverDo", [])) or "—"
        extra = ""
        if row["origin"] == "custom":
            triggers = (row["triggers"] or "").strip()
            skills = ", ".join(decode_json_list(row["skills_json"]))
            bits = []
            if triggers:
                bits.append(f"ENGAGE WHEN: {triggers}")
            if skills:
                bits.append(f"SKILLS: {skills}")
            note = (row["note"] or "").strip()
            if note:
                bits.append(note)
            if bits:
                extra = " | " + " | ".join(bits)
        lines.append(
            f"- {name} ({role}) [level: {trust_level}{fixed}] [{state}] | ALWAYS DO: {always} | ASK FIRST: {ask} | NEVER DO: {never}{extra}"
        )
    lines.append(
        "Routing: prepared outward items go to the Results/Drafts section (not the Approval inbox). "
        "At Draft, leave them for the user to send (send_state='open_to_send'). At Assist, leave them "
        "for the user's Send click (send_state='ready') and only send when a send-draft job is queued. "
        "At Autonomous, send immediately and mark send_state='sent' (unless Confidential/Highly-"
        "Confidential or unknown sensitivity, then hold as send_state='held_classified'). Tag every "
        "autonomous action's event with autonomous=true."
    )
    lines.append(
        "Proactive preparation BY LEVEL (this is how the level you set actually changes the work): for an "
        "ENABLED employee at ASSIST or AUTONOMOUS, when you find an actionable item squarely in that "
        "employee's lane during a sweep, prepare the work directly into Results/Drafts — do not just park "
        "it as an Approval-inbox card. Internal, reversible work (prep docs, research, summaries, "
        "file/label/mark-read) is simply prepared. For an OUTWARD item: at Assist, prepare the draft and "
        "set send_state='ready' (the user clicks Send); at Autonomous, complete and send it, set "
        "send_state='sent', and tag the event autonomous=true. At DRAFT, keep surfacing via the Approval "
        "inbox or leave an outward draft as send_state='open_to_send'. The classified floor always applies: "
        "Confidential/Highly-Confidential or unknown-sensitivity external sends hold as "
        "send_state='held_classified'. Result: at Assist/Autonomous the user should see prepared items "
        "appear in Results without having to approve each inbox card first."
    )
    lines.append(
        "Skill stamping: whenever you complete a job via /api/jobs/{jobId}, also include skill=<the "
        "primary Scout skill id you used> (e.g. docx, pptx, xlsx, excalidraw, "
        "web-artifacts) so per-employee skill usage is tracked accurately. "
        "If a custom employee did the work, use the skill id from that employee's SKILLS list above. "
        "Omit skill only when no named skill applied."
    )
    return "\n".join(lines)


def attention_major_instructions(db: sqlite3.Connection) -> str:
    """Base sweep instructions plus the live team trust/protocol block and the private career context."""
    return attention_major_sweep_instructions() + "\n" + team_protocol_block(db) + career_profile_block(db)


# ---------- Composable team: custom-employee onboarding, skills, and lifecycle (v3.3.0) ----------

def _safe_skill_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(name or "").strip().lower())


def skill_install_status(names: list[str]) -> list[dict[str, Any]]:
    """For each skill name, report whether it is installed in any Scout skill root."""
    out: list[dict[str, Any]] = []
    for raw in names:
        safe = _safe_skill_id(raw)
        if not safe:
            continue
        where: list[str] = []
        for root in SKILL_ROOTS:
            d = root / safe
            if (d / "SKILL.md").exists() or d.is_dir():
                where.append(str(root).replace(str(Path.home()), "~"))
        out.append({"name": safe, "installed": bool(where), "where": where})
    return out


def _scout_data_roots_present() -> list[Path]:
    """Scout skill roots whose parent data dir actually exists on this machine."""
    return [root for root in SKILL_ROOTS if root.parent.exists()]


def install_skill_text(name: str, text: str) -> list[str]:
    """Write a skill's SKILL.md into every present Scout skill root. Returns the roots written."""
    safe = _safe_skill_id(name)
    if not safe or not text.strip():
        return []
    written: list[str] = []
    roots = _scout_data_roots_present() or SKILL_ROOTS
    for root in roots:
        try:
            d = root / safe
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(text, encoding="utf-8")
            written.append(str(root).replace(str(Path.home()), "~"))
        except OSError:
            continue
    return written


def try_install_skill_local(name: str) -> list[str]:
    """Best-effort local install: copy an existing skill from another root or a bundled source."""
    safe = _safe_skill_id(name)
    if not safe:
        return []
    source: Path | None = None
    for root in SKILL_ROOTS:
        cand = root / safe / "SKILL.md"
        if cand.exists():
            source = cand
            break
    if source is None:
        for base in (APP_ROOT / "bundled-skills", APP_ROOT.parent / "skills"):
            cand = base / safe / "SKILL.md"
            if cand.exists():
                source = cand
                break
    if source is None:
        return []
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return install_skill_text(safe, text)


def reassign_open_jobs_to_major(db: sqlite3.Connection, name: str) -> int:
    """Hand a removed employee's still-open work to Major so nothing is orphaned."""
    if name == "Major":
        return 0
    cur = db.execute(
        "UPDATE jobs SET employee = 'Major', updated_at = ? "
        "WHERE employee = ? AND status IN ('queued', 'in_progress', 'blocked')",
        (utc_now(), name),
    )
    return cur.rowcount


def onboarding_instructions(name: str, source_text: str, hint: str) -> str:
    """Instructions for Major to analyze an employee's operating material and propose a profile."""
    app_url = f"http://{HOST}:{PORT}"
    material = (source_text or "").strip()[:12000] or "(no files provided — propose from the name and hint)"
    return (
        f'You are onboarding a NEW Daily Flow employee named "{name}" into the team. Read the operating '
        f"material below (the user's existing workflow / .md / skill description for this employee) and "
        f"PROPOSE a structured profile so they can join the team.\n\n"
        f"USER HINT: {hint or '(none)'}\n\n"
        f'OPERATING MATERIAL:\n"""\n{material}\n"""\n\n'
        f"Propose the profile and POST it as JSON to {app_url}/api/employees/{quote(name)}/proposal with "
        f"these fields:\n"
        f'- role: short role title (e.g. "Contracts Agent")\n'
        f"- summary: one sentence on what they do\n"
        f'- internal: the reversible internal-housekeeping verb they do on their own, lowercase phrase '
        f'(e.g. "organize and tag contract files")\n'
        f'- outward: their outward action verb, lowercase phrase (e.g. "send contract status emails")\n'
        f'- always: array of 2-4 first-person "always do" lines (e.g. "Track contract renewal dates")\n'
        f'- triggers: when Major should engage them (e.g. "when an email or Teams message mentions a '
        f'contract, renewal, SOW, or MSA")\n'
        f"- skills: array of lowercase Scout skill ids this employee relies on (e.g. [\"docx\",\"pptx\"]); [] if none\n"
        f'- level: recommended default trust level — almost always "draft"\n'
        f"- note: optional one-line note\n\n"
        f"POST that JSON once. Then report completion via /api/jobs/{{jobId}} with a one-line resultSummary "
        f'like "Proposed profile for {name} — ready for review." This is private internal setup: do not '
        f"contact anyone or send anything externally."
    )


def create_civilian_batch(db: sqlite3.Connection, title: str, count: Any, instructions: str = "") -> list[str]:
    """Spin up N throwaway 'civilian' workers for parallel one-off work. Tracked as jobs; results
    return to the user only (CARDINAL OUTPUT RULE). Civilians are unnamed and dissolve when done."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(n, 12))
    now = utc_now()
    ids: list[str] = []
    clean_title = str(title or "Parallel one-off work").strip()
    for i in range(1, n + 1):
        jid = new_id("job")
        db.execute(
            "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, instructions) "
            "VALUES(?, ?, ?, ?, 'civilian', ?, 'queued', 'normal', 'civilian-batch', ?)",
            (jid, now, now, f"Civilian {i}/{n}", f"{clean_title} (civilian {i}/{n})", instructions),
        )
        ids.append(jid)
    add_event(db, "Major", f"Spun up {n} civilian(s) for parallel one-off work: {clean_title}", "Throwaway workers; results return to you only.")
    touch_version(db)
    return ids


def queue_attention_major(db: sqlite3.Connection, source: str = "dashboard", *, force: bool = False) -> dict[str, Any]:
    active = db.execute(
        "SELECT * FROM jobs WHERE type = 'manual-signal-sweep' AND status IN ('queued', 'in_progress') "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if active:
        db.execute(
            "UPDATE jobs SET title = 'Broad Attention Major sweep', priority = 'urgent', instructions = ?, updated_at = ? WHERE id = ?",
            (attention_major_instructions(db), utc_now(), active["id"]),
        )
        add_event(db, "Major", "Attention Major requested: existing broad sweep refreshed.")
        return {"queued": False, "jobId": active["id"], "status": active["status"]}

    now = utc_now()
    if not force:
        recent = db.execute(
            "SELECT * FROM jobs WHERE type = 'manual-signal-sweep' AND status IN ('completed', 'done') "
            "ORDER BY completed_at DESC, updated_at DESC LIMIT 1"
        ).fetchone()
        recent_time = parse_timestamp((recent["completed_at"] or recent["updated_at"]) if recent else None)
        if recent_time and datetime.now(APP_TIMEZONE) - recent_time < timedelta(minutes=ATTENTION_MAJOR_COOLDOWN_MINUTES):
            return {
                "queued": False,
                "jobId": recent["id"],
                "status": "cooldown",
                "cooldownMinutes": ATTENTION_MAJOR_COOLDOWN_MINUTES,
            }

    job_id = new_id("job")
    db.execute(
        "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, instructions) "
        "VALUES(?, ?, ?, 'Major', 'manual-signal-sweep', 'Broad Attention Major sweep', 'queued', 'urgent', ?, ?)",
        (job_id, now, now, source, attention_major_instructions(db)),
    )
    add_event(db, "Major", "Attention Major requested: broad sweep queued.")
    return {"queued": True, "jobId": job_id, "status": "queued"}


def signal_source_id(raw: dict[str, Any]) -> str:
    value = raw.get("sourceId") or raw.get("id") or ""
    if isinstance(value, dict):
        value = value.get("id") or value.get("messageId") or ""
    return str(value).strip()


def signal_sender_text(raw: dict[str, Any]) -> str:
    value = raw.get("sender") or raw.get("from") or ""
    if isinstance(value, dict):
        email = value.get("emailAddress", {}) if isinstance(value.get("emailAddress"), dict) else {}
        value = email.get("address") or email.get("name") or value.get("address") or value.get("name") or ""
    return str(value).strip()


def teams_message_discriminator(signal: dict[str, Any]) -> str:
    """Per-message identity for a Teams signal. A Teams 1:1 chat id (19:...@unq.gbl.spaces)
    is stable PER PERSON, so keying a card on it alone collapses every future message onto
    one card; once that card is rejected/handled, every later message from that person is
    silently muted on the dashboard. Prefer an explicit per-message id; otherwise fall back
    to the message timestamp so a genuinely new message yields a new card while re-posting
    the same message stays idempotent (a dismissed message is not re-nagged)."""
    for key in ("messageId", "chatMessageId", "message_id", "clientMessageId", "teamsMessageId"):
        value = signal.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("messageId") or ""
        text = str(value or "").strip()
        if text:
            return f"msg:{text.lower()}"
    received = str(
        signal.get("receivedAt")
        or signal.get("receivedDateTime")
        or signal.get("createdDateTime")
        or signal.get("messageTime")
        or signal.get("date")
        or ""
    ).strip().lower()
    chat = signal_source_id(signal).lower()
    if chat and received:
        return f"chat:{chat}|at:{received}"
    if received:
        sender = signal_sender_text(signal).lower()
        return f"from:{sender}|at:{received}"
    return ""


def stable_inbox_signal_id(signal: dict[str, Any]) -> str:
    """Deterministic, stable id for a review-signal card.

    Keyed on the normalized action_type plus the durable Graph sourceId when present,
    so the same logical item always resolves to the same card across sweeps even if the
    sweep rewords the subject or reformats the timestamp. Falls back to action_type +
    subject + sender (never a random value) so a missing sourceId still de-duplicates
    instead of spawning a new orphan card every sweep.

    Teams is special-cased to a PER-MESSAGE identity (see teams_message_discriminator):
    a chat-level sourceId alone would permanently mute a person once any one of their
    messages is rejected, so a new message must produce a new card.
    """
    import hashlib

    action_type = review_signal_action_type(signal)
    source_id = signal_source_id(signal).lower()
    if action_type == "teams":
        disc = teams_message_discriminator(signal)
        if disc:
            basis = f"teams|{disc}"
        elif source_id:
            basis = f"teams|id:{source_id}"
        else:
            subject = str(signal.get("subject") or "").strip().lower()
            sender = signal_sender_text(signal).lower()
            basis = f"teams|subj:{subject}|from:{sender}"
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        return f"inbox_{digest}"
    if source_id:
        basis = f"{action_type}|id:{source_id}"
    else:
        subject = str(signal.get("subject") or "").strip().lower()
        sender = signal_sender_text(signal).lower()
        basis = f"{action_type}|subj:{subject}|from:{sender}"
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"inbox_{digest}"


_MEETING_SUBJECT_RE = re.compile(
    r"^\s*(canceled:|cancelled:|accepted:|declined:|tentative:|updated:|"
    r"invitation:|fw:\s*invitation:|meeting forward notification:|"
    r"new time proposed:|location changed:)",
    re.IGNORECASE,
)
# A person's time-off / status block sent as a calendar appointment, e.g. "Emily DTO",
# "Sarah - OOF Mon-Wed", "Raj PTO". The time-off token is essentially the whole subject.
_TIMEOFF_SUBJECT_RE = re.compile(
    r"^[\w .,'/&-]{0,45}\b(dto|oof|ooo|pto|out of office|time off|vacation|"
    r"annual leave|holiday|day off|on leave|sick day)\b[\w .,'/&-]{0,18}$",
    re.IGNORECASE,
)


def looks_like_meeting_message(raw: dict[str, Any]) -> bool:
    """Decide whether an Inbox signal is really a calendar/meeting message rather than a
    plain email. Prefers authoritative Outlook/Graph fields when the sweep provides them
    (meetingMessageType, isMeetingMessage, messageClass, @odata.type eventMessage, or
    Exchange meeting headers); falls back to a conservative subject heuristic for invite
    prefixes (Canceled:/Accepted:/Invitation: ...) and short time-off status blocks
    (e.g. "Emily DTO") for the common case where no message-type metadata is sent."""
    if not isinstance(raw, dict):
        return False
    # 1. Authoritative message-type fields (used when the sweep sends them).
    mmt = str(raw.get("meetingMessageType") or "").strip().lower()
    if mmt and mmt not in ("none", "null", "false", "0"):
        return True
    if raw.get("isMeetingMessage") is True or str(raw.get("isMeetingMessage") or "").strip().lower() == "true":
        return True
    odata = str(raw.get("@odata.type") or raw.get("odataType") or "").lower()
    if "eventmessage" in odata or "calendar" in odata:
        return True
    mclass = str(raw.get("messageClass") or "").lower()
    if mclass.startswith("ipm.schedule") or "meeting" in mclass:
        return True
    headers = raw.get("headers") or raw.get("internetMessageHeaders") or raw.get("internetHeaders") or ""
    htext = headers.lower() if isinstance(headers, str) else json.dumps(headers).lower()
    # Deterministic, authoritative Exchange meeting-message markers (header-level ground
    # truth). These survive even when the invite arrives as a plain Message + TNEF rather
    # than a modern Graph eventMessage — exactly the "Placeholder" invite case that fools
    # subject heuristics (no Invitation:/Accepted: prefix, no meetingMessageType). Any one
    # of these present => it is unambiguously a calendar/meeting message.
    if (
        "ee_meetingmessage" in htext
        or "x-ms-exchange-calendar-originator-id" in htext
        or "x-ms-exchange-calendar-series-instance-id" in htext
    ):
        return True
    # Guard: a genuine Teams chat message or @mention must NEVER be reclassified as a
    # calendar invite by the weak heuristics below. Teams group/meeting chats carry
    # "meeting" in their chat id/topic (19:meeting_...@thread.v2) and an @mention can sit
    # in a meeting-named chat, so the keyword hint and subject heuristics would otherwise
    # misroute it to the calendar pipeline and drop it. Only authoritative meeting evidence
    # (handled above) may reclassify an explicit chat signal.
    signal_kind = " ".join(str(raw.get(k) or "") for k in ("signalType", "type", "sourceType", "workflowType")).lower()
    is_explicit_chat = (
        "mention" in signal_kind
        or "1:1" in signal_kind
        or "directed" in signal_kind
        or str(raw.get("sourceType") or raw.get("channel") or "").strip().lower() in ("teams", "chat")
        or bool(raw.get("mentions"))
        or bool(raw.get("chatId"))
    )
    # Guard 2: explicit Daily Flow workflow lanes are never Outlook invites. In particular
    # "meeting-prep" contains the substring "meeting", which the weak keyword hint below would
    # otherwise treat as a calendar invite and mis-file a prep-gap card into the RSVP pipeline.
    explicit_types = {str(raw.get(k) or "").strip().lower() for k in ("sourceType", "workflowType", "signalType", "type")}
    DAILY_FLOW_WORKFLOW_TYPES = {
        "meeting-prep", "commitment", "follow-up", "followup", "blocked-work",
        "outbound-draft", "research", "impact-highlight", "stale-thread", "thread-nudge",
    }
    if is_explicit_chat or (explicit_types & DAILY_FLOW_WORKFLOW_TYPES):
        return False
    # 2. Explicit classification hint from the sweep.
    hint = " ".join(str(raw.get(k) or "") for k in ("sourceType", "workflowType", "channel")).lower()
    if "calendar" in hint or "invite" in hint or "meeting" in hint:
        return True
    # 3. Conservative subject heuristic (no message-type metadata available).
    subject = str(raw.get("subject") or "").strip()
    if _MEETING_SUBJECT_RE.search(subject):
        return True
    if _TIMEOFF_SUBJECT_RE.match(subject):
        return True
    return False


def signal_attachment_names(raw: dict[str, Any]) -> list[str]:
    """Attachment file name(s) carried on a raw signal, from whichever field the sweep used."""
    names = raw.get("attachmentNames")
    if isinstance(names, list):
        return [str(n).strip() for n in names if str(n).strip()]
    single = raw.get("attachmentName")
    return [str(single).strip()] if str(single or "").strip() else []


def signal_document_link(raw: dict[str, Any]) -> str:
    """A linked document URL carried on a raw signal (SharePoint/OneDrive link, etc.), if any."""
    for key in ("documentLink", "attachmentUrl", "linkedDocumentUrl", "linkedDocument"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def signal_has_reviewable_attachment(raw: dict[str, Any]) -> bool:
    """True when a raw signal carries an attachment or a linked document that deserves a dedicated
    staff review, rather than being left as a generic inbox skim."""
    return bool(raw.get("hasAttachments")) or bool(signal_attachment_names(raw)) or bool(signal_document_link(raw))


ATTACHMENT_ACTION_HINTS = (
    "reply", "respond", "decide", "approve", "sign", "confirm", "please advise",
    "action needed", "needs your", "review and",
)
ATTACHMENT_FYI_HINTS = (
    "fyi", "for your records", "no action", "for reference", "heads up", "informational", "reference only",
)


def classify_attachment_review(raw: dict[str, Any]) -> tuple[bool | None, str]:
    """Best-effort explicit Action-needed vs FYI framing for an attachment/linked-document review
    item, so the card states plainly whether the user must act or it is purely informational.
    Returns (needs_action, recommendation_text); needs_action is None when genuinely undetermined
    (no explicit signal and the text gives no heuristic hint either way)."""
    explicit = raw.get("needsAction")
    if isinstance(explicit, bool):
        needs_action = explicit
    else:
        text = " ".join(str(raw.get(k) or "") for k in ("recommendation", "summary", "subject")).lower()
        # Check FYI cues first: phrases like "no action needed" contain the substring "action
        # needed", so checking action-hints first would misclassify an explicit FYI as actionable.
        if any(hint in text for hint in ATTACHMENT_FYI_HINTS):
            needs_action = False
        elif any(hint in text for hint in ATTACHMENT_ACTION_HINTS):
            needs_action = True
        else:
            needs_action = None
    base = str(raw.get("recommendation") or "").strip()
    if needs_action is True:
        return needs_action, f"🔔 Action needed — {base}" if base else "🔔 Action needed — review this and decide how to respond."
    if needs_action is False:
        return needs_action, f"ℹ️ FYI, no action needed — {base}" if base else "ℹ️ FYI, no action needed — filed for reference."
    return needs_action, base


HIGH_VALUE_ATTACHMENT_HINTS = ("roi", "deck", "proposal", "roadmap", "business case", "pitch deck", "forecast", "budget")


def looks_like_high_value_attachment(raw: dict[str, Any]) -> bool:
    """Heuristic: is this attachment/linked document important reference material (an ROI deck,
    proposal, roadmap, etc.) worth filing, rather than a routine/disposable attachment."""
    explicit = raw.get("highValue")
    if isinstance(explicit, bool):
        return explicit
    haystack = " ".join([
        str(raw.get("subject") or ""), str(raw.get("summary") or ""), " ".join(signal_attachment_names(raw)),
    ]).lower()
    return any(hint in haystack for hint in HIGH_VALUE_ATTACHMENT_HINTS)


_EXPLICIT_ASK_SENTENCE = re.compile(r"[^.!?]*\?[^.!?]*|[^.!?]*\b(?:please|can you|could you|need you to|would you)\b[^.!?]*[.!?]?", re.IGNORECASE)


def extract_explicit_ask(raw: dict[str, Any]) -> str:
    """Best-effort extraction of the concrete ask embedded in the message, so the card names what
    is actually being requested instead of forcing the user to re-read the thread to find it.
    Prefers an ask the sweep already identified; otherwise pulls the first question/imperative
    sentence out of the summary text."""
    explicit = str(raw.get("explicitAsk") or "").strip()
    if explicit:
        return explicit
    text = str(raw.get("summary") or raw.get("preview") or "").strip()
    match = _EXPLICIT_ASK_SENTENCE.search(text)
    if match:
        return match.group(0).strip()
    return ""


def evidence_importance(raw: dict[str, Any], field: str, fallback_high: bool) -> str:
    """importance-to-me / importance-to-them, each low/medium/high. Prefers what the sweep already
    determined; otherwise falls back to a coarse proxy (urgency for -to-me, presence of an
    explicit ask for -to-them)."""
    val = str(raw.get(field) or "").strip().lower()
    if val in {"low", "medium", "high"}:
        return val
    return "high" if fallback_high else "medium"


def evidence_urgency_and_impact(raw: dict[str, Any]) -> tuple[str, str]:
    """Urgency label plus a one-line service-impact note, from explicit sweep fields when present,
    else derived from priority + a small set of impact keywords."""
    urgency = str(raw.get("urgency") or "").strip()
    if not urgency:
        priority = str(raw.get("priority") or "normal").strip().lower()
        urgency = "urgent" if priority in {"urgent", "high"} else "normal"
    impact = str(raw.get("serviceImpact") or "").strip()
    if not impact:
        text = " ".join(str(raw.get(k) or "") for k in ("subject", "summary")).lower()
        impact_hints = ("outage", "down", "blocked", "sla", "production", "customer-facing", "at risk")
        impact = "Possible service/customer impact flagged in the text." if any(h in text for h in impact_hints) else "No service impact indicated."
    return urgency, impact


def evidence_roi_deck_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """ROI-deck-specific evidence fields (investment, expected return, payback, key assumptions),
    populated only when the sweep supplied them or the item is otherwise recognized as an ROI
    deck — never fabricated when the underlying numbers are not actually known."""
    supplied = raw.get("roiFields")
    if isinstance(supplied, dict) and supplied:
        return {
            "totalInvestment": str(supplied.get("totalInvestment") or "").strip(),
            "expectedReturn": str(supplied.get("expectedReturn") or "").strip(),
            "paybackPeriod": str(supplied.get("paybackPeriod") or "").strip(),
            "keyAssumptions": [str(a).strip() for a in supplied.get("keyAssumptions", []) if str(a).strip()] if isinstance(supplied.get("keyAssumptions"), list) else [],
        }
    text = " ".join([str(raw.get("subject") or ""), " ".join(signal_attachment_names(raw))]).lower()
    if "roi" in text:
        return {"totalInvestment": "", "expectedReturn": "", "paybackPeriod": "", "keyAssumptions": [],
                "note": "Recognized as an ROI deck by title, but the sweep did not supply the underlying figures — Quinn should pull them from the deck itself during review."}
    return {}


_MISROUTE_PHRASES = re.compile(
    r"\b(?:please forward (?:this|it) to|reassign(?:ing)? to|not my (?:area|team|responsibility)|"
    r"should (?:go|be sent) to|wrong (?:team|list|distribution|group)|meant for|"
    r"this belongs to|please route to|redirect(?:ing)? to)\s+"
    r"([A-Z][^.,\n]{1,60}?|the [a-z][^.,\n]{1,60}? team)(?=\s+for\b|[.,]|\s*$)",
    re.IGNORECASE,
)


def evidence_misroute_check(raw: dict[str, Any], current_role: str) -> dict[str, Any]:
    """WorkIQ misroute check: compare what this email is actually asking against the user's own
    defined role/responsibilities (WorkIQ current-role text). Prefers an explicit sweep judgment
    (the sweep/Riley has full WorkIQ context and can reason about scope far better than a local
    keyword match); only falls back to a narrow phrase heuristic — and only when the user has
    actually defined a role — so this never guesses a misroute out of thin air."""
    explicit_flag = raw.get("misrouted")
    explicit_owner = str(raw.get("suggestedOwner") or "").strip()
    if isinstance(explicit_flag, bool) and explicit_flag:
        return {
            "isMisroute": True,
            "suggestedOwner": explicit_owner or "the appropriate owner",
            "reason": str(raw.get("misrouteReason") or "Flagged by the sweep as outside your defined scope.").strip(),
        }
    if explicit_owner and explicit_flag is not False:
        return {
            "isMisroute": True,
            "suggestedOwner": explicit_owner,
            "reason": str(raw.get("misrouteReason") or f"The sweep identified {explicit_owner} as the right owner for this.").strip(),
        }
    role_text = str(current_role or "").strip()
    if not role_text:
        return {"isMisroute": False, "suggestedOwner": "", "reason": ""}
    text = " ".join([str(raw.get("subject") or ""), str(raw.get("summary") or raw.get("preview") or "")])
    match = _MISROUTE_PHRASES.search(text)
    if match:
        return {
            "isMisroute": True,
            "suggestedOwner": match.group(1).strip().rstrip(".,"),
            "reason": "The message text itself names a different owner/team for this request.",
        }
    return {"isMisroute": False, "suggestedOwner": "", "reason": ""}


# The staff hand-off an Evidence Review candidate travels through before the user ever sees a
# recommendation: Riley flags it out of the inbox, Casey checks the knowledge graph for related
# people/projects/commitments, Drew weighs in when the artifact is deck/proposal content, Quinn
# renders the final quality/risk verdict and files anything worth keeping, and Major reports the
# result back. Major actively orchestrates this sequence: /api/jobs/{jobId} auto-computes and
# stamps handoffTo to the next required stage (see evidence_review_next_hop) each time a leg
# reports its stamp (knowledgeLinks, contentReviewed, qualityVerdict) — Major isn't just quoting
# static instruction text, it is reading the evidence dossier and the stamps filled in so far and
# deciding where the item goes next, every time the job updates.
EVIDENCE_REVIEW_CHAIN = (
    {"employee": "Riley", "role": "Inbox triage — flags the item out of the generic inbox skim."},
    {"employee": "Casey", "role": "Knowledge check — related people, projects, prior commitments/decisions."},
    {"employee": "Drew", "role": "Content judgment — only when the attachment/document is a deck, proposal, or similar authored artifact."},
    {"employee": "Quinn", "role": "Final quality/risk verdict and filing of anything worth keeping."},
    {"employee": "Major", "role": "Orchestrates every hop above and reports the completed review and recommendation back to you."},
)


def evidence_review_next_hop(evidence: dict[str, Any], job: sqlite3.Row | dict[str, Any] | None) -> str:
    """Major's active routing decision: given the evidence dossier and the stamps a job has
    accumulated so far (knowledgeLinks from Casey, contentReviewed from Drew, qualityVerdict from
    Quinn), decide which employee should work this job next. A misroute always short-circuits
    straight back to Major for delegation — the rest of the review chain is skipped because the
    item was never Quinn/Casey/Drew's to review in the first place."""
    misroute = evidence.get("misroute") if isinstance(evidence, dict) else None
    if isinstance(misroute, dict) and misroute.get("isMisroute"):
        return "Major"

    def _field(name: str) -> Any:
        if job is None:
            return None
        try:
            return job[name]
        except (KeyError, IndexError, TypeError):
            return job.get(name) if isinstance(job, dict) else None

    knowledge_links = _field("knowledge_links_json")
    knowledge_done = bool(knowledge_links) and str(knowledge_links).strip() not in ("", "[]", "null")
    if not knowledge_done:
        return "Casey"
    needs_drew = bool(evidence.get("highValue")) or bool((evidence.get("roiDeck") or {}).get("note")) or bool((evidence.get("roiDeck") or {}).get("totalInvestment"))
    content_reviewed = bool(_field("content_reviewed"))
    if needs_drew and not content_reviewed:
        return "Drew"
    quality_verdict = _field("quality_verdict")
    if not quality_verdict:
        return "Quinn"
    return "Major"


# Document-backed draft workflow: explicit role ownership, not just prose. Major recognizes the
# request and actively routes it; Drew owns document discovery/validation (Cowork/OneDrive/Scout
# search, reporting documentStatus/documentEvidence); Riley owns composing the plain-text draft, and
# ONLY after Drew has confirmed a real path/id — Riley must never emit HTML markup or substitute a
# generated summary for a missing source; Quinn verifies the confirmed source and attachment/link
# before the approval card is presented to the user.
DOCUMENT_DRAFT_CHAIN = (
    {"employee": "Major", "role": "Recognizes a named/recent-document + draft-email request and actively routes the sequence below."},
    {"employee": "Drew", "role": "Document discovery/validation in Cowork, OneDrive, and Scout locations — reports documentStatus/documentEvidence."},
    {"employee": "Riley", "role": "Composes the human-readable, plain-text draft ONLY after Drew has confirmed the document path/id; never emits HTML or a fabricated summary for a missing source."},
    {"employee": "Quinn", "role": "Verifies the confirmed source and attachment/link before the approval card is presented."},
)

_DOCUMENT_REFERENCE_RE = re.compile(
    r"\b(document|doc|deck|file|attachment|cowork|onedrive|word doc(?:ument)?)\b", re.IGNORECASE
)
_DRAFT_INTENT_RE = re.compile(
    r"\b(draft|email|forward|send|attach)\b", re.IGNORECASE
)


def looks_like_document_backed_draft_request(message: str) -> bool:
    """True when a dashboard chat message references an existing/named/just-created document AND
    asks for a draft/email/forward/attach action on it (e.g. 'put the Cowork doc I made before the
    meeting with Heather into a draft email'). Used to seed the explicit Major -> Drew -> Riley ->
    Quinn routing at job creation, instead of leaving the sequence to be inferred from prose alone.
    """
    text = message or ""
    return bool(_DOCUMENT_REFERENCE_RE.search(text)) and bool(_DRAFT_INTENT_RE.search(text))


def document_draft_next_hop(job: sqlite3.Row | dict[str, Any] | None) -> str:
    """Major's active routing decision for the document-backed draft chain (Major -> Drew -> Riley
    -> Quinn -> Major), driven by the stamps accumulated so far: document_status is Drew's
    discovery verdict, draft_composed is Riley's completion stamp, quality_verdict is Quinn's
    sign-off. A 'not_found'/'attach_failed' verdict from Drew returns straight to Major — the job is
    already blocked/review-required and there is nothing for Riley or Quinn to do."""
    def _field(name: str) -> Any:
        if job is None:
            return None
        try:
            return job[name]
        except (KeyError, IndexError, TypeError):
            return job.get(name) if isinstance(job, dict) else None

    document_status = str(_field("document_status") or "").strip().lower()
    if document_status in {"not_found", "attach_failed"}:
        return "Major"
    if document_status != "found":
        return "Drew"
    if not bool(_field("draft_composed")):
        return "Riley"
    if not _field("quality_verdict"):
        return "Quinn"
    return "Major"


# Document/deck creation workflow: two-mode (primary: create a real reviewable .docx/.pptx draft
# in the permitted Scout/OneDrive workspace, never externally shared/sent without approval;
# fallback: produce a complete Word/PowerPoint Copilot build prompt when direct creation is
# unavailable). Every request yields a structured package -- artifact type, audience/objective,
# verified evidence with gaps explicitly called out, a Word section outline or slide-by-slide
# storyboard, content body/speaker notes, deck design/visual guidance, a ready-to-paste Copilot
# prompt with explicit no-invention constraints, and the output destination/draft-approval status
# -- not just prose. Major recognizes the request and actively routes it; Casey supplies confirmed
# customer/project/commitment context only (never speculative facts) when Drew flags a need for
# it; Drew sources evidence and creates the artifact (or the fallback prompt); Mina owns narrative
# structure, slide storyline and speaker notes for a deck; Riley composes the human-readable cover
# note once the package is confirmed; Quinn validates evidence, sensitivity and the no-invention
# constraint before the approval gate.
ARTIFACT_CREATION_CHAIN = (
    {"employee": "Major", "role": "Recognizes a document/deck creation request and actively routes the sequence below."},
    {"employee": "Casey", "role": "Confirmed customer/project/commitment context only, when Drew flags a need for it -- never speculative facts."},
    {"employee": "Drew", "role": "Sources evidence, builds the outline/storyboard and content, and creates the real .docx/.pptx draft (or the Copilot-prompt fallback), reporting creationMode."},
    {"employee": "Mina", "role": "Owns narrative structure, slide storyline and speaker notes for a deck's storyboard (pptx artifacts only)."},
    {"employee": "Riley", "role": "Composes the human-readable cover note once the package is confirmed -- plain text, never HTML."},
    {"employee": "Quinn", "role": "Validates evidence, sensitivity, and the no-invention constraint before the approval gate."},
)

_ARTIFACT_TYPE_RE = re.compile(
    r"\b(document|deck|presentation|slides?|slide deck|proposal|report|word doc(?:ument)?|powerpoint|pptx|docx)\b",
    re.IGNORECASE,
)
_ARTIFACT_CREATE_INTENT_RE = re.compile(
    r"\b(create|build|make|put together|write|prepare|design|draft up)\b", re.IGNORECASE
)


def looks_like_artifact_creation_request(message: str) -> bool:
    """True when a dashboard chat message asks for a NEW document/deck to be created (e.g. 'build
    a proposal deck for the Contoso renewal', 'put together a one-pager on Q3 results'), as opposed
    to a document-backed draft request that references an EXISTING/prior document (see
    looks_like_document_backed_draft_request). Deliberately excludes 'draft' as a creation verb --
    it collides with the document-backed-draft phrasing ('put the Cowork doc into a draft email')
    -- and is checked first at job creation so the two detectors never both fire on one message."""
    text = message or ""
    return bool(_ARTIFACT_TYPE_RE.search(text)) and bool(_ARTIFACT_CREATE_INTENT_RE.search(text))


def artifact_creation_next_hop(job: sqlite3.Row | dict[str, Any] | None) -> str:
    """Major's active routing decision for the document/deck creation chain: Major -> Casey (only
    if Drew flagged artifactNeedsContext and Casey hasn't reported knowledgeLinks yet) -> Drew
    (evidence + package + creation, reports artifactCreationMode) -> Mina (only when the artifact
    is a pptx and narrativeReviewed is not yet set) -> Riley (coverNoteComposed) -> Quinn
    (qualityVerdict) -> Major. Mirrors evidence_review_next_hop / document_draft_next_hop."""
    def _field(name: str) -> Any:
        if job is None:
            return None
        try:
            return job[name]
        except (KeyError, IndexError, TypeError):
            return job.get(name) if isinstance(job, dict) else None

    needs_context = bool(_field("artifact_needs_context"))
    knowledge_links = _field("knowledge_links_json")
    knowledge_done = bool(knowledge_links) and str(knowledge_links).strip() not in ("", "[]", "null")
    if needs_context and not knowledge_done:
        return "Casey"
    creation_mode = str(_field("artifact_creation_mode") or "").strip().lower()
    if creation_mode not in {"created", "copilot_prompt_fallback"}:
        return "Drew"
    artifact_type = str(_field("artifact_type") or "").strip().lower()
    if artifact_type == "pptx" and not bool(_field("narrative_reviewed")):
        return "Mina"
    if not bool(_field("cover_note_composed")):
        return "Riley"
    if not _field("quality_verdict"):
        return "Quinn"
    return "Major"


def validate_artifact_creation_completion(data: dict, status: str) -> tuple[str, str] | None:
    """Refuse a fabricated 'completed' claim for a document/deck creation request. A 'created'
    claim (the primary path) must carry a real file link; a 'copilot_prompt_fallback' claim (the
    fallback path, used when direct creation is unavailable) must carry a non-empty build prompt.
    Returns (override_status, blocker_text) when the reported evidence does not hold up, or None
    when no override is needed (not an artifact-creation completion, or the evidence is
    sufficient). Standalone and HTTP-independent so it is directly unit-testable, mirroring
    validate_document_backed_completion."""
    creation_mode = str(data.get("creationMode", "")).strip().lower()
    if creation_mode not in {"created", "copilot_prompt_fallback"} or status != "completed":
        return None
    package = data.get("artifactPackage") if isinstance(data.get("artifactPackage"), dict) else {}
    link_present = bool(str(data.get("link", "")).strip())
    if creation_mode == "created":
        if link_present:
            return None
        return (
            "blocked",
            "Reported the artifact as created, but no file link was provided, so this cannot be "
            "marked completed.",
        )
    prompt = str(package.get("copilotPrompt") or data.get("copilotPrompt") or "").strip()
    if prompt:
        return None
    return (
        "blocked",
        "Reported a Copilot-prompt fallback, but no build prompt was included, so this cannot be "
        "marked completed.",
    )


def build_evidence_review(raw: dict[str, Any], needs_action: bool | None, recommendation_text: str, current_role: str = "") -> dict[str, Any]:
    """Assemble the Evidence Review v1 dossier for an attachment/linked-document candidate: thread
    delta, explicit ask, importance-to-me vs importance-to-them, urgency/service impact, attachment
    analysis, ROI-deck fields when relevant, and the final ACT/FYI/REVIEW REQUIRED recommendation
    with a subtype and next-best action. Every field prefers what the sweep already determined and
    only falls back to a light heuristic when it didn't."""
    subject = str(raw.get("subject") or "").strip()
    summary = str(raw.get("summary") or raw.get("preview") or "").strip()
    thread_delta = str(raw.get("latestMessageDelta") or raw.get("threadDelta") or "").strip() or summary
    thread_summary = str(raw.get("threadSummary") or "").strip() or summary
    explicit_ask = extract_explicit_ask(raw)
    priority = str(raw.get("priority") or "normal").strip().lower()
    urgent_default = priority in {"urgent", "high"}
    importance_to_me = evidence_importance(raw, "importanceToMe", urgent_default)
    importance_to_them = evidence_importance(raw, "importanceToThem", bool(explicit_ask))
    urgency, service_impact = evidence_urgency_and_impact(raw)
    attachment_names = signal_attachment_names(raw)
    document_link = signal_document_link(raw)
    high_value = looks_like_high_value_attachment(raw)
    roi_fields = evidence_roi_deck_fields(raw)
    attachment_analysis = str(raw.get("attachmentAnalysis") or "").strip()
    if not attachment_analysis:
        what = ", ".join(attachment_names) if attachment_names else (document_link or "the linked document")
        attachment_analysis = (
            f"{'High-value reference material' if high_value else 'Attachment'} ({what}) — "
            f"{'flagged for keeping' if high_value else 'not flagged as reference material'}; "
            "full content review happens once Quinn opens it."
        )

    # Final verdict: ACT / FYI / REVIEW REQUIRED. REVIEW REQUIRED covers genuine ambiguity — the
    # sweep gave no clear act-vs-FYI signal, or the priority-to-me/priority-to-them read conflicts
    # (e.g. urgent to them but no clear ask surfaced for me) — rather than silently guessing.
    conflicting = importance_to_them == "high" and importance_to_me == "low" and not explicit_ask
    if needs_action is None or conflicting:
        verdict = "review_required"
        subtype = "conflicting_priority" if conflicting else "insufficient_context"
        next_best_action = (
            "Ask Quinn to confirm the sender's real expectation before deciding act vs. FYI."
            if conflicting else
            "Have Quinn open the attachment/document directly — the summary alone doesn't say whether this needs your action."
        )
    elif needs_action:
        verdict = "act"
        subtype = "reply_or_decision_needed" if explicit_ask else "review_and_respond"
        next_best_action = explicit_ask or "Reply or make the requested decision, then let Quinn file the source material."
    else:
        verdict = "fyi"
        subtype = "reference_only"
        next_best_action = (
            f"File under {EPIC_DOCUMENT_ROOT} for reference — no action needed."
            if high_value else "No action needed; dismiss once reviewed."
        )

    # WorkIQ misroute check: if this clearly belongs to someone else's scope (either the sweep says
    # so, or the message text itself names a different owner/team), that overrides everything above
    # — it's an action for the user (delegate it), not a FYI/ambiguous review, and it never needs to
    # travel through Casey/Drew/Quinn since it was never really theirs to review.
    misroute = evidence_misroute_check(raw, current_role)
    if misroute.get("isMisroute"):
        verdict = "act"
        subtype = "delegate_misroute"
        owner = misroute.get("suggestedOwner") or "the appropriate owner"
        next_best_action = f"Delegate to {owner} — {misroute.get('reason') or 'outside your defined scope'}."
        recommendation_text = f"ACT: Delegate — this belongs to {owner}."

    return {
        "threadSummary": thread_summary,
        "latestMessageDelta": thread_delta,
        "explicitAsk": explicit_ask,
        "importanceToMe": importance_to_me,
        "importanceToThem": importance_to_them,
        "urgency": urgency,
        "serviceImpact": service_impact,
        "attachmentAnalysis": attachment_analysis,
        "attachmentNames": attachment_names,
        "documentLink": document_link,
        "highValue": high_value,
        "roiDeck": roi_fields,
        "misroute": misroute,
        "recommendation": {
            "verdict": verdict,
            "subtype": subtype,
            "nextBestAction": next_best_action,
            "displayText": recommendation_text,
        },
        "reviewChain": list(EVIDENCE_REVIEW_CHAIN),
    }


def review_signal_action_type(raw: dict[str, Any]) -> str:
    if looks_like_meeting_message(raw):
        return "calendar"
    source_type = str(raw.get("sourceType") or raw.get("workflowType") or raw.get("channel") or raw.get("signalType") or raw.get("type") or "email").lower()
    signal_type = str(raw.get("signalType") or raw.get("type") or "").lower()
    summary_text = " ".join(str(raw.get(key) or "") for key in ("subject", "summary", "recommendation")).lower()
    # 1. Impact/gratitude intent explicitly tagged by the sweep itself -> impact-highlight.
    if (
        "impact" in source_type
        or "highlight" in source_type
        or "gratitude" in source_type
        or "impact" in signal_type
        or "gratitude" in signal_type
    ):
        return "impact-highlight"
    # 2. Explicit Teams chat / @mention signals must route to the Teams lane reliably, even
    # when the message text contains "thanks" or praise. The user requires every 1:1 and
    # @mention to surface as a directed Teams item, so this is checked BEFORE the free-text
    # gratitude heuristic below — otherwise an actionable @mention that merely says "thanks"
    # would be mis-lane'd to impact-highlight and look like passive praise.
    explicit_teams = (
        str(raw.get("sourceType") or raw.get("channel") or "").strip().lower() in ("teams", "chat")
        or "mention" in signal_type
        or "1:1" in signal_type
        or "directed" in signal_type
        or bool(raw.get("mentions"))
        or bool(raw.get("chatId"))
    )
    if explicit_teams:
        return "teams"
    # 3. Gratitude/praise found only in free text on a non-Teams signal -> impact-highlight.
    if (
        "thank you" in summary_text
        or "thanks for" in summary_text
        or "appreciate" in summary_text
        or "grateful" in summary_text
    ):
        return "impact-highlight"
    if "meeting-prep" in source_type or "prep" in source_type:
        return "meeting-prep"
    if "commitment" in source_type or "follow-up" in source_type or "followup" in source_type:
        return "commitment"
    if "blocked" in source_type or "blocker" in source_type:
        return "blocked-work"
    if "outbound" in source_type or "draft-ready" in source_type:
        return "outbound-draft"
    if "research" in source_type:
        return "research"
    if "stale" in source_type or "thread-nudge" in source_type:
        return "stale-thread"
    if "team" in source_type or "chat" in source_type or "mention" in source_type:
        return "teams"
    # An otherwise-generic email carrying an attachment or a linked document deserves a staff
    # reviewer's actual read (body + attachment/document content), not a plain inbox skim.
    if signal_has_reviewable_attachment(raw):
        return "attachment-review"
    return "email"


# Review types whose follow-up produces an OUTWARD send. When the user explicitly APPROVES one of these
# in the inbox, that is the approval to send it (see create_review_follow_up_job).
OUTWARD_REVIEW_TYPES = {"email", "teams", "outbound-draft"}


def review_signal_metadata(action_type: str) -> tuple[str, str, str]:
    return {
        "email": ("Riley", "Inbox email review needed", "Outlook Inbox"),
        "teams": ("Riley", "Teams review needed", "Microsoft Teams"),
        "meeting-prep": ("Mina", "Meeting prep gap", "Calendar / meeting context"),
        "commitment": ("Major", "Follow-up commitment detected", "Commitment tracking"),
        "blocked-work": ("Dash", "Blocked work needs decision", "Daily Flow blocker"),
        "outbound-draft": ("Riley", "Outbound draft ready for review", "Draft approval"),
        "research": ("Reese", "Customer research opportunity", "Research queue"),
        "impact-highlight": ("Logan", "Impact highlight candidate", "Impact ledger"),
        "stale-thread": ("Major", "Stale thread needs attention", "Major thread"),
        "attachment-review": ("Quinn", "Attachment/document review needed", str(EPIC_DOCUMENT_ROOT)),
    }.get(action_type, ("Major", "Review needed", "Daily Flow"))


# Where to "open the source" for each surfaced item, and the friendly link label.
_SOURCE_LINK_LABELS = {
    "email": "Open in Outlook",
    "teams": "Open in Teams",
    "calendar": "Open invite",
    "meeting-prep": "Open in calendar",
}


def approval_source_link(action_type: str, details: dict[str, Any]) -> dict[str, str]:
    """Best source URL + label for an approval card so the user can jump to the original.
    Prefers an explicit URL Major posts (Graph webLink/webUrl); returns {} when none is available."""
    if not isinstance(details, dict):
        return {}
    url = ""
    for key in ("sourceUrl", "webLink", "webUrl", "url", "sourceLink", "link"):
        val = details.get(key)
        if isinstance(val, str) and val.strip().lower().startswith("http"):
            url = val.strip()
            break
    if not url:
        return {}
    return {"url": url, "label": _SOURCE_LINK_LABELS.get(action_type, "Open source")}


# Review-signal types that get content-based de-duplication. Calendar is intentionally
# excluded: distinct meetings can share a generic title ("Inbox calendar invite"), so
# calendar cards are de-duplicated by their own subject+organizer+time+sourceId id only.
DEDUPE_TYPES = {
    "email", "teams", "meeting-prep", "commitment", "blocked-work",
    "outbound-draft", "research", "impact-highlight", "stale-thread", "attachment-review",
}
ADVISORY_DEDUPE_TYPES = {
    "meeting-prep", "commitment", "blocked-work", "outbound-draft",
    "research", "impact-highlight", "stale-thread",
}

_DEDUPE_REPLY = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.IGNORECASE)
_DEDUPE_PARENS = re.compile(r"\([^)]*\)")
_DEDUPE_PREFIXES = re.compile(
    r"^(?:\s*(?:meeting prep gap|inbox email review needed|teams review needed|"
    r"prep for|prep|reminder)\s*[:\-]?\s*)+",
    re.IGNORECASE,
)
_DEDUPE_NOISE = re.compile(
    r"\b(?:needs a prep brief|prep brief|needs prep|prep for|prep|tomorrow|today|reminder|fyi)\b",
    re.IGNORECASE,
)
_DEDUPE_NONWORD = re.compile(r"[^a-z0-9]+")


def normalize_dedupe_subject(subject: str, action_type: str) -> str:
    """Collapse a card's subject to a stable topic key so the same logical item
    de-duplicates even when sweeps reword it or invent different sourceIds."""
    text = str(subject or "").lower().strip()
    text = _DEDUPE_REPLY.sub("", text)
    text = _DEDUPE_PREFIXES.sub("", text)
    text = _DEDUPE_PARENS.sub(" ", text)  # drop "(Wed Jun 18, 10:30 AM)" etc.
    if action_type in ADVISORY_DEDUPE_TYPES:
        text = _DEDUPE_NOISE.sub(" ", text)
    text = _DEDUPE_NONWORD.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def approval_content_key(action_type: str, subject: str, sender: str) -> str:
    """Identity used to detect duplicate cards that point at the same logical item
    regardless of the model-supplied sourceId. Email/Teams include the sender so two
    unrelated messages that merely share a subject are not merged."""
    norm = normalize_dedupe_subject(subject, action_type)
    if action_type in ("email", "teams", "attachment-review"):
        return f"{action_type}|{str(sender or '').strip().lower()}|{norm}"
    return f"{action_type}|{norm}"


# --- Decision memory (3.0.0): stop re-surfacing items the user already dismissed ---
DECISION_MEMORY_TYPES = ("email", "teams", "attachment-review")
DECISION_MEMORY_TTL_DAYS = {"rejected": 14, "deferred": 3}


def record_decision_memory(db: sqlite3.Connection, action_type: str, subject: str, sender: str, source_id: str, decision: str) -> None:
    """Remember a reject/defer so the same logical item is not re-surfaced within its TTL."""
    if action_type not in DECISION_MEMORY_TYPES or decision not in DECISION_MEMORY_TTL_DAYS:
        return
    key = approval_content_key(action_type, subject, sender)
    now = utc_now()
    ttl_until = (datetime.now(APP_TIMEZONE) + timedelta(days=DECISION_MEMORY_TTL_DAYS[decision])).isoformat()
    db.execute(
        """
        INSERT INTO decision_memory(content_key, action_type, subject, sender, source_id, decision, created_at, updated_at, ttl_until, status)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(content_key) DO UPDATE SET
            subject=excluded.subject, sender=excluded.sender, source_id=excluded.source_id,
            decision=excluded.decision, updated_at=excluded.updated_at, ttl_until=excluded.ttl_until, status='active'
        """,
        (key, action_type, subject, sender, source_id, decision, now, now, ttl_until),
    )


def active_decision_memory(db: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Map content_key -> row for un-expired, active dismissals."""
    now = datetime.now(APP_TIMEZONE)
    result: dict[str, sqlite3.Row] = {}
    for row in db.execute("SELECT * FROM decision_memory WHERE status = 'active'"):
        ttl = parse_timestamp(row["ttl_until"])
        if ttl and ttl < now:
            continue
        result[row["content_key"]] = row
    return result


def decision_memory_summary(db: sqlite3.Connection) -> dict[str, Any]:
    """Active (un-expired) muted items, for the cockpit's transparent 'muted' control."""
    items: list[dict[str, Any]] = []
    now = datetime.now(APP_TIMEZONE)
    for row in db.execute("SELECT * FROM decision_memory WHERE status = 'active' ORDER BY updated_at DESC"):
        ttl = parse_timestamp(row["ttl_until"])
        if ttl and ttl < now:
            continue
        items.append({
            "contentKey": row["content_key"],
            "actionType": row["action_type"],
            "subject": row["subject"],
            "sender": row["sender"],
            "decision": row["decision"],
            "updatedAt": row["updated_at"],
            "ttlUntil": row["ttl_until"],
        })
    return {"count": len(items), "items": items}


def restore_muted_items(db: sqlite3.Connection, content_keys: set[str]) -> int:
    """Bring un-muted items back into the Approval inbox IMMEDIATELY, instead of waiting for a sweep
    that may never re-detect them. Restores ONLY items whose content_key is in `content_keys` (the
    exact set being un-muted) from whatever record exists for each:
      1) a re-activated suppressed inbox_signal (the rich record left when a sweep re-encountered it), and
      2) the original rejected/deferred/superseded approval, flipped back to 'pending'.
    Callers MUST clear the matching decision_memory rows BEFORE calling this so restored cards are not
    muted again. Strictly scoped by `content_keys` so 'Clear all' never resurrects every item ever
    dismissed — only the items currently muted."""
    if not content_keys:
        return 0
    keys = set(content_keys)
    restored = 0
    # 1) Rebuild cards from suppressed inbox signals (re-running the normal upsert path; decision
    #    memory is already cleared, so they are no longer muted and become pending approvals again).
    raws: list[dict[str, Any]] = []
    for row in db.execute("SELECT details_json, subject, sender FROM inbox_signals WHERE status = 'suppressed'"):
        try:
            raw = json.loads(row["details_json"] or "{}")
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        atype = review_signal_action_type(raw)
        key = approval_content_key(atype, str(row["subject"] or ""), str(row["sender"] or ""))
        if key not in keys:
            continue
        raws.append(raw)
    if raws:
        upsert_inbox_signals(db, raws, reconcile=False)
        restored += len(raws)
    # 2) Flip the original dismissed approval(s) back to pending (covers items never re-swept, so no
    #    suppressed signal exists). dedupe below collapses any twins (rejected + superseded) into one.
    for ap in db.execute(
        "SELECT id, action_type, details_json FROM approvals "
        "WHERE status IN ('rejected','deferred','superseded') AND action_type IN ('email','teams')"
    ).fetchall():
        try:
            d = json.loads(ap["details_json"] or "{}")
        except Exception:
            d = {}
        subj = str(d.get("subject") or "").strip()
        sndr = str(d.get("sender") or d.get("from") or "").strip()
        key = approval_content_key(ap["action_type"], subj, sndr)
        if key not in keys:
            continue
        db.execute("UPDATE approvals SET status = 'pending', updated_at = ? WHERE id = ?", (utc_now(), ap["id"]))
        restored += 1
    if restored:
        dedupe_pending_by_content(db, {"email", "teams"})
        touch_version(db)
    return restored


def build_guardrails(db: sqlite3.Connection) -> dict[str, Any]:
    """Delegation ledger: each employee's level + what's been auto-done under the trust you granted."""
    # Outward sends the team executed itself (autonomous + approved-send paths).
    autonomous_outward = db.execute(
        "SELECT COUNT(*) FROM jobs WHERE type = 'send-draft' AND status IN ('completed','done')"
    ).fetchone()[0]
    auto_events = db.execute(
        "SELECT COUNT(*) FROM events WHERE summary LIKE '%· autonomous%'"
    ).fetchone()[0]
    rsvp_sends = db.execute(
        "SELECT COUNT(*) FROM jobs WHERE type = 'calendar-rsvp' AND status IN ('completed','done')"
    ).fetchone()[0]

    levels = []
    counts = {"draft": 0, "assist": 0, "autonomous": 0}
    for row in db.execute("SELECT name, trust_level, enabled FROM employees ORDER BY rowid"):
        lvl = row["trust_level"]
        counts[lvl] = counts.get(lvl, 0) + 1
        levels.append({
            "name": row["name"],
            "level": lvl,
            "mode": employee_mode(row["name"]),
            "enabled": bool(row["enabled"]),
        })
    muted = db.execute("SELECT COUNT(*) FROM decision_memory WHERE status = 'active'").fetchone()[0]
    paused = [r[0] for r in db.execute("SELECT name FROM employees WHERE enabled = 0 ORDER BY rowid")]
    adjustable_at_autonomous = [
        r["name"] for r in db.execute("SELECT name, trust_level FROM employees")
        if employee_mode(r["name"]) == "adjustable" and r["trust_level"] == "autonomous"
    ]
    return {
        "cardinalRule": CARDINAL_OUTPUT_RULE,
        "policy": POLICY,
        "levels": levels,
        "levelCounts": counts,
        "audit": {
            "autonomousActions": auto_events,
            "outwardSends": autonomous_outward + rsvp_sends,
            "mutedByMemory": muted,
            "classifiedAlwaysPauses": True,
        },
        "pausedEmployees": paused,
        "adjustableAtAutonomous": adjustable_at_autonomous,
        "retention": "Preserve-forever — approval, job, ledger, and chat history is never deleted.",
        "sensitivity": "Confidential / Highly-Confidential external sends always pause for you, at every level. Unknown sensitivity is treated as classified.",
    }


def build_skill_usage(db: sqlite3.Connection) -> dict[str, Any]:
    """Per-employee skill usage for the architecture capability map, derived from REAL completed jobs.

    The capability map shows, per employee, the share of that employee's completed work attributed to
    each of their skills. Earlier versions depended on the worker LLM stamping jobs.skill, which almost
    never happened (so every badge showed "—"). This version is deterministic and always populates:
    it uses each job's real owner (jobs.employee) and maps the job to one representative skill from the
    job type (preferring an explicit jobs.skill stamp when present and valid for that owner). Percentages
    are within-employee (their skills sum to ~100%); a skill with no attributed work shows "—"."""
    # Skill-links that actually carry a badge on each capability card (must match data-skill in
    # static/architecture.html). Only these are valid attribution targets per employee.
    CARD_SKILLS: dict[str, list[str]] = {
        "Major": ["daily-flow-team", "app-api"],
        "Riley": ["workiq-email", "daily-flow-team"],
        "Mina": ["workiq-calendar", "daily-flow-team"],
        "Reese": ["web-research", "workiq-people"],
        "Tilly": ["workiq-calendar", "daily-flow-team"],
        "Dash": ["app-api", "daily-flow-team"],
        "Drew": ["docx", "pptx", "xlsx", "excalidraw"],
        "Logan": ["web-artifacts", "app-api"],
        "Quinn": ["content-pass", "daily-flow-team"],
        "Casey": ["knowledge-graph", "daily-flow-team"],
    }
    # (owner, job-type) -> representative skill on that owner's card.
    TYPE_SKILL: dict[tuple[str, str], str] = {
        ("Major", "manual-signal-sweep"): "daily-flow-team",
        ("Major", "employee-work"): "daily-flow-team",
        ("Major", "send-draft"): "daily-flow-team",
        ("Major", "chat-job"): "daily-flow-team",
        ("Mina", "calendar-rsvp"): "workiq-calendar",
        ("Mina", "email-action"): "workiq-calendar",
        ("Mina", "meeting-prep"): "workiq-calendar",
        ("Mina", "employee-work"): "workiq-calendar",
        ("Riley", "email-action"): "workiq-email",
        ("Riley", "send-draft"): "workiq-email",
        ("Riley", "teams-action"): "workiq-email",
        ("Riley", "employee-work"): "workiq-email",
        ("Reese", "employee-work"): "web-research",
        ("Tilly", "employee-work"): "workiq-calendar",
        ("Tilly", "calendar-rsvp"): "workiq-calendar",
        ("Drew", "employee-work"): "docx",
        ("Logan", "impact-highlight"): "web-artifacts",
        ("Logan", "employee-work"): "web-artifacts",
    }

    def skill_for(owner: str, jtype: str, stamped: str) -> str | None:
        valid = CARD_SKILLS.get(owner)
        if not valid:
            return None  # unknown/removed employee — no card to badge
        if stamped and stamped in valid:
            return stamped  # precise worker stamp wins when it fits the card
        mapped = TYPE_SKILL.get((owner, jtype))
        if mapped:
            return mapped
        return valid[0]  # fall back to the owner's primary skill so real work still counts

    by_emp: dict[str, dict[str, int]] = {}
    total = 0
    for row in db.execute(
        "SELECT employee, type, skill, COUNT(*) n FROM jobs "
        "WHERE status IN ('completed','done') GROUP BY employee, type, skill"
    ):
        owner = row["employee"]
        sk = skill_for(owner, row["type"] or "", (row["skill"] or "").strip())
        if not sk:
            continue
        by_emp.setdefault(owner, {})
        by_emp[owner][sk] = by_emp[owner].get(sk, 0) + row["n"]
        total += row["n"]
    usage: dict[str, Any] = {}
    for emp, skills in by_emp.items():
        emp_total = sum(skills.values()) or 1
        usage[emp] = {sk: {"count": n, "pct": round(100 * n / emp_total)} for sk, n in skills.items()}
    return {"byEmployee": usage, "totalStamped": total}


ADOPTION_CATEGORIES = {"adoption", "enablement", "mentoring", "ripple"}
# High-precision adoption signals only. Noisy tokens that produced false positives
# ("colleague", "demand", "shared the", "across microsoft", "socializ") were removed;
# weak summary-only matches are additionally gated on having a real audience below.
ADOPTION_KEYWORDS = (
    "adopt", "enablement", "mentor", "workshop", "onboard", "replicat",
    "how-to", "garage", "colleague", "shared scout", "shared the dream team", "build their own",
)
SESSION_KEYWORDS = ("workshop", "session", "demo", "briefing", "1:1", "sync", "working session", "how-to", "training")


def _kw_hit(text: str, keywords: tuple[str, ...] = ADOPTION_KEYWORDS) -> bool:
    """Word-boundary-ish keyword match to avoid mid-word noise."""
    return any(re.search(r"(?<![a-z])" + re.escape(k), text) for k in keywords)


def looks_like_adoption(entry: dict[str, Any]) -> bool:
    """An entry counts as adoption/ripple when it is tagged as such, OR a strong keyword
    appears in the TITLE, OR a keyword appears in the summary AND the entry names a real
    audience (a customer/org or people). The audience gate filters internal-ops noise."""
    cat = str(entry.get("category") or "").strip().lower()
    src = str(entry.get("source_type") or "").strip().lower()
    if cat in ADOPTION_CATEGORIES or src in ADOPTION_CATEGORIES:
        return True
    title = str(entry.get("title") or "").lower()
    if _kw_hit(title):
        return True
    summary = str(entry.get("summary") or "").lower()
    has_audience = bool(str(entry.get("customer") or "").strip()) or bool(decode_json_list(entry.get("people_json")))
    return has_audience and _kw_hit(summary)


def _canon_org(name: str) -> tuple[str, str]:
    """Return (dedupe_key, display) for a customer/org, collapsing near-duplicates such as
    'DXC' vs 'DXC Technology' and 'Microsoft Garage' vs 'Microsoft Garage community'."""
    raw = str(name or "").strip()
    if not raw:
        return "", ""
    base = re.sub(r"\(.*?\)", "", raw).strip()  # drop parentheticals, e.g. Lumen (CenturyLink)
    low = base.lower()
    if low.startswith("internal") or "microsoft field" in low or "field/community" in low:
        return "internal-field", "Internal (Microsoft field/community)"
    display = re.sub(r"\b(technology|technologies|community|corporation|corp|inc|llc|ltd|company)\b", "", base, flags=re.IGNORECASE)
    display = re.sub(r"\s+", " ", display).strip(" ,&-")
    if not display:
        display = base
    return display.lower(), display


def _split_people(raw: str) -> list[str]:
    """Split a possibly-jammed people string (a few names in one field) into individual names."""
    text = re.sub(r"\(.*?\)", "", str(raw or ""))  # drop role parentheticals
    parts = re.split(r"[;,/\n]| and | & ", text)
    out: list[str] = []
    for part in parts:
        name = re.sub(r"\s+", " ", part).strip(" .")
        if len(name) >= 2 and not name.isdigit():
            out.append(name)
    return out


def _first_evidence_link(value: Any) -> str:
    """Best-effort extract a single URL from a work-ledger evidence field (string/obj/list/JSON)."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw if raw.startswith("http") else ""
    if isinstance(parsed, str):
        return parsed if parsed.startswith("http") else ""
    if isinstance(parsed, dict):
        return str(parsed.get("href") or parsed.get("url") or "")
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, str):
            return first if first.startswith("http") else ""
        if isinstance(first, dict):
            return str(first.get("href") or first.get("url") or "")
    return ""


def _iso_week_key(value: str | None) -> str:
    dt = parse_timestamp(value)
    if not dt:
        return ""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def build_ripple(db: sqlite3.Connection) -> dict[str, Any]:
    """Adoption Ripple — derive cross-org influence from the work ledger, with the underlying
    evidence (orgs, people, dated items) returned so the numbers can show their receipts."""
    entries = [dict(r) for r in db.execute("SELECT * FROM work_ledger_entries WHERE status = 'active' ORDER BY occurred_at DESC")]
    adoption = [e for e in entries if looks_like_adoption(e)]
    people_disp: dict[str, str] = {}   # lower-key -> display name (deduped)
    orgs_disp: dict[str, str] = {}     # canonical-key -> display org (deduped)
    sessions = 0
    this_week = 0
    week_ago = datetime.now(APP_TIMEZONE) - timedelta(days=7)
    items: list[dict[str, Any]] = []
    for e in adoption:
        entry_people: list[str] = []
        for raw in decode_json_list(e.get("people_json")):
            for name in _split_people(raw):
                key = name.lower()
                people_disp.setdefault(key, name)
                entry_people.append(people_disp[key])
        org_key, org_disp = _canon_org(e.get("customer"))
        if org_key:
            orgs_disp.setdefault(org_key, org_disp)
        text = f"{e.get('title','')} {e.get('summary','')}".lower()
        is_session = _kw_hit(text, SESSION_KEYWORDS)
        if is_session:
            sessions += 1
        occ = parse_timestamp(e.get("occurred_at") or e.get("created_at"))
        fresh = bool(occ and occ >= week_ago)
        if fresh:
            this_week += 1
        items.append({
            "title": e.get("title", ""),
            "summary": e.get("summary", ""),
            "occurredAt": e.get("occurred_at") or e.get("created_at"),
            "date": occ.astimezone(APP_TIMEZONE).date().isoformat() if occ else "",
            "org": org_disp,
            "people": list(dict.fromkeys(entry_people)),
            "session": is_session,
            "fresh": fresh,
            "impactLevel": e.get("impact_level", ""),
            "link": _first_evidence_link(e.get("evidence_json")),
        })
    return {
        "adoptionEntries": len(adoption),
        "peopleInfluenced": len(people_disp),
        "orgsTouched": len(orgs_disp),
        "sessions": sessions,
        "thisWeek": this_week,
        "orgsList": sorted(orgs_disp.values(), key=str.lower),
        "peopleList": sorted(people_disp.values(), key=str.lower),
        "items": items[:500],
    }


def get_career_profile(db: sqlite3.Connection) -> dict[str, Any]:
    """Private, single-row career profile. Empty on a fresh install (so shared copies behave identically)."""
    row = db.execute(
        "SELECT current_role, target_role, review_rubric, updated_at FROM career_profile WHERE id = 1"
    ).fetchone()
    if not row:
        return {"currentRole": "", "targetRole": "", "reviewRubric": "", "updatedAt": ""}
    return {
        "currentRole": row[0] or "",
        "targetRole": row[1] or "",
        "reviewRubric": row[2] or "",
        "updatedAt": row[3] or "",
    }


def save_career_profile(db: sqlite3.Connection, current_role: str, target_role: str, review_rubric: str) -> dict[str, Any]:
    db.execute(
        "INSERT INTO career_profile(id, current_role, target_role, review_rubric, updated_at) VALUES(1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET current_role=excluded.current_role, target_role=excluded.target_role, "
        "review_rubric=excluded.review_rubric, updated_at=excluded.updated_at",
        ((current_role or "").strip(), (target_role or "").strip(), (review_rubric or "").strip(), utc_now()),
    )
    # Log that it changed, never the content (the content is private).
    add_event(db, "You", "Updated your private career profile",
              "Current/target role and review rubric saved locally. This data stays on this machine and is never included in a shared package.")
    touch_version(db)
    return get_career_profile(db)


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx_text(data: bytes) -> str:
    """Extract paragraph text from a .docx using only the standard library (a .docx is a zip
    of XML). No third-party dependency, so this works on any machine the app is shared to."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            document_xml = zf.read("word/document.xml")
    except Exception as exc:
        raise ValueError("That .docx could not be read. Try pasting the text instead.") from exc
    try:
        root = ET.fromstring(document_xml)
    except Exception as exc:
        raise ValueError("That .docx could not be parsed. Try pasting the text instead.") from exc
    paragraphs: list[str] = []
    for para in root.iter(ns + "p"):
        runs = [node.text for node in para.iter(ns + "t") if node.text]
        paragraphs.append("".join(runs).strip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(paragraphs)).strip()
    return text


def extract_uploaded_text(filename: str, data: bytes) -> str:
    """Turn an uploaded .txt/.md/.docx into plain text. Other types are rejected with a clear message."""
    name = (filename or "").lower().strip()
    if name.endswith(".docx"):
        return _extract_docx_text(data)
    if name.endswith((".txt", ".md", ".markdown", ".text")):
        return _decode_text(data).strip()
    if name.endswith((".doc", ".pdf", ".rtf", ".pages")):
        raise ValueError("Only .txt, .md, and .docx uploads are supported. Open the file and paste its text instead.")
    # Unknown extension: accept if it decodes as text, otherwise reject.
    text = _decode_text(data)
    if "\x00" in text:
        raise ValueError("Only .txt, .md, and .docx uploads are supported. Paste the text instead.")
    return text.strip()


COVERAGE_STOPWORDS = {
    "that", "this", "with", "your", "their", "they", "them", "from", "into", "will", "have",
    "been", "when", "what", "which", "while", "such", "more", "than", "also", "each", "other",
    "team", "able", "across", "using", "used", "make", "made", "help", "ensure", "drive",
    "deliver", "delivers", "including", "through", "within", "around", "where", "these", "those",
    "based", "level", "role", "year", "years", "work", "works", "working", "must", "should",
    "could", "would", "about", "over", "under", "between", "both", "well", "very", "high",
    "strong", "good", "great", "consistently", "demonstrate", "demonstrates", "ability",
}


def build_coverage(db: sqlite3.Connection) -> dict[str, Any]:
    """Heuristic 'where am I strong vs light' map: parse the user's pasted review rubric (or
    target role) into criteria, then count how much captured work touches each. A focus aid
    grounded in their own rubric + real ledger data — explicitly NOT a score."""
    profile = get_career_profile(db)
    rubric = (profile.get("reviewRubric") or "").strip()
    target = (profile.get("targetRole") or "").strip()
    if not rubric and not target:
        return {"available": False, "criteria": [], "basedOn": ""}
    source = rubric if rubric else target
    based_on = "your performance rubric" if rubric else "your target role"
    raw_lines = re.split(r"[\n;]|(?:^|\s)[-*•·]\s+", source)
    criteria: list[str] = []
    for line in raw_lines:
        cleaned = re.sub(r"\s+", " ", line).strip(" -*:•·.")
        if len(cleaned.split()) >= 3 and cleaned not in criteria:
            criteria.append(cleaned)
    criteria = criteria[:12]
    if not criteria:
        return {"available": False, "criteria": [], "basedOn": based_on}
    entries = [dict(r) for r in db.execute("SELECT * FROM work_ledger_entries WHERE status = 'active'")]
    blobs: list[tuple[str, bool, str]] = []
    for e in entries:
        is_impact = (
            str(e.get("impact_level", "")) in ("highlight", "significant")
            or bool(str(e.get("impact_summary") or "").strip())
            or "impact" in str(e.get("category") or "")
        )
        blob = f"{e.get('title','')} {e.get('summary','')} {e.get('impact_summary','')}".lower()
        blobs.append((blob, is_impact, str(e.get("title") or "")))
    out: list[dict[str, Any]] = []
    for crit in criteria:
        keywords = [w for w in re.findall(r"[a-z]{4,}", crit.lower()) if w not in COVERAGE_STOPWORDS]
        keywords = list(dict.fromkeys(keywords))[:8]
        matches = 0
        impact_matches = 0
        examples: list[str] = []
        for blob, is_impact, title in blobs:
            if keywords and any(k in blob for k in keywords):
                matches += 1
                if is_impact:
                    impact_matches += 1
                if title and len(examples) < 3:
                    examples.append(title)
        level = "strong" if (matches >= 3 or impact_matches >= 1) else ("light" if matches >= 1 else "none")
        out.append({
            "criterion": crit,
            "matches": matches,
            "impactMatches": impact_matches,
            "level": level,
            "examples": examples,
        })
    return {"available": True, "criteria": out, "basedOn": based_on}


def career_profile_block(db: sqlite3.Connection) -> str:
    """Append the private career context to the sweep prompt so capture is biased toward the
    evidence the user's review rewards. Returns '' when empty, so shared copies are unaffected."""
    profile = get_career_profile(db)
    current = (profile.get("currentRole") or "").strip()
    target = (profile.get("targetRole") or "").strip()
    rubric = (profile.get("reviewRubric") or "").strip()
    if not (current or target or rubric):
        return ""
    clip = lambda s: s[:4000]
    lines = [
        "",
        "CAREER CONTEXT (PRIVATE — use ONLY to decide what is worth capturing in the work/impact "
        "ledger and how to frame it; never share externally, never fabricate or inflate work):",
        f"- Current role / responsibilities: {clip(current) or 'not provided'}",
        f"- Target / future role the user is growing into: {clip(target) or 'not provided'}",
        f"- How the user's company defines impact, the metrics measured, and the goals they are rated against: {clip(rubric) or 'not provided'}",
        "When capturing body-of-work and impact (instructions 12-13), bias toward REAL evidence that "
        "maps to this rubric and target role: prefer outcomes that help meet or exceed these "
        "expectations, and when logging impact name the specific goal or metric it advances. Only "
        "capture work that actually happened.",
    ]
    return "\n".join(lines)


def _norm_cadence_label(text: str) -> str:
    out = re.sub(r"[0-9]", "", str(text or "").lower())
    out = re.sub(r"[^a-z ]", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def build_cadences(db: sqlite3.Connection) -> dict[str, Any]:
    """Cadences shipped, not hours saved — recurring delivered outputs grouped by week."""
    entries = [dict(r) for r in db.execute("SELECT * FROM work_ledger_entries WHERE status = 'active'")]
    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        category = str(e.get("category") or "").strip()
        key = category.lower() or _norm_cadence_label(e.get("title"))[:40]
        wk = _iso_week_key(e.get("occurred_at") or e.get("created_at"))
        if not key or not wk:
            continue
        g = groups.setdefault(key, {"label": category or str(e.get("title") or "").strip(), "weeks": set(), "count": 0})
        g["weeks"].add(wk)
        g["count"] += 1
    this_wk = _iso_week_key(utc_now())
    recurring = []
    new_this_week = []
    for g in groups.values():
        if len(g["weeks"]) >= 2:
            recurring.append({"label": g["label"], "weeks": len(g["weeks"]), "count": g["count"]})
        elif this_wk in g["weeks"]:
            new_this_week.append({"label": g["label"], "count": g["count"]})
    recurring.sort(key=lambda x: (x["weeks"], x["count"]), reverse=True)
    return {"recurring": recurring[:12], "newThisWeek": new_this_week[:12]}


def dedupe_pending_by_content(db: sqlite3.Connection, action_types: set[str] | None = None) -> int:
    """Keep only the newest pending card per (type, content key); supersede older twins.

    This catches duplicates that the sourceId-based stable id cannot: cards for the same
    item created by different sweeps with inconsistent/garbage sourceIds or reworded
    subjects. History is preserved (status 'superseded'), and an exact source re-post can
    still reactivate a card via the INSERT ... ON CONFLICT clauses.
    """
    types = (set(action_types) & DEDUPE_TYPES) if action_types else set(DEDUPE_TYPES)
    if not types:
        return 0
    placeholders = ",".join("?" for _ in types)
    rows = db.execute(
        f"SELECT * FROM approvals WHERE status = 'pending' AND action_type IN ({placeholders}) "
        "ORDER BY updated_at DESC, created_at DESC",
        tuple(sorted(types)),
    ).fetchall()
    seen: set[str] = set()
    superseded = 0
    now = utc_now()
    for row in rows:  # newest first -> first occurrence of a key is kept
        details: dict[str, Any] = {}
        if row["details_json"]:
            try:
                parsed = json.loads(row["details_json"])
                if isinstance(parsed, dict):
                    details = parsed
            except (json.JSONDecodeError, TypeError):
                details = {}
        subject = str(details.get("subject") or row["title"]).strip()
        sender = signal_sender_text(details)
        key = approval_content_key(row["action_type"], subject, sender)
        if key in seen:
            db.execute(
                "UPDATE approvals SET status = 'superseded', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            superseded += 1
        else:
            seen.add(key)
    if superseded:
        touch_version(db)
    return superseded


def create_calendar_card_from_signal(db: sqlite3.Connection, raw: dict[str, Any], now: str) -> str:
    """Create/merge a calendar approval card from an Inbox signal that was detected as a
    meeting/calendar message (rather than a plain email). Uses the same calendar id scheme
    and details shape as the live invite scan, so it merges by sourceId with any card the
    /api/inbox-invites pipeline posts for the same invite."""
    subject = str(raw.get("subject") or "").strip() or "Calendar invite"
    sender = signal_sender_text(raw)
    organizer = sender or str(raw.get("from") or "Unknown organizer").strip()
    raw_when = str(raw.get("meetingTime") or raw.get("when") or raw.get("start") or "").strip()
    when_display = format_invite_time(raw_when) if raw_when else "Time not captured from invite metadata"
    source_id = signal_source_id(raw)
    summary = str(raw.get("summary") or "").strip()
    recommendation = str(raw.get("recommendation") or "Review this calendar invite and choose an RSVP.").strip()
    approval_id = stable_calendar_approval_id(subject, organizer, raw_when, source_id)
    details = {
        "type": "calendar-invite",
        "about": subject,
        "meetingTime": when_display,
        "rawMeetingTime": raw_when,
        "organizer": organizer,
        "location": str(raw.get("location") or "Location not captured from invite metadata"),
        "currentStatus": "Detected as a calendar/meeting message in the Inbox.",
        "conflictSummary": str(raw.get("conflictSummary") or "Not checked yet"),
        "recommendation": recommendation,
        "whyApproval": "This Inbox item is a meeting/calendar invite, so it is an RSVP decision rather than an email reply.",
        "sourceId": source_id,
        "summary": summary,
        "reclassifiedFrom": str(raw.get("sourceType") or "email"),
    }
    preview = (
        f"What it is: {subject}\n"
        f"When: {when_display}\n"
        f"Organizer: {organizer}\n"
        + (f"Summary: {summary}\n" if summary else "")
        + f"Recommendation: {recommendation}"
    )
    db.execute(
        """
        INSERT INTO approvals(id, created_at, updated_at, employee, action_type, risk, title, preview, destination, status, details_json)
        VALUES(?, ?, ?, 'Mina', 'calendar', 'medium', ?, ?, 'Outlook RSVP decision', 'pending', ?)
        ON CONFLICT(id) DO UPDATE SET
            updated_at=excluded.updated_at,
            employee=excluded.employee,
            action_type=excluded.action_type,
            risk=excluded.risk,
            title=excluded.title,
            preview=excluded.preview,
            destination=excluded.destination,
            status=CASE WHEN approvals.status = 'pending' OR approvals.status = 'superseded' THEN 'pending' ELSE approvals.status END,
            details_json=excluded.details_json
        """,
        (approval_id, now, now, f"Inbox calendar decision needed: {subject}", preview, json.dumps(details)),
    )
    return approval_id


def upsert_inbox_signals(
    db: sqlite3.Connection,
    signals: list[Any],
    reconcile: bool = False,
    covered_types: list[str] | None = None,
    resolved_ids: list[str] | None = None,
    complete_snapshot: bool = False,
) -> dict[str, Any]:
    if not isinstance(signals, list):
        raise ValueError("signals must be an array")
    now = utc_now()
    upserted = 0
    reclassified = 0
    suppressed = 0
    dismissed = active_decision_memory(db)
    live_ids: set[str] = set()
    live_source_ids: set[str] = set()
    present_types: set[str] = set()
    # WorkIQ misroute check (Evidence Review) needs the user's own defined role/responsibilities;
    # fetched once per call rather than per-signal since it rarely changes mid-sweep.
    current_role = str(get_career_profile(db).get("currentRole") or "").strip()
    for raw in signals:
        if not isinstance(raw, dict):
            raise ValueError("each inbox signal must be an object")
        subject = str(raw.get("subject") or "").strip()
        summary = str(raw.get("summary") or raw.get("preview") or "").strip()
        if not subject or not summary:
            raise ValueError("each inbox signal requires subject and summary")
        signal_id = stable_inbox_signal_id(raw)
        sender_value = raw.get("sender") or raw.get("from") or ""
        if isinstance(sender_value, dict):
            sender_value = sender_value.get("emailAddress", {}).get("address") or sender_value.get("emailAddress", {}).get("name") or ""
        source_id = signal_source_id(raw)
        action_type = review_signal_action_type(raw)
        if action_type == "calendar":
            # Detected as a meeting/calendar invite mislabeled as email/teams -> route to the
            # calendar pipeline so it lands in the Calendar group with RSVP semantics and
            # merges (by sourceId) with any live invite-scan card for the same invite.
            create_calendar_card_from_signal(db, raw, now)
            reclassified += 1
            continue
        # Single choke point every non-email/calendar review signal passes through: sanitize here
        # so the cleanup covers the dashboard preview AND every outbound job instruction built from
        # these same fields later (create_review_follow_up_job re-reads summary/recommendation/
        # evidence text back out of details_json; classify_attachment_review/build_evidence_review
        # below read straight from `raw`). This applies regardless of action_type/source -- not just
        # action_type == "teams" -- since a Teams-sourced item can be classified as meeting-prep,
        # commitment, attachment-review, etc. Email is intentionally excluded (untouched).
        raw = sanitize_review_signal_html(raw, action_type)
        summary = str(raw.get("summary") or raw.get("preview") or "").strip()
        if source_id:
            live_source_ids.add(source_id.lower())
        live_ids.add(stable_inbox_signal_id(raw).replace("inbox_", "approval_review_"))
        present_types.add(action_type)
        owner, title_prefix, destination = review_signal_metadata(action_type)
        priority = str(raw.get("priority") or "normal").strip().lower()
        risk = "high" if priority in {"urgent", "high"} else "medium"
        signal_type = str(raw.get("signalType") or raw.get("type") or "action").strip()
        received_at = str(raw.get("receivedAt") or raw.get("receivedDateTime") or raw.get("date") or "").strip()
        recommendation = str(raw.get("recommendation") or "").strip()
        needs_action: bool | None = None
        evidence: dict[str, Any] = {}
        if action_type == "attachment-review":
            needs_action, recommendation = classify_attachment_review(raw)
            evidence = build_evidence_review(raw, needs_action, recommendation, current_role)
        sender_text = str(sender_value).strip()
        details = {
            **raw,
            "type": f"{action_type}-review-signal",
            "sourceType": action_type,
            "sourceId": source_id,
            "subject": subject,
            "sender": sender_text,
            "receivedAt": received_at,
            "signalType": signal_type,
            "priority": priority,
            "summary": summary,
            "recommendation": recommendation,
        }
        if action_type == "attachment-review":
            details["attachmentNames"] = signal_attachment_names(raw)
            details["documentLink"] = signal_document_link(raw)
            details["needsAction"] = needs_action
            details["highValue"] = looks_like_high_value_attachment(raw)
            details["evidence"] = evidence
        preview_parts = [
            f"What it is: {subject}",
            f"From: {sender_text or 'Unknown sender'}",
            f"When: {format_invite_time(received_at) if received_at else 'Time not captured'}",
            f"Signal: {signal_type}",
            f"Summary: {summary}",
        ]
        if action_type == "attachment-review":
            rec = evidence.get("recommendation", {})
            verdict_label = {"act": "🔔 ACT", "fyi": "ℹ️ FYI", "review_required": "🟡 REVIEW REQUIRED"}.get(rec.get("verdict"), "")
            misroute = evidence.get("misroute") or {}
            preview_parts.extend(part for part in [
                f"Attachment(s): {', '.join(evidence.get('attachmentNames') or []) or (evidence.get('documentLink') or 'Linked document')}",
                f"Thread/latest message: {evidence.get('latestMessageDelta', '')}" if evidence.get("latestMessageDelta") else "",
                f"Explicit ask: {evidence.get('explicitAsk', '')}" if evidence.get("explicitAsk") else "Explicit ask: none surfaced — Quinn will confirm on review.",
                f"Importance to me: {evidence.get('importanceToMe', '')} · Importance to them: {evidence.get('importanceToThem', '')}",
                f"Urgency: {evidence.get('urgency', '')} · Service impact: {evidence.get('serviceImpact', '')}",
                f"Attachment analysis: {evidence.get('attachmentAnalysis', '')}",
                f"ROI deck: investment {evidence['roiDeck'].get('totalInvestment') or 'n/a'}, return {evidence['roiDeck'].get('expectedReturn') or 'n/a'}, payback {evidence['roiDeck'].get('paybackPeriod') or 'n/a'}" if evidence.get("roiDeck") else "",
                f"WorkIQ scope check: outside your role — suggested owner {misroute.get('suggestedOwner', '')} ({misroute.get('reason', '')})" if misroute.get("isMisroute") else "",
                f"Recommendation: {verdict_label} ({rec.get('subtype', '')}) — {rec.get('nextBestAction', '')}",
            ] if part)
        elif recommendation:
            preview_parts.append(f"Recommendation: {recommendation}")
        preview = "\n".join(part for part in preview_parts if part)
        # Decision memory: if the user already rejected/deferred this same logical item recently,
        # mute it instead of re-surfacing a new approval card. Transparent + reversible (Manage muted).
        if action_type in DECISION_MEMORY_TYPES:
            memo = dismissed.get(approval_content_key(action_type, subject, sender_text))
            if memo is not None:
                db.execute(
                    """
                    INSERT INTO inbox_signals(
                        id, created_at, updated_at, source_id, subject, sender, received_at, importance,
                        is_read, has_attachments, signal_type, priority, summary, recommendation, status, details_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'suppressed', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        updated_at=excluded.updated_at, status='suppressed', details_json=excluded.details_json
                    """,
                    (
                        signal_id, now, now, source_id, subject, sender_text, received_at,
                        str(raw.get("importance") or "").strip(), 1 if raw.get("isRead") else 0,
                        1 if raw.get("hasAttachments") else 0, signal_type, priority, summary, recommendation,
                        json.dumps(raw),
                    ),
                )
                add_event(
                    db, "Major",
                    f"Decision memory: muted a re-surfaced {action_type} you already {memo['decision']}: {subject}",
                    "Hidden from the Approval inbox because you dismissed it recently. Manage muted items from the cockpit.",
                )
                suppressed += 1
                continue
        db.execute(
            """
            INSERT INTO inbox_signals(
                id, created_at, updated_at, source_id, subject, sender, received_at, importance,
                is_read, has_attachments, signal_type, priority, summary, recommendation, status, details_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                subject=excluded.subject,
                sender=excluded.sender,
                received_at=excluded.received_at,
                importance=excluded.importance,
                is_read=excluded.is_read,
                has_attachments=excluded.has_attachments,
                signal_type=excluded.signal_type,
                priority=excluded.priority,
                summary=excluded.summary,
                recommendation=excluded.recommendation,
                status='active',
                details_json=excluded.details_json
            """,
            (
                signal_id,
                now,
                now,
                source_id,
                subject,
                str(sender_value).strip(),
                str(raw.get("receivedAt") or raw.get("receivedDateTime") or raw.get("date") or "").strip(),
                str(raw.get("importance") or "").strip(),
                1 if raw.get("isRead") else 0,
                1 if raw.get("hasAttachments") else 0,
                str(raw.get("signalType") or raw.get("type") or "action").strip(),
                str(raw.get("priority") or "normal").strip().lower(),
                summary,
                recommendation,
                json.dumps(raw),
            ),
        )
        db.execute(
            """
            INSERT INTO approvals(id, created_at, updated_at, employee, action_type, risk, title, preview, destination, status, details_json, user_guidance, evidence_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, '', ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                employee=excluded.employee,
                action_type=excluded.action_type,
                risk=excluded.risk,
                title=excluded.title,
                preview=excluded.preview,
                destination=excluded.destination,
                status=CASE WHEN approvals.status = 'pending' OR approvals.status = 'superseded' THEN 'pending' ELSE approvals.status END,
                details_json=excluded.details_json,
                evidence_json=excluded.evidence_json
            """,
            (
                stable_inbox_signal_id(raw).replace("inbox_", "approval_review_"),
                now,
                now,
                owner,
                action_type,
                risk,
                f"{title_prefix}: {subject}",
                preview,
                destination,
                json.dumps(details),
                json.dumps(evidence),
            ),
        )
        upserted += 1
    retired = 0
    if reconcile:
        scope = {str(t).strip() for t in (covered_types or []) if str(t).strip()}
        if not scope:
            scope = set(present_types)
        resolved_set = {str(s).strip().lower() for s in (resolved_ids or []) if str(s).strip()}
        retired = reconcile_pending_approvals(
            db,
            scope,
            resolved_source_ids=resolved_set,
            live_source_ids=live_source_ids if complete_snapshot else None,
            complete_snapshot=complete_snapshot,
            protect_ids=live_ids,
        )
    summary = f"Review signal scan persisted {upserted} approval item(s)."
    if reclassified:
        summary += f" Reclassified {reclassified} meeting/calendar invite(s) out of email/Teams."
    if reconcile:
        summary += f" Retired {retired} review card(s) confirmed handled at the source."
    deduped = dedupe_pending_by_content(db, set(present_types) | (scope if reconcile else set()))
    if deduped:
        summary += f" Collapsed {deduped} duplicate card(s)."
    add_event(db, "Riley", summary)
    touch_version(db)
    return {"upserted": upserted, "reclassifiedCalendar": reclassified, "retiredStale": retired, "mutedByMemory": suppressed}


# Calendar invites carry four RSVP decisions instead of the generic approve/reject/defer used
# by every other approval type: accept, tentative, decline (all three send a real Outlook RSVP)
# and follow (no RSVP is sent; Mina just keeps watching the invite for changes — see
# create_follow_invite_job below). CALENDAR_RSVP_RESPONSE maps a decision straight to the RSVP
# verb Mina should execute.
CALENDAR_RSVP_RESPONSE = {"accept": "accept", "tentative": "tentative", "decline": "decline"}
CALENDAR_DECISIONS = {"accept", "tentative", "follow", "decline"}


def create_rsvp_job(db: sqlite3.Connection, approval: sqlite3.Row, decision: str, user_guidance: str) -> str:
    details = {}
    if approval["details_json"]:
        try:
            details = json.loads(approval["details_json"])
        except json.JSONDecodeError:
            details = {}
    response = CALENDAR_RSVP_RESPONSE[decision]
    title = approval["title"].removeprefix("Inbox calendar decision needed: ").strip() or approval["title"]
    job_id = new_id("job")
    now = utc_now()
    instructions = (
        f"User selected {decision} for this calendar approval. Execute the Outlook RSVP as {response}.\n\n"
        f"Meeting: {title}\n"
        f"When: {details.get('meetingTime') or 'See approval preview'}\n"
        f"Organizer: {details.get('organizer') or approval['employee']}\n"
        f"Location: {details.get('location') or approval['destination']}\n"
        f"Source Inbox message ID: {details.get('sourceId') or 'not captured'}\n"
        f"Conflict summary: {details.get('conflictSummary') or 'See approval preview'}\n"
        f"Recommendation shown to user: {details.get('recommendation') or 'See approval preview'}\n\n"
        "After executing the RSVP, report back through /api/jobs/{jobId} with status completed or blocked. "
        "Do not include private conflict details in any RSVP comment; use a blank or generic comment only. "
        "For accept RSVP jobs, delete the handled invite email from the Inbox after the RSVP succeeds because action has been taken."
    )
    if user_guidance:
        instructions += f"\n\nUser feedback for Major/Mina: {user_guidance}"
    db.execute(
        "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, instructions) "
        "VALUES(?, ?, ?, 'Mina', 'calendar-rsvp', ?, 'queued', 'urgent', 'approval-inbox', ?)",
        (job_id, now, now, f"{response.title()} RSVP: {title}", instructions),
    )
    add_event(db, "Mina", f"RSVP job queued: {response.title()} {title}", user_guidance)
    return job_id


def create_follow_invite_job(db: sqlite3.Connection, approval: sqlite3.Row, user_guidance: str) -> str:
    """The user chose Follow: they can't attend but want to keep tracking this invite rather than
    RSVP or remove it. Unlike Decline/Tentative, this never sends an RSVP; unlike the old Defer
    cleanup, it never deletes the source invite email either — Mina just watches it for
    reschedules/cancellations and flags anything that changes."""
    details = approval_details(approval)
    title = approval["title"].removeprefix("Inbox calendar decision needed: ").strip() or approval["title"]
    job_id = new_id("job")
    now = utc_now()
    instructions = (
        "The user selected Follow for this calendar invite: they can't attend but want to keep "
        "monitoring it. Do NOT send an RSVP and do NOT delete or modify the invite email or calendar event.\n\n"
        f"Meeting: {title}\n"
        f"When: {details.get('meetingTime') or 'See approval preview'}\n"
        f"Organizer: {details.get('organizer') or approval['employee']}\n"
        f"Location: {details.get('location') or approval['destination']}\n"
        f"Source Inbox invite message ID: {details.get('sourceId') or 'not captured'}\n"
        f"Conflict summary shown to user: {details.get('conflictSummary') or 'See approval preview'}\n\n"
        "Keep watching this invite for reschedules, cancellations, or new details, and notify the user if "
        "anything changes. Report completed or blocked via /api/jobs/{jobId}."
    )
    if user_guidance:
        instructions += f"\n\nUser feedback for Major/Mina: {user_guidance}"
    db.execute(
        "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, instructions) "
        "VALUES(?, ?, ?, 'Mina', 'calendar-follow', ?, 'queued', 'normal', 'approval-inbox', ?)",
        (job_id, now, now, f"Follow invite: {title}", instructions),
    )
    add_event(db, "Mina", f"Following invite (no RSVP): {title}", user_guidance)
    return job_id


CALENDAR_FRESHNESS_MESSAGES = {
    "missing": "This calendar invite is no longer in your Approval inbox, so no RSVP was sent.",
    "expired": "This meeting already ended, so no RSVP was sent.",
    "superseded": "This invite was already resolved outside the app (for example, you responded to it directly in Outlook), so no RSVP was sent.",
    "already-decided": "This invite was already decided — from another tab, a duplicate click, or a background sweep — so no additional RSVP was sent.",
}


def calendar_invite_freshness_check(db: sqlite3.Connection, approval_id: str) -> tuple[sqlite3.Row | None, str]:
    """Pre-execution freshness check for calendar approvals only. Re-fetches the approval
    (the local mirror of the underlying Outlook invite) immediately before a decision is acted
    on, so a card the user is looking at cannot be double-acted-on if it has since become
    non-actionable:

      * 'expired'   — expire_time_bound_approvals() is run first so a meeting whose end time has
                       just passed is caught right now, not on the next poll.
      * 'superseded' — a background Inbox sweep already confirmed the invite was handled outside
                        the app (reconcile_pending_approvals()) since the card was loaded.
      * 'already-decided' — a concurrent request already recorded a decision for this same
                             approval (duplicate click / another tab) since it was loaded.
      * 'missing'   — the approval row is gone entirely.

    Returns (fresh_row, "") when the invite is still pending and safe to act on, otherwise
    (row_or_None, reason) with reason one of the keys in CALENDAR_FRESHNESS_MESSAGES.
    """
    expire_time_bound_approvals(db)
    fresh = db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if not fresh:
        return None, "missing"
    if fresh["status"] == "pending":
        return fresh, ""
    if fresh["status"] == "expired":
        return fresh, "expired"
    if fresh["status"] == "superseded":
        return fresh, "superseded"
    return fresh, "already-decided"


def clean_subject(subject: str) -> str:
    return subject.replace("[EXTERNAL]", "").strip() or subject.strip() or "Inbox calendar invite"


def parse_iso_datetime(value: str) -> datetime | None:
    text = value.strip()
    if "T" not in text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if "." in text:
        prefix, suffix = text.split(".", 1)
        fractional = []
        rest = ""
        for char in suffix:
            if char.isdigit() and not rest:
                fractional.append(char)
            else:
                rest += char
        text = f"{prefix}.{''.join(fractional)[:6]}{rest}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_invite_time(value: str) -> str:
    dt = parse_iso_datetime(value)
    if not dt:
        return value
    if dt.tzinfo:
        dt = dt.astimezone(APP_TIMEZONE)
    hour = dt.strftime("%I").lstrip("0") or "12"
    # Use the actual resolved timezone's abbreviation (e.g. PST/PDT, CET, IST) rather than a
    # hardcoded "PT", so this reads correctly for machines/APP_TIMEZONE configs outside Pacific.
    tz_label = dt.strftime("%Z") or APP_TIMEZONE_NAME
    return f"{dt.strftime('%a %b')} {dt.day}, {dt.year}, {hour}:{dt.strftime('%M %p')} {tz_label}"


def invite_key(subject: str, organizer: str, when: str, source_id: str = "") -> str:
    source = source_id.strip().lower()
    if source:
        return f"source:{source}"
    return "|".join(part.strip().lower() for part in (subject, organizer, when))


def invite_match_keys(subject: str, organizer: str, when: str, source_id: str = "") -> set[str]:
    keys = {invite_key(subject, organizer, when, "")}
    if source_id.strip():
        keys.add(invite_key(subject, organizer, when, source_id))
    if subject.strip() and organizer.strip():
        keys.add(f"subject-organizer:{subject.strip().lower()}|{organizer.strip().lower()}")
    return keys


def stable_calendar_approval_id(subject: str, organizer: str, when: str, source_id: str = "") -> str:
    import hashlib

    digest = hashlib.sha256(invite_key(subject, organizer, when, source_id).encode("utf-8")).hexdigest()[:16]
    return f"approval_calendar_{digest}"


def handled_calendar_invite_keys(db: sqlite3.Connection) -> set[str]:
    keys: set[str] = set()
    # Includes the legacy approved/rejected/deferred values alongside the current
    # accept/tentative/follow/decline set so invites decided under the old scheme stay
    # recognized as handled.
    for row in db.execute(
        "SELECT title, details_json FROM approvals WHERE action_type = 'calendar' "
        "AND status IN ('accept', 'tentative', 'follow', 'decline', 'approved', 'rejected', 'deferred')"
    ):
        title = str(row["title"]).removeprefix("Inbox calendar decision needed: ").strip()
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        keys.update(invite_match_keys(
            title or str(details.get("about", "")),
            str(details.get("organizer", "")),
            str(details.get("rawMeetingTime") or details.get("meetingTime", "")),
            str(details.get("sourceId", "")),
        ))
    return keys


def approval_source_id(row: sqlite3.Row) -> str:
    """Durable source id (Graph message/event/chat id) stored in an approval's details."""
    raw = row["details_json"] if "details_json" in row.keys() else None
    if not raw:
        return ""
    try:
        details = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(details, dict):
        return ""
    return signal_source_id(details).lower()


def reconcile_pending_approvals(
    db: sqlite3.Connection,
    action_types: set[str],
    live_ids: set[str] | None = None,
    *,
    resolved_source_ids: set[str] | None = None,
    live_source_ids: set[str] | None = None,
    complete_snapshot: bool = False,
    protect_ids: set[str] | None = None,
) -> int:
    """Evidence-based retirement of pending approvals.

    A pending card is retired (status 'superseded', preserved in history) ONLY when there
    is positive evidence that its source was handled outside the app:

      * its durable sourceId is in `resolved_source_ids` (the sweep explicitly confirmed
        the email was deleted / Teams replied / meeting handled), OR
      * `complete_snapshot` is True (the sweep asserts it fully enumerated everything that
        is still live for these types). Under a complete snapshot, a pending card is retired
        when its sourceId is NOT in `live_source_ids`, INCLUDING cards that have no
        verifiable sourceId at all (they cannot be confirmed present, so an authoritative
        full enumeration treats them as gone). An empty `live_source_ids` under a complete
        snapshot is valid and means nothing is live -> retire all cards of these types.

    Mere absence from a sweep NEVER retires a card: `complete_snapshot` must be explicitly
    asserted by the caller. User-acted approvals (approved/rejected/deferred) are untouched.
    A superseded card auto-reactivates to 'pending' if the same item is re-submitted live
    (see INSERT ... ON CONFLICT clauses).

    `live_ids` is accepted for backwards compatibility but is no longer used to retire on
    absence.
    """
    if not action_types:
        return 0
    resolved_source_ids = {s.strip().lower() for s in (resolved_source_ids or set()) if str(s).strip()}
    live_source_ids = {s.strip().lower() for s in (live_source_ids or set()) if str(s).strip()}
    protect_ids = protect_ids or set()
    if not resolved_source_ids and not complete_snapshot:
        # No positive evidence supplied -> nothing is retired.
        return 0
    now = utc_now()
    retired = 0
    placeholders = ",".join("?" for _ in action_types)
    rows = db.execute(
        f"SELECT * FROM approvals WHERE status = 'pending' AND action_type IN ({placeholders})",
        tuple(sorted(action_types)),
    ).fetchall()
    for row in rows:
        if row["id"] in protect_ids:
            # Just upserted in this same call -> never retire it here.
            continue
        sid = approval_source_id(row)
        retire = False
        if sid and sid in resolved_source_ids:
            retire = True
        elif complete_snapshot and sid not in live_source_ids:
            # Authoritative full enumeration: anything not provably live is gone.
            # (sid == "" is never in live_source_ids, so unverifiable cards are retired too.)
            retire = True
        if retire:
            db.execute(
                "UPDATE approvals SET status = 'superseded', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            retired += 1
    if retired:
        touch_version(db)
    return retired


DEFAULT_MEETING_MINUTES = 60


def parse_display_time(value: str) -> datetime | None:
    """Parse the app's own formatted meeting time, e.g. 'Wed Jun 19, 2026, 11:40 AM PT'."""
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+[A-Za-z]{2,4}$", "", text).strip()  # drop trailing tz abbrev like ' PT'
    for fmt in ("%a %b %d, %Y, %I:%M %p", "%b %d, %Y, %I:%M %p", "%a %b %d %Y %I:%M %p"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=APP_TIMEZONE)
        except ValueError:
            continue
    return None


def _coerce_meeting_dt(value: str) -> datetime | None:
    dt = parse_iso_datetime(str(value or "").strip()) or parse_display_time(value)
    if not dt:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=APP_TIMEZONE)


def approval_meeting_end(row: sqlite3.Row, action_type: str | None = None) -> datetime | None:
    """Best-effort meeting end time for a time-bound approval (calendar / meeting-prep).

    Accepts ISO timestamps and the app's formatted display strings. Explicit end fields are
    used directly; start fields get a default meeting duration added. For meeting-prep cards
    (which store the meeting time in receivedAt) receivedAt is used as a last resort.
    """
    raw = row["details_json"] if "details_json" in row.keys() else None
    details: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                details = parsed
        except (json.JSONDecodeError, TypeError):
            details = {}
    if action_type is None and "action_type" in row.keys():
        action_type = row["action_type"]
    for end_key in ("meetingEnd", "endTime", "end", "rawMeetingEnd"):
        end_dt = _coerce_meeting_dt(details.get(end_key))
        if end_dt:
            return end_dt
    start_keys = ["rawMeetingTime", "meetingTime", "when", "start", "startTime", "meetingStart"]
    if action_type == "meeting-prep":
        start_keys.append("receivedAt")
    for start_key in start_keys:
        start_dt = _coerce_meeting_dt(details.get(start_key))
        if start_dt:
            return start_dt + timedelta(minutes=DEFAULT_MEETING_MINUTES)
    return None


def expire_time_bound_approvals(db: sqlite3.Connection) -> int:
    """Mark calendar + meeting-prep approvals 'expired' once the meeting end time has passed.

    Status 'expired' hides the card but preserves history; it does not auto-reactivate.
    Cards with no parseable meeting time are left untouched.
    """
    now = datetime.now(APP_TIMEZONE)
    expired = 0
    rows = db.execute(
        "SELECT * FROM approvals WHERE status = 'pending' AND action_type IN ('calendar', 'meeting-prep')"
    ).fetchall()
    for row in rows:
        end_dt = approval_meeting_end(row, row["action_type"])
        if end_dt and end_dt < now:
            db.execute(
                "UPDATE approvals SET status = 'expired', updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
            expired += 1
    if expired:
        touch_version(db)
    return expired


# Gate so the stale-job scan below runs at most once a minute even though /api/state may be
# polled every few seconds; the scan itself is cheap, but there is no reason to hammer the jobs
# table on every single dashboard refresh.
_STALE_JOB_CHECK_LOCK = threading.Lock()
_last_stale_job_check_ts = 0.0


def requeue_stale_jobs(db: sqlite3.Connection) -> int:
    """Reclaim jobs stuck in 'queued' or 'in_progress' because the Scout automation that owned
    them was interrupted (crashed, killed, lost network) and never came back to advance or fail
    them. Without this, an employee's roster card shows 'working' forever with no way to recover
    short of manually editing the database.

    Runs at most once a minute (see _STALE_JOB_CHECK_LOCK / _last_stale_job_check_ts above) since
    it is invoked on every /api/state poll. Threshold is configurable via config.json's
    'staleJobTimeoutHours' (default 2h). Jobs gated behind Quinn's redaction check
    (redaction_required but not yet redaction_applied) are left untouched so this cannot be used
    to bypass that gate.
    """
    global _last_stale_job_check_ts
    now_ts = time.time()
    with _STALE_JOB_CHECK_LOCK:
        if now_ts - _last_stale_job_check_ts < 60:
            return 0
        _last_stale_job_check_ts = now_ts

    try:
        timeout_hours = float(_setting("staleJobTimeoutHours", "DAILY_FLOW_STALE_JOB_TIMEOUT_HOURS", 2))
    except (TypeError, ValueError):
        timeout_hours = 2.0
    if timeout_hours <= 0:
        return 0
    cutoff = datetime.now(APP_TIMEZONE) - timedelta(hours=timeout_hours)

    candidates = db.execute(
        "SELECT * FROM jobs WHERE status IN ('in_progress', 'queued')"
    ).fetchall()
    now = utc_now()
    requeued = 0
    for job in candidates:
        updated = parse_timestamp(job["updated_at"])
        if updated is None or updated >= cutoff:
            continue
        # Quinn's redaction gate must never be bypassed by the watchdog.
        if job["redaction_required"] and not job["redaction_applied"]:
            continue
        # A job already sitting in 'queued' was never picked up at all, so resetting it to
        # 'queued' again is a no-op on status and only bumps updated_at — that would create an
        # infinite refresh loop where the same stuck job keeps "surviving" the stale check
        # forever. Only 'in_progress' jobs (automation crashed mid-flight) get re-queued; a
        # 'queued' job that sat untouched past the timeout is cancelled instead.
        if job["status"] == "queued":
            new_status = "cancelled"
            note = "auto-cancelled: queued but never picked up within the stale timeout"
        else:
            new_status = "queued"
            note = "auto-requeued after stale timeout"
        existing_blocker = (job["blocker"] or "").strip()
        blocker = f"{existing_blocker}; {note}" if existing_blocker else note
        db.execute(
            "UPDATE jobs SET status = ?, started_at = NULL, updated_at = ?, blocker = ? WHERE id = ?",
            (new_status, now, blocker, job["id"]),
        )
        add_event(
            db,
            job["employee"],
            f"Stale job {new_status}: {job['title']}",
            f"Job {job['id']} was stuck in '{job['status']}' since {job['updated_at']}; "
            f"set to '{new_status}' after exceeding the {timeout_hours}h stale-job timeout.",
        )
        requeued += 1
    if requeued:
        touch_version(db)
    return requeued



def replace_inbox_invite_approvals(
    db: sqlite3.Connection,
    invites: list[dict[str, Any]],
    reconcile: bool = True,
    complete_snapshot: bool = True,
) -> dict[str, int]:
    for invite in invites:
        subject = clean_subject(str(invite.get("subject", "")))
        when = str(invite.get("when") or invite.get("meetingTime") or invite.get("start") or "").strip()
        conflict_summary = str(invite.get("conflictSummary") or "").strip()
        recommendation = str(invite.get("recommendation") or "").strip()
        if not when or "time not available" in when.lower():
            raise ValueError(f"Invite '{subject}' is missing a matched calendar time. Match the Inbox invite to a calendar event or explain why matching failed before posting.")
        if not conflict_summary or conflict_summary.lower() == "not checked yet":
            raise ValueError(f"Invite '{subject}' is missing a real conflict check. Check the user's calendar for overlapping/adjacent events before posting.")
        if not recommendation:
            raise ValueError(f"Invite '{subject}' is missing a recommendation.")

    handled_keys = handled_calendar_invite_keys(db)
    now = utc_now()
    added = 0
    skipped = 0
    live_ids: set[str] = set()
    live_source_ids: set[str] = set()
    for invite in invites:
        subject = clean_subject(str(invite.get("subject", "")))
        sender = str(invite.get("sender") or invite.get("from") or "")
        organizer = str(invite.get("organizer") or sender or "Unknown organizer")
        raw_when = str(invite.get("when") or invite.get("meetingTime") or invite.get("start") or "Time not available from captured invite metadata")
        when = format_invite_time(raw_when)
        source_id = str(invite.get("id") or invite.get("sourceId") or invite.get("messageId") or "")
        if source_id.strip():
            live_source_ids.add(source_id.strip().lower())
        approval_id = stable_calendar_approval_id(subject, organizer, raw_when, source_id)
        live_ids.add(approval_id)
        if handled_keys.intersection(invite_match_keys(subject, organizer, raw_when, source_id)):
            skipped += 1
            continue
        current_status = str(invite.get("currentStatus") or "Inbox invite still present; Exchange meeting headers confirmed.")
        conflict_summary = str(invite.get("conflictSummary") or "Not checked yet")
        recommendation = str(invite.get("recommendation") or "Queue for user decision because this meeting invite is still in Inbox.")
        preview = (
            f"What it is: {subject}\n"
            f"When: {when}\n"
            f"Organizer: {organizer}\n"
            f"Conflicts: {conflict_summary}\n"
            f"Recommendation: {recommendation}"
        )
        details = {
            "type": "calendar-invite",
            "about": subject,
            "meetingTime": when,
            "rawMeetingTime": raw_when,
            "organizer": organizer,
            "location": invite.get("location", "Location not available from captured invite metadata"),
            "currentStatus": current_status,
            "conflictSummary": conflict_summary,
            "recommendation": recommendation,
            "whyApproval": "This invite is still in your Inbox, which means Daily Flow treats it as an unresolved calendar decision.",
            "sourceId": source_id,
            "sourceUrl": str(invite.get("sourceUrl") or invite.get("webLink") or invite.get("webUrl") or invite.get("url") or "").strip(),
            "evidence": invite.get("evidence", ""),
        }
        db.execute(
            """
            INSERT INTO approvals(id, created_at, updated_at, employee, action_type, risk, title, preview, destination, status, details_json)
            VALUES(?, ?, ?, 'Mina', 'calendar', 'medium', ?, ?, 'Outlook RSVP decision', 'pending', ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                employee=excluded.employee,
                action_type=excluded.action_type,
                risk=excluded.risk,
                title=excluded.title,
                preview=excluded.preview,
                destination=excluded.destination,
                status=CASE WHEN approvals.status = 'pending' OR approvals.status = 'superseded' THEN 'pending' ELSE approvals.status END,
                details_json=excluded.details_json
            """,
            (
                stable_calendar_approval_id(subject, organizer, raw_when, source_id),
                now,
                now,
                f"Inbox calendar decision needed: {subject}",
                preview,
                json.dumps(details),
            ),
        )
        added += 1
    retired = reconcile_pending_approvals(
        db,
        {"calendar"},
        live_source_ids=live_source_ids,
        complete_snapshot=complete_snapshot,
        protect_ids=live_ids,
    ) if reconcile else 0
    add_event(
        db,
        "Mina",
        f"Live Inbox invite scan persisted {added} header-confirmed calendar approval card(s).",
        f"Source: live Inbox message metadata plus per-message Exchange/Outlook meeting headers. "
        f"Skipped {skipped} already-handled invite(s). Retired {retired} calendar card(s) whose invite is no longer in the Inbox.",
    )
    return {
        "confirmedInvites": len(invites),
        "calendarApprovals": added,
        "skippedHandled": skipped,
        "retiredStale": retired,
    }


def event_date(value: str) -> str:
    return local_date_key(value)


WORK_LEDGER_JOB_TYPES = {
    "employee-work",
    "calendar-rsvp",
    "email-action",
    "teams-action",
    "workflow-action",
    "impact-highlight",
}
WORK_LEDGER_NOISE_TERMS = (
    "swept all surfaces",
    "no new signals",
    "approval inbox steady",
    "request logging",
)
WORK_LEDGER_EMPLOYEE_NAMES = ("Major", "Riley", "Mina", "Reese", "Tilly", "Dash", "Drew", "Logan")


def stable_work_ledger_id(raw: dict[str, Any]) -> str:
    explicit = str(raw.get("id") or "").strip()
    if explicit:
        return explicit if explicit.startswith("work_") else f"work_{explicit}"
    source_type = str(raw.get("sourceType") or raw.get("source_type") or "").strip()
    source_id = str(raw.get("sourceId") or raw.get("source_id") or "").strip()
    occurred_at = str(raw.get("occurredAt") or raw.get("occurred_at") or raw.get("createdAt") or "").strip()
    title = str(raw.get("title") or raw.get("subject") or "").strip()
    digest = hashlib.sha256("|".join([source_type, source_id, occurred_at, title]).encode("utf-8")).hexdigest()[:24]
    return f"work_{digest}"


def json_list(value: Any) -> str:
    if value is None or value == "":
        return "[]"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "[]"
        try:
            parsed = json.loads(text)
            return json.dumps(parsed if isinstance(parsed, list) else [text])
        except json.JSONDecodeError:
            return json.dumps([text])
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([str(value)])


def json_object(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
            return json.dumps(parsed) if isinstance(parsed, dict) else text
        except json.JSONDecodeError:
            return text
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def upsert_work_ledger_entries(db: sqlite3.Connection, raw_entries: list[Any]) -> dict[str, int]:
    if not isinstance(raw_entries, list):
        raise ValueError("entries must be an array")
    now = utc_now()
    upserted = 0
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("each work ledger entry must be an object")
        title = str(raw.get("title") or raw.get("subject") or "").strip()
        summary = str(raw.get("summary") or raw.get("workSummary") or raw.get("bodyOfWork") or "").strip()
        if not title or not summary:
            raise ValueError("each work ledger entry requires title and summary")
        entry_id = stable_work_ledger_id(raw)
        occurred_at = str(raw.get("occurredAt") or raw.get("occurred_at") or raw.get("createdAt") or now).strip()
        source_type = str(raw.get("sourceType") or raw.get("source_type") or "").strip()
        source_id = str(raw.get("sourceId") or raw.get("source_id") or "").strip()
        db.execute(
            """
            INSERT INTO work_ledger_entries(
                id, created_at, updated_at, occurred_at, employee, category, title, summary,
                people_json, customer, evidence_json, impact_level, impact_summary,
                source_type, source_id, status
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                occurred_at=excluded.occurred_at,
                employee=excluded.employee,
                category=excluded.category,
                title=excluded.title,
                summary=excluded.summary,
                people_json=excluded.people_json,
                customer=excluded.customer,
                evidence_json=excluded.evidence_json,
                impact_level=excluded.impact_level,
                impact_summary=excluded.impact_summary,
                source_type=excluded.source_type,
                source_id=excluded.source_id,
                status=excluded.status
            """,
            (
                entry_id,
                now,
                now,
                occurred_at,
                str(raw.get("employee") or "Daily Flow Team").strip() or "Daily Flow Team",
                str(raw.get("category") or "work-completed").strip() or "work-completed",
                title,
                summary,
                json_list(raw.get("people") or raw.get("peopleWorkedWith")),
                str(raw.get("customer") or raw.get("account") or "").strip(),
                json_object(raw.get("evidence") or raw.get("link")),
                str(raw.get("impactLevel") or raw.get("impact_level") or "supporting").strip() or "supporting",
                str(raw.get("impactSummary") or raw.get("impact_summary") or "").strip(),
                source_type,
                source_id,
                str(raw.get("status") or "active").strip() or "active",
            ),
        )
        upserted += 1
    touch_version(db)
    return {"upserted": upserted}


def decode_json_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    return [str(parsed)]


def classify_work_category(job: dict[str, Any], summary: str) -> str:
    text = f"{job.get('type', '')} {job.get('title', '')} {summary}".lower()
    if job.get("type") == "impact-highlight":
        return "gratitude-or-business-impact" if any(term in text for term in ("thank", "appreciat", "grateful", "gratitude")) else "business-impact"
    if any(term in text for term in ("meeting", "sync", "call", "briefing", "workshop")):
        return "meeting"
    if any(term in text for term in ("doc", "document", "draft", "deck", "proposal", "brief", "email", "message")):
        return "document-or-draft"
    if any(term in text for term in ("research", "finding", "context", "account")):
        return "research"
    if any(term in text for term in ("schedule", "rsvp", "calendar", "demo time")):
        return "scheduling"
    if any(term in text for term in ("teams", "chat", "worked with", "collaborat")):
        return "collaboration"
    return "work-completed"


def parse_result_link(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items() if val}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        text = str(value).strip()
        return {"href": text} if text.startswith(("http://", "https://", "/api/documents/")) else {}
    if isinstance(parsed, str):
        return parse_result_link(parsed)
    if isinstance(parsed, dict):
        return {str(key): str(val) for key, val in parsed.items() if val}
    return {}


def cleanup_report_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for name in WORK_LEDGER_EMPLOYEE_NAMES:
        text = re.sub(rf"^{name}\s+(?=\w)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe user-approved\b", "the approved", text, flags=re.IGNORECASE)
    text = re.sub(r"\buser guidance shaped\b", "the request shaped", text, flags=re.IGNORECASE)
    return text[:1].upper() + text[1:] if text else text


def readable_title_from_job(job: dict[str, Any], summary: str, link: dict[str, str]) -> str:
    lowered_summary = summary.lower()
    if "copilot studio overview deck" in lowered_summary:
        return "Created Copilot Studio overview deck"
    if "microsoft 365 copilot" in lowered_summary or "m365 copilot" in lowered_summary:
        if "deck" in lowered_summary or "powerpoint" in lowered_summary:
            return "Created Microsoft 365 Copilot what's-new deck"
    if "openai" in lowered_summary and "anthropic" in lowered_summary and ("research document" in lowered_summary or "research brief" in lowered_summary):
        return "Created OpenAI and Anthropic model comparison research document"
    if "email draft" in lowered_summary and "microsoft scout" in lowered_summary:
        return "Drafted Microsoft Scout explanation email"
    if "email draft" in lowered_summary:
        return "Drafted review-ready email"
    raw_title = re.sub(r"\s+", " ", str(job.get("title") or "")).strip()
    title = re.sub(r"^\[EXTERNAL\]\s*", "", raw_title, flags=re.IGNORECASE).strip()
    if title.lower() in {"done?", "done", "complete", "completed"} or len(title) > 90:
        label = link.get("label", "")
        if label:
            title = label
        elif "powerpoint" in summary.lower() or "deck" in summary.lower():
            title = "Created presentation deck"
        elif "word" in summary.lower() or "research document" in summary.lower():
            title = "Created research document"
        elif "email draft" in summary.lower():
            title = "Drafted customer/internal email"
        else:
            title = "Completed reportable work"
    title = re.sub(r"^(Open|Created)\s+", "", title, flags=re.IGNORECASE).strip()
    if any(term in summary.lower() for term in ("created", "built", "prepared", "drafted")) and not title.lower().startswith(("created", "drafted", "prepared", "built")):
        title = f"Created {title}" if title else "Created deliverable"
    return cleanup_report_text(title)


def inferred_for_whom(job: dict[str, Any]) -> str:
    text = f"{job.get('title', '')} {job.get('instructions', '')} {job.get('result_summary', '')}"
    for pattern in (
        r"\bfor\s+customer\s+([^.;\n]+)",
        r"\bfor\s+client\s+([^.;\n]+)",
        r"\bfor\s+account\s+([^.;\n]+)",
        r"\bfor\s+([^.;\n]+?)\s+(?:customer|client|account)\b",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return cleanup_report_text(match.group(1))
    return ""


def should_include_calendar_job(job: dict[str, Any]) -> bool:
    text = f"{job.get('title', '')} {job.get('instructions', '')} {job.get('result_summary', '')}".lower()
    return any(term in text for term in ("customer", "client", "executive", "external", "partner")) and "no private conflict details" not in text


def is_reportable_job(job: dict[str, Any]) -> bool:
    if job["status"] not in ("completed", "done"):
        return False
    if job["type"] not in WORK_LEDGER_JOB_TYPES:
        return False
    if job["type"] == "calendar-rsvp" and not should_include_calendar_job(job):
        return False
    summary = str(job.get("result_summary") or "").strip()
    if not summary:
        return False
    lowered = f"{job.get('title', '')} {summary}".lower()
    return not any(term in lowered for term in WORK_LEDGER_NOISE_TERMS)


def job_work_entry(job: dict[str, Any]) -> dict[str, Any]:
    summary = cleanup_report_text(str(job.get("result_summary") or "").strip())
    is_impact = job["type"] == "impact-highlight"
    link = parse_result_link(job.get("result_link_json"))
    raw_title = job["title"].removeprefix("Impact highlight accepted: ").strip() if is_impact else job["title"]
    title = readable_title_from_job({**job, "title": raw_title}, summary, link)
    category = classify_work_category(job, summary)
    customer = inferred_for_whom(job)
    return {
        "id": f"work_job_{job['id']}",
        "date": event_date(job["completed_at"] or job["updated_at"] or job["created_at"]),
        "occurredAt": job["completed_at"] or job["updated_at"] or job["created_at"],
        "employee": job["employee"],
        "title": title or "Work completed",
        "work": summary,
        "impact": summary if is_impact else "",
        "evidence": "User-approved outcome evidence captured in Daily Flow." if is_impact else "Completed Daily Flow work result.",
        "category": category,
        "people": [],
        "customer": customer,
        "impactLevel": "highlight" if is_impact else "supporting",
        "link": job["result_link_json"],
        "sourceType": "job",
        "sourceId": job["id"],
        "reportable": True,
    }


def stored_work_entry(entry: dict[str, Any]) -> dict[str, Any]:
    impact_summary = str(entry.get("impact_summary") or "").strip()
    return {
        "id": entry["id"],
        "date": event_date(entry["occurred_at"]),
        "occurredAt": entry["occurred_at"],
        "employee": entry["employee"],
        "title": cleanup_report_text(entry["title"]),
        "work": cleanup_report_text(entry["summary"]),
        "impact": cleanup_report_text(impact_summary),
        "evidence": entry["evidence_json"],
        "category": entry["category"],
        "people": decode_json_list(entry["people_json"]),
        "customer": entry["customer"],
        "impactLevel": entry["impact_level"],
        "link": entry["evidence_json"],
        "sourceType": entry["source_type"],
        "sourceId": entry["source_id"],
        "reportable": entry["status"] == "active",
    }


def build_impact_ledger(
    approvals: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    work_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    today = local_date_key()
    highlights: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_content: set[str] = set()
    seen_day_titles: set[str] = set()

    for entry in work_entries or []:
        item = stored_work_entry(entry)
        source_key = (item["sourceType"], item["sourceId"])
        if all(source_key):
            seen_sources.add(source_key)
        content_key = "|".join(
            str(item.get(key) or "").strip().lower()
            for key in ("date", "title", "work", "link")
        )
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        seen_day_titles.add(f"{item.get('date', '').strip().lower()}|title|{item.get('title', '').strip().lower()}")
        highlights.append(item)

    for job in jobs:
        if not is_reportable_job(job):
            continue
        source_key = ("job", job["id"])
        if source_key in seen_sources:
            continue
        item = job_work_entry(job)
        link = parse_result_link(item.get("link"))
        link_key = str(link.get("href") or link.get("downloadHref") or "").strip().lower()
        content_key = f"{item.get('date', '').strip().lower()}|link|{link_key}" if link_key else "|".join(
            [
                str(item.get("date") or "").strip().lower(),
                str(item.get("title") or "").strip().lower(),
                str(item.get("work") or "").strip().lower(),
            ]
        )
        if content_key in seen_content:
            continue
        day_title_key = f"{item.get('date', '').strip().lower()}|title|{item.get('title', '').strip().lower()}"
        if day_title_key in seen_day_titles:
            continue
        seen_content.add(content_key)
        seen_day_titles.add(day_title_key)
        highlights.append(item)
        seen_sources.add(source_key)

    highlights.sort(key=lambda item: str(item.get("occurredAt") or item.get("date") or ""), reverse=True)

    if not highlights:
        highlights.append(
            {
                "date": today,
                "employee": "Daily Flow Team",
                "title": "No work ledger entries captured yet",
                "work": "No completed work, meetings, created content, collaboration, or outcome highlights have been captured for this view yet.",
                "impact": "",
                "evidence": "The verbose Activity Log remains separate and preserved.",
                "category": "empty",
                "people": [],
                "customer": "",
                "impactLevel": "none",
                "link": "",
                "reportable": False,
            }
        )

    reportable = [highlight for highlight in highlights if highlight["reportable"]]
    impact_items = [highlight for highlight in reportable if highlight.get("impact") or highlight.get("impactLevel") in {"highlight", "significant"} or "impact" in str(highlight.get("category", ""))]
    return {
        "date": today,
        "highlights": highlights,
        "metrics": {
            "reportableHighlights": len(reportable),
            "impactItems": len(impact_items),
            "peopleWorkedWith": len({person for item in reportable for person in item.get("people", [])}),
            "evidenceLinks": len([item for item in reportable if item.get("link")]),
        },
    }


def _safe_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _json_or(value: Any, fallback: str) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return fallback


def record_sweep_start(db: sqlite3.Connection, source: str = "automation", model: str = "", channels: Any = None) -> str:
    """Open a structured audit record for a sweep so coverage and verification are
    visible and never silently lost. Returns the sweep id the worker reports back to."""
    sweep_id = new_id("sweep")
    now = utc_now()
    db.execute(
        "INSERT INTO sweep_runs(id, created_at, started_at, source, model, status, channels_json) "
        "VALUES(?, ?, ?, ?, ?, 'running', ?)",
        (sweep_id, now, now, str(source or ""), str(model or ""),
         _json_or(channels if isinstance(channels, list) else [], "[]")),
    )
    add_event(db, "Major", f"Sweep started: {source or 'sweep'}.")
    touch_version(db)
    return sweep_id


def record_sweep_finish(
    db: sqlite3.Connection,
    sweep_id: str | None,
    status: str = "completed",
    counts: Any = None,
    passes: Any = None,
    verify: Any = None,
    summary: str = "",
    error: str = "",
    channels: Any = None,
) -> str:
    """Close the sweep audit record with what was covered (channels), what was found
    (counts), which specialist passes ran (passes), and what the critic verified
    (verify). Tolerates a finish without a matching start so telemetry is never lost."""
    now = utc_now()
    final_status = status if status in ("completed", "blocked", "partial") else "completed"
    counts_j = _json_or(counts or {}, "{}")
    passes_j = _json_or(passes or [], "[]")
    verify_j = _json_or(verify or {}, "{}")
    existing = db.execute("SELECT id FROM sweep_runs WHERE id = ?", (sweep_id,)).fetchone() if sweep_id else None
    if existing:
        if channels:
            db.execute("UPDATE sweep_runs SET channels_json = ? WHERE id = ?", (_json_or(channels, "[]"), sweep_id))
        db.execute(
            "UPDATE sweep_runs SET finished_at=?, status=?, counts_json=?, passes_json=?, verify_json=?, summary=?, error=? WHERE id=?",
            (now, final_status, counts_j, passes_j, verify_j, str(summary or ""), str(error or ""), sweep_id),
        )
        final_id = sweep_id
    else:
        final_id = sweep_id or new_id("sweep")
        db.execute(
            "INSERT INTO sweep_runs(id, created_at, started_at, finished_at, source, model, status, channels_json, counts_json, passes_json, verify_json, summary, error) "
            "VALUES(?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, ?, ?)",
            (final_id, now, now, now, final_status, _json_or(channels or [], "[]"), counts_j, passes_j, verify_j, str(summary or ""), str(error or "")),
        )
    add_event(db, "Major", f"Sweep {final_status}.", str(summary or "")[:280])
    touch_version(db)
    return final_id


def recent_sweeps(db: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    raw = rows(db.execute("SELECT * FROM sweep_runs ORDER BY started_at DESC LIMIT ?", (int(limit),)))
    out: list[dict[str, Any]] = []
    for r in raw:
        out.append({
            "id": r.get("id"),
            "startedAt": r.get("started_at") or "",
            "finishedAt": r.get("finished_at") or "",
            "source": r.get("source") or "",
            "model": r.get("model") or "",
            "status": r.get("status") or "",
            "channels": _safe_json(r.get("channels_json"), []),
            "counts": _safe_json(r.get("counts_json"), {}),
            "passes": _safe_json(r.get("passes_json"), []),
            "verify": _safe_json(r.get("verify_json"), {}),
            "summary": r.get("summary") or "",
            "error": r.get("error") or "",
        })
    return out


def sweep_stats(db: sqlite3.Connection) -> dict[str, Any]:
    today = local_date_key()
    raw = rows(db.execute("SELECT started_at, finished_at, status FROM sweep_runs ORDER BY started_at DESC LIMIT 200"))
    today_runs = [r for r in raw if event_date(r.get("started_at") or "") == today]
    completed_today = [r for r in today_runs if (r.get("status") or "") in ("completed", "partial")]
    last = raw[0] if raw else {}
    return {
        "todayTotal": len(today_runs),
        "todayCompleted": len(completed_today),
        "lastStartedAt": last.get("started_at", "") if last else "",
        "lastFinishedAt": last.get("finished_at", "") if last else "",
        "lastStatus": last.get("status", "") if last else "",
    }


def work_ledger_today(db: sqlite3.Connection) -> dict[str, Any]:
    """Surface how much real body-of-work has been captured today so the dedicated
    capture pass can self-verify and self-correct instead of silently under-firing."""
    today = local_date_key()
    active = rows(db.execute(
        "SELECT occurred_at, title, employee, category, impact_level, customer FROM work_ledger_entries "
        "WHERE status='active' ORDER BY occurred_at DESC LIMIT 50"
    ))
    today_entries = [e for e in active if event_date(e.get("occurred_at") or "") == today]
    return {
        "todayCount": len(today_entries),
        "recent": [
            {
                "occurredAt": e.get("occurred_at") or "",
                "title": e.get("title") or "",
                "category": e.get("category") or "",
                "impactLevel": e.get("impact_level") or "",
                "customer": e.get("customer") or "",
            }
            for e in active[:10]
        ],
    }


def classify_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Authoritative server-side email-vs-meeting classification. Reserves the LLM for
    genuinely ambiguous items: when authoritative is True the routing is header/Graph
    backed and the worker should trust it rather than re-deciding."""
    if not isinstance(raw, dict):
        raw = {}
    is_meeting = looks_like_meeting_message(raw)
    action_type = review_signal_action_type(raw)
    mmt = str(raw.get("meetingMessageType") or "").strip().lower()
    odata = str(raw.get("@odata.type") or raw.get("odataType") or "").lower()
    mclass = str(raw.get("messageClass") or "").lower()
    headers = raw.get("headers") or raw.get("internetMessageHeaders") or ""
    htext = headers.lower() if isinstance(headers, str) else json.dumps(headers).lower()
    authoritative = bool(
        (mmt and mmt not in ("none", "null", "false", "0"))
        or "eventmessage" in odata
        or mclass.startswith("ipm.schedule")
        or "ee_meetingmessage" in htext
        or "x-ms-exchange-calendar-originator-id" in htext
    )
    if is_meeting:
        route = "/api/inbox-invites"
        confidence = "high" if authoritative else "medium"
    else:
        route = "/api/review-signals"
        confidence = "high"
    return {
        "isMeeting": is_meeting,
        "actionType": action_type,
        "route": route,
        "confidence": confidence,
        "authoritative": authoritative,
    }


_TERMINAL_JOB_STATUSES = {"completed", "done", "blocked"}


def _trim_terminal_job_instructions(jobs: list[dict[str, Any]], limit: int = 280) -> None:
    """Shrink the /api/state payload: completed/blocked jobs do not need their full
    (often ~6KB, near-identical) sweep instructions on every poll. Queued/in_progress
    jobs keep full instructions because the worker reads them to execute. DB history is
    untouched -- this only shapes the client payload."""
    for job in jobs:
        if job.get("status") in _TERMINAL_JOB_STATUSES:
            instr = job.get("instructions") or ""
            if len(instr) > limit:
                job["instructions"] = instr[:limit] + " …[trimmed in state payload]"


_ACTIONABLE_JOB_STATUSES = {"queued", "in_progress"}

# Job types the workers are known to execute. Deliberately advisory: the gate
# reports every pending job regardless, and only tags which ones match. An
# unknown type must never be filtered out here -- a false positive costs one
# cheap run, a false negative silently drops work the user approved.
_KNOWN_JOB_TYPES = {
    "manual-signal-sweep", "dashboard-chat", "employee-work",
    "email-action", "teams-action", "calendar-rsvp", "email-review",
    "impact-highlight", "send-draft",
}


def get_gate() -> dict[str, Any]:
    """The cheapest possible 'is there anything to do?' answer.

    The interval worker asks this on every run and the honest answer is
    almost always no. Answering it from /api/state meant shipping the whole
    payload, most of it completed-job history, for a question a few hundred
    bytes can settle. Since an automation pays for every token it reads, that
    made the no-op case the most expensive thing the worker did. Job ids and
    types are included so a run that does have work can go straight to
    /api/jobs/{id} without fetching state at all.
    """
    with connect() as db:
        pending = rows(db.execute(
            "SELECT id, type, status, employee, title, priority, created_at "
            "FROM jobs WHERE status IN ('queued','in_progress') "
            "ORDER BY created_at"
        ))
        blocked = db.execute(
            "SELECT COUNT(*) n FROM jobs WHERE status = 'blocked'"
        ).fetchone()["n"]
        approvals = db.execute(
            "SELECT COUNT(*) n FROM approvals WHERE status = 'pending'"
        ).fetchone()["n"]
    unknown = sorted({(job.get("type") or "") for job in pending}
                     - _KNOWN_JOB_TYPES)
    return {
        # Any pending job means work. Classification is the caller's problem.
        "hasWork": bool(pending),
        "pendingJobs": pending,
        "pendingCount": len(pending),
        "unrecognisedTypes": unknown,
        "blockedJobs": blocked,
        "pendingApprovals": approvals,
        "serverTime": utc_now(),
    }


def get_job_detail(job_id: str) -> dict[str, Any] | None:
    """Everything a worker needs to run one job, without reading /api/state.

    The gate hands out job ids precisely so a run that has work can fetch just
    that job, which only holds if the id actually resolves to something. This
    returns the full row -- including instructions, the part the worker
    executes -- plus the chat thread for dashboard-chat jobs so progress can be
    posted back in context. Returns None when the id is unknown.
    """
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        thread: dict[str, Any] | None = None
        messages: list[dict[str, Any]] = []
        thread_id = job.get("thread_id")
        if thread_id:
            thread_row = db.execute(
                "SELECT * FROM chat_threads WHERE id = ?", (thread_id,)
            ).fetchone()
            thread = dict(thread_row) if thread_row else None
            messages = rows(db.execute(
                "SELECT * FROM chat_messages WHERE thread_id = ? "
                "ORDER BY created_at LIMIT 50",
                (thread_id,),
            ))
    return {
        "ok": True,
        "job": job,
        "thread": thread,
        "messages": messages,
        "serverTime": utc_now(),
    }


_REQUIRED_AUTOMATIONS = (
    "Daily Flow Morning Brief",
    "Daily Flow Evening Wrap-up",
    "Daily Flow Continuous Work Pulse",
    "Daily Flow Attention Major Trigger",
)

# Scout's own automations file. The data folder name varies by build, which is
# why this mirrors the SKILL_ROOTS candidate list rather than assuming .scout.
SCOUT_AUTOMATION_FILES = [
    Path.home() / _root_dir / "m-automations" / "automations.json"
    for _root_dir in (".scout", ".copilot", ".copilot-cloud", ".copilot-dev")
]


def get_automation_health() -> dict[str, Any]:
    """Report which of the four required automations are switched off.

    A paused automation does nothing, and the only symptom is a board that
    quietly stops updating, which is easy to mistake for a quiet day. Scout
    owns the on/off state, so read it from Scout's own file instead of
    inferring it from app activity.

    Every failure path here is non-fatal and reported as readable=False. The
    file belongs to another program, its folder name differs between Scout
    builds, and some builds encrypt it. A dashboard that broke because an
    optional file outside the app was missing would be worse than one that
    simply does not show this particular warning.
    """
    for path in SCOUT_AUTOMATION_FILES:
        try:
            if not path.is_file():
                continue
            # utf-8-sig, not utf-8: a BOM is legal here and some writers add one,
            # and plain utf-8 turns that into a parse error that would silently
            # disable this whole check.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        # Current builds store a top-level array. Accept the object form too so
        # a future wrapper key does not silently turn every automation "missing".
        entries = data if isinstance(data, list) else data.get("automations")
        if not isinstance(entries, list):
            continue
        by_name = {
            str(item.get("name") or ""): item
            for item in entries if isinstance(item, dict)
        }
        disabled, missing = [], []
        for name in _REQUIRED_AUTOMATIONS:
            item = by_name.get(name)
            if item is None:
                missing.append(name)
            elif not item.get("enabled"):
                disabled.append(name)
        return {
            "readable": True,
            "source": str(path),
            "disabled": disabled,
            "missing": missing,
            "healthy": not disabled and not missing,
            "enabledCount": len(_REQUIRED_AUTOMATIONS) - len(disabled) - len(missing),
            "requiredCount": len(_REQUIRED_AUTOMATIONS),
        }
    return {
        "readable": False,
        "source": None,
        "disabled": [],
        "missing": [],
        "healthy": True,
        "enabledCount": 0,
        "requiredCount": len(_REQUIRED_AUTOMATIONS),
    }


# Dropped from the agent view, with the reason each one is safe to drop.
# Counts for all three are still reported under `omitted`, so a caller can
# tell the difference between "nothing there" and "not sent".
_AGENT_VIEW_DROP = {
    "jobs": "only queued/in_progress are kept, as activeJobs",
    "events": "todayActivity carries today's; full log at /api/activity-log",
    "impactLedger": "summary only; full ledger at /api/impact-ledger",
    "removedEmployees": "not used by any automation",
}


def to_agent_view(state: dict[str, Any]) -> dict[str, Any]:
    """Shrink a full state payload to what an automation actually reads.

    Built as a projection of get_state() rather than a second query path, so
    the two can never disagree about the same facts -- a lean view that
    quietly drifts from the real state would be worse than a large one.

    The browser dashboard still gets the full payload; only callers that ask
    for view=agent see this. Everything removed here is either available from
    a dedicated endpoint or is history no run needs to re-read.
    """
    view = {k: v for k, v in state.items() if k not in _AGENT_VIEW_DROP}
    jobs = state.get("jobs") or []
    view["activeJobs"] = [job for job in jobs
                          if job.get("status") in _ACTIONABLE_JOB_STATUSES]
    ledger = state.get("impactLedger") or {}
    view["impactLedgerSummary"] = {
        key: len(value) if isinstance(value, list) else value
        for key, value in ledger.items()
    }
    view["omitted"] = {
        "jobs": len(jobs),
        "events": len(state.get("events") or []),
        "impactLedger": len(ledger),
        "note": "view=agent. Full payload at /api/state, "
                "full ledger at /api/impact-ledger.",
    }
    return view


KNOWLEDGE_STALE_DAYS = 30
KNOWLEDGE_QUERY_LIMIT = 500


def knowledge_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """One knowledge row in dashboard shape. `stale` is computed from updated_at on read rather
    than trusted from the column, so an entry cannot look fresh just because nothing re-flagged it."""
    entry = dict(row)
    entry["details"] = _safe_json(entry.pop("details_json", "{}"), {})
    entry["relatedIds"] = _safe_json(entry.pop("related_ids_json", "[]"), [])
    updated = parse_timestamp(entry.get("updated_at"))
    cutoff = datetime.now(APP_TIMEZONE) - timedelta(days=KNOWLEDGE_STALE_DAYS)
    entry["stale"] = bool(updated is not None and updated < cutoff)
    due = parse_timestamp(entry.get("due_date"))
    entry["overdue"] = bool(
        due is not None
        and entry.get("type") == "commitment"
        and entry.get("status") == "active"
        and due < datetime.now(APP_TIMEZONE)
    )
    return entry


def query_knowledge(db: sqlite3.Connection, entry_type: str = "", search: str = "",
                    status: str = "active") -> list[dict[str, Any]]:
    """Filtered knowledge lookup. Defaults to status='active' so soft-deleted entries stay out of
    the way; pass status='all' to include everything."""
    clauses: list[str] = []
    params: list[Any] = []
    if entry_type:
        clauses.append("type = ?")
        params.append(entry_type)
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, needle])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(KNOWLEDGE_QUERY_LIMIT)
    cursor = db.execute(
        f"SELECT * FROM knowledge_entries {where} ORDER BY updated_at DESC LIMIT ?", params
    )
    return [knowledge_to_dict(row) for row in cursor.fetchall()]


def save_knowledge_entry(db: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Create or update one knowledge entry. Passing an existing `id` updates it in place, which is
    what makes the graph a graph rather than an append-only log: Casey re-verifies a person or a
    project instead of accumulating duplicates of it."""
    now = utc_now()
    entry_id = str(data.get("id", "")).strip()
    existing = None
    if entry_id:
        existing = db.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
    if existing is None:
        entry_id = entry_id or new_id("kn")
        db.execute(
            "INSERT INTO knowledge_entries(id, created_at, updated_at, type, title, summary, "
            "details_json, status, owner, due_date, source_type, source_id, related_ids_json, "
            "last_verified_at, stale) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                entry_id, now, now,
                str(data.get("type", "note")).strip() or "note",
                str(data.get("title", "")).strip(),
                str(data.get("summary", "")).strip(),
                json.dumps(data.get("details") or {}),
                str(data.get("status", "active")).strip() or "active",
                str(data.get("owner", "")).strip(),
                str(data.get("dueDate", "")).strip(),
                str(data.get("sourceType", "")).strip(),
                str(data.get("sourceId", "")).strip(),
                json.dumps(data.get("relatedIds") or []),
                now,
            ),
        )
    else:
        current = dict(existing)
        db.execute(
            "UPDATE knowledge_entries SET updated_at = ?, type = ?, title = ?, summary = ?, "
            "details_json = ?, status = ?, owner = ?, due_date = ?, source_type = ?, source_id = ?, "
            "related_ids_json = ?, last_verified_at = ?, stale = 0 WHERE id = ?",
            (
                now,
                str(data.get("type", current["type"])).strip() or current["type"],
                str(data.get("title", current["title"])).strip() or current["title"],
                str(data.get("summary", current["summary"])),
                json.dumps(data["details"]) if "details" in data else current["details_json"],
                str(data.get("status", current["status"])).strip() or current["status"],
                str(data.get("owner", current["owner"])),
                str(data.get("dueDate", current["due_date"])),
                str(data.get("sourceType", current["source_type"])),
                str(data.get("sourceId", current["source_id"])),
                json.dumps(data["relatedIds"]) if "relatedIds" in data else current["related_ids_json"],
                now,
                entry_id,
            ),
        )
    db.commit()
    row = db.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
    return knowledge_to_dict(row)


def delete_knowledge_entry(db: sqlite3.Connection, entry_id: str) -> bool:
    """Soft delete. The row stays so a wrong delete is recoverable and so history stays honest."""
    row = db.execute("SELECT id FROM knowledge_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return False
    db.execute(
        "UPDATE knowledge_entries SET status = 'deleted', updated_at = ? WHERE id = ?",
        (utc_now(), entry_id),
    )
    db.commit()
    return True


def knowledge_summary(db: sqlite3.Connection) -> dict[str, Any]:
    """Casey's headline numbers for the dashboard: how much is remembered, what is overdue, and
    what has gone quiet long enough to be worth re-checking."""
    try:
        entries = [knowledge_to_dict(row) for row in db.execute(
            "SELECT * FROM knowledge_entries WHERE status = 'active'"
        ).fetchall()]
    except sqlite3.OperationalError:
        return {"readable": False, "totalEntries": 0, "overdueCommitments": 0, "staleEntries": 0,
                "byType": {}, "lastUpdated": ""}
    by_type: dict[str, int] = {}
    for entry in entries:
        by_type[entry["type"]] = by_type.get(entry["type"], 0) + 1
    last_updated = max((entry.get("updated_at") or "" for entry in entries), default="")
    return {
        "readable": True,
        "totalEntries": len(entries),
        "overdueCommitments": len([e for e in entries if e.get("overdue")]),
        "staleEntries": len([e for e in entries if e.get("stale")]),
        "byType": dict(sorted(by_type.items())),
        "lastUpdated": last_updated,
    }


def quality_summary(db: sqlite3.Connection) -> dict[str, Any]:
    """Quinn's risk register headline: what is waiting on review, what she has already ruled on,
    and which automations have gone stale. Reported from the job rows themselves so it cannot
    drift from what actually happened."""
    try:
        jobs = rows(db.execute(
            "SELECT id, employee, title, status, quality_review, quality_verdict, risk_level, updated_at "
            "FROM jobs WHERE quality_review = 1 ORDER BY updated_at DESC LIMIT 200"
        ))
    except sqlite3.OperationalError:
        # Only reachable if the jobs migration has not run yet. Reported rather than swallowed, so a
        # panel showing zeroes is distinguishable from a panel that could not read anything.
        return {"readable": False, "flaggedForReview": 0, "awaitingReview": 0, "heldJobs": 0,
                "heldItems": [], "staleAutomations": [], "lastSweepAt": "", "lastVerdict": "",
                "lastVerdictAt": ""}
    awaiting = [job for job in jobs if not (job.get("quality_verdict") or "").strip()]
    held = [job for job in jobs if (job.get("quality_verdict") or "").strip() == "hold"]
    health = get_automation_health()
    # "Stale" per Quinn's brief means an automation that has not run in over 25h. The app cannot see
    # Scout's run history, but it does see every sweep those automations post, so absence of sweeps
    # is the honest available signal. Reported as a reason string rather than a bare count so the
    # dashboard never implies more precision than the app actually has.
    stale_reasons: list[str] = []
    last_sweep = db.execute(
        "SELECT started_at FROM sweep_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    last_sweep_at = (dict(last_sweep).get("started_at") if last_sweep else "") or ""
    parsed_sweep = parse_timestamp(last_sweep_at)
    if parsed_sweep is None:
        stale_reasons.append("No sweep has ever been recorded")
    elif parsed_sweep < datetime.now(APP_TIMEZONE) - timedelta(hours=25):
        stale_reasons.append(f"No sweep since {last_sweep_at}")
    for name in health.get("disabled") or []:
        stale_reasons.append(f"{name} is switched off in Scout")
    for name in health.get("missing") or []:
        stale_reasons.append(f"{name} is not installed in Scout")
    last_verdict = next(
        (job for job in jobs if (job.get("quality_verdict") or "").strip()), None
    )
    return {
        "readable": True,
        "flaggedForReview": len(jobs),
        "awaitingReview": len(awaiting),
        "heldJobs": len(held),
        "heldItems": [{"id": job["id"], "employee": job.get("employee", ""),
                       "title": job.get("title", ""), "riskLevel": job.get("risk_level", "")}
                      for job in held[:10]],
        "staleAutomations": stale_reasons,
        "lastSweepAt": last_sweep_at,
        "lastVerdict": (last_verdict or {}).get("quality_verdict", ""),
        "lastVerdictAt": (last_verdict or {}).get("updated_at", ""),
    }


PII_PATTERNS = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit-card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")),
    ("date-of-birth", re.compile(r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b")),
    ("mrn", re.compile(r"\b(?:MRN|Medical Record(?: Number)?)\W{0,3}\d{4,}\b", re.I)),
]

HEDGE_WORDS = ["obviously", "clearly", "just", "simply", "very", "really", "actually", "basically"]
BANNED_ABSOLUTES = ["always", "never", "guaranteed", "100%", "everyone knows"]


def scan_sensitive_text(text: str) -> dict[str, Any]:
    """Find likely PII/PHI in free text.

    Deliberately conservative and pattern-based: it runs locally with no model call, so it can be
    used as a hard gate before anything leaves. It reports *candidates* — the caller decides. A
    miss is possible, so this raises the floor rather than replacing human review, and the response
    says so via `advisory`.
    """
    findings: list[dict[str, Any]] = []
    for label, pattern in PII_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = match.group(0)
            findings.append({
                "type": label,
                "excerpt": value[:4] + "…" if len(value) > 4 else value,
                "offset": match.start(),
            })
    by_type: dict[str, int] = {}
    for item in findings:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
    return {
        "findings": findings[:100],
        "count": len(findings),
        "byType": by_type,
        "redactionRequired": bool(findings),
        "advisory": "Pattern-based scan. Treat as a floor, not a guarantee — review before sending.",
    }


def redact_sensitive_text(text: str) -> str:
    """Replace detected PII/PHI spans with a typed placeholder."""
    out = text or ""
    for label, pattern in PII_PATTERNS:
        out = pattern.sub(f"[REDACTED-{label.upper()}]", out)
    return out


def audit_content(text: str, *, audience: str = "", brand_voice: str = "") -> dict[str, Any]:
    """Content-quality + brand-voice pass.

    Returns concrete, located findings rather than a bare score, so the caller can act on each one.
    The score is derived from the findings — it is never invented independently of them.
    """
    body = (text or "").strip()
    words = body.split()
    sentences = [s for s in re.split(r"[.!?]+", body) if s.strip()]
    word_count = len(words)
    avg_sentence = round(word_count / len(sentences), 1) if sentences else 0.0
    findings: list[dict[str, str]] = []

    if not body:
        return {
            "ok": False,
            "error": "text is required",
            "score": 0,
            "verdict": "hold",
            "findings": [{"kind": "empty", "detail": "No text supplied."}],
        }

    lowered = body.lower()
    for word in HEDGE_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            findings.append({"kind": "hedge", "detail": f"Filler or hedging word: '{word}'."})
    for phrase in BANNED_ABSOLUTES:
        if phrase in lowered:
            findings.append({"kind": "absolute", "detail": f"Unqualified absolute: '{phrase}'."})
    if avg_sentence > 28:
        findings.append({"kind": "readability",
                         "detail": f"Average sentence length is {avg_sentence} words; aim under 25."})
    if word_count > 400 and audience.lower() in {"email", "teams", "chat"}:
        findings.append({"kind": "length",
                         "detail": f"{word_count} words is long for {audience}; consider trimming."})
    if re.search(r"\b(TODO|TBD|FIXME|XXX|\[insert)\b", body, re.I):
        findings.append({"kind": "placeholder", "detail": "Unfilled placeholder text is still present."})
    if re.search(r"\bhttps?://\S*(example\.com|localhost)\S*", body, re.I):
        findings.append({"kind": "placeholder", "detail": "Placeholder URL is still present."})
    if brand_voice and brand_voice.lower() not in {"", "default"}:
        if brand_voice.lower() == "formal" and re.search(r"\b(gonna|wanna|kinda|hey there)\b", lowered):
            findings.append({"kind": "voice", "detail": "Informal phrasing in a formal brand voice."})
        if brand_voice.lower() == "plain" and avg_sentence > 20:
            findings.append({"kind": "voice", "detail": "Sentences run long for a plain-language voice."})

    sensitive = scan_sensitive_text(body)
    if sensitive["redactionRequired"]:
        findings.append({"kind": "sensitive",
                         "detail": f"{sensitive['count']} possible PII/PHI item(s) detected."})

    # Score is a function of what was actually found, weighted by severity.
    weights = {"sensitive": 4, "placeholder": 3, "absolute": 2, "voice": 2,
               "readability": 1, "length": 1, "hedge": 1}
    penalty = sum(weights.get(f["kind"], 1) for f in findings)
    score = max(0, 10 - penalty)
    verdict = "pass" if score >= 9 and not sensitive["redactionRequired"] else (
        "pass-with-notes" if score >= 7 and not sensitive["redactionRequired"] else "hold")
    return {
        "ok": True,
        "score": score,
        "verdict": verdict,
        "findings": findings,
        "metrics": {"words": word_count, "sentences": len(sentences),
                    "avgSentenceWords": avg_sentence},
        "sensitive": sensitive,
        "brandVoice": brand_voice,
        "audience": audience,
    }


SKILL_REQUIRED_SECTIONS = ["purpose", "when to use", "steps", "safety"]


def lint_skill_text(text: str, *, name: str = "") -> dict[str, Any]:
    """Skill-authoring checks for a SKILL.md body.

    Checks the things that actually break a Scout skill: missing or malformed frontmatter, a
    missing name/description, no trigger guidance, and unbounded instructions.
    """
    body = text or ""
    lines = body.split("\n")
    issues: list[dict[str, str]] = []
    if not body.strip():
        return {"ok": False, "error": "text is required", "issues": [], "score": 0}

    has_frontmatter = lines[0].strip() == "---" and "---" in lines[1:]
    frontmatter = ""
    if has_frontmatter:
        end = lines.index("---", 1)
        frontmatter = "\n".join(lines[1:end])
    else:
        issues.append({"severity": "error",
                       "detail": "No YAML frontmatter. A Scout skill must open with a '---' block."})

    if has_frontmatter:
        if not re.search(r"^name:\s*\S", frontmatter, re.M):
            issues.append({"severity": "error", "detail": "Frontmatter is missing 'name:'."})
        if not re.search(r"^description:\s*\S", frontmatter, re.M):
            issues.append({"severity": "error",
                           "detail": "Frontmatter is missing 'description:' — Scout uses it to decide when to load the skill."})
        else:
            desc = re.search(r"^description:\s*\"?(.+?)\"?$", frontmatter, re.M)
            if desc and len(desc.group(1)) < 40:
                issues.append({"severity": "warn",
                               "detail": "description is very short; Scout matches on it, so be specific about when to use this skill."})

    headings = [ln.strip("# ").strip().lower() for ln in lines if ln.startswith("#")]
    joined = " ".join(headings)
    for section in SKILL_REQUIRED_SECTIONS:
        if section not in joined and section not in body.lower():
            issues.append({"severity": "warn",
                           "detail": f"No '{section}' guidance found. Skills without it are applied inconsistently."})
    if len(body) > 60000:
        issues.append({"severity": "warn",
                       "detail": f"{len(body)} characters is very long; consider splitting into focused skills."})
    if not re.search(r"\b(never|do not|don't|must not)\b", body, re.I):
        issues.append({"severity": "warn",
                       "detail": "No explicit prohibitions. State what the skill must not do."})

    errors = sum(1 for i in issues if i["severity"] == "error")
    warns = sum(1 for i in issues if i["severity"] == "warn")
    score = max(0, 10 - (errors * 4) - warns)
    return {"ok": True, "name": name, "score": score, "issues": issues,
            "errors": errors, "warnings": warns,
            "verdict": "pass" if errors == 0 and warns <= 1 else ("pass-with-notes" if errors == 0 else "hold")}


def format_list_rows(rows_in: list[Any], *, columns: list[str] | None = None) -> dict[str, Any]:
    """Normalize tabular rows into a consistent, typed shape.

    Accepts a list of dicts or a list of lists. Trims whitespace, unifies the column set, coerces
    obvious numbers and booleans, and reports what it changed so nothing is silently rewritten.
    """
    if not isinstance(rows_in, list) or not rows_in:
        return {"ok": False, "error": "rows must be a non-empty array"}

    normalized: list[dict[str, Any]] = []
    if isinstance(rows_in[0], list):
        header = [str(c).strip() for c in (columns or rows_in[0])]
        data_rows = rows_in if columns else rows_in[1:]
        for row in data_rows:
            if not isinstance(row, list):
                continue
            normalized.append({header[i] if i < len(header) else f"column{i+1}": row[i]
                               for i in range(len(row))})
    else:
        for row in rows_in:
            if isinstance(row, dict):
                normalized.append(dict(row))

    if not normalized:
        return {"ok": False, "error": "no usable rows found"}

    all_columns: list[str] = []
    for row in normalized:
        for key in row:
            clean = str(key).strip()
            if clean and clean not in all_columns:
                all_columns.append(clean)
    if columns:
        all_columns = [str(c).strip() for c in columns if str(c).strip()] or all_columns

    def coerce(value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.lower() in {"true", "yes"}:
                return True
            if text.lower() in {"false", "no"}:
                return False
            if re.fullmatch(r"-?\d+", text):
                return int(text)
            if re.fullmatch(r"-?\d*\.\d+", text):
                return float(text)
            return text
        return value

    out_rows: list[dict[str, Any]] = []
    blanks = 0
    for row in normalized:
        clean_row: dict[str, Any] = {}
        stripped = {str(k).strip(): v for k, v in row.items()}
        for column in all_columns:
            if column in stripped:
                clean_row[column] = coerce(stripped[column])
            else:
                clean_row[column] = ""
                blanks += 1
        out_rows.append(clean_row)

    types: dict[str, str] = {}
    for column in all_columns:
        values = [r[column] for r in out_rows if r[column] not in ("", None)]
        if values and all(isinstance(v, bool) for v in values):
            types[column] = "boolean"
        elif values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            types[column] = "number"
        elif values and all(isinstance(v, str) and parse_timestamp(v) for v in values):
            types[column] = "date"
        else:
            types[column] = "text"
    return {"ok": True, "columns": all_columns, "columnTypes": types,
            "rows": out_rows[:1000], "rowCount": len(out_rows),
            "filledBlanks": blanks,
            "truncated": len(out_rows) > 1000}


def document_flow(spec: Any) -> dict[str, Any]:
    """Summarize an automation/flow definition into human-readable documentation.

    Works on a Power Automate style export or any nested dict of actions: it walks the structure and
    reports triggers, actions, and connectors rather than requiring a specific schema.
    """
    if not isinstance(spec, dict):
        return {"ok": False, "error": "flow must be a JSON object"}

    triggers: list[dict[str, str]] = []
    actions: list[dict[str, str]] = []
    connectors: set[str] = set()

    def walk(node: Any, path: str = "", depth: int = 0) -> None:
        if depth > 12 or not isinstance(node, dict):
            return
        for key, value in node.items():
            if not isinstance(value, dict):
                continue
            lowered = key.lower()
            kind = str(value.get("type", "")).strip()
            if lowered in {"triggers", "trigger"}:
                for tname, tval in value.items():
                    if isinstance(tval, dict):
                        triggers.append({"name": tname, "type": str(tval.get("type", "unknown"))})
                    else:
                        triggers.append({"name": tname, "type": "unknown"})
                continue
            if lowered == "actions":
                walk(value, path, depth + 1)
                continue
            if kind:
                actions.append({"name": key, "type": kind, "path": path or "root"})
                host = value.get("inputs", {})
                if isinstance(host, dict):
                    api = host.get("host", {})
                    if isinstance(api, dict):
                        conn = api.get("connectionName") or api.get("apiId") or api.get("connection")
                        if conn:
                            connectors.add(str(conn).split("/")[-1])
            walk(value, f"{path}/{key}" if path else key, depth + 1)

    walk(spec)
    summary_bits = []
    if triggers:
        summary_bits.append(f"Triggered by {', '.join(t['name'] for t in triggers[:3])}")
    if actions:
        summary_bits.append(f"{len(actions)} action(s)")
    if connectors:
        summary_bits.append(f"connectors: {', '.join(sorted(connectors)[:6])}")
    return {
        "ok": True,
        "name": str(spec.get("name", "")) or "(unnamed flow)",
        "triggers": triggers,
        "actions": actions[:200],
        "actionCount": len(actions),
        "connectors": sorted(connectors),
        "summary": "; ".join(summary_bits) or "No triggers or actions found in this definition.",
    }


def build_chart_spec(data: dict[str, Any]) -> dict[str, Any]:
    """Turn tabular input into a renderer-agnostic chart spec.

    Picks a chart type from the shape of the data when the caller doesn't specify one, and refuses
    combinations that would produce a misleading chart rather than emitting something unreadable.
    """
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        return {"ok": False, "error": "rows must be a non-empty array"}
    formatted = format_list_rows(raw_rows, columns=data.get("columns") or None)
    if not formatted.get("ok"):
        return formatted

    columns = formatted["columns"]
    types = formatted["columnTypes"]
    rows_out = formatted["rows"]
    x = str(data.get("x", "")).strip()
    series = data.get("series") or []
    if isinstance(series, str):
        series = [series]
    series = [str(s).strip() for s in series if str(s).strip()]

    if not x:
        x = next((c for c in columns if types[c] in {"date", "text"}), columns[0])
    if not series:
        series = [c for c in columns if c != x and types[c] == "number"]
    if not series:
        return {"ok": False,
                "error": "no numeric column found to chart",
                "columns": columns, "columnTypes": types}

    requested = str(data.get("chartType", "")).strip().lower()
    if requested:
        chart_type = requested
    elif types.get(x) == "date":
        chart_type = "line"
    elif len(rows_out) <= 8 and len(series) == 1:
        chart_type = "bar"
    else:
        chart_type = "bar"

    notes: list[str] = []
    if chart_type == "pie":
        if len(series) > 1:
            notes.append("Pie charts show one series; using the first and ignoring the rest.")
            series = series[:1]
        if len(rows_out) > 8:
            notes.append(f"{len(rows_out)} slices is hard to read; consider a bar chart.")
    if chart_type == "line" and types.get(x) not in {"date", "number"}:
        notes.append("A line chart implies an ordered x-axis; this column is text, so order may be arbitrary.")

    return {
        "ok": True,
        "chartType": chart_type,
        "x": {"column": x, "type": types.get(x, "text")},
        "series": [{"column": s, "type": types.get(s, "number")} for s in series],
        "data": [{"x": r.get(x, ""), **{s: r.get(s, 0) for s in series}} for r in rows_out[:500]],
        "rowCount": len(rows_out),
        "title": str(data.get("title", "")) or f"{', '.join(series)} by {x}",
        "notes": notes,
    }


def build_conference_pack(data: dict[str, Any]) -> dict[str, Any]:
    """Scaffold a conference submission: titles, abstract, objectives, takeaways, bio.

    This returns a *structured scaffold with explicit gaps*, not invented content. The employee
    filling it in supplies the substance; inventing a speaker bio or fake credentials here would be
    the wrong kind of helpful.
    """
    topic = str(data.get("topic", "")).strip()
    if not topic:
        return {"ok": False, "error": "topic is required"}
    audience = str(data.get("audience", "")).strip() or "a general technical audience"
    duration = str(data.get("duration", "")).strip() or "45 minutes"
    level = str(data.get("level", "")).strip() or "intermediate"
    speaker = str(data.get("speaker", "")).strip()
    takeaway_count = 3
    try:
        takeaway_count = max(2, min(6, int(data.get("takeaways", 3))))
    except (TypeError, ValueError):
        takeaway_count = 3

    return {
        "ok": True,
        "topic": topic,
        "titleOptions": [
            f"{topic}: What Actually Works",
            f"Getting {topic} Right — Lessons From Practice",
            f"{topic} in the Real World",
        ],
        "abstractScaffold": {
            "hook": f"[Open with the concrete problem {audience} hit with {topic}.]",
            "context": f"[Why {topic} matters now — 1-2 sentences.]",
            "body": f"[What you did, what happened, what you learned. Aim for {duration}.]",
            "outcome": "[The measurable result or the honest non-result.]",
            "callToAction": "[What you want the audience to do next.]",
        },
        "learningObjectives": [
            f"[Objective 1 — what the audience will be able to DO with {topic}.]",
            "[Objective 2 — a decision they'll be able to make.]",
            "[Objective 3 — a mistake they'll be able to avoid.]",
        ],
        "takeaways": [f"[Takeaway {i + 1}]" for i in range(takeaway_count)],
        "audience": audience,
        "level": level,
        "duration": duration,
        "speakerBioScaffold": (
            f"[{speaker}: role, the specific experience that makes this talk credible, "
            "and one human detail. Keep to 60-80 words.]" if speaker
            else "[Speaker name, role, the experience that makes this talk credible, 60-80 words.]"
        ),
        "gaps": [
            "Abstract body needs your real experience — the scaffold marks where.",
            "Learning objectives must be specific and verifiable, not aspirational.",
            "Bio must be factual. Do not invent credentials.",
        ],
        "advisory": "This is a scaffold, not finished copy. Every [bracket] needs a human answer.",
    }


def build_talk_track(data: dict[str, Any]) -> dict[str, Any]:
    """Build presenter notes for a deck: per-slide talking points, timing, and transitions."""
    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        return {"ok": False, "error": "slides must be a non-empty array"}
    try:
        total_minutes = max(1, min(600, int(data.get("durationMinutes", 20))))
    except (TypeError, ValueError):
        total_minutes = 20

    cleaned: list[dict[str, Any]] = []
    for index, slide in enumerate(slides):
        if isinstance(slide, str):
            cleaned.append({"title": slide.strip(), "points": []})
        elif isinstance(slide, dict):
            points = slide.get("points") or slide.get("bullets") or []
            if isinstance(points, str):
                points = [points]
            cleaned.append({
                "title": str(slide.get("title", "") or f"Slide {index + 1}").strip(),
                "points": [str(p).strip() for p in points if str(p).strip()][:12],
            })
    if not cleaned:
        return {"ok": False, "error": "no usable slides found"}

    # Opening and closing slides get a little more room; the middle splits the remainder evenly.
    # Weights are normalized so the allocation always sums to the requested duration — a track that
    # quietly runs 17% long is worse than no timing at all.
    count = len(cleaned)
    weights = []
    for index in range(count):
        if count >= 3 and index == 0:
            weights.append(1.3)
        elif count >= 3 and index == count - 1:
            weights.append(1.2)
        else:
            weights.append(1.0)
    weight_total = sum(weights)
    track: list[dict[str, Any]] = []
    for index, slide in enumerate(cleaned):
        minutes = round(total_minutes * weights[index] / weight_total, 1)
        if index == 0:
            transition = "Open: state the problem before the agenda."
        elif index == count - 1:
            transition = "Close: restate the single thing you want remembered, then the ask."
        else:
            transition = f"Bridge from '{cleaned[index - 1]['title']}' to this point."
        track.append({
            "slide": index + 1,
            "title": slide["title"],
            "minutes": minutes,
            "talkingPoints": slide["points"] or ["[No bullets supplied — add the point this slide makes.]"],
            "transition": transition,
            "cue": "[Pause for questions.]" if minutes >= 3 else "",
        })
    allocated = round(sum(item["minutes"] for item in track), 1)
    return {
        "ok": True,
        "slideCount": count,
        "durationMinutes": total_minutes,
        "allocatedMinutes": allocated,
        "overUnder": round(allocated - total_minutes, 1),
        "track": track,
        "advisory": "Timings are a starting split. Rehearse and adjust — the middle slides usually run long.",
    }


def runtime_inventory() -> dict[str, Any]:
    """Snapshot of what this install can actually do right now.

    Reports only what it can verify from disk and process state — no capability is claimed on the
    basis of it being 'supposed to' be there.
    """
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root_name in (".scout", ".copilot", ".copilot-cloud", ".copilot-dev"):
        skills_dir = Path.home() / root_name / "m-skills"
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            skill_file = entry / "SKILL.md"
            if entry.is_dir() and skill_file.is_file() and entry.name not in seen:
                seen.add(entry.name)
                skills.append({
                    "name": entry.name,
                    "root": root_name,
                    "bytes": skill_file.stat().st_size,
                })
    package_skills = []
    package_dir = APP_ROOT.parent / "skills"
    if package_dir.is_dir():
        package_skills = sorted(p.name for p in package_dir.iterdir()
                                if p.is_dir() and (p / "SKILL.md").is_file())
    automation_names: list[str] = []
    automations_file = APP_ROOT.parent / "automations" / "automations.json"
    if automations_file.is_file():
        try:
            payload = json.loads(automations_file.read_text(encoding="utf-8-sig"))
            automation_names = [str(a.get("name", "")) for a in payload.get("automations", [])]
        except (json.JSONDecodeError, OSError):
            automation_names = []
    return {
        "ok": True,
        "serverTime": utc_now(),
        "app": {
            "version": APP_VERSION,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "host": HOST,
            "authRequired": AUTH_REQUIRED,
        },
        "installedSkills": skills,
        "installedSkillCount": len(skills),
        "packageSkills": package_skills,
        "automations": automation_names,
        "automationCount": len(automation_names),
        "capabilities": sorted(CAPABILITY_ENDPOINTS),
        "advisory": "Reflects what is on disk now. Scout's own tool list is not visible from here.",
    }


CAPABILITY_ENDPOINTS = [
    "/api/runtime-inventory",
    "/api/skill-lint",
    "/api/format-list",
    "/api/document-flow",
    "/api/content-pass",
    "/api/conference-pack",
    "/api/chart-spec",
    "/api/talk-track",
]

# The POST subset. /api/runtime-inventory is a GET, so it is deliberately not in this set.
CAPABILITY_POST_PATHS = frozenset(CAPABILITY_ENDPOINTS) - {"/api/runtime-inventory"}


def capability_summary(db: sqlite3.Connection) -> dict[str, Any]:
    """Counts of the Phase 3 payloads attached to jobs, for the dashboard."""
    try:
        row = db.execute(
            """
            SELECT
              SUM(CASE WHEN quality_audit_json != '{}' THEN 1 ELSE 0 END)    AS audits,
              SUM(CASE WHEN talk_track_json != '{}' THEN 1 ELSE 0 END)       AS talkTracks,
              SUM(CASE WHEN conference_pack_json != '{}' THEN 1 ELSE 0 END)  AS conferencePacks,
              SUM(CASE WHEN chart_spec_json != '{}' THEN 1 ELSE 0 END)       AS chartSpecs,
              SUM(CASE WHEN flow_doc_json != '{}' THEN 1 ELSE 0 END)         AS flowDocs,
              SUM(CASE WHEN redaction_required = 1 THEN 1 ELSE 0 END)        AS redactionRequired,
              SUM(CASE WHEN redaction_required = 1 AND redaction_applied = 0 THEN 1 ELSE 0 END)
                                                                             AS redactionPending
            FROM jobs
            """
        ).fetchone()
    except sqlite3.OperationalError:
        # Older DB that has not been migrated yet. Say so rather than reporting zeroes, which
        # would read as "nothing to do" when it actually means "cannot see".
        return {"readable": False}
    return {
        "readable": True,
        "contentAudits": int(row["audits"] or 0),
        "talkTracks": int(row["talkTracks"] or 0),
        "conferencePacks": int(row["conferencePacks"] or 0),
        "chartSpecs": int(row["chartSpecs"] or 0),
        "flowDocs": int(row["flowDocs"] or 0),
        "redactionRequired": int(row["redactionRequired"] or 0),
        "redactionPending": int(row["redactionPending"] or 0),
    }


def get_state(since: str = "") -> dict[str, Any]:
    """Full dashboard state.

    `since` is an optional ISO-8601 timestamp: when supplied, events / today's activity / work-ledger
    entries and completed jobs are filtered to what changed after it, so a polling client can ask for
    a delta instead of re-reading the whole history. Without it, history is capped (events 500,
    completed jobs 200) — active, queued, in-progress and blocked jobs are always returned in full.
    """
    since_dt = parse_timestamp(since) if since else None
    with connect() as db:
        expire_time_bound_approvals(db)
        requeue_stale_jobs(db)
        dedupe_pending_by_content(db)
        employees = rows(db.execute(
            "SELECT * FROM employees WHERE status != 'removed' ORDER BY rowid"
        ))
        # v3.3.9: live per-employee work status from real jobs they own, so the roster reflects what's
        # actually happening (working / blocked / paused) instead of every card reading "ready".
        active_by_emp: dict[str, int] = {}
        blocked_by_emp: dict[str, int] = {}
        for r in db.execute(
            "SELECT employee, status, COUNT(*) n FROM jobs "
            "WHERE status IN ('queued','in_progress','blocked') GROUP BY employee, status"
        ):
            if r["status"] == "in_progress":
                active_by_emp[r["employee"]] = active_by_emp.get(r["employee"], 0) + r["n"]
            elif r["status"] == "blocked":
                blocked_by_emp[r["employee"]] = blocked_by_emp.get(r["employee"], 0) + r["n"]
        for emp in employees:
            level = emp.get("trust_level", "draft")
            name = emp.get("name", "")
            # Always derive the protocol live from (role x level) so the card can never lie.
            emp["protocol"] = derive_protocol(name, level)
            emp["enabled"] = bool(emp.get("enabled", 1))
            emp["trustLabel"] = TRUST_LABELS.get(level, "")
            emp["mode"] = employee_mode(name)
            emp["note"] = employee_note(name)
            emp["levelOptions"] = TRUST_LEVELS
            emp["origin"] = emp.get("origin") or "builtin"
            emp["status"] = emp.get("status") or "active"
            emp["removable"] = name != "Major"
            emp["skills"] = decode_json_list(emp.get("skills_json"))
            # Live work status for the roster card: paused (off) > working (owns queued/in_progress) >
            # blocked (owns a blocked job) > ready. Lifecycle states (onboarding/review) pass through.
            if emp["status"] in ("onboarding", "review"):
                emp["workStatus"] = emp["status"]
            elif not emp["enabled"]:
                emp["workStatus"] = "paused"
            elif active_by_emp.get(name):
                emp["workStatus"] = "working"
            elif blocked_by_emp.get(name):
                emp["workStatus"] = "blocked"
            else:
                emp["workStatus"] = "ready"
            # Onboarding/review employees expose their editable proposal fields (for the review UI);
            # the raw operating brain (source_text) always stays server-side. Active employees stay lean.
            if emp["status"] in ("onboarding", "review"):
                emp["internal"] = emp.get("internal_verb") or ""
                emp["outward"] = emp.get("outward_verb") or ""
                emp["always"] = decode_json_list(emp.get("always_json"))
            for k in ("source_text", "always_json", "skills_json", "internal_verb", "outward_verb"):
                emp.pop(k, None)
        removed_employees = rows(db.execute(
            "SELECT name, role, origin, detail FROM employees WHERE status = 'removed' ORDER BY rowid"
        ))
        approvals = rows(db.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at DESC"))
        # Attach a "view source" link (Outlook/Teams/calendar deep link) when Major captured one.
        for ap in approvals:
            try:
                ap_details = json.loads(ap.get("details_json") or "{}")
            except Exception:
                ap_details = {}
            link = approval_source_link(ap.get("action_type", ""), ap_details if isinstance(ap_details, dict) else {})
            ap["sourceUrl"] = link.get("url", "")
            ap["sourceLabel"] = link.get("label", "")
        jobs = rows(db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1000"))
        # P1-E: keep every live job (queued / in_progress / blocked) but cap terminal history so a
        # long-lived install does not grow an unbounded /api/state payload. The impact ledger below
        # is still built from the untrimmed rows so capping never changes what it reports.
        all_jobs = list(jobs)
        live_ids = {job["id"] for job in jobs if job["status"] in ("queued", "in_progress", "blocked")}
        live_jobs = [job for job in jobs if job["id"] in live_ids]
        terminal_jobs = [job for job in jobs if job["id"] not in live_ids]
        if since_dt is not None:
            terminal_jobs = [job for job in terminal_jobs if _after(job.get("updated_at") or job.get("created_at"), since_dt)]
        else:
            terminal_jobs = terminal_jobs[:200]
        jobs = sorted(live_jobs + terminal_jobs, key=lambda job: str(job.get("created_at") or ""), reverse=True)
        threads = rows(db.execute("SELECT * FROM chat_threads ORDER BY updated_at DESC LIMIT 50"))
        messages = rows(db.execute("SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT 200"))
        # Reflect each linked job's live status on its chat message so a message no longer shows a
        # stale "queued"/"in progress" after the work has finished. Read-only (display) reconciliation.
        _job_status_by_id = {j["id"]: j.get("status") for j in jobs}
        for _m in messages:
            _jid = _m.get("job_id")
            if _jid and _job_status_by_id.get(_jid):
                _m["status"] = _job_status_by_id[_jid]
        events = rows(db.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT 1000"))
        work_entries = rows(db.execute("SELECT * FROM work_ledger_entries WHERE status = 'active' ORDER BY occurred_at DESC LIMIT 1000"))
        all_events = list(events)
        all_work_entries = list(work_entries)
        if since_dt is not None:
            events = [row for row in events if _after(row.get("created_at"), since_dt)]
            work_entries = [row for row in work_entries if _after(row.get("occurred_at") or row.get("created_at"), since_dt)]
        else:
            events = events[:500]
        inbox_signals = rows(db.execute("SELECT * FROM inbox_signals WHERE status = 'active' ORDER BY received_at DESC, updated_at DESC LIMIT 25"))
        today = local_date_key()
        today_activity = [event for event in events if event_date(event["created_at"]) == today]
        impact_ledger = build_impact_ledger(approvals, all_jobs, all_events, all_work_entries)
        recent_sweep_runs = recent_sweeps(db, 20)
        sweep_summary = sweep_stats(db)
        work_today = work_ledger_today(db)
        _trim_terminal_job_instructions(jobs)
        metrics = {
            "pendingApprovals": len(approvals),
            "activeJobs": len([job for job in all_jobs if job["status"] in ("queued", "in_progress")]),
            "blockedJobs": len([job for job in all_jobs if job["status"] == "blocked"]),
            "completedJobs": len([job for job in all_jobs if job["status"] in ("completed", "done")]),
        }
        return {
            "metrics": metrics,
            "employees": employees,
            "removedEmployees": removed_employees,
            "approvals": approvals,
            "jobs": jobs,
            "threads": threads,
            "messages": messages,
            "events": events,
            "inboxSignals": inbox_signals,
            "todayActivity": today_activity,
            "impactLedger": impact_ledger,
            "recentSweeps": recent_sweep_runs,
            "sweepStats": sweep_summary,
            "workLedgerToday": work_today,
            "automationHealth": get_automation_health(),
            "operatingLoop": OPERATING_LOOP,
            "decisionMemory": decision_memory_summary(db),
            "guardrails": build_guardrails(db),
            "skillUsage": build_skill_usage(db),
            "knowledgeSummary": knowledge_summary(db),
            "qualitySummary": quality_summary(db),
            "capabilitySummary": capability_summary(db),
            "since": since if since_dt is not None else "",
            "serverTime": utc_now(),
        }


def get_activity_log() -> dict[str, Any]:
    with connect() as db:
        events = rows(db.execute("SELECT * FROM events ORDER BY created_at DESC"))
        return {
            "events": events,
            "totalEvents": len(events),
            "serverTime": utc_now(),
        }


def get_impact_ledger() -> dict[str, Any]:
    with connect() as db:
        approvals = rows(db.execute("SELECT * FROM approvals ORDER BY created_at DESC"))
        jobs = rows(db.execute("SELECT * FROM jobs ORDER BY created_at DESC"))
        events = rows(db.execute("SELECT * FROM events ORDER BY created_at DESC"))
        work_entries = rows(db.execute("SELECT * FROM work_ledger_entries WHERE status = 'active' ORDER BY occurred_at DESC"))
        ledger = build_impact_ledger(approvals, jobs, events, work_entries)
        return {
            **ledger,
            "totalEntries": len([item for item in ledger["highlights"] if item["reportable"]]),
            "ripple": build_ripple(db),
            "cadences": build_cadences(db),
            "careerProfile": get_career_profile(db),
            "coverage": build_coverage(db),
            "serverTime": utc_now(),
        }


def import_legacy_ledger(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    ledger = json.loads(path.read_text(encoding="utf-8-sig"))
    with connect() as db:
        for approval in ledger.get("approvals", []):
            db.execute(
                "INSERT OR IGNORE INTO approvals(id, created_at, updated_at, employee, action_type, risk, title, preview, destination, status, details_json, user_guidance) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.get("id") or new_id("approval"),
                    approval.get("createdAt") or utc_now(),
                    approval.get("updatedAt") or approval.get("createdAt") or utc_now(),
                    approval.get("employee") or "Major",
                    approval.get("actionType") or "other",
                    approval.get("risk") or "medium",
                    approval.get("title") or "Approval needed",
                    approval.get("preview") or "",
                    approval.get("destination") or approval.get("recipientOrDestination") or "",
                    approval.get("status") or "pending",
                    json.dumps(approval.get("details") or {}),
                    approval.get("userGuidance") or "",
                ),
            )
        for action in ledger.get("actionQueue", []):
            thread_id = action.get("threadId")
            employee = action.get("employee") or "Major"
            if thread_id:
                db.execute(
                    "INSERT OR IGNORE INTO chat_threads(id, created_at, updated_at, employee, title, status) VALUES(?, ?, ?, ?, ?, 'open')",
                    (
                        thread_id,
                        action.get("createdAt") or utc_now(),
                        action.get("updatedAt") or action.get("createdAt") or utc_now(),
                        employee,
                        action.get("title") or action.get("subject") or "Imported chat thread",
                    ),
                )
            db.execute(
                "INSERT OR IGNORE INTO jobs(id, created_at, updated_at, started_at, completed_at, employee, type, title, status, priority, source, thread_id, user_message_id, instructions, result_summary, blocker) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    action.get("id") or new_id("job"),
                    action.get("createdAt") or utc_now(),
                    action.get("updatedAt") or action.get("completedAt") or action.get("createdAt") or utc_now(),
                    action.get("startedAt"),
                    action.get("completedAt"),
                    employee,
                    action.get("type") or "employee-work",
                    action.get("title") or action.get("subject") or "Queued work",
                    action.get("status") or "queued",
                    action.get("priority") or "normal",
                    action.get("source") or action.get("decision") or "",
                    thread_id,
                    action.get("chatMessageId"),
                    action.get("about") or action.get("userGuidance") or "",
                    action.get("outcomeSummary") or "",
                    action.get("error") or action.get("blocker") or "",
                ),
            )
        for message in ledger.get("chatMessages", []):
            thread_id = message.get("threadId") or message.get("relatedActionId") or new_id("thread")
            employee = message.get("employee") or "Major"
            job_id = message.get("relatedActionId")
            if job_id and not db.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
                job_id = None
            db.execute(
                "INSERT OR IGNORE INTO chat_threads(id, created_at, updated_at, employee, title, status) VALUES(?, ?, ?, ?, ?, 'open')",
                (thread_id, message.get("createdAt") or utc_now(), message.get("createdAt") or utc_now(), employee, "Imported chat thread"),
            )
            db.execute(
                "INSERT OR IGNORE INTO chat_messages(id, created_at, thread_id, employee, sender, message, status, job_id, link_json) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.get("id") or new_id("msg"),
                    message.get("createdAt") or utc_now(),
                    thread_id,
                    employee,
                    message.get("sender") or "system",
                    message.get("message") or "",
                    message.get("status") or "sent",
                    job_id,
                    json.dumps(message.get("link") or ""),
                ),
            )
        for event in ledger.get("events", []):
            db.execute(
                "INSERT OR IGNORE INTO events(id, created_at, employee, summary, detail, sensitivity, status) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    event.get("id") or new_id("event"),
                    event.get("timestamp") or event.get("createdAt") or utc_now(),
                    event.get("employee") or "Daily Flow",
                    event.get("summary") or event.get("title") or "",
                    event.get("detail") or "",
                    event.get("sensitivity") or "private",
                    event.get("status") or "logged",
                ),
            )
        touch_version(db)


APPROVAL_TITLE_PREFIXES = (
    "Inbox calendar decision needed:",
    "Inbox email review needed:",
    "Teams review needed:",
    "Meeting prep gap:",
    "Follow-up commitment detected:",
    "Blocked work needs decision:",
    "Outbound draft ready for review:",
    "Customer research opportunity:",
    "Impact highlight candidate:",
    "Stale thread needs attention:",
    "Attachment/document review needed:",
    "Review needed:",
)


def build_export_zip() -> bytes:
    """Everything the user owns, in one file: every table as JSON plus every document under the
    OneDrive document root. Nothing leaves the machine — this is what the browser downloads."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        with connect() as db:
            tables = [
                r["name"] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table in tables:
                try:
                    payload = rows(db.execute(f"SELECT * FROM {table}"))  # table names come from sqlite_master
                except sqlite3.Error:
                    continue
                archive.writestr(f"database/{table}.json", json.dumps(payload, indent=2, default=str))
            archive.writestr(
                "export-manifest.json",
                json.dumps(
                    {
                        "version": APP_VERSION,
                        "exportedAt": utc_now(),
                        "timezone": APP_TIMEZONE_NAME,
                        "documentRoot": str(ONEDRIVE_DOCUMENT_ROOT),
                        "tables": tables,
                    },
                    indent=2,
                ),
            )
        root = ONEDRIVE_DOCUMENT_ROOT
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    archive.write(path, f"documents/{path.relative_to(root).as_posix()}")
                except Exception:
                    continue  # a locked or syncing OneDrive file must not fail the whole export
    return buffer.getvalue()


# Tables wiped by POST /api/reset. Config, the employee roster, and the career profile are
# deliberately NOT in this list: resetting private data should leave a working, configured team.
RESETTABLE_TABLES = [
    "jobs",
    "approvals",
    "inbox_signals",
    "events",
    "work_ledger_entries",
    "chat_threads",
    "chat_messages",
    "decision_memory",
    "sweep_runs",
    "knowledge_entries",
]

# BEFORE DELETE triggers normally enforce "history is preserved forever" so a sweep can never quietly
# drop the user's record. An explicit user-initiated reset is the one sanctioned exception, so it
# drops them for the duration of the wipe and init_db() puts them straight back.
RETENTION_TRIGGERS = [
    "preserve_approvals_delete",
    "preserve_inbox_signals_delete",
    "preserve_jobs_delete",
    "preserve_chat_threads_delete",
    "preserve_chat_messages_delete",
    "preserve_events_delete",
    "preserve_work_ledger_entries_delete",
]


def reset_private_data() -> dict[str, Any]:
    """Delete every row of user-generated data, keeping config, employees, and the career profile."""
    cleared: dict[str, int] = {}
    db = connect()
    try:
        counts = {}
        for table in RESETTABLE_TABLES:
            try:
                counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                continue  # table not present on this install
        # Schema changes need a connection with nothing else in flight, so the counts above are
        # fully read before any DROP TRIGGER runs.
        db.execute("BEGIN IMMEDIATE")
        for trigger in RETENTION_TRIGGERS:
            db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in counts:
            db.execute(f"DELETE FROM {table}")
            cleared[table] = counts[table]
        db.commit()
    finally:
        db.close()
    init_db()  # restores the retention triggers and re-seeds the roster metadata
    log = connect()
    try:
        detail = ", ".join(f"{name} ({count})" for name, count in cleared.items() if count) or "Nothing to clear."
        add_event(log, "You", "User reset all private data", detail)
        log.commit()
    finally:
        log.close()  # close before checkpointing: an open reader blocks a TRUNCATE checkpoint
    _checkpoint_database()
    return {"ok": True, "cleared": cleared}


def clean_approval_title(title: str) -> str:
    """Strip the internal 'Inbox ... needed:' card-title prefix so the Activity Log names
    the actual item (meeting/email/thread), not the dashboard's routing label."""
    text = str(title or "").strip()
    for prefix in APPROVAL_TITLE_PREFIXES:
        if text.lower().startswith(prefix.lower()):
            return text[len(prefix):].strip() or text
    return text


def approval_decision_log(action_type: str, decision: str, title: str) -> str:
    """Human-readable Activity Log line for a user's approval decision, naming the item —
    so the log records 'You accepted meeting: <name>' instead of a raw approval GUID."""
    name = clean_approval_title(title)
    at = (action_type or "").lower()
    if at == "calendar":
        verb = {
            "accept": "You accepted meeting",
            "tentative": "You RSVP'd tentative for meeting",
            "follow": "You are following meeting (no RSVP sent, invite kept for monitoring)",
            "decline": "You declined meeting",
        }.get(decision, "You updated meeting")
        return f"{verb}: {name}"
    if at == "email":
        verb = {
            "approved": "You approved — sending an email reply for",
            "rejected": "You rejected an email — removing it from your Inbox",
            "deferred": "You dismissed an email review item",
        }.get(decision, "You updated an email review")
        return f"{verb}: {name}"
    if at == "teams":
        verb = {
            "approved": "You approved — sending a Teams reply for",
            "rejected": "You dismissed a Teams message",
            "deferred": "You dismissed a Teams message",
        }.get(decision, "You updated a Teams review")
        return f"{verb}: {name}"
    if at == "attachment-review":
        verb = {
            "approved": "You approved — Quinn is reviewing the attachment/document for",
            "rejected": "You rejected an attachment review — removing the email from your Inbox",
            "deferred": "You dismissed an attachment review item",
        }.get(decision, "You updated an attachment review")
        return f"{verb}: {name}"
    verb = {"approved": "You approved — preparing", "rejected": "You skipped", "deferred": "You snoozed"}.get(decision, "You updated")
    return f"{verb}: {name}"


class Handler(BaseHTTPRequestHandler):
    server_version = f"DailyFlowApp/{APP_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        if LOG_REQUESTS:
            print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BYTES:
            # Refuse before reading a byte so a huge body cannot pin memory (P1-C).
            raise RequestTooLarge(f"request body exceeds {MAX_REQUEST_BYTES} bytes")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- local auth / CSRF guards -------------------------------------------------------------

    def presented_token(self) -> str:
        header = self.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        from_header = (self.headers.get("X-Local-Token") or "").strip()
        if from_header:
            return from_header
        # EventSource cannot set request headers, so the SSE stream (and only the SSE stream) also
        # accepts ?token=. It is a same-origin localhost URL, so it never leaves the machine.
        parsed = urlparse(self.path)
        if parsed.path == "/api/events":
            return parse_qs(parsed.query).get("token", [""])[0].strip()
        return ""

    def authorized(self) -> bool:
        if not AUTH_REQUIRED:
            return True
        return bool(LOCAL_TOKEN) and secrets.compare_digest(self.presented_token(), LOCAL_TOKEN)

    def require_auth(self) -> bool:
        """Send 403 and return False when the caller is missing/using the wrong local token."""
        if self.authorized():
            return True
        self.send_json(
            {"ok": False, "error": "local token required", "hint": f"send Authorization: Bearer <token> from {LOCAL_TOKEN_PATH}"},
            HTTPStatus.FORBIDDEN,
        )
        return False

    def origin_allowed(self) -> bool:
        """Reject cross-origin writes (P1-B). An absent Origin (curl, PowerShell, the automations)
        is fine; a browser tab on another site is not."""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        host_port = self.server.server_address[1] if isinstance(self.server.server_address, tuple) else PORT
        return origin.rstrip("/") in {f"http://127.0.0.1:{host_port}", f"http://localhost:{host_port}"}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith(PRIVATE_GET_PREFIXES) and not self.require_auth():
            return
        if parsed.path == "/api/health":
            # Unauthenticated on purpose: installers, the smoke test, and start-app.ps1 poll this to
            # decide whether the app came up. It exposes nothing private.
            self.send_json({"ok": True, "version": APP_VERSION, "serverTime": utc_now()})
            return
        if parsed.path == "/api/state":
            query = parse_qs(parsed.query)
            view = query.get("view", [""])[0].strip().lower()
            since = query.get("since", [""])[0].strip()
            state = get_state(since)
            self.send_json(to_agent_view(state) if view == "agent" else state)
            return
        if parsed.path == "/api/gate":
            self.send_json(get_gate())
            return
        if parsed.path.startswith("/api/jobs/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 3 or not parts[2]:
                self.send_json({"ok": False, "error": "invalid job route"}, HTTPStatus.NOT_FOUND)
                return
            detail = get_job_detail(parts[2])
            if detail is None:
                self.send_json({"ok": False, "error": "job not found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(detail)
            return
        if parsed.path == "/api/activity-log":
            self.send_json(get_activity_log())
            return
        if parsed.path == "/api/impact-ledger":
            self.send_json(get_impact_ledger())
            return
        if parsed.path == "/api/runtime-inventory":
            self.send_json({**runtime_inventory(), "serverTime": utc_now()})
            return
        if parsed.path == "/api/knowledge":
            query = parse_qs(parsed.query)
            with connect() as db:
                entries = query_knowledge(
                    db,
                    entry_type=query.get("type", [""])[0].strip(),
                    search=query.get("q", [""])[0].strip(),
                    status=(query.get("status", ["active"])[0].strip() or "active"),
                )
                summary = knowledge_summary(db)
            self.send_json({"entries": entries, "total": len(entries), "summary": summary,
                            "serverTime": utc_now()})
            return
        if parsed.path == "/api/sweeps":
            with connect() as db:
                self.send_json({"sweeps": recent_sweeps(db, 100), "serverTime": utc_now()})
            return
        if parsed.path == "/api/export":
            body = build_export_zip()
            filename = f"daily-flow-export-{local_date_key()}.zip"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/architecture-skill":
            self.serve_architecture_skill(parsed)
            return
        if parsed.path == "/api/events":
            self.stream_events()
            return
        if parsed.path.startswith("/api/documents/"):
            self.serve_document(parsed.path)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "cross-origin request rejected"}, HTTPStatus.FORBIDDEN)
            return
        if not self.require_auth():
            return
        try:
            if parsed.path == "/api/career-profile/extract":
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_REQUEST_BYTES:
                    self.close_connection = True
                    self.send_json({"ok": False, "error": "request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    return
                raw = self.rfile.read(length) if length > 0 else b""
                filename = self.headers.get("X-Filename") or parse_qs(parsed.query).get("filename", [""])[0]
                text = extract_uploaded_text(unquote(filename), raw)
                self.send_json({"ok": True, "text": text})
                return
            if parsed.path == "/api/knowledge":
                data = self.read_json()
                title = str(data.get("title", "")).strip()
                entry_type = str(data.get("type", "")).strip()
                if not title or not entry_type:
                    self.send_json({"ok": False, "error": "type and title are required"}, HTTPStatus.BAD_REQUEST)
                    return
                with connect() as db:
                    entry = save_knowledge_entry(db, data)
                self.send_json({"ok": True, "id": entry["id"], "entry": entry})
                return
            if parsed.path in CAPABILITY_POST_PATHS:
                self.handle_capability(parsed.path)
                return
            if parsed.path == "/api/career-profile":
                data = self.read_json()
                with connect() as db:
                    profile = save_career_profile(
                        db,
                        str(data.get("currentRole", "")),
                        str(data.get("targetRole", "")),
                        str(data.get("reviewRubric", "")),
                    )
                self.send_json({"ok": True, "careerProfile": profile})
                return
            if parsed.path == "/api/chat":
                data = self.read_json()
                message = str(data.get("message", "")).strip()
                thread_id = data.get("threadId") or None
                if not message:
                    self.send_json({"ok": False, "error": "message is required"}, HTTPStatus.BAD_REQUEST)
                    return
                with connect() as db:
                    result = create_chat_job(db, message, thread_id, instructions=dashboard_chat_instructions(db, message))
                    # Checked before the document-backed-draft detector so the two can never both
                    # fire on the same message: this one is for creating a NEW document/deck,
                    # that one is for drafting an email around an EXISTING/prior document.
                    if looks_like_artifact_creation_request(message):
                        db.execute(
                            "UPDATE jobs SET artifact_request = 1, handoff_to = 'Drew' WHERE id = ?",
                            (result["jobId"],),
                        )
                        add_event(db, "Major", "Document/deck creation routed to Drew for evidence and package build",
                                   title_from_text(message))
                    elif looks_like_document_backed_draft_request(message):
                        # Explicit routing, not just prose: Major recognizes this as a
                        # document-backed draft request and hands it straight to Drew for source
                        # discovery/validation before Riley is allowed to draft anything.
                        db.execute(
                            "UPDATE jobs SET document_backed_draft = 1, handoff_to = 'Drew' WHERE id = ?",
                            (result["jobId"],),
                        )
                        add_event(db, "Major", "Document-backed draft routed to Drew for source discovery",
                                   title_from_text(message))
                    queue_attention_major(db, "dashboard-chat", force=True)
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/attention-major":
                data = self.read_json()
                # Dashboard-only endpoint, so an unsourced POST is a dashboard press.
                # No automation queues sweeps here; defaulting to "automation" would
                # mislabel a real button press as a duplicated background sweep.
                source = str(data.get("source") or "dashboard")
                force = bool(data.get("force"))
                # P1-F: a repeated press / retried automation carrying the same idempotencyKey inside
                # the TTL window gets the original job back instead of queuing a duplicate sweep.
                idem_key = str(data.get("idempotencyKey") or "").strip()
                cached = idempotency_lookup(idem_key)
                if cached is not None:
                    self.send_json({"ok": True, **cached, "idempotent": True})
                    return
                with connect() as db:
                    result = queue_attention_major(db, source, force=force)
                idempotency_store(idem_key, result)
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/inbox-invites":
                data = self.read_json()
                invites = data.get("invites", [])
                if not isinstance(invites, list):
                    self.send_json({"ok": False, "error": "invites must be an array"}, HTTPStatus.BAD_REQUEST)
                    return
                reconcile = data.get("reconcile", True) is not False
                complete_snapshot = data.get("completeSnapshot", data.get("complete", True)) is not False
                with connect() as db:
                    result = replace_inbox_invite_approvals(db, invites, reconcile=reconcile, complete_snapshot=complete_snapshot)
                self.send_json({"ok": True, **result})
                return
            if parsed.path in {"/api/inbox-signals", "/api/review-signals"}:
                data = self.read_json()
                signals = data.get("signals", [])
                reconcile = bool(data.get("reconcile", False))
                covered_types = data.get("coveredTypes") or data.get("scope") or []
                if not isinstance(covered_types, list):
                    covered_types = [covered_types]
                resolved_ids = data.get("resolvedIds") or data.get("resolvedSourceIds") or []
                if not isinstance(resolved_ids, list):
                    resolved_ids = [resolved_ids]
                complete_snapshot = bool(data.get("completeSnapshot") or data.get("complete"))
                with connect() as db:
                    result = upsert_inbox_signals(
                        db,
                        signals,
                        reconcile=reconcile,
                        covered_types=covered_types,
                        resolved_ids=resolved_ids,
                        complete_snapshot=complete_snapshot,
                    )
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/work-ledger":
                data = self.read_json()
                entries = data.get("entries", [])
                with connect() as db:
                    result = upsert_work_ledger_entries(db, entries)
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/civilians":
                data = self.read_json()
                with connect() as db:
                    ids = create_civilian_batch(
                        db,
                        str(data.get("title") or "Parallel one-off work"),
                        data.get("count", 3),
                        str(data.get("instructions") or ""),
                    )
                self.send_json({"ok": True, "jobIds": ids, "count": len(ids)})
                return
            if parsed.path.startswith("/api/drafts/") and parsed.path.endswith("/send"):
                self.handle_draft_send(parsed.path)
                return
            if parsed.path == "/api/team/all-to-draft":
                with connect() as db:
                    names = [r[0] for r in db.execute("SELECT name FROM employees")]
                    reset = []
                    for name in names:
                        if employee_mode(name) == "adjustable":
                            db.execute(
                                "UPDATE employees SET trust_level = 'draft', protocol_json = ? WHERE name = ?",
                                (json.dumps(derive_protocol(name, "draft")), name),
                            )
                            reset.append(name)
                    add_event(db, "You", "Set everyone back to Draft", f"Reset to Draft: {', '.join(reset) or 'none'}.")
                self.send_json({"ok": True, "reset": reset})
                return
            if parsed.path == "/api/sweep/start":
                data = self.read_json()
                with connect() as db:
                    sweep_id = record_sweep_start(
                        db,
                        str(data.get("source") or "automation"),
                        str(data.get("model") or ""),
                        data.get("channels"),
                    )
                self.send_json({"ok": True, "sweepId": sweep_id})
                return
            if parsed.path == "/api/sweep/finish":
                data = self.read_json()
                with connect() as db:
                    final_id = record_sweep_finish(
                        db,
                        data.get("sweepId") or data.get("id"),
                        str(data.get("status") or "completed"),
                        counts=data.get("counts"),
                        passes=data.get("passes"),
                        verify=data.get("verify"),
                        summary=str(data.get("summary") or ""),
                        error=str(data.get("error") or ""),
                        channels=data.get("channels"),
                    )
                self.send_json({"ok": True, "sweepId": final_id})
                return
            if parsed.path == "/api/classify":
                data = self.read_json()
                msgs = data.get("messages")
                if isinstance(msgs, list):
                    self.send_json({"ok": True, "results": [classify_message(m if isinstance(m, dict) else {}) for m in msgs]})
                else:
                    msg = data.get("message") if isinstance(data.get("message"), dict) else data
                    self.send_json({"ok": True, **classify_message(msg if isinstance(msg, dict) else {})})
                return
            if parsed.path == "/api/maintenance":
                with connect() as db:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.send_json({"ok": True, "checkpointed": True})
                return
            if parsed.path.startswith("/api/jobs/"):
                self.handle_job_update(parsed.path)
                return
            if parsed.path.startswith("/api/approvals/"):
                self.handle_approval_update(parsed.path)
                return
            if parsed.path == "/api/employees/add":
                self.handle_employee_add()
                return
            if parsed.path == "/api/skills/check":
                data = self.read_json()
                self.send_json({"ok": True, "results": skill_install_status(data.get("skills") or [])})
                return
            if parsed.path == "/api/skills/install":
                self.handle_skill_install()
                return
            if parsed.path.startswith("/api/employees/") and parsed.path.rstrip("/").split("/")[-1] in ("proposal", "confirm", "remove", "restore"):
                self.handle_employee_lifecycle(parsed.path)
                return
            if parsed.path.startswith("/api/employees/"):
                self.handle_employee_update(parsed.path)
                return
            if parsed.path == "/api/decision-memory/clear":
                data = self.read_json()
                with connect() as db:
                    if data.get("clearAll"):
                        muted_keys = {row["content_key"] for row in db.execute(
                            "SELECT content_key FROM decision_memory WHERE status = 'active'")}
                        db.execute("UPDATE decision_memory SET status = 'cleared', updated_at = ? WHERE status = 'active'", (utc_now(),))
                        restored = restore_muted_items(db, muted_keys)
                        add_event(db, "You", "Un-muted all dismissed items (decision memory).",
                                  (f"Brought {restored} item(s) back into the Approval inbox." if restored
                                   else "No held items remained to restore."))
                        self.send_json({"ok": True, "restored": restored})
                        return
                    key = str(data.get("contentKey", "")).strip()
                    if not key:
                        self.send_json({"ok": False, "error": "contentKey or clearAll required"}, HTTPStatus.BAD_REQUEST)
                        return
                    db.execute("UPDATE decision_memory SET status = 'cleared', updated_at = ? WHERE content_key = ?", (utc_now(), key))
                    restored = restore_muted_items(db, {key})
                    add_event(db, "You", "Un-muted a dismissed item (decision memory).",
                              ("Brought it back into the Approval inbox." if restored
                               else "It can surface again on the next sweep."))
                self.send_json({"ok": True, "restored": restored})
                return
            if parsed.path == "/api/reset":
                self.send_json(reset_private_data())
                return
        except RequestTooLarge as exc:
            # The body was never read off the socket, so this connection cannot be reused for a
            # follow-up request — close it after answering instead of desyncing the stream.
            self.close_connection = True
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "cross-origin request rejected"}, HTTPStatus.FORBIDDEN)
            return
        if not self.require_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/knowledge/"):
            entry_id = unquote(parsed.path.removeprefix("/api/knowledge/")).strip("/")
            if not entry_id or "/" in entry_id:
                self.send_json({"ok": False, "error": "invalid knowledge route"}, HTTPStatus.NOT_FOUND)
                return
            with connect() as db:
                found = delete_knowledge_entry(db, entry_id)
            if not found:
                self.send_json({"ok": False, "error": "knowledge entry not found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "id": entry_id})
            return
        self.send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "cross-origin request rejected"}, HTTPStatus.FORBIDDEN)
            return
        if not self.require_auth():
            return
        self.send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def handle_job_update(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 3:
            self.send_json({"ok": False, "error": "invalid job route"}, HTTPStatus.NOT_FOUND)
            return
        _, _, job_id = parts
        data = self.read_json()
        status = str(data.get("status", "")).strip().lower()
        # Phase 2 stamp-only fields. Major hands a job off, Quinn returns a verdict, and Casey links
        # knowledge without any of them owning the job's status, so a request carrying only these is
        # valid. Status is still required for anything that moves the job through its lifecycle.
        stamp_keys = ("qualityReview", "qualityVerdict", "riskLevel", "handoffTo", "eta",
                      "slaBreached", "knowledgeLinks", "sourceIds", "contentReviewed")
        has_stamps = any(key in data for key in stamp_keys)
        if status not in {"in_progress", "completed", "blocked"}:
            if not has_stamps:
                self.send_json({"ok": False, "error": "status must be in_progress, completed, or blocked"}, HTTPStatus.BAD_REQUEST)
                return
            status = ""
        now = utc_now()
        with connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job:
                self.send_json({"ok": False, "error": "job not found"}, HTTPStatus.NOT_FOUND)
                return
            if status:
                db.execute(
                    "UPDATE jobs SET status = ?, updated_at = ?, started_at = COALESCE(started_at, ?), completed_at = CASE WHEN ? IN ('completed', 'blocked') THEN ? ELSE completed_at END, result_summary = ?, blocker = ?, result_link_json = ? WHERE id = ?",
                    (
                        status,
                        now,
                        now,
                        status,
                        now,
                        str(data.get("resultSummary", "")),
                        str(data.get("blocker", "")),
                        json.dumps(publish_document_link(data.get("link", "")) or data.get("link", "")),
                        job_id,
                    ),
                )
            else:
                db.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id))
            self.stamp_job_fields(db, job_id, data)
            # Document-backed draft workflow: never let a fabricated "completed" claim stand in for
            # a source document that was never actually found and attached/linked. See
            # validate_document_backed_completion for the evidence rules; any violation downgrades
            # the job to 'blocked' with the searched-locations/search-terms/reason preserved in the
            # blocker text, so the user sees a visible review-required state instead of a silently
            # fabricated result.
            document_evidence_failed = False
            override = validate_document_backed_completion(data, status)
            if override:
                document_evidence_failed = True
                override_status, reason = override
                db.execute(
                    "UPDATE jobs SET status = ?, completed_at = ?, blocker = ? WHERE id = ?",
                    (override_status, now, reason, job_id),
                )
                add_event(db, job["employee"], f"Job blocked (document not attached): {job['title']}", reason)
                status = override_status
            # Document/deck creation workflow: same enforcement pattern -- never let a fabricated
            # "completed" claim stand in for a real artifact. See
            # validate_artifact_creation_completion for the evidence rules (a real file link for
            # the 'created' path, a real build prompt for the 'copilot_prompt_fallback' path).
            artifact_evidence_failed = False
            artifact_override = validate_artifact_creation_completion(data, status)
            if artifact_override:
                artifact_evidence_failed = True
                override_status, reason = artifact_override
                db.execute(
                    "UPDATE jobs SET status = ?, completed_at = ?, blocker = ? WHERE id = ?",
                    (override_status, now, reason, job_id),
                )
                add_event(db, job["employee"], f"Job blocked (artifact not delivered): {job['title']}", reason)
                status = override_status
            # Evidence Review v1: Major actively orchestrates the Riley->Casey->Drew->Quinn->Major
            # hand-off. Whenever a leg reports its stamp (knowledgeLinks, contentReviewed,
            # qualityVerdict) or the job otherwise updates, Major re-reads the evidence dossier plus
            # whatever stamps have accumulated so far and decides the next stop, auto-advancing
            # handoffTo — this is a real routing decision made on every update, not a one-time static
            # instruction.
            job_for_routing = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job_for_routing is not None and job_for_routing["evidence_json"]:
                try:
                    evidence = json.loads(job_for_routing["evidence_json"])
                except json.JSONDecodeError:
                    evidence = {}
                if isinstance(evidence, dict) and evidence:
                    next_hop = evidence_review_next_hop(evidence, job_for_routing)
                    if next_hop and next_hop != job_for_routing["handoff_to"]:
                        db.execute("UPDATE jobs SET handoff_to = ? WHERE id = ?", (next_hop, job_id))
                        add_event(db, "Major", f"Evidence Review routing decision: {next_hop} is next on {job_for_routing['title']}",
                                  evidence.get("recommendation", {}).get("nextBestAction", ""))
            # Document-backed draft workflow: same active-routing pattern, explicit roles instead of
            # implicit prose. Major -> Drew (discovery/validation) -> Riley (plain-text draft, only
            # once Drew confirms a real path/id) -> Quinn (verifies source + attachment/link before
            # approval) -> Major. Only engages for jobs flagged document_backed_draft at creation.
            if job_for_routing is not None and job_for_routing["document_backed_draft"]:
                next_hop = document_draft_next_hop(job_for_routing)
                if next_hop and next_hop != job_for_routing["handoff_to"]:
                    db.execute("UPDATE jobs SET handoff_to = ? WHERE id = ?", (next_hop, job_id))
                    add_event(db, "Major", f"Document-backed draft routing decision: {next_hop} is next on {job_for_routing['title']}",
                              f"documentStatus={job_for_routing['document_status'] or 'pending'}")
            # Document/deck creation workflow: same active-routing pattern. Major -> Casey
            # (conditional, only if Drew flagged a need for confirmed context) -> Drew (evidence +
            # package + creation) -> Mina (pptx storyboard/speaker notes only) -> Riley (cover
            # note) -> Quinn (validation) -> Major. Only engages for jobs flagged artifact_request.
            if job_for_routing is not None and job_for_routing["artifact_request"]:
                next_hop = artifact_creation_next_hop(job_for_routing)
                if next_hop and next_hop != job_for_routing["handoff_to"]:
                    db.execute("UPDATE jobs SET handoff_to = ? WHERE id = ?", (next_hop, job_id))
                    add_event(db, "Major", f"Document/deck creation routing decision: {next_hop} is next on {job_for_routing['title']}",
                              f"creationMode={job_for_routing['artifact_creation_mode'] or 'pending'}")
            # v3.1.0: let the worker stamp the outward-draft send state and the skill it used.
            # Skip this when the document-backed draft workflow just downgraded the job to blocked
            # above -- an unattached/missing source document must never be reported as sent.
            send_state = str(data.get("sendState", "")).strip().lower()
            if send_state in {"open_to_send", "ready", "sent", "held_classified"} and not document_evidence_failed and not artifact_evidence_failed:
                db.execute("UPDATE jobs SET send_state = ? WHERE id = ?", (send_state, job_id))
            skill_used = str(data.get("skill", "")).strip()
            if skill_used:
                db.execute("UPDATE jobs SET skill = ? WHERE id = ?", (skill_used, job_id))
            # v3.3.9: let Major stamp WHO actually did the work when he delegates, so the cockpit shows
            # the real owner (and that employee's live status), not always "Major". Only accept a known
            # active employee name; ignore anything else.
            new_owner = str(data.get("employee", "")).strip()
            if new_owner and new_owner != job["employee"]:
                known = db.execute(
                    "SELECT 1 FROM employees WHERE name = ? AND status = 'active'", (new_owner,)
                ).fetchone()
                if known:
                    db.execute("UPDATE jobs SET employee = ? WHERE id = ?", (new_owner, job_id))
                    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job["thread_id"] and data.get("message"):
                db.execute(
                    "INSERT INTO chat_messages(id, created_at, thread_id, employee, sender, message, status, job_id, link_json) VALUES(?, ?, ?, ?, 'employee', ?, ?, ?, ?)",
                    (
                        new_id("msg"),
                        now,
                        job["thread_id"],
                        job["employee"],
                        str(data["message"]),
                        status or job["status"],
                        job_id,
                        json.dumps(publish_document_link(data.get("link", "")) or data.get("link", "")),
                    ),
                )
            if status:
                add_event(db, job["employee"], f"Job {status}{' · autonomous' if data.get('autonomous') else ''}: {job['title']}", str(data.get("resultSummary", "")))
        self.send_json({"ok": True})

    def stamp_job_fields(self, db: sqlite3.Connection, job_id: str, data: dict[str, Any]) -> None:
        """Apply the Phase 2 job stamps: Major's delegation and SLA fields, Quinn's review flag and
        verdict, and Casey's knowledge links. Every field is optional and only written when present,
        so a routine status update never clears a stamp another employee set."""
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            return
        if "qualityReview" in data:
            db.execute("UPDATE jobs SET quality_review = ? WHERE id = ?",
                       (1 if data.get("qualityReview") else 0, job_id))
        verdict = str(data.get("qualityVerdict", "")).strip().lower()
        if verdict in {"pass", "pass-with-notes", "hold"}:
            db.execute("UPDATE jobs SET quality_verdict = ?, quality_review = 1 WHERE id = ?",
                       (verdict, job_id))
            add_event(db, "Quinn", f"Quality review {verdict}: {job['title']}",
                      str(data.get("reviewNotes", "")))
        risk = str(data.get("riskLevel", "")).strip().lower()
        if risk in {"low", "medium", "high"}:
            db.execute("UPDATE jobs SET risk_level = ? WHERE id = ?", (risk, job_id))
        handoff = str(data.get("handoffTo", "")).strip()
        if handoff:
            # Only a real active employee can receive a handoff, so a typo cannot route work to a
            # name nobody owns. Either way the attempt is logged, so a lost handoff is visible
            # in the Activity Log instead of silently disappearing.
            known = db.execute("SELECT 1 FROM employees WHERE name = ? AND status = 'active'",
                               (handoff,)).fetchone()
            if known:
                db.execute("UPDATE jobs SET handoff_to = ? WHERE id = ?", (handoff, job_id))
                add_event(db, job["employee"], f"Handed off to {handoff}: {job['title']}",
                          str(data.get("handoffReason", "")))
            else:
                add_event(db, job["employee"], f"Handoff to unknown employee ignored: {handoff}",
                          f"Job {job_id} stayed with {job['employee']}.")
        eta = str(data.get("eta", "")).strip()
        if eta:
            db.execute("UPDATE jobs SET eta = ? WHERE id = ?", (eta, job_id))
        if "slaBreached" in data:
            db.execute("UPDATE jobs SET sla_breached = ? WHERE id = ?",
                       (1 if data.get("slaBreached") else 0, job_id))
        if "knowledgeLinks" in data:
            db.execute("UPDATE jobs SET knowledge_links_json = ? WHERE id = ?",
                       (json_list(data.get("knowledgeLinks")), job_id))
        if "sourceIds" in data:
            db.execute("UPDATE jobs SET source_ids_json = ? WHERE id = ?",
                       (json_list(data.get("sourceIds")), job_id))
        if "contentReviewed" in data:
            # Drew's completion stamp for Evidence Review's content-judgment leg (deck/proposal
            # analysis), parallel to Casey's knowledgeLinks and Quinn's qualityVerdict.
            db.execute("UPDATE jobs SET content_reviewed = ? WHERE id = ?",
                       (1 if data.get("contentReviewed") else 0, job_id))
        document_status = str(data.get("documentStatus", "")).strip().lower()
        if document_status in {"found", "not_found", "attach_failed"}:
            # Document-backed draft workflow (e.g. "put the Cowork doc I just made into a draft
            # email"): the worker's discovery-task verdict on locating the actual source document.
            # handle_job_update enforces this against the reported completion status so a fabricated
            # "completed" claim can never stand in for a real file that was never located/attached.
            db.execute("UPDATE jobs SET document_status = ? WHERE id = ?", (document_status, job_id))
        if "documentEvidence" in data:
            db.execute("UPDATE jobs SET document_evidence_json = ? WHERE id = ?",
                       (json_object(data.get("documentEvidence")), job_id))
        if "draftComposed" in data:
            # Riley's completion stamp for the document-backed draft chain: the plain-text draft
            # was composed, and ONLY after Drew's document_status was 'found' (enforced by
            # document_draft_next_hop routing, not by this write itself).
            db.execute("UPDATE jobs SET draft_composed = ? WHERE id = ?",
                       (1 if data.get("draftComposed") else 0, job_id))
        artifact_type = str(data.get("artifactType", "")).strip().lower()
        if artifact_type in {"docx", "pptx"}:
            # Document/deck creation workflow: the artifact type Drew is building, used to decide
            # whether Mina's storyboard/speaker-notes leg applies (pptx only).
            db.execute("UPDATE jobs SET artifact_type = ? WHERE id = ?", (artifact_type, job_id))
        if "artifactNeedsContext" in data:
            # Drew's request for Casey's confirmed customer/project/commitment context before
            # finalizing the package -- never speculative facts. See artifact_creation_next_hop.
            db.execute("UPDATE jobs SET artifact_needs_context = ? WHERE id = ?",
                       (1 if data.get("artifactNeedsContext") else 0, job_id))
        if "artifactPackage" in data:
            # The full structured package: audience/objective, evidence + gaps, outline/storyboard,
            # content body/speaker notes, design guidance, the Copilot prompt, and
            # destination/status.
            db.execute("UPDATE jobs SET artifact_package_json = ? WHERE id = ?",
                       (json_object(data.get("artifactPackage")), job_id))
        creation_mode = str(data.get("creationMode", "")).strip().lower()
        if creation_mode in {"created", "copilot_prompt_fallback"}:
            # Drew's stamp of which of the two modes was used. handle_job_update enforces this
            # against the reported completion status via validate_artifact_creation_completion, so
            # a fabricated "completed" claim can never stand in for a missing file/prompt.
            db.execute("UPDATE jobs SET artifact_creation_mode = ? WHERE id = ?", (creation_mode, job_id))
        if "narrativeReviewed" in data:
            # Mina's completion stamp for the deck storyline/speaker-notes leg (pptx artifacts
            # only), parallel to Casey's knowledgeLinks and Drew's contentReviewed.
            db.execute("UPDATE jobs SET narrative_reviewed = ? WHERE id = ?",
                       (1 if data.get("narrativeReviewed") else 0, job_id))
        if "coverNoteComposed" in data:
            # Riley's completion stamp for the document/deck creation chain: the plain-text cover
            # note was composed, once the package itself is confirmed.
            db.execute("UPDATE jobs SET cover_note_composed = ? WHERE id = ?",
                       (1 if data.get("coverNoteComposed") else 0, job_id))
        # Phase 3 payload stamps. Same rule as above: written only when the key is present, so an
        # employee attaching a chart spec never wipes another's talk track.
        for key, column in (
            ("qualityAudit", "quality_audit_json"),
            ("talkTrack", "talk_track_json"),
            ("conferencePack", "conference_pack_json"),
            ("chartSpec", "chart_spec_json"),
            ("runtimeInventory", "runtime_inventory_json"),
            ("flowDoc", "flow_doc_json"),
        ):
            if key in data:
                db.execute(f"UPDATE jobs SET {column} = ? WHERE id = ?",
                           (json_object(data.get(key)), job_id))
        if "redactionRequired" in data:
            db.execute("UPDATE jobs SET redaction_required = ? WHERE id = ?",
                       (1 if data.get("redactionRequired") else 0, job_id))
        if "redactionApplied" in data:
            applied = 1 if data.get("redactionApplied") else 0
            db.execute("UPDATE jobs SET redaction_applied = ? WHERE id = ?", (applied, job_id))
            if applied:
                add_event(db, "Quinn", f"Redaction applied: {job['title']}",
                          str(data.get("redactionNotes", "")))
        brand_voice = str(data.get("brandVoiceProfile", "")).strip()
        if brand_voice:
            db.execute("UPDATE jobs SET brand_voice_profile = ? WHERE id = ?",
                       (brand_voice[:120], job_id))

    def handle_capability(self, path: str) -> None:
        """Phase 3 capability endpoints.

        All of these are pure transforms over the posted body: they compute and return, and none of
        them writes to the database. Persisting a result is a separate, explicit act — the employee
        stamps it onto the job it belongs to via POST /api/jobs/{id}. That keeps a scratch
        calculation from silently becoming part of the record.

        Auth and origin checks already ran in do_POST, so every path here is guarded.
        """
        data = self.read_json()
        if not isinstance(data, dict):
            self.send_json({"ok": False, "error": "request body must be a JSON object"},
                           HTTPStatus.BAD_REQUEST)
            return
        try:
            if path == "/api/content-pass":
                text = str(data.get("text", ""))
                result = audit_content(
                    text,
                    audience=str(data.get("audience", "")),
                    brand_voice=str(data.get("brandVoice", "")),
                )
                if result.get("ok") and data.get("redact"):
                    result["redactedText"] = redact_sensitive_text(text)
                    result["redactionApplied"] = result["redactedText"] != text
            elif path == "/api/skill-lint":
                text = str(data.get("text", ""))
                skill_path = str(data.get("path", "")).strip()
                if not text and skill_path:
                    # Reading a path the caller supplied is a file-read primitive, so it is confined
                    # to the package's own skills folder. Anything else is refused outright.
                    root = (APP_ROOT.parent / "skills").resolve()
                    target = (root / skill_path.lstrip("/\\")).resolve()
                    if not target.is_relative_to(root) or not target.is_file():
                        self.send_json({"ok": False,
                                        "error": "path must name a SKILL.md inside the package skills folder"},
                                       HTTPStatus.FORBIDDEN)
                        return
                    text = target.read_text(encoding="utf-8-sig")
                result = lint_skill_text(text, name=str(data.get("name", "")))
            elif path == "/api/format-list":
                result = format_list_rows(data.get("rows"), columns=data.get("columns") or None)
            elif path == "/api/document-flow":
                result = document_flow(data.get("flow") if "flow" in data else data)
            elif path == "/api/chart-spec":
                result = build_chart_spec(data)
            elif path == "/api/conference-pack":
                result = build_conference_pack(data)
            elif path == "/api/talk-track":
                result = build_talk_track(data)
            else:
                self.send_json({"ok": False, "error": "unknown capability"}, HTTPStatus.NOT_FOUND)
                return
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json({"ok": False, "error": f"could not process request: {exc}"},
                           HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self.send_json({"ok": False, "error": f"could not read file: {exc}"},
                           HTTPStatus.BAD_REQUEST)
            return
        status = HTTPStatus.OK if result.get("ok", True) else HTTPStatus.BAD_REQUEST
        self.send_json({**result, "serverTime": utc_now()}, status)

    def serve_document(self, path: str) -> None:
        name = unquote(path.removeprefix("/api/documents/"))
        if not name or "/" in name or "\\" in name:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        root = ONEDRIVE_DOCUMENT_ROOT.resolve()
        target = (root / name).resolve()
        # is_relative_to() compares resolved paths, so a symlink or "..\..\" cannot escape the
        # document root the way a plain string-prefix check allows (P1-D).
        if not target.is_relative_to(root):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        suffix = target.suffix.lower()
        disposition = f'inline; filename="{target.name}"'
        if suffix in {".md", ".markdown"}:
            # Render Markdown to a readable HTML page so it opens in the browser instead of
            # downloading as raw text (or being mis-served as octet-stream).
            try:
                body = render_markdown_page(target.stem, target.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                body = target.read_bytes()
                content_type = "text/plain; charset=utf-8"
            else:
                content_type = "text/html; charset=utf-8"
                disposition = "inline"  # render as a page; no .md filename to avoid a download heuristic
        elif suffix in {".txt", ".csv", ".log", ".json"}:
            body = target.read_bytes()
            content_type = "text/plain; charset=utf-8"
        else:
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def serve_architecture_skill(self, parsed: Any) -> None:
        skill = parse_qs(parsed.query).get("name", [""])[0].strip().lower()
        if skill not in ARCHITECTURE_SKILLS:
            self.send_json({"ok": False, "error": "skill not available in architecture map"}, HTTPStatus.NOT_FOUND)
            return
        for root in SKILL_ROOTS:
            target = (root / skill / "SKILL.md").resolve()
            if str(target).startswith(str(root.resolve())) and target.exists() and target.is_file():
                self.send_json({
                    "ok": True,
                    "name": skill,
                    "title": f"/{skill}",
                    "markdown": target.read_text(encoding="utf-8"),
                })
                return
        self.send_json({"ok": False, "error": "skill markdown file not found"}, HTTPStatus.NOT_FOUND)

    def handle_draft_send(self, path: str) -> None:
        # /api/drafts/<jobId>/send — the user clicks Send on an Assist-prepared outward draft.
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            self.send_json({"ok": False, "error": "invalid draft route"}, HTTPStatus.NOT_FOUND)
            return
        job_id = parts[2]
        with connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job:
                self.send_json({"ok": False, "error": "draft not found"}, HTTPStatus.NOT_FOUND)
                return
            if job["send_state"] not in {"ready", "held_classified"}:
                self.send_json({"ok": False, "error": "this draft is not awaiting your Send"}, HTTPStatus.BAD_REQUEST)
                return
            employee = job["employee"]
            title = job["title"]
            now = utc_now()
            send_job_id = new_id("job")
            instructions = (
                f"The user clicked Send on {employee}'s prepared item: {title}. "
                f"Deliver exactly that prepared draft now (the result link on job {job_id}). "
                "Do not rewrite it. After sending, post status='completed' to this job with a short "
                "confirmation. This is the user's explicit approval to send this one item."
            )
            db.execute(
                "INSERT INTO jobs(id, created_at, updated_at, employee, type, title, status, priority, source, instructions) "
                "VALUES(?, ?, ?, ?, 'send-draft', ?, 'queued', 'high', 'results-send', ?)",
                (send_job_id, now, now, employee, f"Send approved: {title}", instructions),
            )
            db.execute("UPDATE jobs SET send_state = 'sent', updated_at = ? WHERE id = ?", (now, job_id))
            add_event(db, "You", f"Approved send: {title}", f"{employee} will deliver the prepared item.")
            queue_attention_major(db, "results-send", force=True)
        self.send_json({"ok": True, "sendJobId": send_job_id})

    def handle_employee_update(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 3:
            self.send_json({"ok": False, "error": "invalid employee route"}, HTTPStatus.NOT_FOUND)
            return
        name = unquote(parts[2])
        data = self.read_json()
        with connect() as db:
            emp = db.execute("SELECT * FROM employees WHERE name = ?", (name,)).fetchone()
            if not emp:
                self.send_json({"ok": False, "error": "employee not found"}, HTTPStatus.NOT_FOUND)
                return
            updates: list[str] = []
            params: list[Any] = []
            logged: list[str] = []
            if "trustLevel" in data:
                if employee_mode(name) == "fixed":
                    self.send_json({"ok": False, "error": f"{name}'s level is fixed and cannot be changed"}, HTTPStatus.BAD_REQUEST)
                    return
                trust = str(data.get("trustLevel", "")).strip().lower()
                if trust not in TRUST_LEVELS:
                    self.send_json({"ok": False, "error": f"trustLevel must be one of {TRUST_LEVELS}"}, HTTPStatus.BAD_REQUEST)
                    return
                updates.append("trust_level = ?")
                params.append(trust)
                # Keep the stored protocol in sync with the new level (state also derives it live).
                updates.append("protocol_json = ?")
                params.append(json.dumps(derive_protocol(name, trust)))
                logged.append(f"level -> {trust}")
            if "enabled" in data:
                enabled = 1 if data.get("enabled") else 0
                updates.append("enabled = ?")
                params.append(enabled)
                logged.append("enabled" if enabled else "paused")
            if not updates:
                self.send_json({"ok": False, "error": "no recognized fields (trustLevel, enabled)"}, HTTPStatus.BAD_REQUEST)
                return
            params.append(name)
            db.execute(f"UPDATE employees SET {', '.join(updates)} WHERE name = ?", params)
            add_event(db, "You", f"Updated {name}: {', '.join(logged)}", "Changed from the cockpit Team panel.")
        self.send_json({"ok": True})

    def handle_employee_add(self) -> None:
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 40:
            self.send_json({"ok": False, "error": "A name is required (max 40 characters)."}, HTTPStatus.BAD_REQUEST)
            return
        if name.lower() == "major":
            self.send_json({"ok": False, "error": "Major is reserved and cannot be reused."}, HTTPStatus.BAD_REQUEST)
            return
        source_text = str(data.get("sourceText", ""))
        hint = str(data.get("hint", ""))
        role_hint = (str(data.get("role") or "Custom Agent").strip()[:60]) or "Custom Agent"
        analyze = bool(data.get("analyze", True)) and bool(source_text.strip() or hint.strip())
        with connect() as db:
            clash = db.execute("SELECT status FROM employees WHERE name = ?", (name,)).fetchone()
            if clash and clash["status"] != "removed":
                self.send_json({"ok": False, "error": "An employee with that name already exists."}, HTTPStatus.BAD_REQUEST)
                return
            now = utc_now()
            if clash:  # revive a removed tombstone of the same name as a fresh custom onboarding
                db.execute(
                    "UPDATE employees SET role=?, detail=?, origin='custom', status='onboarding', trust_level='draft', "
                    "enabled=1, source_text=?, lane='custom', internal_verb='', outward_verb='', always_json='[]', "
                    "triggers='', skills_json='[]', note='' WHERE name=?",
                    (role_hint, hint, source_text, name),
                )
            else:
                db.execute(
                    "INSERT INTO employees(name, role, detail, created_at, origin, status, trust_level, enabled, source_text, lane) "
                    "VALUES(?, ?, ?, ?, 'custom', 'onboarding', 'draft', 1, ?, 'custom')",
                    (name, role_hint, hint, now, source_text),
                )
            job_id = None
            if analyze:
                res = create_chat_job(
                    db,
                    f"Onboarding {name}: I'm reading the material you provided and will propose a profile for your review.",
                    title=f"Onboard {name}",
                    instructions=onboarding_instructions(name, source_text, hint),
                )
                job_id = res["jobId"]
                queue_attention_major(db, "employee-onboarding", force=True)
            add_event(db, "You", f"Started onboarding {name}",
                      "Major is analyzing the provided material." if analyze else "Draft created for manual setup.")
            refresh_custom_employee_config(db)
        self.send_json({"ok": True, "name": name, "status": "onboarding", "analyzing": bool(analyze), "jobId": job_id})

    def handle_skill_install(self) -> None:
        data = self.read_json()
        name = _safe_skill_id(data.get("name", ""))
        if not name:
            self.send_json({"ok": False, "error": "skill name required"}, HTTPStatus.BAD_REQUEST)
            return
        text = data.get("text")
        roots = install_skill_text(name, str(text)) if (text and str(text).strip()) else try_install_skill_local(name)
        installed = bool(roots)
        self.send_json({
            "ok": True, "name": name, "installed": installed, "roots": roots, "restartNeeded": installed,
            "message": "Installed — restart Scout to activate it." if installed
            else "Could not find this skill locally. Paste its SKILL.md to install it.",
        })

    def _apply_employee_profile(self, db: sqlite3.Connection, name: str, data: dict[str, Any], emp: sqlite3.Row) -> str:
        """Shared writer for proposal/confirm — stores the structured profile fields. Returns level."""
        level = str(data.get("level") or emp["trust_level"] or "draft").strip().lower()
        if level not in TRUST_LEVELS:
            level = "draft"
        always = [str(x).strip()[:200] for x in (data.get("always") or []) if str(x).strip()][:6]
        skills = [_safe_skill_id(x) for x in (data.get("skills") or []) if _safe_skill_id(x)][:12]
        db.execute(
            "UPDATE employees SET role=?, detail=?, internal_verb=?, outward_verb=?, always_json=?, triggers=?, "
            "skills_json=?, note=?, trust_level=? WHERE name=?",
            (
                (str(data.get("role") or emp["role"] or "Custom Agent").strip()[:60]) or "Custom Agent",
                str(data.get("summary") or emp["detail"] or "").strip()[:300],
                str(data.get("internal") or "").strip()[:200],
                str(data.get("outward") or "").strip()[:200],
                json.dumps(always),
                str(data.get("triggers") or "").strip()[:600],
                json.dumps(skills),
                str(data.get("note") or "").strip()[:300],
                level,
                name,
            ),
        )
        return level

    def handle_employee_lifecycle(self, path: str) -> None:
        parts = path.rstrip("/").split("/")  # ['', 'api', 'employees', '<name>', '<action>']
        if len(parts) != 5:
            self.send_json({"ok": False, "error": "invalid employee action route"}, HTTPStatus.NOT_FOUND)
            return
        name = unquote(parts[3])
        action = parts[4]
        data = self.read_json() if action in ("proposal", "confirm") else {}
        with connect() as db:
            emp = db.execute("SELECT * FROM employees WHERE name = ?", (name,)).fetchone()
            if not emp:
                self.send_json({"ok": False, "error": "employee not found"}, HTTPStatus.NOT_FOUND)
                return
            if action == "proposal":
                self._apply_employee_profile(db, name, data, emp)
                db.execute("UPDATE employees SET status='review' WHERE name=?", (name,))
                refresh_custom_employee_config(db)
                add_event(db, "Major", f"Proposed a profile for {name}", "Ready for your review in Add Employee.")
                _row = db.execute("SELECT skills_json FROM employees WHERE name=?", (name,)).fetchone()
                skills = skill_install_status(decode_json_list(_row["skills_json"]) if _row else [])
                self.send_json({"ok": True, "status": "review", "skills": skills})
                return
            if action == "confirm":
                level = self._apply_employee_profile(db, name, data, emp)
                db.execute("UPDATE employees SET origin='custom', status='active', enabled=1 WHERE name=?", (name,))
                refresh_custom_employee_config(db)  # so derive_protocol sees the new verbs
                db.execute("UPDATE employees SET protocol_json=? WHERE name=?", (json.dumps(derive_protocol(name, level)), name))
                add_event(db, "You", f"Added {name} to the team",
                          "Now a first-class employee — on the roster, in Major's routing, and in the guardrails.")
                _row = db.execute("SELECT skills_json FROM employees WHERE name=?", (name,)).fetchone()
                skills = skill_install_status(decode_json_list(_row["skills_json"]) if _row else [])
                self.send_json({"ok": True, "status": "active", "skills": skills})
                return
            if action == "remove":
                if name == "Major":
                    self.send_json({"ok": False, "error": "Major coordinates the team and cannot be removed."}, HTTPStatus.BAD_REQUEST)
                    return
                if emp["status"] == "removed":
                    self.send_json({"ok": True, "alreadyRemoved": True})
                    return
                reassigned = reassign_open_jobs_to_major(db, name)
                db.execute("UPDATE employees SET status='removed', enabled=0 WHERE name=?", (name,))
                refresh_custom_employee_config(db)
                tail = f" Reassigned {reassigned} open job(s) to Major." if reassigned else ""
                add_event(db, "You", f"Removed {name} from the active team",
                          f"Their past work stays in your ledger; they left the active roster.{tail} You can restore them later.")
                self.send_json({"ok": True, "reassigned": reassigned})
                return
            if action == "restore":
                db.execute("UPDATE employees SET status='active', enabled=1 WHERE name=?", (name,))
                refresh_custom_employee_config(db)
                add_event(db, "You", f"Restored {name} to the team", "Back on the active roster and in Major's routing.")
                self.send_json({"ok": True})
                return
        self.send_json({"ok": False, "error": "unknown action"}, HTTPStatus.NOT_FOUND)

    def handle_approval_update(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 3:
            self.send_json({"ok": False, "error": "invalid approval route"}, HTTPStatus.NOT_FOUND)
            return
        _, _, approval_id = parts
        data = self.read_json()
        decision = str(data.get("status", "")).strip().lower()
        with connect() as db:
            approval = db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if not approval:
                self.send_json({"ok": False, "error": "approval not found"}, HTTPStatus.NOT_FOUND)
                return
            # Calendar invites use RSVP-shaped decisions (accept/tentative/follow/decline);
            # every other approval type keeps the generic approved/rejected/deferred set.
            if approval["action_type"] == "calendar":
                if decision not in CALENDAR_DECISIONS:
                    self.send_json({"ok": False, "error": "status must be accept, tentative, follow, or decline"}, HTTPStatus.BAD_REQUEST)
                    return
            elif decision not in {"approved", "rejected", "deferred"}:
                self.send_json({"ok": False, "error": "status must be approved, rejected, or deferred"}, HTTPStatus.BAD_REQUEST)
                return
            user_guidance = str(data.get("userGuidance", "")).strip()
            # Pre-execution freshness check (calendar invites only): re-fetch the approval right
            # before acting on it so a card the user is looking at can't be double-acted-on if it
            # has since become non-actionable (meeting ended, invite resolved outside the app, or
            # already decided by a concurrent request). Other approval types are unaffected.
            if approval["action_type"] == "calendar":
                fresh_approval, not_actionable = calendar_invite_freshness_check(db, approval_id)
                if not_actionable:
                    message = CALENDAR_FRESHNESS_MESSAGES[not_actionable]
                    add_event(db, "Mina", f"Skipped RSVP — invite already handled: {approval['title']}", message)
                    self.send_json({
                        "ok": True,
                        "alreadyHandled": True,
                        "reason": not_actionable,
                        "message": message,
                        "createdJobs": [],
                    })
                    return
                approval = fresh_approval
            db.execute(
                "UPDATE approvals SET status = ?, updated_at = ?, user_guidance = ? WHERE id = ?",
                (decision, utc_now(), user_guidance, approval_id),
            )
            # Decision memory: remember reject/defer of email/Teams so Major stops re-surfacing it.
            if decision in {"rejected", "deferred"} and approval["action_type"] in DECISION_MEMORY_TYPES:
                memo_details = {}
                try:
                    memo_details = json.loads(approval["details_json"] or "{}")
                except Exception:
                    memo_details = {}
                memo_subject = str(memo_details.get("subject") or "").strip()
                if not memo_subject:
                    memo_subject = re.sub(r"^[^:]+:\s*", "", str(approval["title"] or "")).strip()
                memo_sender = str(memo_details.get("sender") or memo_details.get("from") or "").strip()
                memo_source = str(memo_details.get("sourceId") or "").strip()
                record_decision_memory(db, approval["action_type"], memo_subject, memo_sender, memo_source, decision)
            created_jobs = []
            if approval["action_type"] == "calendar":
                if decision == "follow":
                    created_jobs.append(create_follow_invite_job(db, approval, user_guidance))
                else:
                    created_jobs.append(create_rsvp_job(db, approval, decision, user_guidance))
            elif decision == "deferred" and approval["action_type"] in {"email", "teams", "attachment-review"}:
                add_event(db, "Major", f"{approval['action_type'].title()} review item deferred: {approval['title']}", "Removed from Approval inbox without follow-up work.")
            elif approval["action_type"] in {"email", "teams", "meeting-prep", "commitment", "blocked-work", "outbound-draft", "research", "impact-highlight", "stale-thread", "attachment-review"}:
                review_job_id = create_review_follow_up_job(db, approval, decision, user_guidance)
                if review_job_id:
                    created_jobs.append(review_job_id)
            if user_guidance:
                if approval["action_type"] == "calendar" and decision != "follow":
                    created_jobs.append(create_approval_follow_up_job(db, approval, decision, user_guidance))
            if created_jobs:
                queue_attention_major(db, "approval-decision", force=True)
            add_event(db, "You", approval_decision_log(approval["action_type"], decision, approval["title"]), user_guidance)
        self.send_json({"ok": True, "createdJobs": created_jobs})

    def stream_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last = ""
        for _ in range(120):
            with connect() as db:
                row = db.execute("SELECT value FROM app_meta WHERE key = 'version'").fetchone()
                version = row["value"] if row else ""
            if version != last:
                last = version
                self.wfile.write(f"data: {json.dumps({'version': version})}\n\n".encode("utf-8"))
                self.wfile.flush()
            time.sleep(2)

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        root = STATIC_ROOT.resolve()
        target = (root / unquote(path).lstrip("/")).resolve()
        if not target.is_relative_to(root):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        if target.suffix == ".webmanifest":
            # Browsers require this exact MIME type to treat the file as an installable manifest;
            # mimetypes has no built-in mapping for .webmanifest, so it must be set explicitly.
            content_type = "application/manifest+json"
        else:
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if target.name == "sw.js":
            # The service worker script must never be served stale (a cached old version would
            # keep clients pinned to outdated caching logic), and Service-Worker-Allowed widens its
            # control scope to "/" even though it is physically served from /sw.js.
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body)


def _checkpoint_database() -> None:
    """Flush the write-ahead log into the main .db file so a copy/backup of daily_flow.db is
    complete and a hard stop cannot leave a large stray -wal beside it."""
    try:
        with connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Flow App")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--verbose", action="store_true", help="Print HTTP request logs to the console.")
    parser.add_argument("--init", action="store_true", help="Initialize the local SQLite database and exit.")
    parser.add_argument("--import-ledger", type=Path, help="Import a legacy ledger.json into the local SQLite database.")
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument(
        "--auth",
        dest="auth",
        action="store_true",
        default=None,
        help="Recommended. Require a local bearer token on mutating and private-data endpoints.",
    )
    auth_group.add_argument(
        "--no-auth",
        dest="auth",
        action="store_false",
        help="Default, for backward compatibility with already-installed automations.",
    )
    args = parser.parse_args()
    global LOG_REQUESTS, AUTH_REQUIRED
    LOG_REQUESTS = LOG_REQUESTS or args.verbose
    if args.auth is not None:
        AUTH_REQUIRED = args.auth

    init_db()
    _checkpoint_database()
    publish_existing_result_documents()
    if args.import_ledger:
        import_legacy_ledger(args.import_ledger)
        print(f"Imported legacy ledger from {args.import_ledger}")
    if args.init:
        print(f"Initialized {DB_PATH}")
        return

    load_or_create_local_token()
    record_local_token_path()

    def _shutdown(sig: Any, frame: Any) -> None:
        _checkpoint_database()
        raise SystemExit(0)

    for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        handler_sig = getattr(signal, sig_name, None)
        if handler_sig is not None:
            try:
                signal.signal(handler_sig, _shutdown)
            except (ValueError, OSError):
                pass  # not the main thread, or unsupported on this platform

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Daily Flow App v{APP_VERSION} running at http://{args.host}:{args.port}")
    print(f"[config] timezone: {APP_TIMEZONE_NAME} · documents: {ONEDRIVE_DOCUMENT_ROOT}")
    if AUTH_REQUIRED:
        print(f"[auth] Local token: {LOCAL_TOKEN}")
        print(f"[auth] Stored at: {LOCAL_TOKEN_PATH}")
    else:
        print("[auth] Local token NOT required (--no-auth default). Restart with --auth to require it.")
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        server.server_close()
        _checkpoint_database()


if __name__ == "__main__":
    main()
