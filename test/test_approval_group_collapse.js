const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const app = fs.readFileSync(path.join(root, "app", "static", "app.js"), "utf8");
const styles = fs.readFileSync(path.join(root, "app", "static", "styles.css"), "utf8");

assert.ok(
  app.includes("const approvalGroupCollapsed = new Map();"),
  "approval category state is held for the current page session",
);
assert.ok(
  app.includes("let approvalGroupsInitialized = false;"),
  "initial render is distinguished from later polling renders",
);
assert.ok(
  app.includes(": !approvalGroupsInitialized;"),
  "categories present on initial load default to collapsed",
);
assert.ok(
  app.includes("approvalGroupCollapsed.set(groupKey, collapsed);"),
  "manual category changes update session state",
);
assert.ok(
  app.includes('container.querySelectorAll("[data-group-section]").forEach((section) => {'),
  "a polling re-render captures the live category state before replacing the DOM",
);
assert.ok(
  app.includes('const groupCollapse = event.target.closest("[data-group-collapse]");'),
  "native button clicks, including keyboard-generated clicks, toggle categories",
);
assert.ok(
  app.includes('aria-expanded="${String(!collapsed)}"'),
  "category toggles expose their initial expanded state",
);
assert.ok(
  app.includes('groupCollapse.setAttribute("aria-expanded", String(!collapsed));'),
  "manual toggles keep aria-expanded synchronized",
);
assert.ok(
  !app.includes("df-collapse-approval-group"),
  "category collapse state is not persisted across page loads",
);
assert.ok(
  styles.includes(".approval-group.collapsed > *:not(.approval-group-head)"),
  "collapsed categories hide only their body",
);

console.log("[ok] approval categories collapse once and preserve session choices");
