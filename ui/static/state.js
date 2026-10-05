/**
 * Shared UI state + track-list helpers.
 * Classic script in the browser; CommonJS for Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api;
  } else {
    /** @type {Record<string, unknown>} */ (root).MusicSorterState = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function createInitialState() {
    return {
      mode: "add_cues",
      accentTheme: "lime",
      colorScheme: "dark",
      /** @type {import('./types').Track[]} */
      tracks: [],
      index: 0,
      practiceMixes: [],
      practiceDetail: null,
      practiceDb: null,
      practiceMixPath: "",
      practiceTxSort: "order",
      practiceView: "mix",
      practiceBestItems: [],
      practiceBestLoading: false,
      practiceBestHidePlayed: false,
      practiceBestLivePlayedError: "",
      practiceAnalyzeJob: null,
      practiceAnalyzeTimer: null,
      practiceSummary: null,
      library: "House",
      folders: [],
      folderTrees: null,
      selectedDests: [],
      selectedLane: "",
      recommendedLane: "",
      selectedPath: "",
      selectedPathLibrary: "",
      expanded: new Set(),
      filter: "",
      trackSearch: "",
      readinessFilter: "all",
      crateFilter: "all",
      /** House fork: Add Cues folder (crate) filter; "" = all folders. */
      houseCrate: "Sauna Fest House",
      /** House fork: BPM window filter (null = off) */
      bpmMin: null,
      bpmMax: null,
      /** "default" | "bpm" | "camelot" */
      houseSortKey: "default",
      /** "asc" | "desc" */
      houseSortDir: "asc",
      setDirFilter: "pajamathon",
      setApprovalFilter: "all",
      recommendation: null,
      recommendAbort: null,
      health: null,
      activeCueKey: null,
      waveform: null,
      waveformLoading: false,
      waveformError: null,
      waveformAbort: null,
      waveZoom: 1,
      waveOffset: 0,
      waveViewBeforeAlign: null,
      waveViewPinned: false,
      waveCueChromeHits: null,
      showBeatOnes: true,
      gridAlignMode: false,
      gridAlignAnchor: null,
      gridAlignOriginal: null,
      gridAlignDragging: false,
      gridAlignDragOriginTime: 0,
      gridAlignDragOriginAnchor: 0,
      gridAlignPlan: null,
      loopDrag: null,
      placeCueMode: false,
      placeCuePreview: null,
      placeCueInFlight: false,
      placeLoopMode: false,
      placeLoopPreview: null,
      placeLoopInFlight: false,
      targetBpm: 75,
      playbackRate: 1,
      zoukSpeedOn: false,
      halfBpm: false,
      loopPlaybackOn: false,
      activeLoopKey: null,
      loopApproachUntil: null,
      exactCueJump: true,
      loopRaf: null,
      cueListFilter: "all",
      notesPath: null,
      notesSaveTimer: null,
      notesSaveGen: 0,
      notesDirty: false,
      /** @type {Record<string, import('./types').RetryJob>} */
      retryJobs: {},
      batchId: null,
      batchPollTimer: null,
      gridPreflight: null,
      gridManualConfirmed: {},
      trackGen: 0,
      tracksLoadGen: 0,
      quietSession: false,
      allowAutoplay: false,
      waveformDebounce: null,
      lastDrawMs: 0,
      playheadRaf: null,
      waveSeekTime: null,
      trackMeta: null,
      metaAbort: null,
      sortInFlight: false,
      promoteInFlight: false,
      notesWarnedVdj: false,
      batchPollInFlight: false,
      gridFixPollTimer: null,
      gridFixPollInFlight: false,
      autocueJobChip: null,
      assemblePreview: null,
      assembleJob: null,
      assemblePlaylistSort: "crate",
      assembleLaneShares: null,
      assembleMinFit: null,
      assembleMixPrefsTimer: null,
      lastCueCopy: null,
      stemAuditJob: null,
      stemAuditTimer: null,
      stemInventory: null,
      stemCheckResult: null,
    };
  }

  const state = createInitialState();

  function isReviewMode() {
    return state.mode === "add_cues";
  }

  function isRecsMode() {
    return state.mode === "recs";
  }

  function isAssembleMode() {
    return state.mode === "assemble";
  }

  function isPracticeMode() {
    return state.mode === "practice";
  }

  function isBestSetMode() {
    return state.mode === "best_set";
  }

  function isSetOverviewMode() {
    return state.mode === "set_overview";
  }

  function isStemsMode() {
    return state.mode === "stems";
  }

  /**
   * @returns {import('./types').Track | null}
   */
  function currentTrack() {
    return state.tracks[state.index] || null;
  }

  /**
   * @param {string} path
   * @param {number} gen
   */
  function stillOnTrack(path, gen) {
    return Boolean(path) && currentTrack()?.path === path && state.trackGen === gen;
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   * @returns {string | null}
   */
  function trackReadinessStatus(track) {
    const status = track?.readiness?.status;
    return typeof status === "string" ? status : null;
  }

  /**
   * @type {import('./types').AddCuesReadinessRank}
   */
  function addCuesReadinessRank(track) {
    const status = trackReadinessStatus(track);
    const paj = addCuesSection(track) === "pajamathon";
    if (paj) {
      if (status === "not_cued" || status === "missing") return 0;
      if (status === "needs_review" || status === "partial") return 1;
      if (status === "approved") return 4;
      return 3;
    }
    if (status === "ready") return 0;
    if (status === "partial") return 1;
    if (status === "not_cued" || status === "missing") return 2;
    return 3;
  }

  /**
   * @param {number[]} indexes
   */
  function sortAddCuesIndexes(indexes) {
    return indexes.slice().sort((a, b) => {
      const rank =
        addCuesReadinessRank(state.tracks[a]) - addCuesReadinessRank(state.tracks[b]);
      if (rank !== 0) return rank;
      return a - b;
    });
  }


  /**
   * BPM for a track row (server exposes bpm top-level; fall back to cues.bpm).
   * @param {any} track
   * @returns {number | null}
   */
  function trackBpmValue(track) {
    const raw = track && (track.bpm != null ? track.bpm : track.cues && track.cues.bpm);
    const n = Number(raw);
    return raw != null && raw !== "" && Number.isFinite(n) && n > 0 ? n : null;
  }

  /**
   * Parse "8A" / "12b" into { num, letter } or null.
   * @param {unknown} value
   */
  function parseCamelot(value) {
    const m = /^\s*(1[0-2]|[1-9])\s*([ABab])\s*$/.exec(String(value == null ? "" : value));
    if (!m) return null;
    return { num: Number(m[1]), letter: m[2].toUpperCase() };
  }

  /**
   * Sort rank for Camelot keys: 1A, 1B, 2A, 2B ... 12A, 12B (number-major).
   * @param {unknown} value
   * @returns {number | null}
   */
  function camelotSortValue(value) {
    const c = parseCamelot(value);
    return c ? c.num * 2 + (c.letter === "B" ? 1 : 0) : null;
  }

  /**
   * @param {any} track
   * @returns {string}
   */
  function trackCamelot(track) {
    const c = parseCamelot(track && (track.camelot || (track.cues && track.cues.camelot)));
    return c ? `${c.num}${c.letter}` : "";
  }

  /**
   * @param {any} track
   * @param {number | null} min
   * @param {number | null} max
   */
  function bpmInRange(track, min, max) {
    const lo = min == null || min === "" ? null : Number(min);
    const hi = max == null || max === "" ? null : Number(max);
    if (lo == null && hi == null) return true;
    const bpm = trackBpmValue(track);
    if (bpm == null) return false;
    if (lo != null && Number.isFinite(lo) && bpm < lo) return false;
    if (hi != null && Number.isFinite(hi) && bpm > hi) return false;
    return true;
  }

  /**
   * House Add Cues ordering. Tracks without the sort value always go last
   * (in both directions). Ties fall back to BPM then original index.
   * @param {number[]} indexes
   * @param {"default"|"bpm"|"camelot"} key
   * @param {"asc"|"desc"} dir
   */
  function sortHouseIndexes(indexes, key, dir) {
    if (key !== "bpm" && key !== "camelot") return sortAddCuesIndexes(indexes);
    const sign = dir === "desc" ? -1 : 1;
    const val = (i) =>
      key === "bpm" ? trackBpmValue(state.tracks[i]) : camelotSortValue(trackCamelot(state.tracks[i]));
    return indexes.slice().sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va == null && vb == null) return a - b;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (va !== vb) return sign * (va - vb);
      if (key === "camelot") {
        const ba = trackBpmValue(state.tracks[a]);
        const bb = trackBpmValue(state.tracks[b]);
        if (ba != null && bb != null && ba !== bb) return ba - bb;
      }
      return a - b;
    });
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   */
  function addCuesSection(track) {
    if (
      track?.section === "pajamathon" ||
      track?.section === "inbox" ||
      track?.section === "in_set"
    ) {
      return track.section;
    }
    const group = String(track?.group || "").toLowerCase();
    const rel = String(track?.relative_path || "")
      .replace(/\\/g, "/")
      .toLowerCase();
    if (
      group.startsWith("pajamathon") ||
      rel.startsWith("pajamathon/") ||
      rel.startsWith("pajamathon ")
    ) {
      return "pajamathon";
    }
    return "inbox";
  }

  /**
   * @type {import('./types').TrackRetryKind}
   */
  const RECENTLY_CUED_MS = 72 * 60 * 60 * 1000;

  /**
   * @param {import('./types').Track | null | undefined} track
   * @param {import('./types').RetryJob | null | undefined} job
   */
  function trackLastCuedMs(track, job) {
    const hist = track && track.retry_history && track.retry_history.last_ts;
    const jobTs =
      job && (job.status === "ok" || job.status === "error")
        ? job.finished_at || job.finishedAt
        : "";
    const times = [Date.parse(String(hist || "")), Date.parse(String(jobTs || ""))].filter(
      (n) => Number.isFinite(n)
    );
    return times.length ? Math.max.apply(null, times) : 0;
  }

  /**
   * @param {import('./types').Track | null | undefined} track
   * @param {import('./types').RetryJob | null | undefined} job
   * @param {number} [nowMs]
   * @param {number} [windowMs]
   */
  function isRecentlyCued(track, job, nowMs, windowMs) {
    const at = trackLastCuedMs(track, job);
    if (!at) return false;
    const now = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    const window = Number.isFinite(Number(windowMs)) ? Number(windowMs) : RECENTLY_CUED_MS;
    return now - at <= window && now - at >= 0;
  }

  function trackRetryKind(track, job) {
    const hist = track?.retry_history || {};
    let cues = Boolean(hist.tried_cues);
    let loops = Boolean(hist.tried_loops);
    if (hist.kind === "both" || hist.tried_both) {
      cues = true;
      loops = true;
    } else if (hist.kind === "cues") {
      cues = true;
    } else if (hist.kind === "loops") {
      loops = true;
    }
    const scope = String(job?.writeScope || job?.write_scope || "").toLowerCase();
    const finished = ["ok", "error", "skipped"].includes(String(job?.status || ""));
    if (job && finished && scope) {
      if (scope === "cues" || scope === "all" || scope === "both") cues = true;
      if (scope === "loops" || scope === "all" || scope === "both") loops = true;
    }
    if (cues && loops) return "both";
    if (cues) return "cues";
    if (loops) return "loops";
    return null;
  }

  return {
    createInitialState,
    state,
    isReviewMode,
    isRecsMode,
    isAssembleMode,
    isPracticeMode,
    isBestSetMode,
    isSetOverviewMode,
    isStemsMode,
    currentTrack,
    stillOnTrack,
    trackReadinessStatus,
    addCuesReadinessRank,
    sortAddCuesIndexes,
    trackBpmValue,
    parseCamelot,
    camelotSortValue,
    trackCamelot,
    bpmInRange,
    sortHouseIndexes,
    addCuesSection,
    trackRetryKind,
    trackLastCuedMs,
    isRecentlyCued,
    RECENTLY_CUED_MS,
  };
});
