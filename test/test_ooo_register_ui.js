const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const index = fs.readFileSync(path.join(root, "app", "static", "index.html"), "utf8");
const page = fs.readFileSync(path.join(root, "app", "static", "ooo-register.html"), "utf8");
const app = fs.readFileSync(path.join(root, "app", "app.py"), "utf8");
const serviceWorker = fs.readFileSync(path.join(root, "app", "static", "sw.js"), "utf8");
const timeline = require(path.join(root, "app", "static", "ooo-timeline.js"));

assert.ok(index.includes('href="ooo-register.html"'), "dashboard links to OOO register");
assert.ok(page.includes('fetch(`/api/ooo?${params}`'), "page reads the provider-neutral OOO API");
assert.ok(page.includes("localTokenHeaders()"), "private OOO reads carry the local auth token");
assert.ok(page.includes('aria-live="polite"'), "loading and result changes are announced");
assert.ok(page.includes("Loading out-of-office dates"), "page has a loading state");
assert.ok(page.includes("No out-of-office periods overlap"), "page has an empty state");
assert.ok(page.includes('role="alert"'), "page has an accessible error state");
assert.ok(page.includes('type="date" id="fromDate"'), "page exposes a start-date filter");
assert.ok(page.includes('type="date" id="toDate"'), "page exposes an end-date filter");
assert.ok(page.includes("df-hide-person-names"), "person-name privacy preference is respected");
assert.ok(serviceWorker.includes('"/ooo-timeline.js"'), "timeline renderer is available offline");
assert.ok(page.includes('aria-label="${esc(label)}"'), "timeline bars expose evidence to keyboard and assistive technology");
assert.ok(page.includes('title="${esc(label)}"'), "timeline bars expose evidence on hover");
assert.ok(app.includes('if parsed.path == "/api/ooo":'), "GET and POST OOO routes are wired");
assert.ok(
  /if parsed\.path in \{[\s\S]*"\/api\/ooo"[\s\S]*\} and not self\.require_connector_auth\(\):/.test(app),
  "OOO ingest requires connector auth",
);

const data = {
  people: [
    {
      personName: "Zoe Partner",
      periods: [{
        startDate: "2026-09-01",
        endDate: "2026-09-03",
        status: "confirmed",
        confidence: 0.95,
        evidence: [{ sourceType: "calendar", sourceLabel: "Calendar: Out of office" }],
      }],
    },
    {
      personName: "Amy Customer",
      periods: [{
        startDate: "2026-09-02",
        endDate: "2026-09-02",
        status: "confirmed",
        confidence: 0.9,
        evidence: [{ sourceType: "email", sourceLabel: "Email: automatic reply" }],
      }],
    },
  ],
};
const model = timeline.buildModel(data, "2026-09-01", "2026-09-07");
assert.deepStrictEqual(
  model.dates.map(date => date.dayLabel),
  ["Tu", "W", "Th", "F", "M"],
  "selected date range renders business-day headers grouped across weeks",
);
assert.deepStrictEqual(
  model.people.map(person => person.personName),
  ["Amy Customer", "Zoe Partner"],
  "timeline rows are alphabetically ordered",
);
assert.deepStrictEqual(
  model.people[1].segments.map(segment => [segment.start, segment.span]),
  [[0, 3]],
  "a multi-day OOO period renders as one contiguous grid bar",
);
assert.strictEqual(
  model.people[0].segments[0].sources,
  "Email: automatic reply",
  "bar accessibility text retains its evidence source",
);
assert.deepStrictEqual(
  timeline.buildModel({ people: [] }, "2026-09-01", "2026-09-07").people,
  [],
  "empty API results remain empty for the existing empty-state renderer",
);

console.log("[ok] OOO register UI and route contract");
