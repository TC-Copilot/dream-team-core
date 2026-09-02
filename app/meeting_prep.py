"""Deterministic, provider-neutral meeting-prep contracts and orchestration helpers."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal


Confidence = Literal["high", "medium", "low"]
ResolvedBy = Literal["domain", "subject", "account-team", "none"]
DomainSource = Literal["manual", "mailbox", "meeting-learned"]

ACCOUNT_ROLES = ("ssp", "ae", "ats", "csam", "business_process")
PUBLIC_SUFFIXES = frozenset({
    "com", "net", "org", "edu", "gov", "mil", "io", "ai", "co.uk", "com.au",
    "co.nz", "co.jp", "co.in", "com.br", "com.mx", "ca", "de", "fr",
})
COMPANY_NOISE = frozenset({
    "inc", "incorporated", "corp", "corporation", "llc", "co", "company", "software",
    "solutions", "systems", "global", "international",
})
WORD_RE = re.compile(r"[a-z0-9]+")
DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return " ".join(WORD_RE.findall(text.casefold()))


def _tokens(value: Any, *, company: bool = False) -> list[str]:
    result = WORD_RE.findall(_canonical(value))
    return [x for x in result if not company or x not in COMPANY_NOISE]


def _domain(value: Any) -> str:
    return _text(value).casefold().removeprefix("@").removeprefix("www.").rstrip(".")


def _email_domain(value: Any) -> str:
    value = _text(value).casefold()
    return _domain(value.rsplit("@", 1)[1]) if "@" in value else ""


def _iso(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _business_dates(today: date, count: int) -> set[date]:
    dates: set[date] = set()
    cursor = today
    while len(dates) < max(0, count):
        if cursor.weekday() < 5:
            dates.add(cursor)
        cursor += timedelta(days=1)
    return dates


def _next_business_date(today: date) -> date:
    cursor = today + timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


@dataclass(frozen=True)
class AccountTeam:
    ssp: str = ""
    ae: str = ""
    ats: str = ""
    csam: str = ""
    business_process: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "ssp": self.ssp, "ae": self.ae, "ats": self.ats, "csam": self.csam,
            "businessProcess": self.business_process,
        }


@dataclass(frozen=True)
class DomainEntry:
    d: str
    source: DomainSource
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {"d": self.d, "source": self.source, "verified": self.verified}


@dataclass(frozen=True)
class Attendee:
    name: str
    email: str
    external: bool
    account_role: str = ""

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name, "email": self.email, "external": self.external,
        }
        if self.account_role:
            value["accountRole"] = self.account_role
        return value


@dataclass(frozen=True)
class CustomerSignal:
    matched_accounts: tuple[str, ...]
    resolved_by: ResolvedBy
    confidence: Confidence
    team: AccountTeam | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "matchedAccounts": list(self.matched_accounts),
            "resolvedBy": self.resolved_by,
            "confidence": self.confidence,
        }
        if self.team:
            value["team"] = self.team.to_dict()
        return value


@dataclass(frozen=True)
class ExistingAgenda:
    source: Literal["body", "attachment", "link"]
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "text": self.text}


@dataclass(frozen=True)
class MeetingCandidate:
    event_id: str
    subject: str
    start_local: str
    is_recurring: bool
    attendees: tuple[Attendee, ...]
    customer_signal: CustomerSignal
    existing_agenda: ExistingAgenda | None = None
    series_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "eventId": self.event_id,
            "subject": self.subject,
            "startLocal": self.start_local,
            "isRecurring": self.is_recurring,
            "myResponse": "required",
            "attendees": [x.to_dict() for x in self.attendees],
            "customerSignal": self.customer_signal.to_dict(),
        }
        if self.existing_agenda:
            value["existingAgenda"] = self.existing_agenda.to_dict()
        if self.series_id:
            value["seriesId"] = self.series_id
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MeetingCandidate":
        if not isinstance(value, dict):
            raise ValueError("MeetingCandidate must be an object")
        signal = value.get("customerSignal") or {}
        if not isinstance(signal, dict):
            raise ValueError("customerSignal must be an object")
        event_id = _text(value.get("eventId"))
        subject = _text(value.get("subject"))
        start_local = _text(value.get("startLocal"))
        if not event_id or not subject:
            raise ValueError("eventId and subject are required")
        if _iso(start_local) is None:
            raise ValueError("startLocal must be an ISO timestamp with a UTC offset")
        if value.get("myResponse", "required") != "required":
            raise ValueError("myResponse must be required")
        if not isinstance(value.get("isRecurring"), bool):
            raise ValueError("isRecurring must be a boolean")
        attendee_values = value.get("attendees")
        if not isinstance(attendee_values, list) or not attendee_values:
            raise ValueError("attendees must be a non-empty array")
        if any(
            not isinstance(x, dict) or not isinstance(x.get("external"), bool)
            for x in attendee_values
        ):
            raise ValueError("each attendee must be an object with boolean external")
        if signal.get("resolvedBy") not in {"domain", "subject", "account-team", "none"}:
            raise ValueError("customerSignal.resolvedBy is invalid")
        if signal.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError("customerSignal.confidence is invalid")
        matched_accounts = signal.get("matchedAccounts")
        if not isinstance(matched_accounts, list):
            raise ValueError("customerSignal.matchedAccounts must be an array")
        team_value = signal.get("team")
        team = account_team_from_dict(team_value) if isinstance(team_value, dict) else None
        agenda_value = value.get("existingAgenda")
        agenda = None
        if isinstance(agenda_value, dict) and _text(agenda_value.get("text")):
            if agenda_value.get("source", "body") not in {"body", "attachment", "link"}:
                raise ValueError("existingAgenda.source is invalid")
            agenda = ExistingAgenda(
                source=agenda_value.get("source", "body"),
                text=_text(agenda_value.get("text")),
            )
        return cls(
            event_id=event_id,
            subject=subject,
            start_local=start_local,
            is_recurring=value["isRecurring"],
            attendees=tuple(
                Attendee(
                    name=_text(x.get("name")), email=_text(x.get("email")),
                    external=bool(x.get("external")),
                    account_role=_text(x.get("accountRole")),
                )
                for x in attendee_values if isinstance(x, dict)
            ),
            customer_signal=CustomerSignal(
                matched_accounts=tuple(_text(x) for x in matched_accounts),
                resolved_by=signal.get("resolvedBy", "none"),
                confidence=signal.get("confidence", "low"),
                team=team,
            ),
            existing_agenda=agenda,
            series_id=_text(value.get("seriesId")),
        )


@dataclass(frozen=True)
class Citation:
    claim: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {"claim": self.claim, "source": self.source}


@dataclass(frozen=True)
class CustomerBrief:
    customer: str
    as_of: str
    initiatives: tuple[str, ...]
    interest_areas: tuple[str, ...]
    open_issues: tuple[str, ...]
    adoption_signals: tuple[str, ...]
    citations: tuple[Citation, ...]
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer": self.customer, "asOf": self.as_of,
            "initiatives": list(self.initiatives), "interestAreas": list(self.interest_areas),
            "openIssues": list(self.open_issues), "adoptionSignals": list(self.adoption_signals),
            "citations": [x.to_dict() for x in self.citations], "gaps": list(self.gaps),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], focus_ids: set[str]) -> "CustomerBrief":
        if not isinstance(value, dict):
            raise ValueError("CustomerBrief must be an object")
        customer, as_of = _text(value.get("customer")), _text(value.get("asOf"))
        if not customer:
            raise ValueError("CustomerBrief customer is required")
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise ValueError("CustomerBrief asOf must be YYYY-MM-DD") from exc
        list_fields = (
            "initiatives", "interestAreas", "openIssues", "adoptionSignals",
            "citations", "gaps",
        )
        if any(not isinstance(value.get(field, []), list) for field in list_fields):
            raise ValueError("CustomerBrief list fields must be arrays")
        if any(not isinstance(x, dict) for x in value.get("citations", [])):
            raise ValueError("CustomerBrief citations must contain objects")
        for field_name in (
            "initiatives", "interestAreas", "openIssues", "adoptionSignals", "gaps",
        ):
            if any(not isinstance(x, str) for x in value.get(field_name, [])):
                raise ValueError(f"CustomerBrief {field_name} must contain strings")
        if any(
            not _text(x.get("claim")) or not _text(x.get("source"))
            for x in value.get("citations", [])
        ):
            raise ValueError("CustomerBrief citations require claim and source")
        areas = tuple(_text(x) for x in value.get("interestAreas", []))
        unknown = sorted(set(areas) - focus_ids)
        if unknown:
            raise ValueError(f"CustomerBrief interestAreas are not configured focus areas: {unknown}")
        citations = tuple(
            Citation(_text(x.get("claim")), _text(x.get("source")))
            for x in value.get("citations", []) if isinstance(x, dict)
            and _text(x.get("claim")) and _text(x.get("source"))
        )
        return cls(
            customer=customer, as_of=as_of,
            initiatives=tuple(_text(x) for x in value.get("initiatives", [])),
            interest_areas=areas,
            open_issues=tuple(_text(x) for x in value.get("openIssues", [])),
            adoption_signals=tuple(_text(x) for x in value.get("adoptionSignals", [])),
            citations=citations,
            gaps=tuple(_text(x) for x in value.get("gaps", [])),
        )


@dataclass(frozen=True)
class MyAgendaItem:
    title: str
    what_to_prepare: str
    focus_area_id: str
    source_item: str = ""

    def to_dict(self) -> dict[str, str]:
        value = {
            "title": self.title, "whatToPrepare": self.what_to_prepare,
            "focusAreaId": self.focus_area_id,
        }
        if self.source_item:
            value["sourceItem"] = self.source_item
        return value


@dataclass(frozen=True)
class ProposedAgendaItem:
    title: str
    why_now: str
    owner: str
    minutes: int
    outcome: str
    focus_area_id: str
    basis: Literal["my-role", "customer-research", "history"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "whyNow": self.why_now, "owner": self.owner,
            "minutes": self.minutes, "outcome": self.outcome,
            "focusAreaId": self.focus_area_id, "basis": self.basis,
        }


@dataclass(frozen=True)
class NotMineItem:
    item: str
    suggested_owner: str

    def to_dict(self) -> dict[str, str]:
        return {"item": self.item, "suggestedOwner": self.suggested_owner}


@dataclass(frozen=True)
class AgendaRecommendation:
    event_id: str
    mode: Literal["review-existing", "propose-new"]
    my_items: tuple[MyAgendaItem, ...] = ()
    proposed_items: tuple[ProposedAgendaItem, ...] = ()
    not_mine: tuple[NotMineItem, ...] = ()
    risks: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id, "mode": self.mode,
            "myItems": [x.to_dict() for x in self.my_items],
            "proposedItems": [x.to_dict() for x in self.proposed_items],
            "notMine": [x.to_dict() for x in self.not_mine],
            "risks": list(self.risks), "unverified": list(self.unverified),
        }


@dataclass
class Account:
    name: str
    team: AccountTeam
    domains: list[DomainEntry] = field(default_factory=list)
    domains_known: bool = False


@dataclass
class SEScope:
    path: Path
    raw: dict[str, Any]
    accounts: list[Account]
    source_used: str
    account_team_index: dict[str, list[tuple[str, str]]]
    weak_signals: set[str]
    aliases: dict[str, str]

    @property
    def focus_areas(self) -> list[dict[str, Any]]:
        return self.raw["focus_areas"]

    @property
    def focus_ids(self) -> set[str]:
        return {_text(x["id"]) for x in self.focus_areas}

    @property
    def runtime(self) -> dict[str, Any]:
        return self.raw["runtime"]

    def account(self, name: str) -> Account | None:
        key = _canonical(name)
        return next((x for x in self.accounts if _canonical(x.name) == key), None)

    def normalize_name(self, name: str) -> str:
        key = _canonical(name)
        return self.aliases.get(key, _text(name))


def account_team_from_dict(value: dict[str, Any]) -> AccountTeam:
    return AccountTeam(
        ssp=_text(value.get("ssp")), ae=_text(value.get("ae")), ats=_text(value.get("ats")),
        csam=_text(value.get("csam")),
        business_process=_text(value.get("business_process", value.get("businessProcess"))),
    )


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS meeting_prep_fingerprints (
          series_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
          brief_fingerprint TEXT NOT NULL, prepared_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_prep_skips (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, reason TEXT NOT NULL,
          observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_domains (
          domain TEXT PRIMARY KEY, account TEXT NOT NULL, source TEXT NOT NULL,
          verified INTEGER NOT NULL DEFAULT 0, confirmation_count INTEGER NOT NULL DEFAULT 0,
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_domain_evidence (
          domain TEXT NOT NULL, account TEXT NOT NULL, evidence_key TEXT NOT NULL,
          source TEXT NOT NULL, observed_at TEXT NOT NULL,
          PRIMARY KEY(domain, account, evidence_key)
        );
        CREATE TABLE IF NOT EXISTS pending_domains (
          domain TEXT PRIMARY KEY, candidates_json TEXT NOT NULL, reason TEXT NOT NULL,
          confirmation_count INTEGER NOT NULL DEFAULT 0, message_count INTEGER NOT NULL DEFAULT 0,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_domain_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, account TEXT NOT NULL,
          action TEXT NOT NULL, detail TEXT NOT NULL, observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_domain_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL,
          observed_count INTEGER NOT NULL, assigned_count INTEGER NOT NULL,
          pending_count INTEGER NOT NULL, conflict_count INTEGER NOT NULL,
          finished_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_prep_config_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, detail TEXT NOT NULL,
          observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meeting_prep_delivery_jobs (
          job_id TEXT PRIMARY KEY, series_key TEXT NOT NULL, event_id TEXT NOT NULL,
          fingerprint TEXT NOT NULL, brief_fingerprint TEXT NOT NULL,
          queued_at TEXT NOT NULL, delivered_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_meeting_prep_skips_event
          ON meeting_prep_skips(event_id, observed_at);
        """
    )
    pending_columns = {
        row[1] for row in db.execute("PRAGMA table_info(pending_domains)")
    }
    if "message_count" not in pending_columns:
        db.execute(
            "ALTER TABLE pending_domains ADD COLUMN message_count INTEGER NOT NULL DEFAULT 0"
        )


def _xlsx_accounts(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(x.itertext()) for x in root]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
            for rel in relationships
        }
        sheet_name = ""
        for sheet in workbook.iter():
            if sheet.tag.endswith("}sheet") and _canonical(sheet.attrib.get("name")) == "accounts":
                relation_id = next(
                    (value for key, value in sheet.attrib.items() if key.endswith("}id")),
                    "",
                )
                target = targets.get(relation_id, "")
                if target:
                    sheet_name = target.replace("\\", "/").removeprefix("/")
                    if not sheet_name.startswith("xl/"):
                        sheet_name = "xl/" + sheet_name
                break
        if not sheet_name or sheet_name not in archive.namelist():
            raise ValueError("account workbook must contain an Accounts worksheet")
        root = ET.fromstring(archive.read(sheet_name))
        table: list[list[str]] = []
        for row in root.iter():
            if not row.tag.endswith("}row"):
                continue
            values: list[str] = []
            for cell in row:
                if not cell.tag.endswith("}c"):
                    continue
                ref = cell.attrib.get("r", "A1")
                col = 0
                for char in re.match(r"[A-Z]+", ref).group(0):
                    col = col * 26 + ord(char) - 64
                while len(values) < col:
                    values.append("")
                raw = next((x.text or "" for x in cell.iter() if x.tag.endswith("}v")), "")
                if cell.attrib.get("t") == "s" and raw.isdigit():
                    raw = strings[int(raw)]
                elif cell.attrib.get("t") == "inlineStr":
                    raw = "".join(cell.itertext())
                values[col - 1] = raw
            table.append(values)
    if not table:
        return []
    headers = {_canonical(x).replace(" ", "_"): i for i, x in enumerate(table[0])}
    name_index = next((headers[x] for x in ("name", "account", "account_name") if x in headers), -1)
    if name_index < 0:
        raise ValueError("account workbook has no account/name column")
    result = []
    for row in table[1:]:
        if name_index >= len(row) or not _text(row[name_index]):
            continue
        item = {"name": _text(row[name_index])}
        for role in ACCOUNT_ROLES:
            index = headers.get(role, headers.get("businessprocess") if role == "business_process" else None)
            item[role] = _text(row[index]) if isinstance(index, int) and index < len(row) else ""
        result.append(item)
    return result


def _validate_config(raw: dict[str, Any], text: str) -> None:
    required = ("role", "focus_areas", "not_my_scope", "accounts", "name_aliases", "runtime")
    missing = [x for x in required if x not in raw]
    if missing:
        raise ValueError(f"se-scope config missing: {', '.join(missing)}")
    if not isinstance(raw["focus_areas"], list) or not raw["focus_areas"]:
        raise ValueError("se-scope config requires at least one focus area")
    focus_ids = [_text(x.get("id")) for x in raw["focus_areas"] if isinstance(x, dict)]
    if len(focus_ids) != len(raw["focus_areas"]) or any(not x for x in focus_ids):
        raise ValueError("every focus area requires a non-empty id")
    if len(set(focus_ids)) != len(focus_ids):
        raise ValueError("focus area ids must be unique")
    account_names = [
        _canonical(x.get("name")) for x in raw["accounts"] if isinstance(x, dict)
    ]
    if len(account_names) != len(raw["accounts"]) or any(not x for x in account_names):
        raise ValueError("every account requires a non-empty name")
    if len(set(account_names)) != len(account_names):
        raise ValueError("account names must be unique")
    owners: dict[str, tuple[str, int]] = {}
    lines = text.splitlines()
    search_from = 0
    for account in raw["accounts"]:
        name = _text(account.get("name"))
        domains = account.get("domains")
        if domains is not None and not isinstance(domains, list):
            raise ValueError(f"domains for {name} must be a list")
        for entry in domains or []:
            if not isinstance(entry, dict):
                raise ValueError(f"domain entries for {name} must be objects")
            domain = _text(entry.get("d"))
            normalized = _domain(domain)
            line = next((
                i for i, value in enumerate(lines[search_from:], search_from + 1)
                if f'"{domain}"' in value
            ), 0)
            if line:
                search_from = line
            if domain != normalized or not DOMAIN_RE.fullmatch(domain):
                raise ValueError(f"invalid lowercase domain {domain!r} for {name} at line {line}")
            source = entry.get("source")
            if source not in {"manual", "mailbox", "meeting-learned"}:
                raise ValueError(f"invalid domain source for {domain} at line {line}")
            verified = entry.get("verified")
            if not isinstance(verified, bool):
                raise ValueError(f"verified must be boolean for {domain} at line {line}")
            if source == "manual" and not verified:
                raise ValueError(f"manual domain {domain} must be verified at line {line}")
            prior = next(
                (
                    (owned_domain, owner)
                    for owned_domain, owner in owners.items()
                    if owner[0] != name and (
                        domain == owned_domain
                        or domain.endswith("." + owned_domain)
                        or owned_domain.endswith("." + domain)
                    )
                ),
                None,
            )
            if prior:
                owned_domain, owner = prior
                raise ValueError(
                    f"overlapping domains {owned_domain} and {domain} are assigned to "
                    f"{owner[0]} (line {owner[1]}) and {name} (line {line})"
                )
            owners[domain] = (name, line)
    runtime = raw["runtime"]
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be an object")
    if not isinstance(runtime.get("tomorrow_only"), bool):
        raise ValueError("runtime.tomorrow_only must be a boolean")
    aliases = raw.get("name_aliases")
    if not isinstance(aliases, dict):
        raise ValueError("name_aliases must be an object")
    valid_targets = {
        _canonical(x.get("name")) for x in raw["accounts"]
    } | {
        _canonical(x.get(role))
        for x in raw["accounts"] for role in ACCOUNT_ROLES
        if _text(x.get(role))
    }
    for alias, target in aliases.items():
        if not _text(alias) or _canonical(target) not in valid_targets:
            raise ValueError(f"name alias {alias!r} has an unknown target")
    minimum, maximum = int(runtime.get("agenda_item_min", 3)), int(
        runtime.get("agenda_item_max", 5)
    )
    if not 3 <= minimum <= maximum <= 5:
        raise ValueError("agenda item bounds must satisfy 3 <= min <= max <= 5")


def load_scope(
    config_path: Path, db: sqlite3.Connection | None = None, workbook_path: Path | None = None,
    now: str = "",
) -> SEScope:
    text = config_path.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid se-scope YAML/JSON at line {exc.lineno}: {exc.msg}") from exc
    _validate_config(raw, text)
    fallback = raw["accounts"]
    configured_workbook = workbook_path or config_path.parent.parent / _text(raw.get("account_source"))
    source_used = "config fallback"
    account_rows = fallback
    detail = f"AccountsFY26 fallback: {configured_workbook} was not reachable"
    if configured_workbook and configured_workbook.exists():
        try:
            loaded = _xlsx_accounts(configured_workbook)
            if loaded:
                loaded_by_name = {_canonical(x["name"]): x for x in loaded}
                account_rows = [
                    {**row, **loaded_by_name.get(_canonical(row["name"]), {})}
                    for row in fallback
                ]
                account_rows.extend(
                    row for row in loaded
                    if _canonical(row["name"]) not in {
                        _canonical(x["name"]) for x in fallback
                    }
                )
                source_used = str(configured_workbook)
                detail = f"AccountsFY26 live workbook loaded: {configured_workbook}"
        except Exception as exc:
            detail = f"AccountsFY26 fallback after workbook error: {exc}"
    fallback_by_name = {_canonical(x["name"]): x for x in fallback}
    accounts: list[Account] = []
    for row in account_rows:
        configured = fallback_by_name.get(_canonical(row["name"]), {})
        domains_present = "domains" in configured
        domains = [
            DomainEntry(_domain(x["d"]), x["source"], bool(x["verified"]))
            for x in configured.get("domains", [])
        ]
        accounts.append(Account(
            name=_text(row["name"]), team=account_team_from_dict(row), domains=domains,
            domains_known=domains_present,
        ))
    if db is not None:
        init_schema(db)
        for learned in db.execute(
            "SELECT domain, account, source, verified FROM meeting_domains ORDER BY first_seen_at"
        ):
            account = next((x for x in accounts if _canonical(x.name) == _canonical(learned["account"])), None)
            if account and not any(x.d == learned["domain"] for x in account.domains):
                account.domains.append(DomainEntry(
                    learned["domain"], learned["source"], bool(learned["verified"])
                ))
                account.domains_known = True
        db.execute(
            "INSERT INTO meeting_prep_config_log(source, detail, observed_at) VALUES(?, ?, ?)",
            (source_used, detail, now or datetime.now().astimezone().isoformat()),
        )
    aliases = {_canonical(k): _text(v) for k, v in raw["name_aliases"].items()}
    index: dict[str, list[tuple[str, str]]] = {}
    for account in accounts:
        for role in ACCOUNT_ROLES:
            person = getattr(account.team, role)
            if not person or person.casefold().startswith("none "):
                continue
            canonical = _canonical(aliases.get(_canonical(person), person))
            index.setdefault(canonical, []).append((account.name, role))
    threshold = int(raw["runtime"].get("weak_signal_account_threshold", 8))
    weak = {person for person, coverage in index.items() if len({x[0] for x in coverage}) > threshold}
    return SEScope(config_path, raw, accounts, source_used, index, weak, aliases)


def _domain_matches(attendee_domain: str, configured_domain: str) -> bool:
    attendee_domain, configured_domain = _domain(attendee_domain), _domain(configured_domain)
    if configured_domain in PUBLIC_SUFFIXES or "." not in configured_domain:
        return False
    return attendee_domain == configured_domain or attendee_domain.endswith("." + configured_domain)


def _domain_match_kind(attendee_domain: str, configured_domain: str) -> str:
    attendee_domain, configured_domain = _domain(attendee_domain), _domain(configured_domain)
    if configured_domain in PUBLIC_SUFFIXES or "." not in configured_domain:
        return ""
    if attendee_domain == configured_domain:
        return "exact"
    if attendee_domain.endswith("." + configured_domain):
        return "parent"
    return ""


def _subject_accounts(scope: SEScope, subject: str) -> list[str]:
    haystack = f" {_canonical(subject)} "
    hits: list[str] = []
    for alias, target in scope.aliases.items():
        account = scope.account(target)
        if account and f" {alias} " in haystack and account.name not in hits:
            hits.append(account.name)
    for account in scope.accounts:
        name_tokens = _tokens(account.name, company=True)
        phrase = " ".join(name_tokens)
        if phrase and f" {phrase} " in haystack and account.name not in hits:
            hits.append(account.name)
    return hits


def resolve_customer(
    scope: SEScope, attendees: tuple[Attendee, ...], subject: str, organizer: str = "",
    history_accounts: list[str] | None = None,
) -> CustomerSignal:
    external_domains = [_email_domain(x.email) for x in attendees if x.external and x.email]
    for source, verified, confidence in (
        ("manual", True, "high"), ("discovered", True, "high"), ("discovered", False, "medium"),
    ):
        hits: list[str] = []
        for match_kind in ("exact", "parent"):
            for account in scope.accounts:
                for entry in account.domains:
                    source_matches = entry.source == "manual" if source == "manual" else entry.source != "manual"
                    if source_matches and entry.verified == verified and any(
                        _domain_match_kind(domain, entry.d) == match_kind for domain in external_domains
                    ):
                        hits.append(account.name)
                        break
            if hits:
                break
        if hits and confidence == "high":
            account = scope.account(hits[0])
            return CustomerSignal(tuple(dict.fromkeys(hits)), "domain", "high", account.team if len(hits) == 1 else None)
        unverified_domain_hits = hits
        if hits:
            break
    else:
        unverified_domain_hits = []
    subject_hits = _subject_accounts(scope, subject)
    if subject_hits:
        if unverified_domain_hits and set(subject_hits).isdisjoint(unverified_domain_hits):
            return CustomerSignal(
                tuple(dict.fromkeys(subject_hits + unverified_domain_hits)),
                "subject", "medium",
            )
        account = scope.account(subject_hits[0])
        return CustomerSignal(
            tuple(subject_hits), "subject", "high" if len(subject_hits) == 1 else "medium",
            account.team if len(subject_hits) == 1 else None,
        )
    if external_domains and not unverified_domain_hits:
        return CustomerSignal((), "none", "low")
    people = [organizer] + [x.name for x in attendees if not x.external]
    candidates: list[str] = []
    for person in people:
        normalized = scope.normalize_name(person)
        key = _canonical(normalized)
        if not key or key in scope.weak_signals:
            continue
        for account, _role in scope.account_team_index.get(key, []):
            if account not in candidates:
                candidates.append(account)
    if len(candidates) > 1:
        history = {_canonical(x) for x in (history_accounts or [])}
        narrowed = [x for x in candidates if _canonical(x) in history]
        if len(narrowed) == 1:
            candidates = narrowed
    if len(candidates) == 1:
        account = scope.account(candidates[0])
        if candidates[0] in unverified_domain_hits:
            return CustomerSignal(tuple(candidates), "domain", "high", account.team if account else None)
        if unverified_domain_hits:
            return CustomerSignal(
                tuple(dict.fromkeys(candidates + unverified_domain_hits)),
                "account-team", "medium",
            )
        return CustomerSignal(tuple(candidates), "account-team", "high", account.team if account else None)
    if len(candidates) > 1:
        return CustomerSignal(tuple(candidates), "account-team", "medium")
    if unverified_domain_hits:
        return CustomerSignal(tuple(unverified_domain_hits), "domain", "medium")
    return CustomerSignal((), "none", "low")


def _agenda_from_event(event: dict[str, Any]) -> ExistingAgenda | None:
    explicit = event.get("existingAgenda")
    if isinstance(explicit, dict) and _text(explicit.get("text")):
        return ExistingAgenda(explicit.get("source", "body"), _text(explicit["text"]))
    for source, key in (("attachment", "agendaAttachmentText"), ("link", "agendaLinkText")):
        if _text(event.get(key)):
            return ExistingAgenda(source, _text(event[key]))
    if _text(event.get("agendaText")):
        return ExistingAgenda("body", _text(event["agendaText"]))
    body = _text(event.get("body"))
    if body and (
        bool(event.get("bodyIsAgenda")) or bool(event.get("hasAgenda"))
        or re.search(r"(?im)^\s*agenda\s*:?\s*$", body)
    ):
        return ExistingAgenda("body", _text(event["body"]))
    return None


def _event_skip_reason(
    event: dict[str, Any], scope: SEScope, today: date | None, tomorrow_only: bool,
    observed_at: str,
) -> str:
    attendee_rows = [x for x in event.get("attendees", []) if isinstance(x, dict)]
    user_key = _canonical(scope.raw["role"].get("user_name"))
    self_row = next((
        x for x in attendee_rows
        if x.get("isSelf") or (user_key and _canonical(x.get("name")) == user_key)
    ), {})
    if bool(event.get("isCancelled")) or _canonical(event.get("status")) == "cancelled":
        return "cancelled"
    response = _canonical(event.get(
        "responseStatus", event.get("myStatus", self_row.get("responseStatus"))
    ))
    if response == "declined":
        return "declined"
    attendance = _canonical(event.get(
        "myAttendance", event.get(
            "attendeeType", event.get("myResponse", self_row.get("type"))
        )
    ))
    if attendance != "required" and event.get("isRequired") is not True:
        return "optional-or-not-required"
    if bool(event.get("isPrivate")) or bool(event.get("isPersonal")) or _canonical(
        event.get("sensitivity")
    ) in {"private", "personal"}:
        return "private-or-personal"
    if bool(event.get("isAllDay")):
        return "all-day"
    event_type = _canonical(event.get("eventType"))
    show_as = _canonical(event.get("showAs"))
    if event_type in {"focus time", "focustime", "focus"} or show_as in {"focus time", "focustime", "focus"}:
        return "focus-time"
    if event_type in {"oof", "out of office"} or show_as in {"oof", "out of office"}:
        return "out-of-office"
    others = [
        x for x in attendee_rows
        if not x.get("isSelf") and (not user_key or _canonical(x.get("name")) != user_key)
    ]
    if len(others) < int(scope.runtime.get("minimum_other_attendees", 1)):
        return "no-other-attendees"
    series = _text(event.get("seriesId", event.get("seriesMasterId")))
    deny = {_text(x) for x in scope.runtime.get("series_deny_list", [])}
    allow = {_text(x) for x in scope.runtime.get("series_allow_list", [])}
    if series and series in deny:
        return "series-denied"
    if allow and series and series not in allow:
        return "series-not-allowed"
    start_value = event.get("startLocal", event.get("start"))
    if not _text(start_value):
        return "missing-start"
    start = _iso(start_value)
    if start is None:
        return "invalid-start"
    end_value = event.get("endLocal", event.get("end"))
    if _text(end_value):
        end = _iso(end_value)
        if end is None:
            return "invalid-end"
    else:
        end = start + timedelta(hours=1)
    observed = _iso(observed_at) or datetime.now(timezone.utc)
    if end.astimezone(timezone.utc) <= observed.astimezone(timezone.utc):
        return "already-ended"
    if today and start:
        valid = _business_dates(today, int(scope.runtime.get("lookahead_business_days", 2)))
        if tomorrow_only:
            valid = {_next_business_date(today)}
        if start.date() not in valid:
            return "outside-lookahead"
    return ""


def scan_events(
    db: sqlite3.Connection, scope: SEScope, events: list[dict[str, Any]], *,
    today: date | None = None, tomorrow_only: bool | None = None, observed_at: str = "",
) -> tuple[list[MeetingCandidate], list[dict[str, str]]]:
    init_schema(db)
    candidates: list[MeetingCandidate] = []
    skips: list[dict[str, str]] = []
    stamp = observed_at or datetime.now().astimezone().isoformat()
    morning = bool(scope.runtime.get("tomorrow_only")) if tomorrow_only is None else tomorrow_only
    for event in events:
        event_id = _text(event.get("eventId", event.get("id")))
        reason = _event_skip_reason(event, scope, today, morning, stamp)
        if not event_id:
            reason = "missing-event-id"
        if reason:
            skips.append({"eventId": event_id, "reason": reason})
            db.execute(
                "INSERT INTO meeting_prep_skips(event_id, reason, observed_at) VALUES(?, ?, ?)",
                (event_id, reason, stamp),
            )
            continue
        attendees = tuple(
            Attendee(
                name=scope.normalize_name(_text(x.get("name"))),
                email=_text(x.get("email")),
                external=bool(x.get("external")),
                account_role=_text(x.get("accountRole")),
            )
            for x in event.get("attendees", [])
            if isinstance(x, dict) and not x.get("isSelf")
            and _canonical(x.get("name")) != _canonical(scope.raw["role"].get("user_name"))
        )
        signal = resolve_customer(
            scope, attendees, " ".join(
                x for x in (
                    _text(event.get("subject")), _text(event.get("seriesName")),
                ) if x
            ),
            scope.normalize_name(_text(event.get("organizerName"))),
            [_text(x) for x in event.get("historyAccountHints", [])],
        )
        candidate = MeetingCandidate(
            event_id=event_id, subject=_text(event.get("subject")),
            start_local=_text(event.get("startLocal", event.get("start"))),
            is_recurring=bool(event.get("isRecurring") or event.get("seriesId") or event.get("seriesMasterId")),
            attendees=attendees, customer_signal=signal, existing_agenda=_agenda_from_event(event),
            series_id=_text(event.get("seriesId", event.get("seriesMasterId"))),
        )
        candidates.append(candidate)
        learn_meeting_domains(db, scope, candidate, stamp)
    return candidates, skips


def _focus_for_line(scope: SEScope, line: str) -> dict[str, Any] | None:
    haystack = _canonical(line)
    for area in scope.focus_areas:
        terms = [area.get("label", ""), *area.get("keywords", [])]
        if any(_canonical(term) in haystack for term in terms if _canonical(term)):
            return area
    return None


def _scope_owner(scope: SEScope, line: str, team: AccountTeam | None) -> str:
    haystack = _canonical(line)
    for rule in scope.raw.get("scope_owners", []):
        if any(_canonical(x) in haystack for x in rule.get("keywords", [])):
            role = _text(rule.get("account_role"))
            owner = getattr(team, role, "") if team and role in ACCOUNT_ROLES else ""
            return owner or f"{role.replace('_', ' ').upper() or 'account team'} — confirm coverage"
    return ""


def _is_not_scope(scope: SEScope, line: str) -> bool:
    if _focus_for_line(scope, line):
        return False
    line_tokens = set(_tokens(line))
    for phrase in scope.raw["not_my_scope"]:
        phrase_tokens = set(_tokens(phrase))
        if {"commercial", "pricing"} & phrase_tokens and {"commercial", "pricing", "negotiation"} & line_tokens:
            return True
        if {"contract", "legal"} & phrase_tokens and {"contract", "legal"} & line_tokens:
            return True
        if "non" in phrase_tokens and "copilot" in phrase_tokens:
            deep_dive = {"deep", "dive", "dives"} & line_tokens
            non_copilot_technical = {"technical", "workload"} & line_tokens and "copilot" not in line_tokens
            if deep_dive and non_copilot_technical:
                return True
            continue
        meaningful = phrase_tokens - {"pure", "my", "scope", "terms"}
        if meaningful and meaningful <= line_tokens:
            return True
    return False


def synthesize(
    scope: SEScope, candidate: MeetingCandidate, brief: CustomerBrief | None,
    history_items: list[dict[str, str]] | None = None, research_error: str = "",
) -> AgendaRecommendation:
    unverified: list[str] = []
    risks: list[str] = []
    if brief is None and candidate.customer_signal.matched_accounts:
        unverified.append("Customer research unavailable" + (f": {research_error}" if research_error else ""))
    if candidate.existing_agenda:
        mine: list[MyAgendaItem] = []
        not_mine: list[NotMineItem] = []
        lines = [
            re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", x).strip()
            for x in candidate.existing_agenda.text.splitlines() if x.strip()
        ]
        user_name = _text(scope.raw["role"].get("user_name"))
        for line in lines:
            owner = _scope_owner(scope, line, candidate.customer_signal.team)
            if _is_not_scope(scope, line) or owner:
                not_mine.append(NotMineItem(
                    line, owner or "someone else owns this — confirm coverage"
                ))
                continue
            focus = _focus_for_line(scope, line)
            user_tokens = _tokens(user_name)
            line_tokens = set(_tokens(line))
            directed = bool(
                user_name and (
                    _canonical(user_name) in _canonical(line)
                    or (user_tokens and user_tokens[0] in line_tokens)
                )
            )
            if focus or directed:
                area_label = focus["label"] if focus else "the item directed to you"
                preparation = (
                    "Prepare a decision recommendation, options, and tradeoffs."
                    if {"decision", "approve", "choose", "select"} & set(_tokens(line))
                    else f"Bring current guidance and a decision-ready recommendation for {area_label}."
                )
                mine.append(MyAgendaItem(
                    title=line,
                    what_to_prepare=preparation,
                    focus_area_id=focus["id"] if focus else "", source_item=line,
                ))
        if brief:
            agenda_focus = {x.focus_area_id for x in mine}
            for gap in brief.interest_areas:
                if gap not in agenda_focus:
                    label = next(x["label"] for x in scope.focus_areas if x["id"] == gap)
                    risks.append(f"Agenda gap: {label} is relevant in current customer research")
        if not mine and not not_mine and len(lines) < 2:
            risks.append("Agenda is too limited to confirm that no preparation is needed")
        return AgendaRecommendation(
            candidate.event_id, "review-existing", tuple(mine), (),
            tuple(not_mine), tuple(risks), tuple(unverified),
        )
    proposed: list[ProposedAgendaItem] = []
    labels = {x["id"]: x["label"] for x in scope.focus_areas}
    user_name = _text(scope.raw["role"].get("user_name")) or "You"
    cited = {x.claim: x.source for x in brief.citations} if brief else {}
    research_claims = list(brief.open_issues + brief.initiatives + brief.adoption_signals) if brief else []
    for index, claim in enumerate(research_claims):
        source = cited.get(claim)
        if not source:
            if claim:
                unverified.append(claim)
            continue
        area_id = next((
            x for x in brief.interest_areas
            if x in labels and x not in {p.focus_area_id for p in proposed}
        ), "")
        if not area_id:
            continue
        proposed.append(ProposedAgendaItem(
            title=labels[area_id],
            why_now=f"{claim} [{source}]",
            owner=user_name, minutes=10,
            outcome="Agree the next action, owner, and success measure",
            focus_area_id=area_id, basis="customer-research",
        ))
        if len(proposed) >= int(scope.runtime.get("agenda_item_max", 5)):
            break
    for item in history_items or []:
        area = _focus_for_line(scope, _text(item.get("title")))
        if area and _text(item.get("source")) and len(proposed) < int(scope.runtime.get("agenda_item_max", 5)):
            proposed.append(ProposedAgendaItem(
                title=_text(item["title"]), why_now=f"Carry-over [{_text(item['source'])}]",
                owner=_text(item.get("owner")) or user_name, minutes=5,
                outcome=_text(item.get("outcome")) or "Close or explicitly reassign the carry-over",
                focus_area_id=area["id"], basis="history",
            ))
    minimum = int(scope.runtime.get("agenda_item_min", 3))
    for area in scope.focus_areas:
        if len(proposed) >= minimum:
            break
        if area["id"] in {x.focus_area_id for x in proposed}:
            continue
        proposed.append(ProposedAgendaItem(
            title=area["label"],
            why_now="Role-based check; validate relevance in the meeting",
            owner=user_name, minutes=5,
            outcome="Confirm relevance and identify a concrete next step",
            focus_area_id=area["id"], basis="my-role",
        ))
        unverified.append(f"{area['label']} relevance is not customer-verified")
    if brief:
        risks.extend(f"Research gap: {x}" for x in brief.gaps)
    return AgendaRecommendation(
        candidate.event_id, "propose-new", (), tuple(proposed), (),
        tuple(risks), tuple(dict.fromkeys(unverified)),
    )


def format_teams_message(
    scope: SEScope, candidate: MeetingCandidate, recommendation: AgendaRecommendation,
    brief: CustomerBrief | None,
) -> str:
    if (
        recommendation.mode == "review-existing" and not recommendation.my_items
        and not recommendation.not_mine and not recommendation.risks
        and not recommendation.unverified
    ):
        return f"**Meeting prep — {candidate.subject}:** Nothing needed from you."
    start = _iso(candidate.start_local)
    when = start.strftime("%a, %b %d · %I:%M %p").replace(" 0", " ") if start else candidate.start_local
    lines = [f"**Meeting prep — {candidate.subject} · {when}**"]
    accounts = candidate.customer_signal.matched_accounts
    if accounts:
        likely = (
            f" (likely — {candidate.customer_signal.confidence} confidence)"
            if candidate.customer_signal.confidence != "high" else ""
        )
        lines.append(f"**Customer:** {', '.join(accounts)}{likely} · **Your role:** required")
        team = candidate.customer_signal.team
        if team:
            team_bits = [f"SSP {team.ssp}" if team.ssp else "", f"AE {team.ae}" if team.ae else "",
                         f"CSAM {team.csam}" if team.csam else ""]
            lines.append("**Account team:** " + " · ".join(x for x in team_bits if x))
    if recommendation.my_items:
        lines.append("**What applies to you**")
        lines.extend(f"• {x.title} — {x.what_to_prepare}" for x in recommendation.my_items[:3])
    if recommendation.proposed_items:
        lines.append("**Recommended agenda** _(no agenda on the invite)_")
        lines.extend(
            f"{i}. {x.title} — {x.why_now} · owner: {x.owner} · {x.minutes}m · outcome: {x.outcome}"
            for i, x in enumerate(recommendation.proposed_items[:5], 1)
        )
    if brief and brief.citations:
        lines.append(f"**Customer context** _(as of {brief.as_of or 'unknown'})_")
        lines.extend(f"• {x.claim} [{x.source}]" for x in brief.citations[:2])
    if recommendation.not_mine:
        lines.extend(
            f"**Not yours → {item.suggested_owner}:** {item.item}"
            for item in recommendation.not_mine[:2]
        )
    combined_unverified = list(recommendation.unverified) + list(recommendation.risks)
    if combined_unverified:
        lines.append("**Unverified / risks:** " + "; ".join(combined_unverified[:3]))
    return "\n".join(lines[:15])


def fingerprint(candidate: MeetingCandidate, brief: CustomerBrief | None) -> tuple[str, str]:
    brief_value = brief.to_dict() if brief else {"unavailable": True}
    brief_fingerprint = _json_hash(brief_value)
    start = _iso(candidate.start_local)
    meeting_value = {
        "seriesId": candidate.series_id,
        "subject": _canonical(candidate.subject),
        "startTime": start.timetz().isoformat() if start else candidate.start_local,
        "attendees": sorted(
            (
                _canonical(item.name), item.email.casefold(), item.external,
                item.account_role.casefold(),
            )
            for item in candidate.attendees
        ),
        "customerSignal": candidate.customer_signal.to_dict(),
        "existingAgenda": candidate.existing_agenda.to_dict()
        if candidate.existing_agenda else None,
    }
    return _json_hash({"meeting": meeting_value, "brief": brief_fingerprint}), brief_fingerprint


def queue_teams_prep(
    db: sqlite3.Connection, candidate: MeetingCandidate, recommendation: AgendaRecommendation,
    message: str, brief: CustomerBrief | None, now: str,
) -> dict[str, Any]:
    init_schema(db)
    fp, brief_fp = fingerprint(candidate, brief)
    series_key = candidate.series_id or candidate.event_id
    prior = db.execute(
        "SELECT fingerprint FROM meeting_prep_fingerprints WHERE series_key = ?", (series_key,)
    ).fetchone()
    if prior and prior["fingerprint"] == fp:
        return {"queued": False, "reason": "unchanged-recurring-fingerprint", "fingerprint": fp}
    approval_id = "meeting_prep_" + hashlib.sha256(
        f"{series_key}:{fp}".encode("utf-8")
    ).hexdigest()[:24]
    details = {
        "sourceType": "meeting-prep", "sourceId": candidate.event_id,
        "eventId": candidate.event_id, "seriesId": candidate.series_id,
        "startLocal": candidate.start_local, "fingerprint": fp,
        "briefFingerprint": brief_fp, "seriesKey": series_key,
        "deliveryMode": "teams-self",
        "recommendation": recommendation.to_dict(), "outboundAction": "not_performed",
        "approvalRequired": True,
    }
    db.execute(
        """
        INSERT INTO approvals(
          id, created_at, updated_at, employee, action_type, risk, title, preview,
          destination, status, details_json
        ) VALUES(?, ?, ?, 'Mina', 'meeting-prep', 'low', ?, ?, 'Teams 1:1 (self)', 'pending', ?)
        ON CONFLICT(id) DO UPDATE SET
          updated_at=excluded.updated_at, preview=excluded.preview,
          details_json=excluded.details_json
        """,
        (approval_id, now, now, f"Meeting prep ready: {candidate.subject}", message,
         json.dumps(details, ensure_ascii=False)),
    )
    return {
        "queued": True, "approvalId": approval_id, "fingerprint": fp,
        "seriesKey": series_key,
    }


def register_delivery_job(
    db: sqlite3.Connection, job_id: str, details: dict[str, Any], queued_at: str,
) -> None:
    init_schema(db)
    required = ("seriesKey", "eventId", "fingerprint", "briefFingerprint")
    if any(not _text(details.get(key)) for key in required):
        raise ValueError("meeting-prep approval is missing recurrence metadata")
    db.execute(
        """
        INSERT INTO meeting_prep_delivery_jobs(
          job_id, series_key, event_id, fingerprint, brief_fingerprint, queued_at
        ) VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO NOTHING
        """,
        (
            job_id, _text(details["seriesKey"]), _text(details["eventId"]),
            _text(details["fingerprint"]), _text(details["briefFingerprint"]), queued_at,
        ),
    )


def promote_delivered_fingerprint(
    db: sqlite3.Connection, job_id: str, delivered_at: str,
) -> bool:
    init_schema(db)
    row = db.execute(
        "SELECT * FROM meeting_prep_delivery_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None or row["delivered_at"]:
        return False
    db.execute(
        """
        INSERT INTO meeting_prep_fingerprints(
          series_key, event_id, fingerprint, brief_fingerprint, prepared_at
        ) VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(series_key) DO UPDATE SET event_id=excluded.event_id,
          fingerprint=excluded.fingerprint, brief_fingerprint=excluded.brief_fingerprint,
          prepared_at=excluded.prepared_at
        """,
        (
            row["series_key"], row["event_id"], row["fingerprint"],
            row["brief_fingerprint"], delivered_at,
        ),
    )
    db.execute(
        "UPDATE meeting_prep_delivery_jobs SET delivered_at = ? WHERE job_id = ?",
        (delivered_at, job_id),
    )
    return True


def _manual_owner(scope: SEScope, domain: str) -> str:
    for account in scope.accounts:
        if any(
            x.source == "manual" and _domain_matches(domain, x.d)
            for x in account.domains
        ):
            return account.name
    return ""


def _log_domain(
    db: sqlite3.Connection, domain: str, account: str, action: str, detail: str, now: str,
) -> None:
    db.execute(
        "INSERT INTO meeting_domain_log(domain, account, action, detail, observed_at) VALUES(?,?,?,?,?)",
        (domain, account, action, detail, now),
    )


def assign_discovered_domain(
    db: sqlite3.Connection, scope: SEScope, domain: str, account: str, source: DomainSource,
    verified: bool, now: str, *, last_seen: str = "",
) -> bool:
    domain = _domain(domain)
    manual = _manual_owner(scope, domain)
    if manual and _canonical(manual) != _canonical(account):
        _log_domain(db, domain, account, "manual-conflict",
                    f"Rejected; immutable manual owner is {manual}", now)
        return False
    existing = db.execute(
        "SELECT account FROM meeting_domains WHERE domain = ?", (domain,)
    ).fetchone()
    if existing and _canonical(existing["account"]) != _canonical(account):
        _log_domain(db, domain, account, "conflict",
                    f"Rejected; discovered owner is {existing['account']}", now)
        return False
    if manual:
        _log_domain(db, domain, manual, "manual-preserved", "Manual assignment unchanged", now)
        return True
    db.execute(
        """
        INSERT INTO meeting_domains(
          domain, account, source, verified, confirmation_count, first_seen_at, last_seen_at
        ) VALUES(?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET last_seen_at=excluded.last_seen_at,
          verified=MAX(meeting_domains.verified, excluded.verified)
        """,
        (domain, account, source, int(verified), now, last_seen or now),
    )
    _log_domain(db, domain, account, "assigned", f"source={source}; verified={verified}", now)
    return True


def _domain_account_candidates(scope: SEScope, domain: str) -> list[str]:
    stem_tokens = set(_tokens(domain.split(".")[0], company=True))
    compact = "".join(stem_tokens)
    scored: list[tuple[float, str]] = []
    for account in scope.accounts:
        account_tokens = set(_tokens(account.name, company=True))
        if not account_tokens:
            continue
        joined = "".join(account_tokens)
        overlap = len(stem_tokens & account_tokens) / len(stem_tokens | account_tokens) if stem_tokens else 0
        score = 1.0 if compact and compact == joined else overlap
        if score > 0:
            scored.append((score, account.name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored or scored[0][0] < 0.6:
        return []
    return [name for score, name in scored if score == scored[0][0]]


def discover_mailbox_domains(
    db: sqlite3.Connection, scope: SEScope, messages: list[dict[str, Any]], now: str,
    mode: str = "nightly",
) -> dict[str, Any]:
    init_schema(db)
    deny = {_domain(x) for x in scope.raw.get("domain_deny_list", [])}
    manual_domains = {
        entry.d for account in scope.accounts for entry in account.domains
        if entry.source == "manual"
    }
    now_value = _iso(now)
    cutoff = (
        now_value - timedelta(days=31 * int(scope.runtime.get("domain_discovery_months", 12)))
        if now_value else None
    )
    observed: dict[str, dict[str, Any]] = {}
    for index, message in enumerate(messages):
        received = _iso(message.get("receivedAt"))
        if received is None or now_value is None:
            continue
        if received.timestamp() > now_value.timestamp() + 300:
            continue
        if cutoff and received.timestamp() < cutoff.timestamp():
            continue
        message_id = _text(message.get("id")) or f"row-{index}"
        for participant in message.get("participants", []):
            email = _text(participant.get("email") if isinstance(participant, dict) else participant)
            local = email.split("@", 1)[0].casefold()
            domain = _email_domain(email)
            denied = any(
                _domain_matches(domain, denied_domain) for denied_domain in deny
            )
            if (
                not domain or (denied and domain not in manual_domains)
                or domain in PUBLIC_SUFFIXES
                or "noreply" in local or "no-reply" in local
                or "marketing" in local
            ):
                continue
            item = observed.setdefault(domain, {"ids": set(), "lastSeen": ""})
            item["ids"].add(message_id)
            item["lastSeen"] = max(item["lastSeen"], _text(message.get("receivedAt")))
    report: dict[str, Any] = {
        "observed": len(observed), "assigned": [], "pending": [], "conflicts": [],
        "accountsWithUnknownDomains": sum(not x.domains_known for x in scope.accounts),
        "accountsCheckedWithNone": sum(x.domains_known and not x.domains for x in scope.accounts),
    }
    for domain, evidence in sorted(observed.items()):
        manual = _manual_owner(scope, domain)
        if manual:
            _log_domain(db, domain, manual, "manual-observed", "Mailbox evidence confirmed seed", now)
            continue
        candidates = _domain_account_candidates(scope, domain)
        if len(candidates) == 1:
            assigned = assign_discovered_domain(
                db, scope, domain, candidates[0], "mailbox", False, now,
                last_seen=evidence["lastSeen"],
            )
            (report["assigned"] if assigned else report["conflicts"]).append({
                "domain": domain, "account": candidates[0],
                "messageCount": len(evidence["ids"]), "lastSeen": evidence["lastSeen"],
            })
        else:
            reason = "multiple-candidates" if candidates else "no-strong-token-match"
            db.execute(
                """
                INSERT INTO pending_domains(
                  domain, candidates_json, reason, confirmation_count, message_count,
                  first_seen_at, last_seen_at
                ) VALUES(?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET candidates_json=excluded.candidates_json,
                  reason=excluded.reason, message_count=excluded.message_count,
                  last_seen_at=excluded.last_seen_at
                """,
                (
                    domain, json.dumps(candidates), reason, len(evidence["ids"]),
                    now, evidence["lastSeen"],
                ),
            )
            report["pending"].append({
                "domain": domain, "candidates": candidates, "reason": reason,
                "messageCount": len(evidence["ids"]), "lastSeen": evidence["lastSeen"],
            })
    db.execute(
        """
        INSERT INTO meeting_domain_runs(
          mode, observed_count, assigned_count, pending_count, conflict_count, finished_at
        ) VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            mode if mode in {"bootstrap", "nightly", "manual"} else "manual",
            report["observed"], len(report["assigned"]), len(report["pending"]),
            len(report["conflicts"]), now,
        ),
    )
    return report


def learn_meeting_domains(
    db: sqlite3.Connection, scope: SEScope, candidate: MeetingCandidate, now: str,
) -> None:
    signal = candidate.customer_signal
    if (
        signal.resolved_by not in {"subject", "account-team"} or signal.confidence != "high"
        or len(signal.matched_accounts) != 1
    ):
        return
    account = signal.matched_accounts[0]
    threshold = int(scope.runtime.get("meeting_domain_confirmations", 2))
    for attendee in candidate.attendees:
        domain = _email_domain(attendee.email) if attendee.external else ""
        if not domain:
            continue
        manual = _manual_owner(scope, domain)
        if manual and _canonical(manual) != _canonical(account):
            _log_domain(db, domain, account, "manual-conflict",
                        f"Meeting evidence rejected; immutable manual owner is {manual}", now)
            continue
        db.execute(
            """
            INSERT OR IGNORE INTO meeting_domain_evidence(
              domain, account, evidence_key, source, observed_at
            ) VALUES(?, ?, ?, 'meeting-learned', ?)
            """,
            (domain, account, candidate.event_id, now),
        )
        count = db.execute(
            "SELECT COUNT(*) FROM meeting_domain_evidence WHERE domain=? AND account=?",
            (domain, account),
        ).fetchone()[0]
        if count >= threshold:
            if assign_discovered_domain(
                db, scope, domain, account, "meeting-learned", True, now
            ):
                db.execute(
                    "UPDATE meeting_domains SET confirmation_count=? WHERE domain=?",
                    (count, domain),
                )
            db.execute("DELETE FROM pending_domains WHERE domain = ?", (domain,))
        else:
            db.execute(
                """
                INSERT INTO pending_domains(
                  domain, candidates_json, reason, confirmation_count, message_count,
                  first_seen_at, last_seen_at
                ) VALUES(?, ?, 'awaiting-independent-confirmations', ?, 0, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET confirmation_count=excluded.confirmation_count,
                  last_seen_at=excluded.last_seen_at
                """,
                (domain, json.dumps([account]), count, now, now),
            )


def confirm_pending_domain(
    db: sqlite3.Connection, scope: SEScope, domain: str, account: str, now: str,
) -> bool:
    row = db.execute("SELECT domain FROM pending_domains WHERE domain = ?", (_domain(domain),)).fetchone()
    if not row:
        raise ValueError(f"pending domain not found: {domain}")
    if not scope.account(account):
        raise ValueError(f"unknown account: {account}")
    assigned = assign_discovered_domain(db, scope, domain, account, "mailbox", True, now)
    if assigned:
        db.execute("DELETE FROM pending_domains WHERE domain = ?", (_domain(domain),))
    return assigned


def domain_run_report(db: sqlite3.Connection) -> dict[str, Any]:
    last_run = db.execute(
        "SELECT * FROM meeting_domain_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "bootstrapNeeded": last_run is None,
        "lastRun": dict(last_run) if last_run else None,
        "promoted": [
            dict(x) for x in db.execute(
                "SELECT domain, account, source, verified, confirmation_count, last_seen_at "
                "FROM meeting_domains ORDER BY last_seen_at DESC"
            )
        ],
        "pending": [
            {
                **dict(x), "candidates": json.loads(x["candidates_json"]),
            }
            for x in db.execute("SELECT * FROM pending_domains ORDER BY last_seen_at DESC")
        ],
        "conflicts": [
            dict(x) for x in db.execute(
                "SELECT domain, account, action, detail, observed_at FROM meeting_domain_log "
                "WHERE action LIKE '%conflict%' ORDER BY observed_at DESC"
            )
        ],
    }
