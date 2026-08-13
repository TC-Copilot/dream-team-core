(function exposePrivacyMask(globalScope) {
  function canonicalCompanyKey(name) {
    return String(name || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLocaleLowerCase();
  }

  function companyNameVariants(name) {
    const normalized = String(name || "").normalize("NFKC").replace(/\s+/g, " ").trim();
    if (!normalized) return [];
    const tokens = normalized.split(" ");
    const variants = new Set([normalized, encodeURI(normalized), encodeURIComponent(normalized)]);
    if (tokens.length > 1) {
      for (const separator of ["-", "_", "+", "%20"]) variants.add(tokens.join(separator));
    }
    return Array.from(variants);
  }

  function buildCompanyAliasMetadata(names, savedMetadata = {}) {
    const metadata = { ...savedMetadata };
    const canonicalNames = Array.from(new Set((names || []).map(canonicalCompanyKey).filter(Boolean))).sort();
    const usedNumbers = Object.values(metadata)
      .map((alias) => Number(String(alias).match(/^Company (\d+)$/)?.[1] || 0))
      .filter(Boolean);
    let nextNumber = Math.max(0, ...usedNumbers) + 1;
    for (const key of canonicalNames) {
      if (!/^Company \d+$/.test(metadata[key] || "")) metadata[key] = `Company ${nextNumber++}`;
    }
    return metadata;
  }

  function buildCompanyReplacementEntries(names, metadata) {
    const entries = [];
    for (const name of names || []) {
      const alias = metadata[canonicalCompanyKey(name)];
      if (!alias) continue;
      for (const variant of companyNameVariants(name)) {
        entries.push([variant.toLocaleLowerCase(), alias]);
      }
    }
    return entries.sort((a, b) => b[0].length - a[0].length);
  }

  function maskWithEntries(text, entries) {
    let output = String(text ?? "");
    for (const [variant, alias] of entries || []) {
      const pattern = variant.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const startsWithWord = /^[\p{L}\p{N}]/u.test(variant);
      const endsWithWord = /[\p{L}\p{N}]$/u.test(variant);
      const prefix = startsWithWord ? "(^|[^\\p{L}\\p{N}])" : "";
      const suffix = endsWithWord ? "(?=$|[^\\p{L}\\p{N}])" : "";
      output = output.replace(new RegExp(`${prefix}${pattern}${suffix}`, "giu"), (match, before = "") => {
        return `${startsWithWord ? before : ""}${alias}`;
      });
    }
    return output;
  }

  const api = {
    canonicalCompanyKey,
    companyNameVariants,
    buildCompanyAliasMetadata,
    buildCompanyReplacementEntries,
    maskWithEntries
  };
  globalScope.DailyFlowPrivacy = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
