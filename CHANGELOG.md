# Changelog

This page lists what changed in each release of The Dream Team for Microsoft Scout, newest first.

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
