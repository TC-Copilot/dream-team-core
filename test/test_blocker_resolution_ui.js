#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "app", "static", "index.html"), "utf8");
const js = fs.readFileSync(path.join(root, "app", "static", "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "app", "static", "styles.css"), "utf8");

const required = [
  [html, 'id="blockerResolveDialog"'],
  [html, 'id="blockerJobPicker"'],
  [html, 'id="blockerResolveNote"'],
  [html, 'id="blockerResolveActions"'],
  [js, "data-blocker-employee"],
  [js, "data-blocker-job"],
  [js, "data-blocker-resolution"],
  [js, "/resolve-blocker"],
  [js, "activityTrail"],
  [css, ".blocker-resolve-dialog"],
];

for (const [source, token] of required) {
  if (!source.includes(token)) {
    console.error(`[FAIL] missing blocker UI contract: ${token}`);
    process.exit(1);
  }
}
console.log("[ok] blocker resolution UI contract");
