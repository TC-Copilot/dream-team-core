let state = null;
let activeThreadId = "";
let pendingApprovalDecision = "";
let pendingApprovalIds = [];
let transientStatus = "";
let sweepRequestedAt = 0;
// Selection is tracked here (not just in the DOM) so it survives the periodic
// re-render driven by the SSE /api/events stream and the 15s poll. Without this,
// a checkbox could be wiped by a refresh a couple seconds after being clicked.
const selectedApprovals = new Set();
let approvalsRenderSig = "";
let runtimeInventory = null;

// "Hide company names" / "Hide person names" privacy toggles for Results and drafts prepared:
// two independent, client-side-only visual masks, never mutating state/API payloads. Both default
// off and persist per browser. Toggling either off immediately restores real names on the next
// render, since preview/title text is always recomputed from the raw job data, never cached masked.
const HIDE_COMPANY_NAMES_KEY = "df-hide-company-names";
const HIDE_PERSON_NAMES_KEY = "df-hide-person-names";
const COMPANY_ALIAS_METADATA_KEY = "df-company-alias-map-v2";
let hideCompanyNames = false;
let hidePersonNames = false;
try { hideCompanyNames = localStorage.getItem(HIDE_COMPANY_NAMES_KEY) === "1"; } catch (e) {}
try { hidePersonNames = localStorage.getItem(HIDE_PERSON_NAMES_KEY) === "1"; } catch (e) {}
// Confirmed name -> stable "Company N" / "Person N" alias, assigned once per name for the life of
// this page load and never reassigned, so a re-render (poll/SSE) can't renumber a name already seen.
let companyReplacementEntries = [];
const personAliasMap = new Map();
let nextPersonAliasNumber = 1;
let companyMaskReady = false;
let privacyObserver = null;
const rawPrivacyAttributes = new WeakMap();
const rawPrivacyText = new WeakMap();
const rawPrivacyControlValues = new WeakMap();
const rawPrivacyControlReadOnly = new WeakMap();

const $ = (id) => document.getElementById(id);

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  }[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(options.headers || {}) },
    ...options
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
  return data;
}

// --- Local token (P1-A) -----------------------------------------------------------------------
// The app only requires this when it was started with --auth. Storing it here means the dashboard
// keeps working unchanged in the default --no-auth mode: an empty token simply sends no header.
const TOKEN_KEY = "dailyflow_token";

function localToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function authHeaders() {
  const token = localToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function setLocalToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {}
}

function statusClass(status = "") {
  return `status ${(status || "queued").toLowerCase()}`;
}

function parseJson(value, fallback = "") {
  if (!value) return fallback;
  try { return JSON.parse(value); } catch { return fallback; }
}

function looksLikeOutlookItemId(value = "") {
  const text = String(value).trim();
  return text.length >= 60 &&
    /^(AAMk|AMk|AQMk)/.test(text) &&
    !/\s/.test(text) &&
    /^[A-Za-z0-9+/=_-]+$/.test(text);
}

function outlookDraftHref(itemId = "") {
  return `https://outlook.office.com/mail/deeplink/compose/${encodeURIComponent(String(itemId).trim())}`;
}

function normalizeLink(value) {
  const link = typeof value === "string" ? parseJson(value, value) : value;
  if (!link) return null;
  if (typeof link === "string") {
    if (!link.trim()) return null;
    const href = link.trim();
    return looksLikeOutlookItemId(href)
      ? { label: "Open Outlook draft", href: outlookDraftHref(href), draftId: href }
      : { label: "Open result", href };
  }
  const href = link.href || link.url || link.path || "";
  if (looksLikeOutlookItemId(href)) {
    return { label: link.label || link.title || "Open Outlook draft", href: outlookDraftHref(href), draftId: href };
  }
  const label = link.label || link.title || (String(href).includes("outlook.office.com/mail") ? "Open Outlook draft" : "Open result");
  const oneDrivePath = link.oneDrivePath || "";
  return href ? { label, href, oneDrivePath, draftId: link.draftId || "" } : { label, href: "", oneDrivePath };
}

function linkHref(href = "") {
  if (!href) return "";
  if (href.startsWith("/")) return href;
  if (href.startsWith("http") || href.startsWith("file:")) return href;
  if (/^[A-Za-z]:\\/.test(href)) {
    return `file:///${encodeURI(href.replace(/\\/g, "/"))}`;
  }
  return "";
}

function renderLink(value) {
  const link = normalizeLink(value);
  if (!link) return "";
  const href = linkHref(link.href);
  return href
    ? `<div class="preview"><strong>Where it is:</strong> <a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(link.label)}</a></div>`
    : `<div class="preview"><strong>Where it is:</strong> ${escapeHtml(link.href || link.label)}</div>`;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "";
}

// Converts a raw ISO-8601 instant into a human-readable string in the *browser's* local
// timezone/locale (no explicit timeZone override), so it always matches what the user's
// system clock shows — regardless of what timezone the backend machine happens to be in.
// Falls back to returning the original value unchanged if it doesn't parse as a valid date.
function friendlyLocal(value) {
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

// Replace raw ISO-8601 timestamps embedded in generated preview/summary text with a
// human-readable rendering in the browser's own local timezone. Only matches instants that
// carry an explicit timezone (trailing Z or +HH:MM/-HH:MM offset) so date-only values
// (e.g. "2026-08-11") and timezone-less/naive timestamps are left untouched rather than
// having a timezone guessed for them.
function humanizeTimes(text) {
  if (!text) return text;
  const iso = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b/g;
  return text.replace(iso, (m) => friendlyLocal(m));
}

function formatApprovalPreview(text) {
  const safe = humanizeTimes(escapeHtml(text || ""));
  return safe.replace(/(^|\n)(Recommendation:)/g, '$1<strong class="recommendation-label">$2</strong>');
}

function dateKey(value) {
  return value ? new Date(value).toLocaleDateString("en-CA") : "";
}

function currentDashboardDate() {
  return state?.serverTime ? dateKey(state.serverTime) : new Date().toLocaleDateString("en-CA");
}

function activeJobs() {
  return activeWorkJobs();
}

function completedJobs() {
  return state.jobs.filter((job) => ["completed", "done"].includes(job.status));
}

function resultEligibleJobs() {
  // Broader than completedJobs(): a document-backed draft or artifact-creation job that ended up
  // 'blocked' (source document not found, attachment failed, or no file/prompt reported) still
  // needs to show up in Results and drafts prepared as a visibly blocked entry -- not vanish --
  // and a completed copilot_prompt_fallback artifact job never carries a result_link_json (there
  // is no file, only a build prompt to paste into Word/PowerPoint Copilot), so it would otherwise
  // be silently dropped by a completed+link-required filter.
  return state.jobs.filter((job) => ["completed", "done", "blocked"].includes(job.status));
}

// True when a job with no result link should still surface as a visible entry (typically blocked,
// or completed-via-Copilot-prompt-fallback) rather than being silently absent because it "is not
// itself an outbound message".
function visibleWithoutLink(job) {
  if (job.document_backed_draft && job.document_status) return true;
  if (job.artifact_request && job.artifact_creation_mode) return true;
  return false;
}

// ---------------------------------------------------------------------------------------------
// PRIVACY-MASKING VEIL GUARANTEE (company/person name hiding)
//
// The mask functions below are a **client-side-only, purely presentational display veil**. They
// take already-loaded state and return a *new string* for rendering; they never mutate `state`,
// never mutate a `job` object, and their output is never read back by any code that talks to the
// backend. In particular:
//   - Masked text is only ever assigned to local render variables (e.g. `previewText`,
//     `titleText`) built fresh on every render call, and is only used inside innerHTML strings.
//   - Every outbound call (`api()`, `fetch()`, `sendPreparedDraft()`, `data-send-draft` attributes,
//     approval decisions, etc.) reads identifiers straight off the original `job`/`state` objects
//     (e.g. `job.id`) -- never off a masked/aliased string -- so a masked/aliased value can never
//     be persisted to SQLite, sent as an API payload, included in a job instruction, or reach any
//     draft, attachment, email, or Teams message.
//   - Nothing here alters evidence, source documents, drafts, or any other backend-held data --
//     toggling either mask on/off changes only what this browser tab currently displays.
// See smoke-test.ps1 for a static check that guards this invariant (send controls keyed off raw
// job.id, never off masked/alias variables).
// ---------------------------------------------------------------------------------------------

// Company/account names come exclusively from the user's configured owned-account list. This
// deliberately does not scan arbitrary dashboard text or guess capitalized phrases.
function knownCompanyNames() {
  return (state?.ownedAccounts?.names || [])
    .map((name) => String(name || "").normalize("NFKC").replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

// Confirmed person names for the "Hide person names" mask. Sourced only from the impact ledger's
// own "people" field, which is populated exclusively from explicit peopleWorkedWith tags set when
// work is reported to the work ledger -- never inferred from capitalized words in free text.
// Dream Team employee names are excluded here even if a tag happens to collide with one, so the
// person mask can never touch an employee's own name/role label.
function knownPersonNames() {
  const employeeNames = new Set((state?.employees || []).map((e) => String(e.name || "").trim().toLowerCase()).filter(Boolean));
  const names = new Set();
  for (const item of (state?.impactLedger?.highlights || [])) {
    for (const raw of (Array.isArray(item.people) ? item.people : [])) {
      const name = String(raw || "").trim();
      if (name && !employeeNames.has(name.toLowerCase())) names.add(name);
    }
  }
  return Array.from(names);
}

// Assigns each confirmed name a stable "Company N" / "Person N" alias the first time it is seen.
// Once assigned, a name keeps its number for the rest of this page load -- later polls/SSE
// updates only ever add new names, they never renumber one already shown to the user.
function loadCompanyAliasMetadata() {
  try {
    const parsed = JSON.parse(localStorage.getItem(COMPANY_ALIAS_METADATA_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function buildCompanyReplacementMap() {
  const names = knownCompanyNames();
  const metadata = DailyFlowPrivacy.buildCompanyAliasMetadata(names, loadCompanyAliasMetadata());
  try { localStorage.setItem(COMPANY_ALIAS_METADATA_KEY, JSON.stringify(metadata)); } catch (e) {}
  companyReplacementEntries = DailyFlowPrivacy.buildCompanyReplacementEntries(names, metadata);
  companyMaskReady = true;
}

function ensurePersonAliases() {
  for (const name of knownPersonNames()) {
    const key = name.toLowerCase();
    if (!personAliasMap.has(key)) {
      personAliasMap.set(key, `Person ${nextPersonAliasNumber++}`);
    }
  }
}

function maskWithAliasMap(text, enabled, aliasMap) {
  if (!enabled || !text) return text;
  let out = String(text);
  // Longest names first so e.g. "Contoso Corp" is masked as a whole instead of leaving "Corp"
  // exposed after a shorter "Contoso" match already ran.
  const keys = Array.from(aliasMap.keys()).sort((a, b) => b.length - a.length);
  for (const key of keys) {
    const alias = aliasMap.get(key);
    const pattern = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    out = out.replace(new RegExp(pattern, "gi"), alias);
  }
  return out;
}

// Masks only confirmed company/account names (see knownCompanyNames) anywhere they occur in a
// piece of plain display text. No-op when the preference is off or there is nothing to mask.
function maskCompanyNames(text) {
  if (!hideCompanyNames || !text) return text;
  return DailyFlowPrivacy.maskWithEntries(text, companyReplacementEntries);
}

// Masks only confirmed person names (see knownPersonNames) anywhere they occur in a piece of
// plain display text. No-op when the preference is off or there is nothing to mask.
function maskPersonNames(text) {
  return maskWithAliasMap(text, hidePersonNames, personAliasMap);
}

// Applies both independent masks in sequence. Callers must run this on raw text before
// escapeHtml(). The returned string is for display only: never store it
// back onto `job`/`state`, never pass it to api()/fetch(), and never use it to key a
// data-send-draft/decision id -- always use the original job.id for those.
function maskPrivacyText(text) {
  return maskPersonNames(maskCompanyNames(text));
}

const PRIVACY_ATTRIBUTE_NAMES = new Set([
  "title", "alt", "placeholder", "href",
  "aria-label", "aria-description", "aria-valuetext", "aria-placeholder"
]);
const STRUCTURAL_DATA_ATTRIBUTES = new Set([
  "data-theme", "data-theme-set", "data-collapsible", "data-trust", "data-enabled",
  "data-group", "data-group-key", "data-group-section", "data-group-selectall",
  "data-group-action", "data-action", "data-decision", "data-clear-all"
]);

function rememberRawPrivacyAttribute(element, name, value) {
  let attributes = rawPrivacyAttributes.get(element);
  if (!attributes) {
    attributes = new Map();
    rawPrivacyAttributes.set(element, attributes);
  }
  if (!attributes.has(name)) attributes.set(name, value);
}

function privacyAttribute(element, name) {
  return rawPrivacyAttributes.get(element)?.get(name) ?? element?.getAttribute(name);
}

function privacyControlValue(control) {
  return rawPrivacyControlValues.get(control) ?? control?.value ?? "";
}

function clearPrivacyControlValue(control) {
  if (!control) return;
  if (hideCompanyNames && rawPrivacyControlValues.has(control)) {
    rawPrivacyControlValues.set(control, "");
  } else {
    rawPrivacyControlValues.delete(control);
  }
  control.value = "";
}

function maskCompanyElement(element) {
  if (!(element instanceof Element)) return;
  for (const attr of Array.from(element.attributes)) {
    const shouldMask = PRIVACY_ATTRIBUTE_NAMES.has(attr.name) ||
      (attr.name.startsWith("data-") && !STRUCTURAL_DATA_ATTRIBUTES.has(attr.name));
    if (shouldMask) {
      const masked = maskCompanyNames(attr.value);
      if (masked !== attr.value) {
        rememberRawPrivacyAttribute(element, attr.name, attr.value);
        element.setAttribute(attr.name, masked);
      }
    }
  }
  if (element.id === "ownedAccountsInput") {
    element.value = maskCompanyNames(element.value);
    element.disabled = true;
  } else if (
    (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) &&
    !["password", "file", "checkbox", "radio", "hidden", "button", "submit"].includes(element.type)
  ) {
    if (!rawPrivacyControlValues.has(element)) {
      rawPrivacyControlValues.set(element, element.value);
      rawPrivacyControlReadOnly.set(element, element.readOnly);
    }
    element.value = maskCompanyNames(rawPrivacyControlValues.get(element));
    element.readOnly = true;
  }
}

function scrubCompanyNamesFromDom(root = document.documentElement) {
  if (!hideCompanyNames || !companyMaskReady) return;
  document.title = maskCompanyNames(document.title);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  for (const node of textNodes) {
    const masked = maskCompanyNames(node.nodeValue);
    if (masked !== node.nodeValue) {
      if (!rawPrivacyText.has(node)) rawPrivacyText.set(node, node.nodeValue);
      node.nodeValue = masked;
    }
  }
  if (root instanceof Element) maskCompanyElement(root);
  for (const element of root.querySelectorAll?.("*") || []) maskCompanyElement(element);
}

function beginCompanyMaskPreparation() {
  document.documentElement.classList.add("privacy-mask-pending");
  document.documentElement.setAttribute("aria-busy", "true");
  const status = $("companyMaskStatus");
  if (status) status.textContent = "Working...";
  const saveButton = $("saveOwnedAccountsBtn");
  if (saveButton) saveButton.disabled = true;
}

function finishCompanyMaskPreparation() {
  const count = knownCompanyNames().length;
  const status = $("companyMaskStatus");
  if (status) {
    status.textContent = count
      ? `${count} configured company name${count === 1 ? "" : "s"} masked for this browser.`
      : "No owned accounts are configured, so there are no company names to mask.";
  }
  document.documentElement.classList.remove("privacy-mask-pending");
  document.documentElement.removeAttribute("aria-busy");
}

function observeCompanyPrivacy() {
  if (privacyObserver) return;
  privacyObserver = new MutationObserver((records) => {
    if (!hideCompanyNames || !companyMaskReady) return;
    privacyObserver.disconnect();
    for (const record of records) {
      if (record.type === "characterData") {
        record.target.nodeValue = maskCompanyNames(record.target.nodeValue);
      } else if (record.type === "attributes") {
        maskCompanyElement(record.target);
      } else {
        for (const node of record.addedNodes) {
          if (node.nodeType === Node.TEXT_NODE) node.nodeValue = maskCompanyNames(node.nodeValue);
          else if (node.nodeType === Node.ELEMENT_NODE) scrubCompanyNamesFromDom(node);
        }
      }
    }
    privacyObserver.observe(document.documentElement, { attributes: true, childList: true, characterData: true, subtree: true });
  });
  privacyObserver.observe(document.documentElement, { attributes: true, childList: true, characterData: true, subtree: true });
}

async function prepareCompanyMask({ rerender = true } = {}) {
  beginCompanyMaskPreparation();
  await new Promise((resolve) => requestAnimationFrame(resolve));
  buildCompanyReplacementMap();
  if (rerender) render();
  scrubCompanyNamesFromDom();
  observeCompanyPrivacy();
  finishCompanyMaskPreparation();
}

function restoreUnmaskedDashboard() {
  companyMaskReady = false;
  if (privacyObserver) {
    privacyObserver.disconnect();
    privacyObserver = null;
  }
  document.documentElement.classList.remove("privacy-mask-pending");
  document.documentElement.removeAttribute("aria-busy");
  const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const original = rawPrivacyText.get(walker.currentNode);
    if (original !== undefined) walker.currentNode.nodeValue = original;
  }
  for (const element of document.querySelectorAll("*")) {
    for (const [name, value] of rawPrivacyAttributes.get(element) || []) {
      element.setAttribute(name, value);
    }
    if (rawPrivacyControlValues.has(element)) {
      element.value = rawPrivacyControlValues.get(element);
      element.readOnly = rawPrivacyControlReadOnly.get(element) || false;
      rawPrivacyControlValues.delete(element);
      rawPrivacyControlReadOnly.delete(element);
    }
  }
  document.title = "The Dream Team";
  ownedAccountsLoadedInto = null;
  approvalsRenderSig = "";
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  render();
  renderRuntimeInventory();
  const input = $("ownedAccountsInput");
  if (input) input.disabled = false;
  const saveButton = $("saveOwnedAccountsBtn");
  if (saveButton) saveButton.disabled = false;
  const status = $("companyMaskStatus");
  if (status) status.textContent = "Company names are visible.";
}

function linkedDocuments(date = currentDashboardDate()) {
  const seen = new Set();
  const docs = [];
  for (const job of resultEligibleJobs()) {
    if (date && dateKey(job.completed_at || job.updated_at || job.created_at) !== date) continue;
    const link = normalizeLink(job.result_link_json);
    const hasLink = !!link?.href;
    if (!hasLink && !visibleWithoutLink(job)) continue;
    // Fall back to the job id as the dedupe key when there is no link, so two different
    // link-less blocked/prompt-only jobs never collapse into a single displayed entry.
    const key = hasLink ? link.href.toLowerCase() : `job:${job.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    docs.push({ job, link: link || { label: "", href: "" } });
  }
  return docs;
}

function fileNameFromLink(link = {}) {
  const source = link.oneDrivePath || decodeURIComponent(String(link.href || "").split("?")[0]);
  const parts = String(source).replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || "";
}

function documentKind(fileName = "", label = "") {
  const text = `${fileName} ${label}`.toLowerCase();
  if (text.includes("outlook") || text.includes("email")) return "Email draft";
  if (text.endsWith(".pptx") || text.includes("powerpoint") || text.includes("deck")) return "PowerPoint deck";
  if (text.endsWith(".docx") || text.includes("word")) return "Word document";
  if (text.endsWith(".xlsx") || text.includes("spreadsheet") || text.includes("excel")) return "Spreadsheet";
  if (text.includes("teams")) return "Teams draft";
  return "Prepared item";
}

function humanizeFileName(fileName = "") {
  const withoutExtension = fileName.replace(/\.[^.]+$/, "");
  return withoutExtension
    .replace(/\b20\d{2}-\d{2}-\d{2}\b/g, "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanResultSummary(summary = "") {
  return String(summary)
    .replace(/[A-Za-z]:\\[^\n\r]+/g, "")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/\s*(Published to Daily Flow Results:|The deck is published to Daily Flow Results:)\s*$/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function isOperationalResultSummary(summary = "") {
  const text = String(summary).toLowerCase();
  return [
    "verified real outlook draft",
    "exists in drafts",
    "placeholder id",
    "graph draft id",
    "inbox cleanup",
    "source email",
    "source message",
    "deleted the exact",
    "not sent",
    "report completed",
  ].some((marker) => text.includes(marker));
}

function contentRequestFromTitle(title = "") {
  let text = String(title)
    .replace(/^\[[^\]]+\]\s*/g, "")
    .replace(/^(re|fw|fwd):\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
  const afterColon = text.split(":").slice(1).join(":").trim();
  if (/\b(draft|write|create|prepare|explain|summarize|review|compare|build)\b/i.test(afterColon)) {
    text = afterColon;
  }
  text = text
    .replace(/^draft\s+/i, "")
    .replace(/^create\s+/i, "")
    .replace(/^write\s+/i, "")
    .replace(/^prepare\s+/i, "")
    .replace(/\.$/, "")
    .trim();
  return text || "a response for review";
}

function draftContentPreview(job, label = "") {
  const lowerLabel = String(label).toLowerCase();
  const kind = lowerLabel.includes("teams") ? "Teams message draft" : "Email draft";
  const request = contentRequestFromTitle(job.title);
  return `${kind} prepared for review: ${request}.`;
}

// Preview text for a result entry with no link at all: either a document-backed draft blocked
// before a real attachment/link existed, or an artifact-creation job that finished via the
// copilot_prompt_fallback path (a build prompt to paste into Word/PowerPoint Copilot, not a
// file). There is nothing under "Where it is" for either case, so the reason/prompt itself has to
// be the visible content -- otherwise the card would be an empty-looking shell.
function artifactFallbackPreview(job) {
  const docStatus = job.document_status || "";
  if (job.document_backed_draft && docStatus && docStatus !== "found") {
    return job.blocker || "Source document could not be located, attached, or linked.";
  }
  if (job.document_backed_draft && docStatus === "found") {
    // Reported found, but no attachment/link survived -- validate_document_backed_completion
    // already downgraded this job to blocked with the reason in job.blocker.
    return job.blocker || "Source document was reported found, but no attachment or link was recorded.";
  }
  if (job.artifact_creation_mode === "copilot_prompt_fallback") {
    let prompt = "";
    try {
      const pkg = JSON.parse(job.artifact_package_json || "{}");
      prompt = String(pkg.copilotPrompt || "").trim();
    } catch {}
    if (prompt) {
      const truncated = prompt.length > 400 ? `${prompt.slice(0, 400).trim()}…` : prompt;
      return `Copilot build prompt ready to paste into Word/PowerPoint Copilot: ${truncated}`;
    }
  }
  if (job.artifact_request && job.status === "blocked") {
    return job.blocker || "Artifact could not be created or delivered.";
  }
  return job.blocker || cleanResultSummary(job.result_summary) || "Prepared item — no further detail recorded.";
}

function resultPreview(job, link) {
  if (!link?.href) return artifactFallbackPreview(job);
  if (link.draftId) {
    return draftContentPreview(job, link.label);
  }
  const cleaned = cleanResultSummary(job.result_summary);
  if (cleaned && !isOperationalResultSummary(cleaned)) return cleaned;
  const fileName = fileNameFromLink(link);
  const topic = humanizeFileName(fileName) || job.title || link.label || "review";
  return `${documentKind(fileName, link.label)} prepared for review: ${topic}.`;
}

function artifactStatusBadges(job) {
  // Surfaces the document-backed-draft / artifact-creation stamps that are otherwise invisible in
  // the dashboard: where the referenced source document stands, what kind of artifact this is,
  // whether it was actually created or only handed off as a Copilot build prompt, and whether an
  // email draft has the source document attached. Quiet chips for good news, "blocked"-styled
  // chips for anything the user still needs to act on -- mirrors readinessBadges' pattern.
  const out = [];
  const docStatus = job.document_status || "";
  if (docStatus === "found") {
    out.push(`<span class="ready-badge" title="Drew located the source document referenced in this request.">Source document found</span>`);
  } else if (docStatus === "not_found") {
    out.push(`<span class="ready-badge blocked" title="Drew searched and could not locate the source document referenced in this request.">Source document not found</span>`);
  } else if (docStatus === "attach_failed") {
    out.push(`<span class="ready-badge blocked" title="The source document was found but could not be attached or linked.">Attachment failed</span>`);
  }
  const artifactType = job.artifact_type || "";
  if (artifactType === "docx") out.push(`<span class="ready-badge" title="A Word document was requested for this job.">Word document</span>`);
  if (artifactType === "pptx") out.push(`<span class="ready-badge" title="A PowerPoint deck was requested for this job.">PowerPoint deck</span>`);
  const creationMode = job.artifact_creation_mode || "";
  if (creationMode === "created") {
    out.push(`<span class="ready-badge" title="Drew created the file directly.">Created</span>`);
  } else if (creationMode === "copilot_prompt_fallback") {
    out.push(`<span class="ready-badge" title="Direct creation was unavailable, so a complete build prompt was prepared to paste into Word/PowerPoint Copilot instead.">Copilot prompt fallback</span>`);
  }
  if (job.document_backed_draft && docStatus === "found" && job.draft_composed) {
    out.push(`<span class="ready-badge" title="This email draft has the located source document attached or linked.">Draft includes source document</span>`);
  }
  return out.join("");
}

function renderMetrics() {
  $("approvalCount").textContent = kpiItems("approvals").length;
  $("urgentCount").textContent = kpiItems("urgent").length;
  $("taskCount").textContent = kpiItems("tasks").length;
  $("draftCount").textContent = kpiItems("results").length;
  $("inboxSignal").textContent = kpiItems("review").length;
  $("calendarSignal").textContent = kpiItems("calendar").length;
  $("teamsSignal").textContent = kpiItems("messages").length;
  $("ledgerUpdated").textContent = state.serverTime ? new Date(state.serverTime).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "—";
}

function jobsForEmployee(name) {
  return state.jobs.filter((job) => job.employee === name);
}

const TRUST_OPTIONS = [["draft", "Draft"], ["assist", "Assist"], ["autonomous", "Autonomous"]];
const TRUST_NAME = { draft: "Draft", assist: "Assist", autonomous: "Autonomous" };
// Which employee protocol panels are expanded — survives the ~15s SSE/poll re-render.
const expandedEmployees = new Set();

function protoList(items) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return "<li class='muted'>—</li>";
  return list.map((t) => `<li>${escapeHtml(t)}</li>`).join("");
}

function renderEmployees() {
  const active = (state.employees || []).filter((e) => (e.status || "active") === "active");
  $("employees").innerHTML = active.map((employee) => {
    const jobs = jobsForEmployee(employee.name);
    const active = jobs.find((job) => ["queued", "in_progress"].includes(job.status));
    const status = employee.workStatus || (active ? "working" : "ready");
    const initials = String(employee.name || "?").split(/\s+/).map((w) => w[0] || "").join("").slice(0, 2).toUpperCase();
    const trust = String(employee.trust_level || "draft").toLowerCase();
    const proto = employee.protocol || {};
    const enabled = employee.enabled !== false;
    const fixed = employee.mode === "fixed";
    const trustName = TRUST_NAME[trust] || "Draft";
    const open = expandedEmployees.has(employee.name) ? " open" : "";
    // Adjustable employees get a level dropdown; fixed ones (Major/Dash/Reese) get a locked badge + note.
    const levelControl = fixed
      ? `<div class="trust-fixed">Level <strong>${escapeHtml(trustName)}</strong> · fixed${employee.note ? `<span class="trust-note">${escapeHtml(employee.note)}</span>` : ""}</div>`
      : `<label class="trust-grad">Level
           <select data-emp-trust="${escapeHtml(employee.name)}">${TRUST_OPTIONS.map(([v, label]) => `<option value="${v}"${v === trust ? " selected" : ""}>${label}</option>`).join("")}</select>
         </label>`;
    const powerBtn = fixed
      ? ""
      : `<button type="button" class="emp-power ${enabled ? "on" : "off"}" data-emp-toggle="${escapeHtml(employee.name)}" data-enabled="${enabled}">${enabled ? "On" : "Paused"}</button>`;
    return `
      <article class="employee${enabled ? "" : " paused"}" data-trust="${trust}">
        <div class="employee-top">
          <div class="emp-id">
            <span class="avatar">${escapeHtml(initials)}</span>
            <h3>${escapeHtml(employee.name)}</h3>
          </div>
          <div class="emp-top-right">
            <span class="${statusClass(status)}">${escapeHtml(status)}</span>
            ${employee.removable ? `<button type="button" class="emp-remove" data-emp-remove="${escapeHtml(employee.name)}" title="Remove ${escapeHtml(employee.name)} from the team">✕</button>` : ""}
          </div>
        </div>
        <div class="role">${escapeHtml(employee.role)}</div>
        <div class="emp-trust-row">
          <span class="trust-badge trust-${trust}" title="${escapeHtml(employee.trustLabel || "")}">${escapeHtml(trustName)}</span>
          ${powerBtn}
        </div>
        <div class="skills">${escapeHtml(active?.title || employee.detail)}</div>
        <details class="emp-protocol" data-emp="${escapeHtml(employee.name)}"${open}>
          <summary>Trust &amp; protocol</summary>
          <div class="proto">
            <div class="trust-label-line">${escapeHtml(employee.trustLabel || "")}</div>
            <div class="proto-block always"><span class="proto-h">Always do</span><ul>${protoList(proto.alwaysDo)}</ul></div>
            <div class="proto-block ask"><span class="proto-h">Ask first</span><ul>${protoList(proto.askFirst)}</ul></div>
            <div class="proto-block never"><span class="proto-h">Never do</span><ul>${protoList(proto.neverDo)}</ul></div>
            ${levelControl}
          </div>
        </details>
      </article>
    `;
  }).join("");
}
// Persist which protocol panels are open across re-renders.
document.addEventListener("toggle", (event) => {
  const d = event.target;
  if (!d.classList || !d.classList.contains("emp-protocol")) return;
  const name = privacyAttribute(d, "data-emp");
  if (!name) return;
  if (d.open) expandedEmployees.add(name); else expandedEmployees.delete(name);
}, true);

// ---------- Composable team: onboarding cards, removed list, Add Employee dialog ----------
function renderOnboardingCards() {
  const host = $("onboardingCards");
  if (!host) return;
  const pending = (state.employees || []).filter((e) => e.status === "onboarding" || e.status === "review");
  if (!pending.length) { host.innerHTML = ""; return; }
  host.innerHTML = pending.map((e) => {
    const ready = e.status === "review";
    const msg = ready
      ? "Major proposed a profile — review and add to your team."
      : "Major is reading the material and will propose a profile…";
    return `
      <div class="onboard-card ${ready ? "ready" : ""}">
        <div class="onboard-main">
          <span class="onboard-spin">${ready ? "✓" : "⟳"}</span>
          <div>
            <div class="onboard-name">Onboarding ${escapeHtml(e.name)}</div>
            <div class="onboard-msg">${escapeHtml(msg)}</div>
          </div>
        </div>
        <div class="onboard-actions">
          ${ready ? `<button class="btn primary" data-onboard-review="${escapeHtml(e.name)}">Review proposal</button>` : ""}
          <button class="btn" data-onboard-cancel="${escapeHtml(e.name)}">Cancel</button>
        </div>
      </div>`;
  }).join("");
}

function renderRemovedEmployees() {
  const host = $("removedEmployees");
  if (!host) return;
  const removed = state.removedEmployees || [];
  if (!removed.length) { host.innerHTML = ""; return; }
  host.innerHTML = `
    <div class="removed-bar">
      <span class="removed-label">Removed (${removed.length}):</span>
      ${removed.map((e) => `<span class="removed-chip">${escapeHtml(e.name)} <button type="button" data-restore="${escapeHtml(e.name)}" title="Restore ${escapeHtml(e.name)}">restore</button></span>`).join("")}
    </div>`;
}

async function removeEmployee(name) {
  if (!confirm(`Remove ${name} from the active team?\n\nTheir past work stays in your ledger and any open jobs move to Major. You can restore them later.`)) return;
  try { await api(`/api/employees/${encodeURIComponent(name)}/remove`, { method: "POST", body: "{}" }); await loadState(); }
  catch (err) { transientStatus = `Could not remove ${name}: ${err.message}`; render(); }
}

async function restoreEmployee(name) {
  try { await api(`/api/employees/${encodeURIComponent(name)}/restore`, { method: "POST", body: "{}" }); await loadState(); }
  catch (err) { transientStatus = `Could not restore ${name}: ${err.message}`; render(); }
}

document.addEventListener("click", (event) => {
  const rm = event.target.closest("[data-emp-remove]");
  const restore = event.target.closest("[data-restore]");
  const review = event.target.closest("[data-onboard-review]");
  const cancel = event.target.closest("[data-onboard-cancel]");
  if (rm) { event.preventDefault(); removeEmployee(privacyAttribute(rm, "data-emp-remove")); }
  else if (restore) { event.preventDefault(); restoreEmployee(privacyAttribute(restore, "data-restore")); }
  else if (review) { event.preventDefault(); const e = (state.employees || []).find((x) => x.name === privacyAttribute(review, "data-onboard-review")); if (e) openAddEmployeeReview(e); }
  else if (cancel) { event.preventDefault(); removeEmployee(privacyAttribute(cancel, "data-onboard-cancel")); }
});

function openPrivateHref(event) {
  if (event.type === "auxclick" && event.button !== 1) return false;
  const anchor = event.target.closest("a[href]");
  if (!anchor) return false;
  const rawHref = privacyAttribute(anchor, "href");
  if (!rawHref || rawHref === anchor.getAttribute("href")) return false;
  event.preventDefault();
  if (event.button === 1 || anchor.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey) {
    window.open(rawHref, "_blank", "noopener");
  } else {
    window.location.assign(rawHref);
  }
  return true;
}

document.addEventListener("click", openPrivateHref, true);
document.addEventListener("auxclick", openPrivateHref, true);

function openAddEmployee() {
  renderAddEmpForm();
  const dlg = $("addEmployeeDialog");
  if (dlg && !dlg.open) dlg.showModal();
}

function openAddEmployeeReview(emp) {
  renderAddEmpReview(emp);
  const dlg = $("addEmployeeDialog");
  if (dlg && !dlg.open) dlg.showModal();
}

async function extractFileToTextarea(file, textarea, statusEl) {
  if (!file || !textarea) return;
  if (statusEl) { statusEl.textContent = `Reading ${file.name}…`; statusEl.className = "career-status"; }
  try {
    const buf = await file.arrayBuffer();
    const res = await fetch("/api/career-profile/extract", {
      method: "POST",
      headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream", ...authHeaders() },
      body: buf
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
    const existing = privacyControlValue(textarea).trim();
    const nextValue = existing ? `${existing}\n\n${data.text}` : data.text;
    if (hideCompanyNames) {
      rawPrivacyControlValues.set(textarea, nextValue);
      textarea.value = maskCompanyNames(nextValue);
      textarea.readOnly = true;
    } else {
      textarea.value = nextValue;
    }
    if (statusEl) { statusEl.textContent = `Loaded ${file.name}.`; statusEl.className = "career-status ok"; }
  } catch (err) {
    if (statusEl) { statusEl.textContent = `Could not read ${file.name}: ${err.message}`; statusEl.className = "career-status err"; }
  }
}

function renderAddEmpForm() {
  $("addEmpTitle").textContent = "Add an employee";
  $("addEmpSub").textContent = "Port one of your own Scout workflows in as a first-class team member.";
  $("addEmpBody").innerHTML = `
    <div class="add-emp-form">
      <label class="field-label">Name<input id="aeName" type="text" maxlength="40" placeholder="e.g. Nova" autocomplete="off"></label>
      <label class="field-label">What is this employee? <span class="muted">(optional, one line)</span><input id="aeHint" type="text" placeholder="e.g. Tracks contracts and renewals"></label>
      <label class="field-label">Their operating material <span class="muted">— paste their .md / workflow</span>
        <textarea id="aeSource" placeholder="Paste the markdown / instructions that define this employee…"></textarea>
      </label>
      <div class="upload-row"><input type="file" id="aeFile" accept=".txt,.md,.markdown,.docx"><span class="upload-hint">…or upload .txt, .md, or .docx</span></div>
      <label class="ae-check"><input type="checkbox" id="aeAnalyze" checked> Let Major read the material and propose the profile <span class="muted">(recommended)</span></label>
      <div class="career-actions">
        <button class="btn primary" id="aeStartBtn" type="button">Start onboarding</button>
        <span class="career-status" id="aeStatus"></span>
      </div>
    </div>`;
  const fileInput = $("aeFile");
  if (fileInput) fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) extractFileToTextarea(f, $("aeSource"), $("aeStatus"));
    fileInput.value = "";
  });
  $("aeStartBtn").addEventListener("click", startOnboarding);
}

async function startOnboarding() {
  const name = privacyControlValue($("aeName")).trim();
  const status = $("aeStatus");
  if (!name) { status.textContent = "A name is required."; status.className = "career-status err"; return; }
  const analyze = $("aeAnalyze").checked;
  const body = {
    name,
    hint: privacyControlValue($("aeHint")).trim(),
    sourceText: privacyControlValue($("aeSource")),
    analyze
  };
  status.textContent = analyze ? "Starting — Major will read the material…" : "Creating draft…";
  status.className = "career-status";
  try {
    const res = await api("/api/employees/add", { method: "POST", body: JSON.stringify(body) });
    await loadState();
    if (res.analyzing) {
      renderAddEmpAnalyzing(name);
    } else {
      const emp = (state.employees || []).find((e) => e.name === name);
      if (emp) renderAddEmpReview(emp); else closeAddEmployee();
    }
  } catch (err) {
    status.textContent = `Could not start: ${err.message}`;
    status.className = "career-status err";
  }
}

function renderAddEmpAnalyzing(name) {
  $("addEmpTitle").textContent = `Onboarding ${name}`;
  $("addEmpSub").textContent = "Major is getting to know your new employee.";
  $("addEmpBody").innerHTML = `
    <div class="ae-analyzing">
      <div class="ae-spin-big">⟳</div>
      <p>Major is reading <strong>${escapeHtml(name)}</strong>'s material and will propose a role, triggers, skills, and trust level.</p>
      <p class="muted">This takes a moment. You can close this — a card on the cockpit will say when the proposal is ready to review, and you'll get a Teams ping.</p>
      <div class="career-actions"><button class="btn" id="aeCloseAnalyzeBtn" type="button">Close — I'll review later</button></div>
    </div>`;
  $("aeCloseAnalyzeBtn").addEventListener("click", closeAddEmployee);
}

function renderAddEmpReview(emp) {
  $("addEmpTitle").textContent = `Review ${emp.name}`;
  $("addEmpSub").textContent = "Edit anything, then add them to your team. Their level starts at Draft — you can change it anytime.";
  const always = (emp.always || []).join("\n");
  const skills = (emp.skills || []).join(", ");
  const lvl = (emp.trust_level || "draft");
  $("addEmpBody").innerHTML = `
    <div class="add-emp-form" data-emp="${escapeHtml(emp.name)}">
      <label class="field-label">Role<input id="aeRole" type="text" maxlength="60" value="${escapeHtml(emp.role || "")}"></label>
      <label class="field-label">Summary<input id="aeSummary" type="text" value="${escapeHtml(emp.detail || "")}"></label>
      <div class="ae-two">
        <label class="field-label">Does on its own <span class="muted">(internal)</span><input id="aeInternal" type="text" placeholder="e.g. organize and tag contract files" value="${escapeHtml(emp.internal || "")}"></label>
        <label class="field-label">Outward action<input id="aeOutward" type="text" placeholder="e.g. send contract status emails" value="${escapeHtml(emp.outward || "")}"></label>
      </div>
      <label class="field-label">Always do <span class="muted">(one per line)</span><textarea id="aeAlways" rows="3" placeholder="Track contract renewal dates&#10;Flag expiring MSAs">${escapeHtml(always)}</textarea></label>
      <label class="field-label">Engage when <span class="muted">— tells Major when to use them</span><textarea id="aeTriggers" rows="2" placeholder="when an email or Teams message mentions a contract, renewal, SOW, or MSA">${escapeHtml(emp.triggers || "")}</textarea></label>
      <label class="field-label">Skills <span class="muted">(comma-separated Scout skill ids)</span><input id="aeSkills" type="text" placeholder="docx, pptx" value="${escapeHtml(skills)}"></label>
      <label class="field-label">Starting trust level
        <select id="aeLevel">
          <option value="draft"${lvl === "draft" ? " selected" : ""}>Draft — prepares, you send</option>
          <option value="assist"${lvl === "assist" ? " selected" : ""}>Assist — you approve, it sends</option>
          <option value="autonomous"${lvl === "autonomous" ? " selected" : ""}>Autonomous — it sends</option>
        </select>
      </label>
      <div id="aeSkillCheck" class="ae-skillcheck"></div>
      <div class="career-actions">
        <button class="btn primary" id="aeConfirmBtn" type="button">Add ${escapeHtml(emp.name)} to the team</button>
        <button class="btn" id="aeCancelReviewBtn" type="button">Cancel onboarding</button>
        <span class="career-status" id="aeStatus"></span>
      </div>
    </div>`;
  $("aeConfirmBtn").addEventListener("click", () => confirmEmployee(emp.name));
  $("aeCancelReviewBtn").addEventListener("click", () => { removeEmployee(emp.name); closeAddEmployee(); });
  $("aeSkills").addEventListener("change", () => checkSkills());
  checkSkills();
}

function reviewSkillList() {
  return privacyControlValue($("aeSkills")).split(",").map((s) => s.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "")).filter(Boolean);
}

async function checkSkills() {
  const host = $("aeSkillCheck");
  if (!host) return;
  const skills = reviewSkillList();
  if (!skills.length) { host.innerHTML = ""; return; }
  try {
    const res = await api("/api/skills/check", { method: "POST", body: JSON.stringify({ skills }) });
    host.innerHTML = `<div class="ae-skill-h">Skills check</div>` + (res.results || []).map((r) =>
      `<div class="ae-skill-row ${r.installed ? "ok" : "missing"}">
        <span>${r.installed ? "✓" : "⚠"} ${escapeHtml(r.name)}</span>
        <span class="ae-skill-state">${r.installed ? "installed" : `<button class="btn tiny" data-install-skill="${escapeHtml(r.name)}">Install</button>`}</span>
      </div>`).join("");
    host.querySelectorAll("[data-install-skill]").forEach((btn) => btn.addEventListener("click", () => installSkill(privacyAttribute(btn, "data-install-skill"))));
  } catch (err) { host.innerHTML = `<div class="career-status err">Skill check failed: ${escapeHtml(err.message)}</div>`; }
}

async function installSkill(name) {
  const host = $("aeSkillCheck");
  try {
    const res = await api("/api/skills/install", { method: "POST", body: JSON.stringify({ name }) });
    if (!res.installed) {
      const text = prompt(`Couldn't find "${name}" locally. Paste its SKILL.md contents to install it (or Cancel):`);
      if (text && text.trim()) {
        const res2 = await api("/api/skills/install", { method: "POST", body: JSON.stringify({ name, text }) });
        if (res2.installed) transientStatus = `Installed ${name}. Restart Scout to activate it.`;
      }
    } else {
      transientStatus = `Installed ${name}. Restart Scout to activate it.`;
    }
  } catch (err) { transientStatus = `Install failed: ${err.message}`; }
  checkSkills();
}

async function confirmEmployee(name) {
  const status = $("aeStatus");
  const body = {
    role: privacyControlValue($("aeRole")), summary: privacyControlValue($("aeSummary")),
    internal: privacyControlValue($("aeInternal")), outward: privacyControlValue($("aeOutward")),
    always: privacyControlValue($("aeAlways")).split("\n").map((s) => s.trim()).filter(Boolean),
    triggers: privacyControlValue($("aeTriggers")), skills: reviewSkillList(), level: $("aeLevel").value
  };
  status.textContent = "Adding…"; status.className = "career-status";
  try {
    await api(`/api/employees/${encodeURIComponent(name)}/confirm`, { method: "POST", body: JSON.stringify(body) });
    await loadState();
    closeAddEmployee();
  } catch (err) { status.textContent = `Could not add: ${err.message}`; status.className = "career-status err"; }
}

function closeAddEmployee() {
  const dlg = $("addEmployeeDialog");
  if (dlg && dlg.open) dlg.close();
}

const ACTION_LABELS = {
  approved: "Approve", rejected: "Reject", deferred: "Defer",
  accept: "Accept", tentative: "Tentative", follow: "Follow", decline: "Decline",
  acknowledged: "Keep it"
};
const ALL_ACTIONS = ["approved", "rejected", "deferred"];
const CALENDAR_ACTIONS = ["accept", "tentative", "follow", "decline"];
const DEADLINE_BLOCK_ACTIONS = ["acknowledged", "rejected"];

const APPROVAL_GROUPS = [
  { key: "calendar", icon: "📅", label: "Calendar invites", types: ["calendar"], actions: CALENDAR_ACTIONS,
    legend: "Accept/Tentative/Decline = real Outlook RSVP · Follow = no RSVP sent, invite kept so Mina keeps watching it for changes (use when you can't attend but still want updates)",
    capabilities: "Accept, Tentative, and Decline each send a real RSVP on the original invite. Follow sends no RSVP and leaves the invite in place while Mina monitors it for reschedules/cancellations. Proposing a new time isn't available from here." },
  { key: "deadline-block", icon: "⏰", label: "Auto-scheduled deadlines", types: ["deadline-block"], actions: DEADLINE_BLOCK_ACTIONS,
    legend: "Tilly already created this focus block on your calendar before the deadline — no approval needed to create it. Keep it = leave the block on your calendar · Reject = cancel/delete the block Tilly created",
    capabilities: "This event is created automatically as soon as Tilly finds conflict-free time before the deadline — this card is here for visibility and reversal only. Keep it just closes the card with the block in place. Reject deletes the calendar event Tilly created and logs that you rejected it." },
  { key: "email", icon: "✉️", label: "Emails", types: ["email"], actions: ALL_ACTIONS,
    legend: "Approve = Major carries out your instruction on this email for real (reply, send, forward) and files the source — drafts only if you ask · Reject = delete the email · Defer = dismiss (email kept)",
    capabilities: "CAN: actually send your reply/forward from Outlook and file the source email. Say \"draft it\" in your note to get a reviewable draft instead of sending. CAN'T: send to brand-new recipients you didn't name, or send if it can't resolve the recipient (it'll report blocked)." },
  { key: "attachment-review", icon: "📎", label: "Documents for review", types: ["attachment-review"], actions: ALL_ACTIONS,
    legend: "Approve = Quinn reads the email + attachment/document, states whether you need to act or it's just FYI, and files anything worth keeping into the epiq folder · Reject = delete the source email · Defer = dismiss (email kept)",
    capabilities: "CAN: read the attachment/linked document content (not just the subject line), decide FYI vs needs-action, and automatically file high-value reference material (ROI decks, proposals, roadmaps) into the epiq working folder. CAN'T: send a reply on your behalf — that's still routed through Emails." },
  { key: "teams", icon: "💬", label: "Teams", types: ["teams"], actions: ["approved", "rejected"],
    legend: "Approve = Major carries out your instruction on the original chat for real (reply, 👍 react, forward, send) — drafts only if you ask · Reject = dismiss",
    capabilities: "CAN: post your reply for real in the original 1:1/chat; say \"draft it\" to get a draft instead. CAN'T: add a native emoji reaction (the tap-the-message kind) — that tool isn't available, so a \"👍 react\" request is sent as a short \"👍\" reply in the chat." },
  { key: "suggestions", icon: "🧠", label: "Suggestions",
    types: ["meeting-prep", "commitment", "blocked-work", "outbound-draft", "research", "impact-highlight", "stale-thread"],
    actions: ALL_ACTIONS,
    legend: "Approve = do the work (outbound items are carried out per your instruction) · Reject = skip · Defer = snooze",
    capabilities: "Approve = the team does the work and prepares the result; anything outbound is carried out per your instruction. These are internal prep, research, and draft items — nothing is sent unless you say so." },
];

function approvalEffect(actionType, decision) {
  const effects = {
    calendar: { accept: "RSVP Accept", tentative: "RSVP Tentative", follow: "No RSVP — keep the invite and watch it for changes", decline: "RSVP Decline" },
    "deadline-block": { acknowledged: "Keep the auto-created focus block on your calendar", rejected: "Cancel and delete the auto-created focus block" },
    email: { approved: "Do what you instructed on this email for real (send/reply/forward), then file the source — drafts only if you ask", rejected: "Delete the email from your Inbox", deferred: "Dismiss this card (email left untouched)" },
    teams: { approved: "Do what you instructed on the original chat for real (reply, 👍 react, forward) — drafts only if you ask", rejected: "Dismiss this card", deferred: "Dismiss this card" },
    "attachment-review": { approved: "Quinn inspects the email + attachment/document content, decides FYI vs needs-action, and files anything worth keeping into the epiq folder", rejected: "Delete the email from your Inbox", deferred: "Dismiss this card (email left untouched)" },
  };
  const advisory = { approved: "Do the work (outbound items are carried out per your instruction)", rejected: "Skip it", deferred: "Snooze it" };
  return (effects[actionType] || advisory)[decision] || decision;
}


function approvalGroupItems(groupKey) {
  const group = APPROVAL_GROUPS.find((g) => g.key === groupKey);
  if (!group || !state || !state.approvals) return [];
  return state.approvals.filter((approval) => group.types.includes(approval.action_type));
}

function syncSelectAllStates() {
  APPROVAL_GROUPS.forEach((group) => {
    const selectAll = document.querySelector(`[data-group-selectall="${group.key}"]`);
    if (!selectAll) return;
    const items = approvalGroupItems(group.key);
    const selectedCount = items.filter((a) => selectedApprovals.has(a.id)).length;
    selectAll.checked = items.length > 0 && selectedCount === items.length;
    selectAll.indeterminate = selectedCount > 0 && selectedCount < items.length;
  });
}

function evidenceVerdictBadge(approval) {
  if (approval.action_type !== "attachment-review" || !approval.evidence_json) return "";
  let evidence;
  try { evidence = JSON.parse(approval.evidence_json); } catch { return ""; }
  const rec = evidence && evidence.recommendation;
  const verdict = rec && rec.verdict;
  if (rec && rec.subtype === "delegate_misroute") {
    return `<span class="risk high" title="Outside your WorkIQ role/responsibilities">🔀 ACT: Delegate</span>`;
  }
  const labels = { act: "🔔 ACT", fyi: "ℹ️ FYI", review_required: "🟡 REVIEW REQUIRED" };
  if (!labels[verdict]) return "";
  return `<span class="risk ${verdict === "act" ? "high" : verdict === "review_required" ? "medium" : "low"}">${labels[verdict]}</span>`;
}

function renderApprovals() {
  const container = $("approvals");
  if (!state.approvals.length) {
    container.innerHTML = `<div class="empty">No pending approvals.</div>`;
    approvalsRenderSig = "";
    selectedApprovals.clear();
    return;
  }
  // Forget selections for cards that are no longer pending (acted on or retired).
  const liveIds = new Set(state.approvals.map((a) => a.id));
  for (const id of [...selectedApprovals]) if (!liveIds.has(id)) selectedApprovals.delete(id);

  // Only rebuild the DOM when the set of cards actually changes. A 2s SSE refresh that
  // does not change the cards must NOT wipe an in-progress selection, so on a no-op
  // refresh we just re-apply the tracked selection to the existing checkboxes.
  const sig = state.approvals.map((a) => `${a.id}:${a.status}`).join("|");
  if (sig === approvalsRenderSig && container.querySelector("[data-approval-check]")) {
    container.querySelectorAll("[data-approval-check]").forEach((cb) => {
      cb.checked = selectedApprovals.has(privacyAttribute(cb, "data-approval-check"));
    });
    syncSelectAllStates();
    return;
  }
  approvalsRenderSig = sig;

  container.innerHTML = APPROVAL_GROUPS.map((group) => {
    const items = state.approvals.filter((approval) => group.types.includes(approval.action_type));
    if (!items.length) return "";
    const cards = items.map((approval) => `
      <article class="approval">
        <input type="checkbox" data-approval-check="${escapeHtml(approval.id)}" data-group="${group.key}"${selectedApprovals.has(approval.id) ? " checked" : ""} aria-label="Select ${escapeHtml(approval.title)}">
        <div>
          <h3>${escapeHtml(approval.title)}</h3>
          <div class="approval-meta">
            <span>${escapeHtml(approval.employee)}</span>
            <span class="risk ${escapeHtml(approval.risk)}">${escapeHtml(approval.risk)}</span>
            <span>${escapeHtml(approval.action_type)}</span>
            ${evidenceVerdictBadge(approval)}
            ${approval.sourceUrl ? `<a class="approval-source" href="${escapeHtml(approval.sourceUrl)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(approval.sourceLabel || "Open source")}">${escapeHtml(approval.sourceLabel || "Open source")} <span aria-hidden="true">↗</span></a>` : ""}
          </div>
          <div class="preview">${formatApprovalPreview(approval.preview)}</div>
        </div>
      </article>`).join("");
    return `
      <section class="approval-group" data-group-section="${group.key}">
        <div class="approval-group-head">
          <h3 class="approval-group-title">${group.icon} ${escapeHtml(group.label)} <span class="approval-group-count">(${items.length})</span>${group.capabilities ? gInfo(group.capabilities) : ""}</h3>
          <label class="approval-selectall"><input type="checkbox" data-group-selectall="${group.key}"> Select all</label>
        </div>
        <div class="approval-group-bar">
          <div class="toolbar approval-group-actions" style="justify-content:flex-start;">
            ${(group.actions || ALL_ACTIONS).map((action, i) => `<button class="btn ${i === 0 ? "primary" : ""}" data-group-action="${action}" data-group-key="${group.key}">${ACTION_LABELS[action]}</button>`).join("")}
          </div>
          <p class="approval-legend">${escapeHtml(group.legend)}</p>
        </div>
        <div class="approval-group-list">${cards}</div>
      </section>`;
  }).join("");
  syncSelectAllStates();
}

function sendControl(job) {
  const s = job.send_state || "";
  if (s === "open_to_send") {
    return `<div class="send-row"><span class="send-tag manual">Ready — open it above and send it yourself</span></div>`;
  }
  if (s === "ready") {
    return `<div class="send-row"><button type="button" class="btn primary send-btn" data-send-draft="${escapeHtml(job.id)}">Send</button><span class="send-hint">${escapeHtml(job.employee)} will deliver it on your click</span></div>`;
  }
  if (s === "held_classified") {
    return `<div class="send-row held"><button type="button" class="btn send-btn" data-send-draft="${escapeHtml(job.id)}">Review &amp; Send</button><span class="send-hint">🔒 Confidential — held for your OK even at Autonomous</span></div>`;
  }
  if (s === "sent") {
    return `<div class="send-row"><span class="send-tag sent">Sent ✓</span></div>`;
  }
  return "";
}

function renderTeamIntel() {
  // Quinn's risk register and Casey's knowledge graph, side by side. Both summaries carry
  // readable=false when the app could not read their tables, and that is shown rather than
  // silently drawn as zeroes — "nothing to review" and "cannot see the reviews" are different
  // things, and only one of them is good news.
  const body = $("teamIntelBody");
  const section = $("teamIntelSection");
  if (!body || !section) return;
  const q = state.qualitySummary;
  const k = state.knowledgeSummary;
  if (!q && !k && !state.capabilitySummary) { section.hidden = true; return; }
  section.hidden = false;
  const stale = (q && q.staleAutomations) || [];
  const held = (q && q.heldItems) || [];
  const byType = (k && k.byType) || {};
  const typeChips = Object.keys(byType).length
    ? Object.entries(byType).map(([type, n]) =>
        `<span class="intel-chip">${escapeHtml(type)} · ${n}</span>`).join("")
    : `<span class="intel-chip muted">nothing recorded yet</span>`;
  const quinnUnreadable = q && q.readable === false;
  const caseyUnreadable = k && k.readable === false;
  const c = state.capabilitySummary;
  const capUnreadable = c && c.readable === false;
  const redactionPending = (c && c.redactionPending) || 0;
  const capChips = c && !capUnreadable
    ? [
        ["talk tracks", c.talkTracks],
        ["conference packs", c.conferencePacks],
        ["chart specs", c.chartSpecs],
        ["flow docs", c.flowDocs],
      ].filter(([, n]) => Number(n) > 0)
        .map(([label, n]) => `<span class="intel-chip">${escapeHtml(label)} · ${n}</span>`).join("")
    : "";
  body.innerHTML = `
    <div class="g-cols">
      <div>
        <h4 class="g-h">Quinn — quality &amp; risk</h4>
        ${quinnUnreadable ? `<p class="empty">Quinn could not read the job table, so these numbers are unknown rather than zero.</p>` : `
        <div class="g-stats">
          <div class="g-stat"><span class="g-num">${(q && q.awaitingReview) || 0}</span><span class="g-lab">awaiting review ${gInfo("Drafts flagged qualityReview=true that Quinn has not returned a verdict on yet. These are held back from the Approval inbox until she does.")}</span></div>
          <div class="g-stat"><span class="g-num">${(q && q.heldJobs) || 0}</span><span class="g-lab">on hold ${gInfo("Items Quinn returned as 'hold' — something must be fixed before they go any further.")}</span></div>
          <div class="g-stat"><span class="g-num">${(q && q.flaggedForReview) || 0}</span><span class="g-lab">reviewed in total ${gInfo("Every job that has ever been flagged for Quinn's review.")}</span></div>
        </div>
        ${capUnreadable ? "" : `
        <div class="g-stats" style="margin-top:8px;">
          <div class="g-stat"><span class="g-num">${(c && c.contentAudits) || 0}</span><span class="g-lab">content audits ${gInfo("Drafts that have been through the brand-voice and quality pass and carry a stored audit.")}</span></div>
          <div class="g-stat"><span class="g-num ${redactionPending ? "warn-num" : ""}">${redactionPending}</span><span class="g-lab">redaction pending ${gInfo("Items where the sensitive-text scan found something and the redaction has not been applied yet. These are blocked from going anywhere until it is. The scan catches known patterns and is a floor, not a guarantee — read the draft too.")}</span></div>
        </div>`}
        ${held.length ? `<ul class="g-list">${held.map((j) =>
          `<li><span class="risk-badge risk-${escapeHtml(j.riskLevel || "none")}">hold</span> ${escapeHtml(j.title || j.id)} <span class="g-lab">— ${escapeHtml(j.employee || "")}</span></li>`).join("")}</ul>` : ""}
        ${stale.length ? `<h4 class="g-h warn" style="margin-top:10px;">Risk register</h4><ul class="g-list">${stale.map((s) =>
          `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : `<p class="g-lab" style="margin-top:10px;">No stale automations. Last sweep ${escapeHtml((q && q.lastSweepAt) ? formatTime(q.lastSweepAt) : "not yet recorded")}.</p>`}
        `}
      </div>
      <div>
        <h4 class="g-h">Casey — knowledge &amp; commitments</h4>
        ${caseyUnreadable ? `<p class="empty">Casey could not read the knowledge table, so these numbers are unknown rather than zero.</p>` : `
        <div class="g-stats">
          <div class="g-stat"><span class="g-num">${(k && k.totalEntries) || 0}</span><span class="g-lab">entries remembered ${gInfo("People, projects, commitments, decisions, files, and preferences the team has recorded locally. Nothing here leaves this machine.")}</span></div>
          <div class="g-stat"><span class="g-num">${(k && k.overdueCommitments) || 0}</span><span class="g-lab">overdue commitments ${gInfo("Commitments whose due date has passed and that are still open. These surface in the Morning Brief.")}</span></div>
          <div class="g-stat"><span class="g-num">${(k && k.staleEntries) || 0}</span><span class="g-lab">stale entries ${gInfo("Entries not updated in over 30 days. They may still be right — they are just worth re-checking.")}</span></div>
        </div>
        <div class="intel-chips">${typeChips}</div>
        ${capChips ? `<h4 class="g-h" style="margin-top:10px;">Artifacts produced ${gInfo("Extras the team has attached to jobs: talk tracks, conference packs, chart specs and flow documentation. Counts only.")}</h4><div class="intel-chips">${capChips}</div>` : ""}
        <p class="g-lab" style="margin-top:10px;">Last updated ${escapeHtml((k && k.lastUpdated) ? formatTime(k.lastUpdated) : "never")}.</p>
        `}
      </div>
    </div>`;
}

function qualityBadge(job) {
  // Quinn's verdict on a prepared item. An item flagged for review with no verdict yet is the
  // important case: it looks finished but has not been checked, so it gets the loudest badge.
  if (!job || !Number(job.quality_review)) return "";
  const verdict = (job.quality_verdict || "").trim();
  if (!verdict) return `<span class="quality-badge pending" title="Flagged for Quinn's review. Not checked yet.">Review required</span>`;
  if (verdict === "hold") return `<span class="quality-badge hold" title="Quinn held this: something must be fixed before it goes further.">Quinn: hold</span>`;
  if (verdict === "pass-with-notes") return `<span class="quality-badge notes" title="Quinn passed this with minor notes.">Quinn: pass with notes</span>`;
  return `<span class="quality-badge pass" title="Quinn checked this and found no issues.">Quinn: pass</span>`;
}

function readinessBadges(job) {
  // Extras the team attached to a job: a talk track for a deck, a conference pack, a chart spec,
  // flow documentation. These are informational, so they are quiet chips rather than status badges.
  // Redaction is the exception: an item that needs redacting and has not had it is blocked, and
  // that has to be visible on the card itself, not only in the summary panel.
  if (!job) return "";
  const out = [];
  if (Number(job.redaction_required) && !Number(job.redaction_applied)) {
    out.push(`<span class="ready-badge blocked" title="The sensitive-text scan found something and the redaction has not been applied. This is blocked until it is.">Redaction required</span>`);
  } else if (Number(job.redaction_applied)) {
    out.push(`<span class="ready-badge" title="Sensitive text was found and redacted.">Redacted</span>`);
  }
  const extras = [
    ["talk_track_json", "Talk track", "Per-slide timing, transitions and pause cues for this deck."],
    ["conference_pack_json", "Conference pack", "Title options, abstract, learning objectives and a bio scaffold."],
    ["chart_spec_json", "Chart spec", "A chart schema generated from this job's tabular data."],
    ["flow_doc_json", "Flow doc", "A plain-language summary of a Power Automate flow definition."],
  ];
  for (const [field, label, hint] of extras) {
    if (job[field]) out.push(`<span class="ready-badge" title="${escapeHtml(hint)}">${escapeHtml(label)}</span>`);
  }
  return out.join("");
}

// Looks up the account-ownership scope classification (see classify_account_scope server-side)
// for a job by matching it to its impact-ledger highlight (source_type "job", source_id job.id).
// Returns null when the item carries no confirmed account/customer context (account-neutral) or
// isn't in the ledger yet -- callers render nothing in that case rather than a misleading badge.
function accountScopeForJob(job) {
  const highlights = state?.impactLedger?.highlights || [];
  const match = highlights.find((item) => item.sourceType === "job" && item.sourceId === job.id);
  const scope = match?.accountScope;
  if (!scope || scope.scope === "account_neutral") return null;
  return scope;
}

function accountScopeBadge(job) {
  const scope = accountScopeForJob(job);
  if (!scope) return "";
  const labels = {
    owned_account: "🏢 Owned account",
    unowned_account: scope.importance === "raised" ? "⚠️ Unowned account — priority raised" : "🔽 Unowned account — lowest priority",
    uncertain_account: "❔ Uncertain account ownership",
  };
  const label = labels[scope.scope] || scope.scope;
  return `<span class="badge" title="${escapeHtml(scope.reason || "")}">${escapeHtml(label)}</span>`;
}

function renderDrafts() {
  ensurePersonAliases();
  const docs = linkedDocuments();
  $("drafts").innerHTML = docs.length ? docs.map(({ job, link }) => {
    const href = linkHref(link.href);
    const previewText = maskPrivacyText(resultPreview(job, link));
    // No result link at all (a blocked document-backed draft, or a completed
    // copilot_prompt_fallback artifact job) still needs a readable title -- fall back to the job's
    // own title rather than rendering an empty heading. Masked before display, same as the preview.
    const titleText = maskPrivacyText(link.label || job.title || "Prepared item (no link yet)");
    const linkContent = href
      ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(titleText)}</a>`
      : escapeHtml(titleText);
    return `
    <article class="item">
      <div class="item-top">
        <h3>${linkContent}</h3>
        <span class="${statusClass(job.status)}">${escapeHtml(job.status)}</span>
      </div>
      ${qualityBadge(job)}${readinessBadges(job)}${artifactStatusBadges(job)}${accountScopeBadge(job)}
      <div class="small-meta">
        <span>Created by ${escapeHtml(job.employee)}</span>
        <span>${formatTime(job.completed_at || job.updated_at)}</span>
      </div>
      <div class="preview">${escapeHtml(previewText)}</div>
      ${sendControl(job)}
    </article>
  `}).join("") : `<div class="empty">No created documents with links yet for today. Use Previous to browse earlier days.</div>`;
}

async function sendPreparedDraft(jobId) {
  try {
    await api(`/api/drafts/${encodeURIComponent(jobId)}/send`, { method: "POST", body: "{}" });
    await loadState();
  } catch (err) {
    transientStatus = `Could not send: ${err.message}`;
    render();
  }
}
document.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-send-draft]");
  if (btn) sendPreparedDraft(privacyAttribute(btn, "data-send-draft"));
});

function messagesForActiveView() {
  return state.messages.slice().sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
}

function minutesSince(value) {
  if (!value) return "";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  return minutes <= 0 ? "just now" : `${minutes} min ago`;
}

function activeWorkJobs() {
  return state.jobs.filter((job) =>
    ["queued", "in_progress", "blocked"].includes(job.status)
    && ["calendar-rsvp", "employee-work", "manual-signal-sweep", "email-action", "teams-action", "workflow-action"].includes(job.type)
  );
}

// Internal orchestration jobs that should never surface as user-facing work
// (the broad sweep is plumbing for Major, not an actionable task for the user).
const INTERNAL_JOB_TYPES = ["manual-signal-sweep"];

function userWorkJobs() {
  return activeWorkJobs().filter((job) => !INTERNAL_JOB_TYPES.includes(job.type));
}

function kpiItems(metric) {
  const work = userWorkJobs();
  return {
    approvals: state.approvals,
    urgent: [
      ...state.approvals.filter((approval) => ["high", "medium"].includes(approval.risk)),
      ...work.filter((job) => ["urgent", "high"].includes(job.priority)),
    ],
    tasks: work,
    results: linkedDocuments(),
    review: state.approvals.filter((approval) => ["email", "teams", "attachment-review"].includes(approval.action_type)),
    calendar: state.approvals.filter((approval) => approval.action_type === "calendar"),
    messages: messagesForActiveView(),
  }[metric] || [];
}

function jobProgressWidth(job) {
  // Advance in small, time-based increments within each status band instead of snapping straight
  // to a fixed number whenever the status changes, so the bar visibly creeps forward over time.
  const elapsedMin = Math.max(0, (Date.now() - new Date(job.updated_at || job.created_at).getTime()) / 60000);
  const creep = Math.min(elapsedMin, 6); // 0..6 minutes of gradual movement within the current band
  if (job.status === "blocked") return 100;
  if (job.status === "in_progress") return Math.min(50 + creep * 6, 90); // 50% -> 86% over ~6min
  if (job.status === "queued") return Math.min(10 + creep * 2, 22); // 10% -> 22% over ~6min
  return 80;
}

function renderWorkStatus(job, activeCount, message = "") {
  const lastUpdate = minutesSince(job.updated_at || job.created_at);
  const pulse = job.status === "blocked" ? "Waiting on blocker" : "Next Major status pulse within 3 min";
  const width = jobProgressWidth(job);
  $("chatStatus").className = `attention-banner active work-status ${job.status}`;
  $("chatStatus").innerHTML = `
    <div class="work-status-top">
      <strong>${escapeHtml(message || job.title)}</strong>
      <span>${escapeHtml(job.status)}</span>
    </div>
    <div class="work-status-meta">
      <span>Owner: ${escapeHtml(job.employee || "Major")}</span>
      <span>Active work: ${activeCount}</span>
      <span>Last update: ${escapeHtml(lastUpdate || "not yet")}</span>
      <span>ETA: ${escapeHtml(pulse)}</span>
    </div>
    <div class="work-progress" aria-hidden="true"><span style="width:${width}%"></span></div>
  `;
}

function latestSweep() {
  return state.jobs
    .filter((job) => job.type === "manual-signal-sweep")
    .sort((a, b) =>
      new Date(b.completed_at || b.updated_at || b.created_at) -
      new Date(a.completed_at || a.updated_at || a.created_at))[0];
}

function renderSweepSummary(sweep) {
  const cleaned = cleanResultSummary(sweep.result_summary) || "Broad sweep complete.";
  const when = sweep.completed_at || sweep.updated_at;
  $("chatStatus").className = "attention-banner active done";
  $("chatStatus").innerHTML = `
    <div class="work-status-top">
      <strong>Last Attention Major sweep — ${escapeHtml(cleaned)}</strong>
      <span>done</span>
    </div>
    <div class="work-status-meta">
      <span>Owner: Major</span>
      <span>Swept: ${escapeHtml(formatTime(when))}</span>
      <span>${escapeHtml(minutesSince(when) || "just now")}</span>
    </div>
  `;
}

function renderChatStatus() {
  const activeJobs = activeWorkJobs();
  const active = activeJobs[0];
  const sweep = latestSweep();
  const sweepDone = sweep && ["completed", "done"].includes(sweep.status);

  // A sweep that finished after the user's last request supersedes the transient "queued" banner.
  if (sweepRequestedAt && sweepDone) {
    const doneAt = new Date(sweep.completed_at || sweep.updated_at).getTime();
    if (doneAt >= sweepRequestedAt - 1000) {
      transientStatus = "";
      sweepRequestedAt = 0;
    }
  }

  if (transientStatus) {
    renderWorkStatus({ title: transientStatus, status: "queued", employee: "Major", updated_at: new Date().toISOString() }, 1, transientStatus);
    return;
  }
  if (!active) {
    // No active work: confirm the most recent sweep instead of going blank, so each press shows what Major found.
    const recent = sweepDone && (Date.now() - new Date(sweep.completed_at || sweep.updated_at).getTime()) < 45 * 60000;
    if (recent) {
      renderSweepSummary(sweep);
      return;
    }
    $("chatStatus").className = "attention-banner";
    $("chatStatus").textContent = "";
    return;
  }
  $("chatStatus").className = `attention-banner active ${active.status === "completed" ? "done" : active.status}`;
  if (active.type === "calendar-rsvp") {
    renderWorkStatus(active, activeJobs.length, active.status === "in_progress"
      ? `Mina is executing the RSVP: ${active.title}. This will update when completed or blocked.`
      : active.status === "blocked"
        ? `Mina is blocked on the RSVP: ${active.title}. Check the activity log for details.`
        : `RSVP queued: ${active.title}. The approval was removed from the inbox and the worker will report completion here.`);
    return;
  }
  if (active.type === "manual-signal-sweep") {
    renderWorkStatus(active, activeJobs.length, active.status === "in_progress"
      ? "Major is running a broad sweep now across app state, Outlook email, Inbox invites, calendar, Teams, WorkIQ/research context, drafts/results, blockers, and impact highlights."
      : active.status === "blocked"
        ? "Major's broad sweep is blocked. Check the activity log for details."
        : "Broad Attention Major sweep queued. This view updates live as Major checks app state, Outlook, calendar, Teams, WorkIQ/research context, drafts/results, blockers, and impact highlights.");
    return;
  }
  renderWorkStatus(active, activeJobs.length, active.status === "in_progress"
    ? `Major is working on: ${active.title}. Major will report who did the work, completion or blocker, and where the result is.`
    : active.status === "blocked"
      ? `Major is blocked on: ${active.title}. Check the thread for the blocker.`
      : `${active.title} is queued for Major. This view updates live when Major reports real progress or completion.`);
}

function renderMessages() {
  const messages = messagesForActiveView();
  $("messages").innerHTML = messages.length ? messages.map((message) => `
    <article class="chat-message ${message.sender === "user" ? "user" : "major"}">
      <div class="item-top">
        <h3>${escapeHtml(message.sender === "user" ? "You" : "Major")}</h3>
        <span class="${statusClass(message.status)}">${escapeHtml(message.status)}</span>
      </div>
      <div class="small-meta">
        <span>${escapeHtml(message.sender === "user" ? "To Major" : "From Major")}</span>
        <span>${formatTime(message.created_at)}</span>
      </div>
      <div class="message-body">${escapeHtml(message.message)}</div>
      ${renderLink(message.link_json)}
      <div class="toolbar" style="margin-top:10px; justify-content:flex-start;">
        <button data-thread="${escapeHtml(message.thread_id)}">Reply in thread</button>
      </div>
    </article>
  `).join("") : `<div class="empty">No Major chat messages yet.</div>`;
  renderChatStatus();
}

function renderThreadContext() {
  if (!activeThreadId) {
    $("threadContext").className = "chat-context";
    $("threadContext").textContent = "";
    $("sendBtn").textContent = "Send to Major";
    return;
  }
  $("threadContext").className = "chat-context active";
  $("threadContext").textContent = "Replying in an existing Major thread. Your next message will stay attached to this conversation.";
  $("sendBtn").textContent = "Reply in thread";
}

function renderFirstRunBanner() {
  const el = $("firstRunBanner");
  if (!el) return;
  const boardEmpty =
    state.approvals.length === 0 &&
    (((state.workLedgerToday && state.workLedgerToday.todayCount) || 0) === 0) &&
    kpiItems("results").length === 0 &&
    kpiItems("review").length === 0 &&
    kpiItems("calendar").length === 0 &&
    kpiItems("messages").length === 0 &&
    kpiItems("tasks").length === 0;
  if (!boardEmpty) {
    el.hidden = true;
    el.className = "first-run-banner";
    return;
  }
  const sweeps = state.jobs.filter((job) => job.type === "manual-signal-sweep");
  const sweepActive = sweeps.some((job) => ["queued", "in_progress"].includes(job.status));
  const everCompleted = sweeps.some((job) => ["completed", "done"].includes(job.status));
  let title;
  let body;
  let variant;
  if (sweepActive) {
    variant = "working";
    title = "Your team is doing its first sweep";
    body = "This usually takes 5 to 10 minutes, and the board fills in as it goes. You can leave this page open — it refreshes on its own.";
  } else if (everCompleted) {
    variant = "clear";
    title = "You're all caught up";
    body = "Your team checked your email, Teams, calendar, and meetings and found nothing that needs you right now. New items show up here automatically, or press Attention Major at the top to sweep again.";
  } else {
    variant = "";
    title = "Your board is ready to fill";
    body = "Press Attention Major at the top to run the first sweep across your email, Teams, calendar, and meetings. It takes about 5 to 10 minutes and fills the board as it goes.";
  }
  el.hidden = false;
  el.className = `first-run-banner${variant ? " " + variant : ""}`;
  el.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(body)}</span>`;
}

function renderAutomationBanner() {
  const el = $("automationBanner");
  if (!el) return;
  const health = state.automationHealth;
  // readable=false means Scout's automations file was not found or not parseable.
  // That is expected on some builds, so say nothing rather than raise a false alarm.
  if (!health || !health.readable || health.healthy) {
    el.hidden = true;
    return;
  }
  const off = (health.disabled || []).concat(health.missing || []);
  const label = off.map((name) => name.replace(/^Daily Flow /, "")).join(", ");
  const isMissing = (health.missing || []).length > 0;
  const title = off.length === 1
    ? "One of your automations is switched off"
    : `${off.length} of your automations are switched off`;
  const body = isMissing
    ? `Your team is not running ${label}. A missing or paused automation does nothing, so the board stops updating. Re-run /daily-flow-setup to put them back.`
    : `Your team is not running ${label}. A paused automation does nothing, so the board stops updating. Switch it back on in Scout under Automations.`;
  el.hidden = false;
  el.className = "first-run-banner warn";
  el.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(body)}</span>`;
}

function render() {
  renderFirstRunBanner();
  renderAutomationBanner();
  renderMetrics();
  renderEmployees();
  renderOnboardingCards();
  renderRemovedEmployees();
  renderGuardrails();
  renderTeamIntel();
  renderCivilianBadge();
  renderApprovals();
  renderDecisionMemory();
  renderDrafts();
  renderMessages();
  renderThreadContext();
  renderOwnedAccounts();
  if (hideCompanyNames && companyMaskReady) scrubCompanyNamesFromDom();
}

function renderRuntimeInventory() {
  // Counts only, and deliberately modest about scope: this reports what the app can verify about
  // itself from disk. It cannot see Scout's own tool list or MCP servers, so it must not be read
  // as a complete picture of the environment. Saying so here is cheaper than a wrong conclusion.
  const body = $("runtimeInventoryBody");
  if (!body) return;
  const r = runtimeInventory;
  if (!r || r.ok === false) { body.hidden = true; return; }
  body.hidden = false;
  const app = r.app || {};
  body.innerHTML = `
    <h4 class="g-h" style="margin-top:4px;">Runtime ${gInfo("What this machine can verify about the app itself: version, Python, capability endpoints and installed skills. It cannot see Scout's own tools or MCP servers, so it is not a full picture of the environment.")}</h4>
    <div class="g-stats">
      <div class="g-stat"><span class="g-num">${escapeHtml(String(app.version || "?"))}</span><span class="g-lab">app version</span></div>
      <div class="g-stat"><span class="g-num">${escapeHtml(String(app.python || "?"))}</span><span class="g-lab">python</span></div>
      <div class="g-stat"><span class="g-num">${(r.capabilities || []).length}</span><span class="g-lab">capability endpoints</span></div>
      <div class="g-stat"><span class="g-num">${Number(r.installedSkillCount) || 0}</span><span class="g-lab">skills installed</span></div>
    </div>
    <p class="g-lab" style="margin-top:8px;">Local token ${app.authRequired ? "required" : "not required"} · reports the app only, not Scout's tools.</p>`;
}

async function loadRuntimeInventory() {
  // Fetched once on load rather than on every poll: it describes the running process and does not
  // change while the process lives. A failure here is not worth an error banner — the panel just
  // stays hidden, because the rest of the dashboard is unaffected.
  try {
    runtimeInventory = await api("/api/runtime-inventory");
  } catch {
    runtimeInventory = null;
  }
  renderRuntimeInventory();
}

async function loadState() {
  state = await api("/api/state");
  if (hideCompanyNames) await prepareCompanyMask();
  else render();
}

async function sendChat(event) {
  event.preventDefault();
  const message = privacyControlValue($("chatMessage")).trim();
  if (!message) return;
  const result = await api("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, threadId: activeThreadId || undefined })
  });
  activeThreadId = result.threadId;
  clearPrivacyControlValue($("chatMessage"));
  await loadState();
}

async function attentionMajor() {
  $("attentionBtn").disabled = true;
  sweepRequestedAt = Date.now();
  transientStatus = "Attention Major requested. Queuing a broad sweep across app state, Outlook email, calendar, Inbox invites, Teams, WorkIQ/research context, drafts/results, blockers, and impact highlights...";
  renderChatStatus();
  try {
    const result = await api("/api/attention-major", {
      method: "POST",
      body: JSON.stringify({ source: "dashboard", force: true })
    });
    transientStatus = result.queued
      ? "Broad Attention Major sweep queued. Major will refresh signals and post live progress/results here as soon as the worker picks it up."
      : `Broad Attention Major sweep is already ${result.status}. Refreshing the cockpit now.`;
    await loadState();
    setTimeout(loadState, 1000);
    setTimeout(loadState, 3000);
    setTimeout(loadState, 7000);
    setTimeout(loadState, 20000);
    setTimeout(loadState, 40000);
    setTimeout(loadState, 65000);
  } finally {
    $("attentionBtn").disabled = false;
  }
}

function selectedApprovalIds(groupKey) {
  if (!groupKey) return [...selectedApprovals];
  const groupIds = new Set(approvalGroupItems(groupKey).map((a) => a.id));
  return [...selectedApprovals].filter((id) => groupIds.has(id));
}

function openApprovalFeedback(status, ids) {
  const selected = (ids && ids.length) ? ids : selectedApprovalIds();
  if (!selected.length) {
    alert("Select at least one item in this group first.");
    return;
  }
  pendingApprovalDecision = status;
  pendingApprovalIds = selected;
  const label = ACTION_LABELS[status] || "Send";
  $("approvalFeedbackTitle").textContent = `${label} ${selected.length} item${selected.length === 1 ? "" : "s"}`;
  const effects = selected.map((id) => {
    const approval = state.approvals.find((item) => item.id === id);
    if (!approval) return "";
    return `<li><strong>${escapeHtml(approvalEffect(approval.action_type, status))}</strong> — ${escapeHtml(approval.title)}</li>`;
  }).join("");
  $("approvalFeedbackEffects").innerHTML = `<p class="effects-label">This will:</p><ul class="effects-list">${effects}</ul>`;
  clearPrivacyControlValue($("approvalFeedbackText"));
  setGuidanceMicStatus("");
  setGuidanceMicRecordingUi(false);
  $("submitApprovalFeedbackBtn").textContent = `${label} and notify Major`;
  $("approvalFeedbackDialog").showModal();
  $("approvalFeedbackText").focus();
}

function setDecisionButtonsDisabled(disabled) {
  document.querySelectorAll("[data-group-action]").forEach((button) => {
    button.disabled = disabled;
  });
}

async function decideSelectedApprovals(status, userGuidance = "") {
  const selected = pendingApprovalIds.slice();
  if (!selected.length) throw new Error("No approvals are selected.");
  const selectedActionTypes = new Set(
    selected.map((id) => (state.approvals.find((a) => a.id === id) || {}).action_type)
  );
  transientStatus = `Sending ${selected.length} approval decision${selected.length === 1 ? "" : "s"} to Major...`;
  renderChatStatus();
  setDecisionButtonsDisabled(true);
  const alreadyHandled = [];
  for (const approvalId of selected) {
    const result = await api(`/api/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ status, userGuidance })
    });
    if (result && result.alreadyHandled) alreadyHandled.push(result.message || "Already handled.");
  }
  state.approvals = state.approvals.filter((approval) => !selected.includes(approval.id));
  state.metrics.pendingApprovals = state.approvals.length;
  if (alreadyHandled.length) {
    // Some (or all) calendar invites turned out to be no longer actionable (already responded to,
    // expired, or resolved outside the app) by the time the decision reached the server — nothing
    // was queued for those, so say so distinctly rather than claiming an RSVP/follow-up was sent.
    const rest = selected.length - alreadyHandled.length;
    transientStatus = `${alreadyHandled.length} item${alreadyHandled.length === 1 ? "" : "s"} skipped — already handled: ${alreadyHandled[0]}`
      + (rest > 0 ? ` ${rest} other item${rest === 1 ? "" : "s"} processed normally.` : "");
  } else {
    transientStatus = status === "deferred"
      ? `${selected.length} item${selected.length === 1 ? "" : "s"} deferred and removed from the Approval inbox. Email and Teams defers are dismiss-only.`
      : status === "follow"
      ? `${selected.length} item${selected.length === 1 ? "" : "s"} marked Follow. No RSVP was sent; Mina keeps watching the invite and will flag any changes.`
      : status === "acknowledged"
      ? `${selected.length} focus block${selected.length === 1 ? "" : "s"} kept as-is on your calendar.`
      : status === "rejected" && selectedActionTypes.has("deadline-block")
      ? `${selected.length} item${selected.length === 1 ? "" : "s"} rejected. Tilly is cancelling the auto-created calendar block(s).`
      : `${selected.length} approval decision${selected.length === 1 ? "" : "s"} sent. The item was removed from the inbox; RSVP/follow-up work is queued and will update live here.`;
  }
  render();
  await loadState();
  setTimeout(() => {
    transientStatus = "";
    renderChatStatus();
  }, 8000);
  setDecisionButtonsDisabled(false);
}

async function submitApprovalFeedback(event) {
  event.preventDefault();
  if (!pendingApprovalDecision) return;
  stopGuidanceDictation();
  $("submitApprovalFeedbackBtn").disabled = true;
  try {
    await decideSelectedApprovals(pendingApprovalDecision, privacyControlValue($("approvalFeedbackText")).trim());
    $("approvalFeedbackDialog").close();
    pendingApprovalDecision = "";
    pendingApprovalIds = [];
  } catch (error) {
    transientStatus = `Approval decision failed: ${error.message}. Nothing was changed; try again or ask Major to inspect the blocker.`;
    renderChatStatus();
    console.error(error);
  } finally {
    $("submitApprovalFeedbackBtn").disabled = false;
    setDecisionButtonsDisabled(false);
  }
}

document.addEventListener("click", async (event) => {
  const groupActionBtn = event.target.closest("[data-group-action]");
  if (groupActionBtn) {
    const groupKey = privacyAttribute(groupActionBtn, "data-group-key");
    const ids = selectedApprovalIds(groupKey);
    if (!ids.length) {
      alert("Select at least one item in this group first.");
      return;
    }
    openApprovalFeedback(privacyAttribute(groupActionBtn, "data-group-action"), ids);
    return;
  }
  const approvalButton = event.target.closest("[data-approval]");
  if (approvalButton) {
    await api(`/api/approvals/${privacyAttribute(approvalButton, "data-approval")}`, {
      method: "POST",
      body: JSON.stringify({ status: privacyAttribute(approvalButton, "data-decision") })
    });
    await loadState();
    return;
  }
  const threadButton = event.target.closest("[data-thread]");
  if (threadButton) {
    activeThreadId = privacyAttribute(threadButton, "data-thread");
    renderThreadContext();
    $("chatMessage").focus();
  }
});

document.addEventListener("change", (event) => {
  const selectAll = event.target.closest("[data-group-selectall]");
  if (selectAll) {
    const groupKey = privacyAttribute(selectAll, "data-group-selectall");
    approvalGroupItems(groupKey).forEach((a) => {
      if (selectAll.checked) selectedApprovals.add(a.id);
      else selectedApprovals.delete(a.id);
    });
    document.querySelectorAll(`[data-approval-check][data-group="${groupKey}"]`).forEach((checkbox) => {
      checkbox.checked = selectAll.checked;
    });
    selectAll.indeterminate = false;
    return;
  }
  const itemCheck = event.target.closest("[data-approval-check]");
  if (itemCheck) {
    const id = privacyAttribute(itemCheck, "data-approval-check");
    if (itemCheck.checked) selectedApprovals.add(id);
    else selectedApprovals.delete(id);
    syncSelectAllStates();
  }
});

$("chatForm").addEventListener("submit", sendChat);
$("newThreadBtn").addEventListener("click", () => {
  activeThreadId = "";
  renderThreadContext();
});
$("attentionBtn").addEventListener("click", attentionMajor);

// --- Privacy controls (P2-F) + local token (P1-A) ----------------------------------------------

function setPrivacyStatus(text, isError = false) {
  const el = $("privacyStatus");
  if (!el) return;
  el.textContent = text;
  el.className = isError ? "career-status err" : "career-status";
}

async function exportAllData() {
  setPrivacyStatus("Building your export…");
  try {
    const res = await fetch("/api/export", { headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const stamp = new Date().toISOString().slice(0, 10);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `daily-flow-export-${stamp}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setPrivacyStatus("Export downloaded.");
  } catch (err) {
    setPrivacyStatus(`Could not export: ${err.message}`, true);
  }
}

async function resetAllData() {
  const ok = window.confirm(
    "Delete ALL of your private data?\n\n" +
    "This permanently removes every job, result, approval, inbox signal, chat message, " +
    "activity-log entry, work-ledger entry, and sweep record on this machine.\n\n" +
    "Your settings and your team roster are kept. This cannot be undone — export first if you want a copy."
  );
  if (!ok) return;
  setPrivacyStatus("Deleting your private data…");
  try {
    const data = await api("/api/reset", { method: "POST", body: "{}" });
    const total = Object.values(data.cleared || {}).reduce((sum, n) => sum + n, 0);
    setPrivacyStatus(`Deleted ${total} record(s). Your settings and team are unchanged.`);
    await loadState();
  } catch (err) {
    setPrivacyStatus(`Could not reset: ${err.message}`, true);
  }
}

function initTokenUi() {
  const input = $("localTokenInput");
  const saveBtn = $("saveTokenBtn");
  const clearBtn = $("clearTokenBtn");
  if (!input || !saveBtn) return;
  input.value = localToken();
  saveBtn.addEventListener("click", () => {
    setLocalToken(input.value.trim());
    setPrivacyStatus(input.value.trim() ? "Token saved for this browser." : "Token cleared.");
    loadState().catch(() => {});
    loadRuntimeInventory();
  });
  if (clearBtn) clearBtn.addEventListener("click", () => {
    input.value = "";
    setLocalToken("");
    setPrivacyStatus("Token cleared.");
  });
}

const _exportBtn = document.getElementById("exportDataBtn");
if (_exportBtn) _exportBtn.addEventListener("click", exportAllData);
const _resetBtn = document.getElementById("resetDataBtn");
if (_resetBtn) _resetBtn.addEventListener("click", resetAllData);
initTokenUi();
const _addEmpBtn = document.getElementById("addEmployeeBtn");
if (_addEmpBtn) _addEmpBtn.addEventListener("click", openAddEmployee);
const _addEmpClose = document.getElementById("addEmpCloseBtn");
if (_addEmpClose) _addEmpClose.addEventListener("click", closeAddEmployee);
$("approvalFeedbackForm").addEventListener("submit", submitApprovalFeedback);
$("cancelApprovalFeedbackBtn").addEventListener("click", () => { stopGuidanceDictation(); $("approvalFeedbackDialog").close(); });

// --- Voice dictation for the "Optional guidance for Major" textarea ---------------------------
// Uses the browser's built-in Web Speech API (SpeechRecognition / webkitSpeechRecognition).
// Audio never leaves the browser and is never sent to or stored by the app backend; only the
// recognized final transcript text is inserted into the existing guidance textarea, same as if
// the user had typed it.
let guidanceRecognition = null;
let guidanceRecognizing = false;

function guidanceSpeechCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function setGuidanceMicStatus(text, isError) {
  const el = $("approvalFeedbackMicStatus");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("error", !!isError);
}

function setGuidanceMicRecordingUi(recording) {
  const btn = $("approvalFeedbackMicBtn");
  if (!btn) return;
  guidanceRecognizing = recording;
  btn.classList.toggle("recording", recording);
  btn.setAttribute("aria-pressed", recording ? "true" : "false");
  btn.setAttribute("aria-label", recording ? "Stop voice dictation" : "Start voice dictation");
  btn.title = recording ? "Stop dictation" : "Dictate guidance (uses your browser's speech recognition)";
  btn.textContent = recording ? "⏺ Stop" : "🎤 Dictate";
}

function stopGuidanceDictation() {
  if (guidanceRecognition && guidanceRecognizing) {
    try { guidanceRecognition.stop(); } catch (err) { /* ignore */ }
  }
}

function insertGuidanceText(finalText) {
  const field = $("approvalFeedbackText");
  if (!field || !finalText || field.readOnly) return;
  const existing = field.value;
  const needsSpace = existing && !/\s$/.test(existing);
  field.value = existing + (needsSpace ? " " : "") + finalText;
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

function toggleGuidanceDictation() {
  const Ctor = guidanceSpeechCtor();
  if (!Ctor) {
    setGuidanceMicStatus("Voice dictation isn't supported in this browser. Try Edge or Chrome, or type your guidance instead.", true);
    return;
  }
  if (guidanceRecognizing) {
    stopGuidanceDictation();
    return;
  }
  const recognition = new Ctor();
  guidanceRecognition = recognition;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = (navigator.language || "en-US");

  recognition.onstart = () => {
    setGuidanceMicRecordingUi(true);
    setGuidanceMicStatus("Listening… speak your guidance, then click Stop.");
  };
  recognition.onresult = (event) => {
    let finalText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) finalText += result[0].transcript;
    }
    if (finalText.trim()) insertGuidanceText(finalText.trim());
  };
  recognition.onerror = (event) => {
    const reason = event && event.error ? event.error : "unknown error";
    if (reason === "no-speech") {
      setGuidanceMicStatus("No speech detected. Click Dictate to try again.");
    } else if (reason === "not-allowed" || reason === "service-not-allowed") {
      setGuidanceMicStatus("Microphone access was blocked. Allow microphone access to use dictation, or type your guidance instead.", true);
    } else {
      setGuidanceMicStatus(`Dictation error (${reason}). You can keep typing your guidance instead.`, true);
    }
  };
  recognition.onend = () => {
    setGuidanceMicRecordingUi(false);
    guidanceRecognition = null;
  };
  try {
    recognition.start();
  } catch (err) {
    setGuidanceMicStatus("Couldn't start dictation. Try again or type your guidance instead.", true);
    setGuidanceMicRecordingUi(false);
    guidanceRecognition = null;
  }
}

const _guidanceMicBtn = document.getElementById("approvalFeedbackMicBtn");
if (_guidanceMicBtn) {
  if (!guidanceSpeechCtor()) {
    _guidanceMicBtn.title = "Voice dictation isn't supported in this browser (needs Chrome/Edge)";
  }
  _guidanceMicBtn.addEventListener("click", toggleGuidanceDictation);
}

async function updateEmployee(name, payload) {
  try {
    await api(`/api/employees/${encodeURIComponent(name)}`, { method: "POST", body: JSON.stringify(payload) });
    await loadState();
  } catch (err) {
    transientStatus = `Could not update ${name}: ${err.message}`;
    render();
  }
}
document.addEventListener("change", (event) => {
  const sel = event.target.closest("[data-emp-trust]");
  if (sel) updateEmployee(privacyAttribute(sel, "data-emp-trust"), { trustLevel: sel.value });
});
document.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-emp-toggle]");
  if (!btn) return;
  const enabled = privacyAttribute(btn, "data-enabled") === "true";
  updateEmployee(privacyAttribute(btn, "data-emp-toggle"), { enabled: !enabled });
});

function renderCivilianBadge() {
  // Adoption Ripple moved to the Impact Ledger; the cockpit only keeps the live
  // "+N civilians working" indicator that used to live alongside it.
  const badge = $("civilianBadge");
  if (!badge) return;
  const civ = (state.jobs || []).filter((j) => j.type === "civilian" && ["queued", "in_progress"].includes(j.status)).length;
  if (civ) { badge.hidden = false; badge.textContent = `+${civ} civilians working`; }
  else { badge.hidden = true; badge.textContent = ""; }
}

function gInfo(text) {
  // Small ⓘ affordance with an accessible tooltip (hover, tap, and keyboard focus).
  const t = escapeHtml(text);
  return `<span class="g-info" tabindex="0" role="note" aria-label="${t}">i<span class="g-tip" role="tooltip">${t}</span></span>`;
}

function renderGuardrails() {
  const g = state.guardrails;
  const sumEl = $("guardrailsSummary");
  const bodyEl = $("guardrailsBody");
  if (!g || !sumEl || !bodyEl) return;
  const a = g.audit || {};
  const lc = g.levelCounts || {};
  sumEl.textContent = `${a.outwardSends || 0} outward sends · ${a.autonomousActions || 0} autonomous actions · classified always pauses`;
  const li = (items) => (items || []).map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  const paused = (g.pausedEmployees || []);
  const levelRows = (g.levels || []).map((e) => {
    const cls = `trust-${e.level}`;
    const fixed = e.mode === "fixed" ? " · fixed" : "";
    const pausedTag = e.enabled ? "" : " · paused";
    return `<div class="g-level-row"><span>${escapeHtml(e.name)}</span><span class="trust-badge ${cls}">${escapeHtml((TRUST_NAME[e.level] || e.level))}${fixed}${pausedTag}</span></div>`;
  }).join("");
  const showReset = (g.adjustableAtAutonomous || []).length || (g.levels || []).some((e) => e.mode === "adjustable" && e.level !== "draft");
  bodyEl.innerHTML = `
    <p class="g-cardinal">Your level controls how far each employee goes. Draft = you send · Assist = you approve, it sends · Autonomous = it sends. Confidential / Highly-Confidential external sends always pause for you.</p>
    <div class="g-stats">
      <div class="g-stat"><span class="g-num">${a.outwardSends || 0}</span><span class="g-lab">outward sends ${gInfo("Times an employee sent something to other people (email, Teams, or an RSVP) — either on its own or after you approved it.")}</span></div>
      <div class="g-stat"><span class="g-num">${a.autonomousActions || 0}</span><span class="g-lab">autonomous actions ${gInfo("Actions an employee completed on its own, without pausing for you, within the trust level you granted it.")}</span></div>
      <div class="g-stat"><span class="g-num">${(lc.draft || 0)}/${(lc.assist || 0)}/${(lc.autonomous || 0)}</span><span class="g-lab">draft / assist / autonomous ${gInfo("How many of your employees are currently set to each trust level.")}</span></div>
      <div class="g-stat"><span class="g-num">${a.mutedByMemory || 0}</span><span class="g-lab">muted by memory ${gInfo("Items you rejected or deferred that the team is holding back so they don't keep re-surfacing (reject lasts 14 days, defer 3). Manage them in the 🔕 muted bar under the Approval inbox.")}</span></div>
    </div>
    <div class="g-cols">
      <div><h4 class="g-h">Each employee's level</h4><div class="g-levels">${levelRows}</div>
        ${showReset ? `<button type="button" class="btn g-reset" id="allToDraftBtn">Set everyone back to Draft</button>` : ""}
      </div>
      <div><h4 class="g-h ok">Always automatic</h4><ul>${li((g.policy || {}).alwaysAutomatic)}</ul>
        <h4 class="g-h warn" style="margin-top:10px;">Always pauses for you</h4><ul>${li((g.policy || {}).alwaysPausesForYou)}</ul></div>
    </div>
    <div class="g-foot">
      <span>🔒 ${escapeHtml(g.retention || "")}</span>
      <span>🏷️ ${escapeHtml(g.sensitivity || "")}</span>
      ${paused.length ? `<span>⏸️ Paused: ${escapeHtml(paused.join(", "))}</span>` : ""}
    </div>`;
  const resetBtn = $("allToDraftBtn");
  if (resetBtn) resetBtn.addEventListener("click", async () => {
    try { await api("/api/team/all-to-draft", { method: "POST", body: "{}" }); await loadState(); }
    catch (err) { transientStatus = `Could not reset: ${err.message}`; render(); }
  });
}

function renderDecisionMemory() {  const bar = $("memoryBar");
  if (!bar) return;
  const mem = state.decisionMemory || { count: 0, items: [] };
  if (!mem.count) { bar.hidden = true; bar.innerHTML = ""; return; }
  bar.hidden = false;
  // Preserve expand/collapse across the periodic state refresh: a re-rendered <details> defaults to
  // closed, which made the panel auto-collapse every ~15s. Carry the live open state forward (or the
  // saved one on first paint after a reload).
  const existing = bar.querySelector(".memory-details");
  let openState;
  if (existing) {
    openState = existing.open;
  } else {
    try { openState = localStorage.getItem("df-muted-open") === "1"; } catch (e) { openState = false; }
  }
  const items = (mem.items || []).map((m) => `
    <li>
      <span class="mem-tag mem-${escapeHtml(m.decision)}">${escapeHtml(m.decision)}</span>
      <span class="mem-subj">${escapeHtml(m.subject || "(no subject)")}</span>
      <span class="mem-from">${escapeHtml(m.sender || "")}</span>
      <button type="button" class="btn tiny" data-unmute="${escapeHtml(m.contentKey)}">Un-mute</button>
    </li>`).join("");
  bar.innerHTML = `
    <details class="memory-details"${openState ? " open" : ""}>
      <summary>🔕 ${mem.count} muted — already-dismissed items hidden from new cards
        <button type="button" class="mem-clear" data-clear-all="1">Clear all</button>
      </summary>
      <ul class="mem-list">${items}</ul>
    </details>`;
  const details = bar.querySelector(".memory-details");
  if (details) {
    details.addEventListener("toggle", () => {
      try { localStorage.setItem("df-muted-open", details.open ? "1" : "0"); } catch (e) {}
    });
  }
}
document.addEventListener("click", async (event) => {
  const un = event.target.closest("[data-unmute]");
  const all = event.target.closest("[data-clear-all]");
  if (!un && !all) return;
  event.preventDefault();
  event.stopPropagation();
  try {
    const body = all ? { clearAll: true } : { contentKey: privacyAttribute(un, "data-unmute") };
    await api("/api/decision-memory/clear", { method: "POST", body: JSON.stringify(body) });
    await loadState();
  } catch (err) {
    transientStatus = `Could not update muted items: ${err.message}`;
    render();
  }
});

function applyTheme(name) {
  document.documentElement.setAttribute("data-theme", name);
  try { localStorage.setItem("df-theme", name); } catch (e) {}
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.classList.toggle("active", privacyAttribute(b, "data-theme-set") === name));
}

(function initThemePicker() {
  const btn = document.getElementById("themeBtn");
  const menu = document.getElementById("themeMenu");
  if (!btn || !menu) return;
  const current = document.documentElement.getAttribute("data-theme") || "light";
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.classList.toggle("active", privacyAttribute(b, "data-theme-set") === current));
  const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = menu.hidden;
    menu.hidden = !willOpen;
    btn.setAttribute("aria-expanded", String(willOpen));
  });
  menu.addEventListener("click", (event) => {
    const option = event.target.closest("[data-theme-set]");
    if (!option) return;
    applyTheme(privacyAttribute(option, "data-theme-set"));
    close();
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && event.target !== btn) close();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
})();

function setupCollapsibles() {
  // Persisted collapse for any <section data-collapsible id="..."> with a .collapse-toggle.
  document.querySelectorAll("section[data-collapsible]").forEach((sec) => {
    const id = sec.id;
    const toggle = sec.querySelector(".collapse-toggle");
    if (!id || !toggle) return;
    const key = `df-collapse-${id}`;
    let collapsed = false;
    try { collapsed = localStorage.getItem(key) === "1"; } catch (e) {}
    const apply = () => {
      sec.classList.toggle("collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.title = collapsed ? "Expand" : "Collapse";
    };
    apply();
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      collapsed = !collapsed;
      try { localStorage.setItem(key, collapsed ? "1" : "0"); } catch (e) {}
      apply();
    });
  });
}
setupCollapsibles();

// "Hide company names" / "Hide person names" toggles, next to the Results and drafts prepared
// section header. Two independent, purely client-side masking preferences -- persisted locally,
// default off, each re-renders drafts on change so turning one off immediately restores real text.
(function setupPrivacyToggles() {
  const companyToggle = document.getElementById("hideCompanyNamesToggle");
  if (companyToggle) {
    companyToggle.checked = hideCompanyNames;
    companyToggle.addEventListener("change", async () => {
      hideCompanyNames = companyToggle.checked;
      try { localStorage.setItem(HIDE_COMPANY_NAMES_KEY, hideCompanyNames ? "1" : "0"); } catch (e) {}
      if (hideCompanyNames) await prepareCompanyMask();
      else restoreUnmaskedDashboard();
    });
  }
  const personToggle = document.getElementById("hidePersonNamesToggle");
  if (personToggle) {
    personToggle.checked = hidePersonNames;
    personToggle.addEventListener("change", () => {
      hidePersonNames = personToggle.checked;
      try { localStorage.setItem(HIDE_PERSON_NAMES_KEY, hidePersonNames ? "1" : "0"); } catch (e) {}
      renderDrafts();
    });
  }
})();

// Owned-account editor: paste/persist the account/company names the user owns, used only to
// scope work already tagged with a confirmed customer/account name into
// account_neutral / owned_account / unowned_account / uncertain_account (see classify_account_scope
// server-side). This never guesses a company name from capitalization -- the textarea is the sole
// source of "owned" truth, and the scope summary below only ever reflects confirmed accounts
// already present in the impact ledger's highlights.
let ownedAccountsLoadedInto = null;

function renderOwnedAccounts() {
  const input = $("ownedAccountsInput");
  const countEl = $("ownedAccountsCount");
  const summaryEl = $("ownedAccountsScopeSummary");
  const accounts = state?.ownedAccounts || { rawText: "", names: [] };
  // Only set .value from the server once (or after an explicit save), so it never clobbers text
  // the user is actively typing on the next 15s poll/SSE refresh.
  if (input && ownedAccountsLoadedInto !== accounts.updatedAt) {
    input.value = hideCompanyNames && companyMaskReady
      ? maskCompanyNames(accounts.rawText || "")
      : (accounts.rawText || "");
    input.disabled = hideCompanyNames;
    ownedAccountsLoadedInto = accounts.updatedAt || "";
  }
  if (countEl) {
    const n = (accounts.names || []).length;
    countEl.textContent = n ? `${n} account${n === 1 ? "" : "s"} saved.` : "No owned accounts saved yet.";
  }
  if (summaryEl) {
    const highlights = state?.impactLedger?.highlights || [];
    const scoped = highlights.filter((item) => item.accountScope && item.accountScope.scope !== "account_neutral");
    const counts = { owned_account: 0, unowned_account: 0, uncertain_account: 0 };
    for (const item of scoped) {
      const scope = item.accountScope.scope;
      if (scope in counts) counts[scope] += 1;
    }
    summaryEl.innerHTML = scoped.length
      ? `Current results and drafts: <strong>${counts.owned_account}</strong> owned, <strong>${counts.unowned_account}</strong> unowned (default lowest priority unless raised), <strong>${counts.uncertain_account}</strong> uncertain (no owned-account list configured). Hover a result for the exact reason.`
      : "No results currently carry confirmed account/customer context to scope.";
  }
}

async function saveOwnedAccounts() {
  const input = $("ownedAccountsInput");
  const status = $("ownedAccountsStatus");
  if (!input) return;
  try {
    const result = await api("/api/owned-accounts", {
      method: "POST",
      body: JSON.stringify({ rawText: input.value })
    });
    ownedAccountsLoadedInto = result.ownedAccounts?.updatedAt || "";
    if (status) {
      status.textContent = `Saved ✓ — ${(result.ownedAccounts?.names || []).length} account(s) recognized. Stored locally only.`;
      status.className = "career-status ok";
    }
    await loadState();
  } catch (err) {
    if (status) {
      status.textContent = `Could not save: ${err.message}`;
      status.className = "career-status err";
    }
  }
}

(function setupOwnedAccounts() {
  const btn = document.getElementById("saveOwnedAccountsBtn");
  if (btn) {
    btn.disabled = hideCompanyNames;
    btn.addEventListener("click", saveOwnedAccounts);
  }
})();

loadState();
loadRuntimeInventory();
let events = null;
if ("EventSource" in window) {
  // EventSource cannot send headers, so the local token (when one is set) rides along as a
  // same-origin query param. Without a token this is exactly the old URL.
  const _t = localToken();
  events = new EventSource("/api/events" + (_t ? `?token=${encodeURIComponent(_t)}` : ""));
  events.onmessage = () => loadState();
  events.onerror = () => setTimeout(loadState, 1000);
  window.addEventListener("pagehide", () => events.close());
}
setInterval(loadState, 15000);
