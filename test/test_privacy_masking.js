const assert = require("node:assert/strict");
const privacy = require("../app/static/privacy-mask.js");

const configured = ["Northwind Traders", "Contoso Ltd", "Fabrikam, Inc."];
const metadata = privacy.buildCompanyAliasMetadata(configured, {});
const entries = privacy.buildCompanyReplacementEntries(configured, metadata);
const source = {
  title: "Contoso Ltd renewal for Northwind Traders",
  href: "file:///Customers/Northwind-Traders/Contoso%20Ltd-plan.pptx",
  payload: { customer: "Contoso Ltd" }
};

assert.equal(metadata["contoso ltd"], "Company 1");
assert.equal(metadata["fabrikam, inc."], "Company 2");
assert.equal(metadata["northwind traders"], "Company 3");
assert.equal(
  privacy.maskWithEntries(source.title, entries),
  "Company 1 renewal for Company 3"
);
assert.equal(
  privacy.maskWithEntries(source.href, entries),
  "file:///Customers/Company 3/Company 1-plan.pptx"
);
assert.equal(source.payload.customer, "Contoso Ltd", "display masking must not mutate source payloads");
const shortEntries = privacy.buildCompanyReplacementEntries(
  ["Box", "US"],
  privacy.buildCompanyAliasMetadata(["Box", "US"], {})
);
assert.equal(privacy.maskWithEntries("checkbox status Box US", shortEntries), "checkbox status Company 1 Company 2");
assert.equal(privacy.maskWithEntries("Tell us about status", shortEntries), "Tell us about status");
const encodedEntries = privacy.buildCompanyReplacementEntries(
  ["AT&T"],
  privacy.buildCompanyAliasMetadata(["AT&T"], {})
);
assert.equal(privacy.maskWithEntries("https://host/AT%26T/report", encodedEntries), "https://host/Company 1/report");

const mriMetadata = privacy.buildCompanyAliasMetadata(["MRI SOFTWARE"], {});
const mriEntries = privacy.buildCompanyReplacementEntries(["MRI SOFTWARE"], mriMetadata);
const mriEmail = ["owner", "mrisoftware.com"].join("@");
assert.equal(
  privacy.maskWithEntries("MRI SOFTWARE review: MRI is not Microsoft or an mri scan.", mriEntries),
  "Company 1 review: Company 1 is not Microsoft or an mri scan."
);
assert.equal(
  privacy.maskWithEntries(`Contact ${mriEmail} or visit MRI/Software.`, mriEntries),
  "Contact owner@Company 1.com or visit Company 1."
);
assert.equal(
  privacy.maskWithEntries("MRI / Software; MRI   SOFTWARE; MRI.Software", mriEntries),
  "Company 1; Company 1; Company 1"
);
assert.equal(
  privacy.maskWithEntries("We arrived at T station.", encodedEntries),
  "We arrived at T station."
);
const punctuatedSuffixEntries = privacy.buildCompanyReplacementEntries(
  ["AT&T Inc."],
  privacy.buildCompanyAliasMetadata(["AT&T Inc."], {})
);
assert.equal(
  privacy.maskWithEntries("We arrived at T station.", punctuatedSuffixEntries),
  "We arrived at T station."
);

const stable = privacy.buildCompanyAliasMetadata(["A Datum", ...configured], metadata);
assert.equal(stable["contoso ltd"], "Company 1");
assert.equal(stable["a datum"], "Company 4");

const textarea = { closest: () => textarea };
const contenteditable = { closest: () => contenteditable };
const textInsideEditor = { nodeType: 3, parentElement: contenteditable };
const displayText = { nodeType: 3, parentElement: { closest: () => null } };
assert.equal(privacy.isInsideUserEditable(textarea), true);
assert.equal(privacy.isInsideUserEditable(textInsideEditor), true);
assert.equal(privacy.isInsideUserEditable(displayText), false);

const fs = require("node:fs");
const appSource = fs.readFileSync(require.resolve("../app/static/app.js"), "utf8");
assert.doesNotMatch(appSource, /element\.readOnly\s*=\s*true/);
assert.doesNotMatch(appSource, /element\.value\s*=\s*maskCompanyNames/);
assert.match(appSource, /privacyObserver\?\.takeRecords\(\)/);
assert.match(appSource, /function runWithoutPrivacyObservation/);
assert.match(appSource, /const preparation = \+\+companyMaskPreparation/);
assert.match(appSource, /rawPrivacyText\.delete\(node\)/);
assert.match(appSource, /rawPrivacyAttributes\.delete\(element\)/);
assert.match(appSource, /function isMaskablePrivacyAttribute/);
assert.match(appSource, /runWithoutPrivacyObservation\(restorePrivacySnapshotsNow\)/);
assert.match(appSource, /render\(\);\s*\n\s*if \(hideCompanyNames\) await prepareCompanyMask\(\)/);

console.log("privacy masking behavior: PASS");
