# Daily Flow Team — Architecture

## 1. System diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  YOUR WINDOWS MACHINE — nothing below ever leaves it                         │
│                                                                              │
│  ┌────────────────────┐        reads the skill, then calls the local API     │
│  │  Microsoft Scout   │───────────────────────────────┐                      │
│  │  (the agent host)  │                               │                      │
│  └─────────┬──────────┘                               │                      │
│            │ loads at startup                         │                      │
│            ▼                                          │                      │
│  ┌────────────────────────────────┐                   │                      │
│  │ SKILLS  (~\.scout\m-skills\)   │                   │                      │
│  │  daily-flow-setup/SKILL.md     │  the install +    │                      │
│  │  daily-flow-team/SKILL.md      │  operating manual │                      │
│  └────────────────────────────────┘                   │                      │
│            ▲                                          │                      │
│            │ installed by                             │                      │
│  ┌─────────┴──────────┐                               │                      │
│  │   install.ps1      │  copies app + skills,         │                      │
│  │   preflight.ps1    │  writes config.json,          │                      │
│  └────────────────────┘  starts the app               │                      │
│                                                       │                      │
│  ┌────────────────────────────────┐                   │                      │
│  │  AUTOMATIONS (in Scout)        │  4 scheduled      │                      │
│  │  Morning Brief   7am weekdays  │  prompts, each    │                      │
│  │  Evening Wrap-up 5pm weekdays  │  a /daily-flow-   │                      │
│  │  Work Pulse      hourly        │  team run         │                      │
│  │  Attention Major every 5 min   │                   │                      │
│  └───────────────┬────────────────┘                   │                      │
│                  │  HTTP to 127.0.0.1                 │                      │
│                  ▼                                    ▼                      │
│  ╔══════════════════════════════════════════════════════════════════════╗    │
│  ║  APP  —  app/app.py   http://127.0.0.1:8787  (loopback only)         ║    │
│  ║                                                                      ║    │
│  ║   Handler ── guards ──► routes ──► domain functions ──► SQLite       ║    │
│  ║      │      origin                                                   ║    │
│  ║      │      token (opt-in)                                           ║    │
│  ║      │      10 MB body cap                                           ║    │
│  ║      │      path-traversal resolve()                                 ║    │
│  ║      │                                                               ║    │
│  ║      ├──► /api/*        JSON            ──┐                          ║    │
│  ║      ├──► /api/events   SSE stream       │                           ║    │
│  ║      ├──► /api/documents/* file viewer   │                           ║    │
│  ║      └──► /            static files      │                           ║    │
│  ╚══════════════════════════════════════════╪═══════════════════════════╝    │
│                    │                        │                                │
│                    ▼                        ▼                                │
│  ┌────────────────────────────┐   ┌──────────────────────────────────┐       │
│  │ SQLite  app/data/          │   │ STATIC FRONTEND app/static/      │       │
│  │   daily_flow.db  (WAL)     │   │   index.html · app.js · css      │       │
│  │   local tables             │◄──│   polls /api/state, listens on   │       │
│  │   7 retention triggers     │   │   /api/events, renders the board │       │
│  └────────────────────────────┘   └──────────────────────────────────┘       │
│                    │                                                         │
│                    ▼                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐      │
│  │ DOCUMENT ROOT — OneDrive\Scout\...  (briefs, prep notes, reports)  │      │
│  └────────────────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────────────────┘
```

There is no cloud component, no telemetry, and no outbound network call from the app itself. The
only process that talks to Microsoft 365 is Scout, using the user's own signed-in session.

## 2. Component responsibilities

| Component | File(s) | Responsibility |
| --- | --- | --- |
| **Installer** | `install.ps1` | Resolves Python, copies `app/` and `skills/` into place, preserves full config/runtime data on upgrade, starts the app, verifies `/api/health`, writes `install.log`, and optionally resets only the application layer for a separately managed overlay. |
| **Python doctor** | `preflight.ps1`, `app/preflight.ps1` | Finds a real Python 3.9+ (rejects the Windows Store stub), can install 3.13 via winget, scans install locations dynamically. The two copies are byte-identical by design — edit the root one and copy it over. |
| **Launcher / stopper** | `app/start-app.ps1`, `app/stop-app.ps1` | Start the app hidden, write `daily-flow-app.pid`, capture stderr to `app.err.log`, poll `/api/health`, open the dashboard. |
| **Packager** | `package-share.ps1` | Builds the shareable ZIP from an explicit allowlist, prunes runtime artifacts, and refuses to build unless `verify-clean.ps1` passes and the version is consistent across `manifest.json`, `README.md` and `CHANGELOG.md`. |
| **Clean-room scanner** | `verify-clean.ps1` | Fails on personal identifiers, email addresses, and private/runtime artifacts (`config.json`, the database, `.local-token`, logs). |
| **App / API** | `app/app.py` | The entire backend: HTTP routing, request guards, all domain logic, the SQLite schema, sweep and job orchestration, document rendering. Standard library only. |
| **Prompts** | `app/prompts/*.md` | Large model-facing instruction blocks, loaded at runtime by `_load_prompt()`. Kept out of the Python source so they can be edited and reviewed as prose. |
| **Frontend** | `app/static/` | Single-page dashboard. No build step and no framework — it is plain HTML/CSS/JS served directly. |
| **Skills** | `skills/daily-flow-setup/SKILL.md`, `skills/daily-flow-team/SKILL.md` | What Scout actually reads. `setup` is the guided install wizard; `team` is the operating manual every run follows. |
| **Automations** | `automations/automations.json` | The four scheduled prompts, with `{{APP_URL}}` / `{{DOCUMENT_ROOT}}` placeholders the setup wizard substitutes. |
| **Tests** | `test/smoke-test.ps1`, `test/check_automations.py`, `test/test_layered_install_contract.py` | End-to-end HTTP smoke, metadata consistency, and independent core/overlay contract checks. |

The public/private package boundary and core-first transaction are specified in
[the layered install contract](LAYERED-INSTALL-CONTRACT.md). Public setup has no overlay release
channel and remains public-only.

## 3. Data flow for a typical automation run

Taking the **Morning Brief** at 7am as the example:

```
 1. Scout's scheduler fires the automation on its cron.
 2. The prompt says "Use /daily-flow-team as Major", so Scout loads
    skills/daily-flow-team/SKILL.md and adopts the Major role.
 3. POST /api/sweep/start   {source, model, channels}     -> {sweepId}
       app writes a row to sweep_runs (status = running)
 4. GET  /api/state?view=agent
       app reads SQLite, projects the lean agent view, returns it.
       This is the ONLY state read a run should make.
 5. The agent host gathers private context through its configured read-only connectors. A connector
    may submit a bounded normalized snapshot; credentials and raw provider responses never pass
    through or persist in the app.
 6. Scout classifies and summarises, then writes results back:
       POST /api/inbox-signals   sanitized signals + recommendations
       POST /api/inbox-invites   RSVP approval cards
       POST /api/work-ledger     what was actually done
       POST /api/jobs/<id>       progress on any job it picked up
       -> every write goes through a retention trigger that forbids deletes
 7. Any artifact it produced is saved under the DOCUMENT ROOT, and the
    app serves it read-only at /api/documents/<path>.
 8. POST /api/sweep/finish  {sweepId, status, counts, passes, verify}
       app closes the sweep_runs row.
 9. Each write bumps a version counter; /api/events pushes a nudge.
10. The open dashboard receives the SSE nudge and re-reads /api/state.
    The board fills in without a refresh.
```

The invariant throughout: **the app is the source of truth, and the app never acts.** It stores,
projects and displays. Every outward action (sending mail, replying in Teams, RSVPing) is done by
Scout, and only after the user approved it in the dashboard.

## 4. Database schema

SQLite in WAL mode at `app/data/daily_flow.db`. Fourteen tables.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `employees` | The digital team roster and each member's trust level. | `name`, `role`, `trust_level`, `enabled`, `protocol_json`, `origin`, `status`, `lane`, `skills_json` |
| `jobs` | The unit of work. Everything the team does is a job. | `id`, `employee`, `type`, `title`, `status`, `priority`, `source`, `thread_id`, `instructions`, `result_summary`, `blocker`, `send_state`, `quality_review`, `quality_verdict`, `risk_level`, `handoff_to`, `eta`, `sla_breached`, `knowledge_links_json`, `quality_audit_json`, `redaction_required`, `redaction_applied`, `talk_track_json`, `conference_pack_json`, `chart_spec_json`, `flow_doc_json`, `runtime_inventory_json`, `brand_voice_profile` |
| `approvals` | Cards waiting for a human decision. | `id`, `employee`, `action_type`, `risk`, `title`, `preview`, `destination`, `status`, `user_guidance` |
| `inbox_signals` | Sanitized mail/Teams signals from a sweep. | `source_id`, `subject`, `sender`, `signal_type`, `priority`, `summary`, `recommendation`, `status` |
| `events` | The human-readable activity log. | `id`, `created_at`, `employee`, `summary`, `detail`, `sensitivity` |
| `work_ledger_entries` | What was actually accomplished, for the wrap-up and impact view. | `occurred_at`, `employee`, `category`, `title`, `customer`, `impact_level`, `impact_summary`, `evidence_json` |
| `chat_threads` / `chat_messages` | The dashboard chat with the team. | `thread_id`, `employee`, `sender`, `message`, `job_id` |
| `decision_memory` | Remembers dismissals so the same item is not re-surfaced. | `content_key`, `decision`, `ttl_until`, `status` |
| `sweep_runs` | One row per sweep, start to finish. | `id`, `started_at`, `finished_at`, `source`, `model`, `status`, `counts_json`, `verify_json` |
| `knowledge_entries` | Casey's knowledge graph: people, projects, commitments, decisions, files, preferences. Soft-deleted only. | `id`, `type`, `title`, `summary`, `details_json`, `status`, `owner`, `due_date`, `source_type`, `source_id`, `related_ids_json`, `last_verified_at` |
| `connector_snapshots` | Bounded provider-neutral observations ingested by authenticated server-to-server callers. | `schema_version`, `provider`, `capability`, `subject`, `observed_at`, `expires_at`, `status`, scope/provenance/data/error JSON |
| `career_profile` | Current role, target role, review rubric — used to frame impact. | `current_role`, `target_role`, `review_rubric` |
| `app_meta` | Key/value store, including the state version that drives SSE. | `key`, `value`, `updated_at` |

### Indexes

`idx_approvals_status`, `idx_decision_memory_key`, `idx_events_created`,
`idx_inbox_signals_status`, `idx_jobs_employee`, `idx_jobs_status`, `idx_jobs_thread`,
`idx_knowledge_status`, `idx_knowledge_type`, `idx_messages_thread`, `idx_sweep_runs_started`,
`idx_work_ledger_date`, `idx_work_ledger_occurred`.

These keep the dashboard's load time flat as history accumulates — `/api/state` reads by status,
by employee and by timestamp on every request.

### Retention triggers

Seven `BEFORE DELETE … RAISE(ABORT)` triggers guarantee that history cannot be deleted:
`preserve_approvals_delete`, `preserve_chat_messages_delete`, `preserve_chat_threads_delete`,
`preserve_events_delete`, `preserve_inbox_signals_delete`, `preserve_jobs_delete`,
`preserve_work_ledger_entries_delete`.

A sweep can never quietly drop the user's record. The single sanctioned exception is
`POST /api/reset`, which drops the triggers inside a transaction, wipes, commits, and then calls
`init_db()` to recreate all seven. See `docs/API.md`.

### Durability

The database runs in WAL mode. On `SIGTERM`, `SIGINT` or `KeyboardInterrupt` the app runs
`PRAGMA wal_checkpoint(TRUNCATE)` before exiting, so a normal stop leaves a single consistent
`.db` file rather than a `.db` plus a large `-wal` sidecar. `POST /api/maintenance` performs the
same checkpoint on demand.

> **Locking note.** The checkpoint must run on a connection with no open read. `connect()`
> deliberately does not close its connections, so the checkpoint path closes its own connection
> explicitly first — otherwise SQLite raises `database table is locked`.

## 5. Capability layer

Eight endpoints (§4 of `API.md`) give the roles concrete behaviour to call rather than leaving
each employee to improvise in prose. Three properties shape the design:

**They are pure transforms.** Each computes from its request body and returns. None writes to the
database. Persisting a result is a separate, explicit act — the employee stamps it onto a job via
`POST /api/jobs/<jobId>` — so a scratch calculation cannot quietly become part of the record.

**They are local and dependency-free.** No model call, no network. That is what lets
`/api/content-pass` act as a *hard* pre-send gate: a check that depends on a model round-trip
cannot reliably block a send, because it can fail open exactly when it is needed. The tradeoff is
that the sensitive-text scan is pattern-based and therefore a floor rather than a guarantee, and it
says so in its own response.

**They refuse rather than guess.** `/api/chart-spec` warns instead of emitting a misleading chart;
`/api/conference-pack` returns bracketed gaps instead of inventing a speaker's credentials;
`/api/runtime-inventory` reports only what it can verify from disk and states plainly that it
cannot see Scout's tool list. A confident wrong answer is the failure mode worth engineering
against here.

They inherit the request guards automatically: auth and origin checks run once at the top of
`do_POST`, before routing.

### Connector boundary

Core defines a read-only provider contract rather than a provider adapter. It never obtains a
provider token or makes an outbound provider request. An authenticated server-to-server caller
submits the normalized envelope to `/api/connector-snapshots`; its existing bearer token is always
required, even in legacy no-auth mode. The same-origin browser guard remains in force and no broad
CORS policy is enabled.

Snapshots are capped at 256 KiB after normalization, reject secret-bearing fields, retain explicit
requested versus granted scopes, and carry structured provenance and errors. Health preserves the
distinction among unavailable, unauthorized, forbidden, not-found, rate-limited, stale, and partial
instead of collapsing them into a boolean.

Casey's context vocabulary is intentionally open. The common terms are discoverable at
`/api/context-vocabulary`, while arbitrary non-empty extension types are stored verbatim in both
knowledge entries and connector data.

The results are counted back into `/api/state` as `capabilitySummary`, which carries the same
`readable` flag as the other summaries so that "nothing recorded" and "cannot read the table" stay
distinguishable.

## 6. Timezone handling

Everything stored is UTC. Everything *displayed or bucketed into a day* is local.

`_resolve_app_timezone()` picks the local zone in this order:

1. `config.json` → `"timezone"` (an IANA name such as `America/New_York`)
2. environment → `DAILY_FLOW_TIMEZONE`
3. the Windows registry's current time zone, mapped through a Windows→IANA table
4. a built-in `America/Los_Angeles` fallback that works even without the tz database

The resolved name is exposed as `APP_TIMEZONE_NAME`. It decides which entries land in "today" for
the work ledger, the date stamped on an export filename, and every timestamp the dashboard shows.

This matters because "did this happen today?" is the question the morning brief and evening
wrap-up are both built on. Getting the zone wrong shifts an entire day's work into the wrong
bucket.

## 7. Security model

The app is loopback-only, so the threat model is *other software and other browser tabs on the
same machine*, not the internet.

| Control | What it stops |
| --- | --- |
| Bind to `127.0.0.1` | Any access from another machine. |
| Origin allowlist on mutations | A malicious page in another tab POSTing to your local app (CSRF). |
| Local bearer token (opt-in `--auth`) | Any other local process reading your private state or queuing work. |
| 10 MB body cap | A local process wedging the app with an enormous upload. |
| `resolve()` + `is_relative_to()` on every file path | `..` and symlink escapes out of the static or document roots. |
| Retention triggers | Silent history loss, whether from a bug or a bad sweep. |
| `verify-clean.ps1` | Shipping a package containing personal data, a database, or a local token. |

Auth defaults to **off** for backward compatibility with existing installs and automations.
`--auth` is the recommended mode and is what new installs should use.
## Watch/follow-up persistence

The `watches` SQLite table is a provider-neutral local resource included in dashboard state, the
lean agent projection, reset, and export. It distinguishes direct conditional watches from
investigative follow-ups, stores explicit parent/origin relationships for spawned watches and
action-items, and keeps provenance/freshness and lifecycle timestamps. `DELETE` is a soft
`removed` transition, so history remains auditable. The model is intentionally passive:
`proposed_action` and `proposed_next_step` are data, never execution instructions.
