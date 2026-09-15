const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const index = fs.readFileSync(path.join(root, "app", "static", "index.html"), "utf8");
const page = fs.readFileSync(path.join(root, "app", "static", "customers.html"), "utf8");
const app = fs.readFileSync(path.join(root, "app", "app.py"), "utf8");
const serviceWorker = fs.readFileSync(path.join(root, "app", "static", "sw.js"), "utf8");
const privacy = require(path.join(root, "app", "static", "privacy-mask.js"));

// --- reachability -----------------------------------------------------------------------------
assert.ok(index.includes('href="customers.html"'), "dashboard links to the customers view");
assert.ok(serviceWorker.includes('"/customers.html"'), "customers view is available offline");

// --- loading, empty, and error states ---------------------------------------------------------
assert.ok(page.includes('aria-live="polite"'), "loading and result changes are announced");
assert.ok(page.includes("Loading customer profiles"), "page has a loading state");
assert.ok(page.includes("No customer profiles yet"), "page has an empty state for profiles");
assert.ok(page.includes('role="alert"'), "page has an accessible error state");
assert.ok(page.includes('aria-busy'), "busy state is exposed to assistive technology");

// --- the integrity boundary, stated in the interface ------------------------------------------
assert.ok(
  page.includes("Preferences awaiting your confirmation"),
  "the confirmation queue is a first-class section, not buried in a profile",
);
assert.ok(
  page.includes("proposed — not applied to any draft"),
  "a proposal is labelled as not yet influencing generation",
);
assert.ok(
  page.includes("What Casey observed:"),
  "a proposal shows the observation it came from so the user can judge it",
);
assert.ok(
  /data-confirm=/.test(page) && /data-reject=/.test(page) && /data-edit=/.test(page),
  "the queue offers confirm, edit, and reject",
);
assert.ok(
  page.includes("/confirm"),
  "confirming a proposal goes through the explicit confirm endpoint",
);
assert.ok(
  page.includes("Nothing waiting. Every recorded preference has been confirmed by you."),
  "the queue has an empty state",
);

// A preference the user types is theirs, so it is confirmed on write. Anything the page sends
// must carry actor=user — the server refuses to let a non-user actor confirm.
assert.ok(
  /provenance: "user", prefsStatus: "confirmed", actor: "user"/.test(page),
  "user-entered preferences are written as confirmed by the user",
);

// --- the app never acts -----------------------------------------------------------------------
assert.ok(
  page.includes("the app never acts"),
  "the page says plainly that storing a profile contacts nobody",
);
assert.ok(
  page.includes("Nothing was sent to this customer."),
  "saving a profile reassures the user that nothing was sent",
);

// --- privacy ----------------------------------------------------------------------------------
assert.ok(page.includes("df-hide-company-names"), "company-name privacy preference is respected");
assert.ok(page.includes("df-hide-person-names"), "person-name privacy preference is respected");
assert.ok(
  page.includes("Logo hidden in demo mode"),
  "logos are suppressed in demo mode, since a logo identifies a customer as surely as its name",
);
assert.ok(
  page.includes("Private to this machine — never shared"),
  "the page carries the private-data badge",
);

// --- auth and asset fetching ------------------------------------------------------------------
assert.ok(page.includes("dailyflow_token"), "private reads carry the local auth token");
assert.ok(
  /\?token=\$\{encodeURIComponent\(token\)\}/.test(page),
  "asset <img> tags pass the token in the query string, because an image cannot set a header",
);
assert.ok(
  app.includes('"/api/customer-assets/"'),
  "the server knows about the query-string token path for assets",
);

// --- asset guardrails are surfaced, not just enforced -----------------------------------------
assert.ok(
  page.includes('accept="image/png,image/jpeg,image/svg+xml"'),
  "the file picker offers only the allowed MIME types",
);
assert.ok(
  page.includes("512 KB each"),
  "the per-asset size cap is stated before the user picks a file",
);
assert.ok(
  page.includes("never included in a shared package"),
  "the page states that assets stay on this machine",
);

// --- routes the page depends on actually exist ------------------------------------------------
for (const route of [
  '"/api/customer-profiles"',
  '"/api/customer-contacts',
  '"/api/customer-assets',
]) {
  assert.ok(app.includes(route), `server exposes ${route}`);
}
assert.ok(
  app.includes('"/api/customer-profiles"') && app.includes('"/api/customer-brief"'),
  "profile and brief endpoints are both registered",
);

// --- email masking ----------------------------------------------------------------------------
assert.strictEqual(
  privacy.maskEmailAddress("priya@contoso.example.com", "Person 1", true),
  "person-1@company.example.com",
  "both halves of an address are aliased when both switches are on",
);
assert.strictEqual(
  privacy.maskEmailAddress("priya@contoso.example.com", "Person 1", false),
  "person-1@contoso.example.com",
  "hiding people does not hide the company",
);
assert.strictEqual(
  privacy.maskEmailAddress("priya@contoso.example.com", "", true),
  "priya@company.example.com",
  "hiding companies does not unmask the person's local part",
);
assert.strictEqual(privacy.maskEmailAddress("", "Person 1", true), "", "an empty address stays empty");
assert.ok(
  !privacy.maskEmailAddress("priya@contoso.example.com", "Person 1", true).includes("contoso"),
  "no fragment of the real domain survives masking",
);

console.log("customers UI contract checks passed");
