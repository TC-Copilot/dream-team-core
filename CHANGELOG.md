# Changelog

This page lists what changed in each release of The Dream Team for Microsoft Scout, newest first.

## Versioning policy

This project uses semantic-ish `MAJOR.MINOR.PATCH` versioning, applied as follows when preparing
a release with `package-share.ps1`:

- **PATCH** (`4.5.2` → `4.5.3`) — the default for almost every release. Bug fixes, and additive
  UI/workflow improvements that don't require the user to change how they use the app (new
  buttons/states on an existing surface, a new review stage in an existing pipeline, formatting
  fixes, new smoke/test coverage, etc.). This covers the normal cadence of incremental work —
  for example, the calendar RSVP 4-state UI, the calendar freshness check, email/attachment
  triage and epiq filing, Evidence Review v1 plus Major orchestration and WorkIQ misroute
  detection, voice dictation, the Teams HTML formatting fix, and the local-timezone timestamp
  fix were all released as PATCH bumps.
- **MINOR** (`4.5.x` → `4.6.0`) — reserved for an explicitly planned, substantial
  backwards-compatible milestone (e.g. a new employee, a new top-level surface/dashboard, or a
  batch of related capabilities big enough to warrant calling out as its own milestone rather than
  a routine fix). Only bump MINOR when that's been explicitly decided, not by default.
- **MAJOR** (`x.0.0` → `(x+1).0.0`) — reserved for breaking changes only: anything that changes
  stored data shape in an incompatible way, removes/renames a public API or config field a user
  depends on, or otherwise requires a user to do something different than before to keep working.

Already-published releases are never renumbered or rewritten to match this policy retroactively —
it governs future releases only.

## What the Dream Team does today

The Dream Team is a local command center with ten digital employees that run on Microsoft Scout. Here is what it does as of the latest release.

- It watches your email, Teams, and calendar for things that need you, and lines them up in one approval inbox.
- You approve an item and it carries out what you asked, whether that is a reply, a thumbs up, a forward, or a send. It only drafts when you ask it to.
- It preps your meetings, pulls notes into action items, and flags scheduling risk before it bites.
- It does research with real sources, and it writes documents, decks, and sheets for you.
- It keeps a running record of what you got done, framed for a performance review if you give it your goals.
- You set how far each employee can go on its own, from draft-only up to fully autonomous, and confidential content always waits for you.
- You can add your own employees, or remove any of them except Major.

Everything runs on your machine, and the team never sends anything to other people without your go-ahead.

## Releases

### 4.5.19

- Changed public-core model guidance to use provider-neutral Auto/lightweight routing for routine
  work and reserve a setup-selected frontier model for complex reasoning, high-risk review, and final
  synthesis. The same stable release now carries build revision `20260818.3`; semantic compatibility,
  overlay contracts, evidence requirements, Quinn holds, and approval gates are unchanged.
- `/daily-flow-setup` now fetches and safely applies a refreshed published asset even when its stable
  semantic version is still `4.5.19`. A separate build revision or asset SHA-256 change triggers the
  refresh; neither value changes compatibility or becomes a SemVer suffix.
- Added exact release-asset selection, published digest and extracted-manifest validation, safe
  single-process replacement, and post-restart version plus build-revision verification. Setup
  fails closed instead of accepting a stale same-version process.
- Hardened range-compatible layered installs with schema-2 overlay manifests: a trusted manifest
  SHA-256, expected overlay identity, exact core contract schema/version, and a complete declared
  payload file set are now required before the application layer can change.
- Registered overlays now retain separate integrity metadata and revalidate identity, manifest hash,
  payload hashes, payload file set, and bounded `coreVersionRange` before later core updates.
  Partial integration arguments and orphaned registration artifacts fail closed instead of falling
  back to a core-only update.
- Added coverage for manifest/payload tampering, undeclared files, wrong identity, partial arguments,
  orphaned registrations, and the existing v4.5.x range boundaries. Public installs remain core-only.

### 4.5.18

- Published the tested range-based overlay compatibility contract for core releases
  `>=4.5.16` and `<4.6.0` while retaining required core contract schema `1` and contract version
  `1.0.0`. The installer continues to validate an overlay's declared inclusive/exclusive bounds
  before it stops an existing app or replaces any package files.
- Added focused boundary coverage for accepting the released range, rejecting its `4.6.0` exclusive
  upper bound, and rejecting an overlay that requires a different core contract version.

### 4.5.17

- Made every nonzero **Quality & knowledge** dashboard metric keyboard-accessible and linked to
  its detailed underlying records; zero-valued metrics remain informational and inert. Casey's
  remembered, overdue, and stale entries render as knowledge-detail cards, while Quinn's review,
  hold, audit, and redaction counts render their matching job records.
- Applied configured owned-account prioritization to incoming approval cards. Confirmed unowned
  account work remains visible but defaults to low/FYI and sorts after other pending work, with an
  explainable badge. Direct assignment, deadline, service/customer impact, and
  safety/compliance/security evidence retain or raise normal priority; account-neutral work is
  unchanged.
- Added safe **Open email** and **Open Teams message** source links for new and persisted records
  that carry complete native Outlook or Teams identifiers. Explicit validated URLs still win, and
  incomplete identifiers intentionally produce no guessed link.
- Added focused dashboard-detail, incoming-account-priority, and Outlook/Teams source-link
  regression coverage. Bumped the service-worker cache so installed clients receive the dashboard
  assets.

### 4.5.16

- Added a provider-neutral core/overlay compatibility contract with independently versioned core
  contract and overlay metadata schemas. Public installs remain core-only and do not discover,
  download, install, or execute overlay code.
- External installers can explicitly supply overlay metadata. Registered overlays are automatically
  rechecked before later core updates; incompatible, malformed, unsupported, or missing requested
  metadata fails closed before the running app is stopped or package files are replaced.
- Added persisted and `/api/health` version reporting for core, contract, and optional overlay
  versions, plus focused compatible/incompatible/missing-metadata tests and smoke/CI coverage.
- Documented the exact integration and release responsibilities for an external/private repository.
- Retained the v4.5.15 `-ResetApplicationLayer` transaction and full config/runtime preservation,
  now governed by the canonical compatibility gate. The provisional `.installed-overlay.json`
  identity contract is superseded by `overlay-manifest.json` and `app/.version-report.json`.

### 4.5.15

- Added a provider-neutral layered install contract while keeping public installs public-only. A
  private wrapper can independently fetch and verify public core, request a clean application-layer
  reset that preserves config, port, document root, local data, and token state, then apply its own
  separately versioned payload.
- `/api/health` retains the legacy `version` field and now reports `coreVersion` plus an optional,
  strictly validated overlay identity. Invalid or cross-core installed overlay manifests prevent
  startup instead of producing a success-shaped health response.
- Fixed automatic upgrades to preserve the configured document root and every existing config field,
  rather than rebuilding config from only `port` and `documentRoot`.
- Added contract coverage for independent core-only, overlay-only, both-newer, and already-current
  update decisions, manifest failure cases, public-only health, lifecycle safeguards, and clean
  packaging.

### 4.5.14

- Added a provider-neutral, persisted Action Items / Watch list with authenticated create, list, view, update, complete, dismiss, and explicit soft-remove APIs. Records retain subject/thread/source references, instructions, conditions, proposed actions, owner, provenance, freshness, timestamps, and bounded text fields.
- Added direct conditional watches and investigative follow-ups. Investigative items link back to their originating item, move through pending-investigation and evaluated states, and store an evaluation plus proposed next step before any user action.
- Major now recognizes watch/follow-up language in dashboard chat and routes it to the dedicated list. Spawned watchers and action-items retain parent/origin relationships. Proposed actions are always advisory and never execute automatically.
- Added a dashboard Action Items / Watch list with source/details view, complete, dismiss, and remove controls. Privacy masking remains display-only.
- Added focused model and default/authenticated smoke coverage for both modes, relationship fields, list/view/remove, text bounds, persistence, and the no-automatic-action guarantee.

### 4.5.13

- Fixed the company privacy mask making the approval/decline modal's optional-guidance textarea read-only. Masking now excludes every input, textarea, select, and contenteditable subtree, preserving typed values and focus while privacy mode is on or toggled.
- Company-name masking still covers displayed text and metadata, including `MRI SOFTWARE`, exact uppercase `MRI`, slash variants, and `mrisoftware.com`. Observer-owned mutations are drained while observation is paused, preventing feedback loops, and disabling the mask restores original displayed values in place without closing dialogs or re-rendering editors.
- Added Node regression coverage for editable-subtree exclusion, observer suppression, input preservation, and the existing company-name variant behavior.

### 4.5.12

- Fixed public install/update hangs caused by an old Daily Flow Python process retaining the configured port while new files were copied over it.
- Upgrades now identify the listener on the configured port, prove it is the installed Daily Flow app before stopping it, wait for the port to release, preserve the configured port and document folder, then restart and require `/api/health` to report the exact new version.
- Install, start, and stop flows now fail clearly when another process owns the port or when the expected version does not become responsive; unrelated processes are never stopped.

### 4.5.11

Maintained at <https://github.com/TC-Copilot/dream-team-core>.

- Company privacy aliases now cover configured-name-derived punctuation and slash forms, controlled acronym/short-brand forms, and concatenated company stems when they appear as email/web domains. For example, a configured `MRI SOFTWARE` account masks `MRI SOFTWARE`, exact uppercase `MRI`, `MRI/Software`, and `mrisoftware.com` with the same stable alias.
- Short acronym matching is case-sensitive and all text matching remains token-boundary-aware, so `MRI` does not alter `Microsoft`, lowercase ordinary prose, or longer words. No arbitrary capitalized text or unconfigured domain is guessed.
- Added regression coverage for `MRI SOFTWARE`, `MRI`, `Microsoft`, lowercase `mri`, `MRI/Software`, and `mrisoftware.com`; bumped the PWA cache so installed clients receive the corrected matcher.

### 4.5.10

Maintained at <https://github.com/TC-Copilot/dream-team-core>.

- **Hide company names** now places the dashboard behind a visible **Working...** privacy veil while it builds stable aliases from the complete configured owned-account list. The veil is also activated before first paint when the browser preference was already enabled.
- Masking now covers the full rendered dashboard and display attributes (including titles, accessibility labels, tooltips, link text/paths, and non-control data attributes), is reapplied after normal renders and live updates, and refreshes the complete dashboard before revealing it. Configured URL/filename separator variants are covered without guessing arbitrary capitalized text.
- The underlying API state, SQLite data, drafts, attachments, and outbound identifiers remain untouched. The owned-account editor is read-only while masking is active, toggle-off immediately re-renders real names, aliases stay browser-local, and an empty account list reports a clear status.
- Added privacy smoke coverage for the blocking working state, owned-account replacement map, full-page re-render/DOM scrub, update observer, outbound-mutation guard, and toggle-off restoration.

### 4.5.9

Maintained at <https://github.com/TC-Copilot/dream-team-core>.

- Added a provider-neutral, read-only connector contract. Authenticated server-to-server callers can
  ingest bounded normalized snapshots at `/api/connector-snapshots`; core never calls a provider,
  stores credentials, or retains oversized raw responses.
- Added connector health and provenance views with explicit `available`, `unavailable`,
  `unauthorized`, `forbidden`, `not-found`, `rate-limited`, `stale`, and `partial` states.
- Published Casey's context vocabulary at `/api/context-vocabulary` while preserving arbitrary
  non-empty extension type strings verbatim. Added focused unit, smoke, and CI coverage.

### 4.5.8

Maintained at <https://github.com/TC-Copilot/dream-team-core>.

- The main dashboard now renders the literal **Recommendation:** label in bold while continuing to escape all signal text.
- Incoming email, Teams, and calendar signals preserve a real actionable source from explicit `sourceUrl`/`sourceLinks` fields or, when needed, original HTML anchors and plain-text URLs. The backend accepts only absolute `http`/`https` URLs, rejects unsafe schemes and credential-bearing URLs, stores sanitized plain-text message content, and never invents a destination.
- Approval cards expose a keyboard-focusable, opener-isolated **Open source** or **Open survey** link when a validated URL exists; cards remain unchanged when no safe source is available.
- Scout signal instructions now require source-link preservation, safe schemes, and plain-text summaries. Added targeted URL extraction/state tests and smoke coverage for the backend and dashboard wiring.

### 4.5.7

Maintained at <https://github.com/TC-Copilot/dream-team-core>. Combines four additive features developed together: account-ownership scoping, privacy display toggles, results-history visibility, and deadline-driven calendar auto-scheduling. No change to existing approval workflows; no behavior change for installs that don't configure an owned-account list or enable auto-scheduling.

**Owned-account editor and account-ownership scoping**

- New **"Owned accounts"** panel on the main dashboard (directly below the privacy toggles bar), where the user pastes their own company/account names — CSV commas, newlines, or plain whitespace-separated runs are all accepted, entries are trimmed and de-duplicated case-insensitively while preserving the first-seen casing, and the list is persisted privately on the same machine (mirrors the existing `career_profile` single-row config pattern: `owned_accounts` table, `get_owned_accounts()`/`save_owned_accounts()`, new `GET`/`POST /api/owned-accounts` endpoints gated behind the same local-auth token as other private endpoints, and never shipped in `/api/export`/package-share).
- New `classify_account_scope()` classifies every impact-ledger highlight (and, transitively, every job-derived "Results and drafts prepared" card) into one of four states, using **only** the confirmed `customer` field already attached to that item — never a broad guess from capitalized words in free text:
  - **`account_neutral`** — the item has no confirmed customer/account context at all; the owned-account list is irrelevant.
  - **`owned_account`** — the confirmed customer matches an entry in the owned-account list; normal/high relevance, unchanged from today.
  - **`unowned_account`** — a confirmed customer is present but does not match the list; defaults to the **lowest** importance tier, unless the item's own title/summary text contains a direct-assignment, explicit-mention, evidence-backed-deadline, customer-impact, or safety/compliance/security signal (`UNOWNED_PRIORITY_RAISE_TERMS`), in which case its importance is raised and the exact matched reason is recorded and shown.
  - **`uncertain_account`** — a confirmed customer is present but the owned-account list itself is empty/unconfigured, so ownership genuinely cannot be determined; no ownership-based suppression or boost is applied either way.
  - Every classification carries a human-readable `reason` string so the rationale is visible, not a black-box score; nothing is ever hidden or dropped from results — this is annotation only, matching the requirement that Major's priority/routing never suppresses a request and Tilly's deadline-critical scheduling is unaffected regardless of account ownership.
- `attention_major_instructions()` now includes the owned-account list and scoping rules (including the explicit "never suppress/never block Tilly" instruction) in Major's sweep instructions, once an owned-account list is configured; on a fresh/shared install with no list saved, this block is empty and behavior is unchanged.
- The dashboard's "Results and drafts prepared" cards now show a small explainable badge (🏢 Owned account / 🔽 Unowned account — lowest priority / ⚠️ Unowned account — priority raised / ❔ Uncertain account ownership) with the exact reason in a tooltip, computed by cross-referencing each job to its impact-ledger highlight; a summary line in the new panel shows current owned/unowned/uncertain counts across today's results.
- Added `test/test_account_ownership.py` (28 checks: name-paste parsing across CSV/newline/whitespace-run separators and de-duplication, all four scope classes, the lowest-vs-raised unowned-importance default and its exception keywords, the uncertain-account no-suppression/no-boost behavior, the config round-trip through SQLite, that saving never logs the raw account names, and that `build_impact_ledger` never suppresses an unowned/uncertain item) and a new `smoke-test.ps1` static check (backend functions/endpoint, all four scope-state strings and the "lowest" default, the non-suppressing annotation line, and the editor UI/JS wiring). Full existing Python test suite (5 files) and `smoke-test.ps1` (30/30) both still pass.

**"Hide company names" and "Hide person names" privacy toggles for Results and drafts prepared / results-history.html**

- Two independent checkboxes placed as a single control bar on the main dashboard directly below the top nav row (the "Attention Major" / Impact Ledger / Activity Log row): **"Hide company names"** and **"Hide person names"**. Both are off by default, each choice is remembered separately per browser (`localStorage`, shared with `results-history.html`), and toggling either off immediately restores the real names on the next render — nothing is ever sent to the server or mutated in stored data. `results-history.html` honors the same shared preferences (masking still applies to its cards) without duplicating the controls there; the main dashboard is the single primary place to toggle them.
- **Source of truth for "confirmed" names:** company names come from the `customer` field already present on each entry in `impactLedger.highlights` (returned via `/api/state`), populated either from an explicit `customer`/`account` tag set when work is reported, or — for job-derived entries — the backend's own `for customer X` / `for client X` / `for account X` phrase match. Person names come from the same highlights' `people` field, populated **exclusively** from an explicit `people`/`peopleWorkedWith` tag set when work is reported to the work ledger — job-derived entries never populate `people` at all, so there is no inference path whatsoever for person names. Neither mask ever scans or guesses names from arbitrary capitalized words in free text.
- Each toggle uses its own, separately-numbered alias namespace — "Company 1", "Company 2", … for companies, and "Person 1", "Person 2", … for people — assigned the first time a name is seen and never reassigned, so the same name reads the same way across every card even after a poll/SSE re-render adds more results, and a company alias can never collide with a person alias. The two masks compose (company mask runs, then person mask) when both are enabled, and were verified independent: turning one on/off never affects the other's aliases or matches.
- Masking never touches the underlying `href`/link target (links keep working, no leak through URLs), and Dream Team employee names are explicitly excluded from the person-name source list (matched case-insensitively against `state.employees`), so an employee's own name/role label is never masked by either toggle, e.g. "Created by Drew" never becomes "Created by Person 1" even if a work-ledger entry happened to tag an employee's name.
- Accessibility: native `<input type="checkbox">` elements with associated `<label>`s (not divs/spans pretending to be controls), reachable and toggleable by keyboard and announced normally by assistive tech; `aria-describedby` hints explain the scope of each mask next to its control.
- **Known limitation:** both masks match confirmed names as literal strings (case-insensitive, longest name first). If a confirmed name is referenced in a filename or slug that drops spaces/legal-entity suffixes (e.g. a file literally named `ContosoRenewal.pptx` when the confirmed company name is "Contoso Ltd"), that abbreviated form is not masked unless it independently matches another separately confirmed name — this is intentional, to avoid partial-word/token-level guessing that would either over-mask unrelated text or mangle filenames mid-word. The same reasoning applies identically to person names.
- **Strict client-side-only veil guarantee:** both masks are a purely presentational display layer. The mask functions read already-loaded `state` and return a *new string* for rendering; they never mutate `state` or a `job` object, and masked/aliased text is never read back by anything that talks to the backend. Every outbound action (the Send button's `data-send-draft` id, `sendPreparedDraft()`'s `/api/drafts/{id}/send` call) is keyed off the original, unmasked `job.id` — never off a masked display variable — so a masked/aliased value can never be persisted to SQLite, sent as an API payload, included in a job instruction, or reach a draft, attachment, email, or Teams message. This is documented in-code with an explicit "PRIVACY-MASKING VEIL GUARANTEE" comment block in both `app.js` and `results-history.html`, and guarded by a dedicated `smoke-test.ps1` static check (send controls keyed off raw `job.id`, mask functions never assign onto `job.`/`state.`), plus a live end-to-end verification against a running instance confirming an actual simulated Send click posts only the real job id/href — never an alias — and leaves the underlying `job` object's real name untouched in memory.
- Added a new `smoke-test.ps1` static check confirming both masking helper sets, both checkboxes' markup, and the "never mask employee names" guarantee are all present in `app/static/app.js`, `app/static/results-history.html`, and `app/static/index.html`.

**Results/dashboard visibility for prepared artifacts and document-backed drafts**

- **Root cause (a real code gap, not a stale install):** `app.py` has stored `document_status`, `artifact_type`, `artifact_creation_mode`, and `artifact_package_json` on the `jobs` table since the document-backed-draft and document/deck-creation features shipped, and `/api/state` already returns every column (`SELECT * FROM jobs`) — but neither `app/static/app.js` (the "Results and drafts prepared" dashboard section) nor `app/static/results-history.html` (the Previous Results calendar) ever read those fields. Both required a `result_link_json` href to show *any* entry, so: (a) a document-backed draft blocked because the source document was not found, an attachment failed, or was reported found but never actually linked, disappeared entirely instead of showing as blocked; and (b) an artifact-creation job that legitimately **completed** via the `copilot_prompt_fallback` path (direct file creation unavailable, so a build prompt was produced instead) had no file link at all and was therefore invisible even though it finished successfully.
- Both dashboards now include any `completed`/`done`/`blocked` job that carries a document-backed-draft or artifact-creation signal, even with no link: blocked entries show the existing sanitized `blocker` reason text (the same evidence-validation message already used elsewhere, e.g. "Source document not found: ... Searched: ...; terms: ..."), and completed `copilot_prompt_fallback` entries show the stored Copilot build prompt itself (truncated) as the card's content, since that prompt *is* the deliverable in that path.
- New quiet/blocked-style status chips (reusing the existing `.ready-badge`/`.ready-badge.blocked` styling) show at a glance: source document found / not found / attachment failed, Word document / PowerPoint deck, created / Copilot prompt fallback, and — when a document-backed draft's own job link is present — "Draft includes source document" to make the relationship between the located file and its outbound email draft explicit rather than two unrelated-looking rows.
- Dedupe now falls back to the job id (instead of the link href) when there is no link, so two different link-less blocked/prompt-only entries never collapse into a single displayed card; entries that do have a link keep the existing href-based dedupe unchanged.
- Preserves existing behavior for regular linked drafts/documents and for the outbound-send control: `sendControl()` is untouched, so a newly-visible artifact/blocked entry never shows as sent or gets a Send button it didn't already have — an artifact is only ever marked sent when Riley genuinely reports an outbound `send_state`.
- Verified live end-to-end against a running instance: created a document-backed-draft job and drove it to `not_found` (shows blocked with the search-location/reason text and a "Source document not found" chip), created a second one and drove it through `found` → composed draft with a real link (shows the link, a "Source document found" chip, and "Draft includes source document"), and created an artifact-creation job driven to `completed` via `copilot_prompt_fallback` with no link (previously invisible — now shows completed with the build prompt as its preview and "Copilot prompt fallback" chip). All three were also verified programmatically against the real, unmodified `app.js` functions run in a real JS engine against the live `/api/state` payload.
- Added a new `smoke-test.ps1` static check confirming the broadened eligibility filter, the no-link dedupe fallback, and the new badge/preview functions are present in both `app/static/app.js` and `app/static/results-history.html`.

**Deadline-driven calendar auto-scheduling (Tilly)**

- New, opt-in capability: when an actionable item names its own **explicit** near-term deadline (a `deadline`/`dueDate`/`dueBy`/`deadlineAt`/`dueAt` field on the inbox signal — never inferred from free text), Tilly automatically finds realistic open time on the calendar and **creates a real focus-block event immediately**, before any user approval. A normal pending approval card ("⏰ Auto-scheduled deadlines") appears for visibility, showing the item, the deadline, and the live event status (scheduling / created / blocked).
- This is a brand-new, separate lane from the existing calendar RSVP flow (`accept`/`tentative`/`follow`/`decline`) — it has its own `action_type` (`deadline-block`), its own decision set (`acknowledged`/`rejected`), and never touches `CALENDAR_DECISIONS` or the RSVP job pipeline.
- Card decisions: **Keep it** (`acknowledged`) just closes the card, leaving the event on the calendar. **Reject** queues Tilly to delete/cancel the event it created, and the Activity Log records the reversal.
- Duplicate-safe by design: a repeat scan of the same item (same source id, or subject+deadline when no source id is available) reuses the same approval card and only queues the scheduling job the very first time the card is created — later sweeps just refresh the card, never re-queue or duplicate the event.
- Conflict avoidance and slot selection are delegated to Tilly's own calendar read access via explicit job instructions (check existing busy events first, end the block before the deadline); the app itself has no calendar read/write capability of its own, consistent with every other action in this system.
- **Off by default.** Enable with `"deadlineAutoScheduleEnabled": true` in `config.json` (or `DAILY_FLOW_DEADLINE_AUTOSCHEDULE=1`). Optional `"deadlineBlockLookaheadDays": <int>` controls how near-term a deadline must be to qualify (default `2`, i.e. due today/tomorrow).
- Added `test/test_deadline_autoschedule.py` covering deadline extraction/window gating, the opt-in classification gate, stable-id dedupe, and preview rendering for every event-outcome state, plus a new `smoke-test.ps1` static check confirming the config gate, detection, job creation/cancellation, and the frontend's separate button group are all wired in.

**Outbound job-result HTML-leak closure (every job type) + build/job correlation tag**

- **Root cause:** the v4.5.1/v4.5.3 Teams-HTML fixes centralized cleanup at signal *ingestion* (`sanitize_review_signal_html`, applied when Scout POSTs review signals to the app) and added "plain text, never HTML" prose scoped specifically to the document-backed-draft and document/deck-creation role chains. Neither covered the general case: `resultSummary`, `blocker`, and the dashboard-chat `message` field were stored **verbatim** at every `POST /api/jobs/{jobId}` update, for every job type, regardless of whether the content ever passed through signal ingestion at all. A prep-brief or delivery message generated fresh by an employee for an ordinary `teams-action`/`dashboard-chat`/`employee-work` job — content that never existed as a stored review signal — could still carry raw `<p>`/`<h2>`/`<ol>`/`<li>` markup straight into the dashboard and any outbound Teams send built from it.
- `handle_job_update` now runs `teams_message_to_plain_text` unconditionally on `resultSummary`, `blocker`, and the chat `message` field before they are stored — for every job type, not just email/Teams-classified signals. The function is a safe no-op on already-plain text and never touches persisted document/Word content (it only applies to these short human-summary fields).
- Added a blanket **OUTBOUND CONTENT FORMAT** instruction — in both the live `dashboard_chat_instructions()` prompt and `daily-flow-team/SKILL.md` — stating plainly that *every* job type, not only the two document workflows, must compose Teams/email/resultSummary/chat content as human-readable plain text, and that content generated fresh in an employee's own reasoning never passes through any server-side cleanup, so composing it as plain text in the first place is not optional.
- Added a non-sensitive **build/job correlation tag**: `GET /api/jobs/{jobId}` now returns a top-level `buildTag` (installed app version + the job's id, e.g. `v4.5.7·job:a1b2c3d4`). Employees are instructed to append it as a trailing line on any Teams/email message reporting a job's result, so a future malformed or unexpected message can be traced back to the exact build and job that produced it instead of guessing from a nearby database row.
- Added regression coverage in `test/test_teams_text_format.py` for a job-result-shaped delivery message (headings/lists/links) plus `blocker`/chat-`message` field parity, and a new `smoke-test.ps1` static check confirming the cleanup, the `buildTag` field, and the blanket outbound-format prose are all present and wired in.

### 4.5.6

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall — but it does fix how *updates* get delivered to an existing install, and includes a security hardening fix.

**Security: local bearer token no longer printed to the console**

- **Root cause:** when started with `--auth`, `app.py` printed the raw local bearer token value directly to stdout (`[auth] Local token: <value>`) on every startup — visible in any console, log capture, or screen share of the running process, even though the token is also written to a protected file (`.local-token`) for programmatic use.
- Startup now prints only the token *file path* and usage instructions (`Authorization: Bearer <token from the file above>`); the literal token value is never written to a `print()` call. Token generation (`secrets.token_hex(32)`), storage (`.local-token` file, unchanged permissions/location), and the bearer-comparison authorization check are all unchanged — this is presentation-only.
- Added a static `smoke-test.ps1` check confirming the old print pattern is gone and the safe replacement is present, plus a **live** check (when run with `-Auth`) that starts the app for real and asserts the actual generated token value does not appear anywhere in captured stdout/stderr.

**`/daily-flow-setup` update-check fix**

- **Root cause:** `/daily-flow-setup` is a configuration wizard, not an updater. Its "fast path" (the common case, where the app is already running) went straight to model/automations/verify and never re-fetched or re-installed the code. Telling a user (or an agent) to "run setup again" to pick up a new release therefore did nothing — the old `app.py` kept running and the wizard still reported success, because nothing in it ever compared the installed version to the published one.
- Added an explicit **Step 0.5 - Check for a newer release** to `skills/daily-flow-setup/SKILL.md`, run first on every invocation including the fast path: read the running version from `GET /api/health`, read the latest published tag from the GitHub `releases/latest` API, and if the install is behind, actually perform the download + `install.ps1 -Auto -AgentInline -InstallDir <INSTALL_DIR>` steps against the existing install folder (preserving database/settings/employees) before continuing.
- **Verification gate:** after attempting an update, the skill must call `GET /api/health` again and confirm the version now matches the latest release tag. If it doesn't — update failed, app didn't come back up, etc. — the skill is now explicitly instructed to *not* print any success/"you're all set" message, and instead report the mismatch plainly and point at `install.log`.
- `INSTALL-WITH-SCOUT.md` Step 5 gained a matching `[Scout]` guardrail spelling out that `/daily-flow-setup` alone won't update code, plus a new troubleshooting row for "setup said it succeeded but the version didn't change."
- Added a `smoke-test.ps1` static check confirming both documents carry the update-check language, the version-comparison variables, the actual re-install command, and the "do not report success on failure" instruction — so this regression can't silently reappear.
- Scope: this fixes the *documented/agent-driven* update path only (the SKILL.md instructions Scout follows). The app itself already exposed `.version` via `/api/health` — no backend/schema change was needed.

### 4.5.5

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall.

**Document/deck creation capability**

- New two-mode document/deck creation workflow: primary mode creates a real, reviewable `.docx`/`.pptx` draft in the permitted Scout/OneDrive workspace, never externally shared or sent without approval; fallback mode produces a complete Word/PowerPoint Copilot build prompt the user can paste into Word Copilot or PowerPoint Copilot when direct creation is unavailable.
- Every request yields a structured package, not only prose: artifact type, audience/objective, a verified source/evidence list with gaps explicitly called out, a Word section outline or slide-by-slide storyboard, content body/speaker notes, deck design/visual guidance, a complete Copilot build prompt with explicit no-invention constraints, and the output destination plus draft/approval status. Stored as `jobs.artifact_package_json`.
- Explicit role ownership (not just prose): Major recognizes a document/deck creation request via `looks_like_artifact_creation_request()` and seeds `handoffTo=Drew` at job creation (`artifact_request`); Drew sources evidence and creates the artifact (or the fallback prompt), reporting `artifactType`/`creationMode`/`artifactPackage`; Casey supplies confirmed customer/project/commitment context only — never speculative facts — when Drew flags `artifactNeedsContext=true`; Mina owns the narrative structure, slide storyline and speaker notes for a `pptx` deck's storyboard (`narrativeReviewed`, skipped for `docx`); Riley composes the human-readable, plain-text cover note once the package is confirmed (`coverNoteComposed`); Quinn validates evidence, sensitivity, and the no-invention constraint before the approval gate (`qualityVerdict`).
- `artifact_creation_next_hop()` is Major's active routing decision through Casey (conditional) → Drew → Mina (pptx only) → Riley → Quinn → Major, re-evaluated on every job update — the same pattern used for Evidence Review and the document-backed draft workflow.
- `validate_artifact_creation_completion()`, wired into `handle_job_update()`, refuses a fabricated "completed" claim in either mode: a `created` claim with no file link, or a `copilot_prompt_fallback` claim with no build prompt, is forced to `blocked` instead. The falsely-claimed `sendState` is suppressed on the same downgrade, mirroring the document-backed draft workflow's fabrication guard.
- The document-backed draft detector (for requests about an *existing* document) and the new artifact-creation detector (for requests to *create* a new one) are checked in a fixed order at `/api/chat` so the two can never both fire on the same message.
- Updated `dashboard_chat_instructions()` and `skills/daily-flow-team/SKILL.md` with a full DOCUMENT/DECK CREATION section and per-role bullets for Major, Casey, Drew, Mina, Riley, and Quinn.
- Added `test/test_artifact_creation.py` (28 checks) covering request detection, the full routing chain, and both completion-gate modes. Updated `smoke-test.ps1` with a new source-presence check.
- Preserves external-action approval gating throughout; the finished artifact and its cover note still wait for the user's approval before anything is shared or sent.
- **Known limitation:** there is no direct API to invoke Word Copilot or PowerPoint Copilot, and no bundled Office-document-generation library (python-docx/python-pptx) in `app.py` itself — the primary-mode `.docx`/`.pptx` file is created via Drew's Scout worker tools, the same mechanism used for every other artifact in this codebase (talk tracks, conference packs, etc.). The fallback mode exists specifically to cover the case where that path is unavailable: it hands the user a complete, ready-to-paste build prompt for Word/PowerPoint Copilot instead of a programmatic hand-off.
- All existing v4.5.4 behavior (calendar RSVP, freshness checks, Evidence Review, Major orchestration/WorkIQ misroute detection, voice dictation, Teams HTML sanitization, timestamp localization, document-backed draft workflow) is unchanged.

### 4.5.4

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall.

**Teams outbound formatting: closed a gap where generated content still leaked raw HTML**

- The 4.5.1 fix (`teams_message_to_plain_text()`) only ran when a review signal's `action_type` was literally `"teams"`. That missed the case where a Teams-sourced item is *classified* as something else — e.g. a meeting-prep request, a commitment, or an attachment-review item — which is exactly how a generated prep-brief/delivery message still reached a user with raw `<p>`, `<h2>`, `<hr>`, `<ol>`, `<li>` markup.
- Added `sanitize_review_signal_html()`, the single choke point every non-email/calendar review signal now passes through in `upsert_inbox_signals` regardless of `action_type`. It cleans `summary`, `recommendation`, and the Evidence Review dossier's free-text fields (`explicitAsk`, `attachmentAnalysis`, `latestMessageDelta`, `threadDelta`, `threadSummary`, `misrouteReason`) before they're stored, previewed, or echoed into Major's job instructions (`create_review_follow_up_job` and the Evidence Review chain). Email is intentionally excluded (unchanged); calendar signals never reach this point (already routed to the calendar pipeline earlier).
- Broadened `teams_message_to_plain_text()` to give headings (`<h1>`-`<h6>`), `<blockquote>`, and `<hr>` a proper paragraph break instead of running into adjacent text unreadably — previously they were silently dropped along with other unhandled tags, which never leaked a tag but could glue a heading straight onto the next line (e.g. "Prep BriefAgenda"). Confirmed idempotent: running it twice on the same text is a no-op, so double-application anywhere in the pipeline can't corrupt content.
- Does not touch persisted Word/document content (`markdown_to_html`/`render_markdown_page`, used only by the in-app document viewer) or email/calendar formatting.
- Added regression tests in `test/test_teams_text_format.py` covering the exact reported tag set (p/h2/h3/hr/b/i/ol/ul/li/link), heading/hr line-break behavior, idempotency, and `sanitize_review_signal_html` coverage for a non-`"teams"` action_type plus the email carve-out. Updated the `smoke-test.ps1` source check accordingly.

**Document-backed drafts: stopped fabricating content in place of a real source document**

- Fixed a failure where a request like "put the Cowork doc I made just before the meeting with Heather into a draft email" produced a fabricated standalone HTML summary instead of locating and attaching the real document.
- `dashboard_chat_instructions()` now always includes a **SOURCE DOCUMENT** paragraph: any reference to an existing/named/just-created document must be treated as a discovery task before drafting — search OneDrive/Scout/Cowork locations, disambiguate using the meeting's title/time and the request's subject keywords, and never claim a document was found/attached without a real, stable path and successful attach/link.
- Added `jobs.document_status` (`''`/`found`/`not_found`/`attach_failed`) and `jobs.document_evidence_json` (`searchedLocations`, `searchTerms`, `sourcePath`, `reason`) columns, written via the existing `stamp_job_fields()` pattern only when the worker reports them.
- Added `validate_document_backed_completion()`, called from `handle_job_update()`: a worker reporting `documentStatus="completed"` alongside `not_found` or `attach_failed` — or `found` with no `link` — is force-downgraded to `blocked`, with the blocker text built from the reported evidence (searched locations/terms/reason, or the source path/failure). The falsely-claimed `sendState` is also suppressed on that same downgrade so a blocked, unattached draft can never be reported as `sent`. Existing email/Teams/calendar/suggestions job completions are completely unaffected (the check only activates when `documentStatus` is present).
- Updated `skills/daily-flow-team/SKILL.md` with the matching **SOURCE-DOCUMENT-BACKED DRAFTS** policy paragraph so the worker-side instructions and the server-side enforcement agree.
- Added `test/test_document_discovery.py` covering found+link (stays completed), found-without-link, not_found, and attach_failed (all forced to blocked with evidence preserved), plus the no-`documentStatus`/non-completed/unrecognised-value no-op cases. Added a `smoke-test.ps1` source-presence check.
- **Explicit role ownership in the chain (not just prose):** Major is now the only one who *recognizes* a document-backed draft request — `looks_like_document_backed_draft_request()` detects the pattern (a document/deck/attachment reference plus a draft/email/forward/attach intent) at `/api/chat` job creation and seeds `handoffTo=Drew` immediately, so discovery never silently lands with whoever happens to pick up the job. `document_draft_next_hop()` is Major's active routing decision through the rest of the chain — Drew (discovery/validation, reports `documentStatus`/`documentEvidence`) → Riley (composes the plain-text draft, `draftComposed=true`, and *only* once Drew has reported `documentStatus="found"` with a real `link` — never HTML, never a fabricated substitute for a missing source) → Quinn (`qualityVerdict`, verifies the confirmed source and attachment/link before the approval card is shown) → Major — re-evaluated and auto-advanced (`handoff_to`) on every job update, the same pattern used for Evidence Review's Casey/Drew/Quinn hand-off. `jobs.document_backed_draft` and `jobs.draft_composed` columns added to support this. `skills/daily-flow-team/SKILL.md` gained matching per-role bullets (Major, Riley, Drew, Quinn) spelling out each one's exact leg of the chain. 11 new regression tests cover request detection and every hop of the routing decision.

### 4.5.2

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall.

**Timestamps display in your browser's local timezone**

- Approval-card previews and the dashboard's embedded timestamps (e.g. calendar "When:" lines) are now rendered in whatever timezone your browser/system is actually in, instead of always assuming Pacific Time. `app.js`'s `humanizeTimes()` helper (which finds raw ISO-8601 instants embedded in generated preview text and turns them into a readable string) previously pinned `America/Los_Angeles`; it now lets the browser's own `toLocaleString()` pick the local zone, same as every other timestamp already shown in the dashboard.
- `metric-detail.html`'s drill-down cards now apply the same humanization to approval previews as the main dashboard, so a timestamp reads the same wherever it's shown.
- Fixed a backend labeling bug: `format_invite_time()` (used for the calendar "When:" preview line) always appended a hardcoded "PT" suffix even when the app's resolved timezone wasn't Pacific. It now shows the real abbreviation for whatever timezone is configured/resolved (e.g. `CET`, `IST`, `PDT`), and existing calendar-time parsing (`parse_display_time`) still round-trips correctly regardless of the abbreviation shown.
- Underlying stored/API timestamps are unchanged — they still carry an explicit UTC offset for data integrity and sorting; only the human-facing rendering was affected. Date-only values and timestamps without an explicit timezone are left untouched (no timezone is guessed for them).
- Added `test/test_local_timestamps.py` and a new smoke-test source check.

### 4.5.1

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall.

**Teams outbound message formatting fix**

- Teams review items sourced from Microsoft Graph chat messages could carry raw HTML markup (`<p>`, `<b>`, `<ol>`, `<li>`, `&nbsp;`-style entities, `<a href="...">` links) straight through into the summary/recommendation shown on the approval card and echoed into Major's job instructions — meaning that markup could leak into the actual outbound Teams reply.
- Added `teams_message_to_plain_text()` in `app/app.py` and wired it into `upsert_inbox_signals` for `action_type == "teams"` only: paragraph and `<br>` breaks become blank lines/newlines, `<ol>`/`<ul>` become numbered/dashed plain-text lines (correctly restarting numbering across separate lists), emphasis tags (`<b>`, `<i>`, `<span>`, etc.) are dropped while keeping their text, links become `label (url)` (or just the URL), and HTML entities are decoded safely.
- Scoped strictly to Teams-sourced signals — email, calendar, and attachment-review ingestion are untouched.
- Added `test/test_teams_text_format.py` (14 targeted cases) and a new smoke-test source check.

### 4.5.0

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall.

**Voice dictation for approval guidance**

- The "Optional guidance for Major" textarea shown when you approve/decline/etc. an item now has a 🎤 Dictate button beside its label. Click it to speak your guidance using your browser's built-in speech recognition (Chromium/Edge/Chrome); click again (⏺ Stop) to stop, or it stops automatically when you pause. Recognized text is inserted into the textarea without overwriting anything you've already typed — dictate and type interchangeably in the same field.
- No audio is ever sent to or stored by the app backend — this is entirely the browser's own Web Speech API transcribing locally into the existing field, same as typing.
- If your browser doesn't support the API, or recognition hits an error (no speech, mic permission blocked, etc.), a small status line explains what happened without blocking the field — you can always just type your guidance instead.
- The button and its recording state are keyboard-reachable and screen-reader friendly (`aria-pressed`, `aria-label`, live status region).
- `test/smoke-test.ps1` now checks that the dictation button, status region, and Web Speech wiring are present.

**Evidence Review: Major actively orchestrates the hand-off chain, plus a WorkIQ misroute check**

- The Riley → Casey → Drew → Quinn → Major hand-off is no longer just instructional text — Major now actively decides and sequences it. Every time the queued job updates (Casey stamps `knowledgeLinks`, Drew stamps `contentReviewed`, Quinn stamps `qualityVerdict`, or the job otherwise changes), the app re-reads the evidence dossier plus whatever stamps have accumulated so far and auto-advances the job's `handoffTo` to whichever employee is actually needed next — Drew's leg is skipped entirely when the material isn't authored content (a deck/proposal), so the chain only visits the stages a given item really needs. The initial hop is seeded the moment the job is created, so routing starts immediately instead of waiting on the automation's first move.
- New **WorkIQ misroute check**: the evidence dossier now compares what an email is actually asking against your own defined role/responsibilities (the same WorkIQ role text used for career-context capture). When an item is clearly outside your scope — the sweep flags it explicitly, or the message itself names a different owner/team ("please forward this to...", "this belongs to...") — the verdict becomes **🔀 ACT: Delegate**, naming the suggested owner and reason, and the full Casey/Drew/Quinn review chain is skipped since the item was never really theirs to review. This only ever fires when you've defined a WorkIQ role and never fabricates a misroute out of thin air.
- Both the routing decision and the misroute check are visible in the card preview and (for misroute) as a distinct badge, so the recommendation stays traceable to why Major sent it where it did.
- `test/smoke-test.ps1` now checks that `evidence_review_next_hop`, `evidence_misroute_check`, the `content_reviewed`/`evidence_json` job columns, and the `delegate_misroute` badge are wired in.
- Known v1 gaps: the misroute heuristic fallback (when the sweep doesn't explicitly flag it) is a narrow phrase match, not true semantic scope reasoning, so it favors precision over recall; it only activates once you've filled in your WorkIQ role in Career Profile.

**Evidence Review v1: a formal review stage for review-worthy attachments/linked documents**

- Documents-for-review items (Quinn's lane) now get a structured **evidence dossier** instead of a single recommendation line: thread/latest-message summary, an explicit ask extracted from the message when one is stated, importance-to-me vs. importance-to-them, urgency and service-impact, an attachment/document analysis note, and — when the item looks like an ROI deck or similar business case — dedicated ROI fields (investment, expected return, payback period, key assumptions). The dossier only reports figures the sweep actually supplied; it never fabricates ROI numbers, and instead tells Quinn to pull them from the deck during review.
- Every attachment-review card now carries a final verdict — **🔔 ACT**, **ℹ️ FYI**, or **🟡 REVIEW REQUIRED** — with a subtype (e.g. `reply_or_decision_needed`, `reference_only`, `conflicting_priority`) and a concrete next-best action, shown both in the card preview and as a small badge next to the risk pill. REVIEW REQUIRED is reserved for genuinely ambiguous cases (e.g. the sender clearly cares a lot but nothing was explicitly asked of you), so it doesn't over-trigger.
- The dossier and verdict are persisted in a new `evidence_json` column on `approvals` (additive migration, no data loss) and served automatically via the existing `/api/state` payload — no separate endpoint needed.
- Approving an item now routes it through an explicit staff hand-off chain — **Riley** (inbox flag) → **Casey** (knowledge/commitment links) → **Drew** (content judgment, when the material is authored content like a deck or proposal) → **Quinn** (final read of the email body + attachment/document content and verdict) → **Major** (reports back) — carried as instructional text in the queued job, using the existing `handoffTo` job-stamp mechanism to track progress through each stage. Filing of high-value material into the `epiq` working folder is unchanged from the prior release.
- Preserves the existing FYI-vs-action heuristics and epiq filing from the prior release; email/Teams/calendar/suggestions approvals are untouched.
- `test/smoke-test.ps1` now checks that the evidence dossier functions, the `evidence_json` column, the review chain constant, and the verdict badge are wired into `app.py`/`app.js`.
- Known v1 gaps for a follow-up iteration: explicit-ask extraction and importance/urgency classification fall back to light text heuristics when the sweep doesn't supply structured fields, so accuracy improves as sweeps start sending `explicitAsk`/`importanceToMe`/`importanceToThem`/`urgency`/`serviceImpact`/`roiFields` directly.

**Attachment/document review: route review-worthy emails with attachments to a staff reviewer**

- An email that's marked review-worthy and carries an attachment or a linked document (a OneDrive/SharePoint link, for example) no longer sits in the generic Emails lane as a plain inbox skim. It's now reclassified into a new **Documents for review** group, owned by Quinn, so it gets a real content review instead of a subject-line glance.
- The card states explicitly whether you need to act on it or whether it's informational — **🔔 Action needed** or **ℹ️ FYI, no action needed** — using the sweep's own signal when it provides one, falling back to a text heuristic (e.g. "please review and sign" vs "for your records, no action needed") when it doesn't.
- High-value reference material (an ROI deck, proposal, roadmap, business case, etc.) is recognized as worth keeping, not just skimmed. Approving the card routes Quinn to inspect both the email body and the attachment/linked document content, decide FYI vs needs-action, and — if it's worth keeping — file it automatically under the new `epiq` working folder (`{documentRoot}/epiq`), reporting back the exact path.
- Rejecting a Documents-for-review card deletes the source email, same as rejecting a regular email; deferring dismisses the card and leaves the email untouched. Muting (decision memory) and de-duplication now also apply to this lane.
- Email, Teams, and Suggestions approvals without attachments are unaffected — only signals with an attachment or linked document are reclassified.
- The chat-status progress bar (shown while any job — calendar RSVP, sweep, or this new attachment review — is active) now advances in small time-based increments within each status band instead of jumping straight to a fixed width whenever the status changes, and the bar's width now animates smoothly via CSS transition.
- `test/smoke-test.ps1` now checks that the attachment-review routing (Quinn ownership, FYI/action classification, epiq-folder filing) and the smoothed progress bar are wired into `app.py`/`app.js`.

### 4.4.0

Maintained at <https://github.com/TC-Copilot/dream-team-core>. No behavior you rely on changes, and nothing here requires you to reinstall.

**Calendar RSVP UI: 4 states instead of 3**

- The Calendar invites approval group no longer shares the generic Approve/Reject/Defer buttons with email and Teams. It now shows **Accept / Tentative / Follow / Decline**, matching real Outlook RSVP semantics. Accept, Tentative, and Decline each send a real RSVP on the original invite, same as before. **Follow** is new: pick it when you can't attend but still want to keep an eye on the meeting — no RSVP is sent, the invite email is left alone, and Mina keeps watching it and flags reschedules or cancellations.
- Mina's recommendation (e.g. "Tentative/needs decision — direct conflict with an existing recurring meeting...") continues to show right on the card so you can see why she's flagging it before you decide.
- Backend: `/api/approvals/{id}` now validates and stores `accept`/`tentative`/`follow`/`decline` for calendar-type approvals (other approval types are unchanged). The old Defer-for-calendar behavior, which deleted the invite email without RSVPing, is replaced by Follow, which keeps the invite and only stops watching if you later accept/tentative/decline it.
- `test/smoke-test.ps1` now checks that the four calendar RSVP states are wired into both `app.py` and `app.js`.

**Pre-execution freshness check for calendar approvals**

- Before an Accept/Tentative/Follow/Decline decision on a calendar invite is turned into a queued job, the app now re-checks the underlying approval right at the moment you act on it, not just when the card was first loaded. If the invite already ended, was resolved outside the app (for example you replied to it directly in Outlook and a background sweep already marked it `superseded`), or was already decided (a duplicate click, another tab, or a concurrent request beat you to it), no RSVP/follow job is queued a second time — the app reports back that the item was already handled instead.
- This closes a race window: with several requests able to be handled at once, it was previously possible to double-act on the same invite (e.g. queue two conflicting RSVPs) if a decision came in right as a background sweep or another decision was processing it.
- Email, Teams, and Suggestions approvals are unaffected — this check only applies to calendar invites.
- The dashboard now shows a distinct message ("already handled — no RSVP was sent") when this happens, instead of implying an RSVP/follow-up was queued.
- `test/smoke-test.ps1` now checks that the freshness-check function and the `alreadyHandled` response are present and wired into `app.py`/`app.js`.

**Stale-job watchdog**

- Jobs that got stuck in `queued` or `in_progress` because the Scout automation driving them was interrupted (crashed, killed, lost network) no longer sit there forever showing an employee as "working" with no way to recover. On every `/api/state` read (throttled to at most once a minute), the app now checks any job whose `updated_at` is older than the configurable stale-job timeout: an `in_progress` job (automation crashed mid-flight) is reset back to `queued`, while a `queued` job (never picked up at all) is set to `cancelled` instead — re-queuing an already-queued job would be a no-op on status and only bump `updated_at`, creating an infinite loop where the same stuck job keeps "surviving" the stale check. `started_at` is cleared and a note ("auto-requeued after stale timeout" / "auto-cancelled: queued but never picked up within the stale timeout") is appended to the job's blocker. Each change is also logged to the events table.
- The threshold defaults to 2 hours and is configurable via `staleJobTimeoutHours` in `config.json`.
- Jobs still waiting on Quinn's redaction gate (`redactionRequired` with no `redactionApplied`) are never touched by the watchdog — that gate cannot be bypassed by a stale-job reset.
- Fixed the roster's live `workStatus`: only `in_progress` jobs count toward an employee showing "working" now — a `queued` job that hasn't started yet no longer makes the card read "working" before anything has actually begun.
- `smoke-test.ps1` now checks that the watchdog logic is present in `app.py` and wired into `/api/state`.

**Installable, offline-friendly dashboard**

- The dashboard is now a installable Progressive Web App. Open it in a browser and use "Install" (or "Add to Home screen") to get it as its own app window, with an icon, its own theme color, and no browser chrome.
- A service worker precaches the dashboard pages and static assets, so the shell loads instantly and stays usable — showing cached data plus a clear "Offline" indicator — if the local app is briefly unreachable. It never caches the live SSE update stream or any POST request, so approvals, sends, and other actions always go straight to the server.
- Old cached versions are cleaned up automatically whenever the app updates, so you are never stuck on stale assets.
- `preflight.ps1` now also verifies the PWA assets (manifest, service worker, icons, offline page) are present and wired into every page.

**Two new employees**

- **Quinn, quality and risk.** Checks work before it leaves: verifies claims against their sources, confirms citations actually resolve, and rates a draft pass, pass with notes, or hold. A hold keeps the item out of your approval inbox until it is fixed. Quinn also classifies how sensitive content is, flags automations that have gone quiet, and keeps a risk register on the dashboard.
- **Casey, knowledge and commitments.** Remembers people, projects, commitments, decisions, files, and your preferences, so the team stops rediscovering the same things. Overdue commitments surface in your Morning Brief, and anything not touched in a month is flagged as worth re-checking.
- Both are internal-only. Neither ever emails, messages, or publishes anything, which is why both run fully autonomous out of the box — there is nothing they could do to anyone.
- They are not extra automations to install. Your existing Morning Brief, Work Pulse, and Evening Wrap-up now include their steps.

**New things the team can do**

Nine capabilities that run entirely on your machine — no model call and no network — which is what lets the redaction check actually block a send rather than merely warn about one.

- **A quality and brand-voice pass** over any outbound draft, giving Quinn a score and findings to rule on instead of an impression.
- **A redaction gate.** Drafts are scanned for identifier-shaped text — email addresses, phone numbers and the like. If anything turns up, the card shows a red "Redaction required" badge and the item is blocked until it is dealt with. Be clear-eyed about what this is: it matches known patterns, so it is a floor rather than a guarantee. It will not make content HIPAA- or GDPR-safe, and it will miss sensitive things that do not look like identifiers. It clears the obvious cases so your attention is free for the rest.
- **Talk tracks for decks.** Per-slide timing, transitions and pause cues that add up to the length you asked for.
- **Conference session packs.** Title options, an abstract, learning objectives and a bio. It arrives as a scaffold with `[bracketed]` gaps wherever it would otherwise have had to invent a credential about you — fill those in rather than sending it as-is.
- **Charts** from tabular data, which refuse to draw a misleading picture: too many pie slices or a text axis on a line chart come back as a warning instead of an unreadable chart.
- **List formatting** so account and contact lists have consistent columns and types before anything downstream reads them.
- **Skill review and flow documentation** for Piper, plus a **runtime inventory** panel showing what the app can verify about itself. That panel says plainly that it cannot see Scout's own tools, so its numbers are not mistaken for the whole environment.

Cards now show small chips for the extras attached to them, and the Quality panel counts content audits and anything still waiting on redaction.

There is **no compliance or regulation monitor**, deliberately. Monitoring that is only mostly right is a liability rather than a feature: it would need authoritative, continuously updated regulatory sources this offline package does not have, and a wrong "you are compliant" is worse than no answer.

No new automations and no new scheduled runs — the four existing ones simply do more in the passes they already make.

**The other eight got deeper**
Every existing employee gained real capabilities rather than a longer description: Major tracks ETAs and escalates blocked work, Riley classifies thread intent and scores its own drafts, Mina catches meetings with no agenda and carries forward what was decided last time, Reese scores source quality and reuses past research, Tilly protects your focus blocks, Dash reports time saved and approval aging, Drew versions artifacts and checks accessibility, and Logan enforces a publish workflow with link-health checks. The full detail is in the README's Role depth section.

**Two optional employees you can add**

- **Atlas** for account and customer work, and **Piper** for building and validating automations. Both ship as templates in `skills\` and are deliberately *not* installed for you — adding an employee should be your call. The README explains how to add one.

**Install and first run**

- Rewrote `INSTALL-WITH-SCOUT.md` as a numbered runbook. Every step now has a command and an expected result you can check, an explicit list of stop conditions, and a troubleshooting table that maps each common error to the exact fix.
- The installer now writes everything it prints to `install.log` in the install folder, so a failed install leaves evidence behind.
- If the app does not answer within 20 seconds of starting, the installer now prints the actual Python error that stopped it and exits with a failure code, instead of reporting a silent success.
- The installer prints a summary at the end: what it did, where it installed, the port, the documents folder, the model, and where the log is.
- `-NoBrowser` is now a real switch on the installer.
- Fixed the app failing to start on some machines. The version stamp was written with a byte-order mark, and printing it to a Windows console raised an encoding error that killed the app before it opened its port. The same byte-order mark was also making `config.json` fail to parse silently.

**Automations**

The package ships **four** automations and installs all four switched on. That has not changed, but the file that describes them now says so in a way that cannot drift: it records both the total and how many are on by default, each automation carries its own recommended status and the reason for it, and a new check fails the build if those numbers ever stop matching reality.

| Automation | Schedule | Status |
| --- | --- | --- |
| Daily Flow Morning Brief | every weekday at 7am | required |
| Daily Flow Evening Wrap-up | every weekday at 5pm | required |
| Daily Flow Continuous Work Pulse | every hour | required |
| Daily Flow Attention Major Trigger | every 5 minutes | required |

**Your data**

- **Export all data.** A new button on the dashboard downloads everything the app holds — every database table and every file in your documents folder — as a single ZIP.
- **Reset all private data.** A new button deletes all of your history in one step. Your team roster, your career profile, and your settings survive; the work history does not. It asks first, and it is not reversible, so export before you use it.

**Security**

The app has always been reachable only from your own machine. These add protection from other software and other browser tabs on that machine.

- Optional local access token. Off by default, so nothing that works today stops working. When you switch it on, anything that reads your private data or asks the team to do work has to present a token that only you have.
- Requests coming from another web page are now rejected, so a page in another tab cannot quietly ask your team to do something.
- Oversized uploads are refused rather than absorbed, and file paths are resolved so nothing can be read from outside the app's own folders.

**Speed and reliability**

- Added database indexes for the queries the dashboard runs on every load, so the board stays fast as your history grows.
- The dashboard state request is now capped and supports asking for only what changed since a given time. Totals and metrics are still calculated from your full history, so no number on the board changes.
- Pressing **Attention Major** twice, or an automation retrying, can no longer queue the same sweep twice.
- Stopping the app now settles the database cleanly instead of leaving a write-ahead file behind.

**Maintenance**

- New `test/smoke-test.ps1` starts the app on a spare port and checks health, state, gate, activity log, and every static file, with and without the token.
- New GitHub Actions CI runs the syntax checks, the clean-room scan, and the smoke test on Windows for every push.
- New [`docs/API.md`](docs/API.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- The clean-room scanner and the packager now also refuse to ship a local token or an install log.

### 4.3.1

- Fixed approved work sitting in the queue instead of running. When you pressed **Attention Major**, or approved something in the inbox, the request was recorded correctly but the background worker could not read it back, so it waited rather than starting. It now picks the work up on its next check, which is within five minutes.
- Stopped the background sweep from running twice. The hourly pass across your email, Teams, and calendar was also handing a second copy of the same sweep to the five-minute worker, so the same work was done twice and billed twice. The hourly pass now does it once, on its own. Nothing about what gets scanned has changed.
- Slowed that hourly pass from every 30 minutes to every hour. Together with the duplicate fix, a normal day goes from 96 full sweeps to 24. The **Attention Major** button is still there when you want the board refreshed immediately, and you can set the pulse back to 30 minutes in Scout if you prefer.
- Fixed Send on a prepared draft. When you clicked Send on something the team had written for you, the worker was not told that counted as work it should carry out, so the item could sit unsent. It now delivers exactly what you approved, without rewriting it.
- Wrote down the rules the background workers actually follow. The internal notes the team reads had drifted from how the app really behaves, which is what allowed the duplicate sweep and the stuck queue to go unnoticed through a release. They now describe the real behavior, including that a pending job is always your work and should never be skipped for looking unfamiliar.
- Corrected the setup notes, which still described the old timings from before 4.3.0.

### 4.3.0

- Cut what the background workers read, which is the main thing you pay for. The every-few-minutes worker used to pull your whole board just to find out whether anything needed doing, and on most runs the answer was no. It now asks a small question first, and that check is over 99 percent smaller than what it replaced. When a run does have work, it reads a trimmed view instead of the full one. That saving grows with your history, because what it leaves out is the completed-job and event backlog: on a fresh install there is barely any difference, and on a board with a few weeks of real use the trimmed view is roughly 85 to 90 percent smaller. What the team does has not changed, only how much it reads to decide. The dashboard in your browser still gets everything.
- Slowed the Attention Major worker from every minute to every five minutes. Running it every minute was a large share of the running cost, and the button it serves does not get pressed sixty times an hour. Five minutes still feels immediate when you press it. You can set it back to every minute in Scout if you prefer the old behavior.
- Moved the team onto Claude Opus 5, which is what it is now tuned for. Setup still shows whatever models your Scout offers and falls back to the best one available, so nothing breaks if you do not have Opus 5 yet.
- Added a warning on the dashboard when any of the four automations is switched off or missing. A paused automation does nothing, and until now the only sign was a board that quietly stopped updating, which is easy to mistake for a quiet day. The dashboard now reads your Scout automation settings and names the ones that are off.

### 4.2.1

- Made install and setup one smooth flow in a single chat. Scout now installs the app and then finishes setup right there, so you no longer have to quit Scout, reopen it, and paste a command. When Scout says it is done, your team is on and your dashboard is already showing your real day.
- Fixed the empty-dashboard-after-setup problem at its root. Setup now runs your first sweep itself instead of handing it to a background timer that could not run yet, so the board actually fills before Scout finishes. It also switches the four automations on and double-checks they are on, since a paused automation does nothing.
- Made the restart optional and clearly labeled as such. The team is live without it. Restarting Scout later only registers the `/daily-flow-setup` and `/daily-flow-team` shortcuts for future use.
- Pointed Microsoft employees to the right place to get Scout. The prerequisites now note that Microsoft employees install Microsoft Scout from an internal aka.ms site, while everyone else uses the public link.

### 4.2.0

- Made the install steer itself onto the strongest model. The paste-in prompt and the Scout install guide now ask Scout to run setup on Claude Opus 4.8 when it is available, which is the model the team is tuned for and the one that follows the steps most reliably.
- Fixed setup declaring itself done over an empty dashboard. The wizard now kicks off the first sweep, waits for the board to actually fill, and only then hands off. It also tells you the truth about timing: a first sweep takes about 5 to 10 minutes, not seconds.
- Added a first-run banner on the dashboard so a new user is never staring at a blank board wondering what to do. It says the first sweep is running and the board fills as it goes, switches to a friendly all-caught-up note when there is genuinely nothing to show, and disappears once real items arrive.
- Made the setup wizard present its remaining choices as real decisions instead of buried afterthoughts. The model choice and the default-model prompt are now asked as their own cards during the flow, each with a recommended default, rather than appearing for the first time as a line in the closing summary.
- Added a "The Dream Team" shortcut to your desktop during install, so you can reopen the dashboard anytime with one click. It starts the app first if it is not already running, so it always lands on a live board.

### 4.1.0

- Made the background automations install the same for everyone. When Scout sets up the team, it now places each automation's instructions exactly as written and then reads them back to confirm they match, instead of retyping them from memory. Before this, two people could end up with slightly different wording. If one does not match, Scout redoes just that one, once, then tells you rather than looping.
- Slimmed the automation set to the four that run the team: the 7am Morning Brief, the 5pm Evening Wrap-up, the every-30-minute Work Pulse, and the every-minute Attention Major worker. Three extras that used to ship turned off have been removed to keep things simple and predictable. All four now install turned on.
- Fixed the empty dashboard on a fresh install. Right after setup the team does one pass across your email, Teams, calendar, and meeting prep, so the board shows your real day within about a minute instead of opening blank.
- Tightened the every-minute worker so it is plainly a worker only: most minutes it checks once and stops, it never starts its own sweeps, and it cleans up after itself so it does not clutter Scout.

### 4.0.4

- Tidied how release notes are kept. This changelog is now the single place that lists what changed in each version. Previously there was also a separate notes file per version cluttering the project, and those have been removed. The notes shown on each GitHub release are now taken straight from this file.

### 4.0.3

- Changed how you install. The easy way is now to open Microsoft Scout and ask it to install the Dream Team from GitHub. Scout downloads it, sets it up, checks that it worked, and fixes common problems like missing Python or a busy port on its own. If it cannot solve something, it stops and tells you plainly instead of looping.
- Added INSTALL-WITH-SCOUT.md, a short guide Scout follows to do the install, with clear stop conditions so it never gets stuck in a loop.
- Retired the double-click START HERE.cmd and Check Setup.cmd. Those were the most common source of setup trouble, because Windows would sometimes run them from inside the zip or block them. The install now runs through Scout, with a short manual fallback in the README for the rare case Scout cannot do it.
- Rewrote the README around the new flow.

### 4.0.2

- Fixed the most common setup problem. If you started setup from inside the downloaded zip without extracting it first, you used to get a confusing error about a missing install file. START HERE.cmd now notices this and tells you, in plain words, to extract the zip first and try again. Check Setup.cmd does the same.
- Wrote this full changelog so you can see how the project has grown over time, with a short summary of what it does today at the top.
- Cleaned up the README so it reads more plainly.

### 4.0.1

- Added an MIT license and a short disclaimer. The disclaimer makes clear this is a personal project, not an official Microsoft product, and provided as is. No change to how the app works.

### 4.0.0

- First public release on GitHub. This is the full eight-person team, packaged so anyone on Microsoft Scout can run it.
- It runs on two bundled skills plus the skills already built into Scout, so it works without a corporate sign-in.
- Setup figures out on its own whether you are signed in with Microsoft and adjusts. If you are a Microsoft employee, it offers some optional extra depth, fetched into your own Scout. That depth is never part of the package.
- Every employee has a plain-Scout way to do its job, so nothing breaks if an optional add-on is missing.
- The document folder now finds your OneDrive on any machine, and falls back to a local folder if OneDrive is not set up.

The releases below were shared as zip files before the project moved to GitHub. They are listed here for history.

### 3.3.9

- Setup now checks that Microsoft Scout is actually installed before it acts ready, instead of leaving you with a dashboard that loads but does nothing.
- Employees you add yourself now get picked up and put to work, and what they produce shows up in your results.
- The roster shows real status for each person, working, blocked, paused, or ready, instead of always saying ready.

### 3.3.8

- The capability map on the architecture page now shows real usage numbers for each skill, instead of dashes.

### 3.3.7

- Fixed the real cause behind "I approved it but nothing went out." The background workers that carry out approved actions were still holding an old draft-only rule. An approved reply now actually sends.

### 3.3.6

- Documents the team prepares for you now open correctly from the results list, including a clean reading view for notes and briefs. They used to fail with a not-found error.

### 3.3.5

- Un-muting an item brings it straight back to the approval inbox, and the muted list stays open when you expand it.

### 3.3.4

- When you tell the team what to do on an item, like reply, react with a thumbs up, or forward, it does that exact thing instead of turning everything into a generic draft. It only drafts when you ask.

### 3.3.3

- Approving an email or Teams message in the inbox now sends it. Approval is your go-ahead. The trust levels only govern work the team starts on its own.

### 3.3.2

- Added links on inbox cards to open the original message. Teams replies now actually reach Teams.

### 3.3.1

- Restored the approval buttons after a bug had quietly broken them, made the trust levels actually change behavior, and made the installer handle upgrades cleanly while keeping your data and any employees you added.

### 3.3.0

- You can build the team you want. Add your own employees through a guided onboarding, or remove anyone except Major and bring them back later.
- Setup got tougher about the one thing it really needs, Python, and can install it for you if it is missing or too old.

### 3.2.1

- A polish pass. The adoption view can be scoped by time, the cockpit sections collapse and stay that way, chat statuses tell the truth instead of getting stuck, and the wording reads in the first person.

### 3.2.0

- Added a private career profile. Paste your job description and how your performance is measured, and the team captures and frames your work against what your review actually rewards. It stays on your machine.

### 3.1.0

- Made the per-employee trust levels real. Draft, Assist, and Autonomous now actually control how far each person goes, with a firm rule that confidential content always waits for you.

### 3.0.1

- Theme polish for the then-new look.

### 3.0.0

- A big step up in trust and transparency. Each employee got a trust level and a clear set of what it will and will not do on its own. Added memory that stops re-surfacing things you already dismissed, a guardrails panel that shows the safety model in plain view, an adoption view, and the ability to spin up short-lived helpers for one-off batch work. Everything the team makes still goes to you only.

### 2.1.0

- Fixed skills installing to the wrong folder on some machines, which had stopped the setup command from being recognized. Started naming releases by version so you can tell builds apart.

### 2.0.0

- One download for everyone, with the setup wizard asking who you are and adapting. Added a model choice at setup, and optional extra depth for signed-in Microsoft employees.

### 1.0.0

- The first shareable build. It included the local dashboard, the team of digital employees, the background automations, the guided setup, a one-click installer, and a check that keeps personal data out of the package.
