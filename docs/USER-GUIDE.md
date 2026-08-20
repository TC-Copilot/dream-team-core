# The Dream Team — user guide

This is the day-to-day guide: what your team does while you work, how to talk to it, and how to
decide what it's allowed to do on its own.

If you haven't installed it yet, start with [INSTALL-WITH-SCOUT.md](../INSTALL-WITH-SCOUT.md) and
come back here.

**The one thing to understand first:** you don't operate ten employees. You talk to **Major**, and
Major routes everything else. If you only ever learn one thing from this guide, learn that.

---

## 1. Your first day

### Open the dashboard

Double-click **The Dream Team** on your desktop, or go to <http://127.0.0.1:8787/>.

The dashboard is a *view*, not a control panel. The team runs whether or not it's open. Closing it
stops nothing; leaving it open changes nothing.

### What the panels mean

| Panel | What it's telling you |
| --- | --- |
| **Guardrails** | What each employee is allowed to do without asking. Start here — see §4. |
| **Quality & knowledge** | Quinn's risk register and Casey's knowledge graph. See §5. |
| **Approval inbox** | Things waiting on *you*. This is the only panel that needs action. |
| **Results and drafts prepared** | Finished work and drafts ready to review or send. |
| **Chat with Major** | Talk to your team without leaving the dashboard. |
| **Your data** | Export everything, reset everything, set the local token. See §7. |

**If the Approval inbox is empty and the activity log has recent entries, nothing is wrong.** That's
the normal state. An empty board after a completed sweep means there was genuinely nothing to raise,
not that the team is asleep.

### The first sweep

Your first sweep runs during setup and takes **5 to 10 minutes**. The board fills in progressively.
You don't need to refresh or keep Scout in the foreground.

---

## 2. Talking to your team

Talk to Major in a Scout chat, or in the dashboard's **Chat with Major** panel. Plain language is
fine — you don't need commands or syntax.

```
What needs me today?
```
```
Prep me for my 2pm with Contoso.
```
```
Draft a reply to Priya saying I can't make Thursday but Friday morning works.
```
```
What did I commit to this week?
```
```
Find out what Fabrikam announced at their conference and give me sources.
```

You can name an employee if you want to be specific — *"ask Reese to..."* — but you rarely need to.
Routing is Major's job.

Major routes quick work differently from work that needs substantial judgment. Simple deterministic
checks run locally; routine classification, dispatch, and ordinary drafting use Scout's automatic
routing. Complex reasoning, high-risk review, and final synthesis use the reasoning model you
selected during setup, and Major records why a task was escalated. You ask in the same plain
language either way; routing never weakens evidence, review, or approval safeguards.

### What each employee actually does

| Employee | Ask them for | They never |
| --- | --- | --- |
| **Major** | Anything. Routing, priorities, "what needs me?" | — |
| **Riley** | Inbox triage, draft replies, commitments made in email | Send without your say-so unless set to Autonomous |
| **Mina** | Meeting prep, notes, action items, follow-ups | — |
| **Reese** | Researched answers with real sources and confidence ratings | Present a claim without a source |
| **Tilly** | Availability, scheduling options, RSVP risk | Accept or decline for you unless set to Autonomous |
| **Dash** | Status, metrics, throughput, time saved | — |
| **Drew** | Docs, decks, briefs, artifacts | Publish without Quinn's sign-off |
| **Logan** | Publishing, sites, artifact registry, link health | Publish without Quinn's sign-off |
| **Quinn** | A review before something leaves | Contact anyone, ever — internal only |
| **Casey** | "Who is X?", "what do we know about Y?", open commitments | Contact anyone, ever — internal only |

Quinn and Casey are **internal-only by design**. Quinn reads what the others prepared and can hold
it back; Casey only reads and writes the local knowledge store. Neither can email, message, or
publish. That's why both run at Autonomous by default — there's nothing they could do to anyone.

---

## 3. The rhythm — what happens without you

Four automations run on a schedule. All four are on by default.

| When | What runs | What you get |
| --- | --- | --- |
| Weekdays 7:00am | **Morning Brief** | What matters today, commitments due, meetings needing prep |
| Every hour | **Work Pulse** | Continuous triage, prep, drafting as things arrive |
| Every 5 minutes | **Attention Major Trigger** | Picks up anything you flagged for immediate attention |
| Weekdays 5:00pm | **Evening Wrap-up** | What got done, what's open, what's carried to tomorrow |

You can pause any of them in Scout's automations list. Be aware that **a paused automation fails
silently** — it just quietly stops doing its part. If the board looks stale, check there first.

To force a sweep right now, press **Attention Major** on the dashboard.

### Start fresh without deleting your work

In **Your data → Fresh processing start**, use **Clear app cache / fresh start** when the dashboard
or a prior sweep appears stale. After confirmation, Dream Team advances an ephemeral processing
generation and refreshes only its own PWA application caches.

This action does **not** delete durable user records, handled items, approvals or history, documents,
files, or anything in OneDrive. In particular, it never deletes `Documents/ScoutTeam`. Use the
separate **Reset / delete all private data** control only when you intentionally want the destructive
full-data reset described by that control.

To re-check recent sources, select **1 to 5 days** and choose **Queue Scout sweep**. Scout receives a
bounded job to re-examine real Outlook email and Teams chats/channels using its authorized Microsoft
tools. The dashboard does not read Microsoft data itself. The panel shows queued, in-progress,
completed, or blocked status and progress. Clicking again for the same active window reuses the
existing job instead of creating a duplicate. Starting a fresh processing generation supersedes any
older active request without deleting its history, so the next click queues a current-generation job.

The five-minute trigger checks whether work is waiting before reading team state. If nothing needs
attention, it ends cleanly without creating noise. When work exists, it fetches the waiting job
directly; broader automations use a focused current view and refresh only what changed during the
run. Active and blocked work is always kept in view, so this faster refresh cannot hide an
unfinished item.

---

## 4. Trust levels — the most important setting

Each employee runs at one of three levels. This is the single control that decides how much happens
without you.

| Level | What it means |
| --- | --- |
| **Draft** | Prepares everything; **you** send it. Nothing leaves without your click. |
| **Assist** | Does safe internal work itself; anything going to another person waits for your approval. |
| **Autonomous** | Completes its whole job on its own. Sensitive or classified items still pause for you. |

Change a level in the **Guardrails** panel.

**A sensible starting point:** leave everyone at their default for the first week. Watch the
approval inbox. When you notice you're approving the same kind of thing every time without changing
it, raise that employee to Assist or Autonomous. When something surprises you, lower it.

Two things worth knowing before you raise anyone:

- **Autonomous is bounded by the role, not unlimited.** An Autonomous Riley still can't publish a
  website — that isn't Riley's job. Each employee can only ever do their own work.
- **Major can't be removed and stays Autonomous.** Everything routes through Major, so it has no
  meaningful "off".

---

## 5. Quality and knowledge

These two are what make the team improve over time rather than just repeat itself.

### Quinn — the check before anything leaves

Quinn reviews outbound drafts and returns one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| **pass** | No issues. Proceeds. |
| **pass-with-notes** | Minor issues, noted but not blocking. |
| **hold** | Must be fixed first. **Quinn can block a send.** |

Quinn also verifies claims against their cited sources, classifies content sensitivity
(public / internal / confidential / highly-confidential), flags automations that haven't run in over
25 hours as stale, and keeps a **risk register** on the dashboard.

A **"Review required"** badge on a card means Quinn hasn't returned a verdict yet.

### Casey — the memory

Casey keeps a local knowledge graph: people, projects, commitments, decisions, recurring meetings,
files, and your preferences. Nothing leaves your machine.

The practical payoff is that things stop being rediscovered. Ask Casey directly:

```
Who is Priya and what have we discussed?
```
```
What are my open commitments?
```
```
What did we decide about the Contoso renewal?
```

Casey surfaces **overdue commitments** in the Morning Brief and flags entries not updated in over
30 days as potentially stale.

In **Quality & knowledge**, a nonzero metric opens its detailed underlying records. A zero remains
an informational, non-clickable tile.

When a configured owned-account list identifies an incoming item as unowned, its card stays visible
with a concise lowest-priority reason. It is raised only for direct assignment, deadline, customer
or service impact, or safety/compliance/security evidence; account-neutral work is unchanged.

### Redaction — the block you should never override lightly

Before a draft leaves the team, it is scanned for identifier-shaped text: email addresses, phone
numbers and similar. If anything is found, the card shows a red **"Redaction required"** badge and
the item is blocked until the redaction is applied. Once it is, the badge becomes **"Redacted"**
and the *redaction pending* count in the Quality panel drops back to zero.

Read this honestly: the scan matches **known patterns**. It is a floor, not a certification. It
will not make content HIPAA- or GDPR-safe, and it will miss sensitive information that does not
look like an identifier — a salary figure, an unannounced deal, someone's health situation
described in prose. It catches the obvious cases so your attention is free for the rest. It is not
a substitute for reading the draft.

### Extras on a card

Cards can carry small grey chips showing what else the team produced alongside the document:

| Chip | Meaning |
| --- | --- |
| **Talk track** | Per-slide timing, transitions and pause cues for a deck. |
| **Conference pack** | Title options, abstract, objectives and a bio scaffold for a speaking submission. |
| **Chart spec** | A chart schema generated from the job's data. |
| **Flow doc** | A plain-language summary of a Power Automate flow. |

A conference pack arrives as a **scaffold with `[bracketed]` gaps** wherever it would otherwise
have had to invent something about you — a credential, a title, a claim. That is deliberate. Fill
those in before submitting anything; never send a pack with brackets still in it.

---

## 6. Approvals

The **Approval inbox** is the only panel that needs you. Common card types:

| Card | What it's asking |
| --- | --- |
| **email** | A drafted reply is ready to send |
| **teams** | A Teams message is ready to send |
| **meeting-prep** | A prep brief is ready to review |
| **commitment** | Something you committed to, needing confirmation or action |
| **blocked-work** | An employee is stuck and needs a decision from you |
| **stale-thread** | A thread with a commitment or deadline has gone quiet |

Every card carries a one-line **"why this matters today"** explanation. If you can't tell why
something is in front of you, that line is the answer.

When you approve, reject, acknowledge, or defer an email or message, the app remembers that handled
item locally so the same message does not return as a new card. Email threads remain muted across
sender changes. A thread reopens only when a newer message also brings a new ask, decision request,
or changed amount, date, or owner. Teams chats remain message-by-message, so handling one message
never hides later messages from the same person.

Open **Manage muted** below the Approval inbox to see each decision, when it was handled, when the
mute expires, and why it is still muted. **Restore** returns one item immediately; **Restore all**
returns every currently muted item.

**Blocked work is the one to watch.** If an employee is blocked for more than 30 minutes it
escalates automatically — but a blocked card sitting unanswered means work has genuinely stopped.

Each job also has a safety boundary: no more than three broad sweeps and five escalated reasoning or
review passes. If a job reaches a boundary, it is marked blocked instead of continuing in a loop.
Read the blocker and activity log before retrying. Narrow or split the request, add missing context,
then ask Major for a focused follow-up job. Repeatedly pressing **Attention Major** without changing
the request will not fix the underlying blocker.

---

## 7. Your data

Everything runs on `127.0.0.1` and stores to a local SQLite database on your own machine. Nothing is
sent anywhere except through the Microsoft 365 tools Scout already has, acting as you.

In the **Your data** panel:

- **Export all data** — everything, as JSON, yours to keep.
- **Reset** — wipes the database and starts clean. Irreversible.
- **Local token** — see below.

### The local token (optional hardening)

The app binds to `127.0.0.1` only, so it's never reachable from another machine. Auth is therefore
**off by default**. Turning it on additionally stops *other local software* on your PC from reading
your data.

To enable it, add `"requireLocalToken": true` to `app\config.json`, restart the app, then paste the
printed token into the dashboard's token field. Full steps are in Step 9 of
[INSTALL-WITH-SCOUT.md](../INSTALL-WITH-SCOUT.md).

Worth doing if you share the machine or run software you don't fully trust.

---

## 8. Shaping the team

### Add an employee

Use the **Add an employee** panel on the dashboard. Give them a name, a lane, and a trust level.

### Optional template employees

Two extra employees ship as templates but are **not installed by default**:

| Template | What they do |
| --- | --- |
| **Atlas** | Account and customer intelligence: dossiers, renewal tracking, stakeholder maps |
| **Piper** | Automation and workflow building: validates prompts, detects drift, suggests automations |

To add one, copy `skills\atlas-template` (or `piper-template`) into your Scout m-skills folder,
rename it, and run `/daily-flow-setup`.

### Remove an employee

Any employee except **Major** can be removed from the roster.

---

## 9. When something looks wrong

| What you see | What it usually is | What to do |
| --- | --- | --- |
| Dashboard won't open | App isn't running | Run `app\start-app.ps1` in your install folder |
| Board hasn't changed all day | An automation is paused | Check Scout's automations list; all four should be enabled |
| Empty approval inbox | Usually nothing is wrong | Check the activity log for recent entries — if they're there, the team simply had nothing to raise |
| A draft looks wrong | It's a draft — that's the point | Edit or reject it. Rejections shape future drafts |
| "Review required" stuck on a card | Quinn hasn't finished | Wait for the next pulse; if it persists, check the risk register |
| Job blocked after repeated sweeps or reviews | The job reached a safety boundary | Read the blocker, narrow or split the request, add missing context, and ask Major for a focused follow-up |
| Employee doing too much | Trust level too high | Lower it in Guardrails |
| Employee doing too little | Trust level too low, or not their lane | Raise it, or ask Major who owns that work |

For install-time problems, use the troubleshooting table in
[INSTALL-WITH-SCOUT.md](../INSTALL-WITH-SCOUT.md) — it maps exact error text to exact fixes.

---

## 10. Getting the most out of it

**Let it watch for a week before you tune anything.** The defaults are deliberately cautious. You'll
learn more from a week of watching the approval inbox than from guessing at settings on day one.

**Reject things.** A rejected draft is information. Approving something you'd have written
differently teaches the team that it got it right.

**Ask Casey before you go looking.** Most "where did we land on this?" questions are already
answered in the knowledge graph.

**Trust Quinn's holds.** A hold means a claim didn't check out against its source. That's the
system working.

**Check the roster stays at ten.** If the dashboard shows fewer, something didn't install cleanly —
see Step 8 of the install runbook.

---

## Related documents

| Document | What's in it |
| --- | --- |
| [INSTALL-WITH-SCOUT.md](../INSTALL-WITH-SCOUT.md) | Numbered install runbook with verification and troubleshooting |
| [docs/API.md](API.md) | Every HTTP endpoint, for scripting against the app |
| [docs/ARCHITECTURE.md](ARCHITECTURE.md) | How it's built: components, data flow, schema, security model |
| [README.md](../README.md) | Overview, roster, what you get |
## Action Items / Watch list

Tell Major to "watch this", "watch follow ups", or "when you see X, remind me to review Y." Direct
watches record the condition and proposed action. For a response that needs interpretation, ask
Major to watch for new detail and investigate what it means for the original item; that follow-up
records the evaluation and a proposed next step.

The dashboard list lets you view details and source context, complete, dismiss, or explicitly
remove an item. Remove is persisted rather than erasing provenance. A proposed action is never
performed automatically: triggering or evaluating a watch only updates the local list.
