# The Dream Team for Microsoft Scout

![Version](https://img.shields.io/badge/version-4.3.1-blue)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6)
![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

Your own team of ten AI digital employees, running locally on Microsoft Scout.

Version 4.3.1. See [CHANGELOG.md](CHANGELOG.md) for the full history.

The Dream Team is a local command center plus a team of digital employees that run on [Microsoft Scout](https://learn.microsoft.com/en-us/microsoft-scout/). They watch your work signals, prep your meetings, draft your replies, keep a record of what you got done, and hold anything sensitive for your approval. It all runs on your own machine. Start with the built-in ten, add your own, or remove any of them except Major.

Windows only. The app and the install run on Windows 10 and 11. They will not run on macOS or Linux. There is more in the Platform support section below.

Private by design. Everything runs on 127.0.0.1 and stores to a local database on your own machine.

Not an official Microsoft product. This is a personal project, shared as is for personal and demo use. It is not built, endorsed, or supported by Microsoft, and it is not meant for production. See the Disclaimer and license section below.

## This is the core edition

This repository is the **public core edition** — the complete, public-safe baseline. Everything the
product does is here: the app, the dashboard, all four automations, all ten digital employees, and
every capability endpoint. Nothing is held back behind a sign-in wall, and nothing here needs a
gated or internal source to install or run.

A separate private edition exists for Microsoft employees. It is a thin overlay on this repository
and adds exactly one thing: an optional step that pulls internal-only depth skills into the user's
own Scout. It adds no employee, no endpoint, no automation and no dashboard feature.

| Capability | Core edition (this repo) | Employee edition |
|---|---|---|
| Major — Chief of Staff | Full | Same |
| Riley — Inbox | Full | Same |
| Mina — Meetings | Full | Same |
| Reese — Research | Full | Same |
| Tilly — Scheduling | Full | Same |
| Dash — Dashboard | Full | Same, plus optional internal reporting sources |
| Drew — Content | Full | Same, plus optional internal branding/design skills |
| Logan — Web | Full | Same |
| Quinn — Quality & Risk | Full | Same |
| Casey — Knowledge & Commitments | Full | Same |
| Dashboard, approval inbox, trust levels | Full | Same |
| All four automations | Full | Same |
| All eight capability endpoints | Full | Same |
| Local token auth, export, reset | Full | Same |
| Optional internal depth skills | Not applicable | Optional extra step |

The honest summary: the employee edition is this product plus one optional acquisition step. If you
are not a Microsoft employee, you are not missing a feature.

## Requested skills and their equivalents here

People often ask for these standalone Scout skills by name. Each one's behaviour is implemented
locally in this package. This is a behavioural mapping, not a dependency — nothing below is
installed, imported or called by this package.

| Requested capability | Implemented equivalent | Owner |
|---|---|---|
| content-quality-auditor | `POST /api/content-pass` + Quinn's review verdicts | Quinn |
| institutional-knowledge | Knowledge graph (`/api/knowledge`) + Casey | Casey |
| work-brief | Morning Brief and Evening Wrap-up cadence | Major |
| presentation-talk-track-builder | `POST /api/talk-track` | Drew |
| brand-voice-pass | `POST /api/content-pass` with `brandVoice` | Drew, Riley |
| chart-builder | `POST /api/chart-spec` | Dash |
| skill-authoring-coach | `POST /api/skill-lint` | Piper |
| agent-harness-explorer | `GET /api/runtime-inventory` | Piper |
| sharepoint-list-formatter | `POST /api/format-list` | Casey, Reese |
| power-automate-documentation | `POST /api/document-flow` | Piper |
| phi-deidentifier | Sensitive-text scan and redaction gate in `/api/content-pass` | Quinn |
| b2b-outreach-suite | Normalized lists + brand-voice pass + Quinn gate + approval policy | Riley, Reese |
| conference-session-abstract-pack | `POST /api/conference-pack` | Drew |

**`regulation-monitor` is intentionally not implemented**, in either edition. Compliance monitoring
that is only mostly right is a liability rather than a feature: it would need authoritative,
continuously updated regulatory sources this offline package does not have, and a wrong "you are
compliant" is worse than no answer. Use a dedicated tool with a real regulatory data source.

## What you get without signing in

The Dream Team works for anyone on Microsoft Scout who is signed into their own Microsoft 365. The whole ten-person team is there: inbox triage and replies, meeting prep and follow-ups, research, scheduling, document and deck creation, dashboards, quality review before anything leaves, a local knowledge graph of your commitments and decisions, the approval inbox, the trust levels, and the always-on automations. All of it runs on the two skills in this package plus the skills already built into Scout, so nothing important sits behind a corporate login.

If you happen to be a Microsoft employee, signing in during setup adds some extra depth, mostly for Dash (richer reporting) and Drew (branded templates and image generation). That depth is fetched into your own Scout during setup. It is never part of this package.

You can add your own employees or remove any of them except Major, so the roster is yours to shape.

## What you need

- Windows 10 or 11. See the Platform support section below.
- [Microsoft Scout](https://learn.microsoft.com/en-us/microsoft-scout/), the desktop assistant this team runs inside. **Microsoft employees:** get Scout from an internal aka.ms site, not the link above.
- Python 3.9 or newer. This is the only thing the app itself needs, and it is plain Python with no extra packages. If you do not have it, Scout can install it for you during setup, or you can get it from <https://www.python.org/downloads/> and tick "Add Python to PATH".
- Scout allowed to run shell and file commands. The install lets Scout set everything up for you, so it needs permission to run commands and to read and write files. Scout asks for this, and you approve it when prompted.
- A Microsoft 365 sign-in inside Scout. This is recommended so the team can see your own email, calendar, and Teams. A personal or a work account both work for the core experience.

## Install it

The easy way is to let Scout do the whole thing in one go. Open Microsoft Scout, start a chat, and if you can, set that chat's model to Claude Opus 5, which runs setup most reliably. Then paste this:

> Install The Dream Team from https://github.com/TC-Copilot/dream-team-core. First, if this chat is not already on Claude Opus 5, tell me so I can switch to it before you continue, since it runs setup most reliably. Then read INSTALL-WITH-SCOUT.md in that repo and follow it exactly, including the stop conditions.

If you already have the package folder on disk (for example from a shared ZIP), point Scout at it instead — the repository is private, so a plain download only works for people in the `TC-Copilot` organization:

> Install The Dream Team from the folder at `C:\path\to\dream-team`. Read INSTALL-WITH-SCOUT.md in that folder and follow it exactly, including the stop conditions.

From there, Scout does everything in that same chat: it downloads the latest release, sets up the app, installs your team, switches on the background automations, and runs your first sweep so the dashboard fills with your real email, calendar, Teams, and meeting prep. It fixes common problems on its own, like missing Python or a busy port, and if it hits something it cannot solve, it stops and tells you plainly instead of looping.

You do not need to quit Scout, reopen it, or type any commands. When Scout says it is done, your team is live, your dashboard is open and showing your real day, and a **The Dream Team** shortcut is on your desktop so you can reopen the dashboard anytime. That first sweep takes about 5 to 10 minutes, and the board fills in as it goes, so a fresh dashboard is never left blank.

**A note on permissions.** The install needs ordinary PowerShell, file access inside your own folders, and a local web address. It never needs unsafe browser code execution, Administrator rights, or writes to `C:\Windows`, `C:\Windows\System32`, or `Program Files`. If Scout or any tool asks for those, deny them — nothing in The Dream Team belongs in a system folder. The full allow and deny lists are in sections 11b and 11c of [INSTALL-WITH-SCOUT.md](INSTALL-WITH-SCOUT.md).

## One setup path for everyone

There is no tiering and no gated content. The wizard installs the same complete team for every user,
running on the skills in this package plus the ones built into Scout. No corporate sign-in is needed
to get the full team.

Signing in to your own Microsoft 365 is still worth doing — it is what lets the team read your real
mail, calendar and Teams signals — but it unlocks nothing that other users cannot have.

## Pick your model

The wizard lets you choose the model your team runs on. The default is Claude Opus 5, which is what the team is tuned for, but you can pick any model your Scout offers, or choose Auto. If Opus 5 is not available on your machine, the wizard recommends the best alternative for you.

## Your team

| Employee | Role |
|---|---|
| Major | Chief of Staff. You talk to Major, and Major routes the rest. |
| Riley | Inbox. Triage and draft replies. |
| Mina | Meetings. Prep, notes, and follow-ups. |
| Reese | Research. Cited answers and account context. |
| Tilly | Scheduling. Availability and RSVP risk. |
| Dash | Dashboard. Status, approvals, and metrics. |
| Drew | Content. Docs, decks, and briefs. |
| Logan | Web and publishing. Sites and artifacts. |
| Quinn | Quality and risk. Reviews anything before it leaves, checks claims and citations, keeps the risk register. |
| Casey | Knowledge and commitments. Remembers people, projects, commitments, and decisions so nothing is rediscovered. |

Quinn and Casey are internal-only. Neither one ever emails, messages, or publishes anything — Quinn reads what the others prepared and can hold it back, and Casey only reads and writes the local knowledge store. That is why they run at Autonomous by default: there is nothing they could do to anyone.

Every employee works on the two skills in this package plus Scout's built-ins. You can add your own employees from the cockpit, where the "Add Employee" button walks you through onboarding one of your existing Scout workflows. You can also remove any employee except Major, and their history is kept so you can bring them back later. Employees you add stay on your machine and are never included if you re-share the package.

## Template employees

Two more employees ship as templates rather than as part of the team, because whether you want them depends on what your job actually is. They are not installed automatically — adding an employee should be your decision.

| Template | What it does |
|---|---|
| [Atlas](skills/atlas-template/SKILL.md) | Account and customer intelligence: account dossiers, renewal dates, stakeholder maps, customer-meeting prep, opportunity notes, normalized account lists, and outreach preparation (prepared for you to approve, never sent). Useful if you carry accounts. |
| [Piper](skills/piper-template/SKILL.md) | Automation and workflow building: validates automation prompts, reviews `SKILL.md` files for structure, documents Power Automate flows, reports the runtime inventory, packages new employees, detects drift between what is running and what is configured, and proposes automations for gaps it can evidence. Useful if you tinker with the machinery. |

To add one, copy its folder from `skills\` into your Scout skills directory (`%USERPROFILE%\.scout\m-skills\`), rename it if you like, then ask Scout to run the Dream Team setup again so it is registered. Each template's own file has the full instructions.

## Role depth

Each employee does more than its one-line role suggests. The full instructions live in [`skills/daily-flow-team/SKILL.md`](skills/daily-flow-team/SKILL.md); this is the shape of it:

| Employee | Depth |
|---|---|
| Major | Ranks work against your open goals, writes a delegation plan into each job, stamps an ETA and flags jobs that blow through it, escalates anything blocked over 30 minutes, traces every handoff, and explains why each item matters today. |
| Riley | Classifies each thread's intent, extracts commitments into Casey, keeps a VIP watchlist, scores its own draft replies before showing them to you, sets follow-up reminders, routes mail that belongs to someone else, and applies your filing rules. |
| Mina | Flags meetings with no agenda, pulls attendee dossiers, carries forward what was decided last time in a recurring series, extracts decisions and action items afterwards, and produces the whole follow-up package. |
| Reese | Saves reusable research dossiers and checks for one before starting over, produces a claim-to-source-to-confidence matrix, keeps per-account context, scores source quality, watches named topics, and hands finished research to Drew. |
| Tilly | Scores candidate slots on availability, buffer, focus time, and time of day, protects blocks of two or more hours, adds travel time for in-person meetings, scores RSVP risk, flags overloaded days, and drafts polite negotiation messages. |
| Dash | Tracks automation reliability over 7 and 30 days, per-employee throughput, approval aging, estimated time saved, blocked-work patterns, what a trust-level change would have meant last week, and which model and skill each job used. |
| Drew | Versions artifacts, keeps a template library and style packs, flags citations older than 90 days, converts documents into decks, runs accessibility checks, and hands everything to Quinn before it is called ready. |
| Logan | Keeps a registry of published artifacts, checks their links weekly, enforces draft → Quinn → approved → published, gates private/internal/public visibility, generates changelogs from version diffs, and runs web accessibility checks. |
| Quinn | Verifies claims against their sources, checks citations resolve, returns pass / pass-with-notes / hold on any draft, classifies sensitivity, runs a content-quality audit and a redaction gate over outbound drafts, flags automations that have gone stale, audits completed work for fabrication, and maintains the risk register. |
| Casey | Maintains the knowledge graph — people, projects, recurring meetings, commitments, decisions, files, preferences — normalizes and de-duplicates what it ingests, surfaces overdue commitments, enriches other employees' work with context, learns your scheduling preferences, and flags knowledge that has gone stale. |

## What the team can do for you

Beyond the roles themselves, the app ships a set of local capabilities the team calls by name. They run entirely on your machine — no model call, no network — which is what lets the redaction check actually *block* a send rather than merely advise one.

| Capability | What it produces | Who uses it |
|---|---|---|
| Content quality + brand voice pass | A score, findings and a verdict on any draft | Quinn, Drew, Riley |
| PHI/PII redaction gate | Flags identifier-shaped text and blocks the item until it is redacted | Quinn |
| Talk track builder | Per-slide timing, transitions and pause cues for a deck | Drew |
| Conference session pack | Title options, abstract, objectives and a bio scaffold | Drew |
| Chart builder | A chart schema from tabular data, with warnings when a chart would mislead | Dash |
| List formatter | Consistent column names and types for list-shaped data | Casey, Reese |
| Skill authoring review | Structure checks on a `SKILL.md` | Piper |
| Flow documentation | A plain-language summary of a Power Automate flow | Piper |
| Runtime inventory | What this machine can verify about the app itself | Dash, Piper |

Two things these deliberately will **not** do. The redaction scan matches known patterns, so it is a floor rather than a certification — it does not make content HIPAA- or GDPR-safe and does not replace reading the draft. And the conference pack leaves `[bracketed]` gaps wherever it would otherwise have to invent a credential about you, rather than filling them in with something plausible.

There is no compliance or regulation monitor, on purpose. Monitoring that is only mostly right is a liability: it would need authoritative, continuously updated regulatory sources this offline package does not have, and a wrong "you are compliant" is worse than no answer at all.

Full details in [`docs/API.md`](docs/API.md).

## Managing the app

- Start the app or open the dashboard: `app\start-app.ps1`, in your install folder.
- Stop the app: `app\stop-app.ps1`.
- Reconfigure, pick a different model, or recreate the automations: just ask Scout to run the Dream Team setup again. It is safe to run more than once. (If you have restarted Scout since installing, you can also type `/daily-flow-setup`.)

## If Scout can't install it

Almost everyone can stop at the Install it section above. This is the manual path for the rare case where Scout cannot run the install for you, for example on a machine where it is not allowed to run commands. It ends the same way as the easy path: Scout finishes setup in a chat, with no restart needed.

1. Open the [Releases](../../releases) page and download the latest `dream-team-core-v*.zip`.
2. Right-click the downloaded file, choose Extract All, and pick a folder. Extract it first. Do not run anything from inside the zip preview window.
3. Open a PowerShell window in the extracted folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto
```

4. Open Microsoft Scout and paste this so it finishes setup in the chat, the same way the easy path does:

> Finish setting up The Dream Team. Read and follow the daily-flow-setup skill: confirm my sign-in, let me pick my model, switch on the four background automations, and run my first sweep so my dashboard fills with my real data.

Scout finishes right there in the chat, and your dashboard fills within about 5 to 10 minutes. You do not need to restart Scout or type a slash command. (If Scout says it cannot find the skill, fully close and reopen it once so it loads, then paste the same message again.)

## Platform support

This package runs on Windows 10 and 11 only. It does not run on macOS or Linux. The app itself is plain Python and the skills are plain Markdown, so the idea is portable, but the installer and the setup checks are written for Windows as PowerShell files, and there is no macOS or Linux version in this package today.

## Privacy

The app binds to 127.0.0.1 and stores everything in a local SQLite database on your machine. Nothing is sent anywhere outside your machine. The team prepares drafts. It does not send email, Teams messages, or calendar responses to other people without your approval in the dashboard.

If you add a career profile, meaning your job description and how your performance is measured, on the Impact Ledger, that is kept especially private. It lives only in your local database and is never included when you re-share the package. A copy you give to someone else starts blank.

## Disclaimer and license

This is a personal, community project, and it is not an official Microsoft product. It is not built, endorsed, supported, or maintained by Microsoft. Names like Microsoft Scout and Microsoft 365 are used only to describe the platform this tool runs on. Any views or work here are the author's own and do not represent Microsoft.

It is provided as is, for personal and demo use, and it is not meant for production. The Dream Team is shared free of charge with no warranty of any kind. It can read and act on your own Microsoft 365 data. It drafts, and with your approval it can send email and Teams messages and respond to calendar invites, so you use it at your own risk. Always review what it prepares before you rely on it. To the maximum extent allowed by law, the author is not liable for any outcome, data loss, missed or mistaken message, or other damage that comes from using it.

Licensed under the [MIT License](LICENSE).

Built by Shervin Shaffie. Shared for other Microsoft Scout users.

## Attribution

This repository is maintained at
<https://github.com/TC-Copilot/dream-team-core>.

The original project is by [@ShervinShaffie](https://github.com/ShervinShaffie) at
<https://github.com/ShervinShaffie/dream-team-for-microsoft-scout>, and all credit for the design
and the original implementation belongs there. This copy carries additional work — install and
runbook hardening, local-token auth, privacy export/reset, the Quinn and Casey roles, the local
knowledge graph, docs, and CI — and it stays under the same MIT license.

## Documentation

| Document | What's in it |
| --- | --- |
| [INSTALL-WITH-SCOUT.md](INSTALL-WITH-SCOUT.md) | The numbered install runbook, with verification at every step and a troubleshooting table. |
| [docs/USER-GUIDE.md](docs/USER-GUIDE.md) | Day-to-day use: talking to Major, trust levels, approvals, quality and knowledge, shaping the team. |
| [docs/API.md](docs/API.md) | Every HTTP endpoint: auth, query params, request and response shapes, status codes. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System diagram, component responsibilities, data flow of an automation run, database schema, capability layer, timezone and security model. |
| [skills/daily-flow-team/SKILL.md](skills/daily-flow-team/SKILL.md) | The team's operating manual, including an "External skill equivalence map" if you are coming from standalone Scout skills and want to know where each behaviour lives here. |
| [CHANGELOG.md](CHANGELOG.md) | Release history. |
