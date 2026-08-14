# Daily Flow Team — HTTP API

The app is a Python standard-library HTTP server that binds **127.0.0.1 only**. It is never
reachable from another machine. Everything below is served from `http://127.0.0.1:<port>/`,
where `<port>` defaults to `8787` and is set by `app/config.json` (`port`) or the
`DAILY_FLOW_PORT` environment variable.

All responses are `application/json; charset=utf-8` unless stated otherwise. Successful
mutations return `{"ok": true, ...}`. Errors return `{"ok": false, "error": "<message>"}` with a
non-2xx status.

---

## 1. Authentication

Auth is **opt-in** so that existing installs and automations keep working unchanged.

| Mode | How to start | Behaviour |
| --- | --- | --- |
| `--no-auth` (default) | `python app.py` | No token is checked. Identical to previous releases. |
| `--auth` (recommended) | `python app.py --auth` | Private endpoints require a local token. |

`--auth` can also be turned on without editing the command line:

* `app/config.json` → `"requireLocalToken": true`
* environment → `DAILY_FLOW_REQUIRE_TOKEN=1`

### The token

* A 32-byte hex token is generated on first start and stored in `app/.local-token`
  (git-ignored, and rejected by `verify-clean.ps1` so it can never be packaged).
* Its absolute path is written into `app/config.json` as `localTokenPath`.
* It is printed once at startup: `[auth] Local token: <token>`.

### Sending the token

Either header is accepted:

```http
Authorization: Bearer <token>
X-Local-Token: <token>
```

`/api/events` **also** accepts `?token=<token>` in the query string, because the browser
`EventSource` API cannot set request headers. No other endpoint accepts the token in the URL.

The dashboard stores the token in `localStorage` under `dailyflow_token` (there is a field for it
in the **Your data** section) and attaches it to every call.

### What is protected

| Category | Auth in `--auth` mode |
| --- | --- |
| Every `POST`, `DELETE`, `PATCH` | **Required** |
| `GET` under `/api/state`, `/api/gate`, `/api/impact-ledger`, `/api/activity-log`, `/api/jobs/`, `/api/sweeps`, `/api/documents/`, `/api/export`, `/api/events`, `/api/knowledge`, `/api/watches`, `/api/runtime-inventory`, `/api/connector-snapshots`, `/api/connector-health`, `/api/context-vocabulary` | **Required** |
| `GET /api/health` | Never required |
| Static files (`/`, `/app.js`, `/styles.css`, …) | Never required |

A missing or wrong token returns **403** `{"ok": false, "error": "local token required"}`.
Comparison uses `secrets.compare_digest`, so it is not timing-sensitive.

## 2. Request guards (apply to every route)

| Guard | Rule | Failure |
| --- | --- | --- |
| **Origin** | On `POST`/`DELETE`/`PATCH`, if an `Origin` header is present it must be `http://127.0.0.1:<port>` or `http://localhost:<port>`. A *missing* Origin is allowed, so curl, PowerShell and Scout keep working. | `403 cross-origin request rejected` |
| **Body size** | `Content-Length` above **10,485,760 bytes (10 MB)**. The body is never read, and the connection is closed so the stream cannot desync. | `413 request body too large` |
| **Path traversal** | Static and document paths are `.resolve()`d and must be `is_relative_to()` their root (`app/static`, the document root). Symlinks and `..` are both covered. | `403` |

---

## 3. Endpoints

### `GET /api/health`

Liveness probe. **Never requires auth** — installers, `start-app.ps1` and the smoke test poll it
to decide whether the app came up.

```json
{ "ok": true, "version": "4.3.1", "serverTime": "2026-08-06T13:40:26.369256-07:00" }
```

`version` comes from `manifest.json`, falling back to `app/.installed-version`, falling back to
`0.0.0`. It is never hardcoded.

---

### `GET /api/state`

The dashboard's whole world in one document: employees, jobs, approvals, events, work ledger,
impact ledger, gate, metrics, career profile, decision memory, plus three summaries:

| Key | Contents |
| --- | --- |
| `qualitySummary` | `readable`, `flaggedForReview`, `awaitingReview`, `heldJobs`, `heldItems`, `staleAutomations`, `lastSweepAt`, `lastVerdict`, `lastVerdictAt` — Quinn's risk register. |
| `knowledgeSummary` | `readable`, `totalEntries`, `overdueCommitments`, `staleEntries`, `byType`, `lastUpdated` — Casey's knowledge graph. |
| `capabilitySummary` | `readable`, `contentAudits`, `talkTracks`, `conferencePacks`, `chartSpecs`, `flowDocs`, `redactionRequired`, `redactionPending` — counts of the capability results stamped onto jobs. `redactionPending` is the one that matters: those items are blocked. |

All three carry `readable: false` when the underlying table could not be read, so a caller can tell
"nothing to review" apart from "cannot see the reviews" — as a bare `0` those are identical, and
only one of them is good news.

| Query param | Type | Meaning |
| --- | --- | --- |
| `view` | `agent` | Returns the lean projection built for automations. Much smaller, and much cheaper for a model to read. **Automations should always use this.** |
| `since` | ISO-8601 timestamp | Returns only events and work-ledger entries that occurred strictly after this instant. |

**Pagination and caps.** In the default view the response is capped so it cannot grow without
bound as history accumulates:

| Collection | Default cap | With `?since=` |
| --- | --- | --- |
| `events` | 500 most recent | only those after `since` |
| Work-ledger entries | full day | only those after `since` |
| Completed jobs | 200 most recent | only those after `since` |
| Active / queued / in-progress / blocked jobs | **never capped** | **never capped** |

Two deliberate design points:

* **Counts are computed before trimming.** `metrics`, `impactLedger` and every total are derived
  from the untrimmed rows, so capping a list never changes a reported number.
* **`since` fails open.** A timestamp that cannot be parsed is treated as "include it". A bad
  `since` value can never silently hide work from the user.

---

### `GET /api/gate`

The "is there anything for me to do" summary that drives the top of the dashboard.
Always contains the key `hasWork` (boolean).

### `GET /api/activity-log`

`{ "events": [...] }` — the human-readable activity feed.

### `GET /api/impact-ledger`

Aggregated impact records used by the impact panel.

### `GET /api/sweeps`

`{ "sweeps": [...100 most recent...], "serverTime": "..." }` from the `sweep_runs` table.

### `GET /api/knowledge`  *(Casey's knowledge graph)*

| Param | Values | Meaning |
| --- | --- | --- |
| `type` | any entry type | Filter to one type, e.g. `commitment`, `decision`, `person`, `project`, `research-dossier`, `filing-rule`, `content-template`, `style-pack`, `artifact-registry`, `account-context`. |
| `q` | free text | Case-insensitive substring match against `title` and `summary`. |
| `status` | `active` (default), any status, or `all` | `active` hides soft-deleted entries. |

```json
{ "entries": [ ... ], "total": 12, "summary": { ... }, "serverTime": "..." }
```

Capped at 500 entries, newest first. Each entry carries computed `stale` (not updated in over
30 days) and `overdue` (an active commitment past its `dueDate`). Both are recomputed on every
read rather than trusted from the stored column, so an entry cannot look fresh merely because
nothing re-flagged it.

Knowledge `type` is an open vocabulary. `/api/context-vocabulary` publishes the common Casey types
(`person`, `project`, `commitment`, `decision`, and others), but any non-empty extension type is
accepted and preserved verbatim. This lets connector-defined knowledge types round-trip without a
core release.

### `GET /api/connector-snapshots`

Returns normalized provider snapshots, newest first. Optional exact-match filters are `provider`,
`capability`, and `subject`; `limit` defaults to 100 and is capped at 500.

### `GET /api/connector-health`

Returns the latest effective health per provider/capability/subject and counts by status. An
`available` or `partial` snapshot whose `expiresAt` is past is reported as `stale`. To keep the
polled state path bounded, health considers the 2,000 newest snapshots and returns at most 500
connections; `truncated` says whether the connection cap was reached.

### `GET /api/context-vocabulary`

Returns Casey's common context types and `extensionTypesAllowed: true`.

### Watch and follow-up list

`GET /api/watches` lists open items by default. Pass `status=all` for history or one lifecycle
status (`active`, `triggered`, `pending_investigation`, `evaluated`, `completed`, `dismissed`,
`removed`). Results are capped at 500. `GET /api/watches/<id>` views one item.

`POST /api/watches` creates an item. `mode` is `direct` or `investigative`; `itemKind` is `watch`
or `action-item`. Core fields are `subject`, `threadRef`, `sourceType`, `sourceId`, `sourceUrl`,
`watchInstruction`, `triggerCondition`, `proposedAction`, `owner`, `provenance`,
`lastObservedAt`, and `freshnessAt`. Investigative and spawned records can additionally carry
`parentWatchId`, `originItemType`, `originItemId`, `originItemUrl`, `evaluation`, and
`proposedNextStep`.

`PATCH /api/watches/<id>` updates those fields or the lifecycle status.
`POST /api/watches/<id>/complete` and `/dismiss` are convenience transitions.
`DELETE /api/watches/<id>` is an explicit soft removal: it persists `status=removed` and
`removed_at`, retaining the record and provenance for history/export.

All arbitrary text fields have server-enforced limits (100-4,000 characters by purpose);
provenance JSON is capped at 16,000 characters, and URLs must be absolute HTTP(S). Every response
includes `automaticAction: false`. A trigger or evaluation only updates the local record; no
proposed action or external side effect is executed by the watch API.

### `GET /api/jobs/<jobId>`

Full detail for one job. `404` when the id is unknown, or when the route has the wrong shape.

### `GET /api/events`

**Server-Sent Events** stream (`text/event-stream`) that pushes a nudge whenever state changes, so
the dashboard can refresh without polling. Accepts `?token=` (see Authentication).

### `GET /api/documents/<relative-path>`

Serves a file from the configured document root. Markdown is rendered to HTML and served inline;
anything else is served with its detected content type. Anything resolving outside the document
root is rejected with `403`.

### `GET /api/architecture-skill`

Returns the raw markdown of an installed skill:
`{ "ok": true, "name": "...", "title": "/...", "markdown": "..." }`.

---

### `GET /api/export`  *(privacy control)*

Downloads **everything the app holds** as a single ZIP:

* every table in the SQLite database, one `<table>.json` per table
* every file under the document root

```http
Content-Type: application/zip
Content-Disposition: attachment; filename="daily-flow-export-2026-08-06.zip"
```

The filename date uses the app's configured timezone.

### `POST /api/reset`  *(privacy control)*

Deletes every row of user data and returns per-table counts:

```json
{ "ok": true, "deleted": { "jobs": 41, "events": 260, "sweep_runs": 12 } }
```

* **Cleared:** `jobs`, `approvals`, `inbox_signals`, `events`, `work_ledger_entries`,
  `chat_threads`, `chat_messages`, `decision_memory`, `sweep_runs`.
* **Kept:** `employees`, `career_profile`, `app_meta`, and `config.json`. Your team survives; its
  work history does not.
* The database ships with seven `BEFORE DELETE … RAISE(ABORT)` retention triggers that normally
  make history un-deletable. A user-initiated reset is the one sanctioned exception: it drops
  them inside a `BEGIN IMMEDIATE` transaction, deletes, commits, then calls `init_db()` to
  recreate all seven. A reset therefore leaves the retention guarantees fully intact.
* Writes one audit event: **"User reset all private data"**.
* Requires the local token when `--auth` is on. This endpoint is destructive and irreversible —
  export first.

---

### `POST /api/attention-major`

Queues a full broad sweep. This is a **dashboard-initiated** action.

```jsonc
{
  "source": "dashboard",        // optional, defaults to "dashboard"
  "force": false,               // optional, bypasses the cooldown
  "idempotencyKey": "abc123"    // optional, see below
}
```

**Idempotency.** If `idempotencyKey` is supplied and the same key was seen within the last
**60 seconds**, the original result is returned with `"idempotent": true` and **no second sweep is
queued**:

```json
{ "ok": true, "jobId": "…", "queued": true, "idempotent": true }
```

Keys live in an in-memory dict with a TTL. They are transient by design and do not survive a
restart, which is correct — after a restart no in-flight duplicate can exist.

Without a key the normal cooldown still applies unless `force` is set.

---

### `POST /api/inbox-invites`

Replaces the calendar-invite approval set from a sweep snapshot.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `invites` | array | **required** | The invites in this snapshot. A non-array is `400`. |
| `reconcile` | bool | `true` | Retire approvals no longer present. |
| `completeSnapshot` / `complete` | bool | `true` | This payload is the complete current picture. |

### `POST /api/inbox-signals` · `POST /api/review-signals`

Two paths, one handler. Upserts inbox signals from a sweep.

| Field | Type | Default |
| --- | --- | --- |
| `signals` | array | `[]` |
| `reconcile` | bool | `false` |
| `coveredTypes` / `scope` | array | `[]` |
| `resolvedIds` / `resolvedSourceIds` | array | `[]` |
| `completeSnapshot` / `complete` | bool | `false` |

### `POST /api/work-ledger`

`{ "entries": [...] }` — upserts work-ledger entries. This is what the evening wrap-up writes.

### `POST /api/sweep/start`

`{ "source": "automation", "model": "…", "channels": [...] }` → `{ "ok": true, "sweepId": "…" }`

### `POST /api/sweep/finish`

```jsonc
{
  "sweepId": "…",            // or "id"
  "status": "completed",     // default "completed"
  "counts": {}, "passes": {}, "verify": {},
  "summary": "", "error": "", "channels": []
}
```

### `POST /api/classify`

Sensitivity / priority classification. Accepts either shape:

* `{ "messages": [ {...}, {...} ] }` → `{ "ok": true, "results": [ ... ] }`
* `{ "message": {...} }`, or a bare message object → `{ "ok": true, ...classification }`

### `POST /api/employees/add`

Adds a digital employee. Returns the created employee.

### `POST /api/employees/<name>`

Updates one employee (trust level, enabled, role, detail …).

### `POST /api/employees/<name>/{proposal|confirm|remove|restore}`

Employee lifecycle transitions.

### `POST /api/team/all-to-draft`

Panic switch: sets every *adjustable* employee back to `draft`, so nothing sends without you.
Returns `{ "ok": true, "reset": ["…"] }`. Employees whose mode is fixed are left alone.

### `POST /api/jobs/<jobId>`

Updates a job — status, result summary, result link, blocker, send state. Also accepts the
role-expansion stamps below, each written only when the field is present in the body, so one
employee's stamp never clears another's:

| Field | Type | Written by | Meaning |
| --- | --- | --- | --- |
| `qualityReview` | bool | any employee | Flags the item for Quinn's review. |
| `qualityVerdict` | `pass` \| `pass-with-notes` \| `hold` | Quinn | Her ruling. Also sets `qualityReview`. Anything else is ignored. |
| `riskLevel` | `low` \| `medium` \| `high` | Quinn | Risk rating for the register. |
| `handoffTo` | employee name | Major | Delegation trace. An unknown name is refused and logged, not written. |
| `handoffReason` | string | Major | Logged with the handoff. |
| `eta` | ISO-8601 | Major | Expected completion. |
| `slaBreached` | bool | Major | The job overran its ETA. |
| `knowledgeLinks` | array | Casey | Knowledge entry ids backing this job. |
| `sourceIds` | array | any employee | Source ids the result is grounded in. |
| `qualityAudit` | object | Quinn, Drew | The audit returned by `/api/content-pass`. |
| `redactionRequired` | bool | Quinn | Sensitive text was found. The item is blocked until redaction is applied. |
| `redactionApplied` | bool | Quinn | The redaction has been done. Logs a Quinn event when set. |
| `brandVoiceProfile` | string | Drew, Riley | The voice profile used for the pass (truncated to 120 chars). |
| `talkTrack` | object | Drew | The track returned by `/api/talk-track`. |
| `conferencePack` | object | Drew | The pack returned by `/api/conference-pack`. |
| `chartSpec` | object | Dash | The spec returned by `/api/chart-spec`. |
| `flowDoc` | object | Piper | The summary returned by `/api/document-flow`. |
| `runtimeInventory` | object | Piper, Dash | A snapshot from `/api/runtime-inventory`. |

`status` is normally required, but a body carrying only stamps is accepted without one: a
handoff or a review verdict does not move the job through its own lifecycle. A body with
neither `status` nor any stamp is `400`.

### `POST /api/knowledge`  *(Casey's knowledge graph)*

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `type` | string | **yes** | Entry type. |
| `title` | string | **yes** | Short label. |
| `id` | string | no | **Supplying an existing id updates that entry in place.** |
| `summary` | string | no | One-line description. |
| `details` | object | no | Anything structured. |
| `status` | string | no | Defaults to `active`. |
| `owner` | string | no | Who owns it. |
| `dueDate` | ISO-8601 | no | Drives `overdue` on commitments. |
| `sourceType` / `sourceId` | string | no | Where it came from (email, meeting, …). |
| `relatedIds` | array | no | Other entry ids. |

Returns `{ "ok": true, "id": "kn_…", "entry": { … } }`. A missing `type` or `title` is `400`.

This is an upsert on purpose. A knowledge graph that only ever appends becomes duplicates of
the same person the second time anyone re-verifies them.

### `POST /api/connector-snapshots`

Authenticated server-to-server ingestion for a provider-neutral, read-only connector. Core never
calls a provider and does not accept provider credentials. The existing bearer token is **always
required for this ingestion endpoint**, including when the rest of the app runs in legacy
`--no-auth` mode, and must be sent as `Authorization: Bearer <token>`. The mutation origin guard
also applies; no cross-origin access is added.

```json
{
  "schemaVersion": "1.0",
  "provider": "example-provider",
  "capability": "context.read",
  "subject": "user:example",
  "observedAt": "2026-08-13T20:00:00Z",
  "expiresAt": "2026-08-13T20:30:00Z",
  "status": "partial",
  "requestedScopes": ["context.read", "files.read"],
  "grantedScopes": ["context.read"],
  "provenance": {"source": "provider-api", "requestId": "request-1"},
  "data": {"knowledge": [{"type": "custom-extension/type-v9", "title": "Example"}]},
  "errors": [{"code": "scope_missing", "message": "files.read was not granted"}]
}
```

`status` must be one of `available`, `unavailable`, `unauthorized`, `forbidden`, `not-found`,
`rate-limited`, `stale`, or `partial`. All envelope fields are normalized before persistence.
Secret-bearing fields (tokens, credentials, authorization, cookies, passwords) are rejected, as is
any normalized snapshot larger than 256 KiB. This endpoint is for snapshots, not raw provider
responses.

### `DELETE /api/knowledge/<id>`

Soft delete — sets `status='deleted'` and leaves the row. `{ "ok": true, "id": "…" }`, or `404`
when the id is unknown. Soft so a mistaken delete is recoverable, for the same reason the rest
of the history is preserved.

### `POST /api/approvals/<approvalId>`

Approves, rejects, or edits an approval card.

### `POST /api/drafts/<jobId>/send`

Sends a prepared outward draft after the user pressed Send in the dashboard.

### `POST /api/chat`

`{ "message": "…", "threadId": "…" }` — posts to the dashboard chat and queues the resulting job.
An empty `message` is `400`. Also nudges Major with a forced attention-major.

### `POST /api/civilians`

`{ "title": "…", "count": 3, "instructions": "…" }` — creates a batch of one-off parallel jobs.
Returns `{ "ok": true, "jobIds": [...], "count": n }`.

### `POST /api/career-profile`

`{ "currentRole": "…", "targetRole": "…", "reviewRubric": "…" }`.

### `POST /api/career-profile/extract`

**Not JSON.** The raw file bytes are the request body; the filename comes from the `X-Filename`
header or `?filename=`. Returns `{ "ok": true, "text": "…" }` with the extracted plain text.
Subject to the same 10 MB limit.

### `POST /api/decision-memory/clear`

Un-mutes dismissed items.

* `{ "clearAll": true }` — un-mutes everything.
* `{ "contentKey": "…" }` — un-mutes one item.
* Neither → `400`.

Returns `{ "ok": true, "restored": n }`.

### `POST /api/skills/check` · `POST /api/skills/install`

Checks for / installs Scout skill files. `check` takes `{ "skills": [...] }`.

### `POST /api/maintenance`

Runs `PRAGMA wal_checkpoint(TRUNCATE)` → `{ "ok": true, "checkpointed": true }`.

---

## 4. Capability endpoints

The capability inventory includes local transforms plus the connector contract above. The existing
content/list/document capabilities are **pure transforms**: each computes from the posted body and
returns. Connector snapshot ingestion is the explicit stateful exception.

Persisting a result is a separate, explicit act — stamp it onto the job it belongs to with
`POST /api/jobs/<jobId>`. That is deliberate, so a scratch calculation cannot quietly become part
of the record.

All capabilities obey the guards in section 2 and require the token in `--auth` mode. Every response
carries `ok` and `serverTime`; check `ok` rather than assuming a `200` body is a result.

| Endpoint | Method | Owner | Stamp the result as |
| --- | --- | --- | --- |
| `/api/runtime-inventory` | GET | Dash, Piper | `runtimeInventory` |
| `/api/connector-snapshots` | GET, POST | Casey | — |
| `/api/connector-health` | GET | Casey, Dash | — |
| `/api/context-vocabulary` | GET | Casey | — |
| `/api/content-pass` | POST | Quinn, Drew, Riley | `qualityAudit`, `redactionRequired`/`redactionApplied`, `brandVoiceProfile` |
| `/api/skill-lint` | POST | Piper | — |
| `/api/format-list` | POST | Casey, Reese | — |
| `/api/document-flow` | POST | Piper | `flowDoc` |
| `/api/chart-spec` | POST | Dash | `chartSpec` |
| `/api/conference-pack` | POST | Drew | `conferencePack` |
| `/api/talk-track` | POST | Drew | `talkTrack` |

### `GET /api/runtime-inventory`

What the app can verify about itself: version, Python version, platform, whether auth is on, the
capability list, and the skills present on disk.

```json
{ "ok": true, "app": { "version": "4.3.1", "python": "3.12.10", "platform": "win32",
  "host": "127.0.0.1", "authRequired": false },
  "capabilities": ["/api/runtime-inventory", "…"], "installedSkillCount": 4, "skills": ["…"] }
```

**Limit:** it reports the app and the package folder only. It cannot see Scout's own tool list or
any MCP servers, so it is not a complete picture of the environment.

### `POST /api/content-pass`

Brand-voice and quality audit, with an optional redaction pass.

```json
{ "text": "…", "audience": "email", "brandVoice": "formal", "redact": false }
```

Returns `score` (0–10), `verdict` (`pass` · `pass-with-notes` · `hold`), `findings[]`, and a
`sensitive` block listing the identifier-shaped matches found. With `"redact": true` it also
returns `redactedText` and `redactionApplied`.

The score is derived from the findings and weighted by severity — it is not an independent
judgement. **The sensitive scan is a pattern-based floor, not a certification:** it catches known
shapes (emails, phone numbers and similar) and does not make content HIPAA- or GDPR-safe.

### `POST /api/skill-lint`

Structure checks for a `SKILL.md`. Takes `{ "text": "…" }`, or `{ "path": "…" }` **relative to the
package `skills/` folder** — a path resolving outside it returns `403`.

Returns `score`, `verdict`, `issues[]`, `errors`, `warnings`. A skill can lint at 10/10 and still
tell an employee to do the wrong thing; this checks shape, not content.

### `POST /api/format-list`

`{ "rows": [ {...}, ... ], "columns": ["…"] }` → trimmed column names, inferred `columnTypes`
(`text` · `number` · `boolean` · `date`), coerced values, and ragged rows filled. `400` when `rows`
is missing or empty.

### `POST /api/document-flow`

Takes an exported Power Automate flow definition (as `flow`, or as the body itself) and returns
`summary`, `triggers`, `actionCount`, and `connectors[]`. It documents **structure, not intent** —
it cannot tell you whether a flow is correct or safe.

### `POST /api/chart-spec`

`{ "rows": [...], "chartType": "bar", "x": "…", "series": ["…"] }` — all optional except `rows`.
Infers the axis and series when not given and returns a chart schema plus `warnings[]`.

It **refuses misleading combinations**: more than 8 pie slices, or a text x-axis on a line chart,
come back as warnings. Respect them — fix the data shape or use a table.

### `POST /api/conference-pack`

`{ "topic": "…", "audience": "…", "durationMinutes": 45, "speaker": "…" }` → `titleOptions[]`,
`abstract`, `learningObjectives[]`, `bio`, and `gaps[]`.

It is a **scaffold**. Wherever it would otherwise have to invent a credential it emits a
`[bracketed]` placeholder and records it in `gaps`. Never submit a pack with brackets still in it.

### `POST /api/talk-track`

`{ "slides": ["…" | {"title","points"}], "durationMinutes": 20 }` → per-slide `minutes`,
`talkingPoints`, `transition` and `cue`. Weights give the open and close a little more room, and
are normalized so the allocation sums to the duration you asked for.

---

## 5. Status codes

| Code | Meaning |
| --- | --- |
| `200` | Success. |
| `400` | Malformed JSON, or a failed field validation. |
| `403` | Missing/wrong local token, rejected `Origin`, or attempted path traversal. |
| `404` | Unknown route, or a known route with an unknown id. |
| `413` | Body over 10 MB. The connection is closed. |
| `500` | Unhandled server error; the message is in `error`. |

## 6. Notes for automation authors

1. **Always use `GET /api/state?view=agent`.** The default view carries the full completed-job and
   event history and costs many times more to read.
2. **Use `?since=`** when you only need what changed since your last run.
3. **Send an `idempotencyKey`** on `POST /api/attention-major` if your run can be retried.
4. **Treat `POST /api/reset` as user-only.** Nothing automated should ever call it.
