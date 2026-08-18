const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const indexSource = fs.readFileSync(path.join(__dirname, "../app/static/index.html"), "utf8");
const appSource = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");

const dialog = indexSource.match(/<dialog[^>]*id="ownedAccountsDialog"[\s\S]*?<\/dialog>/);
assert.ok(dialog, "owned-account editor should be rendered in a dialog");
assert.match(indexSource, /id="openOwnedAccountsBtn"[^>]*aria-haspopup="dialog"[^>]*aria-controls="ownedAccountsDialog"/);
assert.match(dialog[0], /id="ownedAccountsInput"/);
assert.match(dialog[0], /id="saveOwnedAccountsBtn"/);
assert.match(dialog[0], /Private to this machine — never shared/);
assert.doesNotMatch(indexSource, /<dialog[^>]*id="ownedAccountsDialog"[^>]*\sopen(?:\s|>)/);
assert.match(appSource, /openBtn\.addEventListener\("click",[\s\S]*?dialog\.showModal\(\)/);
assert.match(appSource, /closeBtn\.addEventListener\("click", \(\) => dialog\.close\(\)\)/);
assert.match(appSource, /cancelBtn\.addEventListener\("click", \(\) => dialog\.close\(\)\)/);

console.log("owned accounts modal UI: PASS");
