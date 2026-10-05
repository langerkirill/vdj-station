/**
 * Stem vocal-audit helpers.
 * Classic script in the browser; CommonJS for Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterStems = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /**
   * @param {{ id?: string, status?: string } | null | undefined} job
   */
  function stemsJobBusy(job) {
    return Boolean(job && job.id && (job.status === "running" || job.status === "queued"));
  }

  /**
   * @param {unknown} seconds
   */
  function formatStemClock(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  /**
   * @param {Array<{ start?: number, end?: number }> | null | undefined} holes
   */
  function formatHoles(holes) {
    if (!holes || !holes.length) return "";
    return holes
      .map((h) => `${formatStemClock(h.start)}–${formatStemClock(h.end)}`)
      .join(", ");
  }

  /**
   * @param {string | null | undefined} path
   * @param {string | null | undefined} root
   */
  function formatStemRel(path, root) {
    const p = String(path || "").replace(/\\/g, "/");
    const r = String(root || "")
      .replace(/\\/g, "/")
      .replace(/\/$/, "");
    if (r && p.toLowerCase().startsWith(`${r.toLowerCase()}/`)) {
      return p.slice(r.length + 1);
    }
    const parts = p.split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : p;
  }

  return {
    stemsJobBusy,
    formatStemClock,
    formatHoles,
    formatStemRel,
  };
});
