# Install the Dream Team — runbook

A finite, numbered procedure. Every step has a command to run and an observable result that tells
you whether it worked. When Step 8 passes, **stop** — the install is done.

This runbook works for both audiences:

* **A person installing it by hand.** Follow steps 1 → 8 in order.
* **Microsoft Scout installing it for someone.** Same steps, plus the agent notes marked
  **[Scout]**, plus the guardrails in section 11. The guardrails are not optional.

---

## Step 0 — Prerequisites

Check all four before you touch anything. If any check fails, fix it here; do not continue and
hope.

| # | Requirement | How to check | Expected |
| --- | --- | --- | --- |
| 0.1 | Windows 10 or 11 | `(Get-CimInstance Win32_OperatingSystem).Caption` | Contains "Windows 10" or "Windows 11" |
| 0.2 | Microsoft Scout installed and signed in to Microsoft 365 | Open Scout; confirm it responds and shows the signed-in account | Scout opens, account is the user's own |
| 0.3 | Python 3.9 or newer, and **not** the Microsoft Store stub | `python --version` | `Python 3.9.x` or higher. If it prints nothing and opens the Store, that's the stub — see 0.3a |
| 0.4 | Permission to run scripts and write files on this machine | — | It's the user's own machine and they've agreed |

**0.3a — if Python is missing or is the Store stub.** The installer will try winget itself, but
you can do it up front:

```powershell
winget install --id Python.Python.3.13 --source winget
```

Then close and reopen PowerShell so `PATH` refreshes, and re-run `python --version`.

> The Dream Team is **Windows only** today. On macOS or Linux, stop here.

> **[Scout]** Prefer to run this install on Claude Opus 5 — it follows the procedure most
> reliably. If Opus 5 isn't available, use the best model on the machine and continue.

> **Permissions, before you start.** This install needs ordinary PowerShell, file access inside
> your own folders, and HTTP to `127.0.0.1`. It **never** needs unsafe browser code execution,
> Administrator elevation, or any write to `C:\Windows`, `C:\Windows\System32`, or
> `Program Files`. If something asks for those, deny it and see section 11 — the full allow/deny
> lists are in **11b** and **11c**.

---

## Step 1 — Get the package

Use the first of these that applies.

**1a. You already have it extracted.** A folder containing `install.ps1`, `app\`, `skills\`, and
`automations\`. Use it. This is the *package folder*.

**1b. Download the latest release.**

```powershell
$zip = "$env:TEMP\dream-team.zip"
$dst = "$env:TEMP\dream-team"
Invoke-WebRequest -Uri (
  (Invoke-RestMethod 'https://api.github.com/repos/TC-Copilot/dream-team-core/releases/latest').assets[0].browser_download_url
) -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $dst -Force
```

The package folder is `$dst` (or the single folder inside it).

**1c. Clone with git.**

```powershell
git clone https://github.com/TC-Copilot/dream-team-core "$env:TEMP\dream-team"
```

> **This repository is public**, so 1b and 1c work without signing in to GitHub. If you are handing
> this to someone who cannot use git, use 1a and give them the ZIP built by `package-share.ps1`.

> Never run anything from inside a ZIP preview window. Extract fully first.

### Verify Step 1

```powershell
cd <package folder>
Test-Path install.ps1, app\app.py, skills\daily-flow-team, skills\daily-flow-setup
```

**Expected:** four lines of `True`. If any is `False`, you have the wrong folder.

---

## Step 2 — Run the installer

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto
```

Useful switches:

| Switch | Effect |
| --- | --- |
| `-Auto` | Full hands-off install: writes config, starts the app, opens the dashboard. |
| `-AgentInline` | Suppress the "restart Scout and paste a command" closing message. **[Scout]** use this — you finish setup in-chat instead. |
| `-NoBrowser` | Don't open the dashboard in a browser. |
| `-InstallDir <path>` | Install somewhere other than `%USERPROFILE%\Daily Flow Team`. |
| `-BasePort <n>` | Start looking for a free port at `<n>` instead of 8787. |

> **[Scout]** run `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto -AgentInline`.
> On an upgrade, this command safely stops only the verified Daily Flow Python listener on the
> existing configured port, waits for release, starts the replacement, and requires `/api/health`
> to report the package version. Never preempt it with a broad Python process kill.

### Verify Step 2

The installer ends with an **Install summary** block and **exit code 0**:

```
--- Install summary ---
  Action:          fresh install of v4.5.19
  Core contract:   schema 1, v1.0.0
  Version report:  C:\Users\<you>\Daily Flow Team\app\.version-report.json
  Install folder:  C:\Users\<you>\Daily Flow Team
  Skills:          daily-flow-setup, daily-flow-team into 2 Scout skills folder(s)
  Dashboard:       http://127.0.0.1:8787/
  Documents:       C:\Users\<you>\OneDrive\Scout\Daily Flow
  Model:           claude-opus-5 (default; confirmed at setup)
  Automations:     installed and switched on during /daily-flow-setup
  Install log:     C:\Users\<you>\Daily Flow Team\install.log
-----------------------
```

```powershell
$LASTEXITCODE   # must be 0
```

**If the exit code is 1**, the installer has already printed the exact Python error that stopped
the app. Read it, then go to the troubleshooting table in Step 10. Everything the installer
printed is also saved to `<install folder>\install.log`.

Note the **port** and the **install folder** from the summary — later steps need them.

---

## Step 3 — Verify the app is healthy

```powershell
Invoke-WebRequest http://127.0.0.1:8787/api/health
```

The response reports `.version` plus `.versions`. Confirm that `.version` matches the package you
installed and that `.versions.core.buildRevision` is present.

(Substitute your port if it isn't 8787.)

**Expected:** `StatusCode : 200`, and a body like:

```json
{ "ok": true, "version": "4.5.19", "buildRevision": "20260818.4", "serverTime": "2026-08-18T15:40:26Z" }
```

A one-liner that either prints the version or throws:

```powershell
(Invoke-RestMethod http://127.0.0.1:8787/api/health).version
```

**If this fails**, go to Step 10. Do not continue.

---

## Step 4 — Verify the skills are installed

```powershell
Get-ChildItem "$env:USERPROFILE\.scout\m-skills","$env:USERPROFILE\.copilot\m-skills" `
  -Filter 'daily-flow-*' -Directory -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
```

**Expected:** `daily-flow-team` and `daily-flow-setup` in at least one skills folder.

Scout looks in `~\.copilot\m-skills`, `~\.scout\m-skills`, `~\.copilot-cloud\m-skills`, and
`~\.copilot-dev\m-skills`. The installer writes to every one that exists.

---

## Step 5 — Open Scout and run `/daily-flow-setup`

1. **Restart Scout.** Slash commands are registered at launch, so a running session won't see
   `/daily-flow-setup` until it restarts.
2. In a new chat, type:

   ```
   /daily-flow-setup
   ```

3. Answer the wizard's questions: confirm your sign-in, pick your reasoning model, and approve the
   automations.

**Expected:** the wizard confirms your sign-in, sets your reasoning model, creates the automations,
assigns routine checks and ordinary drafting to automatic routing, and starts a first sweep.

> **[Scout]** *Do not tell the user to restart Scout or type this command.* You are already Scout,
> with every tool the wizard needs. Instead read
> `<package folder>\skills\daily-flow-setup\SKILL.md` and carry out its steps yourself in this
> same chat. The installer prints a "restart Scout" message aimed at people running it by hand
> with no agent driving — do not relay it. Restarting later is optional and only registers the
> slash commands for future use.

> **[Scout] `/daily-flow-setup` self-refreshes an existing install.** Its Step 0.5 runs the installed
> release bootstrap before configuration, including when the stable semantic version has not
> changed. The user only needs to issue the command; do not ask them to download or reinstall.
> Never report success unless `/api/health` confirms both the stable version and separate build
> revision from the downloaded package.

Your local data, preferences, and configuration are preserved during refresh. If setup cannot prove
the downloaded assets, the running app's identity, or the restarted build, it stops without claiming
success. Use the exact reported condition and the install log for the next attempt.

---

## Step 6 — Verify the automations

The package ships **4 automations, all 4 enabled by default**:

| Automation | Schedule | Status |
| --- | --- | --- |
| Daily Flow Morning Brief | every weekday at 7am | required |
| Daily Flow Evening Wrap-up | every weekday at 5pm | required |
| Daily Flow Continuous Work Pulse | every hour | required |
| Daily Flow Attention Major Trigger | every 5 minutes | required |

In Scout, open your automations list.

**Expected:** all four present, and all four showing **enabled**.

If one is missing or switched off, re-run `/daily-flow-setup` and choose to recreate the
automations. A paused automation doesn't fail loudly — it just silently stops doing its part, so
this check matters.

> **[Scout]** verify with `m_list_automations` and confirm all four report `enabled: true`.

---

## Step 7 — Run the first sweep

The setup wizard normally kicks this off for you. To trigger one yourself, open the dashboard and
press **Attention Major**, or:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8787/api/attention-major `
  -ContentType 'application/json' -Body '{"source":"dashboard"}'
```

The first sweep takes roughly **5 to 10 minutes**. The board fills in progressively — you don't
need to refresh, and you don't need to keep Scout in the foreground.

---

## Step 8 — Confirm the dashboard fills — then STOP

Open `http://127.0.0.1:8787/` (your port).

**Expected — all of these:**

1. The employee roster shows your team (10 by default).
2. The activity log has entries from the sweep.
3. Either real items appear in the approvals/signals panels, **or** the board honestly shows the
   "all caught up" state after a completed sweep.

Check it from the command line if you prefer:

```powershell
$s = Invoke-RestMethod http://127.0.0.1:8787/api/state
$s.employees.Count            # expect 10
$s.events.Count               # expect > 0 after a sweep
(Invoke-RestMethod http://127.0.0.1:8787/api/gate).hasWork
```

### Stop conditions

**The install is complete when all of these are true. When they are, stop.**

- [ ] `GET /api/health` returns **200** with the expected version
- [ ] `daily-flow-team` and `daily-flow-setup` are in a Scout skills folder
- [ ] All **4** automations exist and are **enabled**
- [ ] A first sweep has completed and the dashboard shows real data or an honest "all caught up"

Do not keep tuning, re-running, or "improving" the install past this point.

**Next:** hand the user [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md) — it covers talking to Major,
trust levels, the approval inbox, and the daily rhythm. Everything below this line is optional.

---

## Step 9 — Optional hardening: turn on the local token

The app binds to `127.0.0.1` only, so it is never reachable from another machine. Auth is
therefore **off by default** and every existing install keeps working unchanged. Turning it on
additionally stops *other local software* from reading your data.

1. Edit `<install folder>\app\config.json` and add:

   ```json
   "requireLocalToken": true
   ```

2. Restart the app:

   ```powershell
   & "<install folder>\app\stop-app.ps1"
   & "<install folder>\app\start-app.ps1"
   ```

3. The console prints the token once: `[auth] Local token: <token>`. It is also stored at
   `<install folder>\app\.local-token`.
4. Open the dashboard, expand **Your data**, paste the token into the token field, and save. The
   dashboard sends it on every call from then on.

Full details are in [`docs/API.md`](docs/API.md).

---

## Step 10 — Troubleshooting

| Symptom | Cause | Exact fix |
| --- | --- | --- |
| `python --version` prints nothing and the Microsoft Store opens | The Store stub, not real Python | `winget install --id Python.Python.3.13 --source winget`, reopen PowerShell, retry Step 2 |
| Installer: `Python 3.9+ was not found` | No Python on PATH | Install from <https://www.python.org/downloads/> with **"Add Python to PATH"** ticked, reopen PowerShell, retry Step 2 |
| Installer exits 1: `The app did not answer within 20 seconds` | The app crashed on start; the real traceback is printed right below the message | Read the printed traceback, and `<install folder>\app\app.err.log`. Fix the cause, then `& "<install folder>\app\start-app.ps1"` |
| `Invoke-WebRequest ... : Unable to connect to the remote server` on `/api/health` | The app isn't running | `& "<install folder>\app\start-app.ps1"`, wait 5s, retry Step 3 |
| The port is already taken by something else | Another app owns the port | `Get-NetTCPConnection -LocalPort 8787` to see who. Then either stop it, or set a different `"port"` in `app\config.json` and restart the app |
| Upgrade stops because ownership of the configured port cannot be proven | The listener is unrelated, unhealthy, or from an old install without enough identity evidence | Inspect the reported PID and `<install folder>\install.log`. Do **not** kill every Python process. Stop only the process you independently confirm is the old Daily Flow app, or move the unrelated service/change `app\config.json`, then retry |
| `... cannot be loaded because running scripts is disabled` | Execution policy | Run with `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Auto` (as shown). If a machine policy still blocks it, use the manual fallback below |
| A tool asks to run unsafe browser code, elevate to Admin, or write to `C:\Windows\System32` | Nothing in this install needs any of that | **Deny it.** See sections 11b and 11c. Denying does not break the install; if it appears to, report it as a bug rather than allowing the access |
| Scout doesn't list `/daily-flow-setup` | Skills are registered at launch | Restart Scout. **[Scout]**: expected, not a failure — read `SKILL.md` and continue in-chat |
| Dashboard loads but every panel is empty | No sweep has run yet | Run Step 7 and wait 5–10 minutes |
| Dashboard shows "an automation is switched off" | An automation was paused or deleted | Re-run `/daily-flow-setup` and recreate the automations (Step 6) |
| A job is blocked after repeated broad sweeps or deep review passes | The per-job safety limit stopped an unproductive loop | Read the blocked card and activity log. Narrow or split the request, add the missing context, then ask Major to create a focused follow-up job. Do not repeatedly press **Attention Major** for the unchanged request. |
| `403 local token required` on every call | `requireLocalToken` is on and the dashboard has no token | Paste the token from `app\.local-token` into the dashboard's **Your data** section (Step 9) |
| `403 cross-origin request rejected` | Calling the API from a page on another origin | Use `http://127.0.0.1:<port>/` or `http://localhost:<port>/`, or call without an `Origin` header |
| Export downloads a 0-byte file | Auth is on and the browser has no token | Save the token first (Step 9) |
| Install log needed for a bug report | — | `<install folder>\install.log` holds everything the installer printed |
| `/daily-flow-setup` could not confirm a refresh | Asset identity, digest, manifest, safe process ownership, install, or restarted identity verification failed closed | Read `<install folder>\install.log`, correct the reported condition, then issue `/daily-flow-setup` again. Do not manually reinstall; setup owns the download and refresh. |

### Manual fallback — only if `install.ps1` will not run at all

1. Copy `skills\daily-flow-team` and `skills\daily-flow-setup` into your Scout skills folder (the
   first of `~\.copilot\m-skills`, `~\.scout\m-skills`, `~\.copilot-cloud\m-skills`,
   `~\.copilot-dev\m-skills` that exists and holds your other skills).
2. Copy the `app` and `automations` folders to a stable location, e.g. `%USERPROFILE%\Dream Team`.
3. Ensure Python 3.9+ is available (`python --version`).
4. Start it: `python "<install folder>\app\app.py"`.
5. Resume at Step 3.

---

## 11. Guardrails — **[Scout]**, these are not optional

* **Do not attempt the whole install more than twice.** If it fails twice, stop and show the user
  exactly what you saw. Do not keep trying.
* **Retry any single step at most once.**
* **Never re-run a step that already reported success.**
* **Stop the moment the Step 8 stop conditions are met.** Do not keep changing or improving the
  install.
* **Ask before installing anything system-wide** (Python via winget) or changing a machine
  setting.
* **Only do what this runbook says.** If a situation isn't covered here, stop and ask the user
  rather than inventing a step.
* **Never delete or overwrite the user's existing skills or automations** without asking. If a
  same-named automation already exists, ask skip vs recreate.

### 11a. Never use unsafe browser code execution

**Do not call `playwright-browser_run_code_unsafe`, or any equivalent arbitrary-code-execution
browser tool, at any point during this install or setup.** Nothing in this runbook needs it.

Every step here is served by ordinary PowerShell, file reads and writes, and plain HTTP requests to
`127.0.0.1`. If you find yourself reaching for unsafe code execution, you have left the runbook —
stop and ask the user instead.

Opening the dashboard in a browser to *look* at it is fine. Executing arbitrary code through a
browser tool is not.

### 11b. Write only inside allowed paths

This install writes to a small, known set of locations. **Allowed:**

| Allowed | Example |
| --- | --- |
| The install folder | `<install folder>\...` |
| Subfolders of the user profile | `%USERPROFILE%\Documents\Daily Flow` |
| The temp folder | `%TEMP%\dream-team` |
| Scout data and skills folders | `~\.scout\`, `~\.copilot\`, `~\.copilot-cloud\`, `~\.copilot-dev\` |
| The user's Desktop (the shortcut only) | `<Desktop>\The Dream Team.lnk` |

**Denied — never write here, and never ask the user for permission to:**

| Denied | Why |
| --- | --- |
| `C:\Windows\*` | System directory |
| `C:\Windows\System32\*` | System directory. Nothing in this package belongs here |
| `C:\Program Files\*`, `C:\Program Files (x86)\*` | Machine-wide install locations |
| Startup folders (`...\Start Menu\Programs\Startup`) | The app is started by its own script, not by autorun |
| The registry, or any other system-wide setting | The install reads the timezone from the registry; it never writes to it |
| Any path outside the allowed table above | Not needed |

The one deliberate exception is **Python via winget** (Step 0.3a), which is a normal system-wide
package install — and the runbook already requires you to ask first.

> **Reading is not writing.** The desktop shortcut points at the stock
> `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`, and the timezone is read from the
> registry. Referencing and reading system paths is normal and fine. The rule above is about
> **writing** to them, which this package never does.

**If any tool or step appears to need access beyond the allowed list, stop and ask the user.** Do
not proceed, and do not look for a way around it. A tool asking for broad access during a
local-only install is a signal that something is wrong, not a box to click through.

### 11c. Permission prompts — what to allow

If Scout (or any tool it uses) prompts for permission during setup:

**Allow:**

* Running PowerShell and reading and writing files **inside the allowed paths** in 11b
* HTTP requests to `http://127.0.0.1:<port>` — the local app
* Reading your Microsoft 365 signals through Scout's own tools, once you're signed in
* Creating the four Dream Team automations in Scout
* Installing Python via winget, **if** it asked you first and Python is genuinely missing

**Deny:**

* Unsafe browser code execution (`playwright-browser_run_code_unsafe` or similar)
* Any write to `C:\Windows`, `C:\Windows\System32`, or `Program Files`
* Adding anything to a startup folder
* Writing registry keys or changing machine-wide settings
* Network access to anything other than `127.0.0.1`, GitHub (to fetch the package), and the
  Microsoft 365 endpoints Scout already uses
* Elevation to Administrator — **this install never needs it**

Denying any item on the second list does not break the install. If something genuinely stops
working after a denial, that's worth reporting as a bug, not working around.

---

## Uninstall

```powershell
& "<install folder>\app\stop-app.ps1"
Remove-Item -Recurse -Force "<install folder>"
Remove-Item -Recurse -Force "$env:USERPROFILE\.scout\m-skills\daily-flow-team",
                            "$env:USERPROFILE\.scout\m-skills\daily-flow-setup",
                            "$env:USERPROFILE\.copilot\m-skills\daily-flow-team",
                            "$env:USERPROFILE\.copilot\m-skills\daily-flow-setup" `
                            -ErrorAction SilentlyContinue
```

Then delete the four Dream Team automations in Scout. Your documents folder is left alone —
delete it yourself if you want it gone.

To take your data with you first, open the dashboard, expand **Your data**, and press
**Export all data**.
