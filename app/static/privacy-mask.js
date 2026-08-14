(function exposePrivacyMask(globalScope) {
  const USER_EDITABLE_SELECTOR = "input, textarea, select, [contenteditable]:not([contenteditable='false'])";
  const GENERIC_SHORT_FORM_SUFFIXES = new Set([
    "co", "company", "corp", "corporation", "group", "inc", "incorporated", "llc", "ltd",
    "plc", "services", "software", "solutions", "systems", "technologies", "technology"
  ]);

  function canonicalCompanyKey(name) {
    return String(name || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLocaleLowerCase();
  }

  function companyNameVariants(name) {
    const normalized = String(name || "").normalize("NFKC").replace(/\s+/g, " ").trim();
    if (!normalized) return [];
    const terms = normalized.match(/[\p{L}\p{N}]+/gu) || [];
    const variants = new Map();
    const add = (text, options = {}) => {
      if (!text) return;
      const key = `${text.toLocaleLowerCase()}|${options.caseSensitive ? "case" : "fold"}|${options.domainOnly ? "domain" : "text"}|${options.flexibleTerms ? "flex" : "literal"}`;
      if (!variants.has(key)) variants.set(key, { text, ...options });
    };

    const singleUpperAcronym = terms.length === 1 && /^[A-Z0-9]{2,4}$/.test(terms[0]);
    add(normalized, { caseSensitive: singleUpperAcronym });
    const uriEncoded = encodeURI(normalized);
    const componentEncoded = encodeURIComponent(normalized);
    if (uriEncoded !== normalized) add(uriEncoded);
    if (componentEncoded !== normalized) add(componentEncoded);
    if (terms.length > 1) {
      const supportsSeparatorVariants = terms.every((term) => term.length >= 2) &&
        terms.some((term) => term.length >= 3);
      if (supportsSeparatorVariants) {
        add(normalized, { flexibleTerms: terms });
        for (const separator of ["-", "_", "+", "%20", "/", "%2F"]) add(terms.join(separator));
      }

      const initials = terms.map((term) => term[0]).join("");
      if (initials.length >= 3) add(initials.toLocaleUpperCase(), { caseSensitive: true });

      const shortTerms = terms.slice();
      while (shortTerms.length > 1 && GENERIC_SHORT_FORM_SUFFIXES.has(shortTerms.at(-1).toLocaleLowerCase())) {
        shortTerms.pop();
      }
      if (shortTerms.length < terms.length) {
        const shortForm = shortTerms.join(" ");
        const safeShortForm = shortTerms.length === 1 ||
          (shortTerms.every((term) => term.length >= 2) && shortTerms.some((term) => term.length >= 3));
        if (safeShortForm) {
          const caseSensitive = /^[A-Z0-9]{2,4}$/.test(shortForm);
          add(shortForm, { caseSensitive });
        }
      }

      add(terms.join(""), { domainOnly: true });
    }
    return Array.from(variants.values());
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
        entries.push([
          variant.caseSensitive ? variant.text : variant.text.toLocaleLowerCase(),
          alias,
          {
            caseSensitive: !!variant.caseSensitive,
            domainOnly: !!variant.domainOnly,
            flexibleTerms: variant.flexibleTerms || null
          }
        ]);
      }
    }
    return entries.sort((a, b) => b[0].length - a[0].length);
  }

  function maskWithEntries(text, entries) {
    let output = String(text ?? "");
    for (const [variant, alias, options = {}] of entries || []) {
      const escapePattern = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const pattern = options.flexibleTerms
        ? options.flexibleTerms.map(escapePattern).join("(?:\\s+|\\s*[/._&-]\\s*)")
        : escapePattern(variant);
      const startsWithWord = /^[\p{L}\p{N}]/u.test(variant);
      const endsWithWord = /[\p{L}\p{N}]$/u.test(variant);
      const prefix = startsWithWord ? "(^|[^\\p{L}\\p{N}])" : "";
      const suffix = options.domainOnly
        ? "(?=\\.(?:[a-z0-9-]+\\.)*[a-z]{2,63}(?=$|[^\\p{L}\\p{N}-]))"
        : (endsWithWord ? "(?=$|[^\\p{L}\\p{N}])" : "");
      const flags = options.caseSensitive ? "gu" : "giu";
      output = output.replace(new RegExp(`${prefix}${pattern}${suffix}`, flags), (match, before = "") => {
        return `${startsWithWord ? before : ""}${alias}`;
      });
    }
    return output;
  }

  function isInsideUserEditable(node) {
    const element = node?.nodeType === 3 ? node.parentElement : node;
    return !!element?.closest?.(USER_EDITABLE_SELECTOR);
  }

  const api = {
    canonicalCompanyKey,
    companyNameVariants,
    buildCompanyAliasMetadata,
    buildCompanyReplacementEntries,
    maskWithEntries,
    isInsideUserEditable
  };
  globalScope.DailyFlowPrivacy = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
