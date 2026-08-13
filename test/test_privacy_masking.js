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
const encodedEntries = privacy.buildCompanyReplacementEntries(
  ["AT&T"],
  privacy.buildCompanyAliasMetadata(["AT&T"], {})
);
assert.equal(privacy.maskWithEntries("https://host/AT%26T/report", encodedEntries), "https://host/Company 1/report");

const stable = privacy.buildCompanyAliasMetadata(["A Datum", ...configured], metadata);
assert.equal(stable["contoso ltd"], "Company 1");
assert.equal(stable["a datum"], "Company 4");

console.log("privacy masking behavior: PASS");
