(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OooTimeline = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const DAY_LABELS = ["Su", "M", "Tu", "W", "Th", "F", "Sa"];

  function parseDate(value) {
    return new Date(`${value}T12:00:00`);
  }

  function dateKey(value) {
    return value.toLocaleDateString("en-CA");
  }

  function businessDates(from, to) {
    const dates = [];
    const current = parseDate(from);
    const end = parseDate(to);
    if (Number.isNaN(current.valueOf()) || Number.isNaN(end.valueOf()) || current > end) return dates;
    while (current <= end) {
      if (current.getDay() !== 0 && current.getDay() !== 6) {
        dates.push({
          key: dateKey(current),
          label: current.toLocaleDateString([], { month: "short", day: "numeric" }),
          dayLabel: DAY_LABELS[current.getDay()],
          weekStart: current.getDay() === 1,
        });
      }
      current.setDate(current.getDate() + 1);
    }
    return dates;
  }

  function evidenceSummary(period) {
    const evidence = Array.isArray(period.evidence) ? period.evidence : [];
    const labels = evidence.map(item => item.sourceLabel || item.sourceType).filter(Boolean);
    const sources = [...new Set(labels)];
    return sources.length ? sources.join(", ") : "Source unavailable";
  }

  function buildSegments(periods, dates) {
    const segments = [];
    for (const period of periods || []) {
      let start = -1;
      let end = -1;
      dates.forEach((date, index) => {
        if (date.key >= period.startDate && date.key <= period.endDate) {
          if (start < 0) start = index;
          end = index;
        }
      });
      if (start >= 0) {
        segments.push({
          start,
          span: end - start + 1,
          startDate: period.startDate,
          endDate: period.endDate,
          status: period.status || "confirmed",
          confidence: Number(period.confidence || 0),
          sources: evidenceSummary(period),
        });
      }
    }
    return segments;
  }

  function buildModel(data, from, to) {
    const dates = businessDates(from, to);
    const people = [...(data.people || [])]
      .sort((left, right) => String(left.personName).localeCompare(String(right.personName)))
      .map(person => ({
        personName: person.personName,
        segments: buildSegments(person.periods, dates),
      }));
    return { dates, people };
  }

  return { businessDates, buildSegments, buildModel, evidenceSummary };
});
