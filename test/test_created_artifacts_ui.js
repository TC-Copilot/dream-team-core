const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const index = fs.readFileSync(path.join(root, "app", "static", "index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "app", "static", "app.js"), "utf8");

assert.ok(index.includes("Documents created for you"), "dashboard has the created-documents report");
assert.ok(index.includes('id="createdArtifacts"'), "dashboard has a report container");
assert.ok(app.includes("function renderCreatedArtifacts()"), "dashboard renders created artifacts");
assert.ok(app.includes("state?.createdArtifacts || []"), "report reads the state artifact registry");
assert.ok(app.includes("function artifactOpenHref(artifact)"), "report derives a local open link");
assert.ok(
  app.includes("linkHref(artifact.oneDrivePath || artifact.href)"),
  "report prefers a file URI from the persisted local path",
);
assert.ok(app.includes("Open file"), "each artifact has an open-file action");
assert.ok(
  app.includes("No documents have been created for you yet."),
  "report has an explicit empty state",
);
assert.ok(!app.includes("data-created-artifact-dismiss"), "report is a durable read-only index");

console.log("[ok] created-artifact report UI contract");
