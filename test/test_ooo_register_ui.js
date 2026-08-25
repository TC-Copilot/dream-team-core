const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const index = fs.readFileSync(path.join(root, "app", "static", "index.html"), "utf8");
const page = fs.readFileSync(path.join(root, "app", "static", "ooo-register.html"), "utf8");
const app = fs.readFileSync(path.join(root, "app", "app.py"), "utf8");

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
assert.ok(app.includes('if parsed.path == "/api/ooo":'), "GET and POST OOO routes are wired");
assert.ok(
  /if parsed\.path in \{[\s\S]*"\/api\/ooo"[\s\S]*\} and not self\.require_connector_auth\(\):/.test(app),
  "OOO ingest requires connector auth",
);

console.log("[ok] OOO register UI and route contract");
