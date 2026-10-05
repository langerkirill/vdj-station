/* Domain homes (not this file): state.js, transport.js, waveform.js,
   practice.js, assemble.js, stems.js, placements.js, status_handoff.js */
const MusicSorterState =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterState) ||
  (typeof window !== "undefined" && window.MusicSorterState);

const MusicSorterTransport =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterTransport) ||
  (typeof window !== "undefined" && window.MusicSorterTransport);

const MusicSorterWaveform =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterWaveform) ||
  (typeof window !== "undefined" && window.MusicSorterWaveform);

const MusicSorterPractice =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterPractice) ||
  (typeof window !== "undefined" && window.MusicSorterPractice);

const MusicSorterAssemble =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterAssemble) ||
  (typeof window !== "undefined" && window.MusicSorterAssemble);

const MusicSorterStems =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterStems) ||
  (typeof window !== "undefined" && window.MusicSorterStems);

const state = MusicSorterState.state;

const WAVE_PAD_X = MusicSorterWaveform.WAVE_PAD_X;
const WAVE_ZOOM_MIN = MusicSorterWaveform.WAVE_ZOOM_MIN;
const WAVE_ZOOM_MAX = MusicSorterWaveform.WAVE_ZOOM_MAX;

const CUE_COLORS = {
  blue: "#3b82f6",
  lightblue: "#38bdf8",
  green: "#22c55e",
  purple: "#a855f7",
  yellow: "#eab308",
  orange: "#f97316",
  unknown: "#94a3b8",
};

/** Palette choices for the cue/loop color dropdown (matches AutoCue VDJ ints). */
const CUE_COLOR_OPTIONS = MusicSorterTransport.CUE_COLOR_SCHEME.map((c) => ({
  id: c.id,
  label: `${c.name} · ${c.meaning}`,
  name: c.name,
  meaning: c.meaning,
}));

/** The ONLY place the cue/loop color legend is built (one chip per color, never duplicated). */
function renderCueColorLegend() {
  const host = document.getElementById("cueColorLegend");
  if (!host) return;
  host.replaceChildren();
  for (const c of CUE_COLOR_OPTIONS) {
    const chip = document.createElement("span");
    chip.className = `cue-legend-swatch color-${c.id}`;
    chip.dataset.color = c.id;
    chip.textContent = `${c.name} · ${c.meaning}`;
    host.appendChild(chip);
  }
}
if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", renderCueColorLegend);
  else renderCueColorLegend();
}

const CUE_COLOR_ARGB = {
  blue: 4278190335,
  lightblue: 4278255615,
  green: 4278255360,
  purple: 4288020735,
  yellow: 4294967040,
  orange: 4294934272,
};

function sanitizeColorName(name) {
  const id = String(name || "unknown").toLowerCase().trim();
  if (CUE_COLORS[id]) return id;
  return "unknown";
}

function stillOnTrack(path, gen) {
  return MusicSorterState.stillOnTrack(path, gen);
}

/* A song left the list (deleted / trashed / copied away): nothing of it may linger on screen or be written later.
   Its in-flight drag, unsaved note, markers, grid card and (for a deletion) unsaved edits are dropped. */
function dropSongState(path, { dropFailed = false } = {}) {
  if (!path) return;
  const openPath = currentTrack()?.path || null;
  const wasOpen = openPath === path; // R-96: only the OPEN song's screen is torn down; a song removed behind the viewed one changes nothing on screen
  state.tracks = (state.tracks || []).filter((t) => t.path !== path);
  if (!wasOpen && openPath) {
    const at = state.tracks.findIndex((t) => t.path === openPath);
    if (at >= 0) state.index = at; // the list shrank above the viewed song: keep looking at the same song
  }
  if (dropFailed) {
    state.failedEdits = (state.failedEdits || []).filter((f) => f && f.path !== path);
    try {
      persistFailedEdits();
    } catch {
      /* ignore */
    }
    editChains.delete(path);
  }
  markerRev.delete(path);
  markerHistory.delete(path);
  needsReconcile.delete(path);
  if (state.notesPath === path) {
    clearTimeout(state.notesSaveTimer);
    state.notesSaveTimer = null;
    state.notesDirty = false; // a note typed for a song that is gone is never written to the next song
    state.notesPath = null;
  }
  if (wasOpen) {
    if (state.loopDrag) {
      try {
        removeDragOverlay();
      } catch {
        /* ignore */
      }
      state.loopDrag = null;
    }
    state.dropPreview = null;
    state.placeLoopPreview = null;
    state.panelPath = null; // the next renderPlayer does a full per-song reset
    state.cuesPath = null;
    state.waveform = null;
    state.waveformPath = null;
    if (state.index >= state.tracks.length) state.index = Math.max(0, state.tracks.length - 1);
    try {
      bindNotesToTrack(null);
    } catch {
      /* ignore */
    }
  }
  renderServerBanner();
}

/* ===== ONE loaded-song identity (FilePath) =====
   state.panelPath  = the song the player/review panel was last built for (set in renderPlayer)
   state.cuesPath   = the song the cue list was last rendered for (set in renderCues)
   state.recommendationPath = the song the Gemini card/tags belong to
   Every song-level action (AutoCue, Align, grid, Copy/sort, tag save) captures the identity when the button
   is clicked and is blocked, with a clear message, if the truly loaded song is not that one. */
function captureSong() {
  const t = currentTrack();
  return t ? { path: t.path, name: t.name || t.path } : null;
}
function songPanelReady(path) {
  return Boolean(path) && currentTrack()?.path === path && state.panelPath === path && state.cuesPath === path;
}
/* R-82(c): after Copy auto-advances, refuse another Copy until the NEW open song is fully settled
   (panel + cues + waveform). The in-flight copy itself stays bound to the song captured at click. */
function songCopyReady(track = currentTrack()) {
  return Boolean(track) && songPanelReady(track.path) && waveformReadyFor(track);
}
function copyButtonsLocked() {
  return Boolean(state.sortInFlight || state.copyAdvanceLock);
}
function setCopyAdvanceLock(on) {
  state.copyAdvanceLock = Boolean(on);
  try { updateApproveButtons(); } catch { /* ignore */ }
  try { syncSortButtonState(); } catch { /* ignore */ }
  try { applyCopyBusyToButtons(); } catch { /* ignore */ }
}
async function waitSongSettledForCopy(timeoutMs = 12000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (songCopyReady(currentTrack())) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return songCopyReady(currentTrack());
}
function guardSong(cap, label) {
  const t = currentTrack();
  if (!cap || !t) {
    loudNotice(`${label}: no song is open.`, "error");
    return false;
  }
  if (t.path !== cap.path) {
    loudNotice(`${label} cancelled: the open song changed (it was “${cap.name}”, now “${t.name}”). Nothing was changed.`, "error");
    return false;
  }
  if (!songPanelReady(cap.path)) {
    loudNotice(`${label}: “${t.name}” is still loading - try again in a second. Nothing was changed.`, "error");
    return false;
  }
  return true;
}
/* Markers that lie beyond the end of the song cannot belong to it (another song's markers): never keep them. */
function dropOutOfSongPoints(t) {
  const len = Number(t?.cues?.song_length) || 0;
  const pts = t?.cues?.points;
  if (!(len > 0) || !Array.isArray(pts) || !pts.length) return t;
  const bad = pts.filter((p) => Number(p.pos) > len + 2);
  if (!bad.length) return t;
  try {
    console.warn(`Rejected ${bad.length} marker(s) beyond the end of “${t.name}” (${len.toFixed(1)}s): they belong to another song.`);
  } catch {
    /* ignore */
  }
  const keep = pts.filter((p) => !(Number(p.pos) > len + 2));
  const cueN = keep.filter((p) => pointKind(p) === "cue").length;
  const loopN = keep.filter((p) => pointKind(p) === "loop").length;
  return { ...t, cues: { ...t.cues, points: keep, cue_count: cueN, loop_count: loopN }, rejected_markers: bad.length };
}

const CUE_COLORS_RGB = {
  blue: [59, 130, 246],
  lightblue: [56, 189, 248],
  green: [34, 197, 94],
  purple: [168, 85, 247],
  yellow: [234, 179, 8],
  orange: [249, 115, 22],
  unknown: [148, 163, 184],
};

function cueRgba(colorName, alpha) {
  const rgb = CUE_COLORS_RGB[colorName] || CUE_COLORS_RGB.unknown;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

function accentRgba(alpha) {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--accent-rgb")
    .trim();
  const rgb = raw.match(/\d+(?:\.\d+)?/g)?.slice(0, 3);
  if (!rgb || rgb.length !== 3) return `rgba(200, 255, 98, ${alpha})`;
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

/** VDJ loop Size is in beats → seconds via track BPM. */
function loopDurationSeconds(point, bpm) {
  return MusicSorterTransport.loopDurationSeconds(point, bpm);
}

function isReviewMode() {
  return MusicSorterState.isReviewMode();
}

function isRecsMode() {
  return MusicSorterState.isRecsMode();
}

function isAssembleMode() {
  return MusicSorterState.isAssembleMode();
}

function isPracticeMode() {
  return MusicSorterState.isPracticeMode();
}

function isBestSetMode() {
  return typeof MusicSorterState.isBestSetMode === "function"
    ? MusicSorterState.isBestSetMode()
    : state.mode === "best_set";
}

function isSetOverviewMode() {
  return typeof MusicSorterState.isSetOverviewMode === "function"
    ? MusicSorterState.isSetOverviewMode()
    : state.mode === "set_overview";
}

function isStemsMode() {
  return typeof MusicSorterState.isStemsMode === "function"
    ? MusicSorterState.isStemsMode()
    : state.mode === "stems";
}

function setTrackDir(track) {
  const rel = String(track?.relative_path || track?.group || "").replace(/\\/g, "/");
  return rel.split("/").filter(Boolean)[0] || "";
}

function trackInMustPlayFolder(track) {
  const rel = String((track && (track.relative_path || track.path)) || "").replace(/\\/g, "/");
  return /(^|\/)Must Play(\/|$)/i.test(rel);
}

function isPajamathonSetDir(name) {
  return /^pajamathon/i.test(String(name || ""));
}

function trackMatchesSetDir(track) {
  const filter = state.setDirFilter || "pajamathon";
  if (filter === "all") return true;
  const dir = setTrackDir(track);
  if (filter === "pajamathon") return isPajamathonSetDir(dir);
  return dir === filter;
}

function uniqueSetDirs(tracks) {
  const seen = [];
  for (const tr of tracks || []) {
    const dir = setTrackDir(tr);
    if (/^must play$/i.test(dir)) continue;
    if (dir && !seen.includes(dir)) seen.push(dir);
  }
  return seen.sort((a, b) => {
    const ap = isPajamathonSetDir(a) ? 0 : 1;
    const bp = isPajamathonSetDir(b) ? 0 : 1;
    return ap - bp || a.localeCompare(b);
  });
}

function renderSetDirFilter() {
  const root = $("setDirFilter");
  if (!root) return;
  const current = state.setDirFilter || "pajamathon";
  const extras = uniqueSetDirs(state.tracks).filter((d) => !isPajamathonSetDir(d));
  const chips = [
    ["pajamathon", "Pajamathon"],
    ...extras.map((d) => [d, d]),
    ["all", "All sets"],
  ];
  root.innerHTML = chips
    .map(
      ([value, label]) =>
        `<button type="button" data-set-dir="${escapeHtml(value)}" class="${
          value === current ? "active" : ""
        }">${escapeHtml(label)}</button>`
    )
    .join("");
  root.querySelectorAll("[data-set-dir]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.setDirFilter = btn.dataset.setDir || "pajamathon";
      renderSetDirFilter();
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
      }
      renderTrackList();
      updatePipelineStrip();
      renderSetOverviewRail();
    });
  });
}

function selectedQueueTrack() {
  const cur = currentTrack();
  if (cur) return cur;
  const indexes =
    typeof filteredTrackIndexes === "function" ? filteredTrackIndexes() : [];
  if (indexes.length) return state.tracks[indexes[0]] || null;
  return (state.tracks && state.tracks[0]) || null;
}

function mountSharedSortRail() {
  const rail = $("sharedSortRail");
  const home = $("sharedSortRailHome");
  const mount = $("setOverviewSortMount");
  if (!rail) return;
  if (isSetOverviewMode() && mount) mount.appendChild(rail);
  else if (home && rail.parentElement !== home) home.appendChild(rail);
}

function renderSetOverviewRail() {
  const box = $("setOverviewMatch");
  const btn = $("setOverviewCopyBtn");
  const sendBtn = $("setOverviewSendBackBtn");
  const removeBtn = $("setOverviewRemoveBtn");
  const approveBtn = $("setOverviewApproveBtn");
  if (!box || !btn) return;
  const track = selectedQueueTrack();
  if (!track) {
    box.innerHTML = `<div class="subtitle">No set tracks in this crate</div>`;
    btn.disabled = true;
    btn.textContent = "Copy cues";
    if (sendBtn) sendBtn.disabled = true;
    if (removeBtn) removeBtn.disabled = true;
    if (approveBtn) {
      approveBtn.disabled = true;
      approveBtn.textContent = "Kirill approved";
      approveBtn.classList.remove("is-approved");
    }
    const mustNone = $("setOverviewMustPlayBtn");
    if (mustNone) {
      mustNone.disabled = true;
      mustNone.classList.remove("is-approved");
    }
    return;
  }
  const sib = cuedSiblingHit(track);
  const same = Boolean(sib) && trackMatchesWrittenCopy(track, sib);
  const badge = writtenCopyBadge(track) || (same
    ? `<span class="autocue-flag same">same</span>`
    : `<span class="autocue-flag different">different</span>`);
  const sibName = sib ? escapeHtml(sib.relative_path || sib.path || "sibling") : "no cued sibling";
  const paj = isPajamathonSetQueueTrack(track);
  const approved = trackIsKirillApproved(track);
  const mustPlay = trackIsMustPlay(track);
  box.innerHTML = `
    <div class="readiness-summary">${escapeHtml(trackDisplayTitle(track))}</div>
    <div class="meta-row">${badge}${
      approved ? ` <span class="badge ok">Kirill approved</span>` : ""
    }${
      mustPlay ? ` <span class="badge ok set-ov-must-play">Must Play</span>` : ""
    }</div>
    <div class="subtitle">${sib ? `Sibling · ${sibName}` : "No cued sibling to copy from"}</div>
  `;
  btn.disabled = !(sib && !same);
  btn.textContent = same ? "Same as copy" : sib ? "Copy cues" : "No sibling";
  if (approveBtn) {
    approveBtn.disabled = !track.is_cued;
    approveBtn.textContent = "Kirill approved";
    approveBtn.classList.toggle("is-approved", approved);
    approveBtn.setAttribute("aria-pressed", approved ? "true" : "false");
    approveBtn.title = approved
      ? "Approved — stays on this track"
      : "Stamp these cues. Stays on this row.";
  }
  if (sendBtn) {
    sendBtn.disabled = false;
    sendBtn.title = "Move this set copy to Add Cues. Cues Sorted stays.";
  }
  if (removeBtn) {
    removeBtn.disabled = false;
    removeBtn.title = "Delete this set copy only. Cues Sorted / Add Cues stay.";
  }
  const mustBtn = $("setOverviewMustPlayBtn");
  if (mustBtn) {
    mustBtn.disabled = false;
    mustBtn.classList.toggle("is-approved", mustPlay);
    mustBtn.setAttribute("aria-pressed", mustPlay ? "true" : "false");
    mustBtn.title = mustPlay
      ? "Must Play — stays on this track"
      : "Stamp Must Play and copy into Sets/Pajamathon/Must Play.";
  }
}

function wantsQuietSession() {
  return MusicSorterTransport.wantsQuietSession(window);
}

function shouldAutoplayOnSelect() {
  return MusicSorterTransport.shouldAutoplayOnSelect(state, isPracticeMode());
}

function playAudio(audio) {
  if (!audio) return Promise.resolve();
  if (state.quietSession) {
    try {
      audio.pause();
    } catch {
      /* ignore */
    }
    audio.muted = true;
    setStatus("Sound off — click Sound off in the top bar to hear playback");
    return Promise.resolve();
  }
  if (!audio.src) return Promise.resolve();
  const p = audio.play();
  const markStarted = () => {
    state.allowAutoplay = true;
  };
  if (p && typeof p.then === "function") {
    return p.then(markStarted);
  }
  markStarted();
  return Promise.resolve();
}

function syncQuietSessionUi() {
  const chip = $("quietSessionChip");
  if (chip) chip.hidden = !state.quietSession;
  document.body.classList.toggle("quiet-session", Boolean(state.quietSession));
}

function installQuietPlayGuard(audio) {
  if (!audio || audio.dataset.quietGuard === "1") return;
  audio.dataset.quietGuard = "1";
  const nativePlay = audio.play.bind(audio);
  audio.play = function quietGuardedPlay() {
    if (state.quietSession) {
      try {
        audio.pause();
      } catch {
        /* ignore */
      }
      audio.muted = true;
      return Promise.resolve();
    }
    return nativePlay();
  };
}

function applyQuietSession() {
  state.quietSession = wantsQuietSession();
  const audio = $("audio");
  if (audio) {
    installQuietPlayGuard(audio);
    audio.muted = Boolean(state.quietSession);
    if (state.quietSession) {
      try {
        audio.pause();
      } catch {
        /* ignore */
      }
    }
  }
  if (state.quietSession) state.allowAutoplay = false;
  syncQuietSessionUi();
}

function disableQuietSession() {
  state.quietSession = false;
  const audio = $("audio");
  if (audio) audio.muted = false;
  syncQuietSessionUi();
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("quiet");
    url.searchParams.delete("mute");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* ignore */
  }
}

function formatClock(sec) {
  return MusicSorterTransport.formatClock(sec);
}

function setPracticeWaveStatus(text, kind = "") {
  const el = $("practiceWaveformStatus");
  if (!el) return;
  el.textContent = text || "";
  const empty = !text;
  el.className = `waveform-status${kind ? ` ${kind}` : ""}${empty ? " hidden is-empty" : ""}`;
  el.hidden = empty;
}

function practiceTransitions() {
  return MusicSorterPractice.practiceTransitions(state);
}

function practiceDuration(track, audio) {
  return MusicSorterPractice.practiceDuration(track, audio, state, trackDuration);
}

/** Clamp helper for practice map layout math. */
function practiceClamp(n, lo, hi) {
  return MusicSorterPractice.practiceClamp(n, lo, hi);
}

/**
 * Equal-width song slots for the practice transition map.
 * Slot i covers tracks[i].pos_sec → next (or duration); first slot starts at 0.
 * Returns null when tracks are missing (caller falls back to pure time mapping).
 */
function practiceSongSlots(duration) {
  return MusicSorterPractice.practiceSongSlots(duration, state.practiceDetail);
}

/** Viewport + scrollable content width; px/song clamped so few songs fill, many scroll. */
function practiceMapLayout(wrap, duration) {
  return MusicSorterPractice.practiceMapLayout(wrap, duration, state.practiceDetail);
}

function practiceTimeToX(t, slots, contentW, duration, padX = 10) {
  return MusicSorterPractice.practiceTimeToX(t, slots, contentW, duration, padX);
}

function practiceXToTime(x, slots, contentW, duration, padX = 10) {
  return MusicSorterPractice.practiceXToTime(x, slots, contentW, duration, padX);
}

function visibleBestPracticeItems(items, hidePlayed) {
  return MusicSorterPractice.visibleBestPracticeItems(items, hidePlayed);
}

function bestPracticeHiddenCount(items, hidePlayed) {
  return MusicSorterPractice.bestPracticeHiddenCount(items, hidePlayed);
}

let _practiceWaveScrollMix = null;

function ensurePracticePlayheadVisible(wrap, px) {
  if (!wrap) return;
  const viewW = wrap.clientWidth || 0;
  if (viewW <= 0) return;
  const sl = wrap.scrollLeft || 0;
  const margin = 48;
  if (px >= sl + margin && px <= sl + viewW - margin) return;
  const target = Math.max(0, px - viewW * 0.35);
  if (Math.abs(target - sl) < 8) return;
  wrap.scrollLeft = target;
}

function updatePracticeWaveVisibility() {
  const panel = $("practiceWavePanel");
  if (!panel) return;
  if (!isPracticeMode()) {
    panel.hidden = true;
    panel.classList.remove("is-empty");
    return;
  }
  // Keep map visible whenever a practice mix is selected (even with 0 transitions).
  const hasMix = Boolean(state.practiceMixPath || state.practiceDetail);
  const txs = typeof practiceTransitions === "function" ? practiceTransitions() : [];
  panel.classList.toggle("is-empty", !txs.length);
  panel.hidden = !hasMix;
}

function schedulePracticeWaveRedraw() {
  if (!isPracticeMode()) return;
  // Layout must settle so wrap.clientWidth reflects full main column.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      try {
        drawPracticeWaveform();
      } catch {
        /* ignore */
      }
    });
  });
  // Second pass after fonts/scrollbars
  setTimeout(() => {
    if (isPracticeMode()) {
      try {
        drawPracticeWaveform();
      } catch {
        /* ignore */
      }
    }
  }, 120);
}

/** Full-mix waveform with numbered transition markers (practice mode only). */
function drawPracticeWaveform() {
  const canvas = $("practiceWaveformCanvas");
  const wrap = $("practiceWaveformWrap");
  if (!canvas || !wrap || !isPracticeMode()) return;

  const track = currentTrack();
  const audio = $("audio");
  const duration = practiceDuration(track, audio);
  const layout = practiceMapLayout(wrap, duration);
  const { contentWidth, slots, padX } = layout;
  const dpr = window.devicePixelRatio || 1;
  const cssW = contentWidth;
  const cssH = wrap.clientHeight || 110;

  const mixKey = state.practiceMixPath || "";
  if (_practiceWaveScrollMix !== mixKey) {
    _practiceWaveScrollMix = mixKey;
    wrap.scrollLeft = 0;
  }

  if (
    canvas.width !== Math.floor(cssW * dpr) ||
    canvas.height !== Math.floor(cssH * dpr)
  ) {
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
  }
  canvas.style.width = `${cssW}px`;
  canvas.style.height = `${cssH}px`;

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = cssW;
  const h = cssH;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0a0e16";
  ctx.fillRect(0, 0, w, h);

  const peaks = state.waveform?.peaks;
  const plotW = Math.max(1, w - padX * 2);
  const mid = h / 2;

  // Song slot backgrounds / dividers / labels
  if (slots?.length) {
    const slotW = plotW / slots.length;
    slots.forEach((s, i) => {
      const x0 = padX + i * slotW;
      if (i % 2 === 1) {
        ctx.fillStyle = "rgba(255,255,255,0.025)";
        ctx.fillRect(x0, 0, slotW, h);
      }
      ctx.strokeStyle = "rgba(255,255,255,0.10)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x0, 0);
      ctx.lineTo(x0, h);
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.38)";
      ctx.font = "600 10px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      const label = s.label || String(i + 1);
      ctx.fillText(label, x0 + 4, h - 5, Math.max(24, slotW - 8));
    });
    // Closing edge
    ctx.strokeStyle = "rgba(255,255,255,0.10)";
    ctx.beginPath();
    ctx.moveTo(padX + plotW, 0);
    ctx.lineTo(padX + plotW, h);
    ctx.stroke();
  }

  // Center line
  ctx.strokeStyle = "rgba(42,51,68,0.9)";
  ctx.beginPath();
  ctx.moveTo(padX, mid);
  ctx.lineTo(w - padX, mid);
  ctx.stroke();

  // Normalize peaks so quiet-but-audible files still show shape;
  // truly silent files stay flat and get a banner.
  let drawPeaks = peaks;
  let peakMax = 0;
  if (peaks?.length) {
    for (const p of peaks) if (p > peakMax) peakMax = p;
  }
  const silent = peakMax > 0 && peakMax < 0.02;
  const banner = $("practiceSilentBanner");
  if (banner) banner.hidden = !silent;
  if (peaks?.length && peakMax > 0 && peakMax < 0.35) {
    // boost quiet mixes for display only
    const scale = 0.85 / peakMax;
    drawPeaks = peaks.map((p) => Math.min(1, p * scale));
  }

  if (drawPeaks?.length && duration > 0) {
    ctx.beginPath();
    const n = drawPeaks.length;
    for (let i = 0; i < n; i++) {
      const t = (i / Math.max(1, n - 1)) * duration;
      const x = practiceTimeToX(t, slots, w, duration, padX);
      const amp = Math.min(1, drawPeaks[i]) * (h * 0.4);
      if (i === 0) ctx.moveTo(x, mid - amp);
      else ctx.lineTo(x, mid - amp);
    }
    for (let i = n - 1; i >= 0; i--) {
      const t = (i / Math.max(1, n - 1)) * duration;
      const x = practiceTimeToX(t, slots, w, duration, padX);
      const amp = Math.min(1, drawPeaks[i]) * (h * 0.4);
      ctx.lineTo(x, mid + amp);
    }
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, accentRgba(0.55));
    grad.addColorStop(0.5, accentRgba(0.22));
    grad.addColorStop(1, accentRgba(0.5));
    ctx.fillStyle = grad;
    ctx.fill();
  }

  const txs = practiceTransitions();
  const countEl = $("practiceWaveTxCount");
  if (countEl) {
    countEl.textContent = `${txs.length} transition${txs.length === 1 ? "" : "s"}`;
  }

  // Transition markers (same song-slot coordinate system)
  if (duration > 0 && txs.length) {
    txs.forEach((tx, i) => {
      const t = Number(tx.at_sec) || 0;
      const x = practiceTimeToX(t, slots, w, duration, padX);
      const overall = tx.score?.overall;
      let color = accentRgba(0.95);
      if (overall != null) {
        if (Number(overall) >= 7.5) color = "rgba(34, 197, 94, 0.95)";
        else if (Number(overall) < 5.5) color = "rgba(249, 115, 22, 0.95)";
        else color = "rgba(234, 179, 8, 0.95)";
      }
      // Vertical line
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, 8);
      ctx.lineTo(x, h - 8);
      ctx.stroke();
      // Top disc + number
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, 12, 9, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#0a0e16";
      ctx.font = "bold 10px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(i + 1), x, 12);
    });
  }

  // Playhead
  let playheadX = null;
  if (duration > 0 && audio && Number.isFinite(audio.currentTime)) {
    playheadX = practiceTimeToX(audio.currentTime, slots, w, duration, padX);
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(playheadX, 0);
    ctx.lineTo(playheadX, h);
    ctx.stroke();
    if (!audio.paused) {
      ensurePracticePlayheadVisible(wrap, playheadX);
    }
  }

  // Legend chips under wave
  renderPracticeWaveLegend(txs);
  updatePracticeWaveVisibility();
}

function renderPracticeWaveLegend(txs) {
  const el = $("practiceWaveLegend");
  if (!el) return;
  if (!txs?.length) {
    el.innerHTML = `<span class="subtitle">No transition cues on this mix yet — click the map to scrub & play.</span>`;
    return;
  }
  el.innerHTML = txs
    .map((tx, i) => {
      const score =
        tx.score?.overall != null
          ? `<span class="pw-score">${Number(tx.score.overall).toFixed(1)}</span>`
          : "";
      const save = tx.score?.save_for_set
        ? `<span class="badge ok">save</span>`
        : "";
      return `<button type="button" class="practice-wave-chip" data-at="${tx.at_sec}" data-index="${tx.index}" title="${escapeHtml(
        `${tx.from_track} → ${tx.to_track}`
      )}">
        <span class="pw-num">${i + 1}</span>
        <span class="pw-time">${formatClock(tx.at_sec)}</span>
        <span class="pw-to">${escapeHtml((tx.to_track || "").slice(0, 28))}</span>
        ${score}
        ${save}
      </button>`;
    })
    .join("");
  el.querySelectorAll(".practice-wave-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      seekPracticeTransition(Number(btn.dataset.at) || 0, {
        index: btn.dataset.index,
      });
    });
  });
}

/**
 * Scroll/highlight the matching transition card in the Practice Lab list.
 */
function focusPracticeTransitionCard(atSec, index) {
  const list = $("practiceTransitionList");
  if (!list) return null;
  let card = null;
  if (index != null && String(index) !== "") {
    const idx = String(index);
    card = [...list.querySelectorAll(".practice-tx[data-index]")].find(
      (el) => String(el.dataset.index) === idx
    ) || null;
  }
  if (!card && atSec != null && Number.isFinite(Number(atSec))) {
    const target = Number(atSec);
    let best = null;
    let bestD = Infinity;
    list.querySelectorAll(".practice-tx[data-at]").forEach((el) => {
      const d = Math.abs(Number(el.dataset.at) - target);
      if (d < bestD) {
        bestD = d;
        best = el;
      }
    });
    // Only accept a near match (same transition, not a random card).
    if (best && bestD <= 0.75) card = best;
  }
  list.querySelectorAll(".practice-tx.is-focused").forEach((el) => {
    el.classList.remove("is-focused");
  });
  if (!card) return null;
  card.classList.add("is-focused");
  // Scroll the analysis pane so the description is in view.
  try {
    card.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
  } catch {
    card.scrollIntoView(true);
  }
  const panel = $("practicePanel");
  if (panel && typeof panel.scrollTop === "number") {
    // If scrollIntoView didn't move a nested scroller enough, nudge panel.
    const panelRect = panel.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    if (cardRect.top < panelRect.top + 8 || cardRect.bottom > panelRect.bottom - 8) {
      const delta = cardRect.top - panelRect.top - panel.clientHeight * 0.12;
      panel.scrollBy({ top: delta, behavior: "smooth" });
    }
  }
  return card;
}

function seekPracticeTransition(atSec, { preRoll = 20, play = true, index = null } = {}) {
  const audio = $("audio");
  if (!audio) {
    setStatus("No audio element — refresh the page.", "error");
    return;
  }
  if (!audio.src && !audio.dataset.path) {
    setStatus("No mix loaded — click a practice mix first.", "error");
    return;
  }
  // Mix-card focus lives on Practice. Best for set keeps its own list still.
  if (!isBestSetMode()) {
    focusPracticeTransitionCard(atSec, index);
  }
  const target = Math.max(0, Number(atSec) - preRoll);
  const apply = () => {
    try {
      audio.currentTime = target;
    } catch {
      /* ignore until metadata ready */
    }
    if (play) {
      const p = playAudio(audio);
      if (p && typeof p.catch === "function") {
        p.catch((err) => {
          setStatus(
            `Playback blocked: ${err?.message || "click Play once, then try again"}`,
            "error"
          );
        });
      }
    }
    if (isPracticeMode()) drawPracticeWaveform();
    if (!isBestSetMode()) {
      focusPracticeTransitionCard(atSec, index);
    }
    const silent = isPracticeMixNearlySilent();
    setStatus(
      silent
        ? `Seek ${formatClock(atSec)} (−${preRoll}s) — recording is nearly silent`
        : play
          ? `Playing transition at ${formatClock(atSec)} (−${preRoll}s)`
          : `Stopped at ${formatClock(atSec)} (−${preRoll}s)`
    );
  };
  // Wait for media if needed (common right after switching mixes)
  if (audio.readyState >= 1) {
    apply();
  } else {
    let done = false;
    const once = () => {
      if (done) return;
      done = true;
      audio.removeEventListener("loadedmetadata", once);
      audio.removeEventListener("canplay", once);
      apply();
    };
    audio.addEventListener("loadedmetadata", once);
    audio.addEventListener("canplay", once);
    setTimeout(once, 500);
  }
}

function isPracticeMixNearlySilent() {
  const peaks = state.waveform?.peaks;
  if (!peaks?.length) return false;
  let mx = 0;
  for (const p of peaks) if (p > mx) mx = p;
  return mx < 0.02;
}

function handlePracticeWaveClick(e) {
  if (!isPracticeMode()) return;
  e.preventDefault();
  e.stopPropagation();
  const wrap = $("practiceWaveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !audio) return;
  const duration = practiceDuration(track, audio);
  if (!duration || duration <= 0) {
    setStatus("Wave not ready yet — wait a moment and click again.", "error");
    return;
  }
  const layout = practiceMapLayout(wrap, duration);
  const { contentWidth, slots, padX } = layout;
  const rect = wrap.getBoundingClientRect();
  const x = e.clientX - rect.left + (wrap.scrollLeft || 0);
  const t = practiceXToTime(x, slots, contentWidth, duration, padX);
  const txs = practiceTransitions();

  // Snap in pixel space so equal song-slots stay intuitive
  const snapPx = Math.max(28, contentWidth * 0.02);
  let nearest = null;
  let best = Infinity;
  for (const tx of txs) {
    const txX = practiceTimeToX(Number(tx.at_sec) || 0, slots, contentWidth, duration, padX);
    const d = Math.abs(txX - x);
    if (d < best) {
      best = d;
      nearest = tx;
    }
  }
  if (nearest && best <= snapPx) {
    seekPracticeTransition(Number(nearest.at_sec) || 0, {
      index: nearest.index,
    });
    return;
  }

  // Free scrub — always start playback
  const apply = () => {
    try {
      audio.currentTime = t;
    } catch {
      /* ignore */
    }
    const p = playAudio(audio);
    if (p && typeof p.catch === "function") {
      p.catch((err) => {
        setStatus(
          `Playback blocked: ${err?.message || "click Play once, then try again"}`,
          "error"
        );
      });
    }
    drawPracticeWaveform();
    ensurePracticePlayheadVisible(
      wrap,
      practiceTimeToX(t, slots, contentWidth, duration, padX)
    );
    setStatus(`Playing ${formatClock(t)}`);
  };
  if (audio.readyState >= 1) apply();
  else {
    audio.addEventListener("loadedmetadata", apply, { once: true });
  }
}

function bindPracticeWaveInteractions() {
  const wrap = $("practiceWaveformWrap");
  const canvas = $("practiceWaveformCanvas");
  if (!wrap || wrap.dataset.bound === "1") return;
  wrap.dataset.bound = "1";
  wrap.addEventListener("click", handlePracticeWaveClick);
  // Explicit canvas bind (status overlay uses pointer-events:none / hidden)
  if (canvas && canvas.dataset.bound !== "1") {
    canvas.dataset.bound = "1";
    canvas.addEventListener("click", handlePracticeWaveClick);
  }
  window.addEventListener("resize", () => {
    if (isPracticeMode()) drawPracticeWaveform();
  });
}

const $ = (id) => document.getElementById(id);

const ACCENT_THEME_KEY = "music-sorter-accent-theme";
const ACCENT_THEMES = new Set(["lime", "cyan", "violet", "coral"]);
const COLOR_SCHEME_KEY = "music-sorter-color-scheme";
const COLOR_SCHEMES = new Set(["dark", "light"]);

function storedAccentTheme() {
  try {
    const stored = window.localStorage.getItem(ACCENT_THEME_KEY);
    return ACCENT_THEMES.has(stored) ? stored : "lime";
  } catch {
    return "lime";
  }
}

function applyAccentTheme(theme, { persist = true } = {}) {
  const nextTheme = ACCENT_THEMES.has(theme) ? theme : "lime";
  state.accentTheme = nextTheme;
  document.documentElement.dataset.accentTheme = nextTheme;

  document.querySelectorAll("#accentPicker [data-accent-theme]").forEach((button) => {
    const isActive = button.dataset.accentTheme === nextTheme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });

  if (persist) {
    try {
      window.localStorage.setItem(ACCENT_THEME_KEY, nextTheme);
    } catch {
      /* Theme still applies when storage is unavailable. */
    }
  }

  requestAnimationFrame(() => drawWaveform());
}

function storedColorScheme() {
  try {
    const stored = window.localStorage.getItem(COLOR_SCHEME_KEY);
    return COLOR_SCHEMES.has(stored) ? stored : "dark";
  } catch {
    return "dark";
  }
}

function applyColorScheme(scheme, { persist = true } = {}) {
  const nextScheme = COLOR_SCHEMES.has(scheme) ? scheme : "dark";
  state.colorScheme = nextScheme;
  document.documentElement.dataset.colorScheme = nextScheme;
  document.documentElement.style.colorScheme = nextScheme;

  document.querySelectorAll("#schemePicker [data-color-scheme]").forEach((button) => {
    const isActive = button.dataset.colorScheme === nextScheme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });

  if (persist) {
    try {
      window.localStorage.setItem(COLOR_SCHEME_KEY, nextScheme);
    } catch {
      /* Scheme still applies when storage is unavailable. */
    }
  }

  requestAnimationFrame(() => drawWaveform());
}

let confirmTail = Promise.resolve();

function showConfirmDialog(opts) {
  const shown = confirmTail.then(() => showConfirmDialogUnlocked(opts));
  confirmTail = shown.then(
    () => undefined,
    () => undefined
  );
  return shown;
}

function waitForConfirmDialogIdle() {
  const dialog = $("confirmDialog");
  if (!dialog || !dialog.open) return Promise.resolve();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(), { once: true });
  });
}

async function showConfirmDialogUnlocked({
  title,
  track = "",
  message,
  note = "",
  confirmLabel = "Continue",
  tone = "accent",
  cancelOnly = false,
}) {
  const dialog = $("confirmDialog");
  if (!dialog || typeof dialog.showModal !== "function") {
    if (cancelOnly) {
      window.alert([title, track, message, note].filter(Boolean).join("\n\n"));
      return false;
    }
    return window.confirm([title, track, message, note].filter(Boolean).join("\n\n"));
  }
  if (dialog.open) await waitForConfirmDialogIdle();

  $("confirmTitle").textContent = title;
  $("confirmTrack").textContent = track;
  $("confirmTrack").hidden = !track;
  $("confirmMessage").textContent = message;
  $("confirmNote").textContent = note;
  $("confirmNote").hidden = !note;
  $("confirmAcceptBtn").textContent = confirmLabel;
  $("confirmAcceptBtn").hidden = cancelOnly;
  $("confirmCancelBtn").textContent = cancelOnly ? "Close" : "Cancel";
  dialog.className = `confirm-dialog tone-${tone}`;
  dialog.returnValue = "";

  const previousFocus = document.activeElement;
  return new Promise((resolve) => {
    const onBackdropClick = (event) => {
      if (event.target === dialog) dialog.close("cancel");
    };
    const onClose = () => {
      dialog.removeEventListener("click", onBackdropClick);
      $("confirmAcceptBtn").hidden = false;
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
      resolve(dialog.returnValue === "confirm");
    };

    dialog.addEventListener("click", onBackdropClick);
    dialog.addEventListener("close", onClose, { once: true });
    dialog.showModal();
  });
}

function resetWorkspaceScroll() {
  const player = document.querySelector(".panel.player");
  const reviewBody = document.querySelector("#reviewPanel .review-body");
  if (player) player.scrollTop = 0;
  if (reviewBody) reviewBody.scrollTop = 0;
}

function fmtBytes(n) {
  return MusicSorterTransport.fmtBytes(n);
}

function fmtTime(seconds) {
  return MusicSorterTransport.fmtTime(seconds);
}

function fmtTransportTime(seconds) {
  return MusicSorterTransport.fmtTransportTime(seconds);
}

function trackDisplayTitle(track) {
  return MusicSorterTransport.trackDisplayTitle(track);
}

function trackDisplayArtist(track) {
  return MusicSorterTransport.trackDisplayArtist(track);
}

/* Album cover in the banner: /api/cover is read-only (VDJ cover cache, else embedded art). Any miss keeps
   the placeholder. */
function renderTrackCover(track) {
  const art = $("trackArt");
  const img = $("trackArtImg");
  if (!art || !img) return;
  if (!track) {
    img.removeAttribute("src");
    img.hidden = true;
    img.dataset.path = "";
    art.classList.remove("has-cover");
    return;
  }
  if (img.dataset.path === track.path) return;
  img.dataset.path = track.path;
  img.hidden = true;
  art.classList.remove("has-cover");
  const qs = new URLSearchParams({
    path: track.path,
    artist: trackDisplayArtist(track) || "",
    title: trackDisplayTitle(track) || "",
  });
  img.onload = () => {
    if (img.dataset.path !== track.path) return;
    img.hidden = false;
    art.classList.add("has-cover");
  };
  img.onerror = () => {
    if (img.dataset.path !== track.path) return;
    img.hidden = true;
    art.classList.remove("has-cover");
  };
  img.src = `/api/cover?${qs.toString()}`;
}

function renderNowPlayingTitle(track) {
  const root = $("nowPlaying");
  renderTrackCover(track || null);
  if (!root || !track) return;

  const artist = trackDisplayArtist(track);
  const title = trackDisplayTitle(track);
  const label = artist ? `${artist} — ${title}` : title;

  root.title = label;
  root.dataset.path = track.path;
  root.setAttribute("aria-label", label);
  root.innerHTML = artist
    ? `<span class="now-playing-artist">${escapeHtml(artist)}</span>
       <span class="now-playing-separator" aria-hidden="true">—</span>
       <span class="now-playing-title">${escapeHtml(title)}</span>`
    : `<span class="now-playing-title">${escapeHtml(title)}</span>`;
}

function stepTrack(delta) {
  if (!currentTrack()) return;
  // Always walk the filtered list (search + readiness) so J/K skip hidden rows.
  const indexes = filteredTrackIndexes();
  if (!indexes.length) return;
  const position = indexes.indexOf(state.index);
  const nextPosition =
    position < 0 ? (delta > 0 ? 0 : indexes.length - 1) : position + delta;
  if (nextPosition >= 0 && nextPosition < indexes.length) {
    selectTrack(indexes[nextPosition]);
  }
}

function updateTransportUi() {
  const audio = $("audio");
  if (!audio) return;

  const duration = trackDuration(currentTrack(), audio);
  const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
  const progress = $("transportProgress");
  const playPause = $("playPauseBtn");
  const time = $("transportTime");
  const previous = $("previousTrackBtn");
  const next = $("nextTrackBtn");

  if (progress) {
    progress.max = String(duration || 0);
    progress.value = String(Math.min(current, duration || current || 0));
    progress.disabled = !duration;
  }
  if (time) {
    time.textContent = `${fmtTransportTime(current)} / ${fmtTransportTime(duration)}`;
  }
  if (playPause) {
    const isPlaying = Boolean(audio.src && !audio.paused);
    playPause.classList.toggle("is-playing", isPlaying);
    playPause.disabled = !audio.src;
    playPause.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
  }
  const bestPause = $("bestSetPauseBtn");
  if (bestPause) {
    const isPlaying = Boolean(audio.src && !audio.paused);
    bestPause.classList.toggle("is-playing", isPlaying);
    bestPause.disabled = !audio.src;
    bestPause.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
  }
  const bestStop = $("bestSetStopBtn");
  if (bestStop) bestStop.disabled = !audio.src;

  const indexes = filteredTrackIndexes();
  const position = indexes.indexOf(state.index);
  if (previous) previous.disabled = position <= 0;
  if (next) next.disabled = position < 0 || position >= indexes.length - 1;

  // Keep practice transition map playhead in sync.
  if (isPracticeMode()) drawPracticeWaveform();
}

function cueKey(point) {
  return MusicSorterTransport.cueKey(point);
}

function trackDuration(track, audio) {
  return MusicSorterTransport.trackDuration(track, audio);
}

function syncLoopPlayBtn() {
  const btn = $("loopPlayBtn");
  if (!btn) return;
  if ("checked" in btn) btn.checked = Boolean(state.loopPlaybackOn);
  btn.classList.toggle("active", state.loopPlaybackOn);
  btn.setAttribute("aria-pressed", state.loopPlaybackOn ? "true" : "false");
  const label = $("loopPlayToggleLabel");
  if (label) label.classList.toggle("is-on", Boolean(state.loopPlaybackOn));
}

function wantsExactCueJump(event) {
  const exact = Boolean(state.exactCueJump);
  if (event && (event.altKey || event.metaKey)) return !exact;
  return exact;
}

function syncExactCueJumpUi() {
  const input = $("exactCueJump");
  if (input) {
    input.checked = Boolean(state.exactCueJump);
    input.setAttribute("aria-checked", state.exactCueJump ? "true" : "false");
  }
  const label = $("prerollToggleLabel");
  if (label) {
    label.classList.toggle("is-exact", Boolean(state.exactCueJump));
    label.title = state.exactCueJump
      ? "Landing exactly on the marker. Uncheck (or hold Alt) to hear the approach."
      : "Approach: play a bar (or ~2s) before the marker. Hold Alt to land exactly.";
  }
}

function setExactCueJump(on) {
  state.exactCueJump = Boolean(on);
  syncExactCueJumpUi();
}

function jumpToCue(pos, point = null, event = null) {
  const audio = $("audio");
  if (!audio || !audio.src) return;
  state.seekGuardUntil = Date.now() + 1500;
  state.waveViewPinned = false;
  const markerPos = Math.max(0, Number(pos) || 0);
  const track = currentTrack();
  const bpm = onesBpm(track) || trackBpm(track);
  const exact = !point || wantsExactCueJump(event);
  const t = exact
    ? markerPos
    : MusicSorterTransport.cuePrerollTime(markerPos, bpm);
  const seek = () => {
    try {
      audio.currentTime = t;
    } catch {
      /* ignore seek race before metadata */
    }
    playAudio(audio).catch(() => {});
    if (point) {
      state.activeCueKey = cueKey(point);
      if (point.kind === "loop") {
        // Clicking / hotkeying a loop always starts looping that region.
        const wasOn = state.loopPlaybackOn;
        const prevKey = state.activeLoopKey;
        state.loopPlaybackOn = true;
        state.activeLoopKey = cueKey(point);
        state.loopApproachUntil =
          !exact && t < markerPos - 0.02 ? markerPos : null;
        syncLoopPlayBtn();
        const end =
          markerPos + loopDurationSeconds(point, trackBpm(currentTrack()));
        const approach =
          !exact && t < markerPos - 0.02
            ? ` · approach ${fmtTime(t)}`
            : "";
        setStatus(
          `Looping · ${point.name || "loop"} (${fmtTime(markerPos)}–${fmtTime(end)})${approach}`
        );
        if (!wasOn || prevKey !== state.activeLoopKey) {
          renderCues();
        } else {
          highlightActiveCue();
        }
      } else {
        // Normal cue: stop wrapping so playback continues past loop ends.
        const wasLooping = state.loopPlaybackOn || state.activeLoopKey;
        state.activeLoopKey = null;
        state.loopApproachUntil = null;
        if (state.loopPlaybackOn) {
          state.loopPlaybackOn = false;
          syncLoopPlayBtn();
          stopLoopWatch();
        }
        const approach =
          !exact && t < markerPos - 0.02
            ? ` from ${fmtTime(t)}`
            : "";
        setStatus(
          `Jumped to ${fmtTime(markerPos)}${point?.name ? ` · ${point.name}` : ""}${approach}`
        );
        if (wasLooping) renderCues();
        else highlightActiveCue();
      }
    } else {
      updatePlayhead();
      setStatus(`Jumped to ${fmtTime(t)}`);
    }
    updatePlayhead();
    if (state.loopPlaybackOn) startLoopWatch();
  };

  if (audio.readyState >= 1) seek();
  else audio.addEventListener("loadedmetadata", seek, { once: true });
}

function seekByBeat(direction, bar) {
  const audio = $("audio");
  if (!audio || !audio.src) return;
  const track = currentTrack();
  const bpm = onesBpm(track) || trackBpm(track);
  const duration = trackDuration(track, audio) || Number(audio.duration) || 0;
  const next = MusicSorterTransport.beatSeekTime(audio.currentTime, bpm, {
    direction,
    bar: Boolean(bar),
    duration,
  });
  state.waveViewPinned = false;
  try {
    audio.currentTime = next;
  } catch {
    /* ignore seek race */
  }
  updatePlayhead();
}

function isKeyboardOverlayOpen() {
  const overlay = $("keyboardOverlay");
  return Boolean(overlay && !overlay.hidden);
}

function setKeyboardOverlayOpen(open) {
  const overlay = $("keyboardOverlay");
  if (!overlay) return;
  overlay.hidden = !open;
  overlay.setAttribute("aria-hidden", open ? "false" : "true");
  document.body.classList.toggle("keyboard-overlay-open", Boolean(open));
  const btn = $("shortcutsHelpBtn");
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
}

function toggleKeyboardOverlay(force) {
  const next = typeof force === "boolean" ? force : !isKeyboardOverlayOpen();
  setKeyboardOverlayOpen(next);
}

/** Wall-clock windows for VDJ loop markers (start → end via size beats + BPM). */
function getLoopWindows(track) {
  if (!track) return [];
  const bpm = trackBpm(track);
  return (track.cues?.points || [])
    .filter((p) => p.kind === "loop")
    .map((p) => {
      const start = Number(p.pos) || 0;
      const len = loopDurationSeconds(p, bpm);
      return {
        point: p,
        start,
        end: start + len,
        key: cueKey(p),
        len,
      };
    })
    .filter((w) => w.len > 0.05);
}

function stopLoopWatch() {
  if (state.loopRaf != null) {
    cancelAnimationFrame(state.loopRaf);
    state.loopRaf = null;
  }
}

function startLoopWatch() {
  if (!state.loopPlaybackOn) {
    stopLoopWatch();
    return;
  }
  if (state.loopRaf != null) return;
  const tick = () => {
    maybeLoopPlayback();
    const audio = $("audio");
    if (state.loopPlaybackOn && audio && !audio.paused) {
      state.loopRaf = requestAnimationFrame(tick);
    } else {
      state.loopRaf = null;
    }
  };
  state.loopRaf = requestAnimationFrame(tick);
}

/**
 * When loop playback is on, wrap the playhead at the end of the active (or
 * enclosing) VDJ loop so you can audition seamless loop points.
 */
function maybeLoopPlayback() {
  if (!state.loopPlaybackOn) return;
  const audio = $("audio");
  if (!audio || audio.paused || !audio.src) return;
  const track = currentTrack();
  const windows = getLoopWindows(track);
  if (!windows.length) return;

  const t = audio.currentTime;
  // Small pad so we catch the end even if a frame lands slightly past it.
  const endPad = 0.03;

  let win = null;
  if (state.activeLoopKey) {
    win = windows.find((w) => w.key === state.activeLoopKey) || null;
  }

  // Drop active loop if user seeked well outside it — keep it armed
  // while a cue-review preroll is approaching the loop start.
  const approaching =
    win &&
    state.loopApproachUntil != null &&
    t < win.start &&
    Math.abs(Number(state.loopApproachUntil) - win.start) < 0.05;
  if (win && approaching) {
    /* stay armed until the playhead reaches the loop */
  } else if (win && (t < win.start - 0.25 || t > win.end + 0.35)) {
    win = null;
    state.activeLoopKey = null;
    state.loopApproachUntil = null;
  } else if (win && t >= win.start - 0.02) {
    state.loopApproachUntil = null;
  }

  // Auto-engage a loop the playhead is currently inside.
  if (!win) {
    win =
      windows.find((w) => t >= w.start - 0.02 && t < w.end - endPad) || null;
    if (win) {
      state.activeLoopKey = win.key;
      state.activeCueKey = win.key;
      highlightActiveCue();
    }
  }

  if (!win) return;

  if (t >= win.end - endPad) {
    try {
      // Nudge slightly past start so repeated wraps don't stick on the boundary.
      audio.currentTime = win.start + 0.001;
    } catch {
      /* ignore */
    }
    updatePlayhead();
  }
}

function setLoopPlayback(on) {
  state.loopPlaybackOn = Boolean(on);
  syncLoopPlayBtn();
  if (!state.loopPlaybackOn) {
    state.activeLoopKey = null;
    stopLoopWatch();
    setStatus("Loop play off — continuous playback");
    highlightActiveCue();
    renderCues();
    return;
  }

  const windows = getLoopWindows(currentTrack());
  if (!windows.length) {
    setStatus("Loop play on — this track has no loop markers", "error");
    renderCues();
    return;
  }

  // If already inside a loop (or last active cue is a loop), engage it.
  const audio = $("audio");
  const t = audio?.currentTime || 0;
  let win = windows.find((w) => t >= w.start - 0.02 && t < w.end) || null;
  if (!win && state.activeCueKey) {
    win = windows.find((w) => w.key === state.activeCueKey) || null;
  }
  if (win) {
    state.activeLoopKey = win.key;
    state.activeCueKey = win.key;
    setStatus(
      `Loop play on · ${win.point.name || "loop"} (${fmtTime(win.start)}–${fmtTime(
        win.end
      )}) — click a loop or play into one`
    );
    if (audio && !audio.paused) startLoopWatch();
  } else {
    setStatus(
      `Loop play on · ${windows.length} loop(s) — click a loop cue or play into a region`
    );
  }
  renderCues();
}

function toggleLoopPlayback() {
  setLoopPlayback(!state.loopPlaybackOn);
}

function highlightActiveCue() {
  document.querySelectorAll(".cue-row").forEach((row) => {
    row.classList.toggle("active", row.dataset.key === state.activeCueKey);
  });
}

function startPlayheadWatch() {
  if (state.playheadRaf) return;
  const tick = () => {
    const audio = $("audio");
    if (!audio || audio.paused || audio.ended) {
      state.playheadRaf = null;
      updatePlayhead();
      return;
    }
    updatePlayhead();
    state.playheadRaf = requestAnimationFrame(tick);
  };
  state.playheadRaf = requestAnimationFrame(tick);
}

function stopPlayheadWatch() {
  if (!state.playheadRaf) return;
  cancelAnimationFrame(state.playheadRaf);
  state.playheadRaf = null;
}

function updatePlayhead() {
  const audio = $("audio");
  const playhead = $("cuePlayhead");
  const track = currentTrack();
  if (!audio || !track) return;
  const duration = trackDuration(track, audio);
  if (playhead) {
    if (!duration) {
      playhead.style.left = "0%";
    } else {
      const pct = Math.min(100, Math.max(0, (audio.currentTime / duration) * 100));
      playhead.style.left = `calc(10px + (100% - 20px) * ${pct / 100})`;
    }
  }
  const playing = !audio.paused && !audio.ended;
  if (playing) startPlayheadWatch();
  else stopPlayheadWatch();
  const now = performance.now();
  if (playing) {
    if (now - (state.lastDrawMs || 0) > 16) {
      state.lastDrawMs = now;
      syncMovingPlayhead();
    }
  } else if (now - (state.lastDrawMs || 0) > 80) {
    state.lastDrawMs = now;
    drawWaveform();
  }
}

/** Move the overlay needle; full redraw only when the view pages. */
function syncMovingPlayhead() {
  const audio = $("audio");
  const track = currentTrack();
  if (!audio || !track) return;
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  if (!duration || !Number.isFinite(audio.currentTime)) return;
  if (state.loopDrag) return; // no paging of the view under a live drag
  const prevOffset = state.waveOffset;
  const view = applyPlayheadFollow(duration, audio.currentTime);
  if (view.start !== prevOffset) {
    drawWaveform();
    return;
  }
  const wrap = $("waveformWrap");
  const cssW = wrap?.clientWidth || 600;
  const { padX, plotW } = wavePlotMetrics(cssW);
  positionWavePlayhead(null, audio, view, padX, plotW, 0);
}

function setWaveformStatus(text, kind = "", action = null) {
  const el = $("waveformStatus");
  if (!el) return;
  // Never cover an already-drawn waveform with the gray loading overlay.
  if (text && !kind && waveformLoadedFor(currentTrack())) {
    el.className = "waveform-status hidden";
    el.replaceChildren();
    return;
  }
  if (!text) {
    el.className = "waveform-status hidden";
    el.replaceChildren();
    return;
  }
  el.className = `waveform-status ${kind}`.trim();
  el.replaceChildren();
  const span = document.createElement("span");
  span.className = "waveform-status-text";
  span.textContent = text;
  el.appendChild(span);
  if (action && action.label && typeof action.onClick === "function") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn ghost waveform-retry-btn";
    btn.textContent = action.label;
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      action.onClick();
    });
    el.appendChild(btn);
  }
}

/* R-100: never show raw server text (e.g. "Internal Server Error"); calm words + Retry. */
function plainWaveformError(err) {
  const raw = String((err && err.message) || err || "");
  if (/abort/i.test(raw)) return null;
  if (/failed to fetch|network|not reachable|can't be reached|load failed|timed out/i.test(raw)) {
    return "The waveform could not be loaded (server unreachable). Check that Music Sorter is running, then Retry.";
  }
  if (/internal server error|status of 5\d\d|HTTP 5/i.test(raw)) {
    return "The waveform could not be loaded (server error). Nothing is wrong with the song file — press Retry.";
  }
  if (/not found|404/i.test(raw)) {
    return "No waveform for this song (file missing or unreadable). Try another song, or Retry.";
  }
  // Strip jargon / raw status lines
  const calm = raw.replace(/^FAILED:\s*/i, "").replace(/^Error:\s*/i, "").trim();
  if (!calm || /internal server/i.test(calm)) {
    return "The waveform could not be loaded. Press Retry.";
  }
  return `The waveform could not be loaded. ${calm} — press Retry.`;
}

function setRetryStatus(text, kind = "") {
  const el = $("retryStatus");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    el.className = "retry-status";
    return;
  }
  el.hidden = false;
  el.className = `retry-status ${kind}`.trim();
  el.textContent = text;
}

function stopRetryPoll() {
  // Stop every per-path poller (legacy helper name kept for call sites).
  for (const path of Object.keys(state.retryJobs || {})) {
    stopRetryPollForPath(path);
  }
}

/** First-time cueing (Not cued filter / uncued track) vs re-cueing existing markers. */
function isFirstTimeCueing(track = currentTrack()) {
  if (state.readinessFilter === "not_cued") return true;
  const status = trackReadinessStatus(track);
  if (status === "not_cued" || status === "missing") return true;
  if (track && track.is_cued === false) return true;
  return false;
}

function autocueActionLabels(busy = false) {
  const first = isFirstTimeCueing();
  if (busy) {
    return {
      both: first ? "Adding…" : "Retrying…",
      cues: first ? "Adding…" : "Retrying…",
      loops: first ? "Adding…" : "Retrying…",
      bothReview: first ? "Adding…" : "Retrying…",
      cuesReview: first ? "Adding…" : "Retrying…",
      loopsReview: first ? "Adding…" : "Retrying…",
      bothSide: first ? "↻ Running AutoCue…" : "↻ Retrying AutoCue…",
      cuesSide: "↻ Running…",
      loopsSide: "↻ Running…",
      section: first ? "Add cues" : "Fix cues",
    };
  }
  if (first) {
    return {
      both: "Both",
      cues: "Cues",
      loops: "Loops",
      bothReview: "Add both",
      cuesReview: "Add cues",
      loopsReview: "Add loops",
      bothSide: "↻ Add both (cues + loops)",
      cuesSide: "↻ Cues only",
      loopsSide: "↻ Loops only",
      section: "Add cues",
      titleBoth: "First-time AutoCue: write cues and loops",
      titleCues: "Write cue points only (unusual for first-time — prefer Both)",
      titleLoops: "Write loops only (unusual for first-time — prefer Both)",
    };
  }
  return {
    both: "Both",
    cues: "Cues",
    loops: "Loops",
    bothReview: "Retry both",
    cuesReview: "Retry cues",
    loopsReview: "Retry loops",
    bothSide: "↻ Retry both (cues + loops)",
    cuesSide: "↻ Retry cues only",
    loopsSide: "↻ Retry loops only",
    section: "Fix cues",
    titleBoth: "Re-run AutoCue for cues and loops (overwrites both)",
    titleCues: "Re-run AutoCue for cue points only — keeps existing loops",
    titleLoops: "Re-run AutoCue for loops only — keeps existing cue points",
  };
}

const AUTO_CUE_SCOPE_BUTTONS = [
  // Primary AutoCue controls live only in the Cue review side panel.
  { id: "retryBothBtnSide", scope: "all", labelKey: "bothSide", titleKey: "titleBoth" },
  {
    id: "retryCuesOnlyBtnSide",
    scope: "cues",
    labelKey: "cuesSide",
    titleKey: "titleCues",
  },
  {
    id: "retryLoopsOnlyBtnSide",
    scope: "loops",
    labelKey: "loopsSide",
    titleKey: "titleLoops",
  },
];

const AUTOCUE_ACTIVE_STATUSES = new Set([
  "starting",
  "queued",
  "running",
]);

function isAutocueJobActive(job) {
  return Boolean(job && AUTOCUE_ACTIVE_STATUSES.has(job.status));
}

const AUTOCUE_SESSION_KEY = "ms.autocue.activeJobs";

function rememberActiveAutocueJobs() {
  try {
    const jobs = activeRetryJobs()
      .filter((j) => j.id && j.path)
      .map((j) => ({
        id: j.id,
        path: j.path,
        name: j.name,
        status: j.status,
        message: j.message,
        writeScope: j.writeScope,
      }));
    sessionStorage.setItem(AUTOCUE_SESSION_KEY, JSON.stringify(jobs));
  } catch {
    /* private mode */
  }
}

function readRememberedAutocueJobs() {
  try {
    const raw = sessionStorage.getItem(AUTOCUE_SESSION_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function attachAutocueJob(job, { startPoll = true } = {}) {
  if (!job?.path) return false;
  const pathKey = job.path;
  const existing = state.retryJobs[pathKey];
  if (existing?.pollTimer && existing.id === job.id) {
    existing.status = job.status || existing.status;
    existing.message = job.message || existing.message;
    return false;
  }
  if (existing?.pollTimer) stopRetryPollForPath(pathKey);
  state.retryJobs[pathKey] = {
    id: job.id,
    path: pathKey,
    name: job.name || existing?.name || pathKey,
    message: job.message || "Running AutoCue…",
    status: job.status || "running",
    writeScope: job.write_scope || job.writeScope || existing?.writeScope,
    pollTimer: null,
  };
  if (startPoll && job.id && isAutocueJobActive(job)) {
    startRetryPoll(pathKey, job.id);
  }
  return true;
}

function restoreRememberedAutocueJobs() {
  let n = 0;
  for (const job of readRememberedAutocueJobs()) {
    if (!isAutocueJobActive(job) || !job.path) continue;
    if (attachAutocueJob(job, { startPoll: Boolean(job.id) })) n += 1;
  }
  return n;
}

function activeRetryJobs() {
  return Object.values(state.retryJobs || {}).filter(isAutocueJobActive);
}

function isAutocueJobRunning() {
  return activeRetryJobs().length > 0 || Boolean(state.batchPollTimer);
}

function retryJobForPath(path) {
  if (!path) return null;
  return state.retryJobs[path] || null;
}

function isTrackCueing(track) {
  if (!track?.path) return false;
  if (isAutocueJobActive(retryJobForPath(track.path))) return true;
  return (state.batchCueingPaths || []).includes(track.path);
}

function cueingTrackIndexes() {
  return state.tracks
    .map((t, i) => i)
    .filter((i) => isTrackCueing(state.tracks[i]));
}

function cueingListSignature() {
  const jobs = activeRetryJobs()
    .map((j) => j.path)
    .filter(Boolean)
    .sort();
  const batch = (state.batchCueingPaths || []).slice().sort();
  return `${jobs.join("|")}::${batch.join("|")}`;
}

function updateCueingFilterUi() {
  const btn = $("crateFilterCueing");
  if (!btn) return;
  const unique = new Set([
    ...activeRetryJobs().map((j) => j.path),
    ...(state.batchCueingPaths || []),
  ]);
  const count = unique.size;
  btn.textContent = count ? `Cueing · ${count}` : "Cueing";
  btn.classList.toggle("is-live", count > 0);
  btn.title = count
    ? `${count} track${count === 1 ? "" : "s"} AutoCueing now`
    : "Tracks AutoCue is working on";
}

/** True when the current track already has an AutoCue job in flight. */
function isAutocueBusyForCurrentTrack() {
  const current = currentTrack()?.path;
  if (!current) return false;
  if (isAutocueJobActive(retryJobForPath(current))) return true;
  return (state.batchCueingPaths || []).includes(current);
}

function startRetryPoll(pathKey, jobId) {
  const entry = state.retryJobs[pathKey];
  if (!entry || !jobId) return;
  if (entry.pollTimer) return;
  entry.id = jobId;
  entry.pollTimer = setInterval(async () => {
    const liveGate = state.retryJobs[pathKey];
    if (!liveGate || liveGate.id !== jobId) return;
    if (liveGate._pollInFlight) return;
    liveGate._pollInFlight = true;
    try {
      const res = await api(`/api/retry-cues/${jobId}`);
      const j = res.job;
      const live = state.retryJobs[pathKey];
      if (!live || live.id !== jobId) return;

      if (j.status === "running" || j.status === "queued") {
        live.status = j.status;
        live.message = j.message || "Running AutoCue…";
        rememberActiveAutocueJobs();
        syncAutocueUi();
        return;
      }

      stopRetryPollForPath(pathKey);
      const finishedName = live.name || j.name;
      delete state.retryJobs[pathKey];
      rememberActiveAutocueJobs();
      syncAutocueUi();

      if (j.status === "ok") {
        const doneMsg =
          `${j.message} (was ${j.cue_count_before} cues)` +
          (j.cue_count_after != null ? ` → ${j.cue_count_after}` : "");
        if (currentTrack()?.path === pathKey) {
          setRetryStatus(doneMsg, "ok");
        }
        setStatus(`AutoCue done: ${finishedName}`, "success");
        scheduleLoadTracks({ keepPath: currentTrack()?.path, silent: true });
        if (currentTrack()?.path === pathKey) {
          await loadDeepGridPreflight(currentTrack(), state.trackGen);
        }
        syncAutocueUi();
      } else {
        if (currentTrack()?.path === pathKey) {
          setRetryStatus(j.message || "AutoCue failed", "error");
        }
        setStatus(
          j.message
            ? `${finishedName}: ${j.message}`
            : `AutoCue failed: ${finishedName}`,
          "error"
        );
      }
    } catch (err) {
      const gone = /not found|404/i.test(String(err.message || ""));
      if (!gone) {
        // Keep Cueing across refresh / blip. Do not drop the job.
        return;
      }
      stopRetryPollForPath(pathKey);
      delete state.retryJobs[pathKey];
      rememberActiveAutocueJobs();
      syncAutocueUi();
      if (currentTrack()?.path === pathKey) {
        setRetryStatus(err.message, "error");
      }
      setStatus(err.message, "error");
    } finally {
      const liveEnd = state.retryJobs[pathKey];
      if (liveEnd) liveEnd._pollInFlight = false;
    }
  }, 2000);
}

async function hydrateAutocueJobs() {
  restoreRememberedAutocueJobs();
  const remembered = readRememberedAutocueJobs();
  const data = await api("/api/retry-cues", { timeoutMs: 8000 }).catch(() => null);
  const jobs = data?.jobs || [];
  const byId = new Map(jobs.filter((j) => j && j.id).map((j) => [j.id, j]));
  let attached = 0;
  for (const job of jobs) {
    if (!isAutocueJobActive(job) || !job.path) continue;
    if (attachAutocueJob(job)) attached += 1;
  }
  for (const mem of remembered) {
    if (!mem.id || byId.has(mem.id)) continue;
    const res = await api(`/api/retry-cues/${mem.id}`, { timeoutMs: 5000 }).catch(
      () => null
    );
    const job = res?.job;
    if (job && isAutocueJobActive(job)) {
      if (attachAutocueJob(job)) attached += 1;
    } else if (job && !isAutocueJobActive(job)) {
      stopRetryPollForPath(mem.path);
      delete state.retryJobs[mem.path];
    }
  }
  const batchPaths = [];
  for (const batch of data?.batches || []) {
    if (batch.status !== "queued" && batch.status !== "running") continue;
    for (const item of batch.items || []) {
      if (item.path && isAutocueJobActive(item)) batchPaths.push(item.path);
    }
  }
  state.batchCueingPaths = batchPaths;
  rememberActiveAutocueJobs();
  syncAutocueUi();
  return attached;
}

function stopRetryPollForPath(path) {
  const job = retryJobForPath(path);
  if (job?.pollTimer) {
    clearInterval(job.pollTimer);
    job.pollTimer = null;
  }
}

/**
 * Refresh AutoCue buttons + status for the *current* track.
 * Multiple jobs can run (queued/active); only *this* track shows Retrying…
 * and has its AutoCue buttons disabled. Other tracks stay startable.
 */
function syncAutocueUi() {
  const busyHere = isAutocueBusyForCurrentTrack();
  const labels = autocueActionLabels(busyHere);
  const first = isFirstTimeCueing();
  const others = activeRetryJobs().filter((j) => j.path !== currentTrack()?.path);
  const here = retryJobForPath(currentTrack()?.path);
  const busyMsg = here?.message || "AutoCue is running on this track…";

  for (const spec of AUTO_CUE_SCOPE_BUTTONS) {
    const btn = $(spec.id);
    if (!btn) continue;
    // Only block starting another job on *this* track while it's in flight.
    btn.disabled = busyHere;
    btn.setAttribute("aria-busy", busyHere ? "true" : "false");
    btn.classList.toggle("is-autocue-busy", busyHere);
    const text =
      labels[spec.labelKey] ||
      (spec.scope === "cues"
        ? labels.cues
        : spec.scope === "loops"
          ? labels.loops
          : labels.both);
    btn.textContent = text;
    if (busyHere) {
      btn.title = busyMsg;
    } else if (others.length) {
      btn.title = `Start AutoCue on this track (${others.length} other job${
        others.length === 1 ? "" : "s"
      } in progress)`;
    } else if (labels[spec.titleKey]) {
      btn.title = labels[spec.titleKey];
    }
    if (spec.id === "retryBothBtnSide") {
      btn.classList.toggle("primary", first && !busyHere);
    }
  }

  // Side panel: indeterminate progress + busy chrome while this track runs.
  for (const stackId of ["autocueScopeSide", "autocueScopeReview", "autocueScopeHeader"]) {
    const stack = $(stackId);
    if (stack) stack.classList.toggle("is-autocue-busy", busyHere);
  }
  const busyBar = $("autocueBusyBar");
  if (busyBar) {
    busyBar.hidden = !busyHere;
    busyBar.setAttribute("aria-hidden", busyHere ? "false" : "true");
  }
  const busyLabel = $("autocueBusyLabel");
  if (busyLabel) {
    busyLabel.hidden = !busyHere;
    if (busyHere) busyLabel.textContent = busyMsg;
  }

  const section = $("autocueSectionLabel");
  if (section && labels.section) section.textContent = labels.section;
  const header = $("autocueScopeHeader");
  if (header) header.hidden = !isReviewMode();

  // Sticky topbar chip for any in-flight AutoCue jobs.
  const chip = $("autocueJobChip");
  if (chip) {
    const active = activeRetryJobs();
    const batchOn = Boolean(state.batchPollTimer);
    if (active.length || batchOn) {
      chip.hidden = false;
      chip.classList.toggle("is-error", false);
      const names = active.map((j) => j.name || "track").slice(0, 2).join(", ");
      chip.textContent = batchOn
        ? `AutoCue batch running…`
        : `AutoCue ${active.length} running${names ? ` · ${names}` : ""}`;
      chip.title = active.map((j) => `${j.name}: ${j.message || j.status}`).join("\n");
    } else {
      chip.hidden = true;
      chip.textContent = "";
    }
  }

  const batchBtn = $("batchAddCuesBtn");
  if (batchBtn && !batchBtn.hidden) {
    const n = state.tracks.filter((t) => {
      const st = trackReadinessStatus(t);
      return st === "not_cued" || st === "missing";
    }).length;
    // Batch still blocked while a batch is active; single-track jobs OK.
    batchBtn.disabled = !n || Boolean(state.batchPollTimer);
  }

  // Status strip for the current view.
  if (busyHere && here?.message) {
    setRetryStatus(here.message, "running");
  } else if (others.length) {
    const strip = $("retryStatus");
    const kind = strip?.className || "";
    const keepFinal = /\b(ok|error)\b/.test(kind);
    if (!keepFinal) {
      const names = others
        .slice(0, 2)
        .map((j) => j.name || "track")
        .join(", ");
      const more = others.length > 2 ? ` +${others.length - 2} more` : "";
      setRetryStatus(
        `${others.length} AutoCue job${others.length === 1 ? "" : "s"} on other tracks: ${names}${more}`,
        "running"
      );
    }
  }
  // If nothing running, leave last success/error message alone.
  updateCueingFilterUi();
  const cueSig = cueingListSignature();
  if (isReviewMode() && cueSig !== state.cueingListSig) {
    state.cueingListSig = cueSig;
    renderTrackList();
    if (state.crateFilter === "cueing") {
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
      }
    }
  }
}

function updateAutocueButtonLabels(busy = false) {
  // Prefer path-aware sync; `busy` is kept for call-site compatibility.
  // Always go through syncAutocueUi so disabled/loading state never drifts.
  void busy;
  syncAutocueUi();
}

function setAutocueButtonsBusy(_busy) {
  syncAutocueUi();
}

async function refreshCurrentTrackCues() {
  const track = currentTrack();
  if (!track) return;
  // Reload full track list so placements + cues stay consistent
  await loadTracks({ keepPath: track.path });
}

function writeScopeMessage(scope, first) {
  if (scope === "cues") {
    return first
      ? "AutoCue will write cue points only (no loops)."
      : "AutoCue will rewrite cue points only and keep existing loops.";
  }
  if (scope === "loops") {
    return first
      ? "AutoCue will write loops only (no cue points)."
      : "AutoCue will rewrite loops only and keep existing cue points.";
  }
  return first
    ? "AutoCue will write cue points and loops into VirtualDJ for this file."
    : "AutoCue will overwrite existing cue points and loops for this file.";
}

async function retryCuesForCurrentTrack(writeScope = "all") {
  const cap = captureSong(); // identity at CLICK time
  if (!guardSong(cap, "AutoCue")) return;
  return retryCuesForTrack(currentTrack(), writeScope, { cap });
}

async function retryCuesForTrack(track, writeScope = "all", opts = {}) {
  if (!track) return;
  if (!isReviewMode()) {
    setStatus("Switch to Add Cues to run AutoCue.", "error");
    return;
  }

  const pathKey = track.path;
  historyClear(pathKey); // AutoCue rewrites the markers: earlier undo snapshots would no longer fit
  // Immediate lock so double-clicks / re-clicks can't start another job
  // while preflight or the confirm dialog is open.
  if (isAutocueJobActive(retryJobForPath(pathKey))) {
    setStatus("AutoCue already running on this track.", "error");
    syncAutocueUi();
    return;
  }

  const scope = ["cues", "loops"].includes(writeScope) ? writeScope : "all";
  const first = isFirstTimeCueing(track);
  const scopeWord =
    scope === "cues" ? "cues only" : scope === "loops" ? "loops only" : "cues + loops";
  const actionWord = first ? `Add ${scopeWord}` : `Retry ${scopeWord}`;
  const othersSubmitted = activeRetryJobs().some(
    (j) =>
      j.path &&
      j.path !== pathKey &&
      (j.status === "queued" || j.status === "running")
  );
  // Skip extra confirms only after another job is actually accepted.
  const queueAlongside = othersSubmitted;
  const applyIfCurrent = (fn) => {
    if (currentTrack()?.path === pathKey) fn();
  };

  // Mark starting *before* preflight so side buttons show loading immediately.
  stopRetryPollForPath(pathKey);
  state.retryJobs[pathKey] = {
    id: null,
    path: pathKey,
    name: track.name,
    message: `Checking beatgrid…`,
    status: "starting",
    writeScope: scope,
    pollTimer: null,
  };
  syncAutocueUi();

  // Deep grid preflight before confirming (skip deep if user already confirmed).
  let gridConfirmed = Boolean(
    state.gridManualConfirmed && state.gridManualConfirmed[pathKey]
  );
  applyIfCurrent(() =>
    setRetryStatus(
      gridConfirmed ? "Using confirmed beatgrid…" : "Checking beatgrid…",
      "running"
    )
  );
  let preflight = null;
  try {
    const pf = await api(
      `/api/grid-preflight?path=${encodeURIComponent(track.path)}&deep=${
        gridConfirmed ? "false" : "true"
      }`
    );
    preflight = pf.preflight;
    if (gridConfirmed && preflight) {
      preflight = {
        ...preflight,
        can_autocue: preflight.bpm != null && preflight.grid_anchor != null
          ? true
          : preflight.can_autocue,
        manual_required: false,
        status: preflight.can_autocue === false ? preflight.status : "warn",
        label:
          preflight.can_autocue === false
            ? preflight.label
            : "Grid manually confirmed",
      };
    }
    applyIfCurrent(() => {
      state.gridPreflight = preflight;
      renderGridPreflightCard(track);
    });
  } catch (err) {
    delete state.retryJobs[pathKey];
    syncAutocueUi();
    applyIfCurrent(() =>
      setRetryStatus(`Grid check failed: ${err.message}`, "error")
    );
    setStatus(`Grid check failed (${track.name}): ${err.message}`, "error");
    return;
  }

  if (preflight && !preflight.can_autocue && !gridConfirmed) {
    const reasons = (preflight.issues || []).join("\n• ") || preflight.label;
    const confirmable =
      Boolean(preflight.manual_confirmable) ||
      // Structural grid present (BPM + anchor) but deep onset failed.
      (Boolean(preflight.bpm) &&
        preflight.grid_anchor != null &&
        preflight.manual_required &&
        /onset energy is too weak/i.test(reasons));

    if (confirmable) {
      applyIfCurrent(() =>
        setRetryStatus(preflight.label || "Grid not auto-verified", "error")
      );
      await waitForConfirmDialogIdle();
      const proceed = await showConfirmDialog({
        title: "Beatgrid needs attention",
        track: trackDisplayTitle(track),
        message: `• ${reasons}`,
        note:
          "If you already set the '1' in VirtualDJ and it sounds right, confirm the grid to run AutoCue anyway (skips automatic onset verification).",
        confirmLabel: "Grid is correct — AutoCue",
        tone: "warning",
        cancelOnly: false,
      });
      if (!proceed) {
        delete state.retryJobs[pathKey];
        syncAutocueUi();
        applyIfCurrent(() => setRetryStatus("", ""));
        return;
      }
      state.gridManualConfirmed[pathKey] = true;
      gridConfirmed = true;
      // Treat as OK for the rest of this flow.
      preflight = {
        ...preflight,
        can_autocue: true,
        manual_required: false,
        status: "warn",
        label: "Grid manually confirmed",
        warnings: [
          ...(preflight.warnings || []),
          "User confirmed the VirtualDJ beatgrid after weak onset verification.",
        ],
      };
      applyIfCurrent(() => {
        state.gridPreflight = preflight;
        renderGridPreflightCard(track);
      });
    } else {
      delete state.retryJobs[pathKey];
      syncAutocueUi();
      applyIfCurrent(() =>
        setRetryStatus(preflight.label || "Blocked — fix grid in VDJ", "error")
      );
      setStatus(`Cannot AutoCue ${track.name}: ${preflight.label}`, "error");
      await waitForConfirmDialogIdle();
      await showConfirmDialog({
        title: "Beatgrid needs attention",
        track: trackDisplayTitle(track),
        message: `• ${reasons}`,
        note: "Align the grid in VirtualDJ first (BPM + '1'), then try AutoCue again.",
        confirmLabel: "Close",
        tone: "warning",
        cancelOnly: true,
      });
      return;
    }
  }

  // Keep buttons locked while the confirm dialog is open.
  const liveStart = state.retryJobs[pathKey];
  if (liveStart) {
    liveStart.message = queueAlongside
      ? `Queuing AutoCue (${scopeWord})…`
      : `Waiting for confirm (${scopeWord})…`;
    liveStart.status = "starting";
  }
  syncAutocueUi();

  const gridNote = preflight?.needs_align
    ? " The beatgrid may be misaligned. Align grid first — AutoCue will not move the '1'."
    : "";
  if (!queueAlongside) {
    await waitForConfirmDialogIdle();
    const ok = await showConfirmDialog({
      title: first ? `Add ${scopeWord}?` : `Retry ${scopeWord}?`,
      track: trackDisplayTitle(track),
      message: writeScopeMessage(scope, first),
      note: `Keep VirtualDJ closed while AutoCue updates its database.${gridNote}`,
      confirmLabel: first ? "Run AutoCue" : "Run AutoCue",
      tone: first && scope === "all" ? "accent" : "warning",
    });
    if (!ok) {
      delete state.retryJobs[pathKey];
      syncAutocueUi();
      applyIfCurrent(() => setRetryStatus("", ""));
      return;
    }
  }

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    if (queueAlongside) {
      // Another AutoCue job is already writing; don't block the queue on VDJ.
      allowRunning = true;
    } else {
      await waitForConfirmDialogIdle();
      allowRunning = await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "Cue changes may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
        confirmLabel: "Continue anyway",
        tone: "warning",
      });
      if (!allowRunning) {
        delete state.retryJobs[pathKey];
        syncAutocueUi();
        applyIfCurrent(() => setRetryStatus("", ""));
        setStatus(`Close VirtualDJ, then ${actionWord.toLowerCase()}.`, "error");
        return;
      }
    }
  }

  state.retryJobs[pathKey] = {
    id: null,
    path: pathKey,
    name: track.name,
    message: `Starting AutoCue (${scopeWord})…`,
    status: "queued",
    writeScope: scope,
    pollTimer: null,
  };
  syncAutocueUi();
  setStatus(
    queueAlongside
      ? `Queued AutoCue (${scopeWord}): ${track.name}`
      : `AutoCue (${scopeWord}): ${track.name}…`
  );

  try {
    const data = await api("/api/retry-cues", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
        require_grid: true,
        // Skip deep onset re-check when user confirmed the VDJ grid manually.
        deep_grid_check: !gridConfirmed,
        write_scope: scope,
      }),
    });
    const job = data.job;
    const entry = state.retryJobs[pathKey] || {};
    entry.id = job.id;
    entry.path = job.path || pathKey;
    entry.name = job.name || track.name;
    entry.status = job.status || "queued";
    entry.message = job.message || "Queued…";
    state.retryJobs[pathKey] = entry;

    if (job.status === "skipped") {
      stopRetryPollForPath(pathKey);
      delete state.retryJobs[pathKey];
      syncAutocueUi();
      const needsStems =
        job.preflight &&
        job.preflight.has_stems === false &&
        (job.write_scope || "all") !== "cues";
      const skipMsg =
        job.message ||
        (needsStems
          ? "Go make stems in VirtualDJ first, then AutoCue again."
          : "Skipped — fix beatgrid first");
      applyIfCurrent(() => setRetryStatus(skipMsg, "error"));
      setStatus(`${track.name}: ${skipMsg}`, "error");
      if (job.preflight && currentTrack()?.path === pathKey) {
        state.gridPreflight = job.preflight;
        renderGridPreflightCard(track);
      }
      return;
    }

    rememberActiveAutocueJobs();
    syncAutocueUi();
    startRetryPoll(pathKey, job.id);
  } catch (err) {
    stopRetryPollForPath(pathKey);
    delete state.retryJobs[pathKey];
    rememberActiveAutocueJobs();
    syncAutocueUi();
    applyIfCurrent(() => setRetryStatus(err.message, "error"));
    setStatus(`${track.name}: ${err.message}`, "error");
  }
}

function stopBatchPoll() {
  if (state.batchPollTimer) {
    clearInterval(state.batchPollTimer);
    state.batchPollTimer = null;
  }
}

function pajamathonNotCuedCount() {
  return state.tracks.filter((t) => {
    const st = trackReadinessStatus(t);
    return (
      addCuesSection(t) === "pajamathon" &&
      (st === "not_cued" || st === "missing")
    );
  }).length;
}

async function batchAddCuesForNotCued(scope = "all") {
  if (!isReviewMode()) return;
  const pajOnly = scope === "pajamathon";
  const countHint = pajOnly
    ? pajamathonNotCuedCount()
    : state.tracks.filter((t) => {
        const st = trackReadinessStatus(t);
        return st === "not_cued" || st === "missing";
      }).length;

  if (!countHint) {
    setStatus(
      pajOnly
        ? "No not-cued Pajamathon tracks to queue."
        : "No not-cued tracks to queue.",
      "error"
    );
    return;
  }

  const ok = await showConfirmDialog({
    title: pajOnly ? "Batch Pajamathon cues?" : "Batch add cues?",
    track: pajOnly
      ? `${countHint} not-cued Pajamathon songs`
      : `About ${countHint} not-cued tracks`,
    message:
      "Each track gets a beatgrid preflight, then AutoCue runs (up to two at a time).",
    note: pajOnly
      ? "Only Add Cues / Pajamathon. Inbox songs stay put. Keep VirtualDJ closed during the batch."
      : "Tracks without a usable BPM or grid are skipped. Keep VirtualDJ closed during the batch.",
    confirmLabel: pajOnly ? "Cue Pajamathon" : "Start batch",
    tone: "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      message:
        "Batch cue changes may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then batch add cues.", "error");
      return;
    }
  }

  const batchBtn = $("batchAddCuesBtn");
  const pajBtn = $("batchPajamathonCuesBtn");
  if (batchBtn) batchBtn.disabled = true;
  if (pajBtn) pajBtn.disabled = true;
  stopBatchPoll();
  setRetryStatus(
    pajOnly ? "Starting Pajamathon AutoCue batch…" : "Starting batch Add cues…",
    "running"
  );
  setStatus(pajOnly ? "Pajamathon AutoCue: queuing…" : "Batch AutoCue: queuing…");

  try {
    const pajPaths = pajOnly
      ? state.tracks
          .filter((t) => {
            const st = trackReadinessStatus(t);
            return (
              addCuesSection(t) === "pajamathon" &&
              (st === "not_cued" || st === "missing") &&
              t.path
            );
          })
          .map((t) => t.path)
      : [];
    const data = await api("/api/retry-cues/batch", {
      method: "POST",
      body: JSON.stringify({
        ...(pajOnly
          ? { paths: pajPaths, filter: "pajamathon_not_cued" }
          : { filter: "not_cued" }),
        allow_vdj_running: Boolean(allowRunning),
        require_grid: true,
        deep_grid_check: false,
      }),
    });
    const batch = data.batch;
    state.batchId = batch.id;
    setRetryStatus(batch.message || `Batch ${batch.id}…`, "running");

    state.batchPollTimer = setInterval(async () => {
      if (state.batchPollInFlight) return;
      state.batchPollInFlight = true;
      try {
        const res = await api(`/api/retry-cues/batch/${batch.id}`);
        const b = res.batch;
        setRetryStatus(b.message || "Batch running…", "running");
        setStatus(b.message || "Batch AutoCue…");
        state.batchCueingPaths = (b.items || [])
          .filter((item) => item.path && isAutocueJobActive(item))
          .map((item) => item.path);
        syncAutocueUi();
        if (b.status === "queued" || b.status === "running") return;
        state.batchCueingPaths = [];
        stopBatchPoll();
        if (batchBtn) batchBtn.disabled = false;
        if (pajBtn) pajBtn.disabled = false;
        const kind = b.failed && !b.done ? "error" : "ok";
        setRetryStatus(b.message, kind);
        setStatus(b.message, b.failed && !b.done ? "error" : "success");
        scheduleLoadTracks({ keepPath: currentTrack()?.path, silent: true });
        updateBatchAddCuesButton();
      } catch (err) {
        stopBatchPoll();
        if (batchBtn) batchBtn.disabled = false;
        if (pajBtn) pajBtn.disabled = false;
        setRetryStatus(err.message, "error");
        setStatus(err.message, "error");
      } finally {
        state.batchPollInFlight = false;
      }
    }, 2500);
    if (pajOnly) setCrateFilter("cueing");
  } catch (err) {
    stopBatchPoll();
    if (batchBtn) batchBtn.disabled = false;
    if (pajBtn) pajBtn.disabled = false;
    setRetryStatus(err.message, "error");
    setStatus(err.message, "error");
  }
}

function updateBatchAddCuesButton() {
  updateBatchFixGridsButton();
  const btn = $("batchAddCuesBtn");
  const pajBtn = $("batchPajamathonCuesBtn");
  const pajN = pajamathonNotCuedCount();
  if (pajBtn) {
    const showPaj = isReviewMode() && pajN > 0;
    pajBtn.hidden = !showPaj;
    pajBtn.textContent = pajN
      ? `Batch Pajamathon cues (${pajN})`
      : "Batch Pajamathon cues";
    pajBtn.disabled = !pajN || Boolean(state.batchPollTimer);
  }
  if (!btn) return;
  const show = isReviewMode() && state.readinessFilter === "not_cued";
  btn.hidden = !show;
  if (!show) return;
  const n = state.tracks.filter((t) => {
    const st = trackReadinessStatus(t);
    return st === "not_cued" || st === "missing";
  }).length;
  btn.textContent = n ? `Batch add cues (${n})` : "Batch add cues";
  btn.disabled = !n || Boolean(state.batchPollTimer);
}

function stopGridFixPoll() {
  if (state.gridFixPollTimer) {
    clearInterval(state.gridFixPollTimer);
    state.gridFixPollTimer = null;
  }
}

function pajamathonTrackCount() {
  return state.tracks.filter((t) => addCuesSection(t) === "pajamathon").length;
}

function updateBatchFixGridsButton() {
  const btn = $("batchFixGridsBtn");
  if (!btn) return;
  const n = pajamathonTrackCount();
  const show = isReviewMode() && n > 0;
  btn.hidden = !show;
  btn.textContent = n ? `Fix Pajamathon grids (${n})` : "Fix Pajamathon grids";
  btn.disabled = !n || Boolean(state.gridFixPollTimer);
}

async function batchFixPajamathonGrids() {
  const n = pajamathonTrackCount();
  if (!n) {
    setStatus("No Pajamathon songs in Add Cues.", "error");
    return;
  }
  const ok = await showConfirmDialog({
    title: "Fix Pajamathon grids?",
    track: `${n} Pajamathon songs`,
    message:
      "Halve VDJ double-time BPM when the music is really ~60–80, and snap the beatgrid so the musical 1 lands on beat 1 of the bar (any bar is fine).",
    note: "Close VirtualDJ first or the writes will be refused. Already-good grids are left alone.",
    confirmLabel: "Fix grids",
    tone: "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      message:
        "Grid/BPM writes will be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then fix grids.", "error");
      return;
    }
  }

  const btn = $("batchFixGridsBtn");
  if (btn) btn.disabled = true;
  stopGridFixPoll();
  setRetryStatus("Starting Pajamathon grid/BPM fix…", "running");
  setStatus("Pajamathon grids: analyzing…");

  try {
    const data = await api("/api/grid-fix/batch", {
      method: "POST",
      body: JSON.stringify({
        filter: "pajamathon",
        apply: true,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const batch = data.batch;
    setRetryStatus(batch.message || `Grid fix ${batch.id}…`, "running");
    state.gridFixPollTimer = setInterval(async () => {
      if (state.gridFixPollInFlight) return;
      state.gridFixPollInFlight = true;
      try {
        const res = await api(`/api/grid-fix/batch/${batch.id}`);
        const b = res.batch;
        setRetryStatus(b.message || "Fixing grids…", "running");
        setStatus(b.message || "Fixing grids…");
        if (b.status === "queued" || b.status === "running") return;
        stopGridFixPoll();
        if (btn) btn.disabled = false;
        const kind = b.failed && !b.done && !b.halved ? "error" : "ok";
        setRetryStatus(b.message, kind);
        setStatus(b.message, b.failed && !b.done ? "error" : "success");
        scheduleLoadTracks({ keepPath: currentTrack()?.path, silent: true });
        updateBatchFixGridsButton();
      } catch (err) {
        stopGridFixPoll();
        if (btn) btn.disabled = false;
        setRetryStatus(err.message, "error");
        setStatus(err.message, "error");
      } finally {
        state.gridFixPollInFlight = false;
      }
    }, 2500);
  } catch (err) {
    stopGridFixPoll();
    if (btn) btn.disabled = false;
    setRetryStatus(err.message, "error");
    setStatus(err.message, "error");
  }
}

function gridBadge(track) {
  const g = track.grid || track.grid_preflight;
  if (!g || !isReviewMode()) return "";
  if (g.manual_required || g.status === "blocked") {
    return `<span class="badge bad" title="${escapeHtml(
      (g.issues || []).join(" · ") || g.label || ""
    )}">Grid blocked</span>`;
  }
  if (g.needs_align || g.status === "fixable") {
    return `<span class="badge warn" title="${escapeHtml(
      (g.warnings || []).join(" · ") || g.label || ""
    )}">Grid fix?</span>`;
  }
  if (g.status === "warn") {
    return `<span class="badge warn" title="${escapeHtml(
      (g.warnings || []).join(" · ") || g.label || ""
    )}">${escapeHtml(g.label || "Grid warn")}</span>`;
  }
  if (g.can_autocue) {
    return `<span class="badge ok" title="Beatgrid OK for AutoCue">Grid OK</span>`;
  }
  return "";
}


/** Apply manual grid confirmation onto a preflight object. */
function withManualGridConfirmation(g = {}) {
  return {
    ...g,
    can_autocue:
      g.bpm != null && g.grid_anchor != null
        ? true
        : Boolean(g.can_autocue),
    manual_required: false,
    manual_confirmable: false,
    needs_align: false,
    status: "warn",
    label: "Grid manually confirmed",
    issues: [],
    warnings: [
      ...(g.warnings || []).filter((w) => !/manually confirmed/i.test(String(w))),
      "You confirmed the VirtualDJ beatgrid. AutoCue skips deep onset verification for this track.",
    ],
  };
}

function isGridManuallyConfirmed(path) {
  return Boolean(path && state.gridManualConfirmed && state.gridManualConfirmed[path]);
}

/** User confirms the VDJ beatgrid after weak onset / ambient block. */
function confirmGridManually(track, { forCopy = false } = {}) {
  if (!track?.path) return;
  state.gridManualConfirmed[track.path] = true;
  const g = state.gridPreflight || track.grid || {};
  state.gridPreflight = withManualGridConfirmation(g);
  // Keep list badge / structural grid in sync so the red banner cannot come back.
  const live = state.tracks.find((t) => t.path === track.path);
  if (live) live.grid = withManualGridConfirmation(live.grid || g);
  if (track.grid) track.grid = withManualGridConfirmation(track.grid);
  renderGridPreflightCard(track);
  renderTrackList();
  if (forCopy) {
    setStatus("Grid confirmed - copying again…", "success");
    return;
  }
  setStatus("Grid confirmed — you can run AutoCue now.", "success");
  setRetryStatus("Grid confirmed — ready for AutoCue", "success");
}

function renderGridPreflightCard(track) {
  const card = $("gridPreflightCard");
  if (!card) return;
  if (!isReviewMode() || !track) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  let g = state.gridPreflight || track.grid;
  if (!g) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  if (isGridManuallyConfirmed(track.path)) {
    g = withManualGridConfirmation(g);
    state.gridPreflight = g;
  }
  const cls =
    g.status === "blocked"
      ? "blocked"
      : g.status === "fixable" || g.status === "warn"
        ? "warn"
        : "ok";
  const issues = (g.issues || [])
    .map((i) => `<li>${escapeHtml(i)}</li>`)
    .join("");
  const warnings = (g.warnings || [])
    .map((w) => `<li>${escapeHtml(w)}</li>`)
    .join("");
  const action = g.manual_required
    ? "Fix the beatgrid in VirtualDJ before AutoCue — or use Align grid on the wave."
    : g.needs_align
      ? "Onset analysis disagrees with the current 1. If it already sounds right, leave it. If not, drag Align grid and Apply — do not trust Auto-align on syncopated tracks."
      : "Grid looks ready for AutoCue.";
  // House profile: 115-125 BPM is native tempo. No Halve-BPM nag.
  const showHalve = Boolean(g.suggest_halve_bpm);
  const halfTarget = g.halved_bpm
    ? Number(g.halved_bpm).toFixed(0)
    : g.bpm
      ? (Number(g.bpm) / 2).toFixed(0)
      : "?";
  card.hidden = false;
  card.className = `grid-preflight-card ${cls}`;
  card.innerHTML = `
    <div class="grid-preflight-title">
      <strong>Beatgrid · ${escapeHtml(g.label || g.status)}</strong>
      ${
        g.bpm
          ? `<span class="badge neutral">${Number(g.bpm).toFixed(0)} BPM</span>`
          : ""
      }
      ${
        g.can_autocue
          ? `<span class="badge ok">can AutoCue</span>`
          : `<span class="badge bad">blocked</span>`
      }
    </div>
    <div class="subtitle">${escapeHtml(action)}</div>
    ${issues ? `<ul class="grid-preflight-list">${issues}</ul>` : ""}
    ${warnings ? `<ul class="grid-preflight-list warn">${warnings}</ul>` : ""}
    <div class="grid-preflight-actions">
      ${
        g.bpm || g.grid_anchor != null
          ? `<span class="subtitle">Grid · Align / Auto-align is in the wave toolbar.</span>`
          : ""
      }
      ${
        showHalve
          ? `<button type="button" class="btn primary" id="halveBpmBtn"
               title="Write half BPM into VirtualDJ database (double-time fix)">
               ½ BPM → ~${escapeHtml(halfTarget)} in VDJ
             </button>`
          : ""
      }
      ${
        g.bpm && Number(g.bpm) < 90
          ? `<button type="button" class="btn ghost" id="doubleBpmBtn"
               title="Double VDJ BPM if you halved by mistake">
               ×2 BPM in VDJ
             </button>`
          : ""
      }
      ${
        isGridManuallyConfirmed(track.path)
          ? `<span class="badge ok">Grid confirmed</span>`
          : g.manual_confirmable ||
              (g.manual_required &&
                g.bpm != null &&
                g.grid_anchor != null &&
                !g.can_autocue)
            ? `<button type="button" class="btn primary" id="confirmGridBtn"
                 title="I set the grid in VirtualDJ — allow AutoCue without onset verification">
                 ✓ Grid is correct
               </button>`
            : ""
      }
    </div>
  `;

  $("confirmGridBtn")?.addEventListener("click", () => confirmGridManually(track));
  $("halveBpmBtn")?.addEventListener("click", () => writeBpmFactor({ double: false }));
  $("doubleBpmBtn")?.addEventListener("click", () => writeBpmFactor({ double: true }));
}

async function writeBpmFactor({ double = false } = {}) {
  const track = currentTrack();
  if (!track) return;
  const raw = trackBpm(track);
  if (!raw) {
    setStatus("No VDJ BPM on this track.", "error");
    return;
  }
  const after = double ? raw * 2 : raw / 2;
  const verb = double ? "Double" : "Halve";
  const ok = await (typeof showConfirmDialog === "function"
    ? showConfirmDialog({
        title: `${verb} BPM in VirtualDJ?`,
        track: trackDisplayTitle(track),
        message: `Rewrite database BPM: ${raw.toFixed(1)} → ${after.toFixed(1)}.`,
        note:
          "This updates Scan/Tags Bpm in database.xml so AutoCue quantizes at the correct period. Close VirtualDJ first.",
        confirmLabel: `${verb} BPM`,
        tone: "warning",
      })
    : Promise.resolve(
        confirm(
          `${verb} VDJ BPM for:\n\n${track.name}\n\n${raw.toFixed(1)} → ${after.toFixed(
            1
          )}\n\nClose VirtualDJ first. Continue?`
        )
      ));
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning =
      typeof showConfirmDialog === "function"
        ? await showConfirmDialog({
            title: "VirtualDJ is still open",
            track: trackDisplayTitle(track),
            message:
              "BPM rewrite may be overwritten when VirtualDJ quits. Close it first if possible.",
            confirmLabel: "Continue anyway",
            tone: "warning",
          })
        : confirm("VirtualDJ is running. Continue anyway?");
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then change BPM.", "error");
      return;
    }
  }

  try {
    setStatus(`${verb}ing BPM…`);
    const data = await api("/api/halve-bpm", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
        double_instead: Boolean(double),
      }),
    });
    const r = data.result || {};
    setStatus(
      `BPM ${Number(r.bpm_before).toFixed(0)} → ${Number(r.bpm_after).toFixed(0)} in VDJ`,
      "success"
    );
    // Playback half toggle no longer needed if VDJ is fixed.
    if (!double && state.halfBpm) setHalfBpm(false);
    await loadTracks({ keepPath: track.path });
    const gen = state.trackGen;
    await loadDeepGridPreflight(currentTrack(), gen);
    updateSpeedUi();
    updatePlayerMetaOnly(currentTrack());
  } catch (err) {
    setStatus(err.message, "error");
    if (typeof showConfirmDialog === "function") {
      await showConfirmDialog({
        title: "BPM rewrite failed",
        message: err.message,
        confirmLabel: "Close",
        tone: "warning",
        cancelOnly: true,
      });
    } else {
      alert(err.message);
    }
  }
}

async function loadDeepGridPreflight(track, gen) {
  if (!track || !isReviewMode()) {
    state.gridPreflight = null;
    renderGridPreflightCard(null);
    return;
  }
  // User already confirmed this path — never re-show "Cannot verify grid".
  if (isGridManuallyConfirmed(track.path)) {
    const confirmed = withManualGridConfirmation(
      state.gridPreflight || track.grid || {}
    );
    state.gridPreflight = confirmed;
    if (track.grid) track.grid = confirmed;
    renderGridPreflightCard(track);
    return;
  }
  // Show fast list data immediately.
  state.gridPreflight = track.grid || null;
  renderGridPreflightCard(track);
  const st = trackReadinessStatus(track);
  const g = track.grid || {};
  const needsLook =
    st === "not_cued" ||
    st === "missing" ||
    st === "partial" ||
    g.needs_align ||
    g.status === "blocked" ||
    g.status === "fixable" ||
    g.status === "warn";
  if (!needsLook) return;
  try {
    const data = await api(
      `/api/grid-preflight?path=${encodeURIComponent(track.path)}&deep=true`
    );
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    // Confirmation may have happened while deep request was in flight.
    if (isGridManuallyConfirmed(track.path)) {
      state.gridPreflight = withManualGridConfirmation(data.preflight || {});
    } else {
      state.gridPreflight = data.preflight;
    }
    renderGridPreflightCard(track);
  } catch {
    /* keep structural preflight */
  }
}

const ACTION_LABELS = {
  sort: "Sorted",
  promote: "Moved",
  remove_ready: "Removed",
  retry_cues: "AutoCue started",
  retry_cues_complete: "AutoCue finished",
  retry_cues_batch: "AutoCue batch",
  create_folder: "Folder created",
  undo: "Undone",
  bpm_update: "BPM updated",
};

function actionLabel(action) {
  const raw = String(action || "action");
  return (
    ACTION_LABELS[raw] ||
    raw
      .split("_")
      .filter(Boolean)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
  );
}

function actionDateLabel(timestamp) {
  const date = new Date(timestamp || "");
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDelta = Math.round((startToday - startDate) / 86400000);
  if (dayDelta === 0) return "Today";
  if (dayDelta === 1) return "Yesterday";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function actionMetaParts(action) {
  const details = action.details || {};
  const parts = [];
  if (details.relative_folder) parts.push(`→ ${details.relative_folder}`);
  if (
    action.action === "retry_cues_complete" &&
    details.cue_count_before != null &&
    details.cue_count_after != null
  ) {
    parts.push(`${details.cue_count_before} → ${details.cue_count_after} cues`);
    if (details.loop_count_after != null) {
      parts.push(`${details.loop_count_after} loops`);
    }
  } else if (action.action === "retry_cues") {
    parts.push("Queued for AutoCue");
  }
  if (details.cues_sorted_path) parts.push("Cues Sorted");
  if (details.stems || details.stems_moved) parts.push("stems");
  if (details.library_mode) parts.push(details.library_mode);
  if (details.reconstructed) parts.push("reconstructed");
  if (action.undone) parts.push("undone");
  if (action.success === false) parts.push(action.error || "failed");
  return [...new Set(parts)];
}

async function loadActionsLogPanel() {
  const body = $("actionsLogBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const data = await api("/api/actions?limit=100");
    const logPath = data.log_path || "";
    $("actionsLogPath").textContent = logPath || "Local action history";
    $("actionsLogPath").title = logPath;
    const rows = data.actions || [];
    const count = $("actionsLogCount");
    if (count) count.textContent = `${rows.length} ${rows.length === 1 ? "event" : "events"}`;
    if (!rows.length) {
      body.innerHTML = `<div class="empty">No actions logged yet.</div>`;
      return;
    }
    body.innerHTML = rows
      .map((a) => {
        const t = (a.ts || "").slice(11, 16) || "—";
        const rawType = String(a.action || "action");
        const type = actionLabel(rawType);
        const actionClass = rawType.replace(/[^a-z0-9-]+/gi, "-").toLowerCase();
        const sourceOrDest = a.dest_path || a.source_path || "";
        const name =
          a.name ||
          sourceOrDest.split("/").filter(Boolean).at(-1) ||
          "Unknown item";
        const extra = actionMetaParts(a);
        const fullDetail = [a.source_path, a.dest_path].filter(Boolean).join("\n") || name;
        const fail = a.success === false ? "fail" : "";
        const undoBtn =
          a.undoable && a.id
            ? `<button type="button" class="btn ghost action-undo-btn" data-undo-id="${escapeHtml(
                a.id
              )}" data-undo-name="${escapeHtml(a.name || "")}">Undo</button>`
            : a.undone
              ? `<span class="badge neutral">undone</span>`
              : "";
        return `<div class="action-row action-${escapeHtml(actionClass)} ${fail}">
          <div class="action-time" title="${escapeHtml(a.ts || "")}">
            <span class="action-clock">${escapeHtml(t)}</span>
            <span class="action-date">${escapeHtml(actionDateLabel(a.ts))}</span>
          </div>
          <div class="action-type" title="${escapeHtml(rawType)}">${escapeHtml(type)}</div>
          <div class="action-detail" title="${escapeHtml(fullDetail)}">
            <div class="action-primary">${escapeHtml(name)}</div>
            ${
              extra.length
                ? `<div class="action-meta">${extra
                    .map((item) => `<span>${escapeHtml(item)}</span>`)
                    .join("")}</div>`
                : ""
            }
          </div>
          <div class="action-undo">${undoBtn}</div>
        </div>`;
      })
      .join("");
    body.querySelectorAll("[data-undo-id]").forEach((btn) => {
      btn.addEventListener("click", () =>
        undoAction(btn.dataset.undoId, btn.dataset.undoName)
      );
    });
  } catch (err) {
    body.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

async function undoAction(actionId, name) {
  const ok = await showConfirmDialog({
    title: "Undo this move?",
    track: name || actionId,
    message:
      "The file will move back and its VirtualDJ FilePath will be retargeted.",
    note:
      "Secondary copies created by the sort will be deleted. Keep VirtualDJ closed.",
    confirmLabel: "Undo move",
    tone: "warning",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: name || actionId,
      message:
        "The undo may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
      confirmLabel: "Undo anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then undo.", "error");
      return;
    }
  }

  try {
    setStatus(`Undoing ${name || actionId}…`);
    const data = await api("/api/undo", {
      method: "POST",
      body: JSON.stringify({
        action_id: actionId,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    setStatus(
      `Undone → ${(r.moved_to || "").split("/").slice(-2).join("/") || "ok"}`,
      "success"
    );
    await loadTracks({ keepPath: currentTrack()?.path });
    await loadFolders();
    if ($("actionsLogPanel") && !$("actionsLogPanel").hidden) {
      await loadActionsLogPanel();
    }
  } catch (err) {
    setStatus(err.message, "error");
    await showConfirmDialog({
      title: "Undo failed",
      message: err.message,
      confirmLabel: "Close",
      tone: "danger",
      cancelOnly: true,
    });
  }
}

function resetWaveZoom() {
  state.waveZoom = 1;
  state.waveOffset = 0;
  state.waveViewPinned = false;
}

function snapshotWaveView() {
  state.waveViewBeforeAlign = MusicSorterWaveform.snapshotWaveView(
    state.waveZoom,
    state.waveOffset
  );
}

function restoreWaveView() {
  const prev = state.waveViewBeforeAlign;
  state.waveViewBeforeAlign = null;
  const next = MusicSorterWaveform.restoreWaveView(prev);
  if (!next) return false;
  state.waveZoom = next.zoom;
  state.waveOffset = next.offset;
  return true;
}

function formatBitrate(kbps) {
  if (!kbps || kbps <= 0) return null;
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Mbps`;
  return `${Math.round(kbps)} kbps`;
}

function bitrateBadgeClass(kbps) {
  if (!kbps) return "neutral";
  // Rough quality hints for lossy; lossless FLAC often >> 500
  if (kbps >= 900) return "ok"; // typical lossless territory
  if (kbps >= 256) return "ok";
  if (kbps >= 192) return "neutral";
  if (kbps >= 128) return "warn";
  return "bad";
}

async function loadTrackMeta(track, gen) {
  if (!track) {
    state.trackMeta = null;
    return;
  }
  if (state.metaAbort) state.metaAbort.abort();
  const controller = new AbortController();
  state.metaAbort = controller;
  try {
    const data = await api(`/api/meta?path=${encodeURIComponent(track.path)}`, {
      signal: controller.signal,
    });
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.trackMeta = data;
    // Patch into track for list reuse
    track.bitrate_kbps = data.bitrate_kbps;
    track.codec = data.codec;
    track.sample_rate = data.sample_rate;
    updatePlayerMetaOnly(track);
    // Refresh list badge for this track once kbps is known
    renderTrackList();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (gen !== state.trackGen) return;
    state.trackMeta = null;
  }
}

function updatePlayerMetaOnly(track) {
  const meta = $("playerMeta");
  if (!meta || !track) return;
  // Re-render full meta row (cheap) via shared builder
  meta.innerHTML = buildPlayerMetaHtml(track);
}

function buildPlayerMetaHtml(track) {
  const cues = track.cues || {};
  const metaBits = state.trackMeta;
  const kbps = metaBits?.bitrate_kbps ?? track.bitrate_kbps;
  const codec = metaBits?.codec ?? track.codec;
  const sr = metaBits?.sample_rate ?? track.sample_rate;
  const brLabel = formatBitrate(kbps);
  const brClass = bitrateBadgeClass(kbps);

  // Keep identity chrome light — at most a few chips (readiness, cues/bpm, bitrate).
  const countsChip = track.is_cued
    ? `<span class="badge neutral marker-counts-chip">${cues.cue_count || 0} cues${
        cues.loop_count ? ` · ${cues.loop_count} loops` : ""
      }</span>`
    : "";
  const statusChip = isReviewMode()
    ? `${readinessBadge(track)}${retryHistoryBadge(track)}${countsChip}`
    : track.is_cued
      ? `<span class="badge ok">${cues.cue_count || 0} cues${
          cues.loop_count ? ` · ${cues.loop_count} loops` : ""
        }</span>`
      : `<span class="badge uncued">Not cued</span>`;
  const bpmChip = cues.bpm
    ? state.halfBpm
      ? `<span class="badge ok" title="VDJ ${Number(cues.bpm).toFixed(0)} halved">${(
          Number(cues.bpm) / 2
        ).toFixed(0)} BPM (½)</span>`
      : `<span class="badge neutral">${Number(cues.bpm).toFixed(0)} BPM</span>`
    : "";
  const brChip = brLabel
    ? `<span class="badge ${brClass}" title="${codec || "audio"} ${
        sr ? sr + " Hz" : ""
      }">${escapeHtml(brLabel)}</span>`
    : "";
  const warnChip = !cues.in_database
    ? `<span class="badge bad">not in VDJ</span>`
    : "";
  return `${statusChip}${bpmChip}${brChip}${warnChip}`;
}

function waveformLoadedFor(track) {
  return Boolean(track && state.waveform && state.waveformPath === track.path);
}

/* R-97: the picture on screen is this song's own, fully loaded. Anything placed or dragged "on the waveform" is
   only allowed then - never from the old song's picture, never while "Loading waveform…" is showing. */
function waveformReadyFor(track) {
  return Boolean(track) && waveformLoadedFor(track) && !state.waveformLoading && state.panelPath === track.path;
}
function refuseWhileWaveLoading(track, what = "Place it") {
  if (waveformReadyFor(track)) return false;
  loudNotice(`The waveform for “${trackDisplayTitle(track) || "this song"}” is still loading - wait for it, then ${what.toLowerCase()}. Nothing was changed.`, "warn");
  return true;
}

function scheduleWaveformLoad(track, gen, { force = false } = {}) {
  // Seeking / re-rendering the SAME track never reloads its waveform: no reset,
  // no "Loading waveform…" overlay, no /api/waveform fetch.
  if (!force && waveformLoadedFor(track) && !state.waveformError) {
    state.waveformLoading = false;
    setWaveformStatus("");
    return;
  }
  if (state.waveformDebounce) {
    clearTimeout(state.waveformDebounce);
    state.waveformDebounce = null;
  }
  if (state.waveformAbort) {
    state.waveformAbort.abort();
    state.waveformAbort = null;
  }
  state.waveform = null;
  state.waveformLoading = true;
  state.waveformError = null;
  resetWaveZoom();
  if (isPracticeMode()) {
    setPracticeWaveStatus("Loading waveform…");
    drawPracticeWaveform();
  } else {
    setWaveformStatus("Loading waveform…");
    drawWaveform();
  }

  // Debounce so rapid J/K or list clicks don't stack ffmpeg jobs.
  state.waveformDebounce = setTimeout(() => {
    state.waveformDebounce = null;
    if (gen !== state.trackGen) return;
    loadWaveform(track, gen);
  }, 140);
}

async function loadWaveform(track, gen = state.trackGen) {
  if (!track) {
    state.waveform = null;
    state.waveformError = null;
    state.waveformLoading = false;
    resetWaveZoom();
    drawWaveform();
    setWaveformStatus("No track selected");
    return;
  }
  if (gen !== state.trackGen) return;

  if (state.waveformAbort) state.waveformAbort.abort();
  const controller = new AbortController();
  state.waveformAbort = controller;
  state.waveformLoading = true;
  state.waveformError = null;
  setWaveformStatus("Loading waveform…");

  try {
    const data = await api(
      `/api/waveform?path=${encodeURIComponent(track.path)}&bins=1000`,
      { signal: controller.signal }
    );
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.waveform = data;
    state.waveformPath = track.path;
    state.waveformLoading = false;
    if (state.waveformAutoRetry) delete state.waveformAutoRetry[track.path];
    setWaveformStatus("");
    setPracticeWaveStatus("");
    if (isPracticeMode()) drawPracticeWaveform();
    else drawWaveform();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
    state.waveform = null;
    state.waveformLoading = false;
    const calm = plainWaveformError(err);
    state.waveformError = calm || String(err.message || err || "Waveform failed");
    if (!calm) return; // aborted / nothing to show
    const retry = {
      label: "Retry",
      onClick: () => {
        const cur = currentTrack();
        if (!cur || cur.path !== track.path) return;
        scheduleWaveformLoad(cur, state.trackGen, { force: true });
      },
    };
    setWaveformStatus(calm, "error", retry);
    setPracticeWaveStatus(calm, "error");
    if (isPracticeMode()) drawPracticeWaveform();
    else drawWaveform();
    // One quiet auto-retry with backoff (R-100); a second failure stays on the Retry button.
    const tries = (state.waveformAutoRetry && state.waveformAutoRetry[track.path]) || 0;
    if (tries < 2) {
      state.waveformAutoRetry = state.waveformAutoRetry || {};
      state.waveformAutoRetry[track.path] = tries + 1;
      const delay = tries === 0 ? 700 : 1800;
      clearTimeout(loadWaveform._autoRetry);
      loadWaveform._autoRetry = setTimeout(() => {
        const cur = currentTrack();
        if (!cur || cur.path !== track.path || gen !== state.trackGen) return;
        if (waveformLoadedFor(cur)) return;
        scheduleWaveformLoad(cur, state.trackGen, { force: true });
      }, delay);
    }
  }
}

function waveformDuration(track, audio) {
  const fromWave = Number(state.waveform?.duration) || 0;
  if (fromWave > 0) return fromWave;
  return trackDuration(track, audio);
}

function clampWaveZoom(zoom) {
  return MusicSorterWaveform.clampWaveZoom(zoom);
}

/** Visible time window over the full track duration (no playhead follow). */
function visibleWaveWindow(duration, zoom, offset) {
  return MusicSorterWaveform.visibleWaveWindow(duration, zoom, offset);
}

/** Visible time window over the full track duration. */
function waveViewWindow(duration) {
  const view = visibleWaveWindow(duration, state.waveZoom, state.waveOffset);
  state.waveZoom = view.zoom;
  state.waveOffset = view.start;
  return view;
}

/**
 * Page the zoom window so a moving playhead stays on-screen.
 * Paused / drag leave the user's view alone.
 */
function keepPlayheadInView(
  duration,
  timeSec,
  { zoom, offset, playing, allowFollow, lead } = {}
) {
  return MusicSorterWaveform.keepPlayheadInView(duration, timeSec, {
    zoom: zoom ?? state.waveZoom,
    offset: offset ?? state.waveOffset,
    playing,
    allowFollow,
    lead,
  });
}

function applyPlayheadFollow(duration, timeSec) {
  const audio = $("audio");
  const playing = Boolean(audio && !audio.paused && !audio.ended);
  return MusicSorterWaveform.applyPlayheadFollow(state, duration, timeSec, playing);
}

function wavePlotMetrics(cssW) {
  const padX = WAVE_PAD_X;
  const plotW = Math.max(1, cssW - padX * 2);
  return { padX, plotW };
}

function timeToWaveX(timeSec, padX, plotW, view) {
  return MusicSorterWaveform.timeToWaveX(timeSec, padX, plotW, view);
}

function classifyWaveMarkers(points, view, slack = 0.05) {
  return MusicSorterWaveform.classifyWaveMarkers(points, view, slack);
}

function formatOffscreenCueLabel(points, side) {
  return MusicSorterWaveform.formatOffscreenCueLabel(points, side);
}

function panWaveToTime(timeSec, { frac = 0.22 } = {}) {
  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  const t = Number(timeSec);
  if (!duration || !Number.isFinite(t)) return;
  const view = waveViewWindow(duration);
  const next = visibleWaveWindow(duration, view.zoom, t - view.span * frac);
  state.waveZoom = next.zoom;
  state.waveOffset = next.start;
  state.waveViewPinned = true;
  drawWaveform();
}

function hitTestRect(rect, x, y) {
  return MusicSorterWaveform.hitTestRect(rect, x, y);
}

function hitTestWaveCueChrome(clientX, clientY) {
  const hits = state.waveCueChromeHits;
  const wrap = $("waveformWrap");
  if (!hits || !wrap) return null;
  const box = wrap.getBoundingClientRect();
  const x = clientX - box.left;
  const y = clientY - box.top;
  if (hitTestRect(hits.left, x, y)) return { kind: "left", time: hits.left.time };
  if (hitTestRect(hits.right, x, y)) return { kind: "right", time: hits.right.time };
  // Loop/cue handles win over the full-width overview strip.
  if (
    hitTestRect(hits.overview, x, y) &&
    (hitTestCueAtClientX(clientX) || hitTestLoopAtClientX(clientX))
  ) {
    return null;
  }
  if (hitTestRect(hits.overview, x, y)) {
    const duration = waveformDuration(currentTrack(), $("audio"));
    const plotW = Number(hits.overview.plotW) || 0;
    if (!duration || plotW <= 0) return { kind: "overview", time: null };
    const ratio = Math.min(
      1,
      Math.max(0, (x - hits.overview.padX) / plotW)
    );
    return { kind: "overview", time: ratio * duration };
  }
  return null;
}

function drawWaveCueOverview(ctx, points, view, duration, padX, plotW, h) {
  if (!state.waveCueChromeHits) state.waveCueChromeHits = {};
  if (!duration || state.waveZoom <= 1.01) {
    state.waveCueChromeHits.overview = null;
    return;
  }
  const ovH = 7;
  const ovY = h - ovH - 2;
  ctx.save();
  ctx.fillStyle = "rgba(42, 51, 68, 0.95)";
  ctx.fillRect(padX, ovY, plotW, ovH);
  state.overviewDrawn = [];
  for (const p of points || []) {
    const t = Number(p.pos) || 0;
    const x = padX + (t / duration) * plotW;
    ctx.fillStyle = CUE_COLORS[p.color_name] || CUE_COLORS.unknown;
    ctx.fillRect(x - 1, ovY, 2, ovH);
    (state.overviewDrawn ||= []).push({ key: cueKey(p), color: p.color_name });
  }
  const winX = padX + (view.start / duration) * plotW;
  const winW = Math.max(2, (view.span / duration) * plotW);
  ctx.fillStyle = accentRgba(0.22);
  ctx.fillRect(winX, ovY, winW, ovH);
  ctx.strokeStyle = accentRgba(0.95);
  ctx.lineWidth = 1;
  ctx.strokeRect(winX + 0.5, ovY + 0.5, Math.max(1, winW - 1), ovH - 1);
  ctx.restore();
  state.waveCueChromeHits.overview = {
    x0: padX,
    y0: ovY,
    x1: padX + plotW,
    y1: ovY + ovH,
    padX,
    plotW,
  };
}

function drawOffscreenCueHints(ctx, classified, padX, plotW, h) {
  if (!state.waveCueChromeHits) state.waveCueChromeHits = {};
  state.waveCueChromeHits.left = null;
  state.waveCueChromeHits.right = null;
  if (state.waveZoom <= 1.01 || !classified) return;

  const drawChip = (label, side, targetTime) => {
    if (!label) return;
    ctx.save();
    ctx.font = "11px SF Pro Text, system-ui, sans-serif";
    const tw = Math.ceil(ctx.measureText(label).width);
    const boxW = tw + 12;
    const boxH = 18;
    const x = side === "left" ? padX + 4 : padX + plotW - boxW - 4;
    const y = 20;
    ctx.fillStyle = "rgba(10, 14, 22, 0.88)";
    ctx.strokeStyle = "rgba(255, 214, 102, 0.85)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const r = 4;
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + boxW, y, x + boxW, y + boxH, r);
    ctx.arcTo(x + boxW, y + boxH, x, y + boxH, r);
    ctx.arcTo(x, y + boxH, x, y, r);
    ctx.arcTo(x, y, x + boxW, y, r);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#ffd666";
    ctx.fillText(label, x + 6, y + 13);
    ctx.restore();
    const hit = { x0: x, y0: y, x1: x + boxW, y1: y + boxH, time: targetTime };
    if (side === "left") state.waveCueChromeHits.left = hit;
    else state.waveCueChromeHits.right = hit;
  };

  const leftPts = classified.offLeft || [];
  const rightPts = classified.offRight || [];
  const leftTime = leftPts.length
    ? Math.max(...leftPts.map((p) => Number(p.pos) || 0))
    : null;
  const rightTime = rightPts.length
    ? Math.min(...rightPts.map((p) => Number(p.pos) || 0))
    : null;
  drawChip(formatOffscreenCueLabel(leftPts, "left"), "left", leftTime);
  drawChip(formatOffscreenCueLabel(rightPts, "right"), "right", rightTime);
}

/** Canvas + overlay needle. Moving playhead is never dropped. */
function positionWavePlayhead(ctx, audio, view, padX, plotW, h) {
  const needle = $("wavePlayhead");
  if (!audio || !Number.isFinite(audio.currentTime)) {
    if (needle) needle.hidden = true;
    return;
  }
  const t = audio.currentTime;
  const playing = !audio.paused && !audio.ended;
  const inView = view.span > 0 && t >= view.start && t <= view.end;
  if (!inView && !playing) {
    if (needle) needle.hidden = true;
    return;
  }
  let x = timeToWaveX(t, padX, plotW, view);
  x = Math.max(padX, Math.min(padX + plotW, x));
  if (needle) {
    needle.hidden = false;
    needle.style.left = `${x}px`;
    return;
  }
  if (ctx && h > 0) {
    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.globalAlpha = 0.95;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
    ctx.restore();
  }
}

function clientXToTime(clientX, wrapRect, duration) {
  const { padX, plotW } = wavePlotMetrics(wrapRect.width);
  const x = clientX - wrapRect.left;
  const ratio = Math.min(1, Math.max(0, (x - padX) / plotW));
  const view = waveViewWindow(duration);
  return view.start + ratio * view.span;
}

function peaksForView(peaks, view, duration) {
  if (!peaks?.length || !duration || !view.span) return [];
  const n = peaks.length;
  const i0 = Math.max(0, Math.floor((view.start / duration) * n));
  const i1 = Math.min(n, Math.ceil((view.end / duration) * n));
  if (i1 <= i0) return peaks.slice(i0, i0 + 1);
  return peaks.slice(i0, i1);
}

/** Downbeat / grid anchor in seconds (VDJ beatgrid POI, Scan Phase, or preflight). */
function gridAnchorSeconds(track) {
  // Live drag / align mode overrides stored value for display.
  if (
    state.gridAlignMode &&
    state.gridAlignAnchor != null &&
    Number.isFinite(Number(state.gridAlignAnchor))
  ) {
    return Number(state.gridAlignAnchor);
  }
  const g = state.gridPreflight || track?.grid || {};
  const fromGrid = Number(g.grid_anchor);
  if (Number.isFinite(fromGrid)) return fromGrid;
  const cues = track?.cues || {};
  const phase = Number(cues.scan_phase);
  if (Number.isFinite(phase)) return phase;
  const bg = Number(cues.beatgrid_pos);
  if (Number.isFinite(bg)) return bg;
  return 0;
}

/**
 * Musical BPM for bar-1 spacing on the wave.
 * Honors ½ BPM toggle so double-time VDJ values still land on felt ones.
 */
function onesBpm(track) {
  return sourceBpm(track) || trackBpm(track);
}

function barPeriodSeconds(track) {
  const bpm = onesBpm(track);
  if (!bpm || bpm <= 0) return null;
  // 4/4 bars — beat 1 every 4 beats
  return (60 / bpm) * 4;
}

function syncBeatOnesBtn() {
  const btn = $("beatOnesBtn");
  if (!btn) return;
  btn.classList.toggle("active", state.showBeatOnes);
  btn.setAttribute("aria-pressed", state.showBeatOnes ? "true" : "false");
}

function toggleBeatOnes() {
  state.showBeatOnes = !state.showBeatOnes;
  try {
    localStorage.setItem("musicSorter.showBeatOnes", state.showBeatOnes ? "1" : "0");
  } catch {
    /* ignore */
  }
  syncBeatOnesBtn();
  drawWaveform();
}

/**
 * Draw beatgrid as a *ruler* (top/bottom ticks), not full-height cue-like lines.
 * Cues own the middle of the wave; ones stay out of their way.
 */
function drawBeatOnes(ctx, track, view, padX, plotW, h) {
  // Always show ones while aligning; otherwise honor Ones toggle.
  if (!state.showBeatOnes && !state.gridAlignMode) return;
  const barSec = barPeriodSeconds(track);
  if (!barSec || !view.span || barSec <= 0) return;

  const bpm = onesBpm(track) || trackBpm(track);
  const beatSec = bpm && bpm > 0 ? 60 / bpm : barSec / 4;
  const anchor = gridAnchorSeconds(track);
  // First one at or before view.start
  let t = anchor;
  if (t > view.start) {
    t -= Math.ceil((t - view.start) / barSec) * barSec;
  } else {
    t += Math.floor((view.start - t) / barSec) * barSec;
  }

  const pxPerBar = (barSec / view.span) * plotW;
  const align = state.gridAlignMode;
  if (!align && pxPerBar < 0.75) return;
  if (align && pxPerBar < 0.4) {
    ctx.save();
    ctx.fillStyle = accentRgba(0.18);
    ctx.fillRect(0, 0, padX + plotW + padX, 20);
    ctx.fillStyle = "rgba(200, 250, 255, 0.95)";
    ctx.font = "bold 11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText("ALIGN · zoom in (scroll) to see grid ticks", padX + 6, 14);
    ctx.restore();
    return;
  }

  // Ruler lives in top/bottom gutters — cues keep the center.
  const rulerH = align ? 18 : 14;
  const botY = h - 1;
  const maxLines = 400;
  let count = 0;
  ctx.save();

  // Thin top/bottom rails (grid, not cue)
  if (align) {
    ctx.fillStyle = accentRgba(0.14);
    ctx.fillRect(0, 0, padX + plotW + padX, rulerH + 4);
    ctx.fillStyle = "rgba(200, 250, 255, 0.95)";
    ctx.font = "bold 11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      `ALIGN · drag to shift grid · 1 @ ${(Number(anchor) || 0).toFixed(3)}s`,
      padX + 6,
      13
    );
  } else {
    ctx.strokeStyle = "rgba(255, 214, 102, 0.12)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padX, rulerH + 0.5);
    ctx.lineTo(padX + plotW, rulerH + 0.5);
    ctx.stroke();
  }

  // Weak beat ticks — top gutter only, very short
  if (beatSec > 0) {
    const pxPerBeat = (beatSec / view.span) * plotW;
    if (pxPerBeat >= (align ? 5 : 10)) {
      let bt = anchor;
      if (bt > view.start) {
        bt -= Math.ceil((bt - view.start) / beatSec) * beatSec;
      } else {
        bt += Math.floor((view.start - bt) / beatSec) * beatSec;
      }
      let bc = 0;
      for (; bt <= view.end + 1e-9 && bc < maxLines * 4; bt += beatSec, bc++) {
        const stepsFromAnchor = Math.round((bt - anchor) / beatSec);
        if (Math.abs(stepsFromAnchor % 4) < 1e-6) continue; // bars handled below
        if (bt < view.start - 1e-6) continue;
        const x = timeToWaveX(bt, padX, plotW, view);
        ctx.fillStyle = align
          ? accentRgba(0.35)
          : "rgba(255, 214, 102, 0.28)";
        ctx.fillRect(x, 2, 1, 5);
        ctx.fillRect(x, botY - 5, 1, 4);
      }
    }
  }

  for (; t <= view.end + 1e-9 && count < maxLines; t += barSec, count++) {
    if (t < view.start - 1e-6) continue;
    const x = timeToWaveX(t, padX, plotW, view);

    const barsFromAnchor = Math.round((t - anchor) / barSec);
    const isPhraseOne = Math.abs(barsFromAnchor % 4) < 1e-6;
    const isAnchor =
      Math.abs(t - anchor) < Math.max(barSec * 0.02, 0.002);

    if (isPhraseOne || isAnchor) {
      // Phrase / true 1 — tall ticks + label in gutters only (not full-height)
      const tickTop = isAnchor ? rulerH + 2 : rulerH - 2;
      const tickBot = isAnchor ? 12 : 8;
      ctx.strokeStyle = align
        ? accentRgba(0.85)
        : "rgba(255, 200, 90, 0.7)";
      ctx.lineWidth = isAnchor ? 2 : 1.5;
      // Top tick
      ctx.beginPath();
      ctx.moveTo(x + 0.5, 1);
      ctx.lineTo(x + 0.5, tickTop);
      ctx.stroke();
      // Bottom tick
      ctx.beginPath();
      ctx.moveTo(x + 0.5, botY - tickBot);
      ctx.lineTo(x + 0.5, botY);
      ctx.stroke();

      // Optional ultra-faint center guide only in align (so drag target is clear)
      if (align) {
        ctx.strokeStyle = accentRgba(0.12);
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 6]);
        ctx.beginPath();
        ctx.moveTo(x + 0.5, tickTop + 2);
        ctx.lineTo(x + 0.5, botY - tickBot - 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Badge: rounded chip with "1" — cue labels sit lower/elsewhere
      const label = "1";
      ctx.font = isAnchor
        ? "bold 10px SF Pro Text, system-ui, sans-serif"
        : "bold 9px SF Pro Text, system-ui, sans-serif";
      const tw = ctx.measureText(label).width;
      const bx = x - tw / 2 - 3;
      const by = align ? 22 : 2;
      const bw = tw + 6;
      const bh = 12;
      ctx.fillStyle = align
        ? "rgba(20, 40, 55, 0.92)"
        : "rgba(18, 16, 10, 0.88)";
      ctx.strokeStyle = align
        ? accentRgba(0.9)
        : "rgba(255, 200, 90, 0.85)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      const r = 3;
      ctx.moveTo(bx + r, by);
      ctx.arcTo(bx + bw, by, bx + bw, by + bh, r);
      ctx.arcTo(bx + bw, by + bh, bx, by + bh, r);
      ctx.arcTo(bx, by + bh, bx, by, r);
      ctx.arcTo(bx, by, bx + bw, by, r);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = align ? "#9becff" : "#ffd666";
      ctx.fillText(label, x - tw / 2, by + 9);

      // Drag handle on true anchor only
      if (isAnchor) {
        ctx.fillStyle = align ? accentHex() : "#ffd666";
        ctx.beginPath();
        ctx.moveTo(x, by + bh + 5);
        ctx.lineTo(x - 4, by + bh + 1);
        ctx.lineTo(x + 4, by + bh + 1);
        ctx.closePath();
        ctx.fill();
      }
    } else {
      // Other bar downs (5, 9, 13…) — short gutter ticks only, no line through wave
      ctx.fillStyle = align
        ? accentRgba(0.4)
        : "rgba(255, 214, 102, 0.35)";
      ctx.fillRect(x, 2, 1, 7);
      ctx.fillRect(x, botY - 7, 1, 6);
    }
  }
  if (align) {
    ctx.fillStyle = "rgba(160, 220, 240, 0.85)";
    ctx.font = "10px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      "Grid = top/bottom ticks · cues stay as full colored lines",
      padX + 6,
      h - 6
    );
  }
  ctx.restore();
}

function syncGridAlignUi() {
  const btn = $("gridAlignBtn");
  if (btn) {
    btn.classList.toggle("active", state.gridAlignMode);
    btn.setAttribute("aria-pressed", state.gridAlignMode ? "true" : "false");
    btn.textContent = state.gridAlignMode ? "Aligning…" : "Align";
  }
  const bar = $("gridAlignBar");
  if (bar) {
    if (state.gridAlignMode) {
      bar.hidden = false;
      bar.removeAttribute("hidden");
      bar.style.display = "flex";
    } else {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
      bar.style.display = "";
    }
  }
  const wrap = $("waveformWrap");
  if (wrap) wrap.classList.toggle("grid-align-mode", state.gridAlignMode);
  const label = $("gridAlignAnchorLabel");
  if (label) {
    if (state.gridAlignMode) {
      const a = Number(state.gridAlignAnchor);
      const orig = Number(state.gridAlignOriginal);
      const delta = Number.isFinite(a) && Number.isFinite(orig) ? a - orig : 0;
      const plan = state.gridAlignPlan;
      const autoNote = plan?.reason
        ? ` · auto: ${plan.reason}`
        : " · drag wave to shift";
      label.textContent = Number.isFinite(a)
        ? `1 @ ${a.toFixed(3)}s${
            Math.abs(delta) >= 0.0005
              ? ` (${delta >= 0 ? "+" : ""}${delta.toFixed(3)}s)`
              : ""
          }${autoNote}`
        : "No anchor — drag to set 1";
    } else {
      label.textContent = "1 @ —";
    }
  }
  const applyBtn = $("gridAlignApplyBtn");
  if (applyBtn) {
    const dirty =
      state.gridAlignMode &&
      Math.abs(Number(state.gridAlignAnchor) - Number(state.gridAlignOriginal)) > 1e-4;
    applyBtn.disabled = !dirty;
  }
}

/** Zoom wave so ~12 bars are visible around the playhead (ones stay readable). */
function zoomWaveForGridAlign(track) {
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  const barSec = barPeriodSeconds(track);
  if (!duration || !barSec || barSec <= 0) return;
  const wantSpan = Math.min(duration, barSec * 12);
  const zoom = clampWaveZoom(duration / wantSpan);
  state.waveZoom = Math.max(zoom, 2);
  const span = duration / state.waveZoom;
  // Align the 1, not wherever the playhead happened to sit (that hid every cue).
  const center = gridAnchorSeconds(track);
  state.waveOffset = Math.max(0, Math.min(duration - span, center - span / 2));
  state.waveViewPinned = true;
}

function openGridAlignMode() {
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  if (!trackBpm(track) && !onesBpm(track)) {
    setStatus("Track needs a VDJ BPM before aligning the grid.", "error");
    return;
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  const anchor = gridAnchorSeconds(track);
  state.gridAlignMode = true;
  state.gridAlignOriginal = anchor;
  state.gridAlignAnchor = anchor;
  state.gridAlignDragging = false;
  // Ones must be visible while aligning
  state.showBeatOnes = true;
  syncBeatOnesBtn();
  // Zoom in so ones are clearly visible (full-track view can hide them)
  snapshotWaveView();
  zoomWaveForGridAlign(track);
  syncGridAlignUi();
  drawWaveform();
  // Ensure toolbar is on screen
  try {
    $("gridAlignBar")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch {
    /* ignore */
  }
  setStatus(
    "ALIGN MODE — drag the cyan ones on the wave (or use −1/+1 beat). Apply writes to VDJ.",
    "running"
  );
}

function exitGridAlignMode({ restoreView = false, status } = {}) {
  state.gridAlignMode = false;
  state.gridAlignPlan = null;
  state.gridAlignAnchor = null;
  state.gridAlignOriginal = null;
  state.gridAlignDragging = false;
  if (restoreView) {
    restoreWaveView();
    state.waveViewPinned = true;
  } else {
    state.waveViewBeforeAlign = null;
  }
  syncGridAlignUi();
  drawWaveform();
  if (status != null) setStatus(status);
}

function cancelGridAlignMode() {
  exitGridAlignMode({
    restoreView: true,
    status: "Grid align cancelled — no changes written.",
  });
}

function nudgeGridAlign(deltaSeconds) {
  if (!state.gridAlignMode) return;
  const cur = Number(state.gridAlignAnchor);
  if (!Number.isFinite(cur)) return;
  state.gridAlignAnchor = Math.max(0, cur + deltaSeconds);
  syncGridAlignUi();
  drawWaveform();
}

function nudgeGridAlignBeats(beats) {
  const track = currentTrack();
  const bpm = onesBpm(track) || trackBpm(track);
  if (!bpm) return;
  const beatSec = 60 / bpm;
  nudgeGridAlign(beats * beatSec);
}

async function applyGridAlign() {
  const track = currentTrack();
  if (!track || !state.gridAlignMode) return;
  const cap = captureSong();
  if (!guardSong(cap, "Apply grid")) return;
  const anchor = Number(state.gridAlignAnchor);
  if (!Number.isFinite(anchor)) {
    setStatus("No grid anchor to apply.", "error");
    return;
  }
  historyClear(track.path); // a grid change moves the beats: earlier undo snapshots would no longer fit
  const orig = Number(state.gridAlignOriginal);
  const plan = state.gridAlignPlan;
  const wantHalve = Boolean(plan?.halve);
  if (Number.isFinite(orig) && Math.abs(anchor - orig) < 1e-4 && !wantHalve) {
    setStatus("Grid unchanged — nothing to write.");
    return;
  }

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Beatgrid changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Write grid anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then apply the grid.", "error");
      return;
    }
  }
  // A dialog was open: the grid is written for the song that was open at the click, or not at all.
  if (!guardSong(cap, "Apply grid")) return;

  try {
    if (wantHalve) {
      setStatus("Writing ½ BPM, then the new 1…");
      await api("/api/halve-bpm", {
        method: "POST",
        body: JSON.stringify({
          path: track.path,
          allow_vdj_running: Boolean(allowRunning),
          double_instead: false,
        }),
      });
      if (state.halfBpm) setHalfBpm(false);
    }
    setStatus(`Writing beatgrid 1 @ ${anchor.toFixed(3)}s…`);
    const data = await api("/api/set-beatgrid", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        anchor_seconds: anchor,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(track.path, r.cues);
    }
    // Saved 1 is source of truth. Stamp it now. Do not refetch the old grid.
    const live = currentTrack();
    const cues = (live?.path === track.path && live.cues) ? live.cues : (track.cues || {});
    applyCueSummaryToTrack(track.path, {
      ...cues,
      beatgrid_pos: anchor,
      scan_phase: anchor,
      has_beatgrid: true,
    });
    const stamped = {
      ...(state.gridPreflight || track.grid || {}),
      grid_anchor: anchor,
      beatgrid_pos: anchor,
      scan_phase: anchor,
      needs_align: false,
    };
    state.gridPreflight = stamped;
    const row = state.tracks.find((t) => t.path === track.path);
    if (row) row.grid = { ...(row.grid || {}), ...stamped };
    if (track.grid) track.grid = { ...track.grid, ...stamped };
    if (!state.gridManualConfirmed) state.gridManualConfirmed = {};
    state.gridManualConfirmed[track.path] = true;

    exitGridAlignMode({ restoreView: false });
    renderCues();
    drawWaveform();
    setStatus(
      (wantHalve ? "½ BPM + " : "") +
        `beatgrid 1 @ ${anchor.toFixed(3)}s` +
        (r.changes?.scan_phase_updated ? " · Scan Phase" : "") +
        (r.changes?.beatgrid_poi_updated || r.changes?.beatgrid_poi_created
          ? " · beatgrid POI"
          : ""),
      "success"
    );
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function attemptAutoGridAlign() {
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  const btn = $("autoAlignGridBtn");
  if (btn) btn.disabled = true;
  try {
    setStatus("Attempting automatic beatgrid align (stems + onsets)…", "running");
    const data = await api("/api/grid-align/attempt", {
      method: "POST",
      body: JSON.stringify({ path: track.path, apply: false }),
    });
    const result = data.result || {};
    const plan = result.plan || {};
    const action = String(plan.action || "skip");
    if (action === "skip") {
      state.gridAlignPlan = null;
      setStatus(plan.reason || "Grid already looks aligned — no change.", "success");
      return;
    }
    const proposed = Number(plan.anchor_after);
    if (!Number.isFinite(proposed)) {
      setStatus("Auto-align did not return a usable 1.", "error");
      return;
    }
    state.gridAlignPlan = plan;
    if (!state.gridAlignMode) openGridAlignMode();
    state.gridAlignAnchor = proposed;
    state.showBeatOnes = true;
    syncBeatOnesBtn();
    zoomWaveForGridAlign(track);
    syncGridAlignUi();
    drawWaveform();
    const halfNote = plan.halve
      ? ` · will also write ½ BPM (${Number(plan.bpm_before).toFixed(0)}→${Number(plan.bpm_after).toFixed(0)})`
      : "";
    setStatus(
      `Auto-align preview · 1 @ ${proposed.toFixed(3)}s` +
        (plan.shift_beats ? ` · +${plan.shift_beats} beat` : "") +
        halfNote +
        ". Listen, then Apply to VDJ.",
      "running"
    );
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function onGridAlignPointerDown(e) {
  if (!state.gridAlignMode) return false;
  if (e.button != null && e.button !== 0) return false;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return false;
  if (!waveformReadyFor(track)) return false;
  const duration = waveformDuration(track, audio);
  if (!duration) return false;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  state.gridAlignDragging = true;
  state.gridAlignDragOriginTime = t;
  state.gridAlignDragOriginAnchor = Number(state.gridAlignAnchor) || 0;
  wrap.setPointerCapture?.(e.pointerId);
  e.preventDefault();
  return true;
}

function onGridAlignPointerMove(e) {
  if (!state.gridAlignMode || !state.gridAlignDragging) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  const delta = t - state.gridAlignDragOriginTime;
  state.gridAlignAnchor = Math.max(
    0,
    state.gridAlignDragOriginAnchor + delta
  );
  syncGridAlignUi();
  drawWaveform();
  e.preventDefault();
}

function onGridAlignPointerUp(e) {
  if (!state.gridAlignDragging) return;
  state.gridAlignDragging = false;
  try {
    $("waveformWrap")?.releasePointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }
}

function drawWaveform() {
  const canvas = $("waveformCanvas");
  if (!canvas) return;
  const wrap = $("waveformWrap");
  const dpr = window.devicePixelRatio || 1;
  const cssW = wrap?.clientWidth || canvas.clientWidth || 600;
  const cssH = wrap?.clientHeight || 150;
  if (canvas.width !== Math.floor(cssW * dpr) || canvas.height !== Math.floor(cssH * dpr)) {
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = cssW;
  const h = cssH;

  // Background
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0a0e16";
  ctx.fillRect(0, 0, w, h);

  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio);
  const t = audio && Number.isFinite(audio.currentTime) ? audio.currentTime : NaN;
  const view = duration
    ? applyPlayheadFollow(duration, t)
    : waveViewWindow(duration || 1);
  const { padX, plotW } = wavePlotMetrics(w);

  const peaks = state.waveform?.peaks;
  if (!peaks || !peaks.length) {
    ctx.strokeStyle = "rgba(42,51,68,0.8)";
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    positionWavePlayhead(ctx, audio, view, padX, plotW, h);
    return;
  }
  const mid = h / 2;
  const visiblePeaks = peaksForView(peaks, view, duration || 1);

  // Center line
  ctx.strokeStyle = "rgba(42,51,68,0.9)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padX, mid);
  ctx.lineTo(w - padX, mid);
  ctx.stroke();

  // Waveform polygon (visible window only)
  ctx.beginPath();
  const nVis = visiblePeaks.length;
  for (let i = 0; i < nVis; i++) {
    const x = padX + (i / Math.max(1, nVis - 1)) * plotW;
    const amp = Math.min(1, visiblePeaks[i]) * (h * 0.42);
    const y = mid - amp;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  for (let i = nVis - 1; i >= 0; i--) {
    const x = padX + (i / Math.max(1, nVis - 1)) * plotW;
    const amp = Math.min(1, visiblePeaks[i]) * (h * 0.42);
    ctx.lineTo(x, mid + amp);
  }
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, accentRgba(0.68));
  grad.addColorStop(0.5, accentRgba(0.3));
  grad.addColorStop(1, accentRgba(0.58));
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.strokeStyle = accentRgba(0.38);
  ctx.stroke();

  if (!duration) {
    positionWavePlayhead(ctx, audio, view, padX, plotW, h);
    return;
  }

  // Bar “1” grid (under cues so markers stay readable)
  drawBeatOnes(ctx, track, view, padX, plotW, h);

  // Honor Both / Cues / Loops tabs on the waveform too.
  const points = filteredCuePoints(track?.cues?.points || []);
  const bpm = trackBpm(track);

  // Loop bands first (full duration translucent fill)
  // Apply live preview position while dragging a loop.
  // While a drag is live the moving marker is a DOM overlay (transform only); the canvas draws the rest
  // once, without the dragged marker. After the drop, dropPreview keeps it at its new place until the
  // optimistic model update lands.
  const drag = state.loopDrag || state.dropPreview;
  const hideDragged = Boolean(state.loopDrag && state.loopDrag.overlay);
  state.waveLoopRects = [];
  for (const p of points) {
    if (pointKind(p) !== "loop") continue;
    if (hideDragged && isDraggedMarker(drag, p)) continue;
    let start = Number(p.pos) || 0;
    if (
      drag &&
      (drag.kind || "loop") === "loop" &&
      drag.previewPos != null &&
      Math.abs(Number(drag.originPos) - start) < 0.02 &&
      (drag.point?.name === p.name || drag.point?.slot === p.slot)
    ) {
      start = Number(drag.previewPos);
    }
    let len = loopDurationSeconds(p, bpm);
    if (
      drag &&
      drag.edge === "end" &&
      (drag.kind || "loop") === "loop" &&
      Math.abs(Number(drag.originPos) - (Number(p.pos) || 0)) < 0.02 &&
      Number(drag.previewSize) > 0 &&
      bpm > 0
    ) {
      len = (Number(drag.previewSize) * 60) / bpm;
    }
    if (len <= 0) continue;
    const end = start + len;
    // Skip if entirely outside the visible window
    if (end < view.start || start > view.end) continue;

    const x0 = timeToWaveX(Math.max(start, view.start), padX, plotW, view);
    const x1 = timeToWaveX(Math.min(end, view.end), padX, plotW, view);
    const width = Math.max(2, x1 - x0);
    state.waveLoopRects.push({ key: cueKey(p), x0, x1: x0 + width, w: width });
    const draggingThis =
      drag &&
      (drag.kind || "loop") === "loop" &&
      Math.abs(Number(drag.originPos) - (Number(p.pos) || 0)) < 0.02;
    ctx.save();
    ctx.fillStyle = cueRgba(p.color_name, draggingThis ? 0.38 : 0.22);
    ctx.fillRect(x0, 4, width, h - 8);
    // Soft edges
    ctx.strokeStyle = cueRgba(p.color_name, draggingThis ? 0.85 : 0.45);
    ctx.lineWidth = draggingThis ? 2 : 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(x0 + 0.5, 4.5, width - 1, h - 9);
    ctx.setLineDash([]);
    ctx.restore();
  }

  // Snap indicator: light line + tag at the phrase [1] the dragged loop edge locked to.
  if (!hideDragged && drag && drag.snapped && drag.snapTime != null && (drag.kind || "loop") === "loop") {
    const sx = timeToWaveX(Number(drag.snapTime), padX, plotW, view);
    if (sx >= padX - 1 && sx <= padX + plotW + 1) {
      ctx.save();
      ctx.strokeStyle = "rgba(255, 244, 200, 0.9)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(sx + 0.5, 2);
      ctx.lineTo(sx + 0.5, h - 2);
      ctx.stroke();
      ctx.setLineDash([]);
      const tag = "snap [1]";
      ctx.font = "bold 10px SF Pro Text, system-ui, sans-serif";
      const tw = ctx.measureText(tag).width + 10;
      const tx = Math.min(Math.max(sx - tw / 2, padX), padX + plotW - tw);
      ctx.fillStyle = "rgba(255, 244, 200, 0.92)";
      ctx.fillRect(tx, h / 2 - 8, tw, 16);
      ctx.fillStyle = "#1b1608";
      ctx.fillText(tag, tx + 5, h / 2 + 4);
      ctx.restore();
      state.lastSnapIndicator = { time: Number(drag.snapTime), x: sx };
    }
  } else if (!hideDragged) {
    state.lastSnapIndicator = null;
  }

  // Cue / loop start markers (lines first). Labels laid out separately so
  // cues (top) and loops (bottom) never share the same text band.
  // Already filtered by the Both/Cues/Loops tab above.
  const labelCandidates = [];
  ctx.font = "10px SF Pro Text, system-ui, sans-serif";
  for (const p of points) {
    const kind = pointKind(p);
    if (hideDragged && isDraggedMarker(drag, p)) continue;
    let t = Number(p.pos) || 0;
    if (
      drag &&
      drag.previewPos != null &&
      (drag.kind || "loop") === kind &&
      Math.abs(Number(drag.originPos) - t) < 0.02
    ) {
      t = Number(drag.previewPos);
    }
    const loopLen = kind === "loop" ? loopDurationSeconds(p, bpm) : 0;
    const tEnd = kind === "loop" ? t + loopLen : t;
    // Show if start or any part of loop is in view
    if (kind === "loop") {
      if (tEnd < view.start || t > view.end) continue;
    } else if (t < view.start - 0.05 || t > view.end + 0.05) {
      continue;
    }

    // Use stored pos (not view.start) so Cue 1 at 0.030s sits on the yellow 1, not file start.
    const x = timeToWaveX(t, padX, plotW, view);
    const color = CUE_COLORS[p.color_name] || CUE_COLORS.unknown;
    ctx.save();
    ctx.strokeStyle = color;
    const draggingCue =
      kind === "cue" &&
      drag &&
      drag.kind === "cue" &&
      Math.abs(Number(drag.originPos) - (Number(p.pos) || 0)) < 0.02;
    ctx.lineWidth = kind === "loop" ? 1.5 : draggingCue ? 3 : 2;
    if (kind === "loop") ctx.setLineDash([5, 4]);
    // Leave headroom for cue labels at top and loop labels at bottom.
    ctx.beginPath();
    ctx.moveTo(x, 18);
    ctx.lineTo(x, h - 18);
    ctx.stroke();
    // Loop end marker
    if (kind === "loop" && loopLen > 0 && tEnd >= view.start && tEnd <= view.end) {
      const xEnd = timeToWaveX(tEnd, padX, plotW, view);
      ctx.beginPath();
      ctx.moveTo(xEnd, 18);
      ctx.lineTo(xEnd, h - 18);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.restore();

    const name =
      kind === "loop" && p.size
        ? `${(p.name || "Loop").slice(0, 14)} ${p.size}b`
        : (p.name || kind).slice(0, 16);
    const text = `${name} ${fmtTime(t)}`;
    const textW = Math.ceil(ctx.measureText(text).width);
    labelCandidates.push({
      kind,
      x,
      text,
      textW,
      color,
    });
  }

  drawWaveformLabels(ctx, labelCandidates, w, h, padX);

  // Ghost marker while placing a cue
  const ghost = Number(state.placeCuePreview);
  if (
    state.placeCueMode &&
    Number.isFinite(ghost) &&
    ghost >= view.start - 0.05 &&
    ghost <= view.end + 0.05
  ) {
    const gx = timeToWaveX(ghost, padX, plotW, view);
    ctx.save();
    ctx.strokeStyle = "rgba(52, 211, 153, 0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(gx, 10);
    ctx.lineTo(gx, h - 10);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(10, 14, 22, 0.78)";
    ctx.fillRect(gx + 4, 8, 86, 14);
    ctx.fillStyle = "#6ee7b7";
    ctx.font = "10px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(`Cue @ ${fmtTime(ghost)}`, gx + 6, 18);
    ctx.restore();
  }

  const loopGhost = Number(state.placeLoopPreview);
  if (
    state.placeLoopMode &&
    Number.isFinite(loopGhost) &&
    loopGhost >= view.start - 0.05 &&
    loopGhost <= view.end + 0.05
  ) {
    const bpm = onesBpm(track) || trackBpm(track);
    const len = bpm && bpm > 0 ? (60 / bpm) * 8 : 0;
    const gx = timeToWaveX(loopGhost, padX, plotW, view);
    ctx.save();
    if (len > 0) {
      const x1 = timeToWaveX(loopGhost + len, padX, plotW, view);
      ctx.fillStyle = "rgba(168, 85, 247, 0.22)";
      ctx.fillRect(gx, 4, Math.max(2, x1 - gx), h - 8);
    }
    ctx.strokeStyle = "rgba(168, 85, 247, 0.95)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(gx, 10);
    ctx.lineTo(gx, h - 10);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(10, 14, 22, 0.78)";
    ctx.fillRect(gx + 4, 8, 92, 14);
    ctx.fillStyle = "#d8b4fe";
    ctx.font = "10px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(`Loop 8b @ ${fmtTime(loopGhost)}`, gx + 6, 18);
    ctx.restore();
  }

  // Playhead — follow while moving so it never vanishes at a zoom width
  positionWavePlayhead(ctx, audio, view, padX, plotW, h);

  // Zoom / window chrome — cues outside this slice stay as chips + overview ticks
  if (state.waveZoom > 1.01) {
    ctx.fillStyle = "rgba(10, 14, 22, 0.72)";
    ctx.fillRect(padX, h - 34, 168, 16);
    ctx.fillStyle = "rgba(232, 237, 247, 0.9)";
    ctx.font = "11px SF Pro Text, system-ui, sans-serif";
    ctx.fillText(
      `${state.waveZoom.toFixed(1)}×  ${fmtTime(view.start)}–${fmtTime(view.end)}`,
      padX + 6,
      h - 22
    );
  }
  const classified = classifyWaveMarkers(points, view);
  drawWaveCueOverview(ctx, points, view, duration, padX, plotW, h);
  drawOffscreenCueHints(ctx, classified, padX, plotW, h);
}

/**
 * Place cue labels along the top and loop labels along the bottom.
 * Within each band, stack / nudge so nearby markers' text doesn't collide.
 */
function drawWaveformLabels(ctx, candidates, w, h, padX) {
  if (!candidates.length) return;

  const rowH = 13;
  const pad = 3;
  const maxRows = 4;

  const cues = candidates
    .filter((c) => c.kind === "cue")
    .sort((a, b) => a.x - b.x);
  const loops = candidates
    .filter((c) => c.kind === "loop")
    .sort((a, b) => a.x - b.x);

  // Top band grows downward from y=11; bottom band grows upward from y=h-6.
  const cuePlaced = layoutLabelRows(cues, {
    baseY: 11,
    rowStep: rowH,
    direction: 1,
    maxRows,
    pad,
  });
  const loopPlaced = layoutLabelRows(loops, {
    baseY: h - 6,
    rowStep: rowH,
    direction: -1,
    maxRows,
    pad,
  });

  state.lastWaveLabels = [...cuePlaced, ...loopPlaced].map((it) => it.text); // test/debug hook
  ctx.save();
  ctx.font = "10px SF Pro Text, system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";

  for (const item of [...cuePlaced, ...loopPlaced]) {
    // Keep text inside the plot horizontally.
    let textX = item.x + 3;
    if (textX + item.textW + 4 > w - padX) {
      textX = Math.max(padX, item.x - item.textW - 3);
    }
    const boxX = textX - 2;
    const boxY = item.y - 10;
    const boxW = item.textW + 4;
    const boxH = 12;

    ctx.fillStyle = "rgba(10, 14, 22, 0.78)";
    ctx.fillRect(boxX, boxY, boxW, boxH);
    // Accent bar on the left edge of the pill for kind separation.
    ctx.fillStyle = item.color;
    ctx.fillRect(boxX, boxY, 2, boxH);
    ctx.fillStyle = item.color;
    ctx.fillText(item.text, textX, item.y);
  }
  ctx.restore();
}

function layoutLabelRows(items, { baseY, rowStep, direction, maxRows, pad }) {
  /** @type {{ x: number, textW: number, y: number, text: string, color: string }[]} */
  const placed = [];
  for (const item of items) {
    let row = 0;
    let y = baseY;
    while (row < maxRows) {
      y = baseY + direction * row * rowStep;
      const collides = placed.some((p) => {
        if (Math.abs(p.y - y) > rowStep - 1) return false;
        // Horizontal overlap of text boxes (with padding).
        const a0 = item.x;
        const a1 = item.x + item.textW + pad * 2 + 6;
        const b0 = p.x;
        const b1 = p.x + p.textW + pad * 2 + 6;
        return a0 < b1 && b0 < a1;
      });
      if (!collides) break;
      row += 1;
    }
    placed.push({
      x: item.x,
      textW: item.textW,
      y,
      text: item.text,
      color: item.color,
    });
  }
  return placed;
}

function snapshotWaveSeekTime(clientX) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) {
    state.waveSeekTime = null;
    return;
  }
  const duration = waveformDuration(track, audio);
  if (!duration) {
    state.waveSeekTime = null;
    return;
  }
  const rect = wrap.getBoundingClientRect();
  state.waveSeekTime = clientXToTime(clientX, rect, duration);
}

function seekFromWaveformEvent(e) {
  const chrome = hitTestWaveCueChrome(e.clientX, e.clientY);
  if (chrome) {
    state.waveSeekTime = null;
    if (chrome.time != null && Number.isFinite(Number(chrome.time))) {
      panWaveToTime(chrome.time, {
        frac: chrome.kind === "overview" ? 0.5 : 0.22,
      });
    }
    return;
  }
  // In align mode, drag moves the grid — don't seek.
  if (state.gridAlignMode) {
    state.waveSeekTime = null;
    return;
  }
  // After a loop/cue drag, suppress seek (click fires after pointerup).
  if (state.loopDrag?.moved || state._suppressWaveSeek) {
    state._suppressWaveSeek = false;
    state.waveSeekTime = null;
    return;
  }
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const snapped = Number(state.waveSeekTime);
  state.waveSeekTime = null;
  const t = Number.isFinite(snapped)
    ? snapped
    : clientXToTime(e.clientX, wrap.getBoundingClientRect(), duration);
  if (state.placeLoopMode) {
    if (e.detail > 1) return;
    if (refuseWhileWaveLoading(track, "Place the loop")) return;
    const freeLoop = Boolean(e.shiftKey || e.altKey); // Shift/Alt = no snap; default = phrase [1]
    const pos = snapPhraseTime(t, { free: freeLoop });
    const existing = existingLoopNear(pos);
    if (existing) {
      jumpToCue(Number(existing.pos) || pos, existing);
      return;
    }
    placeLoopAtTime(pos, { free: freeLoop, alreadySnapped: true, forPath: track.path });
    return;
  }
  if (state.placeCueMode || e.altKey) {
    if (e.detail > 1) return;
    if (refuseWhileWaveLoading(track, "Place the cue")) return;
    const pos = snapCueDragTime(t, { free: Boolean(e.shiftKey) });
    const existing = existingCueNear(pos);
    if (existing) {
      jumpToCue(Number(existing.pos) || pos, existing);
      return;
    }
    placeCueAtTime(pos, { free: Boolean(e.shiftKey), alreadySnapped: true, forPath: track.path });
    return;
  }
  jumpToCue(t);
}

/** Snap time to nearest beat (or free if no BPM / Shift held). */
function snapLoopDragTime(t, { free = false } = {}) {
  if (free) return Math.max(0, t);
  const track = currentTrack();
  const bpm = onesBpm(track) || trackBpm(track);
  if (!bpm || bpm <= 0) return Math.max(0, t);
  const beatSec = 60 / bpm;
  const anchor = gridAnchorSeconds(track);
  const steps = Math.round((t - anchor) / beatSec);
  return Math.max(0, anchor + steps * beatSec);
}

/** 16-beat phrase length (the yellow phrase [1] lines on the beatgrid), or null. */
function phrasePeriodSeconds(track) {
  const bar = barPeriodSeconds(track);
  return bar && bar > 0 ? bar * 4 : null;
}

/**
 * Snap to the nearest phrase [1]: anchor + k·16 beats. Same anchor and BPM the
 * yellow phrase lines and the ALIGN tool use (gridAnchorSeconds / barPeriodSeconds).
 */
function snapPhraseTime(t, { free = false } = {}) {
  if (free) return Math.max(0, t);
  const track = currentTrack();
  const period = phrasePeriodSeconds(track);
  if (!period) return snapLoopDragTime(t, { free: false });
  const anchor = gridAnchorSeconds(track);
  let out = anchor + Math.round((t - anchor) / period) * period;
  if (out < 0) out += Math.ceil(-out / period) * period;
  return out;
}

/** Snap time to the nearest bar 1 (downbeat). Shift / free skips snap. */
function snapCueDragTime(t, { free = false } = {}) {
  if (free) return Math.max(0, t);
  const track = currentTrack();
  const barSec = barPeriodSeconds(track);
  if (!barSec || barSec <= 0) {
    return snapLoopDragTime(t, { free: false });
  }
  const anchor = gridAnchorSeconds(track);
  const steps = Math.round((t - anchor) / barSec);
  return Math.max(0, anchor + steps * barSec);
}

function snapMarkerDragTime(t, { kind = "loop", free = false } = {}) {
  return kind === "cue"
    ? snapCueDragTime(t, { free })
    : snapLoopDragTime(t, { free });
}

/** Unfiltered cue sitting on this time (any tab). */
function existingCueNear(pos, tol = 0.03) {
  const points = currentTrack()?.cues?.points || [];
  const target = Number(pos);
  if (!Number.isFinite(target)) return null;
  return (
    points.find(
      (p) =>
        pointKind(p) === "cue" &&
        Math.abs((Number(p.pos) || 0) - target) <= tol
    ) || null
  );
}

function existingLoopNear(pos, tol = 0.03) {
  const points = currentTrack()?.cues?.points || [];
  const target = Number(pos);
  if (!Number.isFinite(target)) return null;
  return (
    points.find(
      (p) =>
        pointKind(p) === "loop" &&
        Math.abs((Number(p.pos) || 0) - target) <= tol
    ) || null
  );
}

/**
 * Hit-test cue start lines under the cursor (~10px).
 * Returns { point, hit: 'start' } or null.
 */
function hitTestCueAtClientX(clientX) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return null;
  const duration = waveformDuration(track, audio);
  if (!duration) return null;
  const rect = wrap.getBoundingClientRect();
  const { padX, plotW } = wavePlotMetrics(rect.width);
  const view = waveViewWindow(duration);
  const x = clientX - rect.left;

  const cues = filteredCuePoints(track.cues?.points || []).filter(
    (p) => pointKind(p) === "cue"
  );
  let best = null;
  let bestDist = 10;
  for (const p of cues) {
    const start = Number(p.pos) || 0;
    const sx = timeToWaveX(start, padX, plotW, view);
    const d = Math.abs(x - sx);
    if (d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  if (best) return { point: best, hit: "start", kind: "cue" };
  return null;
}

/**
 * Hit-test loops under the cursor. Prefers start handle, then body of loop band.
 * Returns { point, hit: 'start'|'body' } or null.
 */
function hitTestLoopAtClientX(clientX) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return null;
  const duration = waveformDuration(track, audio);
  if (!duration) return null;
  const rect = wrap.getBoundingClientRect();
  const { padX, plotW } = wavePlotMetrics(rect.width);
  const view = waveViewWindow(duration);
  const t = clientXToTime(clientX, rect, duration);
  const bpm = trackBpm(track);
  const x = clientX - rect.left;

  const loops = filteredCuePoints(track.cues?.points || []).filter(
    (p) => pointKind(p) === "loop"
  );
  // Edge handles: start (grab radius 12px) and end (9px). A short loop is only a few px wide at full
  // zoom, so each zone shrinks to a third of the loop width and the nearest edge wins.
  let best = null;
  for (const p of loops) {
    const start = Number(p.pos) || 0;
    const len = loopDurationSeconds(p, bpm);
    const sx = timeToWaveX(start, padX, plotW, view);
    const ex = len > 0 ? timeToWaveX(start + len, padX, plotW, view) : sx;
    const w = Math.abs(ex - sx);
    const zone = Math.min(12, Math.max(3, w / 3));
    const ds = Math.abs(x - sx);
    if (ds < zone && (!best || ds < best.d)) best = { d: ds, point: p, hit: "start" };
    if (len > 0) {
      const de = Math.abs(x - ex);
      if (de < Math.min(zone, 9) && (!best || de <= best.d)) best = { d: de, point: p, hit: "end" };
    }
  }
  if (best) return { point: best.point, hit: best.hit, kind: "loop" };

  // Else body of loop region
  for (const p of loops) {
    const start = Number(p.pos) || 0;
    const len = loopDurationSeconds(p, bpm);
    if (len <= 0) continue;
    if (t >= start - 0.02 && t <= start + len + 0.02) {
      return { point: p, hit: "body", kind: "loop" };
    }
  }
  return null;
}

function isDraggedMarker(drag, p) {
  if (!drag || !p) return false;
  if (pointKind(p) !== (drag.kind || "loop")) return false;
  if (Math.abs(Number(drag.originPos) - (Number(p.pos) || 0)) >= 0.02) return false;
  const dp = drag.point || {};
  if (drag.kind === "cue") return dp.num == null || p.num == null || String(dp.num) === String(p.num);
  return dp.slot == null || p.slot == null || String(dp.slot) === String(p.slot);
}

/* Drag overlay: the moving highlight is a few absolutely positioned elements moved ONLY with
   transform: translate3d / scaleX. No canvas redraw, no layout read, no style recalculation of the page. */
function createDragOverlay(drag) {
  const wrap = $("waveformWrap");
  if (!wrap) return;
  removeDragOverlay();
  const color = CUE_COLORS[drag.point?.color_name] || CUE_COLORS.unknown;
  const root = document.createElement("div");
  root.id = "dragOverlay";
  root.className = `drag-overlay drag-overlay-${drag.kind || "loop"}`;
  const mk = (cls) => {
    const el = document.createElement("div");
    el.className = `do-el ${cls}`;
    root.appendChild(el);
    return el;
  };
  const els = {
    root,
    fill: mk("do-fill"),
    left: mk("do-edge do-edge-l"),
    right: mk("do-edge do-edge-r"),
    snap: mk("do-snap"),
    tag: mk("do-tag"),
    label: mk("do-label"),
  };
  els.fill.style.background = cueRgba(drag.point?.color_name, 0.38);
  els.left.style.background = color;
  els.right.style.background = color;
  els.tag.textContent = "snap [1]";
  els.label.textContent = drag.point?.name || (drag.kind === "cue" ? "Cue" : "Loop");
  if (drag.kind === "cue") {
    els.fill.style.display = "none";
    els.right.style.display = "none";
  }
  els.tag.style.visibility = "hidden";
  els.snap.style.visibility = "hidden";
  wrap.appendChild(root);
  drag.overlay = els;
}

function removeDragOverlay() {
  document.getElementById("dragOverlay")?.remove();
}

function updateDragOverlay(drag) {
  const o = drag.overlay;
  const c = drag.ctx;
  if (!o || !c) return;
  const x = (t) => c.padX + ((t - c.view.start) / (c.view.span || 1)) * c.plotW;
  const px = (v) => Math.round(v * 2) / 2; // half-pixel steps keep the lines crisp
  const startT = drag.previewPos;
  const x0 = x(startT);
  if (drag.kind === "cue") {
    o.left.style.transform = `translate3d(${px(x0) - 1}px,0,0)`;
    o.label.style.transform = `translate3d(${px(x0) + 5}px,0,0)`;
  } else {
    const lenT =
      drag.edge === "end" && c.bpm > 0 && drag.previewSize > 0
        ? (drag.previewSize * 60) / c.bpm
        : c.originLen || 0;
    const x1 = x(startT + lenT);
    const w = Math.max(2, x1 - x0);
    o.fill.style.transform = `translate3d(${px(x0)}px,0,0) scaleX(${w})`;
    o.left.style.transform = `translate3d(${px(x0)}px,0,0)`;
    o.right.style.transform = `translate3d(${px(x1) - 2}px,0,0)`;
    o.label.style.transform = `translate3d(${px(x0) + 6}px,0,0)`;
  }
  if (drag.snapped && drag.snapTime != null) {
    const sx = x(Number(drag.snapTime));
    o.snap.style.visibility = "visible";
    o.tag.style.visibility = "visible";
    o.snap.style.transform = `translate3d(${px(sx)}px,0,0)`;
    o.tag.style.transform = `translate3d(${Math.min(Math.max(sx - 24, c.padX), c.padX + c.plotW - 52)}px,0,0)`;
    state.lastSnapIndicator = { time: Number(drag.snapTime), x: sx };
  } else {
    o.snap.style.visibility = "hidden";
    o.tag.style.visibility = "hidden";
    state.lastSnapIndicator = null;
  }
  state.dragOverlayUpdates = (state.dragOverlayUpdates || 0) + 1;
}

/* Right-click a cue or loop on the waveform -> small menu with Delete (uses the same optimistic
   delete + Undo toast as the list). */
function closeWaveMarkerMenu() {
  document.getElementById("waveMarkerMenu")?.remove();
  document.removeEventListener("pointerdown", onWaveMenuOutside, true);
  document.removeEventListener("keydown", onWaveMenuKey, true);
}

function onWaveMenuOutside(e) {
  if (!e.target?.closest?.("#waveMarkerMenu")) closeWaveMarkerMenu();
}

function onWaveMenuKey(e) {
  if (e.key === "Escape") {
    e.stopPropagation();
    closeWaveMarkerMenu();
  }
}

function onWaveformContextMenu(e) {
  if (state.loopDrag && !state.loopDrag.moved) {
    // Ctrl+click on a Mac starts a drag first; a right-click is not a drag.
    if (state.loopDrag.raf) cancelAnimationFrame(state.loopDrag.raf);
    state.loopDrag = null;
    $("waveformWrap")?.classList.remove("loop-dragging", "cue-dragging");
  }
  if (state.gridAlignMode || state.loopDrag || state.placeCueMode || state.placeLoopMode) return;
  const hit = hitTestCueAtClientX(e.clientX) || hitTestLoopAtClientX(e.clientX);
  closeWaveMarkerMenu();
  if (!hit) return;
  e.preventDefault();
  e.stopPropagation();
  const point = hit.point;
  const kind = pointKind(point);
  const label = point.name || (kind === "loop" ? "Loop" : "Cue");
  const menu = document.createElement("div");
  menu.id = "waveMarkerMenu";
  menu.className = "wave-marker-menu";
  menu.setAttribute("role", "menu");
  menu.style.left = `${Math.min(e.clientX, window.innerWidth - 220)}px`;
  menu.style.top = `${Math.min(e.clientY, window.innerHeight - 80)}px`;
  const title = document.createElement("div");
  title.className = "wave-marker-menu-title";
  title.textContent = `${kind === "loop" ? "Loop" : "Cue"} · ${label} · ${fmtTime(Number(point.pos) || 0)}`;
  const del = document.createElement("button");
  del.type = "button";
  del.className = "wave-marker-menu-item danger";
  del.setAttribute("role", "menuitem");
  del.dataset.action = "delete";
  del.textContent = `Delete ${kind}`;
  del.addEventListener("click", () => {
    closeWaveMarkerMenu();
    deleteCuePoint(point);
  });
  menu.append(title, del);
  document.body.appendChild(menu);
  document.addEventListener("pointerdown", onWaveMenuOutside, true);
  document.addEventListener("keydown", onWaveMenuKey, true);
  del.focus();
}

function onLoopDragPointerDown(e) {
  if (state.gridAlignMode) return false;
  if (e.button != null && e.button !== 0) return false;
  // Don't steal events from buttons/selects
  if (e.target?.closest?.("button, select, a, input, label")) return false;
  if (!waveformReadyFor(currentTrack())) return false; // R-97: no drag from a picture that is not this song's
  const hit = hitTestCueAtClientX(e.clientX) || hitTestLoopAtClientX(e.clientX);
  if (!hit) return false;
  // Alt+click on empty wave / a cue places a cue. On a loop, Alt starts a free (no-snap) drag.
  if (e.altKey && hit.kind !== "loop") return false;

  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  const duration = waveformDuration(track, audio);
  if (!wrap || !duration) return false;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(e.clientX, rect, duration);
  const originPos = Number(hit.point.pos) || 0;
  const kind = hit.kind || pointKind(hit.point);

  const edge = hit.hit === "end" ? "end" : "move";
  const originSize = Number(hit.point.size) || 0;
  const originEnd = originPos + loopDurationSeconds(hit.point, trackBpm(track));
  state.loopDrag = {
    path: track.path,
    kind,
    edge,
    point: { ...hit.point },
    originPos,
    originSize,
    previewPos: originPos,
    previewSize: originSize,
    grabOffset: edge === "end" ? t - originEnd : t - originPos, // keep relative grab
    pointerId: e.pointerId,
    moved: false,
    free: Boolean(e.shiftKey),
    snapped: false,
    snapTime: null,
    raf: 0,
    lastPtr: null,
    ctx: {
      rect,
      duration,
      bpm: trackBpm(track),
      period: phrasePeriodSeconds(track),
      originLen: loopDurationSeconds(hit.point, trackBpm(track)),
      ...(() => {
        const m = wavePlotMetrics(rect.width);
        return { padX: m.padX, plotW: m.plotW, view: { ...waveViewWindow(duration) } };
      })(),
    },
  };
  wrap.classList.add(kind === "cue" ? "cue-dragging" : "loop-dragging");
  createDragOverlay(state.loopDrag);
  updateDragOverlay(state.loopDrag);
  drawWaveform(); // once, so the canvas stops drawing the marker that now moves as an overlay
  try {
    wrap.setPointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }
  e.preventDefault();
  return true;
}

/* Drag = pointer position -> preview, at most once per animation frame. No network, no layout reads
   (the wave rect / duration / bpm are cached at pointer-down) and the commit happens on pointer-up. */
function onLoopDragPointerMove(e) {
  const drag = state.loopDrag;
  if (!drag) return;
  drag.lastPtr = { x: e.clientX, alt: e.altKey, shift: e.shiftKey };
  e.preventDefault();
  if (drag.raf) return;
  drag.raf = requestAnimationFrame(() => {
    drag.raf = 0;
    if (state.loopDrag === drag) applyLoopDragFrame(drag);
  });
}

function applyLoopDragFrame(drag) {
  const ptr = drag.lastPtr;
  const track = currentTrack();
  const ctx = drag.ctx;
  if (!ptr || !track || !ctx) return;
  state.dragFrames = (state.dragFrames || 0) + 1;
  const duration = ctx.duration || 0;
  const t = clientXToTime(ptr.x, ctx.rect, duration || 1);
  let next = t - (Number(drag.grabOffset) || 0);
  const free = Boolean(drag.free || ptr.shift || ptr.alt); // Alt/Option (or Shift) bypasses snapping
  if ((drag.kind || "loop") === "loop") {
    const bpm = ctx.bpm;
    if (drag.edge === "end" && bpm > 0) {
      // Resize: the END edge snaps to phrase [1] (16 beats); the start stays put.
      const beatSec = 60 / bpm;
      const start = drag.originPos;
      let endT = snapPhraseTime(next, { free });
      const period = ctx.period;
      while (!free && period && endT < start + beatSec - 1e-6) endT += period;
      if (duration > 0) endT = Math.min(endT, duration);
      endT = Math.max(endT, start + beatSec);
      let beats = ((endT - start) * bpm) / 60;
      beats = free ? Math.round(beats * 4) / 4 : Math.round(beats * 1000) / 1000;
      beats = Math.min(256, Math.max(1, beats));
      drag.previewSize = beats;
      drag.snapped = !free;
      drag.snapTime = start + (beats * 60) / bpm;
      if (Math.abs(beats - drag.originSize) > 0.02) drag.moved = true;
      updateDragOverlay(drag);
      return;
    }
    next = snapPhraseTime(next, { free });
    drag.snapped = !free;
    drag.snapTime = next;
  } else {
    next = snapMarkerDragTime(next, { kind: drag.kind || "loop", free });
    drag.snapped = false;
    drag.snapTime = null;
  }
  if (duration > 0) {
    // a loop moves as a whole: its END must stay inside the song (never start>=end / off the edge)
    const room = (drag.kind || "loop") === "loop" ? Math.max(0.05, Number(ctx.originLen) || 0) : 0.05;
    next = Math.min(next, Math.max(0, duration - room));
  }
  next = Math.max(0, next);
  if (Math.abs(next - drag.originPos) > 0.01) drag.moved = true;
  drag.previewPos = next;
  updateDragOverlay(drag);
}

async function onLoopDragPointerUp(e) {
  const drag = state.loopDrag;
  if (!drag) return;
  const wrap = $("waveformWrap");
  wrap?.classList.remove("loop-dragging", "cue-dragging");
  try {
    wrap?.releasePointerCapture?.(e.pointerId);
  } catch {
    /* ignore */
  }

  if (drag.raf) {
    cancelAnimationFrame(drag.raf);
    drag.raf = 0;
  }
  if (e && Number.isFinite(e.clientX)) {
    drag.lastPtr = { x: e.clientX, alt: e.altKey, shift: e.shiftKey };
  }
  if (drag.lastPtr) applyLoopDragFrame(drag); // final position, once
  const origin = Number(drag.originPos) || 0;
  const next = Number(drag.previewPos);
  const kind = drag.kind || "loop";
  state.loopDrag = null;
  removeDragOverlay();
  if (drag.path && drag.path !== currentTrack()?.path) {
    state.dropPreview = null;
    drawWaveform();
    loudNotice("The marker was not moved: the open song changed during the drag. Nothing was changed.", "error");
    return;
  }
  state.lastSnapIndicator = null;
  state.dropPreview = drag.moved
    ? {
        kind,
        edge: drag.edge,
        point: drag.point,
        originPos: drag.originPos,
        originSize: drag.originSize,
        previewPos: drag.previewPos,
        previewSize: drag.previewSize,
        snapped: false,
        snapTime: null,
      }
    : null;
  drawWaveform(); // the single canvas redraw of the whole drag: marker at its dropped place

  if (kind === "loop" && drag.edge === "end") {
    if (!drag.moved || Math.abs(Number(drag.previewSize) - Number(drag.originSize)) < 0.02) {
      state.dropPreview = null; // never leave a stale preview that hides the real loop
      drawWaveform();
      return;
    }
    state._suppressWaveSeek = true;
    await commitLoopResize(drag.point, Number(drag.originSize), Number(drag.previewSize));
    return;
  }

  if (!drag.moved || !Number.isFinite(next) || Math.abs(next - origin) < 0.015) {
    state.dropPreview = null; // dragged away and back: nothing to save, and the loop must stay drawn
    drawWaveform();
    return;
  }

  state._suppressWaveSeek = true;
  if (kind === "cue") {
    await commitCueMove(drag.point, origin, next);
  } else {
    await commitLoopMove(drag.point, origin, next);
  }
}

/** Resize a loop to newBeats (end edge dragged / snapped). Optimistic; reverts on failure. */
async function commitLoopResize(point, oldBeats, newBeats, opts = {}) {
  const track = currentTrack();
  if (!track || !point || !(oldBeats > 0) || !(newBeats > 0)) return;
  const path = track.path;
  point = resolveLivePoint(path, point);
  const key = cueKey(point);
  state.dropPreview = null; // the optimistic model update (or the revert) takes over from here
  // Never past the end of the song: shrink to the room that is left instead of failing.
  const fit = clampLoopBeatsToSongEnd(track, point, newBeats);
  if (fit.clamped) newBeats = fit.beats;
  if (fit.atEnd || Math.abs(newBeats - oldBeats) < 0.005) {
    setStatus(`“${point.name || "Loop"}” already reaches the end of the song (${fmtBeats(oldBeats)}b).`);
    drawWaveform();
    return;
  }
  let allowRunning = false;
  const hist = historyBegin(path);
  const setSize = (beats) => {
    const mp = modelPoint(path, key) || resolveLivePoint(path, point);
    if (mp) mp.size = String(beats);
    markersChanged(path);
    drawWaveform();
    if (currentTrack()?.path === path) renderCues();
  };
  setSize(newBeats); // optimistic
  const label = point.name || "Loop";
  quickConfirm(`Loop “${label}” → ${fmtBeats(newBeats)}b — saving…`);
  if (opts.audition) {
    const live = modelPoint(path, key);
    if (live) auditionLoopPoint(live);
  }
  setStatus(opts.note ? `${opts.note}` : `Loop “${label}” → ${fmtBeats(newBeats)}b…`);
  try {
    const data = await enqueueTrackEdit(path, async () => {
      const guard = await vdjOpenWriteGuard(
        track,
        "Loop size changes may be overwritten when VirtualDJ quits. Close it first when possible.",
        "Resize anyway"
      );
      if (!guard.ok) throw new Error("Close VirtualDJ, then resize the loop.");
      allowRunning = guard.allowRunning;
      const lp = modelPoint(path, key) || resolveLivePoint(path, point); // identity as of NOW
      return api("/api/scale-loop", {
        method: "POST",
        body: JSON.stringify({
          path,
          pos: Number(lp.pos) || 0,
          factor: newBeats / oldBeats,
          num: lp.num != null ? String(lp.num) : null,
          name: lp.name || null,
          slot: lp.slot != null ? String(lp.slot) : null,
          allow_vdj_running: Boolean(allowRunning),
        }),
      });
    });
    const after = Number(data?.result?.change?.beats_after);
    if (Number.isFinite(after) && Math.abs(after - newBeats) > 0.01) setSize(after);
    historyCommit(hist, `size of loop “${label}”`);
    setStatus(
      opts.note || `Loop “${label}” resized to ${fmtBeats(Number.isFinite(after) ? after : newBeats)}b`,
      "success"
    );
  } catch (err) {
    if (!err.keptFailedEdit) setSize(oldBeats);
    setStatus(`Resize failed — ${err.keptFailedEdit ? "kept on screen, see the NOT saved list" : "loop restored"}. ${err.message}`, "error");
  }
}

async function commitLoopMove(point, originPos, newPos) {
  const track = currentTrack();
  if (!track || !point) return;
  const path = track.path;
  const gen = state.trackGen;

  const guard = await vdjOpenWriteGuard(
    track,
    "Moving a loop may be overwritten when VirtualDJ quits. Close it first when possible.",
    "Move anyway"
  );
  state.dropPreview = null; // the optimistic model update (or the revert) takes over from here
  if (!guard.ok) {
    setStatus("Close VirtualDJ, then move the loop.", "error");
    if (stillOnTrack(path, gen)) drawWaveform();
    return;
  }
  const allowRunning = guard.allowRunning;
  {
    const durL = trackDuration(track, $("audio"));
    const lenL = loopDurationSeconds(point, trackBpm(track));
    if (durL > 0 && lenL > 0) newPos = Math.max(0, Math.min(newPos, durL - lenL)); // never past the end
  }

  // Optimistic: the loop is already where you dropped it; the save follows in the background.
  const setPos = (from, to) => {
    const t = (state.tracks || []).find((x) => x.path === path);
    const m = (t?.cues?.points || []).find(
      (p) =>
        pointKind(p) === "loop" &&
        Math.abs(Number(p.pos) - from) < 0.02 &&
        String(p.slot ?? "") === String(point.slot ?? "")
    );
    if (m) {
      m.pos = to;
      if (state.activeLoopKey) {
        state.activeLoopKey = cueKey(m);
        state.activeCueKey = cueKey(m);
      }
    }
    markersChanged(path);
    if (stillOnTrack(path, gen)) {
      renderCues();
      drawWaveform();
    }
    return m;
  };
  const hist = historyBegin(path);
  const moved = setPos(originPos, newPos);
  quickConfirm(`Loop “${point.name || "Loop"}” moved to ${fmtTime(newPos)} — saving…`);
  setStatus(`Moving loop “${point.name || "Loop"}” ${fmtTime(originPos)} → ${fmtTime(newPos)}…`);
  if (moved) auditionLoopPoint(moved);

  try {
    await enqueueTrackEdit(path, () =>
      api("/api/move-poi", {
        method: "POST",
        body: JSON.stringify({
          path,
          kind: "loop",
          pos: originPos,
          new_pos: newPos,
          num: point.num != null ? String(point.num) : null,
          name: point.name || null,
          slot: point.slot != null ? String(point.slot) : null,
          allow_vdj_running: Boolean(allowRunning),
        }),
      })
    );
    historyCommit(hist, `move of loop “${point.name || "Loop"}”`);
    setStatus(
      `Loop “${point.name || "Loop"}” → ${fmtTime(newPos)}` + (point.size ? ` · ${point.size}b` : ""),
      "success"
    );
  } catch (err) {
    if (!err.keptFailedEdit) setPos(newPos, originPos); // revert (a recorded failed save stays on screen + in the NOT saved list)
    setStatus(err.message, "error");
  }
}

async function commitCueMove(point, originPos, newPos, opts = {}) {
  const track = currentTrack();
  if (!track || !point) return;
  const path = track.path;
  const gen = state.trackGen;

  const guard = opts.allowRunning
    ? { ok: true, allowRunning: true }
    : await vdjOpenWriteGuard(
        track,
        "Moving a cue may be overwritten when VirtualDJ quits. Close it first when possible.",
        "Move anyway"
      );
  state.dropPreview = null; // the optimistic model update (or the revert) takes over from here
  if (!guard.ok) {
    setStatus("Close VirtualDJ, then move the cue.", "error");
    if (stillOnTrack(path, gen)) drawWaveform();
    return;
  }
  const allowRunning = guard.allowRunning;

  // Optimistic: list and waveform change TOGETHER, right now; the save follows in the background.
  // A cue at 0:00 has no Pos in VDJ's file, so match by Num/name and a missing pos counts as 0.
  const setPos = (from, to) => {
    const t = (state.tracks || []).find((x) => x.path === path);
    const pts = (t?.cues?.points || []).filter(
      (p) => pointKind(p) === "cue" && Math.abs((Number(p.pos) || 0) - from) < 0.02
    );
    const m =
      pts.find((p) => point.num != null && String(p.num) === String(point.num)) ||
      pts.find((p) => (p.name || "") === (point.name || "")) ||
      pts[0];
    if (m) {
      m.pos = to;
      if (state.activeCueKey) state.activeCueKey = cueKey(m);
      if (t.cues && Array.isArray(t.cues.points)) {
        t.cues.points.sort((x, y) => (Number(x.pos) || 0) - (Number(y.pos) || 0));
      }
    }
    markersChanged(path);
    if (stillOnTrack(path, gen)) {
      renderCues();
      drawWaveform();
    }
    return m;
  };
  const hist = historyBegin(path);
  const moved = setPos(originPos, newPos);
  quickConfirm(`Cue “${point.name || "Cue"}” moved to ${fmtTime(newPos)} — saving…`);
  if (moved) state.activeCueKey = cueKey(moved);
  setStatus(`Moving cue “${point.name || "Cue"}” ${fmtTime(originPos)} → ${fmtTime(newPos)}…`);
  if (moved && stillOnTrack(path, gen)) jumpToCue(newPos, moved);

  try {
    const data = await enqueueTrackEdit(path, () =>
      api("/api/move-poi", {
        method: "POST",
        body: JSON.stringify({
          path,
          kind: "cue",
          pos: originPos,
          new_pos: newPos,
          num: point.num != null ? String(point.num) : null,
          name: (moved && moved.name) || point.name || null,
          allow_vdj_running: Boolean(allowRunning),
        }),
      })
    );
    historyCommit(hist, `move of cue “${point.name || "Cue"}”`);
    setStatus(`Cue “${point.name || "Cue"}” → ${fmtTime(newPos)} (saved)`, "success");
    reconcileSoon(path);
    return data;
  } catch (err) {
    if (!err.keptFailedEdit) setPos(newPos, originPos); // revert: the file does not have the new position
    setStatus(`Move failed — ${err.keptFailedEdit ? `cue stays at ${fmtTime(newPos)} on screen (NOT saved, see the list)` : `cue is back at ${fmtTime(originPos)}`}. ${err.message}`, "error");
  }
}

function syncPlaceCueUi() {
  const btn = $("placeCueBtn");
  if (btn) {
    btn.classList.toggle("active", state.placeCueMode);
    btn.setAttribute("aria-pressed", state.placeCueMode ? "true" : "false");
    btn.textContent = state.placeCueMode ? "Placing…" : "Place cue";
  }
  const bar = $("placeCueBar");
  if (bar) {
    if (state.placeCueMode) {
      bar.hidden = false;
      bar.removeAttribute("hidden");
    } else {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
    }
  }
  const wrap = $("waveformWrap");
  if (wrap) wrap.classList.toggle("place-cue-mode", state.placeCueMode);
  if (!state.placeCueMode) {
    state.placeCuePreview = null;
    wrap?.classList.remove("place-cue-mode");
  }
}

function togglePlaceCueMode() {
  if (state.placeCueMode) {
    cancelPlaceCueMode();
    return;
  }
  if (state.placeLoopMode) cancelPlaceLoopMode();
  if (state.gridAlignMode) exitGridAlignMode({ restoreView: false });
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  state.placeCueMode = true;
  state.placeCuePreview = null;
  syncPlaceCueUi();
  drawWaveform();
  setStatus("Click the wave to place a cue on the 1. Shift = free. Esc cancels.");
}

function cancelPlaceCueMode() {
  if (!state.placeCueMode) return;
  state.placeCueMode = false;
  state.placeCuePreview = null;
  syncPlaceCueUi();
  drawWaveform();
}

function updatePlaceCuePreview(clientX, free = false) {
  if (!state.placeCueMode) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(clientX, rect, duration);
  state.placeCuePreview = snapCueDragTime(t, { free: Boolean(free) });
  drawWaveform();
}

async function placeCueAtTime(rawTime, { free = false, alreadySnapped = false, forPath = null } = {}) {
  const track = currentTrack();
  if (!track) return;
  if (forPath && track.path !== forPath) {
    loudNotice("Cue not placed: the open song changed. Nothing was changed.", "error");
    return;
  }
  const path = track.path;
  const gen = state.trackGen;
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  let pos = alreadySnapped
    ? Math.max(0, Number(rawTime) || 0)
    : snapCueDragTime(Number(rawTime) || 0, { free });
  if (duration > 0) pos = Math.min(pos, Math.max(0, duration - 0.05));
  const existing = existingCueNear(pos);
  if (existing) {
    jumpToCue(Number(existing.pos) || pos, existing);
    return;
  }
  const pendKey = `cue|${path}|${pos.toFixed(2)}`; // this song + this spot only; other songs/spots are never blocked
  if (placePending.has(pendKey)) return;
  {
    // Rule: cues cannot fall inside other loops.
    const bpmC = trackBpm(track);
    const inside = (track.cues?.points || []).find((p) => {
      if (pointKind(p) !== "loop" || !(bpmC > 0)) return false;
      const st = Number(p.pos) || 0;
      return pos > st + 0.02 && pos < st + loopDurationSeconds(p, bpmC) - 0.02;
    });
    if (inside) {
      loudNotice(
        `Cues cannot fall inside other loops — ${fmtTime(pos)} is inside “${inside.name || "Loop"}” (${fmtTime(inside.pos)}–${fmtTime((Number(inside.pos) || 0) + loopDurationSeconds(inside, bpmC))}). Place it before or after the loop.`,
        "warn"
      );
      return;
    }
  }

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Adding a cue may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Place anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then place the cue.", "error");
      return;
    }
  }
  if (currentTrack()?.path !== path || gen !== state.trackGen) {
    loudNotice("Cue not placed: the open song changed while you were answering. Nothing was changed.", "error");
    return;
  }

  placePending.add(pendKey);
  const hist = historyBegin(path);
  try {
    setStatus(`Placing cue at ${fmtTime(pos)}…`);
    const data = await enqueueTrackEdit(path, () =>
      api("/api/add-cue", {
      method: "POST",
      body: JSON.stringify({
        path,
        pos,
        color: "green",
        allow_vdj_running: Boolean(allowRunning),
      }),
    })
    );
    const r = data.result || {};
    if (r.cues) {
      applyCueSummaryToTrack(path, r.cues);
    }

    historyCommit(hist, `new cue at ${fmtTime(pos)}`);
    if (!stillOnTrack(path, gen)) {
      setStatus(`Cue placed on ${track.name} (switched tracks)`, "success");
      return;
    }

    const placed =
      (currentTrack()?.cues?.points || []).find(
        (p) =>
          pointKind(p) === "cue" && Math.abs(Number(p.pos) - pos) < 0.03
      ) || { name: r.change?.name, pos, kind: "cue", num: r.change?.num };

    state.activeCueKey = cueKey(placed);
    renderCues();
    drawWaveform();
    setStatus(
      `Placed “${placed.name || "Cue"}” at ${fmtTime(pos)}` +
        (free ? " (free)" : " (on the 1)"),
      "success"
    );
    jumpToCue(pos, placed);
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    placePending.delete(pendKey);
  }
}

function placeLoopColor() {
  const id = state.placeLoopColor;
  return CUE_COLOR_OPTIONS.some((c) => c.id === id) ? id : "green";
}

function renderPlaceLoopSwatches() {
  const host = $("placeLoopSwatches");
  if (!host) return;
  const cur = placeLoopColor();
  host.replaceChildren();
  for (const c of CUE_COLOR_OPTIONS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `place-loop-swatch color-${c.id}`;
    b.dataset.color = c.id;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", c.id === cur ? "true" : "false");
    b.setAttribute("aria-label", `${c.name} — ${c.meaning}`);
    b.title = `${c.name} — ${c.meaning}`;
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      state.placeLoopColor = c.id;
      renderPlaceLoopSwatches();
    });
    host.appendChild(b);
  }
}

function bindPlaceLoopFields() {
  const input = $("placeLoopName");
  if (input && !input.dataset.bound) {
    input.dataset.bound = "1";
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        cancelPlaceLoopMode();
      }
      e.stopPropagation(); // typing a name must never trigger transport hotkeys
    });
  }
  renderPlaceLoopSwatches();
}

function syncPlaceLoopUi() {
  const btn = $("placeLoopBtn");
  if (btn) {
    btn.classList.toggle("active", state.placeLoopMode);
    btn.setAttribute("aria-pressed", state.placeLoopMode ? "true" : "false");
    btn.textContent = state.placeLoopMode ? "Placing loop…" : "Place loop";
  }
  const bar = $("placeLoopBar");
  if (bar) {
    if (state.placeLoopMode) {
      bindPlaceLoopFields();
      bar.hidden = false;
      bar.removeAttribute("hidden");
    } else {
      bar.hidden = true;
      bar.setAttribute("hidden", "");
    }
  }
  const wrap = $("waveformWrap");
  if (wrap) wrap.classList.toggle("place-loop-mode", state.placeLoopMode);
  if (!state.placeLoopMode) {
    state.placeLoopPreview = null;
    wrap?.classList.remove("place-loop-mode");
  }
}

function togglePlaceLoopMode() {
  if (state.placeLoopMode) {
    cancelPlaceLoopMode();
    return;
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.gridAlignMode) exitGridAlignMode({ restoreView: false });
  const track = currentTrack();
  if (!track) {
    setStatus("Select a track first.", "error");
    return;
  }
  state.placeLoopMode = true;
  state.placeLoopPreview = null;
  syncPlaceLoopUi();
  drawWaveform();
  setStatus("Click the wave to place an 8-beat loop on the 1. Shift = free. Esc cancels.");
}

function cancelPlaceLoopMode() {
  if (!state.placeLoopMode) return;
  state.placeLoopMode = false;
  state.placeLoopPreview = null;
  syncPlaceLoopUi();
  drawWaveform();
}

function updatePlaceLoopPreview(clientX, free = false) {
  if (!state.placeLoopMode) return;
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track) return;
  const duration = waveformDuration(track, audio);
  if (!duration) return;
  const rect = wrap.getBoundingClientRect();
  const t = clientXToTime(clientX, rect, duration);
  state.placeLoopPreview = snapPhraseTime(t, { free: Boolean(free) });
  drawWaveform();
}

async function placeLoopAtTime(
  rawTime,
  { free = false, alreadySnapped = false, name: nameOverride = null, color: colorOverride = null, beats: beatsOverride = null, forPath = null } = {}
) {
  const track = currentTrack();
  if (!track) return;
  if (forPath && track.path !== forPath) {
    loudNotice("Loop not placed: the open song changed. Nothing was changed.", "error");
    return;
  }
  const path = track.path;
  const gen = state.trackGen;
  const audio = $("audio");
  const duration = waveformDuration(track, audio) || trackDuration(track, audio);
  let pos = alreadySnapped
    ? Math.max(0, Number(rawTime) || 0)
    : snapPhraseTime(Number(rawTime) || 0, { free }); // loops start on the 16-beat phrase [1]
  if (duration > 0) pos = Math.min(pos, Math.max(0, duration - 0.05));
  const existing = existingLoopNear(pos);
  if (existing) {
    jumpToCue(Number(existing.pos) || pos, existing);
    return;
  }
  const pendKey = `loop|${path}|${pos.toFixed(2)}`;
  if (placePending.has(pendKey)) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Adding a loop may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Place anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      loudNotice("Close VirtualDJ, then place the loop.", "error");
      return;
    }
  }

  if (currentTrack()?.path !== path || gen !== state.trackGen) {
    loudNotice("Loop not placed: the open song changed while you were answering. Nothing was changed.", "error");
    return;
  }
  placePending.add(pendKey);
  // One click = one loop: place mode ends NOW, for THIS song's click. (It used to end in `finally`, when the save came
  // back - so a slow save of an earlier song's loop switched off the place mode the user had just turned on for the
  // next song, and the following waveform click only seeked: the "lost" loop of the flaky bleed test.)
  if (state.placeLoopMode) cancelPlaceLoopMode();
  let temp = null;
  const hist = historyBegin(path);
  try {
    const wantName = nameOverride != null ? String(nameOverride).trim() : ($("placeLoopName")?.value || "").trim();
    const wantColor = colorOverride ? sanitizeColorName(colorOverride) : placeLoopColor();
    const wantBeats = Number(beatsOverride) > 0 ? Number(beatsOverride) : 8;
    const fit = clampLoopBeatsToSongEnd(track, { pos, size: 0 }, wantBeats); // never past the end of the song
    const beats = fit.clamped ? fit.beats : wantBeats;
    // Optimistic: the loop is on screen NOW; the save follows in the background.
    temp = {
      kind: "loop",
      name: wantName || "Loop",
      pos,
      num: "-1",
      slot: null,
      size: String(beats),
      color_name: wantColor,
      color: CUE_COLOR_ARGB[wantColor] != null ? CUE_COLOR_ARGB[wantColor] : null,
    };
    addPointToModel(path, temp);
    state.activeLoopKey = cueKey(temp);
    state.activeCueKey = cueKey(temp);
    const nameBox = $("placeLoopName");
    if (nameBox && nameOverride == null) nameBox.value = ""; // the next loop starts unnamed; the color choice sticks
    if (stillOnTrack(path, gen)) {
      renderCues();
      drawWaveform();
    }
    quickConfirm(`Loop ${fmtTime(pos)} · ${beats}b added — saving…`);
    setStatus(`Placing ${beats}-beat loop at ${fmtTime(pos)}…`);
    const savePromise = enqueueTrackEdit(path, () =>
      api("/api/add-loop", {
        method: "POST",
        body: JSON.stringify({
          path,
          pos,
          name: wantName || null,
          color: wantColor,
          beats,
          allow_vdj_running: Boolean(allowRunning),
        }),
      })
    );
    // A cue under the new loop would be buried: move it to the phrase [1] just before the loop.
    const movedNote = await moveCuesOutOfLoop(path, gen, pos, beats, allowRunning);
    const data = await savePromise;
    const r = data.result || {};
    // If a buried cue was moved after this save, the loop response is already out of date for it.
    if (r.cues && !movedNote) {
      applyCueSummaryToTrack(path, r.cues);
    }
    historyCommit(hist, `new loop at ${fmtTime(pos)}`);

    if (!stillOnTrack(path, gen)) {
      setStatus(`Loop placed on ${track.name} (switched tracks)`, "success");
      return;
    }

    const placed =
      (currentTrack()?.cues?.points || []).find(
        (p) => pointKind(p) === "loop" && Math.abs(Number(p.pos) - pos) < 0.03
      ) || {
        name: r.change?.name,
        pos,
        kind: "loop",
        size: r.change?.beats,
        slot: r.change?.slot,
      };

    state.activeLoopKey = cueKey(placed);
    state.activeCueKey = cueKey(placed);
    renderCues();
    drawWaveform();
    setStatus(
      `Placed loop “${placed.name || "Loop"}” at ${fmtTime(pos)} · ${beats}b` +
        (free ? " (free)" : " (on the phrase [1])") +
        (movedNote ? ` · ${movedNote}` : ""),
      "success"
    );
    jumpToCue(pos, placed);
  } catch (err) {
    if (temp) {
      try {
        removePointFromModel(path, cueKey(temp));
        if (stillOnTrack(path, gen)) {
          renderCues();
          drawWaveform();
        }
      } catch {
        /* ignore */
      }
    }
    loudNotice(`Loop not placed: ${err.message}`, "error");
  } finally {
    placePending.delete(pendKey);
  }
}

/* ===== Duplicate loop: the copy goes one phrase [1] after the ORIGINAL's end ===== */
/** Returns {start} for the first phrase [1] at/after the loop's end, or {blocked: reason}. Never reuses another
    marker's time, never overlaps another loop or buries a cue, never leaves the song. Pure: reads the model only. */
function planLoopDuplicate(track, point) {
  const bpm = trackBpm(track);
  const len = loopDurationSeconds(point, bpm);
  const period = phrasePeriodSeconds(track);
  if (!(len > 0)) return { blocked: "this loop has no known length" };
  if (!(period > 0)) return { blocked: "this song has no beatgrid, so there is no phrase [1] to snap to" };
  const anchor = gridAnchorSeconds(track);
  const pos = Number(point.pos) || 0;
  const end = pos + len;
  const k = Math.ceil((end - anchor) / period - 1e-6);
  const start = anchor + k * period;
  const stop = start + len;
  const dur = Number(track?.cues?.song_length) || 0;
  if (dur > 0 && stop > dur - 0.05) return { blocked: "the copy would run past the end of the song" };
  const key = cueKey(point);
  for (const q of track.cues?.points || []) {
    if (cueKey(q) === key) continue;
    const qs = Number(q.pos) || 0;
    if (Math.abs(qs - start) < 0.03) return { blocked: `another marker (“${q.name || pointKind(q)}”) already sits at ${fmtTime(start)}` };
    if (pointKind(q) === "loop") {
      const qe = qs + loopDurationSeconds(q, bpm);
      if (start < qe - 0.01 && stop > qs + 0.01) return { blocked: `it would overlap the loop “${q.name || "loop"}” at ${fmtTime(qs)}` };
    } else if (qs > start + 0.02 && qs < stop - 0.02) {
      return { blocked: `the cue “${q.name || "cue"}” at ${fmtTime(qs)} would end up inside the copy` };
    }
  }
  return { start, beats: Number(point.size) || len * (bpm / 60) };
}

async function duplicateLoopPoint(point) {
  const track = currentTrack();
  const cap = captureSong();
  if (!track || !point || !guardSong(cap, "Duplicate loop")) return;
  const live = resolveLivePoint(track.path, point);
  const plan = planLoopDuplicate(track, live);
  if (plan.blocked) {
    // No room: ask the user to click the waveform instead (never guess a place).
    loudNotice(`Can't place the copy next to the original: ${plan.blocked}. Click the waveform where the copy should go.`, "warn");
    if (!state.placeLoopMode) togglePlaceLoopMode();
    const nameBox = $("placeLoopName");
    if (nameBox) nameBox.value = live.name || "";
    return;
  }
  const baseName = String(live.name || "").trim();
  const name = baseName ? uniqueRenameForKind(track, { kind: "loop", pos: -999, num: "-1" }, baseName) : "";
  await placeLoopAtTime(plan.start, {
    alreadySnapped: true,
    name,
    color: live.color_name,
    beats: plan.beats,
    forPath: cap.path,
  });
}

/** The phrase [1] strictly before `before`, that has no cue on it (or null). */
function phraseBefore(track, before) {
  const period = phrasePeriodSeconds(track) || barPeriodSeconds(track);
  if (!(period > 0)) return null;
  const anchor = gridAnchorSeconds(track);
  let k = Math.ceil((before - anchor) / period - 1e-6) - 1;
  for (let tries = 0; tries < 16 && k >= -64; tries++, k--) {
    const t = anchor + k * period;
    if (t < 0) return 0 === Math.round(t) && !existingCueNear(0) ? 0 : null;
    if (t < before - 0.02 && !existingCueNear(t)) return t;
  }
  return null;
}

/** Cues that would sit INSIDE a loop get moved to the phrase [1] just before it (rule: cues never
    fall inside loops). Returns a short note for the status line, or "". */
async function moveCuesOutOfLoop(path, gen, loopPos, beats, allowRunning) {
  const track = (state.tracks || []).find((x) => x.path === path);
  if (!track) return "";
  const bpm = trackBpm(track);
  const end = loopPos + (bpm > 0 ? (beats * 60) / bpm : 0);
  const buried = (track.cues?.points || []).filter(
    (p) => pointKind(p) === "cue" && (Number(p.pos) || 0) >= loopPos - 0.02 && (Number(p.pos) || 0) < end - 0.02
  );
  const notes = [];
  for (const cue of buried) {
    const dest = phraseBefore(track, loopPos);
    const label = cue.name || "Cue";
    if (dest == null) {
      loudNotice(`Cue “${label}” at ${fmtTime(cue.pos)} is inside the new loop and could not be moved — move it by hand.`, "error");
      continue;
    }
    const from = Number(cue.pos) || 0;
    // the real move (optimistic + queued) - same code path as dragging the cue
    await commitCueMove({ ...cue }, from, dest, { allowRunning });
    notes.push(`cue “${label}” moved ${fmtTime(from)} → ${fmtTime(dest)} (phrase [1] before the loop)`);
  }
  if (notes.length) quickConfirm(notes.join("; "));
  return notes.join("; ");
}

function onWaveformWheel(e) {
  const wrap = $("waveformWrap");
  const track = currentTrack();
  const audio = $("audio");
  if (!wrap || !track || !state.waveform?.peaks?.length) return;
  if (state.loopDrag) {
    e.preventDefault(); // the view must not move under a drag
    return;
  }

  const duration = waveformDuration(track, audio);
  if (!duration) return;

  e.preventDefault();
  e.stopPropagation();

  const rect = wrap.getBoundingClientRect();
  const mouseTime = clientXToTime(e.clientX, rect, duration);
  const mouseRatio = Math.min(
    1,
    Math.max(0, (e.clientX - rect.left - WAVE_PAD_X) / Math.max(1, rect.width - WAVE_PAD_X * 2))
  );

  // Shift + scroll → pan when zoomed; plain scroll → zoom on cursor
  if (e.shiftKey && state.waveZoom > 1.01) {
    const view = waveViewWindow(duration);
    const pan = (e.deltaY > 0 ? 1 : -1) * view.span * 0.12;
    state.waveOffset = Math.max(0, Math.min(duration - view.span, view.start + pan));
    state.waveViewPinned = true;
    drawWaveform();
    return;
  }

  const factor = e.deltaY < 0 ? 1.18 : 1 / 1.18;
  const nextZoom = clampWaveZoom((state.waveZoom || 1) * factor);
  if (Math.abs(nextZoom - state.waveZoom) < 0.001) return;

  state.waveZoom = nextZoom;
  const span = duration / nextZoom;
  // Keep the time under the cursor fixed while zooming.
  let start = mouseTime - mouseRatio * span;
  start = Math.max(0, Math.min(start, Math.max(0, duration - span)));
  state.waveOffset = start;
  // Keep the cursor-centered slice; follow resumes once the needle is in view.
  state.waveViewPinned = true;
  drawWaveform();
}

/* The marker a list row stands for, found by the row's key in the live model at click time. */
function pointForRowEl(el, idx) {
  const track = currentTrack();
  const key = el?.closest?.(".cue-row")?.dataset?.key;
  return (key && track && modelPoint(track.path, key)) || (track?.cues?.points || [])[idx] || null;
}

function pointKind(point) {
  const raw = String(point?.kind || point?.type || "").toLowerCase();
  return raw === "loop" ? "loop" : "cue";
}

function pointMatchesCueFilter(point, filter = state.cueListFilter) {
  const f = filter || "all";
  if (f === "all") return true;
  const kind = pointKind(point);
  if (f === "cues") return kind === "cue";
  if (f === "loops") return kind === "loop";
  return true;
}

function filteredCuePoints(points) {
  return (points || []).filter((p) => pointMatchesCueFilter(p));
}

function syncCueKindFilterUi() {
  const root = $("cueKindFilter");
  if (!root) return;
  root.querySelectorAll("button[data-cue-filter]").forEach((btn) => {
    const value = btn.getAttribute("data-cue-filter") || "all";
    const on = value === state.cueListFilter;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  const panel = $("cuesPanel");
  if (panel) panel.dataset.cueFilter = state.cueListFilter;
}

function setCueListFilter(filter) {
  const raw = String(filter || "all").toLowerCase();
  const next = raw === "cues" || raw === "loops" || raw === "all" ? raw : "all";
  state.cueListFilter = next;
  syncCueKindFilterUi();
  // Always rebuild list + timeline + waveform so isolation is visible.
  renderCues();
  drawWaveform();
}

/* R-77: the cue panel must always show the cues of the loaded song. A title with an empty panel is never an
   acceptable state: it is either "loading", "could not read (Retry)", or the real list. */
const cuesVerify = new Map(); // path -> { status: "loading" | "failed" | "done", message }
function cuePanelNote(kind, text, retryLabel, onRetry) {
  const list = $("cueList");
  if (!list) return;
  list.innerHTML = "";
  const box = document.createElement("div");
  box.className = `empty cue-panel-note cue-panel-${kind}`;
  box.setAttribute("role", "status");
  const span = document.createElement("span");
  span.textContent = text;
  box.appendChild(span);
  if (retryLabel && onRetry) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn primary cue-panel-retry";
    b.textContent = retryLabel;
    b.addEventListener("click", onRetry);
    box.appendChild(b);
  }
  list.appendChild(box);
}
function trackNeedsCueCheck(track) {
  if (!track) return false;
  const c = track.cues;
  if (!c) return true;
  return !(c.points && c.points.length) && c.in_database === false;
}
async function verifyCuesFor(track) {
  const path = track.path;
  const gen = state.trackGen;
  cuesVerify.set(path, { status: "loading" });
  try {
    const data = await api(`/api/cues?path=${encodeURIComponent(path)}`, { timeoutMs: 30000 });
    cuesVerify.set(path, { status: "done" });
    const live = (state.tracks || []).find((t) => t.path === path);
    if (live && data && (data.points || []).length && !(editPending.get(path) > 0)) {
      applyCueSummaryToTrack(path, data);
    }
    if (currentTrack()?.path === path && state.trackGen === gen) {
      repaintAfterMarkerChange(path);
      renderTrackList();
    } else if (currentTrack()?.path === path) {
      renderCues();
    }
  } catch (err) {
    cuesVerify.set(path, { status: "failed", message: (err && err.message) || "network error" });
    if (currentTrack()?.path === path) renderCues();
  }
}
function retryCuePanel() {
  const t = currentTrack();
  if (!t) return;
  cuesVerify.delete(t.path);
  renderCues();
}
function renderCues() {
  try {
    renderCuesInner();
  } catch (err) {
    try {
      console.error("renderCues failed", err);
    } catch {
      /* ignore */
    }
    state.cuesPath = null; // the panel is NOT showing this song's cues
    cuePanelNote("error", `The cue list for this song could not be drawn (${(err && err.message) || "error"}).`, "Try again", () => retryCuePanel());
  }
}
function renderCuesInner() {
  if (colorMenuOpen() || document.querySelector("#cueList .cue-name-input")) {
    state.renderCuesDeferred = true; // a re-render would yank the open menu; it runs when the menu closes
    return;
  }
  const track = currentTrack();
  state.cuesPath = track ? track.path : null;
  const list = $("cueList");
  const timeline = $("cueTimeline");
  const countBadge = $("cuesCountBadge");
  const subtitle = $("cuesSubtitle");
  const filterHint = $("cueKindFilterHint");
  if (!list || !timeline) return;

  syncCueKindFilterUi();

  // Rebuild track + playhead; markers added below.
  timeline.innerHTML = `
    <div class="cue-timeline-track"></div>
    <div class="cue-timeline-playhead" id="cuePlayhead"></div>
  `;

  if (!track) {
    list.innerHTML = `<div class="empty">No track selected.</div>`;
    countBadge.textContent = "0";
    subtitle.textContent = "Scroll to zoom · click to jump";
    if (filterHint) filterHint.textContent = "";
    return;
  }

  const points = track.cues?.points || [];
  const cueN = points.filter((p) => pointKind(p) === "cue").length;
  const loopN = points.filter((p) => pointKind(p) === "loop").length;
  const visible = points
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => pointMatchesCueFilter(p));

  countBadge.textContent =
    state.cueListFilter === "all"
      ? String(points.length)
      : `${visible.length}/${points.length}`;
  countBadge.className = points.length ? "badge ok" : "badge neutral";
  const filterLabel =
    state.cueListFilter === "cues"
      ? "cues only"
      : state.cueListFilter === "loops"
        ? "loops only"
        : "all markers";
  subtitle.textContent = points.length
    ? `Showing ${filterLabel} · scroll zoom · 1–9 jump · ✕ delete`
    : "No cue/loop markers · scroll still zooms the wave";
  if (filterHint) {
    filterHint.textContent = points.length
      ? `Showing ${visible.length} · ${cueN} cues · ${loopN} loops total`
      : "";
  }

  // Always clear list first so a previous tab cannot leave stale rows.
  list.innerHTML = "";

  if (!points.length) {
    if (trackNeedsCueCheck(track)) {
      const v = cuesVerify.get(track.path);
      if (!v) {
        verifyCuesFor(track);
        cuePanelNote("loading", `Loading cues for “${trackDisplayTitle(track)}”…`);
        state.cuesPath = null;
        updatePlayhead();
        return;
      }
      if (v.status === "loading") {
        cuePanelNote("loading", `Loading cues for “${trackDisplayTitle(track)}”…`);
        state.cuesPath = null;
        updatePlayhead();
        return;
      }
      if (v.status === "failed") {
        cuePanelNote("error", `The cues for “${trackDisplayTitle(track)}” could not be read (${v.message}).`, "Retry", () => retryCuePanel());
        state.cuesPath = null;
        updatePlayhead();
        return;
      }
      list.innerHTML = `<div class="empty">No cues for this track - it is not in VirtualDJ's database yet.</div>`;
      updatePlayhead();
      return;
    }
    list.innerHTML = `<div class="empty">No cues for this track.</div>`;
    updatePlayhead();
    return;
  }

  if (!visible.length) {
    const emptyMsg =
      state.cueListFilter === "loops"
        ? "No loops on this track."
        : state.cueListFilter === "cues"
          ? "No cue points on this track."
          : "No markers match this filter.";
    list.innerHTML = `<div class="empty">${emptyMsg}</div>`;
    updatePlayhead();
    // Still draw empty timeline (already cleared above).
    return;
  }

  const duration = trackDuration(track, $("audio"));
  const effectiveDuration =
    duration ||
    Math.max(...points.map((p) => Number(p.pos) || 0), 1) * 1.05;

  // Timeline markers only for the active tab.
  timeline.append(
    ...visible.map(({ p }) => {
      const kind = pointKind(p);
      const pct = Math.min(99.5, Math.max(0.5, ((Number(p.pos) || 0) / effectiveDuration) * 100));
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `cue-marker ${kind} color-${p.color_name || "unknown"}`;
      btn.style.left = `calc(10px + (100% - 20px) * ${pct / 100})`;
      btn.title = `${p.name} · ${fmtTime(p.pos)}${kind === "loop" ? " (loop)" : ""}`;
      btn.dataset.pos = String(p.pos);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        jumpToCue(p.pos, p, e);
      });
      return btn;
    })
  );

  list.innerHTML = visible
    .map(({ p, i }, visIdx) => {
      const kind = pointKind(p);
      const hotkey = visIdx < 9 ? `<span class="kbd">${visIdx + 1}</span>` : "";
      const key = cueKey(p);
      const isLooping =
        state.loopPlaybackOn && state.activeLoopKey === key && kind === "loop";
      const sizeBeats = Number(p.size);
      const canHalve = kind === "loop" && Number.isFinite(sizeBeats) && sizeBeats > 1.01;
      const canDouble =
        kind === "loop" && Number.isFinite(sizeBeats) && sizeBeats < 255;
      const kindLabel =
        kind === "loop"
          ? `loop${p.size ? ` ${p.size}b` : ""}${isLooping ? " · ON" : ""}`
          : `cue ${p.num || ""}`.trim();
      const loopScaleBtns =
        kind === "loop"
          ? `
          <button
            type="button"
            class="btn ghost cue-loop-scale-btn"
            data-index="${i}"
            data-factor="0.5"
            ${canHalve ? "" : "disabled"}
            title="Halve loop length in VirtualDJ and audition"
            aria-label="Halve loop ${escapeHtml(p.name || "")}"
          >½</button>
          <button
            type="button"
            class="btn ghost cue-loop-scale-btn"
            data-index="${i}"
            data-factor="2"
            ${canDouble ? "" : "disabled"}
            title="Double loop length in VirtualDJ and audition"
            aria-label="Double loop ${escapeHtml(p.name || "")}"
          >×2</button>
          <button
            type="button"
            class="btn ghost cue-loop-dup-btn"
            data-index="${i}"
            title="Duplicate: put a copy one phrase [1] after this loop's end"
            aria-label="Duplicate loop ${escapeHtml(p.name || "")}"
          >Duplicate</button>`
          : "";
      const currentColor = sanitizeColorName(p.color_name);
      const colorMeaning = MusicSorterTransport.cueColorMeaning(currentColor);
      const colorOpts = CUE_COLOR_OPTIONS.map(
        (c) =>
          `<option value="${c.id}" ${
            currentColor === c.id ? "selected" : ""
          }>${c.label}</option>`
      ).join("");
      const unknownOpt =
        currentColor && !CUE_COLOR_OPTIONS.some((c) => c.id === currentColor)
          ? `<option value="${escapeHtml(currentColor)}" selected>${escapeHtml(
              currentColor
            )}</option>`
          : "";
      return `
        <div class="cue-row ${
          state.activeCueKey === key ? "active" : ""
        } ${isLooping ? "looping" : ""}" data-key="${escapeHtml(key)}" data-pos="${p.pos}" data-index="${i}" data-kind="${kind}">
          <button type="button" class="cue-row-main" data-index="${i}" title="Jump to marker">
            <span class="cue-dot ${kind} color-${sanitizeColorName(p.color_name)}"></span>
            <span class="cue-time">${fmtTime(p.pos)}</span>
            <span
              class="cue-name"
              data-index="${i}"
              role="button"
              tabindex="0"
              title="Click to rename"
            >${escapeHtml(p.name || (kind === "loop" ? "Loop" : "Cue"))}</span>
            <span class="cue-kind">${escapeHtml(kindLabel)} ${hotkey}</span>
            <span class="cue-color-meaning color-${currentColor}" title="${escapeHtml(
              colorMeaning
            )}">${escapeHtml(colorLabel(currentColor))} · ${escapeHtml(colorMeaning)}</span>
          </button>
          <div class="cue-row-actions">
            <div class="cue-color-pick" data-index="${i}">
              <button
                type="button"
                class="cue-color-btn"
                data-index="${i}"
                aria-haspopup="listbox"
                aria-expanded="false"
                title="Change marker color in VirtualDJ · ${escapeHtml(colorLabel(currentColor))} — ${escapeHtml(colorMeaning)}"
                aria-label="Color for ${escapeHtml(p.name || kind)}: ${escapeHtml(colorLabel(currentColor))}"
              ><span class="cue-color-chip color-${escapeHtml(currentColor)}"></span><span class="cue-color-caret" aria-hidden="true">▾</span></button>
            </div>
            ${loopScaleBtns}
            <button
              type="button"
              class="btn ghost danger cue-delete-btn"
              data-index="${i}"
              title="Delete this ${kind === "loop" ? "loop" : "cue"} from VirtualDJ"
              aria-label="Delete ${escapeHtml(p.name || kind)}"
            >✕</button>
          </div>
        </div>`;
    })
    .join("");

  list.querySelectorAll(".cue-row-main").forEach((row) => {
    row.addEventListener("click", (e) => {
      // Name text has its own rename handler.
      if (e.target.closest(".cue-name")) return;
      const idx = Number(row.dataset.index);
      const point = pointForRowEl(row, idx);
      jumpToCue(point?.pos, point, e);
    });
  });
  list.querySelectorAll(".cue-name").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      const idx = Number(el.dataset.index);
      const point = pointForRowEl(el, idx);
      if (point) beginRenamePoi(point, el);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        const idx = Number(el.dataset.index);
        const point = pointForRowEl(el, idx);
        if (point) beginRenamePoi(point, el);
      }
    });
  });
  list.querySelectorAll(".cue-color-btn").forEach((btn) => {
    // Not a native <select>: its popup lives outside the page, closes when the list re-renders and
    // maps clicks by index. This picker applies exactly the swatch that was clicked, by marker key.
    btn.addEventListener("pointerdown", (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const row = btn.closest(".cue-row");
      openCueColorMenu(btn, row?.dataset.key || "", Number(btn.dataset.index));
    });
  });
  list.querySelectorAll(".cue-loop-scale-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = Number(btn.dataset.index);
      const factor = Number(btn.dataset.factor);
      const point = pointForRowEl(btn, idx);
      if (point && (factor === 0.5 || factor === 2)) scaleLoopPoint(point, factor);
    });
  });
  list.querySelectorAll(".cue-loop-dup-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const point = pointForRowEl(btn, Number(btn.dataset.index));
      if (point) duplicateLoopPoint(point);
    });
  });
  list.querySelectorAll(".cue-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = Number(btn.dataset.index);
      const point = pointForRowEl(btn, idx);
      if (point) deleteCuePoint(point);
    });
  });

  updatePlayhead();
}

/* ===== Optimistic quick edits: patch only the affected DOM + model, save in background ===== */
const editChains = new Map(); // track path -> promise tail: saves for one track run in order

const editPending = new Map(); // track path -> number of queued/running saves

/* Per-song local-edit bookkeeping (R-45 / R-59). A song-list reload is a snapshot taken at one moment;
   it must never overwrite what the user did to a song after (or while) that snapshot was fetched.
   markerRev: bumped on every local change to a song's markers (optimistic patch or saved summary).
   needsReconcile: songs whose server summary was withheld because other saves were still queued.
   placePending: "kind|path|pos" placements in flight: scoped per song AND spot, never one global flag. */
const markerRev = new Map();
const needsReconcile = new Set();
const placePending = new Set();
function bumpMarkerRev(path) {
  if (path) markerRev.set(path, (markerRev.get(path) || 0) + 1);
}
/* Edits whose save FAILED stay on screen (and in the NOT-saved list). Whenever the stored markers of that song
   are (re)applied - list reload or a later save's summary - the retained edit is laid back over them. */
function overlayFailedEdits(t) {
  const fails = (state.failedEdits || []).filter((f) => f && f.path === t?.path && f.bodyText);
  if (!fails.length || !Array.isArray(t.cues?.points)) return t;
  const points = t.cues.points.map((p) => ({ ...p }));
  for (const f of fails) {
    let b;
    try {
      b = JSON.parse(f.bodyText);
    } catch {
      continue;
    }
    const apiPath = String(f.apiPath || "");
    const kind = b.kind || (/scale-loop$/.test(apiPath) ? "loop" : null);
    const hit = points.find(
      (p) =>
        (!kind || pointKind(p) === kind) &&
        Math.abs((Number(p.pos) || 0) - (Number(b.pos) || 0)) < 0.02 &&
        (b.num == null || String(p.num) === String(b.num)) &&
        (b.slot == null || String(p.slot ?? "") === String(b.slot))
    );
    if (!hit) continue;
    if (/set-cue-color$/.test(apiPath) && b.color) hit.color_name = sanitizeColorName(b.color);
    else if (/move-poi$/.test(apiPath) && Number.isFinite(Number(b.new_pos))) hit.pos = Number(b.new_pos);
    else if (/scale-loop$/.test(apiPath) && Number(hit.size) > 0 && Number(b.factor) > 0) hit.size = String(Number(hit.size) * Number(b.factor));
  }
  points.sort((x, y) => (Number(x.pos) || 0) - (Number(y.pos) || 0));
  return { ...t, cues: { ...t.cues, points } };
}

/* ONE source of truth for how many cues / loops a song has: its marker list. The header chips, the
   left song list, the Cue review panel and the cue tabs all read this; markersChanged() re-syncs the
   stored numbers and repaints every surface after any change to the markers. */
function markerCountsOf(track) {
  const pts = track?.cues?.points;
  if (!Array.isArray(pts)) {
    return { cues: Number(track?.cues?.cue_count) || 0, loops: Number(track?.cues?.loop_count) || 0 };
  }
  return {
    cues: pts.filter((p) => pointKind(p) === "cue").length,
    loops: pts.filter((p) => pointKind(p) === "loop").length,
  };
}

let markersSurfacesQueued = false;

/* ===== Undo / Redo of marker edits (per song, 20 deep). Every undo/redo goes through the normal save path:
   the screen changes at once, then the same /api calls an ordinary edit makes are queued for the song. ===== */
const HISTORY_LIMIT = 20;
const markerHistory = new Map(); // path -> { undo: [], redo: [] }
function historyOf(path) {
  let h = markerHistory.get(path);
  if (!h) {
    h = { undo: [], redo: [] };
    markerHistory.set(path, h);
  }
  return h;
}
function snapshotMarkers(path) {
  const t = (state.tracks || []).find((x) => x.path === path);
  return ((t && t.cues && t.cues.points) || []).map((p) => ({ ...p }));
}
function markerIdentity(p, used) {
  const kind = pointKind(p);
  let id;
  if (kind === "loop" && p.slot != null && String(p.slot) !== "" && String(p.slot) !== "-1") id = `loop|slot${p.slot}`;
  else if (kind === "cue" && p.num != null && String(p.num) !== "-1") id = `cue|num${p.num}`;
  else id = `${kind}|${p.name || ""}`;
  let n = 0;
  let key = id;
  while (used.has(key)) key = `${id}#${(n += 1)}`;
  used.add(key);
  return key;
}
function markerFieldsDiffer(a, b) {
  const out = [];
  if (Math.abs((Number(a.pos) || 0) - (Number(b.pos) || 0)) > 0.015) out.push("pos");
  if (String(a.name || "") !== String(b.name || "")) out.push("name");
  if (sanitizeColorName(a.color_name) !== sanitizeColorName(b.color_name)) out.push("color");
  if (pointKind(a) === "loop" && Math.abs((Number(a.size) || 0) - (Number(b.size) || 0)) > 0.01) out.push("size");
  return out;
}
function diffMarkers(cur, target) {
  const usedC = new Set();
  const usedT = new Set();
  const curMap = new Map(cur.map((p) => [markerIdentity(p, usedC), p]));
  const tgtMap = new Map(target.map((p) => [markerIdentity(p, usedT), p]));
  const del = [];
  const add = [];
  const chg = [];
  for (const [k, p] of curMap) if (!tgtMap.has(k)) del.push(p);
  for (const [k, p] of tgtMap) {
    if (!curMap.has(k)) add.push(p);
    else {
      const fields = markerFieldsDiffer(curMap.get(k), p);
      if (fields.length) chg.push({ cur: curMap.get(k), tgt: p, fields });
    }
  }
  // A "delete + add" of the same kind at the same time and look is not a change at all (num re-assigned by the file).
  for (let i = del.length - 1; i >= 0; i -= 1) {
    const d = del[i];
    const j = add.findIndex((a) => pointKind(a) === pointKind(d) && markerFieldsDiffer(a, d).length === 0);
    if (j >= 0) {
      del.splice(i, 1);
      add.splice(j, 1);
    }
  }
  return { del, add, chg };
}
function historyBegin(path) {
  if (!path) return null;
  return { path, before: snapshotMarkers(path), gen: state.trackGen };
}
function historyCommit(h, label, meta = null) {
  if (!h || !h.path) return;
  const after = snapshotMarkers(h.path);
  const d = diffMarkers(h.before, after);
  if (!d.del.length && !d.add.length && !d.chg.length) return;
  const hs = historyOf(h.path);
  const entry = { label, before: h.before, after };
  // R-101: remember deleted_markers id so toolbar/Ctrl+Z Undo can clear it like the toast.
  if (meta && typeof meta === "object") entry.meta = meta;
  hs.undo.push(entry);
  while (hs.undo.length > HISTORY_LIMIT) hs.undo.shift();
  hs.redo.length = 0;
  syncHistoryButtons();
}
function historyClear(path) {
  markerHistory.delete(path);
  syncHistoryButtons();
}
function syncHistoryButtons() {
  const t = currentTrack();
  const hs = t ? markerHistory.get(t.path) : null;
  const u = $("undoEditBtn");
  const r = $("redoEditBtn");
  const busy = Boolean(state.historyBusy);
  if (u) {
    u.disabled = busy || !(hs && hs.undo.length);
    u.title = hs && hs.undo.length ? `Undo: ${hs.undo[hs.undo.length - 1].label}. Shortcut: Ctrl/Cmd+Z` : "Nothing to undo on this song yet";
  }
  if (r) {
    r.disabled = busy || !(hs && hs.redo.length);
    r.title = hs && hs.redo.length ? `Redo: ${hs.redo[hs.redo.length - 1].label}. Shortcut: Shift+Ctrl/Cmd+Z` : "Nothing to redo";
  }
}

/** R-101: match a point against GET /api/deleted-markers for this song. */
async function matchingDeletedMarkerId(path, point) {
  if (!path || !point) return null;
  try {
    const data = await api(`/api/deleted-markers?${new URLSearchParams({ path })}`);
    const kind = pointKind(point);
    const pos = Number(point.pos) || 0;
    const name = String(point.name || "");
    const size = point.size != null && point.size !== "" ? Number(point.size) : null;
    for (const m of data?.markers || []) {
      if ((m.kind || "cue") !== kind) continue;
      if (Math.abs((Number(m.pos) || 0) - pos) > 0.05) continue;
      if (String(m.name || "") !== name) continue;
      if (kind === "loop" && size != null && m.size != null && m.size !== "" && Math.abs(Number(m.size) - size) > 0.05) continue;
      return m.id || null;
    }
  } catch {
    /* list is best-effort */
  }
  return null;
}

/**
 * R-101: clear deleted_markers the same way as the delete Undo toast.
 * Returns true if /api/restore-deleted-marker ran (marker back + list cleared).
 */
async function restoreDeletedMarkerForPoint(path, point, allowRunning, historyEntry = null) {
  let id = historyEntry?.meta?.deletedMarkerId || null;
  if (id) {
    try {
      await api("/api/restore-deleted-marker", {
        method: "POST",
        body: JSON.stringify({ id, allow_vdj_running: Boolean(allowRunning) }),
      });
      return true;
    } catch {
      id = null; // stale (toast already restored) — try a fresh match
    }
  }
  id = await matchingDeletedMarkerId(path, point);
  if (!id) return false;
  await api("/api/restore-deleted-marker", {
    method: "POST",
    body: JSON.stringify({ id, allow_vdj_running: Boolean(allowRunning) }),
  });
  return true;
}

async function historyStep(direction) {
  const track = currentTrack();
  if (!track) return false;
  const path = track.path;
  const hs = historyOf(path);
  const from = direction === "undo" ? hs.undo : hs.redo;
  const to = direction === "undo" ? hs.redo : hs.undo;
  if (state.historyBusy) {
    setStatus(`Wait a moment - the last ${direction} is still saving.`);
    return false;
  }
  let entry = null;
  let plan = null;
  while (from.length) {
    const cand = from[from.length - 1];
    const target = direction === "undo" ? cand.before : cand.after;
    plan = diffMarkers(snapshotMarkers(path), target);
    if (plan.del.length || plan.add.length || plan.chg.length) {
      entry = cand;
      break;
    }
    from.pop(); // already in that state (e.g. restored with the delete toast): skip it
  }
  syncHistoryButtons();
  if (!entry) {
    setStatus(direction === "undo" ? "Nothing to undo on this song." : "Nothing to redo on this song.");
    return false;
  }
  const target = direction === "undo" ? entry.before : entry.after;
  const verb = direction === "undo" ? "Undo" : "Redo";
  const guard = await vdjOpenWriteGuard(track, `${verb} changes the saved song and may be overwritten when VirtualDJ quits.`, `${verb} anyway`);
  if (!guard.ok) {
    setStatus(`Close VirtualDJ, then ${verb.toLowerCase()}.`, "error");
    return false;
  }
  if (currentTrack()?.path !== path) return false;
  // After commit: toolbar/history Ctrl+Z supersedes the delete Undo toast (avoid a second restore with a stale id).
  while (typeof undoToasts !== "undefined" && undoToasts.length) dismissUndoToast(undoToasts[undoToasts.length - 1]);
  from.pop();
  state.historyBusy = true;
  syncHistoryButtons();
  // On screen at once: the markers are exactly the target set.
  const t = (state.tracks || []).find((x) => x.path === path);
  const points = target.map((p) => ({ ...p })).sort((a, b) => (Number(a.pos) || 0) - (Number(b.pos) || 0));
  applyCueSummaryToTrack(
    path,
    { ...t.cues, points, cue_count: points.filter((p) => pointKind(p) === "cue").length, loop_count: points.filter((p) => pointKind(p) === "loop").length },
    { local: true }
  );
  state.activeCueKey = null;
  state.activeLoopKey = null;
  repaintAfterMarkerChange(path);
  setStatus(`${verb === "Undo" ? "Undoing" : "Redoing"}: ${entry.label}…`);
  const allowRunning = Boolean(guard.allowRunning);
  const ident = (p) => ({
    num: p.num != null ? String(p.num) : null,
    name: p.name || null,
    slot: p.slot != null ? String(p.slot) : null,
  });
  let ok = true;
  try {
    await enqueueTrackEdit(path, async () => {
      for (const p of plan.del) {
        await api("/api/delete-cue", {
          method: "POST",
          body: JSON.stringify({ path, kind: pointKind(p), pos: Number(p.pos) || 0, ...ident(p), allow_vdj_running: allowRunning }),
        });
      }
      for (const p of plan.add) {
        // R-101: if this marker is on the deleted-markers list (normal after a delete),
        // restore via the same /api/restore-deleted-marker path as the Undo toast so AutoCue
        // does not keep treating it as deleted. add-cue/add-loop alone leave the list entry.
        const restored = await restoreDeletedMarkerForPoint(path, p, allowRunning, entry);
        if (restored) continue;
        const kind = pointKind(p);
        const color = sanitizeColorName(p.color_name);
        if (kind === "loop") {
          const bpm = trackBpm(track);
          const beats = Number(p.size) > 0 ? Number(p.size) : loopDurationSeconds(p, bpm) * (bpm / 60) || 8;
          await api("/api/add-loop", {
            method: "POST",
            body: JSON.stringify({ path, pos: Number(p.pos) || 0, name: p.name || null, color, beats, allow_vdj_running: allowRunning }),
          });
        } else {
          await api("/api/add-cue", {
            method: "POST",
            body: JSON.stringify({ path, pos: Number(p.pos) || 0, name: p.name || null, color, allow_vdj_running: allowRunning }),
          });
        }
      }
      for (const c of plan.chg) {
        const kind = pointKind(c.cur);
        let curPos = Number(c.cur.pos) || 0;
        let curName = c.cur.name || "";
        const base = () => ({ path, kind, pos: curPos, num: c.cur.num != null ? String(c.cur.num) : null, name: curName || null, slot: c.cur.slot != null ? String(c.cur.slot) : null, allow_vdj_running: allowRunning });
        if (c.fields.includes("name")) {
          await api("/api/rename-poi", { method: "POST", body: JSON.stringify({ ...base(), new_name: c.tgt.name || "" }) });
          curName = c.tgt.name || "";
        }
        if (c.fields.includes("color")) {
          await api("/api/set-cue-color", { method: "POST", body: JSON.stringify({ ...base(), color: sanitizeColorName(c.tgt.color_name) }) });
        }
        if (c.fields.includes("size") && Number(c.cur.size) > 0 && Number(c.tgt.size) > 0) {
          await api("/api/scale-loop", {
            method: "POST",
            body: JSON.stringify({ path, pos: curPos, factor: Number(c.tgt.size) / Number(c.cur.size), num: c.cur.num != null ? String(c.cur.num) : null, name: curName || null, slot: c.cur.slot != null ? String(c.cur.slot) : null, allow_vdj_running: allowRunning }),
          });
        }
        if (c.fields.includes("pos")) {
          await api("/api/move-poi", { method: "POST", body: JSON.stringify({ ...base(), new_pos: Number(c.tgt.pos) || 0 }) });
          curPos = Number(c.tgt.pos) || 0;
        }
      }
    });
    to.push(entry);
    while (to.length > HISTORY_LIMIT) to.shift();
    setStatus(`${verb === "Undo" ? "Undone" : "Redone"}: ${entry.label} - saved`, "success");
    quickConfirm(`${verb === "Undo" ? "Undone" : "Redone"}: ${entry.label} ✓`);
  } catch (err) {
    ok = false;
    from.push(entry);
    setStatus(`${verb} did not finish: ${err.message}. The song on screen is being re-read from the saved file.`, "error");
    scheduleLoadTracks({ keepPath: path });
  } finally {
    state.historyBusy = false;
    syncHistoryButtons();
  }
  if (ok) reconcileSoon(path);
  return ok;
}
function historyUndo() {
  return historyStep("undo");
}
function historyRedo() {
  return historyStep("redo");
}

function markersChanged(path) {
  bumpMarkerRev(path);
  const t = (state.tracks || []).find((x) => x.path === path);
  if (!t || !t.cues) return;
  const n = markerCountsOf(t);
  t.cues.cue_count = n.cues;
  t.cues.loop_count = n.loops;
  if (t.cues.in_database !== false) t.is_cued = n.cues > 0;
  if (t.readiness) {
    t.readiness.cue_count = n.cues;
    t.readiness.loop_count = n.loops;
    t.readiness.checks = {
      ...(t.readiness.checks || {}),
      has_cues: n.cues > 0,
      multiple_cues: n.cues >= 2,
      has_loops: n.loops > 0,
    };
  }
  if (markersSurfacesQueued) return;
  markersSurfacesQueued = true;
  requestAnimationFrame(() => {
    markersSurfacesQueued = false;
    const cur = currentTrack();
    if (cur) {
      try { updatePlayerMetaOnly(cur); } catch {}
      try { renderReviewPanel(); } catch {}
    }
    try { renderTrackList(); } catch {}
  });
}

/* Handlers are bound to the point objects of the render that built the row. A later server summary,
   reload or move swaps those objects, so a stale closure can carry an old name / position. Every
   write resolves the LIVE model point first (by kind+num+pos, then by loop slot / cue num). */
function resolveLivePoint(path, point) {
  if (!point) return point;
  const t = (state.tracks || []).find((x) => x.path === path);
  const pts = t?.cues?.points || [];
  if (pts.includes(point)) return point;
  const kind = pointKind(point);
  const byKey = pts.find((p) => cueKey(p) === cueKey(point));
  if (byKey) return byKey;
  if (kind === "loop" && point.slot != null) {
    const s = pts.find((p) => pointKind(p) === "loop" && String(p.slot) === String(point.slot));
    if (s) return s;
  }
  if (kind === "cue" && point.num != null && String(point.num) !== "-1") {
    const n = pts.find((p) => pointKind(p) === "cue" && String(p.num) === String(point.num));
    if (n) return n;
  }
  return point;
}

function pendingEditTotal() {
  let n = 0;
  for (const v of editPending.values()) n += v;
  return n;
}

function enqueueTrackEdit(path, task) {
  editPending.set(path, (editPending.get(path) || 0) + 1);
  state.lastAllSavedAt = 0;
  try { renderSaveBadges(); } catch {}
  const prev = editChains.get(path) || Promise.resolve();
  const next = prev
    .catch(() => {})
    .then(task)
    .finally(() => {
      const n = (editPending.get(path) || 1) - 1;
      if (n <= 0) {
        editPending.delete(path);
        if (needsReconcile.delete(path)) reconcileSoon(path);
      } else editPending.set(path, n);
      if (pendingEditTotal() === 0 && !(state.failedEdits || []).length) {
        state.lastAllSavedAt = Date.now();
        clearTimeout(state.allSavedTimer);
        state.allSavedTimer = setTimeout(() => {
          state.lastAllSavedAt = 0;
          try { renderSaveBadges(); } catch {}
        }, 6000);
      }
      try { renderSaveBadges(); } catch {}
    });
  editChains.set(path, next);
  next
    .catch(() => {})
    .then(() => {
      if (editChains.get(path) === next) editChains.delete(path);
    });
  return next;
}

function cueRowEl(path, key) {
  if (currentTrack()?.path !== path) return null;
  const list = $("cueList");
  if (!list) return null;
  return list.querySelector(`.cue-row[data-key="${CSS.escape(key)}"]`);
}

function modelPoint(path, key) {
  const t = (state.tracks || []).find((x) => x.path === path);
  return (t?.cues?.points || []).find((p) => cueKey(p) === key) || null;
}

function setRowSaveState(path, key, phase) {
  const row = cueRowEl(path, key);
  if (!row) return;
  clearTimeout(row._saveTimer);
  if (!phase) {
    delete row.dataset.save;
    return;
  }
  row.dataset.save = phase;
  if (phase === "saved" || phase === "failed") {
    row._saveTimer = setTimeout(
      () => {
        if (row.dataset.save === phase) delete row.dataset.save;
      },
      phase === "saved" ? 1600 : 8000
    );
  }
}

let waveRedrawQueued = false;
function scheduleWaveRedraw() {
  if (waveRedrawQueued) return;
  waveRedrawQueued = true;
  requestAnimationFrame(() => {
    waveRedrawQueued = false;
    drawWaveform();
  });
}

function patchPointName(path, point, name) {
  const key = cueKey(point);
  point.name = name;
  const mp = modelPoint(path, key);
  if (mp && mp !== point) mp.name = name;
  markersChanged(path);
  quickConfirm(`Renamed to “${name}” — saving…`);
  const row = cueRowEl(path, key);
  const nameEl = row && row.querySelector(".cue-name");
  if (nameEl) nameEl.textContent = name || (pointKind(point) === "loop" ? "Loop" : "Cue");
  document
    .querySelectorAll(`#cueTimeline .cue-marker[data-pos="${point.pos}"]`)
    .forEach((m) => {
      m.title = `${name} · ${fmtTime(point.pos)}${pointKind(point) === "loop" ? " (loop)" : ""}`;
    });
  scheduleWaveRedraw();
}

function patchPointColor(path, point, colorName) {
  const key = cueKey(point);
  point.color_name = colorName;
  const mp = modelPoint(path, key);
  if (mp && mp !== point) mp.color_name = colorName;
  markersChanged(path);
  quickConfirm(`${pointKind(point) === "loop" ? "Loop" : "Cue"} “${point.name || pointKind(point)}” → ${colorLabel(colorName)} — saving…`);
  for (const p of [point, mp]) {
    if (p && CUE_COLOR_ARGB[colorName] != null) p.color = CUE_COLOR_ARGB[colorName];
  }
  const kind = pointKind(point);
  const meaning = MusicSorterTransport.cueColorMeaning(colorName);
  const pretty = colorLabel(colorName); // R-99: "Light blue" immediately, not raw "lightblue"
  const row = cueRowEl(path, key);
  if (row) {
    const dot = row.querySelector(".cue-dot");
    if (dot) dot.className = `cue-dot ${kind} color-${colorName}`;
    const m = row.querySelector(".cue-color-meaning");
    if (m) {
      m.className = `cue-color-meaning color-${colorName}`;
      m.textContent = `${pretty} · ${meaning}`;
      m.title = `${pretty} · ${meaning}`;
    }
    const chip = row.querySelector(".cue-color-btn .cue-color-chip");
    if (chip) chip.className = `cue-color-chip color-${colorName}`;
    const cbtn = row.querySelector(".cue-color-btn");
    if (cbtn) {
      cbtn.title = `Change marker color in VirtualDJ · ${pretty} — ${meaning}`;
      cbtn.setAttribute("aria-label", `Color for ${point.name || kind}: ${pretty}`);
    }
  }
  document
    .querySelectorAll(`#cueTimeline .cue-marker[data-pos="${point.pos}"]`)
    .forEach((m) => {
      m.className = `cue-marker ${kind} color-${colorName || "unknown"}`;
    });
  drawWaveform(); // repaint loop/cue blocks on the wave immediately
}

/**
 * Inline rename: click cue/loop name text → input → Enter/blur saves, Esc cancels.
 */
/** A list redraw that was held back while a name box (or color menu) was open runs once it is closed. */
function flushDeferredCueRender() {
  if (state.renderCuesDeferred && !colorMenuOpen() && !document.querySelector("#cueList .cue-name-input")) {
    state.renderCuesDeferred = false;
    setTimeout(() => renderCues(), 0);
  }
}

function beginRenamePoi(point, nameEl) {
  const track = currentTrack();
  if (!track || !point || !nameEl || nameEl.dataset.editing === "1") return;

  const kind = pointKind(point);
  const prevName = String(point.name || "").trim();
  nameEl.dataset.editing = "1";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "cue-name-input";
  input.value = prevName;
  input.maxLength = 120;
  input.setAttribute(
    "aria-label",
    `Rename ${kind === "loop" ? "loop" : "cue"}`
  );
  input.title = "Enter to save · Esc to cancel";

  const parent = nameEl.parentNode;
  parent.replaceChild(input, nameEl);
  input.focus();
  input.select();
  // The click that opened the box can still move the caret: select the whole name again right after.
  setTimeout(() => {
    if (document.activeElement === input && !input.dataset.typed) input.select();
  }, 0);
  input.addEventListener("input", () => {
    input.dataset.typed = "1";
  });

  let finished = false;
  // Put the original name span back in place of the input (no list redraw).
  const putBack = (text) => {
    nameEl.dataset.editing = "";
    if (text != null) nameEl.textContent = text;
    if (input.parentNode) input.parentNode.replaceChild(nameEl, input);
    flushDeferredCueRender();
  };
  const restore = (text) => {
    if (finished) return;
    finished = true;
    putBack(text);
  };

  const commit = async () => {
    if (finished) return;
    const next = String(input.value || "").trim();
    if (!next) {
      setStatus("Name cannot be empty", "error");
      input.focus();
      return;
    }
    if (next === prevName) {
      finished = true;
      putBack(null); // unchanged: put the name back (restore() would return early because finished is set)
      return;
    }
    finished = true;
    const unique = uniqueRenameForKind(track, point, next);
    const note =
      unique !== next ? `Another ${kind} is already called “${next}” — using “${unique}”` : "";
    putBack(unique); // optimistic: the new name is on screen before the save starts
    renamePoiPoint(point, unique, prevName, note);
  };

  const cancel = () => {
    if (finished) return;
    finished = true;
    putBack(null);
  };

  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  });
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("mousedown", (e) => e.stopPropagation());
  input.addEventListener("blur", () => {
    // Defer so Enter can mark finished first.
    setTimeout(() => {
      if (!finished) commit();
    }, 0);
  });
}

/* Loops and cues MAY share a name ("Beat Entry" loop next to the "Beat Entry" cue). Only a second
   marker of the SAME kind with the same name is a duplicate; it gets a calm auto-suffix, never a red error. */
function sameKindNameTaken(track, point, name) {
  const kind = pointKind(point);
  const key = cueKey(point);
  const lc = String(name || "").trim().toLowerCase();
  return ((track && track.cues && track.cues.points) || []).some(
    (p) =>
      pointKind(p) === kind &&
      cueKey(p) !== key &&
      String(p.name || "").trim().toLowerCase() === lc
  );
}

function uniqueRenameForKind(track, point, name) {
  if (!sameKindNameTaken(track, point, name)) return name;
  for (let n = 2; n < 100; n += 1) {
    const candidate = `${name} ${n}`;
    if (!sameKindNameTaken(track, point, candidate)) return candidate;
  }
  return name;
}

async function renamePoiPoint(point, newName, prevName, note = "") {
  const track = currentTrack();
  if (!track || !point) return;
  const path = track.path;
  point = resolveLivePoint(path, point);
  const kind = pointKind(point);
  const key = cueKey(point);
  const label = prevName || kind;
  const hist = historyBegin(path);
  const sentName = point.name || null; // what the server currently has (earlier queued renames land first)
  const body = {
    path,
    kind,
    pos: Number(point.pos) || 0,
    new_name: newName,
    num: point.num != null ? String(point.num) : null,
    name: sentName,
    slot: point.slot != null ? String(point.slot) : null,
  };

  // Optimistic: row text, waveform label, timeline tooltip and the in-memory model.
  patchPointName(path, point, newName);
  setRowSaveState(path, key, "saving");
  setStatus(note || `Saving ${kind} name “${newName}”…`);

  const revert = () => {
    const live = modelPoint(path, key) || point;
    if (live.name === newName) patchPointName(path, live, sentName || "");
    if (point !== live && point.name === newName) point.name = sentName || "";
  };

  return enqueueTrackEdit(path, async () => {
    let allowRunning = false;
    if (await isVdjRunningFresh()) {
      allowRunning = await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "Renames may be overwritten when VirtualDJ quits. Close it first when possible.",
        confirmLabel: "Rename anyway",
        tone: "warning",
      });
      if (!allowRunning) {
        revert();
        setRowSaveState(path, key, "failed");
        setStatus("Close VirtualDJ, then rename the marker.", "error");
        return;
      }
    }
    try {
      let finalName = newName;
      refreshBodyIdentity(body, path, key, point);
      try {
        await api("/api/rename-poi", {
          method: "POST",
          body: JSON.stringify({ ...body, allow_vdj_running: Boolean(allowRunning) }),
        });
      } catch (err) {
        if (!err || !err.softConflict) throw err;
        // The name is taken for some data reason: keep going with a clearly-named variant, calmly.
        finalName = `${newName} ${kind === "loop" ? "Loop" : "Cue"}`;
        patchPointName(path, modelPoint(path, key) || point, finalName);
        await api("/api/rename-poi", {
          method: "POST",
          body: JSON.stringify({
            ...body,
            new_name: finalName,
            allow_vdj_running: Boolean(allowRunning),
          }),
        });
        setStatus(`“${newName}” was taken, so this ${kind} is now “${finalName}”.`);
        setRowSaveState(path, key, "saved");
        historyCommit(hist, `name of ${kind} “${label}”`);
        return;
      }
      setRowSaveState(path, key, "saved");
        historyCommit(hist, `name of ${kind} “${label}”`);
      setStatus(`Saved ${kind}: “${label}” → “${finalName}”${note ? ` (${note})` : ""}`, "success");
    } catch (err) {
      revert();
      setRowSaveState(path, key, "failed");
      setStatus(err.message, "error");
    }
  });
}

/* ===== Cue / loop color picker (custom menu; every pick applies the swatch that was clicked) ===== */
let cueColorMenuState = null; // { el, btn, key, index }

function colorMenuOpen() {
  return Boolean(cueColorMenuState);
}

function closeCueColorMenu() {
  const st = cueColorMenuState;
  if (!st) return;
  cueColorMenuState = null;
  st.el.remove();
  st.btn?.setAttribute("aria-expanded", "false");
  document.removeEventListener("pointerdown", onCueColorMenuOutside, true);
  document.removeEventListener("keydown", onCueColorMenuKey, true);
  if (state.renderCuesDeferred) {
    state.renderCuesDeferred = false;
    renderCues();
  }
}

function onCueColorMenuOutside(e) {
  if (e.target?.closest?.(".cue-color-menu")) return;
  closeCueColorMenu();
}

function onCueColorMenuKey(e) {
  if (e.key === "Escape") {
    e.stopPropagation();
    closeCueColorMenu();
  }
}

function openCueColorMenu(btn, key, index) {
  const reopen = cueColorMenuState && cueColorMenuState.btn === btn;
  closeCueColorMenu();
  if (reopen) return;
  const track = currentTrack();
  if (!track) return;
  const points = track.cues?.points || [];
  const point = (key && modelPoint(track.path, key)) || points[index];
  if (!point) return;
  const cur = sanitizeColorName(point.color_name);
  const menu = document.createElement("div");
  menu.className = "cue-color-menu";
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", "Marker color");
  for (const c of CUE_COLOR_OPTIONS) {
    const opt = document.createElement("button");
    opt.type = "button";
    opt.className = `cue-color-opt color-${c.id}`;
    opt.dataset.color = c.id;
    opt.setAttribute("role", "option");
    opt.setAttribute("aria-selected", c.id === cur ? "true" : "false");
    opt.innerHTML = `<span class="cue-color-chip color-${c.id}"></span><span class="cue-color-opt-text">${escapeHtml(c.name)} · ${escapeHtml(c.meaning)}</span>`;
    // Keep focus where it is: nothing may close or re-render the menu between press and click.
    opt.addEventListener("pointerdown", (e) => e.preventDefault());
    opt.addEventListener("click", (e) => {
      e.stopPropagation();
      const chosen = opt.dataset.color;
      const live = (key && modelPoint(track.path, key)) || point;
      closeCueColorMenu();
      setCueColor(live, chosen, null);
    });
    menu.appendChild(opt);
  }
  document.body.appendChild(menu);
  const r = btn.getBoundingClientRect();
  const mh = menu.offsetHeight || 190;
  const top = r.bottom + mh > window.innerHeight - 8 ? Math.max(8, r.top - mh - 4) : r.bottom + 4;
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 270))}px`;
  menu.style.top = `${top}px`;
  btn.setAttribute("aria-expanded", "true");
  cueColorMenuState = { el: menu, btn, key, index };
  document.addEventListener("pointerdown", onCueColorMenuOutside, true);
  document.addEventListener("keydown", onCueColorMenuKey, true);
  menu.querySelector('[aria-selected="true"]')?.focus?.();
}

/* Re-read position / num / slot from the live model right before a queued save is sent. */
function refreshBodyIdentity(body, path, key, fallback) {
  const lp = modelPoint(path, key) || resolveLivePoint(path, fallback);
  if (!lp) return;
  body.pos = Number(lp.pos) || 0;
  body.num = lp.num != null ? String(lp.num) : null;
  body.slot = lp.slot != null ? String(lp.slot) : null;
}

function colorLabel(id) {
  const c = (window.MusicSorterTransport?.CUE_COLOR_SCHEME || []).find((x) => x.id === id);
  return c ? c.name : String(id || "unknown");
}

async function setCueColor(point, color, selectEl) {
  const track = currentTrack();
  if (!track || !point || !color) return;
  const path = track.path;
  point = resolveLivePoint(path, point);
  const kind = pointKind(point);
  const key = cueKey(point);
  const safeColor = sanitizeColorName(color);
  const prev = sanitizeColorName(point.color_name);
  if (prev === safeColor) return;
  const hist = historyBegin(path);
  const body = {
    path,
    kind,
    pos: Number(point.pos) || 0,
    color: safeColor,
    num: point.num != null ? String(point.num) : null,
    name: point.name || null,
    slot: point.slot != null ? String(point.slot) : null,
  };

  patchPointColor(path, point, safeColor); // optimistic: swatch, dot, timeline marker, waveform
  setRowSaveState(path, key, "saving");
  setStatus(`Changing ${kind} color to ${colorLabel(safeColor)}…`);

  const revert = () => {
    const live = modelPoint(path, key) || point;
    if (sanitizeColorName(live.color_name) === safeColor) patchPointColor(path, live, prev);
    if (point !== live) point.color_name = prev;
    if (selectEl) selectEl.value = prev;
  };

  return enqueueTrackEdit(path, async () => {
    let allowRunning = false;
    if (await isVdjRunningFresh()) {
      allowRunning = await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "Color changes may be overwritten when VirtualDJ quits. Close it first when possible.",
        confirmLabel: "Change color anyway",
        tone: "warning",
      });
      if (!allowRunning) {
        revert();
        setRowSaveState(path, key, "failed");
        setStatus("Close VirtualDJ, then change the color.", "error");
        return;
      }
    }
    try {
      refreshBodyIdentity(body, path, key, point); // identity as of NOW, after any earlier queued edits
      const data = await api("/api/set-cue-color", {
        method: "POST",
        body: JSON.stringify({ ...body, allow_vdj_running: Boolean(allowRunning) }),
      });
      const after = data?.result?.change?.color_after;
      let actual = safeColor;
      if (after) {
        const live = modelPoint(path, key);
        if (live) live.color = after;
        point.color = after;
        const found = Object.keys(CUE_COLOR_ARGB).find((k) => String(CUE_COLOR_ARGB[k]) === String(after));
        if (found) actual = found;
      }
      const what = `${kind === "loop" ? "Loop" : "Cue"} “${point.name || kind}”`;
      if (actual !== safeColor) {
        // The file says something different from what was asked: show what is really saved.
        const live = modelPoint(path, key) || point;
        patchPointColor(path, live, actual);
        if (selectEl) selectEl.value = actual;
        setRowSaveState(path, key, "failed");
        setStatus(`${what} is now ${colorLabel(actual)}, not ${colorLabel(safeColor)} - the saved file kept a different color. Try again.`, "error");
      } else {
        setRowSaveState(path, key, "saved");
        historyCommit(hist, `color of ${what} to ${colorLabel(safeColor)}`);
        setStatus(`${what} is now ${colorLabel(safeColor)} - saved`, "success");
      }
    } catch (err) {
      if (!err.keptFailedEdit) revert(); // a recorded failed save stays on screen + in the NOT saved list (Retry)
      const why = String(err.message || err || "").replace(/^FAILED:\s*/i, "").replace(/^\[error\]\s*/i, "");
      const offline = Boolean(err.keptFailedEdit) && /can'?t be reached right now/i.test(why);
      if (offline) {
        // R-99: optimistic color stays; autosave via failedEdits + server banner. Calm status, not red "was not changed".
        setRowSaveState(path, key, "saving");
        setStatus(why, "");
      } else {
        setRowSaveState(path, key, "failed");
        setStatus(
          `${kind === "loop" ? "Loop" : "Cue"} “${point.name || kind}” was not changed to ${colorLabel(safeColor)} (still ${colorLabel(prev)}). ${why}`,
          "error"
        );
      }
    }
  });
}

/**
 * Audition a loop after resize: enable loop play, jump to start, play.
 */
function auditionLoopPoint(point) {
  if (!point || pointKind(point) !== "loop") return;
  const track = currentTrack();
  const bpm = trackBpm(track);
  const start = Number(point.pos) || 0;
  const end = start + loopDurationSeconds(point, bpm);
  state.loopPlaybackOn = true;
  state.activeLoopKey = cueKey(point);
  state.activeCueKey = cueKey(point);
  syncLoopPlayBtn();
  jumpToCue(start, point);
  const audio = $("audio");
  if (audio) {
    playAudio(audio).catch(() => {});
    startLoopWatch();
  }
  setStatus(
    `Auditioning loop · ${point.name || "loop"} ${point.size || "?"}b ` +
      `(${fmtTime(start)}–${fmtTime(end)})`
  );
  renderCues();
  drawWaveform();
}

async function scaleLoopPoint(point, factor) {
  const track = currentTrack();
  if (!track || !point || pointKind(point) !== "loop") return;
  const live = resolveLivePoint(track.path, point);
  const oldBeats = Number(live.size) || 0;
  if (!(oldBeats > 0)) {
    setStatus("This loop has no known size to scale.", "error");
    return;
  }
  const verb = factor < 1 ? "Halve" : "Double";
  const newBeats = Math.min(256, Math.max(1, oldBeats * factor));
  const clamped = clampLoopBeatsToSongEnd(track, live, newBeats);
  if (clamped.atEnd) {
    setStatus(`“${live.name || "Loop"}” already reaches the end of the song (${fmtBeats(oldBeats)}b).`);
    return;
  }
  const note = clamped.clamped
    ? `${verb}d to the end of the song (${fmtBeats(clamped.beats)}b).`
    : "";
  await commitLoopResize(live, oldBeats, clamped.beats, { note, audition: true });
}

function fmtBeats(b) {
  return String(Math.round(Number(b) * 100) / 100);
}

/* A loop may not run past the end of the song: shrink the request to the room that is left. */
function clampLoopBeatsToSongEnd(track, point, wantBeats) {
  const bpm = trackBpm(track);
  const dur = trackDuration(track, $("audio"));
  const start = Number(point.pos) || 0;
  if (!(bpm > 0) || !(dur > 0)) return { beats: wantBeats, clamped: false, atEnd: false };
  const room = Math.floor(((dur - start) * bpm / 60) * 4) / 4;
  const old = Number(point.size) || 0;
  if (wantBeats <= room + 1e-6) return { beats: wantBeats, clamped: false, atEnd: false };
  const beats = Math.max(1, room);
  return { beats, clamped: true, atEnd: old > 0 && Math.abs(beats - old) < 0.01 };
}

/**
 * Patch the in-memory track with fresh cue summary from a mutation API
 * so the list/waveform clear immediately without waiting on full reload.
 */
function applyCueSummaryToTrack(path, cuesSummary, { local = false } = {}) {
  if (!path || !cuesSummary) return null;
  // Other saves for this track are still queued: their optimistic edits (names, colors, sizes) are
  // newer than this server snapshot. Do not overwrite them; the quiet reload after the queue drains
  // brings everything back in line.
  if (!local && (editPending.get(path) || 0) >= 1 && cuesSummary.points) {
    const { points: _skip, ...rest } = cuesSummary;
    cuesSummary = rest;
    needsReconcile.add(path);
    reconcileSoon(path);
  }
  const idx = state.tracks.findIndex((t) => t.path === path);
  if (idx < 0) return null;
  const prev = state.tracks[idx];
  const next = {
    ...prev,
    cues: { ...(prev.cues || {}), ...cuesSummary },
    is_cued: Boolean(cuesSummary.is_cued ?? (cuesSummary.cue_count > 0)),
  };
  // Keep readiness roughly in sync so filters/badges don't lie.
  if (next.readiness || isReviewMode()) {
    const cueN = Number(cuesSummary.cue_count) || 0;
    const loopN = Number(cuesSummary.loop_count) || 0;
    const hasGrid = Boolean(cuesSummary.has_beatgrid);
    let status = "not_cued";
    if (cueN >= 2 && loopN >= 2 && hasGrid) status = "ready";
    else if (cueN > 0 || loopN > 0) status = "partial";
    else if (cuesSummary.in_database === false) status = "missing";
    next.readiness = {
      ...(next.readiness || {}),
      status,
      ready: status === "ready",
      cue_count: cueN,
      loop_count: loopN,
      has_beatgrid: hasGrid,
    };
  }
  state.tracks = state.tracks.map((t, i) => (i === idx ? overlayFailedEdits(dropOutOfSongPoints(next)) : t));
  if (currentTrack()?.path === path) {
    refreshPlacementMatchUi(state.tracks[idx]);
  }
  markersChanged(path);
  return state.tracks[idx];
}

/**
 * VirtualDJ-is-open guard for quick edits: ask ONCE per page session ("Allow edits while VirtualDJ is
 * open"), then stay out of the way. Returns {ok, allowRunning}. The server still refuses writes it
 * considers unsafe.
 */
async function vdjOpenWriteGuard(track, message, confirmLabel) {
  if (!(await isVdjRunningFresh())) return { ok: true, allowRunning: false };
  if (state.vdjWriteApprovedAt && Date.now() - state.vdjWriteApprovedAt < 30 * 60 * 1000) {
    return { ok: true, allowRunning: true };
  }
  const yes = await showConfirmDialog({
    title: "VirtualDJ is still open",
    track: trackDisplayTitle(track),
    message,
    note: "Asked once; later quick edits in this tab go straight through.",
    confirmLabel,
    tone: "warning",
  });
  if (yes) state.vdjWriteApprovedAt = Date.now();
  return { ok: Boolean(yes), allowRunning: Boolean(yes) };
}

/* ===== Delete = instant (optimistic) + 8 s Undo toast; Undo restores the exact marker ===== */
const UNDO_TOAST_MS = 8000;
const undoToasts = []; // newest last

function removePointFromModel(path, key) {
  const t = (state.tracks || []).find((x) => x.path === path);
  const pts = t?.cues?.points || [];
  const gone = pts.find((p) => cueKey(p) === key);
  if (!t || !gone) return null;
  const points = pts.filter((p) => cueKey(p) !== key);
  quickConfirm(`${pointKind(gone) === "loop" ? "Loop" : "Cue"} “${gone.name || pointKind(gone)}” removed — saving…`);
  applyCueSummaryToTrack(path, {
    ...t.cues,
    points,
    cue_count: points.filter((p) => pointKind(p) === "cue").length,
    loop_count: points.filter((p) => pointKind(p) === "loop").length,
  }, { local: true });
  return gone;
}

function addPointToModel(path, snapshot) {
  const t = (state.tracks || []).find((x) => x.path === path);
  if (!t) return;
  const key = cueKey(snapshot);
  const pts = (t.cues?.points || []).filter((p) => cueKey(p) !== key);
  pts.push({ ...snapshot });
  pts.sort((x, y) => (Number(x.pos) || 0) - (Number(y.pos) || 0));
  quickConfirm(`${pointKind(snapshot) === "loop" ? "Loop" : "Cue"} “${snapshot.name || pointKind(snapshot)}” at ${fmtTime(snapshot.pos)} — saving…`);
  applyCueSummaryToTrack(path, {
    ...t.cues,
    points: pts,
    cue_count: pts.filter((p) => pointKind(p) === "cue").length,
    loop_count: pts.filter((p) => pointKind(p) === "loop").length,
  }, { local: true });
}

function repaintAfterMarkerChange(path) {
  if (currentTrack()?.path !== path) return;
  renderCues();
  drawWaveform();
  renderReviewPanel();
  updatePlayerMetaOnly(currentTrack());
}

function reconcileSoon(path) {
  setTimeout(() => {
    if (!editChains.has(path) && !undoToasts.length) scheduleLoadTracks({ keepPath: path });
  }, 900);
}

function toastHost() {
  let host = $("undoToastHost");
  if (!host) {
    host = document.createElement("div");
    host.id = "undoToastHost";
    host.className = "undo-toast-host";
    host.setAttribute("role", "status");
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  return host;
}

function dismissUndoToast(rec) {
  clearTimeout(rec.timer);
  rec.el?.remove();
  const i = undoToasts.indexOf(rec);
  if (i >= 0) undoToasts.splice(i, 1);
}

function showUndoToast(text, onUndo) {
  const rec = { timer: null, el: null, onUndo };
  const el = document.createElement("div");
  el.className = "undo-toast";
  el.innerHTML = `<span class="undo-toast-text"></span><button type="button" class="undo-toast-btn">Undo</button><span class="undo-toast-bar"></span>`;
  el.querySelector(".undo-toast-text").textContent = text;
  el.querySelector(".undo-toast-bar").style.animationDuration = `${UNDO_TOAST_MS}ms`;
  el.querySelector(".undo-toast-btn").addEventListener("click", () => {
    dismissUndoToast(rec);
    onUndo();
  });
  rec.el = el;
  rec.timer = setTimeout(() => dismissUndoToast(rec), UNDO_TOAST_MS);
  toastHost().appendChild(el);
  undoToasts.push(rec);
  return rec;
}

function undoLatestDeleteToast() {
  const rec = undoToasts[undoToasts.length - 1];
  if (!rec) return false;
  dismissUndoToast(rec);
  rec.onUndo();
  return true;
}

async function deleteCuePoint(point) {
  const track = currentTrack();
  if (!track || !point) return;
  point = resolveLivePoint(track.path, point);
  const kind = pointKind(point);
  const path = track.path;
  const key = cueKey(point);
  const snapshot = { ...point }; // exact name / pos / size / color / num, for Undo
  const hist = historyBegin(path);
  // Name the marker that is really being deleted; an unnamed one is told by its position.
  const rawName = String(point.name || "").trim();
  const label =
    rawName && !/^(cue|loop)( \d+)?$/i.test(rawName)
      ? rawName
      : `${kind === "loop" ? "loop" : "cue"} at ${fmtTime(point.pos)}`;

  const guard = await vdjOpenWriteGuard(
    track,
    "Cue changes may be overwritten when VirtualDJ quits. Close it first when possible.",
    "Delete anyway"
  );
  if (!guard.ok) {
    setStatus("Close VirtualDJ, then delete the marker.", "error");
    return;
  }
  const allowRunning = guard.allowRunning;

  // Optimistic: the marker disappears at once (list, timeline, waveform); no confirm modal.
  const delRow = cueRowEl(path, key);
  if (delRow) delRow.classList.add("pending-delete");
  if (kind === "loop" && state.activeLoopKey === key) {
    state.activeLoopKey = null;
    if (state.loopPlaybackOn) {
      state.loopPlaybackOn = false;
      syncLoopPlayBtn();
      stopLoopWatch();
    }
  }
  if (state.activeCueKey === key) state.activeCueKey = null;
  removePointFromModel(path, key);
  repaintAfterMarkerChange(path);
  const delText = rawName && label === rawName ? `Deleted ${kind} “${label}”` : `Deleted ${label}`;
  setStatus(delText, "success");

  const rec = { markerId: null, failed: false };
  const toast = showUndoToast(delText, () => undoDelete(path, snapshot, rec, label, kind));

  try {
    const data = await enqueueTrackEdit(track.path, () => api("/api/delete-cue", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        kind,
        pos: Number(point.pos) || 0,
        num: point.num != null ? String(point.num) : null,
        name: point.name || null,
        slot: point.slot != null ? String(point.slot) : null,
        allow_vdj_running: Boolean(allowRunning),
      }),
    }));
    rec.markerId = data?.result?.deleted_marker_id || null;
    if (!data?.already_gone) historyCommit(hist, `delete of ${kind} “${label}”`, rec.markerId ? { deletedMarkerId: rec.markerId } : null);
    rec.allowRunning = allowRunning;
    if (data?.already_gone) {
      dismissUndoToast(toast);
      setStatus(data.result?.note || "That marker was already gone from the saved song.", "success");
    } else if (!rec.markerId) {
      // Server could not remember it, so there is nothing safe to restore.
      dismissUndoToast(toast);
      setStatus(`Deleted ${kind} “${label}” (undo unavailable)`, "success");
    }
    reconcileSoon(path); // quiet refresh of badges/counts once no edit is in flight
  } catch (err) {
    rec.failed = true;
    dismissUndoToast(toast);
    if (delRow) delRow.classList.remove("pending-delete");
    addPointToModel(path, snapshot);
    repaintAfterMarkerChange(path);
    setStatus(`Delete failed — ${label} is back. ${err.message}`, "error");
  }
}

async function undoDelete(path, snapshot, rec, label, kind) {
  addPointToModel(path, snapshot); // optimistic: back at once with the identical values
  repaintAfterMarkerChange(path);
  setStatus(`Restoring ${kind} “${label}”…`);
  try {
    await enqueueTrackEdit(path, async () => {
      if (rec.failed) throw new Error("nothing to restore");
      // R-101: toast may fire before delete's response assigned markerId — match the list like toolbar Undo.
      let id = rec.markerId || null;
      if (!id) id = await matchingDeletedMarkerId(path, snapshot);
      if (!id) throw new Error("nothing to restore");
      await api("/api/restore-deleted-marker", {
        method: "POST",
        body: JSON.stringify({ id, allow_vdj_running: Boolean(rec.allowRunning) }),
      });
    });
    setStatus(`Restored ${kind} “${label}”`, "success");
    reconcileSoon(path);
  } catch (err) {
    removePointFromModel(path, cueKey(snapshot));
    repaintAfterMarkerChange(path);
    setStatus(`Undo failed — ${label} stays deleted. ${err.message}`, "error");
  }
}

function setStatus(msg, kind = "", action = null) {
  const el = $("status");
  if (!el) return;
  el.className = `status-bar ${kind || ""}`.trim();
  el.replaceChildren();
  const text = document.createElement("span");
  text.className = "status-text";
  text.textContent = msg || "";
  el.appendChild(text);
  if (action && action.label && action.onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn primary empty-cta";
    btn.textContent = action.label;
    if (action.gotoMode) btn.dataset.gotoMode = action.gotoMode;
    btn.addEventListener("click", action.onClick);
    el.appendChild(btn);
  }
}

function updatePipelineStrip() {
  const kicker = $("pipelineKicker");
  const title = $("pipelineTitle");
  const hint = $("pipelineHint");
  const next = $("pipelineNextAction");
  if (!kicker || !title || !hint || !next) return;

  const track = currentTrack();
  const n = state.tracks.length;
  const readyN = state.tracks.filter((t) => trackReadinessStatus(t) === "ready").length;
  const notCuedN = state.tracks.filter((t) => {
    const s = trackReadinessStatus(t);
    return s === "not_cued" || s === "missing";
  }).length;
  const destN = (state.selectedDests || []).length;

  if (isPracticeMode()) {
    kicker.textContent = "Practice";
    title.textContent = "Score transitions";
    hint.textContent = n > 0 ? `${n} mixes` : "Add mixes to begin";
    next.textContent = track ? "Analyze below" : "Select a mix";
    return;
  }
  if (isAssembleMode()) {
    kicker.textContent = "Assemble";
    title.textContent = "Pajamathon crate";
    const job = state.assembleJob;
    const n = job?.result?.playlist?.length;
    hint.textContent = job
      ? job.message || `${n || 0} in playlist`
      : "Gemini scores the library in chunks · newest first";
    next.textContent = assembleJobBusy(job) ? "Scoring chunks…" : "Build 300–500";
    return;
  }
  if (isStemsMode()) {
    kicker.textContent = "Stems";
    title.textContent = "Vocal-layer hole check";
    const job = state.stemAuditJob;
    const n = state.stemInventory?.sidecar_count;
    hint.textContent = job
      ? job.message || `${job.broken_count || 0} broken · ${job.checked || 0} checked`
      : n != null
        ? `${n} .vdjstems sidecars in DJ Music`
        : "Scan sidecars for digital-mute vocal tiles";
    next.textContent = MusicSorterStems.stemsJobBusy(job)
      ? "Scanning…"
      : job?.broken_count
        ? "Review flagged files"
        : "Scan DJ Music";
    return;
  }
  if (isRecsMode()) {
    kicker.textContent = "Recs";
    title.textContent = "Next-track recommendations";
    const np = state.recsNow;
    const n = state.recsResult?.candidates_considered;
    hint.textContent = np
      ? `${np.artist ? np.artist + " – " : ""}${np.title || np.name || "Track"}${
          n != null ? ` · ${n} in-key ±5 BPM` : " · auto"
        }`
      : "Waiting for VirtualDJ · auto-poll + auto-recs";
    next.textContent = np
      ? state.recsJobRunning
        ? "Ranking energy…"
        : "Higher · same · lower"
      : "Play a track in VDJ";
    return;
  }
  if (isSetOverviewMode()) {
    const dirs = uniqueSetDirs(state.tracks);
    const filter = state.setDirFilter || "pajamathon";
    const visible = state.tracks.filter((tr) => trackMatchesSetDir(tr));
    const sameN = visible.filter((tr) => tr.written_matches).length;
    const diffN = visible.length - sameN;
    kicker.textContent = "Set Overview";
    title.textContent = "Review cues vs the written copy";
    hint.textContent = visible.length
      ? `${visible.length} in ${filter === "all" ? "all sets" : filter} · ${sameN} same · ${diffN} different`
      : "No tracks in this crate";
    if (!track) next.textContent = visible.length ? "Select a track" : "Empty crate";
    else if (cuedSiblingHit(track) && !trackMatchesWrittenCopy(track, cuedSiblingHit(track)))
      next.textContent = "Right: Copy cues";
    else next.textContent = "same / different";
    return;
  }
  if (isReviewMode()) {
    kicker.textContent = "Add Cues";
    title.textContent = "Listen, then pick a House folder";
    hint.textContent =
      n > 0
        ? `${n} in queue · ${readyN} ready · ${notCuedN} need cues`
        : "Empty queue. Use Open Sort if Ready already has tracks.";
    const pajNeed = state.tracks.filter(
      (t) =>
        addCuesSection(t) === "pajamathon" &&
        ["not_cued", "missing"].includes(trackReadinessStatus(t))
    ).length;
    const pajN = state.tracks.filter((t) => addCuesSection(t) === "pajamathon").length;
    if (pajN && !isHouseProfile()) {
      hint.textContent =
        n > 0
          ? `${n} in queue · Pajamathon ${pajNeed}/${pajN} need cues · ${readyN} ready`
          : "Empty queue. Use Open Sort if Ready already has tracks.";
    }
    if (!track) next.textContent = n ? "Select a track" : "Queue empty";
    else if (isPajamathonSetQueueTrack(track))
      next.textContent = track.is_cued
        ? "Cue in set · pick a House folder to sort"
        : "Right: AutoCue in set";
    else if (!track.is_cued) next.textContent = "Right: AutoCue";
    else next.textContent = "Right: pick a House folder, then sort";
    return;
  }
  // Sort
  kicker.textContent = "Sort";
  title.textContent = "Copy cued tracks into House folders";
  hint.textContent =
    n > 0
      ? `${n} ready · choose a folder on the right`
      : "Nothing in Ready. Promote from Add Cues first.";
  if (!track) next.textContent = n ? "Select a track" : "Queue empty";
  else if (!track.is_cued) next.textContent = "Send back to Add Cues";
  else if (destN) next.textContent = "Right: Sort";
  else next.textContent = "Right: pick a folder";
}

function emptyStateHtml({ icon = "◎", title, copy, ctaLabel, ctaMode }) {
  const cta = ctaLabel
    ? `<button type="button" class="btn primary empty-cta" data-goto-mode="${escapeHtml(
        ctaMode || ""
      )}">${escapeHtml(ctaLabel)}</button>`
    : "";
  return `<div class="empty empty-state">
    <div class="empty-state-icon" aria-hidden="true">${icon}</div>
    <p class="empty-state-title">${escapeHtml(title)}</p>
    <p class="empty-state-copy">${escapeHtml(copy)}</p>
    ${cta}
  </div>`;
}

function trackBpm(track) {
  /** Raw VDJ BPM from the database (may be double-time). */
  const bpm = Number(track?.cues?.bpm);
  return bpm > 0 ? bpm : null;
}

function sourceBpm(track) {
  /**
   * BPM used for playback-rate math. When halfBpm is on, VDJ's value is
   * treated as double-time (common at ~140 when the track is really ~70).
   * Loop/waveform still use raw trackBpm — cue positions are wall-clock.
   */
  const raw = trackBpm(track);
  if (!raw) return null;
  return state.halfBpm ? raw / 2 : raw;
}

function clampRate(rate) {
  return Math.min(1.15, Math.max(0.35, rate));
}

function rateForTargetBpm(originalBpm, targetBpm) {
  if (!originalBpm || originalBpm <= 0) return 1;
  return clampRate(targetBpm / originalBpm);
}

function applyPlaybackRate(rate, { fromZoukButton = false } = {}) {
  const audio = $("audio");
  const r = clampRate(Number(rate) || 1);
  state.playbackRate = r;
  if (audio) {
    audio.playbackRate = r;
    // Keep pitch linked for a natural slowed feel (HTML audio default).
    try {
      audio.preservesPitch = false;
      audio.mozPreservesPitch = false;
      audio.webkitPreservesPitch = false;
    } catch {
      /* ignore */
    }
  }
  const slider = $("speedSlider");
  if (slider && Math.abs(Number(slider.value) - r) > 0.005) {
    slider.value = String(r.toFixed(2));
  }
  updateSpeedUi(fromZoukButton);
}

function enableZoukSpeed() {
  // House fork: the slow "Zouk speed" playback preset is disabled (inert stub).
  state.zoukSpeedOn = false;
}

function enableNormalSpeed() {
  state.zoukSpeedOn = false;
  applyPlaybackRate(1);
  setStatus("Playback at original speed");
}

function setHalfBpm(on) {
  state.halfBpm = Boolean(on);
  const halfBtn = $("halfBpmBtn");
  if (halfBtn) halfBtn.classList.toggle("active", state.halfBpm);
  // Re-render meta so the BPM badge shows halved / full.
  const track = currentTrack();
  if (track) updatePlayerMetaOnly(track);
  if (state.zoukSpeedOn || state.playbackRate < 0.98) {
    enableZoukSpeed();
  } else {
    updateSpeedUi();
    if (state.halfBpm) {
      const raw = trackBpm(track);
      const half = sourceBpm(track);
      setStatus(
        half && raw
          ? `Source BPM halved: VDJ ${raw.toFixed(0)} → ${half.toFixed(0)} (toggle off or press H)`
          : "½ BPM on — no VDJ BPM on this track"
      );
    } else {
      setStatus("Using full VDJ BPM");
    }
  }
}

function toggleHalfBpm() {
  setHalfBpm(!state.halfBpm);
}

function updateSpeedUi() {
  const track = currentTrack();
  const raw = trackBpm(track);
  const bpm = sourceBpm(track);
  const rate = state.playbackRate || 1;
  const bpmBadge = $("bpmBadge");
  const rateBadge = $("rateBadge");
  const hint = $("speedHint");
  const zoukBtn = $("zoukSpeedBtn");
  const halfBtn = $("halfBpmBtn");

  if (halfBtn) halfBtn.classList.toggle("active", state.halfBpm);

  if (bpmBadge) {
    if (bpm && raw) {
      const effective = bpm * rate;
      bpmBadge.textContent = state.halfBpm
        ? `${raw.toFixed(0)}÷2=${bpm.toFixed(0)} → ${effective.toFixed(0)}`
        : `${bpm.toFixed(0)} → ${effective.toFixed(0)} BPM`;
      bpmBadge.className =
        state.halfBpm || rate < 0.98 ? "badge ok" : "badge neutral";
      bpmBadge.title = state.halfBpm
        ? `VDJ reported ${raw.toFixed(1)}; treating as ${bpm.toFixed(1)} (half)`
        : `VDJ BPM ${raw.toFixed(1)}`;
    } else {
      bpmBadge.textContent = "BPM unknown";
      bpmBadge.className = "badge warn";
      bpmBadge.title = "";
    }
  }
  if (rateBadge) {
    rateBadge.textContent = `${rate.toFixed(2)}×`;
    rateBadge.className = rate < 0.98 ? "badge ok" : "badge neutral";
  }
  if (hint) {
    if (Math.abs(rate - 1) < 0.01) {
      hint.textContent = state.halfBpm
        ? "Native speed · ½ BPM on"
        : "Native speed";
    } else if (bpm) {
      hint.textContent = state.halfBpm
        ? `Slowed from ½ BPM (~${(bpm * rate).toFixed(0)} BPM feel)`
        : `Slowed playback (~${(bpm * rate).toFixed(0)} BPM)`;
    } else {
      hint.textContent = `Playback rate ${rate.toFixed(2)}×`;
    }
  }
  if (zoukBtn) {
    zoukBtn.classList.toggle("active", state.zoukSpeedOn || rate < 0.98);
  }
  const target = Number($("targetBpmInput")?.value) || state.targetBpm || 75;
  document.querySelectorAll(".speed-preset[data-target-bpm]").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.targetBpm) === target);
  });
}

/* ===== Save status: every database write shows Saved / FAILED: reason ===== */
const SAVE_TRACKED_RE =
  /^\/api\/(set-cue-color|delete-cue|rename-poi|move-poi|scale-loop|add-cue|add-loop|set-beatgrid|halve-bpm|notes|sort)(\?|$)/;

state.failedEdits = state.failedEdits || [];
{
  const kept = loadPersistedFailedEdits();
  if (kept.length && !state.failedEdits.length) {
    state.failedEdits = kept;
    setTimeout(() => { try { renderSaveBadges(); } catch {} }, 600);
    // Edits kept from before a reload/outage: try them again by themselves once the page is up.
    setTimeout(() => { try { autoRetryFailedEdits(); } catch {} }, 3500);
  }
}

function isSaveTracked(path, options) {
  const method = String((options && options.method) || "GET").toUpperCase();
  return method === "POST" && SAVE_TRACKED_RE.test(String(path || ""));
}

function saveEditInfo(path, options) {
  let body = {};
  try {
    body = JSON.parse((options && options.body) || "{}") || {};
  } catch {
    body = {};
  }
  const verb =
    {
      "set-cue-color": "color",
      "delete-cue": "delete",
      "rename-poi": "rename",
      "move-poi": "move",
      "scale-loop": "loop size",
      "add-cue": "add cue",
      "add-loop": "add loop",
      "set-beatgrid": "beatgrid",
      "halve-bpm": "BPM",
      notes: "notes",
      sort: "copy to House",
    }[String(path).replace(/^\/api\//, "").split("?")[0]] || "edit";
  const file = String(body.path || "").split("/").pop() || "";
  const what = body.name || body.marker_name || "";
  return {
    apiPath: String(path).split("?")[0],
    bodyText: (options && options.body) || "{}",
    label: `${verb}${what ? ` “${what}”` : ""}${file ? ` · ${file}` : ""}`,
    trackPath: body.path || "",
  };
}

const NETWORK_MSG_RE = /failed to fetch|fetch failed|networkerror|network error|load failed|timed out|not reachable|unreachable|can't be reached|econnre/i;
function isNetworkishError(err) {
  const st = Number(err && err.status);
  return Boolean(
    (err && err.network) || st === 502 || st === 503 || st === 504 || NETWORK_MSG_RE.test(String((err && err.message) || err || ""))
  );
}

function saveFailureReason(err) {
  const msg = String((err && err.message) || err || "unknown error");
  if (isNetworkishError(err)) {
    return "The server can't be reached right now. Your change is kept on screen and is saved by itself as soon as it is back - nothing to do.";
  }
  return msg.replace(/^FAILED:\s*/i, "");
}

/* ===== Server watch: calm "unreachable, retrying" banner + automatic retry with backoff ===== */
const serverWatch = { down: false, tries: 0, timer: null, tick: null, nextAt: 0, probing: false };

function ensureServerBanner() {
  let el = document.getElementById("serverBanner");
  if (el) return el;
  el = document.createElement("div");
  el.id = "serverBanner";
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");
  el.style.cssText =
    "position:fixed;left:50%;top:10px;transform:translateX(-50%);z-index:2147483000;background:#1b3a5c;color:#fff;border:1px solid #6aa7e8;border-radius:8px;padding:8px 14px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 2px 12px #0008";
  document.body.appendChild(el);
  return el;
}

function renderServerBanner() {
  if (!serverWatch.down) {
    document.getElementById("serverBanner")?.remove();
    return;
  }
  const el = ensureServerBanner();
  const secs = Math.max(0, Math.ceil((serverWatch.nextAt - Date.now()) / 1000));
  const n = (state.failedEdits || []).length;
  el.textContent =
    `Server unreachable — retrying ${serverWatch.probing ? "now…" : `in ${secs}s`}` +
    (n ? ` · ${n} edit${n === 1 ? " is" : "s are"} kept and will be saved when it is back` : " · nothing is lost");
}

function noteServerDown() {
  if (serverWatch.down) return;
  serverWatch.down = true;
  serverWatch.tries = 0;
  scheduleServerProbe();
}

function scheduleServerProbe() {
  clearTimeout(serverWatch.timer);
  clearInterval(serverWatch.tick);
  const wait = Math.min(30000, 2000 * Math.pow(1.6, serverWatch.tries));
  serverWatch.nextAt = Date.now() + wait;
  serverWatch.timer = setTimeout(probeServer, wait);
  serverWatch.tick = setInterval(renderServerBanner, 500);
  renderServerBanner();
}

async function probeServer() {
  serverWatch.probing = true;
  serverWatch.tries += 1;
  renderServerBanner();
  let up = false;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 5000);
    const r = await fetch("/api/ping", { cache: "no-store", signal: ctl.signal });
    clearTimeout(t);
    up = r.ok;
  } catch {
    up = false;
  }
  serverWatch.probing = false;
  if (!up) {
    scheduleServerProbe();
    return;
  }
  clearInterval(serverWatch.tick);
  serverWatch.down = false;
  renderServerBanner();
  quickConfirm("Server is back");
  await autoRetryFailedEdits();
  try {
    const keep = currentTrack()?.path;
    if (!(state.tracks || []).length && !isStemsMode()) {
      location.reload(); // the first load never succeeded: start over (the same song reopens)
      return;
    }
    if (keep && !isStemsMode()) await loadTracks({ keepPath: keep, skipStatus: true, silent: true });
  } catch {
    /* the quiet reload is best effort */
  }
}

/* Failed edits whose cause was the network/server being away are retried by themselves, oldest first. */
async function autoRetryFailedEdits() {
  const todo = (state.failedEdits || []).filter((f) => f && f.apiPath && (f.retryable || NETWORK_MSG_RE.test(String(f.reason || ""))));
  for (const f of todo) {
    if (!(state.failedEdits || []).includes(f)) continue;
    await retryFailedEdit(f);
  }
}

/* The open song survives a reload / reconnect: remember its FilePath and reopen exactly that one. */
const OPEN_SONG_KEY = "ms.openSong.v1";
function rememberOpenSong(path, mode) {
  if (!path) return;
  const val = JSON.stringify({ path, mode: mode || state.mode, at: Date.now() });
  try {
    sessionStorage.setItem(OPEN_SONG_KEY, val);
  } catch {
    /* ignore */
  }
  try {
    localStorage.setItem(OPEN_SONG_KEY, val);
  } catch {
    /* ignore */
  }
}
function storedOpenSong(mode) {
  for (const store of [() => sessionStorage, () => localStorage]) {
    try {
      const raw = JSON.parse(store().getItem(OPEN_SONG_KEY) || "null");
      if (raw && raw.path && (!raw.mode || raw.mode === mode)) return raw.path;
    } catch {
      /* try next */
    }
  }
  return "";
}

function ensureSaveBadgeHost() {
  let host = document.getElementById("saveBadgeHost");
  if (host) return host;
  host = document.createElement("div");
  host.id = "saveBadgeHost";
  host.setAttribute("role", "status");
  host.setAttribute("aria-live", "polite");
  host.style.cssText =
    "position:fixed;right:12px;bottom:12px;z-index:99999;max-width:460px;font:13px/1.35 system-ui,sans-serif;display:flex;flex-direction:column;gap:6px;align-items:flex-end";
  document.body.appendChild(host);
  return host;
}

function persistFailedEdits() {
  try {
    // A failed COPY is not an edit to restore: it is answered on the spot and the button is simply pressed again.
    localStorage.setItem("ms.failedEdits.v1", JSON.stringify((state.failedEdits || []).filter((f) => f && f.apiPath !== "/api/sort").slice(-50)));
  } catch {}
}

function loadPersistedFailedEdits() {
  try {
    const raw = JSON.parse(localStorage.getItem("ms.failedEdits.v1") || "[]");
    return Array.isArray(raw) ? raw.filter((f) => f && f.apiPath !== "/api/sort") : [];
  } catch {
    return [];
  }
}

/* Re-send a failed edit exactly as it was; it stays in the list (and on screen) until it saves. */
async function retryFailedEdit(f) {
  if (!f || !f.apiPath) return;
  state.failedEdits = (state.failedEdits || []).filter((e) => e !== f);
  persistFailedEdits();
  renderSaveBadges();
  setStatus(`Retrying: ${f.label}…`);
  try {
    await enqueueTrackEdit(f.path || "", () => api(f.apiPath, { method: "POST", body: f.bodyText }));
    setStatus(`Saved after retry: ${f.label}`, "success");
    if (f.path) reconcileSoon(f.path);
  } catch {
    /* api() already put it back in the failed list with the new reason */
  }
}

/** One quick, calm confirmation the moment an edit is on screen (the save runs behind it). */
function quickConfirm(text) {
  state.quickConfirm = { text: String(text), at: Date.now() };
  clearTimeout(quickConfirm._t);
  quickConfirm._t = setTimeout(() => {
    state.quickConfirm = null;
    renderSaveBadges();
  }, 4000);
  renderSaveBadges();
}

/** Loud notice for things that did NOT happen (a click must never be silent). Also mirrored in the status bar. */
function loudNotice(text, kind = "error", action = null) {
  state.loudNotice = { text: String(text), kind, at: Date.now(), action };
  clearTimeout(loudNotice._t);
  loudNotice._t = setTimeout(() => {
    state.loudNotice = null;
    renderSaveBadges();
  }, 9000);
  try {
    setStatus(String(text), kind === "error" ? "error" : "");
  } catch {
    /* ignore */
  }
  renderSaveBadges();
}

function renderSaveBadges() {
  const host = ensureSaveBadgeHost();
  host.replaceChildren();
  persistFailedEdits();
  const pendingN = pendingEditTotal();
  if (state.loudNotice) {
    const ln = document.createElement("div");
    ln.className = `save-badge loud ${state.loudNotice.kind}`;
    ln.style.cssText = `background:${state.loudNotice.kind === "error" ? "#7a1111" : "#5c4a12"};color:#fff;padding:8px 10px;border-radius:6px;box-shadow:0 2px 10px #000a;border:2px solid ${state.loudNotice.kind === "error" ? "#ff6b6b" : "#ffd666"}`;
    ln.textContent = state.loudNotice.text;
    const act = state.loudNotice.action;
    if (act && act.label && typeof act.onClick === "function") {
      const ab = document.createElement("button");
      ab.type = "button";
      ab.className = "loud-notice-action";
      ab.textContent = act.label;
      ab.style.cssText = "display:block;margin-top:8px;padding:6px 12px;border-radius:6px;border:1px solid #fff;background:#fff;color:#7a1111;font-weight:650;cursor:pointer";
      ab.addEventListener("click", () => {
        state.loudNotice = null;
        renderSaveBadges();
        act.onClick();
      });
      ln.appendChild(ab);
    }
    host.appendChild(ln);
  }
  if (state.quickConfirm) {
    const qc = document.createElement("div");
    qc.className = "save-badge quick-confirm";
    qc.style.cssText =
      "background:#0f5132;color:#fff;padding:6px 10px;border-radius:6px;box-shadow:0 2px 8px #0006";
    qc.textContent = `✓ ${state.quickConfirm.text}`;
    host.appendChild(qc);
  }
  if (pendingN > 0) {
    const sv = document.createElement("div");
    sv.className = "save-badge saving";
    sv.style.cssText =
      "background:#1b3a5c;color:#fff;padding:6px 10px;border-radius:6px;box-shadow:0 2px 8px #0006";
    sv.textContent = `Saving ${pendingN} edit${pendingN === 1 ? "" : "s"}… (already on screen)`;
    host.appendChild(sv);
  } else if (state.lastAllSavedAt && !(state.failedEdits || []).length) {
    const all = document.createElement("div");
    all.className = "save-badge saved all-saved";
    all.style.cssText =
      "background:#0f5132;color:#fff;padding:6px 10px;border-radius:6px;box-shadow:0 2px 8px #0006";
    all.textContent = `All changes saved ✓ ${new Date(state.lastAllSavedAt).toLocaleTimeString()}`;
    host.appendChild(all);
  }
  if (state.lastSavedBadge && pendingN === 0 && !state.lastAllSavedAt) {
    const ok = document.createElement("div");
    ok.className = "save-badge saved";
    ok.style.cssText =
      "background:#0f5132;color:#fff;padding:6px 10px;border-radius:6px;box-shadow:0 2px 8px #0006";
    ok.textContent = `Saved ✓ ${state.lastSavedBadge}`;
    host.appendChild(ok);
  }
  // R-99: network/autosave edits are kept for retry (server banner) but do not raise the red "N edits NOT saved" badge.
  const fails = (state.failedEdits || []).filter((f) => f && !f.network && !f.retryable);
  if (!fails.length) return;
  const box = document.createElement("div");
  box.className = "save-badge failed";
  box.style.cssText =
    "background:#7a1111;color:#fff;padding:8px 10px;border-radius:6px;box-shadow:0 2px 10px #000a;border:2px solid #ff6b6b;max-height:50vh;overflow:auto";
  const head = document.createElement("div");
  head.style.cssText = "font-weight:700;display:flex;gap:8px;justify-content:space-between;align-items:center";
  head.textContent = `${fails.length} edit${fails.length === 1 ? "" : "s"} NOT saved — kept here until you retry or dismiss`;
  const clear = document.createElement("button");
  clear.type = "button";
  clear.textContent = "Dismiss all (discard)";
  clear.addEventListener("click", () => {
    state.failedEdits = [];
    renderSaveBadges();
  });
  head.appendChild(clear);
  box.appendChild(head);
  fails
    .slice()
    .reverse()
    .forEach((f) => {
      const row = document.createElement("div");
      row.style.cssText = "margin-top:6px;border-top:1px solid #ffffff44;padding-top:4px";
      row.textContent = `Not saved yet: ${f.reason}  — ${f.label} (${f.at})`;
      if (f.apiPath) {
        const rt = document.createElement("button");
        rt.type = "button";
        rt.className = "save-retry";
        rt.textContent = "Retry";
        rt.style.marginLeft = "8px";
        rt.addEventListener("click", () => retryFailedEdit(f));
        row.appendChild(rt);
      }
      const x = document.createElement("button");
      x.type = "button";
      x.textContent = "✕";
      x.title = "Dismiss";
      x.style.marginLeft = "8px";
      x.addEventListener("click", () => {
        state.failedEdits = state.failedEdits.filter((e) => e !== f);
        renderSaveBadges();
      });
      row.appendChild(x);
      box.appendChild(row);
    });
  host.appendChild(box);
}

function reportSave(ok, info, reason, retryable = false) {
  if (ok) {
    state.lastSavedBadge = info.label;
    renderSaveBadges();
    clearTimeout(state.savedBadgeTimer);
    state.savedBadgeTimer = setTimeout(() => {
      state.lastSavedBadge = "";
      renderSaveBadges();
    }, 5000);
    return;
  }
  // R-98: a failed COPY has its own calm loudNotice (Grid is correct / pick a folder). Never also raise the
  // generic red "1 edit NOT saved" badge for /api/sort — that badge is for cue/loop/color/notes edits only.
  if (info.apiPath === "/api/sort") {
    // Drop any leftover copy entries; the loudNotice is the only UI for this failure.
    state.failedEdits = (state.failedEdits || []).filter((f) => !(f && f.apiPath === "/api/sort"));
    persistFailedEdits();
    renderSaveBadges();
    return;
  }
  const isNet = /can't be reached right now/.test(String(reason || "")) || Boolean(retryable);
  state.failedEdits.push({
    label: info.label,
    reason,
    at: new Date().toLocaleTimeString(),
    path: info.trackPath,
    apiPath: info.apiPath,
    bodyText: info.bodyText,
    retryable: Boolean(retryable),
    network: isNet,
  });
  renderSaveBadges();
  // R-99: offline/autosave failures stay calm (server banner + quiet status). No red "was not changed" / error style.
  setStatus(isNet ? String(reason || "") : `Not saved yet: ${reason}`, isNet ? "" : "error");
  if (retryable) noteServerDown();
  // The edit stays on screen AND in the NOT-saved list (with Retry). It is no longer "reverted" by reloading the
  // song list: that reload re-fetched the stored (original) cues for EVERY song and wiped all newer edits.
}

/* A failed save that reportSave() already recorded; callers keep their optimistic value when err.keptFailedEdit. */
function failedSaveError(reason) {
  // R-99: no "FAILED:" / "[error]" prefix — callers and toasts stay calm ("Not saved yet…").
  const e = new Error(String(reason || "not saved"));
  e.keptFailedEdit = true;
  return e;
}

const CUE_EDIT_WRITE_RE = /^\/api\/(set-cue-color|delete-cue|rename-poi|move-poi|scale-loop|add-cue|add-loop)(\?|$)/;
function requestedDryRun(options) {
  try {
    return JSON.parse((options && options.body) || "{}").dry_run === true;
  } catch {
    return false;
  }
}

async function api(path, options = {}) {
  if (!isSaveTracked(path, options)) return apiRaw(path, options);
  const info = saveEditInfo(path, options);
  let data;
  try {
    data = await apiRaw(path, options);
  } catch (err) {
    const reason = saveFailureReason(err);
    if (/rename-poi/.test(String(path)) && /already (exists|in use|used|taken)/i.test(reason)) {
      // A name clash is not a failed save: no red toast, no failed-edit entry; the caller adapts.
      const soft = new Error(reason);
      soft.softConflict = true;
      throw soft;
    }
    if (/delete-cue/.test(String(path)) && Number(err && err.status) === 404) {
      // The marker is not in the saved song (already gone, or it never was this song's): the song now has no
      // such marker. That is not a failed save - calm message, no red box, no Retry.
      const calm = /no matching/i.test(reason)
        ? "That marker was already gone from the saved song - nothing was deleted."
        : `Nothing deleted: ${reason}`;
      return { ok: true, already_gone: true, result: { already_gone: true, note: calm } };
    }
    reportSave(false, info, reason, isNetworkishError(err));
    throw failedSaveError(reason);
  }
  const dry = Boolean(data && (data.dry_run || (data.result && data.result.dry_run)));
  if (dry && CUE_EDIT_WRITE_RE.test(String(path)) && !requestedDryRun(options)) {
    // A read-only build answers marker edits with dry_run:true and writes nothing. That is not a save.
    const reason = "nothing was written: the server only did a dry run (read-only build?). The edit exists on this screen only.";
    reportSave(false, info, reason);
    throw failedSaveError(reason);
  }
  if (data && data.saved === false) {
    const reason = data.save_reason || "the server reported saved=false";
    reportSave(false, info, reason);
    throw failedSaveError(reason);
  }
  if (data && data.saved === true && !dry) reportSave(true, info, "");
  return data;
}

async function apiRaw(path, options = {}) {
  const timeoutMs = Number(options.timeoutMs || 0);
  const extra = { ...options };
  delete extra.timeoutMs;
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timer =
    controller && timeoutMs > 0
      ? setTimeout(() => controller.abort(), timeoutMs)
      : null;
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(extra.headers || {}) },
      ...extra,
      signal: extra.signal || controller?.signal,
    });
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      const detail = data?.detail || res.statusText || "Request failed";
      const httpErr = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      httpErr.status = res.status;
      httpErr.http = true;
      throw httpErr;
    }
    return data;
  } catch (err) {
    if (err?.name === "AbortError") {
      if (extra.signal && extra.signal.aborted) throw err; // the caller cancelled it (song switch): not an outage
      const te = new Error("Request timed out — is Music Sorter still running?");
      te.network = true;
      noteServerDown();
      throw te;
    }
    if (!err?.http && (err instanceof TypeError || NETWORK_MSG_RE.test(String(err?.message || "")))) {
      err.network = true;
      noteServerDown();
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function currentTrack() {
  return MusicSorterState.currentTrack();
}

function trackReadinessStatus(track) {
  return MusicSorterState.trackReadinessStatus(track);
}

function trackIsKirillApproved(track) {
  return Boolean(track && track.set_approved);
}

function trackIsMustPlay(track) {
  return Boolean(track && track.must_play);
}

function applyApprovedPaths(paths) {
  const set = new Set((paths || []).map(String));
  for (const t of state.tracks || []) {
    if (t && t.path && set.has(String(t.path))) t.set_approved = true;
  }
}

function applyMustPlayPaths(paths) {
  const set = new Set((paths || []).map(String));
  for (const t of state.tracks || []) {
    if (t && t.path && set.has(String(t.path))) t.must_play = true;
  }
}

function markTrackMustPlay(track) {
  if (!track || !track.path) return;
  for (const t of state.tracks || []) {
    if (t && t.path === track.path) t.must_play = true;
  }
  track.must_play = true;
}

function markTrackKirillApproved(track) {
  if (!track || !track.path) return;
  for (const t of state.tracks || []) {
    if (t && t.path === track.path) {
      t.set_approved = true;
      t.readiness = { ...(t.readiness || {}), status: "approved", label: "Approved", set_approved: true };
    }
  }
  track.set_approved = true;
  track.readiness = { ...(track.readiness || {}), status: "approved", label: "Approved", set_approved: true };
}

function showSetApprovedFilter() {
  state.setApprovalFilter = "approved";
  document.querySelectorAll("#setApprovalFilter button").forEach((b) => {
    const val = b.getAttribute("data-set-approval") || b.dataset.setApproval;
    b.classList.toggle("active", val === "approved");
  });
}

function addCuesReadinessRank(track) {
  return MusicSorterState.addCuesReadinessRank(track);
}

function sortAddCuesIndexes(indexes) {
  return MusicSorterState.sortAddCuesIndexes(indexes);
}

function trackMatchesSearch(track, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  const hay = [
    track.name,
    track.relative_path,
    track.group,
    trackDisplayTitle(track),
    trackDisplayArtist(track),
    track.cues?.title,
    track.cues?.author,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  // All whitespace-separated tokens must match (order-independent).
  return q.split(/\s+/).filter(Boolean).every((tok) => hay.includes(tok));
}

function persistCrateFilter(value) {
  const next =
    value === "pajamathon" || value === "inbox" || value === "cueing" ? value : "all";
  state.crateFilter = next;
  try {
    localStorage.setItem("addCuesCrateFilter", next);
  } catch {
    /* ignore */
  }
}

function loadCrateFilter() {
  try {
    const stored = localStorage.getItem("addCuesCrateFilter");
    if (
      stored === "pajamathon" ||
      stored === "inbox" ||
      stored === "cueing" ||
      stored === "all"
    ) {
      state.crateFilter = stored;
    }
  } catch {
    /* ignore */
  }
}

function syncCrateFilterUi() {
  document.querySelectorAll("#crateFilter button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.crate === (state.crateFilter || "all"));
  });
}

function syncReadinessFilterLabels() {
  const readyBtn = $("filterReadyBtn");
  if (readyBtn) {
    readyBtn.textContent = "Ready";
    readyBtn.title = "Looks ready to Move to Ready for Sort";
  }
  document.querySelectorAll('#readinessFilter [data-filter="needs_review"]').forEach((btn) => {
    btn.hidden = inSet;
    btn.title = "Has cues, not ready to promote yet";
  });
}

function setCrateFilter(value) {
  persistCrateFilter(value);
  syncCrateFilterUi();
  syncReadinessFilterLabels();
  applyModeUi();
  const indexes = filteredTrackIndexes();
  if (indexes.length && !indexes.includes(state.index)) {
    state.index = indexes[0];
    renderPlayer();
  }
  renderTrackList();
  updateBatchAddCuesButton();
  updatePipelineStrip();
}

function addCuesSection(track) {
  return MusicSorterState.addCuesSection(track);
}

function isPajamathonSetQueueTrack(track) {
  const placements =
    (typeof globalThis !== "undefined" && globalThis.MusicSorterPlacements) ||
    (typeof window !== "undefined" && window.MusicSorterPlacements) ||
    {};
  if (typeof placements.trackIsPajamathonSetFile === "function") {
    return placements.trackIsPajamathonSetFile(track);
  }
  const path = String((track && track.path) || "").replace(/\\/g, "/");
  return /\/sets\/pajamathon/i.test(path);
}

function filteredTrackIndexes() {
  const q = (state.trackSearch || "").trim();
  const indexes = state.tracks
    .map((t, i) => i)
    .filter((i) => {
      const track = state.tracks[i];
      if (!trackMatchesSearch(track, q)) return false;
      if (isReviewMode() && isHouseProfile()) {
        if (state.houseCrate && String(track.group || "") !== state.houseCrate) return false;
        if (!MusicSorterState.bpmInRange(track, state.bpmMin, state.bpmMax)) return false;
      }
      if (isSetOverviewMode()) {
        if (trackInMustPlayFolder(track)) return false;
        if (!trackMatchesSetDir(track)) return false;
        const approval = state.setApprovalFilter || "all";
        if (approval === "all") return true;
        if (approval === "needs_sort") {
          return Boolean(track.is_cued) && !track.lane;
        }
        const approved = trackIsKirillApproved(track);
        if (approval === "approved") return approved;
        if (approval === "not_approved") return !approved;
        return true;
      }
      if (isReviewMode() && !isHouseProfile() && state.crateFilter && state.crateFilter !== "all") {
        if (state.crateFilter === "cueing") {
          if (!isTrackCueing(track)) return false;
        } else if (addCuesSection(track) !== state.crateFilter) {
          return false;
        }
      }
      if (
        isReviewMode() &&
        (!state.crateFilter || state.crateFilter === "all") &&
        addCuesSection(track) === "in_set"
      ) {
        return false;
      }
      if (!isReviewMode() || state.readinessFilter === "all") return true;
      if (state.readinessFilter === "needs_sort") {
        // Cued VDJ-white only: no proper lane/genre yet. Do not relocate to fill this.
        return Boolean(track.is_cued) && !track.lane;
      }
      if (state.readinessFilter === "recently_cued") {
        return MusicSorterState.isRecentlyCued(
          track,
          retryJobForPath(track?.path)
        );
      }
      if (
        state.readinessFilter === "retried_cues" ||
        state.readinessFilter === "retried_loops" ||
        state.readinessFilter === "retried_both"
      ) {
        const want = state.readinessFilter.replace("retried_", "");
        return trackRetryKind(track) === want;
      }
      const status = trackReadinessStatus(track);
      if (!status) return false;
      if (state.readinessFilter === "ready" || state.readinessFilter === "approved") {
        return status === "ready";
      }
      if (state.readinessFilter === "needs_review") {
        return (
          status === "needs_review" ||
          status === "partial" ||
          (Boolean(track.is_cued) && status !== "ready" && status !== "approved")
        );
      }
      if (state.readinessFilter === "partial") return status === "partial";
      if (state.readinessFilter === "not_cued") {
        return status === "not_cued" || status === "missing";
      }
      return true;
    });
  if (isReviewMode() && state.readinessFilter === "recently_cued") {
    return indexes.slice().sort((a, b) => {
      const tb = MusicSorterState.trackLastCuedMs(
        state.tracks[b],
        retryJobForPath(state.tracks[b]?.path)
      );
      const ta = MusicSorterState.trackLastCuedMs(
        state.tracks[a],
        retryJobForPath(state.tracks[a]?.path)
      );
      return tb - ta;
    });
  }
  if (isReviewMode() && isHouseProfile()) {
    return MusicSorterState.sortHouseIndexes(
      indexes,
      state.houseSortKey || "default",
      state.houseSortDir || "asc"
    );
  }
  return isReviewMode() ? sortAddCuesIndexes(indexes) : indexes;
}

function trackRetryKind(track) {
  return MusicSorterState.trackRetryKind(track, retryJobForPath(track?.path));
}

function pointTimesForMatch(points, kind) {
  return (points || [])
    .filter((p) => pointKind(p) === kind)
    .map((p) => Number(p.pos) || 0)
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
}

function pairCueTimes(a, b, window) {
  const used = new Set();
  let hits = 0;
  let maxDelta = 0;
  for (const t of a) {
    let best = -1;
    let bestD = Infinity;
    for (let i = 0; i < b.length; i++) {
      if (used.has(i)) continue;
      const d = Math.abs(t - b[i]);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    if (best >= 0 && bestD <= window) {
      used.add(best);
      hits += 1;
      maxDelta = Math.max(maxDelta, bestD);
    }
  }
  return { hits, maxDelta };
}

function placementMatchBadge(track, hit) {
  const actual = track?.cues?.points || [];
  const written = hit?.points;
  if (!actual.length) return "";
  if (!Array.isArray(written)) return "";
  const bpm = Number(track?.cues?.bpm || hit?.bpm || 0);
  const bar = bpm > 0 ? (60 / bpm) * 4 : 2;
  const actC = pointTimesForMatch(actual, "cue");
  const wrC = pointTimesForMatch(written, "cue");
  const actL = pointTimesForMatch(actual, "loop");
  const wrL = pointTimesForMatch(written, "loop");
  if (!wrC.length && !wrL.length) {
    return `<span class="autocue-flag different" title="This copy has no cues">different</span>`;
  }
  const c = pairCueTimes(actC, wrC, bar);
  const l = pairCueTimes(actL, wrL, bar);
  const counts = actC.length === wrC.length && actL.length === wrL.length;
  const paired = c.hits === actC.length && l.hits === actL.length;
  const maxD = Math.max(c.maxDelta, l.maxDelta);
  if (counts && paired && maxD <= 0.08) {
    return `<span class="autocue-flag same" title="Matches this copy">same</span>`;
  }
  if (counts && paired) {
    return `<span class="autocue-flag same" title="Same cues, within a bar">same</span>`;
  }
  return `<span class="autocue-flag different" title="Does not match this copy">different</span>`;
}

function syncPlacementHitFromTrack(track, destPath) {
  if (!track?.placements || !destPath) return;
  const points = (track.cues?.points || []).map((p) => ({ ...p }));
  const cueN =
    Number(track.cues?.cue_count) ||
    points.filter((p) => pointKind(p) === "cue").length;
  const loopN =
    Number(track.cues?.loop_count) ||
    points.filter((p) => pointKind(p) === "loop").length;
  for (const key of ["library", "cues_sorted", "sets"]) {
    const list = track.placements[key];
    if (!Array.isArray(list)) continue;
    const hit = list.find((h) => h.path === destPath);
    if (!hit) continue;
    hit.points = points;
    hit.cue_count = cueN;
    hit.loop_count = loopN;
    hit.is_cued = Boolean(track.is_cued || cueN > 0);
    hit.in_database = true;
  }
}

function refreshPlacementMatchUi(track) {
  const current = track || currentTrack();
  if (!current) return;
  if (typeof renderPlacementCard === "function") {
    try {
      renderPlacementCard(current);
    } catch {
      /* ignore during boot */
    }
  }
}

function autocueMatchBadge(track) {
  const m = track?.autocue_match;
  const matched = Boolean(track?.autocue_matches ?? m?.matches);
  if (!m || !m.status || m.status === "not_cued" || m.status === "unknown") {
    return "";
  }
  const title = escapeHtml(m.reason || "");
  if (matched) {
    return `<span class="autocue-flag same" title="${title}">same</span>`;
  }
  if (m.status === "no_proposal" && !track?.is_cued) {
    return "";
  }
  return `<span class="autocue-flag different" title="${title}">different</span>`;
}

function retryHistoryBadge(track) {
  const kind = trackRetryKind(track);
  if (kind === "both") {
    return `<span class="badge retry-hist" title="AutoCue already ran cues and loops">Tried both</span>`;
  }
  if (kind === "cues") {
    return `<span class="badge retry-hist" title="AutoCue already ran cues only">Retried cues</span>`;
  }
  if (kind === "loops") {
    return `<span class="badge retry-hist" title="AutoCue already ran loops only">Retried loops</span>`;
  }
  return "";
}

function updateRetriedFilterUi() {
  const counts = state.tracks.reduce(
    (acc, track) => {
      const kind = trackRetryKind(track);
      if (kind === "cues") acc.cues += 1;
      else if (kind === "loops") acc.loops += 1;
      else if (kind === "both") acc.both += 1;
      return acc;
    },
    { cues: 0, loops: 0, both: 0 }
  );
  const cuesBtn = $("filterRetriedCues");
  const loopsBtn = $("filterRetriedLoops");
  const bothBtn = $("filterRetriedBoth");
  if (cuesBtn) {
    cuesBtn.textContent = counts.cues ? `Retried cues · ${counts.cues}` : "Retried cues";
  }
  if (loopsBtn) {
    loopsBtn.textContent = counts.loops
      ? `Retried loops · ${counts.loops}`
      : "Retried loops";
  }
  if (bothBtn) {
    bothBtn.textContent = counts.both ? `Tried both · ${counts.both}` : "Tried both";
  }
}

function readinessBadge(track) {
  const r = track.readiness;
  if (!r || !r.status) {
    if (isReviewMode()) {
      return track?.is_cued
        ? `<span class="badge warn">Cued · status unknown</span>`
        : `<span class="badge uncued">Not assessed</span>`;
    }
    return "";
  }
  if (r.status === "approved") return `<span class="badge ok">${escapeHtml(r.label)}</span>`;
  if (r.status === "needs_review") return `<span class="badge warn">${escapeHtml(r.label)}</span>`;
  if (r.status === "ready") return `<span class="badge ok">${escapeHtml(r.label)}</span>`;
  if (r.status === "partial") return `<span class="badge warn">${escapeHtml(r.label)}</span>`;
  if (r.status === "missing") return `<span class="badge bad">${escapeHtml(r.label)}</span>`;
  return `<span class="badge uncued">${escapeHtml(r.label)}</span>`;
}

function renderRecsRail() {
  const root = $("trackList");
  if (!root) return;
  const np = state.recsNow;
  if (np?.path) {
    root.innerHTML = `<button type="button" class="track recs-now-rail active" disabled>
      <div class="track-title">${escapeHtml(np.title || np.name || "Now playing")}</div>
      <div class="track-sub">${escapeHtml(np.artist || "VirtualDJ")}</div>
      <div class="track-badges">
        ${np.bpm != null ? `<span class="badge ok">${Number(np.bpm).toFixed(0)} BPM</span>` : ""}
        ${np.key ? `<span class="badge neutral">${escapeHtml(np.key)}</span>` : ""}
        ${np.genre ? `<span class="badge genre">${escapeHtml(np.genre)}</span>` : ""}
      </div>
    </button>`;
  } else {
    root.innerHTML = emptyStateHtml({
      icon: "↻",
      title: "Watching VirtualDJ",
      copy: "Play a track — recs appear here and in VDJ Sideview (Next Recs).",
      ctaLabel: "",
      ctaMode: "",
    });
  }
  updatePipelineStrip();
}

function renderTrackList() {
  updateRetriedFilterUi();
  const root = $("trackList");
  if (isPracticeMode()) {
    renderPracticeMixList();
    return;
  }
  if (isRecsMode()) {
    renderRecsRail();
    return;
  }
  if (isAssembleMode()) {
    renderAssembleRail();
    return;
  }
  if (isStemsMode()) {
    renderStemsRail();
    return;
  }
  const indexes = filteredTrackIndexes();
  if (isSetOverviewMode()) {
    renderSetOverviewList(indexes);
    return;
  }
  if (!state.tracks.length) {
    if (root.classList.contains("list-loading") || state.tracksLoadTimer) {
      root.innerHTML = `<div class="empty">Loading tracks…</div>`;
      return;
    }
    if (isReviewMode()) {
      root.innerHTML = emptyStateHtml({
        icon: "1",
        title: "Add Cues is empty",
        copy:
          state.crateFilter === "pajamathon"
            ? "Add Cues/Pajamathon is empty. Drop tracks here to cue, then Move to Ready and copy into Sets/Pajamathon."
            : "Drop audio into the Add Cues folder, or jump to Sort if Ready already has tracks.",
        ctaLabel: "Open Sort",
        ctaMode: "sort",
      });
    } else {
      root.innerHTML = emptyStateHtml({
        icon: "2",
        title: "Ready for Sort is empty",
        copy: "Approve cued tracks from Add Cues to fill this queue, then copy them into House folders.",
        ctaLabel: "Open Add Cues",
        ctaMode: "add_cues",
      });
    }
    root.querySelectorAll("[data-goto-mode]").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.dataset.gotoMode));
    });
    updatePipelineStrip();
    return;
  }
  if (!indexes.length) {
    if (isReviewMode() && state.crateFilter === "cueing" && !state.trackSearch.trim()) {
      root.innerHTML = emptyStateHtml({
        icon: "↻",
        title: "Nothing cueing",
        copy: "Start AutoCue on a track and it will list here until it finishes.",
        ctaLabel: "",
        ctaMode: "",
      });
      return;
    }
    if (isReviewMode() && isHouseProfile() && houseBpmWindowActive()) {
      root.innerHTML = `<div class="empty house-empty" role="status">
        <strong>No tracks in this BPM range</strong>
        <div class="subtitle">${escapeHtml(houseBpmWindowLabel())}${
          state.houseCrate ? ` in ${escapeHtml(state.houseCrate)}` : ""
        }. Tracks without a VDJ BPM are hidden while a range is set.</div>
        <button type="button" class="btn" id="houseEmptyClearBtn">Clear BPM filter</button>
      </div>`;
      $("houseEmptyClearBtn")?.addEventListener("click", () => {
        state.bpmMin = null;
        state.bpmMax = null;
        applyHouseTools();
      });
      return;
    }
    root.innerHTML = `<div class="empty">${
      state.trackSearch.trim()
        ? "No tracks match this search."
        : "No tracks match this filter."
    }</div>`;
    return;
  }

  const listScrollTop = root.scrollTop;
  const listHtml = isReviewMode()
    ? renderAddCuesTrackSections(indexes)
    : indexes.map((i) => renderQueueTrackRow(i)).join("");
  // R-82d: a redraw that would produce exactly what is already on screen is skipped (no flicker, no lost hover,
  // listeners stay). Only a real change in what a row shows rebuilds the list.
  if (root.__listHtml === listHtml && root.__listFirst && root.__listFirst === root.firstElementChild) {
    return;
  }
  root.innerHTML = listHtml;
  root.__listHtml = listHtml;
  root.__listFirst = root.firstElementChild;
  root.scrollTop = listScrollTop;

  root.querySelectorAll(".track").forEach((btn) => {
    btn.addEventListener("click", () => selectTrack(Number(btn.dataset.index)));
  });
  root.querySelectorAll(".track-autocue-btn").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const track = state.tracks[Number(btn.dataset.index)];
      if (!track) return;
      retryCuesForTrack(track, "all", { fromQueue: true });
    });
  });
}

function writtenCopyBadge(track) {
  const hit = (() => {
    const p = track?.placements || {};
    const src = track?.path;
    const hits = [...(p.add_cues || []), ...(p.cues_sorted || []), ...(p.library || [])].filter(
      (h) => h && h.path && h.path !== src
    );
    return hits.find((h) => h.is_cued) || hits[0] || null;
  })();
  if (hit && typeof placementMatchBadge === "function") {
    const live = placementMatchBadge(track, hit);
    if (live) return live;
  }
  const m = track?.written_match;
  if (!m || !m.status || m.status === "unknown") return "";
  const title = escapeHtml(m.reason || "");
  if (m.matches || m.status === "match" || m.status === "near") {
    return `<span class="autocue-flag same" title="${title}">same</span>`;
  }
  if (m.status === "no_copy") {
    return `<span class="autocue-flag different" title="${title}">no copy</span>`;
  }
  return `<span class="autocue-flag different" title="${title}">different</span>`;
}

/** Cued destinations: Cues Sorted, and (House build) the House library holding the House folders. */
function isCuedDestinationPath(path) {
  return /\/Cues Sorted\//i.test(String(path || "")) || /\/Music\/House\//i.test(String(path || ""));
}

function destFolderFromPlacementRel(relOrPath) {
  const raw = String(relOrPath || "").replace(/\\/g, "/");
  if (!raw) return "";
  let rest = raw;
  const markers = ["/Cues Sorted/", "/Cues/Cues Sorted/", "/Music/House/"];
  const low = rest.toLowerCase();
  for (const marker of markers) {
    const i = low.lastIndexOf(marker.toLowerCase());
    if (i >= 0) {
      rest = rest.slice(i + marker.length);
      break;
    }
  }
  const parts = rest.split("/").filter(Boolean);
  if (!parts.length) return "";
  const last = parts[parts.length - 1];
  if (/\.(flac|mp3|wav|aiff|aif|m4a|ogg)$/i.test(last)) parts.pop();
  return parts.join("/");
}

function setOverviewDestLeaves(track) {
  const p = track?.placements || {};
  const folders = [];
  const seen = new Set();
  const add = (rel) => {
    const raw = String(rel || "");
    if (/low_quality_backups/i.test(raw)) return;
    const folder = destFolderFromPlacementRel(raw);
    if (!folder) return;
    const key = folder.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    folders.push(folder);
  };
  for (const h of p.cues_sorted || []) {
    if (!h || !h.path) continue;
    add(h.relative_path || h.path);
  }
  for (const h of p.library || []) {
    const path = String(h.path || "");
    if (path && !isCuedDestinationPath(path)) continue;
    if (/low_quality_backups/i.test(path)) continue;
    add(h.relative_path || path);
  }
  const bassy = folders.find((f) => String(f).split("/")[0].toLowerCase() === "bassy");
  if (bassy) return [bassy];
  return folders;
}

function renderSetOverviewList(indexes) {
  const root = $("trackList");
  if (!root) return;
  if (typeof renderSetOverviewRail === "function") {
    // Bind rail to the same queue pick this list is about to paint.
    try { renderSetOverviewRail(); } catch (e) { /* rail optional during first paint */ }
  }
  if (!state.tracks.length) {
    root.innerHTML = `<div class="empty">No audio under Sets/Pajamathon.</div>`;
    return;
  }
  if (!indexes.length) {
    root.innerHTML = `<div class="empty">${
      state.trackSearch.trim() ? "No tracks match this search." : "No tracks in this set."
    }</div>`;
    return;
  }
  const listScrollTop = root.scrollTop;
  root.innerHTML = indexes
    .map((i) => {
      const t = state.tracks[i];
      const folder = t.group || t.relative_path || "";
      const destLeaves = setOverviewDestLeaves(t);
      const destCol = destLeaves.length
        ? `<div class="track-sort-dir">Dest · ${escapeHtml(destLeaves.join(" · "))}</div>`
        : "";
      const cueN = Number(t.cues?.cue_count) || 0;
      const loopN = Number(t.cues?.loop_count) || 0;
      const counts = t.is_cued
        ? `<span class="badge ok">${cueN} cues${loopN ? ` · ${loopN} loops` : ""}</span>`
        : `<span class="badge uncued">Not cued</span>`;
      const sib = cuedSiblingHit(t);
      const needsCopy = Boolean(sib) && !trackMatchesWrittenCopy(t, sib);
      const copyBtn = needsCopy
        ? `<button type="button" class="btn ghost set-copy-cues-btn" data-index="${i}" title="Copy cues from the cued sibling onto this set file">Copy cues</button>`
        : "";
      const approved = trackIsKirillApproved(t);
      const mustPlay = trackIsMustPlay(t);
      const selected = i === state.index;
      return `<div class="track-row${selected ? " is-selected" : ""}">
      <button class="track set-overview-row ${
        selected ? "active" : ""
      }" data-index="${i}" type="button" title="${escapeHtml(t.name || "")}">
        <div class="track-title">${escapeHtml(trackDisplayTitle(t))}</div>
        <div class="track-path">${escapeHtml(folder)}</div>
        ${destCol}
        <div class="track-meta">${counts} ${writtenCopyBadge(t)}${
          approved ? ` <span class="badge ok set-ov-approved">Approved</span>` : ""
        }${mustPlay ? ` <span class="badge ok set-ov-must-play">Must Play</span>` : ""}</div>
      </button>
      ${copyBtn}
      </div>`;
    })
    .join("");
  root.scrollTop = listScrollTop;
  root.querySelectorAll(".track").forEach((btn) => {
    btn.addEventListener("click", () => selectTrack(Number(btn.dataset.index)));
  });
  root.querySelectorAll(".set-copy-cues-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      copySetOverviewCues(Number(btn.dataset.index));
    });
  });
}

function cuedSiblingHit(track) {
  const p = track?.placements || {};
  const src = track?.path;
  const hits = [...(p.cues_sorted || []), ...(p.library || [])].filter(
    (h) => h && h.path && h.path !== src && (h.is_cued || Number(h.cue_count) > 0)
  );
  return hits[0] || null;
}

function trackMatchesWrittenCopy(track, hit) {
  if (!hit) return false;
  const html = typeof placementMatchBadge === "function" ? placementMatchBadge(track, hit) : "";
  return html.includes("same");
}

async function copySetOverviewCues(index) {
  const track = state.tracks[index];
  if (!track) return;
  const sib = cuedSiblingHit(track);
  if (!sib) {
    setStatus("No cued sibling to copy from.", "error");
    return;
  }
  try {
    setStatus(`Copying cues onto ${trackDisplayTitle(track)}…`);
    await api("/api/copy-cues", {
      method: "POST",
      body: JSON.stringify({
        source: sib.path,
        dest: track.path,
        overwrite: Boolean(track.is_cued),
        allow_vdj_running: false,
      }),
    });
    syncPlacementHitFromTrack({ ...track, cues: track.cues, is_cued: true, placements: { cues_sorted: [sib], library: [], sets: [] } }, track.path);
    setStatus(`Copied cues onto ${trackDisplayTitle(track)}`, "success");
    await loadTracks({ keepPath: track.path, skipStatus: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}


function dropSetOverviewRow(track, message, kind = "success") {
  const path = track?.path;
  if (path) {
    const prev = state.tracks || [];
    const gone = prev.findIndex((t) => t.path === path);
    state.tracks = prev.filter((t) => t.path !== path);
    if (!state.tracks.length) state.index = 0;
    else if (gone >= 0 && state.index >= gone) {
      state.index = Math.min(state.index, state.tracks.length - 1);
      if (gone === state.index || state.index >= state.tracks.length) {
        state.index = Math.min(gone, state.tracks.length - 1);
      }
    }
  }
  if (message) setStatus(message, kind);
  renderTrackList();
  if (typeof renderSetOverviewRail === "function") renderSetOverviewRail();
  if (typeof updatePipelineStrip === "function") updatePipelineStrip();
  if (typeof renderPlayer === "function") renderPlayer();
}

function isGoneSetFileError(err) {
  const msg = String(err?.message || err || "").toLowerCase();
  return msg.includes("set file not found") || msg.includes("not found");
}

async function sendBackSetOverview() {
  const track = selectedQueueTrack() || currentTrack();
  if (!track) return;
  if (!isPajamathonSetQueueTrack(track)) {
    setStatus("Send-back is only for Sets/Pajamathon copies.", "error");
    return;
  }
  const ok = await showConfirmDialog({
    title: "Send back to Add Cues?",
    track: trackDisplayTitle(track),
    message:
      "This Sets/Pajamathon copy moves to Add Cues / Pajamathon for new cues.",
    note: "Cues Sorted / other siblings stay. No re-AutoCue. Close VirtualDJ first if it is open.",
    confirmLabel: "Send back",
    tone: "warning",
  });
  if (!ok) return;
  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message: "Path changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then send back.", "error");
      return;
    }
  }
  try {
    setStatus(`Sending ${trackDisplayTitle(track)} to Add Cues / Pajamathon…`);
    const data = await api("/api/send-back-set", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const dest = (r.dest_path || "").split("/").slice(-3).join("/");
    const toast = r.already_in_inbox
        ? `Already in Add Cues / Pajamathon · dropped set name · ${dest || track.name}`
        : `Sent back · ${dest || "Add Cues / Pajamathon"}`;
    dropSetOverviewRow(track, toast);
    loadTracks({ keepPath: state.tracks[state.index]?.path, skipStatus: true }).catch(() => {});
  } catch (err) {
    if (isGoneSetFileError(err)) {
      dropSetOverviewRow(track, `Already gone from the set · ${trackDisplayTitle(track)}`);
      return;
    }
    setStatus(err.message, "error");
  }
}

async function removeSetOverviewCopy() {
  const track = selectedQueueTrack() || currentTrack();
  if (!track) return;
  const cueN = track.cues?.cue_count ?? 0;
  const loopN = track.cues?.loop_count ?? 0;
  const ok = await showConfirmDialog({
    title: "Remove this set copy?",
    track: trackDisplayTitle(track),
    message:
      "Deletes this Sets/Pajamathon file only (and its VDJ entry for that path).",
    note: `Cues Sorted / Add Cues siblings stay. ${cueN} cues, ${loopN} loops on this path. Close VirtualDJ first if it is open.`,
    confirmLabel: "Remove copy",
    tone: "danger",
  });
  if (!ok) return;
  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message: "Deleting the database entry while VirtualDJ is open can be overwritten on quit.",
      confirmLabel: "Remove anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then remove.", "error");
      return;
    }
  }
  try {
    setStatus(`Removing ${trackDisplayTitle(track)}…`);
    const data = await api("/api/remove-set-copy", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        to_trash: true,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const linkPart =
      r.kept_hardlinks > 0
        ? ` · kept ${r.kept_hardlinks} sibling${r.kept_hardlinks === 1 ? "" : "s"}`
        : "";
    dropSetOverviewRow(
      track,
      `Removed set copy · ${r.name || track.name}${r.unlink_only ? " (unlinked)" : ""}${linkPart}`
    );
    loadTracks({ keepPath: state.tracks[state.index]?.path, skipStatus: true }).catch(() => {});
  } catch (err) {
    if (isGoneSetFileError(err)) {
      dropSetOverviewRow(track, `Already gone from the set · ${trackDisplayTitle(track)}`);
      return;
    }
    setStatus(err.message, "error");
  }
}

function renderQueueTrackRow(i) {
  const t = state.tracks[i];
  const cued = t.is_cued;
  const loops = t.cues?.loop_count || 0;
  const dotPts = cued ? [...(t.cues?.points || [])].sort((x, y) => (Number(x.pos) || 0) - (Number(y.pos) || 0)) : [];
  const dots = dotPts.length
    ? `<span class="row-cue-dots" aria-hidden="true">${dotPts
        .slice(0, 16)
        .map((p) => `<i class="row-cue-dot ${pointKind(p) === "loop" ? "is-loop" : ""} color-${sanitizeColorName(p.color_name)}"></i>`)
        .join("")}${dotPts.length > 16 ? "<i class=\"row-cue-more\">+</i>" : ""}</span>`
    : "";
  const badge =
    (cued
      ? `<span class="badge ok">${t.cues.cue_count || 0} cues${
          loops ? ` · ${loops} loops` : ""
        }</span>`
      : `<span class="badge uncued">Not cued</span>`) + dots;
  const g = t.grid || t.grid_preflight || {};
  const gridBlocked =
    isReviewMode() &&
    (g.can_autocue === false || g.manual_required || g.status === "blocked")
      ? gridBadge(t)
      : "";
  const cueingBadge = isTrackCueing(t) ? `<span class="badge warn">Cueing</span>` : "";
  const placements = t.placements || {};
  const archHits = placements.cues_sorted || [];
  const cueing = isTrackCueing(t);
  const bpmKeyHtml = trackBpmKeyChips(t);
  const othersLive = activeRetryJobs().some((j) => j.path && j.path !== t.path);
  const queueAutocue =
    isReviewMode() && !cueing
      ? `<button type="button" class="btn ghost track-autocue-btn" data-index="${i}" title="Queue AutoCue — other jobs keep running">${
          othersLive ? "Queue" : "AutoCue"
        }</button>`
      : "";
  return `
        <div class="track-row">
        <button class="track ${i === state.index ? "active" : ""} ${cued ? "" : "uncued-row"} ${
          placements.already_sorted ? "already-sorted-row" : ""
        }"
                data-index="${i}" type="button" title="${escapeHtml(t.name)}">
          <div class="track-title">${escapeHtml(trackDisplayTitle(t))}</div>
          ${
            trackDisplayArtist(t)
              ? `<div class="track-artist">${escapeHtml(trackDisplayArtist(t))}</div>`
              : ""
          }
          ${
            archHits.length
              ? `<div class="track-path">${archHits
                  .map((hit) => escapeHtml(`Cues Sorted/${hit.relative_path}`))
                  .join("<br>")}</div>`
              : ""
          }
          <div class="track-meta">
            ${bpmKeyHtml}
            ${badge}
            ${cueingBadge}
            ${gridBlocked}
          </div>
        </button>
        ${queueAutocue}
        </div>`;
}

function renderAddCuesTrackSections(indexes) {
  if (isHouseProfile()) {
    const label = state.houseCrate || "All Add Cues folders";
    const win = houseBpmWindowActive() ? ` · BPM ${houseBpmWindowLabel()}` : "";
    const sortLbl =
      state.houseSortKey === "bpm"
        ? ` · by BPM ${state.houseSortDir === "desc" ? "↓" : "↑"}`
        : state.houseSortKey === "camelot"
          ? ` · by key ${state.houseSortDir === "desc" ? "↓" : "↑"}`
          : "";
    return `<div class="track-section-head" data-section="house">
        <strong>${escapeHtml(label)}</strong>
        <span class="subtitle">${indexes.length} track${indexes.length === 1 ? "" : "s"}${escapeHtml(win + sortLbl)}</span>
      </div>${indexes.map((i) => renderQueueTrackRow(i)).join("")}`;
  }
  const cueing = indexes.filter((i) => isTrackCueing(state.tracks[i]));
  const rest = indexes.filter((i) => !isTrackCueing(state.tracks[i]));
  const houseNoPaj = isHouseProfile(); // R-99: no "Pajamathon" heading in the House app - those songs are plain inbox songs
  const paj = houseNoPaj
    ? []
    : sortAddCuesIndexes(rest.filter((i) => addCuesSection(state.tracks[i]) === "pajamathon"));
  const inbox = sortAddCuesIndexes(
    rest.filter((i) => {
      const section = addCuesSection(state.tracks[i]);
      return (houseNoPaj ? true : section !== "pajamathon") && section !== "in_set";
    })
  );
  const parts = [];
  const sectionBlock = (id, label, rows) => {
    if (!rows.length) return;
    const need = rows.filter((i) => {
      const status = trackReadinessStatus(state.tracks[i]);
      return status === "not_cued" || status === "missing";
    }).length;
    const sub =
      id === "cueing"
        ? `${rows.length} running`
        : `${need} not cued · ${rows.length}`;
    parts.push(
      `<div class="track-section-head" data-section="${id}">
        <strong>${escapeHtml(label)}</strong>
        <span class="subtitle">${escapeHtml(sub)}</span>
      </div>${rows.map((i) => renderQueueTrackRow(i)).join("")}`
    );
  };
  if (state.crateFilter === "cueing") {
    sectionBlock("cueing", "Currently cueing", cueing);
    return parts.join("");
  }
  sectionBlock("cueing", "Currently cueing", cueing);
  sectionBlock("pajamathon", "Add Cues / Pajamathon", paj);
  sectionBlock("inbox", "Inbox", inbox);
  return parts.join("");
}

/* ===== House fork: crate select, BPM window, BPM / Camelot sort ===== */
function isHouseProfile() {
  return document.body.dataset.profile === "house";
}

function houseBpmWindowActive() {
  return state.bpmMin != null || state.bpmMax != null;
}

function houseBpmWindowLabel() {
  const lo = state.bpmMin != null ? state.bpmMin : "";
  const hi = state.bpmMax != null ? state.bpmMax : "";
  if (lo !== "" && hi !== "") return `${lo}–${hi}`;
  if (lo !== "") return `≥ ${lo}`;
  if (hi !== "") return `≤ ${hi}`;
  return "";
}

function fmtBpm(n) {
  const r = Math.round(Number(n) * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

function trackBpmKeyChips(t) {
  const bpm = MusicSorterState.trackBpmValue(t);
  const key = MusicSorterState.trackCamelot(t);
  return `<span class="track-bpmkey">
      <span class="bpm-chip${bpm == null ? " is-missing" : ""}" aria-label="BPM ${
        bpm == null ? "unknown" : fmtBpm(bpm)
      }" title="${bpm == null ? "No BPM in VirtualDJ" : `${fmtBpm(bpm)} BPM`}">${
        bpm == null ? "—" : escapeHtml(fmtBpm(bpm))
      }<small>BPM</small></span>
      <span class="key-chip${key ? "" : " is-missing"}" aria-label="Camelot key ${
        key || "unknown"
      }" title="${key ? `Camelot ${key}` : "No key in VirtualDJ"}">${key ? escapeHtml(key) : "—"}</span>
    </span>`;
}

function parseBpmField(value) {
  const txt = String(value == null ? "" : value).trim();
  if (!txt) return null;
  const n = Number(txt);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function syncHouseTools() {
  const wrap = $("houseTools");
  if (!wrap) return;
  const show = isReviewMode() && isHouseProfile();
  wrap.hidden = !show;
  if (!show) return;
  const minEl = $("bpmMinInput");
  const maxEl = $("bpmMaxInput");
  if (minEl && document.activeElement !== minEl) minEl.value = state.bpmMin != null ? String(state.bpmMin) : "";
  if (maxEl && document.activeElement !== maxEl) maxEl.value = state.bpmMax != null ? String(state.bpmMax) : "";
  const chip = $("bpmChip115125");
  if (chip) {
    const on = state.bpmMin === 115 && state.bpmMax === 125;
    chip.classList.toggle("active", on);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
  }
  const clear = $("bpmClearBtn");
  if (clear) clear.disabled = !houseBpmWindowActive();
  const sel = $("houseSortKey");
  if (sel) sel.value = state.houseSortKey || "default";
  const dir = $("houseSortDir");
  if (dir) {
    const desc = state.houseSortDir === "desc";
    dir.textContent = desc ? "↓ Desc" : "↑ Asc";
    dir.setAttribute("aria-label", `Sort direction: ${desc ? "descending" : "ascending"}`);
    dir.setAttribute("aria-pressed", desc ? "true" : "false");
    dir.disabled = (state.houseSortKey || "default") === "default";
  }
  const sum = $("houseToolsSummary");
  if (sum) {
    const shown = filteredTrackIndexes().length;
    const total = state.tracks.filter(
      (t) => !state.houseCrate || String(t.group || "") === state.houseCrate
    ).length;
    sum.textContent = houseBpmWindowActive()
      ? `${shown} of ${total} in BPM ${houseBpmWindowLabel()}`
      : `${total} tracks`;
  }
}

function applyHouseTools() {
  syncHouseTools();
  renderTrackList();
}

function renderHouseCrateSelect(crates, defaultCrate) {
  const sel = $("houseCrateSelect");
  if (!sel) return;
  // R-99: the House app has no Pajamathon crate - it never appears in the dropdown (a remembered pick falls back to the default).
  const names = Array.from(new Set((crates || []).filter((c) => c && !/^pajamathon$/i.test(String(c).trim()))));
  if (/^pajamathon$/i.test(String(state.houseCrate || "").trim())) state.houseCrate = defaultCrate || "";
  if (defaultCrate && !names.includes(defaultCrate)) names.unshift(defaultCrate);
  names.sort((a, b) => (a === defaultCrate ? -1 : b === defaultCrate ? 1 : a.localeCompare(b)));
  const opts = names.map(
    (n) =>
      `<option value="${escapeHtml(n)}"${n === state.houseCrate ? " selected" : ""}>${escapeHtml(n)}${
        n === defaultCrate ? " (default)" : ""
      }</option>`
  );
  opts.push(`<option value=""${state.houseCrate ? "" : " selected"}>All folders</option>`);
  sel.innerHTML = opts.join("");
}

function loadHouseCrate() {
  try {
    const raw = localStorage.getItem("music-sorter-house-crate");
    if (raw === "__all__") {
      state.houseCrate = "";
    } else if (raw) state.houseCrate = raw;
  } catch {
    /* ignore */
  }
}

function bindHouseTools() {
  loadHouseCrate();
  const onBpm = () => {
    state.bpmMin = parseBpmField($("bpmMinInput")?.value);
    state.bpmMax = parseBpmField($("bpmMaxInput")?.value);
    applyHouseTools();
  };
  $("bpmMinInput")?.addEventListener("input", onBpm);
  $("bpmMaxInput")?.addEventListener("input", onBpm);
  $("bpmChip115125")?.addEventListener("click", () => {
    const on = state.bpmMin === 115 && state.bpmMax === 125;
    state.bpmMin = on ? null : 115;
    state.bpmMax = on ? null : 125;
    applyHouseTools();
  });
  $("bpmClearBtn")?.addEventListener("click", () => {
    state.bpmMin = null;
    state.bpmMax = null;
    applyHouseTools();
    $("bpmMinInput")?.focus();
  });
  $("houseSortKey")?.addEventListener("change", (e) => {
    state.houseSortKey = e.target.value || "default";
    applyHouseTools();
  });
  $("houseSortDir")?.addEventListener("click", () => {
    state.houseSortDir = state.houseSortDir === "desc" ? "asc" : "desc";
    applyHouseTools();
  });
  $("houseCrateSelect")?.addEventListener("change", (e) => {
    state.houseCrate = e.target.value || "";
    try {
      localStorage.setItem("music-sorter-house-crate", state.houseCrate || "__all__");
    } catch {
      /* ignore */
    }
    loadTracks();
  });
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setPlayerLoading(isLoading) {
  const panel = document.querySelector(".panel.player");
  if (!panel) return;
  panel.classList.toggle("is-loading", Boolean(isLoading));
}

function setNotesStatus(text, kind = "") {
  const el = $("vdjNotesStatus");
  if (!el) return;
  el.textContent = text || "—";
  el.className = `badge ${
    kind === "ok" ? "ok" : kind === "error" ? "bad" : kind === "warn" ? "warn" : "neutral"
  }`;
}

function bindNotesToTrack(track) {
  const ta = $("vdjNotes");
  if (!ta) return;
  // Don't clobber in-progress typing for the same path.
  if (
    state.notesDirty &&
    state.notesPath &&
    track &&
    state.notesPath === track.path
  ) {
    return;
  }
  // Flush dirty notes for the previous track before rebinding.
  const prevPath = state.notesPath;
  const prevDirty = state.notesDirty;
  const prevText = ta.value;
  if (state.notesSaveTimer) {
    clearTimeout(state.notesSaveTimer);
    state.notesSaveTimer = null;
  }
  if (prevDirty && prevPath && (!track || track.path !== prevPath)) {
    const gen = ++state.notesSaveGen;
    // Fire-and-forget flush; do not require currentTrack match.
    saveVdjNotes(prevPath, prevText, gen, { force: true });
  }
  state.notesDirty = false;
  if (!track) {
    state.notesPath = null;
    ta.value = "";
    ta.disabled = true;
    setNotesStatus("—");
    return;
  }
  state.notesPath = track.path;
  ta.disabled = !track.cues?.in_database || isReadonlyBuild();
  ta.value = track.cues?.comment || "";
  if (!track.cues?.in_database) {
    setNotesStatus("not in VDJ", "warn");
  } else if (ta.value.trim()) {
    setNotesStatus("loaded", "ok");
  } else {
    setNotesStatus("empty");
  }
}

function scheduleNotesSave() {
  const ta = $("vdjNotes");
  const track = currentTrack();
  if (!ta || !track || ta.disabled) return;
  if (state.notesPath && state.notesPath !== track.path) return;

  state.notesDirty = true;
  setNotesStatus("typing…");
  if (state.notesSaveTimer) clearTimeout(state.notesSaveTimer);
  const path = track.path;
  const text = ta.value;
  const gen = ++state.notesSaveGen;
  state.notesSaveTimer = setTimeout(() => {
    state.notesSaveTimer = null;
    saveVdjNotes(path, text, gen);
  }, 550);
}

async function saveVdjNotes(path, comment, gen, opts = {}) {
  const force = Boolean(opts.force);
  if (gen != null && gen !== state.notesSaveGen && !force) return;
  if (!force && currentTrack()?.path !== path) return;

  if (!force) setNotesStatus("saving…", "warn");
  // Warn once per session if VDJ is open (notes can be overwritten on quit).
  if (!state.notesWarnedVdj && (await isVdjRunningFresh())) {
    state.notesWarnedVdj = true;
    setStatus(
      "VirtualDJ is open — notes still save, but VDJ may overwrite them on quit.",
      "warn"
    );
  }
  try {
    const data = await api("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        path,
        comment,
        allow_vdj_running: true,
        // One backup on first notes write of the session.
        create_backup: !state._notesBackupDone,
      }),
    });
    state._notesBackupDone = true;
    if (gen != null && gen !== state.notesSaveGen && !force) return;
    if (!force && currentTrack()?.path !== path) return;

    const saved = data.result?.comment ?? comment;
    const idx = state.tracks.findIndex((t) => t.path === path);
    if (idx >= 0) {
      if (!state.tracks[idx].cues) state.tracks[idx].cues = {};
      state.tracks[idx].cues.comment = saved;
    }
    if (!force || currentTrack()?.path === path) {
      state.notesDirty = false;
    }
    const ta = $("vdjNotes");
    if (!force && ta && ta.value === comment) {
      setNotesStatus(
        data.result?.unchanged ? "saved" : "saved to VDJ",
        "ok"
      );
    } else if (!force) {
      setNotesStatus("saved · editing…", "ok");
    }
  } catch (err) {
    if (gen != null && gen !== state.notesSaveGen && !force) return;
    if (!force) setNotesStatus(err.message || "save failed", "error");
    else setStatus(`Notes save failed: ${err.message}`, "error");
    state.notesDirty = true;
  }
}

/* The open song changed without going through selectTrack (list reload, copy-sort, filter change): reset EVERYTHING
   that belongs to a song so nothing of the previous song (markers, description, tags, destination, grid card,
   copy button state, waveform) is ever shown for the new one. */
function songSwitchReset(track) {
  const prev = state.panelPath;
  try {
    // A color menu / rename box left open would hold back the redraw and leave the PREVIOUS song's cues on screen.
    closeCueColorMenu();
    document.querySelectorAll("#cueList .cue-name-input").forEach((i) => i.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: false })));
    state.renderCuesDeferred = false;
  } catch {
    /* ignore */
  }
  try { syncHistoryButtons(); } catch { /* buttons not built yet */ }
  state.panelPath = track.path;
  rememberOpenSong(track.path, state.mode);
  if (state.genForPath !== track.path) {
    state.trackGen += 1;
    state.genForPath = track.path;
  }
  try {
    clearSelectedDests();
  } catch {
    state.selectedDests = [];
    state.selectedPath = "";
    state.selectedPathLibrary = "";
  }
  state.recommendation = null;
  state.recommendationPath = track.path; // pending: renders as "Asking Gemini…", never the previous song's card
  state.activeLoopKey = null;
  state.activeCueKey = null;
  state.gridPreflight = null;
  if (state.trackMeta && state.trackMeta.path !== track.path) state.trackMeta = null;
  if (state.lastCueCopy && state.lastCueCopy.sourcePath !== track.path) state.lastCueCopy = null;
  if (state.loopDrag) {
    try { removeDragOverlay(); } catch { /* ignore */ }
    state.loopDrag = null; // a drag begun on the previous song's picture never lands on this one
    $("waveformWrap")?.classList.remove("loop-dragging", "cue-dragging");
  }
  state.dropPreview = null;
  state.placeLoopPreview = null;
  state.placeCuePreview = null;
  if (state.waveformPath !== track.path) {
    // R-97: wipe the previous song's picture NOW (not when the new one arrives); nothing can be placed on it.
    state.waveform = null;
    state.waveformPath = null;
    state.waveformLoading = true;
    state.waveformError = null;
    if (!isPracticeMode()) {
      try {
        setWaveformStatus("Loading waveform…");
        drawWaveform();
      } catch { /* ignore */ }
    }
  }
  stopLoopWatch();
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  try {
    renderGridPreflightCard(null);
  } catch {
    /* ignore */
  }
  renderCues(); // this song's own markers, now
  if (prev) state.songSwitchCount = (state.songSwitchCount || 0) + 1;
  // Never a half-loaded panel: if the waveform has not arrived for this song after a while, load it again.
  clearTimeout(songSwitchReset._t);
  const wantPath = track.path;
  songSwitchReset._t = setTimeout(() => {
    const cur = currentTrack();
    if (cur && cur.path === wantPath && !waveformLoadedFor(cur) && !isPracticeMode()) {
      setWaveformStatus("Still loading the waveform… retrying", "error");
      scheduleWaveformLoad(cur, state.trackGen, { force: true });
    }
  }, 9000);
}

function renderPlayer() {
  {
    const cur = currentTrack();
    if (cur && state.panelPath !== cur.path) songSwitchReset(cur);
    else if (!cur) state.panelPath = null;
  }
  const track = currentTrack();
  const gen = state.trackGen;
  const title = $("nowPlaying");
  const meta = $("playerMeta");
  const audio = $("audio");
  const recBox = $("recommendation");
  const block = $("blockBanner");
  const sortBtn = $("sortBtn");

  if (!track) {
    document.body.classList.remove("track-is-cued", "has-track");
    title.textContent = isPracticeMode()
      ? "Select a practice mix"
      : isReviewMode()
        ? "Select a track from the queue"
        : "Select a track from the queue";
    title.removeAttribute("title");
    title.removeAttribute("aria-label");
    meta.innerHTML = "";
    renderTrackCover(null);
    if (!isPracticeMode()) bindNotesToTrack(null);
    audio.pause();
    audio.removeAttribute("src");
    try {
      audio.load();
    } catch {
      /* ignore */
    }
    if (!isPracticeMode()) {
      if (recBox) {
        recBox.hidden = true;
        recBox.className = "recommendation";
        recBox.innerHTML = "";
      }
    }
    if (block) block.hidden = true;
    if ($("placementCard")) {
      $("placementCard").hidden = true;
      $("placementCard").innerHTML = "";
    }
    if (sortBtn) sortBtn.disabled = true;
    if ($("removeReadyBtn")) $("removeReadyBtn").disabled = true;
    if ($("demoteReadyBtn")) {
      $("demoteReadyBtn").disabled = true;
      $("demoteReadyBtn").hidden = isReviewMode() || isPracticeMode();
    }
    state.activeCueKey = null;
    state.waveform = null;
    resetWaveZoom();
    setPlayerLoading(false);
    if (!isPracticeMode()) {
      renderCues();
      drawWaveform();
      setWaveformStatus("No track selected");
      renderReviewPanel();
      syncAutocueUi();
      state.gridPreflight = null;
      renderGridPreflightCard(null);
    }
    updateTransportUi();
    return;
  }

  renderNowPlayingTitle(track);
  document.body.classList.toggle("track-is-cued", Boolean(track.is_cued));
  document.body.classList.toggle("has-track", true);
  // Same track (seek, edit, list refresh): never flash the loading overlay.
  if ($("audio")?.dataset.path !== track.path) setPlayerLoading(true);
  // Show known meta immediately; kbps fills in when probe returns.
  if (state.trackMeta?.path !== track.path) {
    state.trackMeta = track.bitrate_kbps
      ? {
          path: track.path,
          bitrate_kbps: track.bitrate_kbps,
          codec: track.codec,
          sample_rate: track.sample_rate,
        }
      : null;
  }
  meta.innerHTML = isPracticeMode()
    ? buildPracticePlayerMetaHtml(track)
    : buildPlayerMetaHtml(track);
  if (!isPracticeMode()) loadTrackMeta(track, gen);

  if (!isPracticeMode()) renderPlacementCard(track);

  const src = `/api/audio?path=${encodeURIComponent(track.path)}`;
  if (audio.dataset.path !== track.path) {
    // Stop previous download immediately so rapid switches don't pile up.
    audio.pause();
    audio.removeAttribute("src");
    try {
      audio.load();
    } catch {
      /* ignore */
    }
    audio.dataset.path = track.path;
    audio.src = src;
    if (isRecsMode()) refreshRecsNowPlaying({ quiet: true, loadAudio: false });
    state.activeCueKey = null;
    // Always load waveform (practice uses it for transition map).
    scheduleWaveformLoad(track, gen);
    applyPlaybackRate(isPracticeMode() ? 1 : state.playbackRate);
    // Defer play slightly so aborted switches don't start audio.
    // Opening / selecting a track stays silent until the user hits Play
    // (or a previous Play in this tab unlocked autoplay). Quiet sessions never play.
    setTimeout(() => {
      if (gen !== state.trackGen || currentTrack()?.path !== track.path) return;
      if (shouldAutoplayOnSelect()) {
        playAudio(audio).catch(() => {});
      }
      setPlayerLoading(false);
    }, 160);
  } else {
    if (!state.waveform && !state.waveformLoading) {
      scheduleWaveformLoad(track, gen);
    }
    setPlayerLoading(false);
  }
  if (!isPracticeMode()) updateSpeedUi();
  updateTransportUi();
  if (!isPracticeMode()) bindNotesToTrack(track);

  if ($("removeReadyBtn")) {
    $("removeReadyBtn").disabled = isReviewMode() || isPracticeMode();
    $("removeReadyBtn").hidden = isReviewMode() || isPracticeMode();
  }
  if ($("demoteReadyBtn")) {
    $("demoteReadyBtn").disabled = isReviewMode() || isPracticeMode() || !track;
    $("demoteReadyBtn").hidden = isReviewMode() || isPracticeMode();
  }

  if (isPracticeMode()) {
    if (block) block.hidden = true;
    if (sortBtn) sortBtn.disabled = true;
    if (recBox) recBox.hidden = true;
    drawPracticeWaveform();
    return;
  }

  // blockBanner is only for uncued / blocking messages — not placement details.
  if (isReviewMode()) {
    block.hidden = true;
    syncSortButtonState();
    updateApproveButtons();
  } else if (!track.is_cued) {
    block.hidden = false;
    block.className = "block-banner";
    block.textContent =
      "No VirtualDJ cue points yet. Sort is locked — you can still Trash from Ready.";
    sortBtn.disabled = true;
  } else {
    block.hidden = true;
    syncSortButtonState();
  }

  renderCues();
  drawWaveform();
  if (isReviewMode()) {
    renderReviewPanel();
    renderLanePicker();
    renderRecommendation();
  } else {
    recBox.hidden = false;
    renderRecommendation();
  }

  syncAutocueUi();
  loadDeepGridPreflight(track, gen);
}

function buildPracticePlayerMetaHtml(track) {
  const d = state.practiceDetail;
  const bits = [];
  if (d?.duration_sec != null) bits.push(formatClock(d.duration_sec));
  else if (track.duration != null) bits.push(formatClock(track.duration));
  if (d?.track_count != null) bits.push(`${d.track_count} tracks`);
  if (d?.transition_count != null) bits.push(`${d.transition_count} transitions`);
  return bits.length
    ? bits.map((b) => `<span class="badge neutral">${escapeHtml(b)}</span>`).join(" ")
    : `<span class="badge neutral">practice mix</span>`;
}

function placementCueBadge(hit, options = {}) {
  const justCopied = Boolean(options.justCopied);
  const isSource = Boolean(options.isSource);
  if (justCopied) {
    const loops = hit.loop_count ? ` · ${hit.loop_count} loops` : "";
    return `<span class="badge ok">${hit.cue_count} cues${loops}</span><span class="badge ok placement-just-copied">Just copied</span>`;
  }
  if (isSource) {
    const loops = hit.loop_count ? ` · ${hit.loop_count} loops` : "";
    return `<span class="badge ok">${hit.cue_count || 0} cues${loops}</span><span class="badge neutral placement-copy-source">Source</span>`;
  }
  if (hit.is_cued) {
    const loops = hit.loop_count ? ` · ${hit.loop_count} loops` : "";
    return `<span class="badge ok">${hit.cue_count} cues${loops}</span>`;
  }
  if (hit.in_database) {
    return `<span class="badge uncued">Not cued</span>`;
  }
  return `<span class="badge bad">Not in VDJ</span>`;
}

function placementPathRow(labelPath, hit, options = {}) {
  const bpm = hit.bpm ? `<span class="badge neutral">${Number(hit.bpm).toFixed(0)} BPM</span>` : "";
  const grid = hit.has_beatgrid ? `<span class="badge neutral">grid</span>` : "";
  const pathAttr = escapeHtml(hit.path || "");
  const allowDelete = options.allowDelete !== false;
  const allowCopyCues = options.allowCopyCues !== false;
  const justCopied = Boolean(options.justCopied);
  const isSource = Boolean(options.isSource);
  const copyLabel = hit.is_cued ? "Replace cues" : "Copy cues";
  const copyTitle = hit.is_cued
    ? "Replace this copy's VirtualDJ cues with the Ready / Add Cues markers"
    : "Copy this track's VirtualDJ cues onto this existing file";
  const actions = [];
  if (allowCopyCues) {
    actions.push(placementMatchBadge(currentTrack(), hit));
    actions.push(`
      <button
        type="button"
        class="btn ghost placement-copy-cues-btn"
        data-placement-path="${pathAttr}"
        title="${escapeHtml(copyTitle)}"
        aria-label="${escapeHtml(copyLabel)} ${escapeHtml(labelPath)}"
      >${copyLabel}</button>`);
  }
  if (allowDelete) {
    actions.push(`
      <button
        type="button"
        class="btn ghost danger placement-delete-btn"
        data-placement-path="${pathAttr}"
        title="Remove this file from its folder (Trash) and delete its VirtualDJ cues for that path"
        aria-label="Delete from folder ${escapeHtml(labelPath)}"
      >Delete from folder</button>`);
  }
  const rowClass = [
    "placement-path-row",
    justCopied ? "is-just-copied" : "",
    isSource ? "is-copy-source" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return `
    <div class="${rowClass}" data-placement-path="${pathAttr}">
      <div class="placement-path-main">
        <div class="placement-path">${escapeHtml(labelPath)}</div>
        <div class="placement-path-meta">
          ${placementCueBadge(hit, { justCopied, isSource })}
          ${bpm}
          ${grid}
        </div>
      </div>
      <div class="placement-path-actions">${actions.join("")}</div>
    </div>`;
}

const MusicSorterPlacements =
  (typeof globalThis !== "undefined" && globalThis.MusicSorterPlacements) ||
  (typeof window !== "undefined" && window.MusicSorterPlacements) ||
  {};

function isPajamathonPlacement(hit) {
  return MusicSorterPlacements.isPajamathonPlacement(hit);
}

function emptyPlacements() {
  return MusicSorterPlacements.emptyPlacements();
}

function placementsArePopulated(placements) {
  return MusicSorterPlacements.placementsArePopulated(placements);
}

function mergeLoadedPlacements(prevTracks, nextTracks) {
  return MusicSorterPlacements.mergeLoadedPlacements(prevTracks, nextTracks);
}

function applyExistingSetPlacement(track, result) {
  return MusicSorterPlacements.applyExistingSetPlacement(track, result);
}

function rememberCueCopy(sourcePath, payload) {
  state.lastCueCopy = MusicSorterPlacements.normalizeCueCopyReceipt(
    payload,
    sourcePath
  );
}

function cueCopyReceiptForTrack(track) {
  const receipt = state.lastCueCopy;
  if (!receipt || !track || receipt.sourcePath !== track.path) return null;
  return receipt;
}

function placementCardModel(track, options) {
  return MusicSorterPlacements.placementCardModel(track, options);
}

function renderPlacementCard(track) {
  const card = $("placementCard");
  if (!card) return;

  if (!track || isPracticeMode()) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }

  const model = placementCardModel(track, { review: isReviewMode() });
  // House fork: "Not in Pajamathon / No matching Sets/Pajamathon file" means nothing for House songs.
  // Never show that banner (Zouk/Pajamathon profiles keep it).
  if (isHouseProfile() && model.state === "missing") {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  const rows = [];
  const libs = model.libs;
  const sorted = model.sorted;
  const sets = model.sets;

  const canCopyCues = Boolean(track.is_cued);
  const receipt = cueCopyReceiptForTrack(track);
  const rowOpts = (hit, extra) => ({
    ...extra,
    justCopied: Boolean(
      MusicSorterPlacements.cueCopyDestForPath(receipt, hit.path)
    ),
    isSource: Boolean(hit.path && track.path && hit.path === track.path),
  });
  const libraryGroups = [];
  const zoukHits = [];
  const houseHits = libs.filter((p) => p.root_name === "House");
  const otherLibs = libs.filter(
    (p) => p.root_name !== "House"
  );
  if (houseHits.length) libraryGroups.push(["House", houseHits]);
  for (const p of otherLibs) {
    libraryGroups.push([p.root_name || "Library", [p]]);
  }
  for (const [label, hits] of libraryGroups) {
    const paths = hits
      .map((p) =>
        placementPathRow(
          `${p.root_name}/${p.relative_path}`,
          p,
          rowOpts(p, {
            allowCopyCues: canCopyCues,
            allowDelete: true,
          })
        )
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">${escapeHtml(label)}</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }
  if (sorted.length) {
    const paths = sorted
      .map((p) =>
        placementPathRow(
          `Cues Sorted/${p.relative_path}`,
          p,
          rowOpts(p, {
            allowCopyCues: canCopyCues,
            allowDelete: true,
          })
        )
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">Archive</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }
  if (sets.length) {
    const allPaj = sets.every((p) => isPajamathonPlacement(p));
    const paths = sets
      .map((p) =>
        placementPathRow(
          `Sets/${p.relative_path}`,
          p,
          rowOpts(p, {
            allowCopyCues: canCopyCues && p.path !== track.path,
            allowDelete: p.path !== track.path,
          })
        )
      )
      .join("");
    rows.push(`
      <div class="placement-row">
        <div class="placement-label">${allPaj ? "Pajamathon" : "Sets"}</div>
        <div class="placement-paths">${paths}</div>
      </div>`);
  }

  const review = isReviewMode();
  const cuedN = model.cuedN;
  const totalN = model.totalN;
  const inPajamathon = model.inPajamathon;
  const loading = model.loading;
  const loadError = model.loadError;
  const title = model.title;
  const note = model.note;

  const actionBtns = [];
  if (loadError) {
    actionBtns.push(`
      <button
        type="button"
        class="btn ghost placement-retry-btn"
        title="Look up Cues Sorted, Add Cues and set copies again"
      >Retry library lookup</button>`);
  }
  if (canCopyCues && totalN > 1) {
    actionBtns.push(`
      <button
        type="button"
        class="btn ghost placement-copy-cues-all-btn"
        title="Write this track's VirtualDJ cues onto every library, Cues Sorted, and Sets copy listed above"
      >Copy cues to all ${totalN} locations</button>`);
  }
  const allAction = actionBtns.length
    ? `<div class="placement-card-actions">${actionBtns.join("")}</div>`
    : "";
  const receiptLabel = receipt
    ? MusicSorterPlacements.cueCopyReceiptLabel(receipt)
    : "";
  const receiptHtml = receiptLabel
    ? `<div class="placement-copy-receipt" role="status">${escapeHtml(
        receiptLabel
      )}</div>`
    : "";

  card.hidden = false;
  card.classList.toggle("placement-card-review", review);
  card.classList.toggle("placement-card-has-cued", cuedN > 0);
  card.classList.toggle("placement-card-just-copied", Boolean(receiptLabel));
  card.innerHTML = `
    <div class="placement-card-title">${escapeHtml(title)}</div>
    ${receiptHtml}
    <div class="placement-rows">${rows.join("")}</div>
    ${allAction}
    <div class="placement-card-note">${escapeHtml(note)}</div>
  `;

  card.querySelectorAll(".placement-delete-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const path = btn.getAttribute("data-placement-path");
      if (path) deleteLibraryPlacement(path);
    });
  });
  card.querySelectorAll(".placement-copy-cues-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const path = btn.getAttribute("data-placement-path");
      if (path) copyCuesToPlacement(path);
    });
  });
  const allBtn = card.querySelector(".placement-copy-cues-all-btn");
  if (allBtn) {
    allBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyCuesToAllPlacements();
    });
  }
  const retryBtn = card.querySelector(".placement-retry-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      loadTrackPlacements(currentTrack(), { force: true });
    });
  }
}

async function deleteLibraryPlacement(placementPath) {
  const track = currentTrack();
  if (!placementPath) return;

  // Resolve label from current placements if possible.
  const allHits = [
    ...(track?.placements?.library || []),
    ...(track?.placements?.cues_sorted || []),
    ...(track?.placements?.sets || []),
  ];
  const hit = allHits.find((h) => h.path === placementPath);
  const label = hit ? placementHitLabel(hit) : placementPath.split("/").slice(-3).join("/");
  const cueNote =
    hit?.is_cued
      ? ` This copy has ${hit.cue_count || 0} cues` +
        (hit.loop_count ? ` and ${hit.loop_count} loops` : "") +
        " in VirtualDJ — they will be removed for this path only."
      : hit?.in_database
        ? " The VirtualDJ Song entry for this path will be removed (no manual cues found)."
        : " No VirtualDJ entry was found for this path (file still goes to Trash).";

  const ok = await showConfirmDialog({
    title: "Delete from this folder?",
    track: track ? trackDisplayTitle(track) : label,
    message: `Remove “${label}” from that folder and delete its VirtualDJ database entry (cues/loops for that path only).`,
    note:
      `File moves to Trash (recoverable). Ready for Sort / Add Cues is not touched.${cueNote} Use this to remove a library, Cues Sorted, or Pajamathon copy. Close VirtualDJ first if it is open.`,
    confirmLabel: "Delete from folder",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: track ? trackDisplayTitle(track) : label,
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Delete anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then delete the library copy.", "error");
      return;
    }
  }

  try {
    setStatus(`Deleting placement: ${label}…`);
    const data = await api("/api/delete-placement", {
      method: "POST",
      body: JSON.stringify({
        path: placementPath,
        to_trash: true,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    const dbBit = r.database?.removed_from_db
      ? ` · VDJ entry removed (${r.had_cues ?? 0} cues, ${r.had_loops ?? 0} loops)`
      : r.database?.reason === "not_in_database"
        ? " · not in VDJ DB"
        : "";
    const missingBit = r.missing_file ? " · file was already gone" : "";
    setStatus(
      `Deleted ${r.root_name || ""}/${r.relative_path || label}${missingBit}${dbBit}`,
      "success"
    );
    // Refresh placements for the Ready track (source stays).
    await loadTracks({ keepPath: track?.path, skipStatus: true });
    await loadTrackPlacements(currentTrack(), { force: true });
    await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function allPlacementHits(track) {
  const src = track?.path;
  return [
    ...(track?.placements?.library || []),
    ...(track?.placements?.cues_sorted || []),
    ...(track?.placements?.sets || []),
  ].filter((h) => h && h.path && h.path !== src);
}

function placementHitLabel(hit) {
  if (!hit) return "";
  if (hit.root_name === "Cues Sorted" || (hit.root || "").includes("Cues Sorted")) {
    return `Cues Sorted/${hit.relative_path}`;
  }
  if (hit.root_name === "House" && /\/Music\/House$/.test(hit.root || "")) {
    return `House/${hit.relative_path}`;
  }
  if (hit.event || (hit.root || "").includes("/Sets") || (hit.root || "").endsWith("/Sets")) {
    return `Sets/${hit.relative_path}`;
  }
  return `${hit.root_name}/${hit.relative_path}`;
}

async function copyCuesToPlacement(placementPath) {
  const track = currentTrack();
  if (!placementPath || !track) return;
  if (!track.is_cued) {
    setStatus("This track has no cue points to copy.", "error");
    return;
  }

  const allHits = [
    ...(track.placements?.library || []),
    ...(track.placements?.cues_sorted || []),
    ...(track.placements?.sets || []),
  ];
  const hit = allHits.find((h) => h.path === placementPath);
  const label = hit
    ? placementHitLabel(hit)
    : placementPath.split("/").slice(-3).join("/");
  const destCued = Boolean(hit?.is_cued) || Number(hit?.loop_count || 0) > 0;
  const cueNote = destCued
    ? ` This copy already has ${hit.cue_count || 0} cues` +
      (hit.loop_count ? ` and ${hit.loop_count} loops` : "") +
      " — they will be replaced. Beatgrid, BPM, and comments on that file stay."
    : " The Ready / Add Cues markers will be written onto this file. Audio stays put.";

  const ok = await showConfirmDialog({
    title: destCued ? "Replace cues on this copy?" : "Copy cues onto this copy?",
    track: trackDisplayTitle(track),
    message: `Write cues from “${trackDisplayTitle(track)}” onto “${label}”.`,
    note: `VirtualDJ database only — files are not moved.${cueNote} Close VirtualDJ first if it is open.`,
    confirmLabel: destCued ? "Replace cues" : "Copy cues",
    tone: destCued ? "warning" : "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Copy anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then copy cues.", "error");
      return;
    }
  }

  try {
    setStatus(`Copying cues onto ${label}…`);
    const data = await api("/api/copy-cues", {
      method: "POST",
      body: JSON.stringify({
        source: track.path,
        dest: placementPath,
        overwrite: destCued,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    rememberCueCopy(track.path, r);
    syncPlacementHitFromTrack(currentTrack() || track, placementPath);
    refreshPlacementMatchUi(currentTrack() || track);
    const destName = r.root_name
      ? `${r.root_name}/${r.relative_path || label}`
      : label;
    setStatus(
      MusicSorterPlacements.cueCopyReceiptLabel(state.lastCueCopy) ||
        `Copied ${r.copied_cues || 0} cues` +
          (r.copied_loops ? ` · ${r.copied_loops} loops` : "") +
          ` → ${destName}`,
      "success"
    );
    await loadTracks({ keepPath: track.path, skipStatus: true });
    await loadTrackPlacements(currentTrack(), { force: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

async function copyCuesToAllPlacements() {
  const track = currentTrack();
  if (!track) return;
  if (!track.is_cued) {
    setStatus("This track has no cue points to copy.", "error");
    return;
  }

  const hits = allPlacementHits(track);
  if (!hits.length) {
    setStatus("No existing library copies to write cues onto.", "error");
    return;
  }

  const marked = hits.filter(
    (h) => h.is_cued || Number(h.loop_count || 0) > 0
  );
  const labels = hits.map((h) => placementHitLabel(h));
  const list =
    labels.length <= 6
      ? labels.join(", ")
      : `${labels.slice(0, 5).join(", ")} +${labels.length - 5} more`;
  const cueNote = marked.length
    ? ` ${marked.length} of ${hits.length} already have cues/loops and will be replaced.`
    : " Audio files stay put.";

  const ok = await showConfirmDialog({
    title: marked.length
      ? `Replace cues on all ${hits.length} copies?`
      : `Copy cues to all ${hits.length} locations?`,
    track: trackDisplayTitle(track),
    message: `Write cues from “${trackDisplayTitle(track)}” onto: ${list}.`,
    note: `VirtualDJ database only — files are not moved.${cueNote} Close VirtualDJ first if it is open.`,
    confirmLabel: marked.length
      ? `Replace cues on ${hits.length}`
      : `Copy cues to ${hits.length}`,
    tone: marked.length ? "warning" : "accent",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Database edits may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Copy anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then copy cues.", "error");
      return;
    }
  }

  try {
    setStatus(`Copying cues onto ${hits.length} locations…`);
    const data = await api("/api/copy-cues-all", {
      method: "POST",
      body: JSON.stringify({
        source: track.path,
        dests: hits.map((h) => h.path),
        overwrite: marked.length > 0,
        allow_vdj_running: Boolean(allowRunning),
      }),
    });
    const r = data.result || {};
    rememberCueCopy(track.path, r);
    for (const hit of hits) {
      syncPlacementHitFromTrack(currentTrack() || track, hit.path);
    }
    refreshPlacementMatchUi(currentTrack() || track);
    setStatus(
      MusicSorterPlacements.cueCopyReceiptLabel(state.lastCueCopy) ||
        `Copied to ${r.copied || 0}`,
      r.failed ? "error" : "success"
    );
    await loadTracks({ keepPath: track.path, skipStatus: true });
    await loadTrackPlacements(currentTrack(), { force: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function renderReviewPanel() {
  const card = $("readinessCard");
  const track = currentTrack();
  if (!card) return;
  if (!track) {
    card.innerHTML = `<div class="subtitle">Select a track to assess readiness</div>`;
    updateApproveButtons();
    return;
  }

  const setFile = isPajamathonSetQueueTrack(track);
  const fileLabel = setFile
    ? `Sets/${track.relative_path || track.name || ""}`
    : track.relative_path || "";
  const r = track.readiness || {};
  const mc = markerCountsOf(track);
  const checks = {
    ...(r.checks || {}),
    has_cues: mc.cues > 0,
    multiple_cues: mc.cues >= 2,
    has_loops: mc.loops > 0,
  };
  const rows = [
    ["In VDJ database", checks.in_database],
    ["Beatgrid present", checks.has_beatgrid],
    ["Has cue points", checks.has_cues],
    ["At least 2 cues", checks.multiple_cues],
    ["Has loops", checks.has_loops],
  ]
    .map(
      ([label, ok]) => `
      <div class="check-row">
        <span class="${ok ? "check-ok" : "check-no"}">${ok ? "✓" : "–"}</span>
        <span>${escapeHtml(label)}</span>
      </div>`
    )
    .join("");

  const g = state.gridPreflight || track.grid || {};
  const gridLine = g.label
    ? `<div class="review-grid-status">Beatgrid: ${escapeHtml(g.label)}${
        g.can_autocue === false ? " · AutoCue blocked" : g.can_autocue ? " · AutoCue ok" : ""
      }</div>`
    : "";

  card.innerHTML = `
    <div class="readiness-heading">
      <div class="meta-row">${readinessBadge(track)}</div>
      <div class="readiness-summary">${escapeHtml(r.summary || "")}</div>
      <div class="review-marker-counts" data-cues="${mc.cues}" data-loops="${mc.loops}">${mc.cues} cues · ${mc.loops} loops</div>
    </div>
    <div class="check-list">${rows}</div>
    ${gridLine}
    <div class="review-guidance">
      ${
        setFile
          ? r.status === "approved"
            ? "Approved — hidden from the default Pajamathon list. Cue in Sets, not Add Cues."
            : r.status === "needs_review"
              ? "AI or existing cues — listen, then Approve. That is the Ready-for-Sort step for set files."
              : "This is the Sets/Pajamathon event file — not the Add Cues inbox. AutoCue it in place, then Approve."
          : r.ready
            ? "Markers look complete. Listen through key cues, then approve."
            : "Not auto-ready — jump cues, listen, and only promote if they feel right."
      }
    </div>
    ${
      fileLabel
        ? `<div class="review-path" title="${escapeHtml(fileLabel)}">
            <span>${setFile ? "Sets" : "File"}</span>
            <strong>${escapeHtml(fileLabel)}</strong>
          </div>`
        : ""
    }
  `;
  updateApproveButtons();
}

function updateApproveButtons() {
  const track = currentTrack();
  const setFile = isPajamathonSetQueueTrack(track);
  const canApprove = Boolean(track && track.is_cued && !setFile);
  const hasTrack = Boolean(track);
  ["approveBtn", "approveBtnSide"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    if (copyButtonsLocked()) {
      el.disabled = true;
      if (id === "approveBtnSide") {
        el.textContent = state.sortInFlight
          ? (copyFeedback.startedAt ? `Copying… ${copyElapsedText()}` : "Copying…")
          : "Loading next song…";
      }
      return;
    }
    el.disabled = !canApprove;
    if (id === "approveBtnSide") {
      const destN = typeof selectedDestCount === "function" ? selectedDestCount() : 0;
      if (isSetOverviewMode()) {
        if (!track || !track.is_cued) {
          el.textContent = "Cue this set file first";
          el.disabled = true;
        } else if (!destN) {
          el.textContent = "Pick Gemini rec or a folder →";
          el.disabled = true;
        } else {
          const dests = state.selectedDests || [];
          el.textContent = dests[0]
            ? `Sort · ${dests[0].library}/${dests[0].path}`
            : "Sort";
          el.disabled = false;
        }
      } else if (setFile) {
        const approved = trackReadinessStatus(track) === "approved";
        if (!track || !track.is_cued) {
          el.textContent = "Cue this set file first";
          el.disabled = true;
        } else if (approved) {
          el.textContent = "Next Pajamathon track";
          el.disabled = false;
        } else {
          el.textContent = "Approve set cues";
          el.disabled = false;
        }
      } else if (!state.selectedPath) {
        el.textContent = "Pick a folder →";
        el.disabled = true;
        el.title = "Pick a House folder (or a tag chip) first.";
      } else {
        el.title = canApprove ? "Copy this song into the picked House folder (original stays)." : "This track is not cued yet — cue it first.";
        el.textContent = isReadonlyBuild()
          ? `Preview COPY TO House / ${state.selectedPath}`
          : `COPY TO House / ${state.selectedPath}`;
        el.disabled = !canApprove;
      }
    }
  });

  applyCopyBusyToButtons();
  const hint = document.querySelector(".rail-primary-hint");
  if (hint) {
    hint.textContent = isSetOverviewMode()
      ? liveSortDestHint()
      : setFile
      ? "Sets/Pajamathon — Approve after you listen. Approved tracks leave this list. Inbox still uses Move to Ready."
      : liveSortDestHint();
  }
  const railTitle = document.querySelector("#reviewPanel h2");
  const railSub = document.querySelector("#reviewPanel .panel-header .subtitle");
  if (railTitle) {
    railTitle.textContent = setFile ? "Event crate" : "Cue review";
  }
  if (railSub) {
    railSub.textContent = setFile
      ? "Sets/Pajamathon: cue this file. Do not Move to Ready"
      : "Cue, pick a House folder, then sort";
  }
  ["toNoCuesBtn", "toLowSkipBtn", "toAcLowBtn"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.disabled = !hasTrack || setFile;
    if (setFile) {
      el.title = "Park/Ready is only for Add Cues inbox tracks. This file already lives in the set.";
    }
  });
  const deleteBtn = $("deleteAddCuesBtn");
  if (deleteBtn) {
    deleteBtn.disabled = !hasTrack;
    if (hasTrack) {
      deleteBtn.removeAttribute("disabled");
      deleteBtn.setAttribute("aria-disabled", "false");
      deleteBtn.textContent = setFile ? "Delete from Pajamathon" : "Delete from Add Cues";
      deleteBtn.title = setFile
        ? "Remove this Sets/Pajamathon name and its VirtualDJ entry. Library and inbox hard-links stay."
        : "Trash this Add Cues file and remove its VirtualDJ entry. The Pajamathon set copy stays.";
    }
  }
  const deleteHint = document.querySelector(".review-section-delete .hint");
  if (deleteHint) {
    deleteHint.textContent = setFile
      ? "Set copy + stems → Trash · this path’s VDJ cues go with it. Library copies stay."
      : "Audio + stems → Trash · this path’s VDJ cues go with it. Set copy stays.";
  }
}

/* ===== House fork: recommendation + destination picking (existing House subfolders + New folder) ===== */
const HOUSE_MODEL_FALLBACK = "gemini-3.8-flash";
const NEW_FOLDER_CAP = 3;

function flattenFolderPaths(nodes, out = []) {
  for (const n of nodes || []) {
    if (n && n.relative_path) out.push(n.relative_path);
    flattenFolderPaths(n && n.children, out);
  }
  return out;
}

function houseSortFolders() {
  const fromTree = flattenFolderPaths(state.folders);
  if (fromTree.length) return fromTree;
  const p = (state.health && state.health.profile) || {};
  const list = p.sort_folders || p.house_sort_folders;
  return Array.isArray(list) ? list : [];
}

function newFolderInfo() {
  const n = state.newFolders || (state.health && state.health.profile && state.health.profile.new_folders);
  return n || { count: 0, max: NEW_FOLDER_CAP, remaining: NEW_FOLDER_CAP, folders: [] };
}

function isReadonlyBuild() {
  const h = state.health || {};
  return Boolean(h.readonly || (h.profile && h.profile.readonly));
}

function cleanRelPath(path) {
  return String(path || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean)
    .join("/");
}

/** Snap a path to one of the EXISTING House subfolders, or "" when it is not one. */
function ensureSortFolder(path) {
  const raw = cleanRelPath(path);
  if (!raw) return "";
  const hit = houseSortFolders().find((n) => n.toLowerCase() === raw.toLowerCase());
  return hit || "";
}

/* A "new folder" destination that already exists (stale flag after the first sort created it, other
   letter case, same leaf under another parent, Hypnotic ~ Hypnotics) is simply the existing folder.
   Sorting must never be blocked by that. */
function existingHouseFolderFor(path) {
  const raw = cleanRelPath(path);
  if (!raw) return "";
  const list = houseSortFolders();
  const lc = raw.toLowerCase();
  let hit = list.find((n) => n.toLowerCase() === lc);
  if (hit) return hit;
  const leaf = lc.split("/").pop();
  hit = list.find((n) => n.toLowerCase().split("/").pop() === leaf);
  if (hit) return hit;
  const key = (t) => String(t).toLowerCase().replace(/[^a-z0-9]/g, "");
  const k = key(leaf);
  return (
    list.find((n) => {
      const l = key(n.split("/").pop());
      return l && (l === k || l + "s" === k || l === k + "s");
    }) || ""
  );
}

function recHousePick(rec) {
  if (!rec) return null;
  return rec.house || (rec.relative_path ? rec : null);
}

function renderRecommendation() {
  const recBox = $("recommendation");
  if (!recBox) return;
  const track = currentTrack();

  if (!track) {
    recBox.hidden = true;
    return;
  }
  // The card/description/tags belong to ONE song: another song's recommendation is never drawn for this one.
  const rec = state.recommendationPath === track.path ? state.recommendation : null;
  recBox.hidden = false;

  if (rec === null) {
    recBox.className = "recommendation loading review-lane-rec";
    recBox.innerHTML = "Asking Gemini for a House folder…";
    return;
  }
  if (rec.error) {
    recBox.className = "recommendation error review-lane-rec";
    recBox.innerHTML = `<strong>Gemini rec failed</strong><div class="rec-reason">${escapeHtml(
      rec.error
    )}</div>`;
    return;
  }
  const pick = recHousePick(rec) || {};
  const pickIsNew = Boolean(pick.new_folder);
  const destFolder = pickIsNew
    ? cleanRelPath(pick.relative_path || rec.relative_path || "")
    : ensureSortFolder(pick.relative_path || rec.relative_path || "");
  const model = rec.model || (state.health && state.health.gemini_model) || HOUSE_MODEL_FALLBACK;
  const reason = pick.reasoning || rec.reasoning || "";
  const conf = Math.round((pick.confidence != null ? pick.confidence : rec.confidence || 0) * 100);
  const alts = (pick.alternatives || rec.alternatives || [])
    .map((a) => ensureSortFolder(a))
    .filter((a) => a && a !== destFolder);
  const tagList = [];
  (rec.vibe_tags || []).forEach((t) => {
    const label = titleCaseTag(t);
    if (label && !tagList.some((x) => x.toLowerCase() === label.toLowerCase())) tagList.push(label);
  });
  const descLine = String(rec.description || "").trim();
  const nf = newFolderInfo();
  const bpmBit =
    rec.bpm != null && Number.isFinite(Number(rec.bpm)) ? ` · ${Number(rec.bpm).toFixed(1)} BPM` : "";
  const tags = `
      <div class="rec-descriptors" id="recDescriptors">
        ${descLine ? `<p class="rec-desc" id="recDesc">${escapeHtml(descLine)}</p>` : ""}
        ${
          tagList.length
            ? `<div class="rec-tag-chips" id="recTagChips">${tagList
                .map(
                  (t) =>
                    `<button type="button" class="tag-chip" data-action="tag-folder" data-tag="${escapeHtml(
                      t
                    )}" title="Offer 'New folder: ${escapeHtml(t)}' as the destination (nothing is created until you Sort)">${escapeHtml(
                      t
                    )}</button>`
                )
                .join("")}</div>`
            : ""
        }
        <div class="rec-newfolder" id="recNewFolder" role="status" aria-live="polite"></div>
        <div class="rec-newfolder-count" id="recNewFolderCount">New folders created: ${nf.count || 0} (no limit)${bpmBit}</div>
      </div>`;
  recBox.className = "recommendation review-lane-rec";
  recBox.innerHTML = `
      <button type="button" class="review-lane-rec-btn" data-action="use-rec" data-new="${pickIsNew ? "1" : ""}" data-path="${escapeHtml(
        destFolder
      )}"${destFolder ? "" : " disabled"}>
        <div class="subtitle">Gemini rec · ${escapeHtml(model)}${rec.cached ? " (cached)" : ""}</div>
        <div class="rec-path">${escapeHtml(
          destFolder ? (pickIsNew ? `New folder in House: ${destFolder}` : destFolder) : "No matching House folder"
        )}${destFolder ? ` · ${conf}%` : ""}</div>
        <div class="rec-reason">${escapeHtml(reason)}</div>
      </button>
      ${tags}
      ${
        alts.length
          ? `<div class="rec-alts">${alts
              .map(
                (a) =>
                  `<button type="button" class="chip" data-action="use-one" data-path="${escapeHtml(
                    a
                  )}">${escapeHtml(a)}</button>`
              )
              .join("")}</div>`
          : ""
      }
    `;
  const applyRecPath = (path, isNew = false) => {
    if (!path) return;
    if (currentTrack()?.path !== track.path) return;
    applySortDest("House", path, { newFolder: isNew });
  };
  const btn = recBox.querySelector("[data-action='use-rec']");
  if (btn) btn.addEventListener("click", () => applyRecPath(btn.dataset.path || destFolder, pickIsNew));
  recBox.querySelectorAll("[data-action='tag-folder']").forEach((chip) => {
    chip.addEventListener("click", () => offerTagFolder(chip.dataset.tag, track));
  });
  recBox.querySelectorAll("[data-action='use-one']").forEach((chip) => {
    chip.addEventListener("click", () => applyRecPath(chip.dataset.path));
  });
  const destsEmpty = !((state.selectedDests || []).some((d) => d && d.path));
  if (destsEmpty && destFolder && !pickIsNew && !isGroupRootFolder(destFolder) && (isReviewMode() || isSetOverviewMode())) applyRecPath(destFolder);
}

function titleCaseTag(tag) {
  return String(tag || "")
    .replace(/[\\/:<>"|?*\u0000-\u001f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .map((w) =>
      w
        .split("-")
        .map((p) => p.charAt(0).toUpperCase() + p.slice(1).toLowerCase())
        .join("-")
    )
    .join(" ");
}

/* Tag chip click: ask the server what 'New folder: <Tag>' would be. Creates NOTHING - the folder is
   only created by the safe copy-in when you press Sort. */
async function offerTagFolder(tag, track) {
  const box = $("recNewFolder");
  const countEl = $("recNewFolderCount");
  if (!box) return;
  box.className = "rec-newfolder";
  box.textContent = "…";
  let info;
  try {
    info = await api(`/api/house-folder-suggest?tag=${encodeURIComponent(tag)}`);
  } catch (err) {
    box.className = "rec-newfolder is-error";
    box.textContent = err.message;
    return;
  }
  if (currentTrack()?.path !== track.path) return;
  state.newFolders = { count: info.count, max: info.max, remaining: info.remaining, folders: info.folders || [] };
  if (countEl) countEl.textContent = `New folders created: ${info.count} (no limit)`;
  box.dataset.status = info.status;
  if (info.status === "new") {
    box.className = "rec-newfolder is-new";
    box.innerHTML = `<button type="button" class="btn ghost" id="newFolderTagBtn" data-name="${escapeHtml(
      info.name
    )}">New folder: ${escapeHtml(info.name)}</button><span class="hint"> created only when you Sort · no limit on new folders</span>`;
    box.querySelector("#newFolderTagBtn").addEventListener("click", () => {
      if (currentTrack()?.path !== track.path) return;
      applySortDest("House", info.name, { newFolder: true });
      renderFolders();
      updateApproveButtons();
    });
  } else if (info.status === "similar_exists") {
    box.className = "rec-newfolder is-similar";
    box.innerHTML = `<span>${escapeHtml(info.message)}</span> <button type="button" class="btn ghost" id="useExistingTagBtn">Use ${escapeHtml(
      info.existing
    )}</button>`;
    box.querySelector("#useExistingTagBtn").addEventListener("click", () => {
      if (currentTrack()?.path !== track.path) return;
      applySortDest("House", info.existing);
      renderFolders();
      updateApproveButtons();
    });
  } else if (info.status === "cap_reached") {
    box.className = "rec-newfolder is-cap";
    box.textContent = info.message;
  } else {
    box.className = "rec-newfolder is-error";
    box.textContent = info.message || "Cannot use that tag as a folder.";
  }
}

function pathModeLabel() {
  return "House";
}

function destKey(library, relativePath) {
  return `${library}::${relativePath}`;
}

function hasDest(library, relativePath) {
  const key = destKey(library, relativePath);
  return state.selectedDests.some((d) => d.key === key);
}

function selectedDestCount() {
  return (state.selectedDests || []).length;
}

function formatSelectedDestsLabel() {
  const dests = state.selectedDests || [];
  if (!dests.length) return "None selected";
  return dests.map((d) => `House / ${d.path}${d.newFolder ? " (new folder)" : ""}`).join(" · ");
}

function updatePathHint() {
  const el = $("pathHint");
  if (!el) return;
  el.textContent =
    "Pick an existing House folder (or New folder in House). Sort COPIES the track and keeps the original.";
}

function liveSortDestHint() {
  const dests = (state.selectedDests || []).filter((d) => d && d.path);
  if (!dests.length) {
    return "Pick the Gemini rec or a House folder · Sort copies, original stays";
  }
  const base = `House/${dests[0].path}`;
  return isReadonlyBuild() ? `${base} · read-only preview (nothing is copied)` : `${base} · copy (original stays)`;
}

function applySortDest(_library, relativePath, opts = {}) {
  if (isGroupRootFolder(relativePath) && !(opts && opts.newFolder)) {
    loudNotice(`“${relativePath}” is a group folder - pick one of the folders inside it.`, "warn");
    return;
  }
  let isNew = Boolean(opts && opts.newFolder);
  if (isNew) {
    const existing = existingHouseFolderFor(relativePath);
    if (existing) {
      relativePath = existing;
      isNew = false;
    }
  }
  const folder = isNew ? cleanRelPath(relativePath) : ensureSortFolder(relativePath);
  state.selectedDests = folder
    ? [{ library: "House", path: folder, key: destKey("House", folder), newFolder: isNew }]
    : [];
  state.selectedPath = folder;
  state.selectedPathLibrary = "House";
  updateSelectionLabels();
  if (typeof updateApproveButtons === "function") updateApproveButtons();
  syncSortButtonState();
  renderFolders();
}

/* Lanes do not exist in the House profile; keep inert stubs for old call sites. */
function laneFromFolderPath() {
  return "";
}
function syncLaneFromManualFolder() {}
function recommendedLaneFromRec() {
  return "";
}
function renderLanePicker() {
  const box = $("lanePicker");
  if (box) box.innerHTML = "";
}
function selectLane() {}

function resolveSortTrack() {
  const selected = currentTrack();
  if (selected) return selected;
  const audio = $("audio");
  const path = audio && audio.dataset && audio.dataset.path;
  if (path) {
    const hit = (state.tracks || []).find((t) => t.path === path);
    if (hit) return hit;
  }
  return null;
}

function syncSortButtonState() {
  const track = resolveSortTrack();
  const n = selectedDestCount();
  const sortBtn = $("sortBtn");
  if (!sortBtn) return;
  if (copyButtonsLocked()) {
    sortBtn.disabled = true;
    sortBtn.classList.add("is-waiting");
    if (!sortBtn.dataset.busy && state.copyAdvanceLock) sortBtn.textContent = "Loading next song…";
    return;
  }
  const canSort = Boolean(track && track.is_cued && n > 0);
  sortBtn.disabled = !canSort;
  sortBtn.classList.toggle("is-waiting", !canSort && !sortBtn.dataset.busy);
  sortBtn.classList.toggle("btn-cta", canSort || Boolean(sortBtn.dataset.busy));
  if (!sortBtn.dataset.busy) {
    const dests = state.selectedDests || [];
    if (!track) {
      sortBtn.textContent = "Select a track →";
    } else if (!track.is_cued) {
      sortBtn.textContent = "Track not cued";
    } else if (n === 0) {
      sortBtn.textContent = "Select a House folder →";
    } else if (isReadonlyBuild()) {
      sortBtn.textContent = `Preview COPY TO House / ${dests[0].path}`;
    } else {
      sortBtn.textContent = `COPY TO House / ${dests[0].path}`;
    }
  }
  const step = $("sortRailStepLabel");
  if (step) step.textContent = canSort ? "Primary · ready" : "Primary";
  const railTitle = $("foldersRailTitle");
  if (railTitle) railTitle.textContent = canSort ? "Copy destination" : "Choose a House folder";
  const railSub = $("foldersRailSubtitle");
  if (railSub) {
    railSub.textContent = canSort
      ? isReadonlyBuild()
        ? "Read-only: copy is a dry-run preview"
        : "One click COPIES the track · original file stays, the song leaves this list"
      : "Pick a House folder, then copy · original stays";
  }
}

/* "Also copy to Sets/Sauna Fest" toggle (default ON, remembered): every House sort mirrors the
   same subfolder under Sets/Sauna Fest in the same all-or-nothing write. */
function alsoSaunaFest() {
  const chk = $("alsoSaunaChk");
  return chk ? Boolean(chk.checked) : true;
}

function updateSelectionLabels() {
  const label = formatSelectedDestsLabel();
  const sel = $("selectedFolder");
  if (sel) sel.textContent = label;
  syncSortButtonState();
  if (typeof updateApproveButtons === "function") updateApproveButtons();
}

function applyFolderSelection(library, relativePath) {
  applySortDest(library, relativePath);
}

function setSingleDest(library, relativePath) {
  applySortDest(library, relativePath);
}

function toggleDest(library, relativePath) {
  const folder = ensureSortFolder(relativePath);
  if (!folder) return;
  if (isGroupRootFolder(folder)) {
    loudNotice(`“${folder}” is a group folder - pick one of the folders inside it (e.g. ${folder}/${folder === "Energy" ? "Housey" : "Journey"}).`, "warn");
    return;
  }
  if (hasDest("House", folder)) {
    state.selectedDests = [];
    state.selectedPath = "";
    state.selectedPathLibrary = "";
    updateSelectionLabels();
    renderFolders();
    return;
  }
  applySortDest("House", folder);
}

function clearSelectedDests() {
  state.selectedDests = [];
  state.selectedPath = "";
  state.selectedPathLibrary = "";
  updateSelectionLabels();
  renderFolders();
}

function selectFolder(relativePath) {
  applySortDest("House", relativePath);
}

/**
 * The House folder picker always starts unfiltered (all existing House folders). A browser can
 * restore the search box text (or an old build may have left a remembered filter such
 * as a stale folder name) — clear both, and drop legacy remembered-filter keys.
 */
function resetFolderPickerFilter() {
  state.filter = "";
  const input = $("folderFilter");
  if (input) input.value = "";
  try {
    for (const key of Object.keys(localStorage)) {
      if (/folder.?filter|selected.?(dest|folder|path)/i.test(key)) {
        localStorage.removeItem(key);
      }
    }
  } catch {
    /* ignore */
  }
  if (Array.isArray(state.folders) && state.folders.length) renderFolders();
}

function folderMatchesFilter(node, filter) {
  if (!filter) return true;
  const f = filter.toLowerCase();
  return String(node.relative_path || "").toLowerCase().includes(f) ||
    String(node.name || "").toLowerCase().includes(f);
}

/* ===== Song-level color per House subfolder (house_folder_colors.json via /api/house-folder-colors) ===== */
function houseFolderColor(rel) {
  const hc = state.houseColors;
  if (!hc || !hc.exists) return null;
  const parts = String(rel || "").split(/[\\/]+/).filter(Boolean);
  if (!parts.length) return null;
  for (const cand of [parts.join("/"), parts[0]]) {
    const hit = hc.byKey[cand.toLowerCase()];
    if (hit) return hit;
  }
  return hc.default || null;
}

/** Readable text color (near-black / white) for a solid #RRGGBB background. */
function readableOn(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return "#fff";
  const n = parseInt(m[1], 16);
  const lin = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const L = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
  return L > 0.4 ? "#10141a" : "#ffffff";
}

function houseColorStyle(c) {
  return c ? ` style="--fc:${c.hex};--fc-ink:${readableOn(c.hex)}"` : "";
}

async function loadHouseColors() {
  try {
    const data = await api("/api/house-folder-colors");
    const byKey = {};
    for (const f of data.folders || []) byKey[String(f.folder).toLowerCase()] = f;
    state.houseColors = { exists: Boolean(data.exists), byKey, folders: data.folders || [], default: data.default || null };
  } catch {
    state.houseColors = { exists: false, byKey: {}, folders: [], default: null };
  }
}

function renderHouseColorLegend() {
  const host = $("houseColorLegend");
  if (!host) return;
  const hc = state.houseColors;
  if (!hc || !hc.exists || !(hc.folders || []).length) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  host.hidden = false;
  host.innerHTML =
    `<span class="legend-title">Song color in VirtualDJ</span>` +
    hc.folders
      .map(
        (f) =>
          `<span class="legend-item" title="${escapeHtml(`${f.folder}: ${f.name || f.hex} — set on the song when you sort here`)}"${houseColorStyle(f)}><span class="legend-dot"></span>${escapeHtml(f.folder)}</span>`
      )
      .join("") +
    (hc.default
      ? `<span class="legend-item legend-default" title="New folders get this color"${houseColorStyle(hc.default)}><span class="legend-dot"></span>new folder</span>`
      : "");
}

const GROUP_ROOT_FOLDERS = ["chill", "energy"];
function isGroupRootFolder(path) {
  return GROUP_ROOT_FOLDERS.includes(String(path || "").replace(/^\/+|\/+$/g, "").toLowerCase());
}

function renderFolderNode(node, depth = 0, library = "House") {
  const kids = (node.children || []).map((k) => renderFolderNode(k, depth + 1, library)).join("");
  if (!folderMatchesFilter(node, state.filter) && !kids) return "";
  const path = node.relative_path;
  const selected = hasDest("House", path);
  const recPick = recHousePick(state.recommendation) || {};
  const recPath = recPick.new_folder ? "" : ensureSortFolder(recPick.relative_path || "");
  const isRec = Boolean(recPath) && recPath === path;
  const count = node.track_count != null ? node.track_count : node.count != null ? node.count : 0;
  const fc = houseFolderColor(path);
  if (isGroupRootFolder(path)) {
    // Chill / Energy only group their sub-folders: shown as a header, never a destination.
    return `
    <div class="folder-node house-folder-node folder-group" style="margin-left:${depth * 14}px">
      <div class="folder-row folder-group-head" data-group="${escapeHtml(path)}" title="Group folder - pick one of the folders inside">
        <span class="folder-name">${escapeHtml(node.name || path)}</span>
        <span class="folder-sort-to">group · pick a folder inside</span>
      </div>
      ${kids}
    </div>`;
  }
  return `
    <div class="folder-node house-folder-node" style="margin-left:${depth * 14}px">
      <div class="folder-row">
        <button type="button" class="folder ${selected ? "selected" : ""} ${isRec ? "recommended" : ""} ${fc ? "has-folder-color" : ""}"${houseColorStyle(fc)}
                data-path="${escapeHtml(path)}" data-lib="House" aria-pressed="${selected ? "true" : "false"}"
                title="${escapeHtml(`House/${path} — sort copies here, original stays${fc ? ` · song color ${fc.name || fc.hex}` : ""}`)}">
          <span class="folder-copy">
            <span class="folder-name">${fc ? '<span class="folder-swatch"></span>' : ""}${escapeHtml(node.name || path)}${
              isRec ? ' <span class="badge ok">Gemini</span>' : ""
            }</span>
            <span class="folder-sort-to">${fc ? `<span class="sort-to-color">${escapeHtml(fc.name || "color")}</span> · ` : ""}${count} in folder</span>
          </span>
        </button>
      </div>
      ${kids}
    </div>`;
}

function renderNewFolderBlock() {
  const info = newFolderInfo();
  const left = Infinity; // no cap on House folders any more
  const parents = (state.folders || [])
    .map((n) => `<option value="${escapeHtml(n.relative_path)}">${escapeHtml(n.relative_path)}</option>`)
    .join("");
  const draft = state.newFolderDraft || { parent: "", name: "" };
  const capped = false;
  return `
    <div class="house-new-folder" id="houseNewFolder" style="margin-top:10px;padding:8px;border:1px dashed var(--border, #888);border-radius:6px">
      <div class="subtitle"><strong>New folder in House</strong> · no limit</div>
      ${
        capped
          ? `<div class="hint error">Limit reached (${escapeHtml((info.folders || []).join(", "))}). Ask Kirill before creating another folder.</div>`
          : `<div class="hint">Use only when no existing folder fits. Created on the first copy into it.</div>
      <label class="hint">Under <select id="newFolderParent"><option value="">House (top level)</option>${parents}</select></label>
      <input type="text" id="newFolderName" maxlength="60" placeholder="New folder name" value="${escapeHtml(draft.name)}" autocomplete="off" />
      <button type="button" class="btn ghost" id="newFolderUse">Use new folder</button>
      <div class="hint error" id="newFolderErr" hidden></div>`
      }
    </div>`;
}

function bindNewFolderBlock(root) {
  const useBtn = root.querySelector("#newFolderUse");
  if (!useBtn) return;
  const parentEl = root.querySelector("#newFolderParent");
  const nameEl = root.querySelector("#newFolderName");
  const errEl = root.querySelector("#newFolderErr");
  if (parentEl) parentEl.value = (state.newFolderDraft && state.newFolderDraft.parent) || "";
  const remember = () => {
    state.newFolderDraft = { parent: parentEl.value, name: nameEl.value };
  };
  const NAME_PROBLEM_RE = /^(Type a folder name\.|No slashes, colons or|Name cannot start with a dot)/;
  const clearNameProblem = () => {
    // R-99: the warning only lives while the name is still wrong - typing again takes it away (box AND red/yellow toast).
    if (errEl && !errEl.hidden && NAME_PROBLEM_RE.test(errEl.textContent || "")) errEl.hidden = true;
    if (state.loudNotice && NAME_PROBLEM_RE.test(String(state.loudNotice.text || ""))) {
      state.loudNotice = null;
      renderSaveBadges();
    }
  };
  parentEl.addEventListener("change", remember);
  nameEl.addEventListener("input", () => {
    remember();
    clearNameProblem();
  });
  useBtn.addEventListener("click", () => {
    const name = String(nameEl.value || "").trim();
    let problem = "";
    if (!name) problem = "Type a folder name.";
    else if (/[\\/:]/.test(name) || name.includes("..")) problem = "No slashes, colons or '..' in the name.";
    else if (name.startsWith(".")) problem = "Name cannot start with a dot.";
    if (!problem) {
      const wanted = parentEl.value ? `${parentEl.value}/${name}` : name;
      const existing = existingHouseFolderFor(wanted);
      if (existing) {
        // Not an error: that folder is already there - just use it.
        errEl.classList.remove("error");
        errEl.textContent = `Using the existing folder ${existing}.`;
        errEl.hidden = false;
        applySortDest("House", existing);
        return;
      }
    }
    errEl.classList.add("error");
    if (!problem) clearNameProblem();
    if (problem) {
      errEl.textContent = problem;
      errEl.hidden = false;
      loudNotice(problem, "warn");
      return;
    }
    errEl.hidden = true;
    const rel = parentEl.value ? `${parentEl.value}/${name}` : name;
    applySortDest("House", rel, { newFolder: true });
  });
}

function renderFolderSections(folders, _title, library) {
  const rows = (folders || []).map((n) => renderFolderNode(n, 0, library || "House")).join("");
  return rows;
}

function renderSelectedDestChips() {
  const host = $("selectedDestChips");
  if (!host) return;
  const dests = state.selectedDests || [];
  if (!dests.length) {
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  host.hidden = false;
  host.innerHTML =
    dests
      .map(
        (d) => `
      <button type="button" class="dest-chip ${houseFolderColor(d.path) ? "has-folder-color" : ""}"${houseColorStyle(houseFolderColor(d.path))} data-chip-key="${escapeHtml(
        d.key
      )}" title="Remove ${escapeHtml(d.path)}">
        <span>${escapeHtml(d.path)}</span>
        <span class="dest-chip-x" aria-hidden="true">×</span>
      </button>`
      )
      .join("") +
    `<button type="button" class="btn ghost dest-clear-btn" id="clearDestsBtn">Clear</button>`;
  host.querySelectorAll("[data-chip-key]").forEach((btn) => {
    btn.addEventListener("click", () => clearSelectedDests());
  });
  host.querySelector("#clearDestsBtn")?.addEventListener("click", () => clearSelectedDests());
}

function renderFolders() {
  const root = $("folderTree");
  if (!root) return;
  let html = renderFolderSections(state.folders, "", "House");
  if (!html) html = `<div class="empty">No House subfolders found.</div>`;
  root.innerHTML = html + renderNewFolderBlock();
  renderCueColorLegend();
  renderHouseColorLegend();
  bindNewFolderBlock(root);
  renderSelectedDestChips();
  root.querySelectorAll("button.folder[data-path]").forEach((btn) => {
    btn.addEventListener("click", () => {
      toggleDest("House", btn.dataset.path);
      if (typeof updateApproveButtons === "function") updateApproveButtons();
    });
  });
}

const UI_BUILD = "20261005-house-sauna-fest11";

/** House fork: profile flag on <body>, visible read-only badge, model label. */
function applyProfileUi(health) {
  const prof = (health && health.profile) || {};
  const house = prof.name === "house";
  document.body.dataset.profile = house ? "house" : prof.name || "";
  const ro = Boolean(health && (health.readonly || prof.readonly));
  document.body.classList.toggle("is-readonly", ro);
  const badge = $("readonlyBadge");
  if (badge) {
    badge.hidden = !ro;
    badge.textContent = "Read-only (VDJ open)";
    badge.title =
      "This build never writes to VirtualDJ or the library. Sorts are dry-run previews; cue, grid and notes edits are blocked.";
  }
  const model = $("modelBadge");
  if (model && health && health.gemini_model) {
    model.hidden = false;
    model.textContent = health.gemini_model;
  }
  const notes = $("vdjNotes");
  if (notes && ro) {
    notes.disabled = true;
    notes.placeholder = "Notes are read-only in this build";
  }
}

async function loadHealth() {
  state.health = await api("/api/health");
  const serverBuild = state.health && state.health.ui_build;
  if (serverBuild && serverBuild !== UI_BUILD) {
    const url = new URL(window.location.href);
    if (url.searchParams.get("v") === String(serverBuild)) return;
    url.searchParams.set("v", String(serverBuild));
    window.location.replace(url.toString());
    return;
  }
  const vdj = state.health.virtualdj_running;
  $("vdjBadge").className = `badge ${vdj ? "warn" : "ok"}`;
  $("vdjBadge").textContent = vdj ? "VirtualDJ running" : "VirtualDJ closed";
  applyProfileUi(state.health);
  // Do not wipe countsBadge — a concurrent loadTracks owns that label.
}

/** Re-check VDJ process right before a DB write (badge/health can be stale). */
async function isVdjRunningFresh() {
  // Edits must not wait on a health round-trip: reuse a recent answer (<15 s) unless it
  // says "running" (then re-check so a just-closed VirtualDJ doesn't nag). The server
  // still refuses the write if VirtualDJ is really running, so this is only the prompt.
  if (!isReadonlyBuild()) {
    const age = Date.now() - (state.healthAt || 0);
    if (!state.health || age > 15000 || state.health.virtualdj_running) {
      try {
        await loadHealth();
        state.healthAt = Date.now();
        updatePipelineStrip();
      } catch {
        /* keep last known health */
      }
    }
  }
  // Read-only build never writes, so the "VirtualDJ is open" write prompts are moot.
  if (isReadonlyBuild()) return false;
  return Boolean(state.health?.virtualdj_running);
}

function scheduleLoadTracks(opts = {}) {
  state.tracksLoadQueued = { ...(state.tracksLoadQueued || {}), ...opts, silent: true };
  if (state.tracksLoadTimer) return;
  state.tracksLoadTimer = setTimeout(() => {
    const next = state.tracksLoadQueued || { silent: true };
    state.tracksLoadQueued = null;
    state.tracksLoadTimer = null;
    loadTracks(next);
  }, 280);
}

async function loadTracks({ keepPath, skipStatus = false, silent = false } = {}) {
  const listEl = $("trackList");
  const requestedMode = state.mode;
  if (
    requestedMode === "stems" ||
    requestedMode === "recs" ||
    requestedMode === "assemble" ||
    requestedMode === "best_set"
  ) {
    if (listEl) listEl.classList.remove("list-loading");
    return;
  }
  const loadGen = ++state.tracksLoadGen;
  const selectGenAtStart = state.trackGen; // a click on another song after this point wins
  const revAtStart = new Map(markerRev); // songs edited after this point are newer than this list
  const haveTracks = Array.isArray(state.tracks) && state.tracks.length > 0;
  const soft = Boolean(silent || (haveTracks && requestedMode === "add_cues"));
  if (listEl && !soft) listEl.classList.add("list-loading");
  if (!skipStatus && !soft) {
    setStatus(
      requestedMode === "add_cues"
        ? "Loading Add Cues…"
        : requestedMode === "set_overview"
          ? "Loading Set Overview…"
          : "Loading Ready for Sort…"
    );
  } else if (!skipStatus && soft && requestedMode === "add_cues" && !isAutocueJobRunning()) {
    setStatus("Updating cue list…");
  }

  try {
    const crateQs =
      requestedMode === "add_cues" && isHouseProfile() && state.houseCrate
        ? `&crate=${encodeURIComponent(state.houseCrate)}`
        : "";
    const data = await api(`/api/tracks?mode=${encodeURIComponent(requestedMode)}${crateQs}`, {
      timeoutMs: 120000,
    });
    // Drop stale responses: mode switch or a newer refresh finished first.
    if (loadGen !== state.tracksLoadGen || state.mode !== requestedMode) {
      return;
    }
    if (data.mode && data.mode !== requestedMode) {
      return;
    }

    // The user may have clicked another song while this list was loading: that click wins. Keeping
    // the old keepPath here is what flashed the previous song back into the header.
    // keepPath is only a hint: a background reload (reconcile / failed save) of song A must not pull the
    // user back from song B. It is honoured when it IS the open song, or the open song left the list.
    const curPath = currentTrack()?.path;
    const curSurvives = Boolean(curPath) && (data.tracks || []).some((t) => t.path === curPath);
    const prevPath =
      keepPath && state.trackGen === selectGenAtStart && !(curSurvives && curPath !== keepPath)
        ? keepPath
        : curPath || keepPath || storedOpenSong(requestedMode);
    if (requestedMode === "add_cues" && isHouseProfile()) {
      renderHouseCrateSelect(data.crates, data.default_crate);
    }
    const pathBeforeReload = currentTrack()?.path;
    {
      const before = state.tracks;
      const merged = mergeLoadedPlacements(state.tracks, data.tracks || []);
      // A song with edits still queued keeps its on-screen markers; the quiet reload after the queue
      // drains brings in the saved truth. (A reload mid-queue is what used to make edits "vanish".)
      // Same for songs edited since this list was requested: the list is a stale snapshot for them. Keep the
      // on-screen markers; the next quiet reload brings the truth. Failed (unsaved) edits are re-applied on top.
      const editedSince = new Set();
      for (const [p, r] of markerRev) if (r !== (revAtStart.get(p) || 0)) editedSince.add(p);
      state.tracks = merged.map((t) =>
        (editPending.get(t.path) || 0) > 0 || editedSince.has(t.path)
          ? before.find((o) => o.path === t.path) || t
          : overlayFailedEdits(dropOutOfSongPoints(t))
      );
    }
    if (requestedMode === "set_overview") {
      applyApprovedPaths(data.approved_paths);
      applyMustPlayPaths(data.must_play_paths);
    }
    const counts = data.counts || {};

    if (requestedMode === "set_overview") {
      $("countsBadge").textContent = `${counts.total || 0} in set · ${
        counts.different || 0
      } different`;
    } else if (requestedMode === "add_cues") {
      const paj = isHouseProfile() ? 0 : counts.pajamathon || 0; // the House app has no Pajamathon queue
      $("countsBadge").textContent = paj
        ? `Pajamathon ${counts.pajamathon_not_cued || 0}/${paj} need cues · ${
            counts.not_cued || 0
          } not cued`
        : `${counts.ready || 0} ready · ${counts.partial || 0} partial · ${
            counts.not_cued || 0
          } not cued`;
      $("countsBadge").className =
        (counts.ready || 0) > 0 ? "badge ok" : "badge warn";
    } else {
      $("countsBadge").textContent = `${counts.cued || 0} cued · ${counts.uncued || 0} not cued`;
      $("countsBadge").className = counts.uncued ? "badge uncued" : "badge ok";
    }

    let idx = state.tracks.findIndex((t) => t.path === prevPath);
    if (idx < 0) {
      const filtered = filteredTrackIndexes();
      idx = filtered.length ? filtered[0] : 0;
    }
    state.index = idx;
    // trackGen is the per-load token of the OPEN song: a list refresh that keeps the same song open must not
    // invalidate that song's in-flight waveform / meta / save continuations.
    if (state.tracks[idx]?.path !== pathBeforeReload) {
      state.trackGen += 1;
      state.genForPath = state.tracks[idx]?.path || null;
    }
    syncHouseTools();
    renderTrackList();
    renderPlayer();
    if (currentTrack() && !isPracticeMode() && !isRecsMode() && !isAssembleMode()) {
      loadTrackPlacements(currentTrack());
    }
    if (currentTrack() && (requestedMode === "add_cues" || requestedMode === "sort" || requestedMode === "set_overview")) requestRecommendation(currentTrack());
    // Callers that just finished promote/sort pass skipStatus and set their own
    // success handoff *after* this returns so the CTA is not wiped.
    if (!skipStatus) {
      setStatus(
        requestedMode === "add_cues"
          ? counts.pajamathon && !isHouseProfile()
            ? `Add Cues · ${counts.pajamathon} Pajamathon · ${counts.inbox || 0} inbox`
            : `Add Cues · ${counts.total || state.tracks.length} tracks · primary action on the right`
          : requestedMode === "set_overview"
            ? `Set Overview · ${counts.total || state.tracks.length} · same / different · Remove · Send back · Approved`
            : `Ready for Sort · ${counts.total || state.tracks.length} tracks · pick a folder, then Sort`
      );
    }
    updateBatchAddCuesButton();
    updatePipelineStrip();
    syncSortButtonState();
    renderLanePicker();
    if (typeof updateApproveButtons === "function") updateApproveButtons();
    if (requestedMode === "set_overview") renderSetOverviewRail();
  } catch (err) {
    if (loadGen !== state.tracksLoadGen || state.mode !== requestedMode) {
      return;
    }
    if (!skipStatus) {
      setStatus(err.message || "Failed to load tracks", "error");
    } else {
      throw err;
    }
    if (listEl && !state.tracks.length) {
      listEl.innerHTML = emptyStateHtml({
        icon: "!",
        title: "Could not load tracks",
        copy: err.message || "Network error",
        ctaLabel: "Retry",
        ctaMode: "",
      }).replace(
        'data-goto-mode=""',
        'id="retryLoadTracksBtn" data-goto-mode=""'
      );
      const retry = $("retryLoadTracksBtn");
      if (retry) {
        retry.removeAttribute("data-goto-mode");
        retry.addEventListener("click", () => loadTracks({ keepPath }));
      }
    }
  } finally {
    // Only clear loading style if this is still the latest load for this mode.
    if (listEl && loadGen === state.tracksLoadGen) {
      listEl.classList.remove("list-loading");
      await hydrateAutocueJobs();
    }
  }
}

function applyModeUi() {
  syncReadinessFilterLabels();
  const review = isReviewMode();
  const practice = isPracticeMode();
  const recs = isRecsMode();
  const assemble = isAssembleMode();
  const stems = isStemsMode();
  const bestSet = isBestSetMode();
  const setOverview = isSetOverviewMode();
  document.body.classList.toggle("mode-practice", practice);
  document.body.classList.toggle("mode-recs", recs);
  document.body.classList.toggle("mode-assemble", assemble);
  document.body.classList.toggle("mode-stems", stems);
  document.body.classList.toggle("mode-review", review);
  document.body.classList.toggle("mode-best-set", bestSet);
  document.body.classList.toggle("mode-set-overview", setOverview);
  document.body.classList.toggle("mode-sort", !review && !practice && !recs && !assemble && !stems && !bestSet && !setOverview);
  document.body.classList.toggle("practice-stack-layout", practice);

  $("listTitle").textContent = bestSet
    ? "Best for set"
    : practice
    ? "Practice mixes"
    : setOverview
      ? "Set Overview"
      : isAssembleMode()
      ? "Pajamathon"
      : isRecsMode()
        ? "Live from VDJ"
        : isStemsMode()
          ? "Stem check"
          : review
          ? state.crateFilter === "pajamathon"
            ? "Add Cues / Pajamathon"
            : state.crateFilter === "cueing"
              ? "Currently cueing"
              : "Add Cues"
          : "Ready for Sort";
  $("listSubtitle").textContent = bestSet
    ? "Play a keeper · Pause / Stop stay on this page"
    : practice
    ? "Select a mix to analyze"
    : setOverview
      ? "same / different · Copy cues · Remove · Send back · Approved"
      : isAssembleMode()
      ? "Newest first · vibe crate"
      : isRecsMode()
        ? "Follows VDJ / STAGE now-playing"
        : isStemsMode()
          ? "Vocal-layer holes vs the original mix"
          : review
          ? state.crateFilter === "pajamathon"
            ? "Cue, pick a House folder, then sort"
            : state.crateFilter === "cueing"
              ? "Tracks AutoCue is working on right now"
              : "Cue, pick a House folder, then sort"
          : "Cue, pick a House folder, then sort";
  const playerHeading = $("playerHeading");
  if (playerHeading) {
    playerHeading.textContent = practice ? "Mix playback" : "Now playing";
  }

  // Stage subtitle is redundant with pipeline — hide in all modes.
  const subEl = $("playerSubtitle");
  if (subEl) {
    subEl.textContent = "";
    subEl.hidden = true;
  }
  $("listToolbar").hidden = (!review && !setOverview) || isRecsMode() || isAssembleMode() || isStemsMode();
  const trackSearch = $("trackSearch");
  if (trackSearch) {
    trackSearch.placeholder = practice
      ? "Search practice mixes…"
      : setOverview
        ? "Search the set…"
        : review
        ? "Search Add Cues…"
        : "Search Ready for Sort…";
  }
  const recsMode = isRecsMode();
  const assembleMode = isAssembleMode();
  const stemsMode = isStemsMode();
  $("foldersPanel").hidden = review || practice || recsMode || assembleMode || stemsMode || bestSet || setOverview;
  const crateFilter = $("crateFilter");
  if (crateFilter) crateFilter.hidden = setOverview;
  const readinessFilter = $("readinessFilter");
  if (readinessFilter) readinessFilter.hidden = setOverview;
  const setDirFilter = $("setDirFilter");
  if (setDirFilter) setDirFilter.hidden = !setOverview;
  const setApprovalFilter = $("setApprovalFilter");
  if (setApprovalFilter) setApprovalFilter.hidden = !setOverview;
  if (setOverview) {
    renderSetDirFilter();
    document.querySelectorAll("#setApprovalFilter button").forEach((b) => {
      b.classList.toggle("active", b.dataset.setApproval === (state.setApprovalFilter || "all"));
    });
  }
  renderLanePicker();
  syncHouseTools();
  const houseCrateWrap = $("houseCrateWrap");
  if (houseCrateWrap) houseCrateWrap.hidden = !(isHouseProfile() && review);
  if (isHouseProfile() && crateFilter) crateFilter.hidden = true;
  $("reviewPanel").hidden = !review;
  const setOverviewPanel = $("setOverviewPanel");
  if (setOverviewPanel) setOverviewPanel.hidden = !setOverview;
  if (typeof mountSharedSortRail === "function") mountSharedSortRail();
  if (setOverview) renderSetOverviewRail();
  const practicePanel = $("practicePanel");
  if (practicePanel) practicePanel.hidden = !practice;
  const bestSetPanel = $("bestSetPanel");
  if (bestSetPanel) bestSetPanel.hidden = !bestSet;
  const recsPanel = $("recsPanel");
  if (recsPanel) recsPanel.hidden = !recsMode;
  const assemblePanel = $("assemblePanel");
  if (assemblePanel) assemblePanel.hidden = !assembleMode;
  const stemsPanel = $("stemsPanel");
  if (stemsPanel) stemsPanel.hidden = !stemsMode;
  const playerPanel = $("playerPanel");
  if (playerPanel) playerPanel.hidden = bestSet || stemsMode;
  const queue = document.querySelector(".zone-queue");
  if (queue) queue.hidden = bestSet;

  // Practice: transport + transition waveform; hide sort/cue chrome.
  const hideInPractice = [
    "speedPanel",
    "notesPanel",
    "cuesPanel",
    "blockBanner",
    "placementCard",
    "recommendation",
    "sortActions",
    "reviewActions",
  ];
  hideInPractice.forEach((id) => {
    const el = $(id);
    if (!el) return;
    if (practice || recsMode || assembleMode || bestSet || stemsMode) {
      el.hidden = true;
    } else if (id === "sortActions") {
      el.hidden = review || setOverview;
    } else if (id === "reviewActions") {
      el.hidden = !review;
    } else if (id === "recommendation") {
      el.hidden = review || setOverview;
    } else if (id === "blockBanner" || id === "placementCard") {
      // leave to their own renderers when leaving practice
    } else {
      el.hidden = false;
    }
  });
  if (practice) {
    updatePracticeWaveVisibility();
  } else {
    const practiceWave = $("practiceWavePanel");
    if (practiceWave) {
      practiceWave.hidden = true;
      practiceWave.classList.remove("is-empty");
    }
  }
  $("rerunRecBtn").hidden = review || setOverview || practice || bestSet || isRecsMode() || isAssembleMode() || isStemsMode();

  // AutoCue scope buttons live in Add Cues review, not Sort.
  const headerScopes = $("autocueScopeHeader");
  if (headerScopes) headerScopes.hidden = !review;
  const retryStatus = $("retryStatus");
  if (retryStatus && !review) {
    retryStatus.hidden = true;
  }
  if (review) syncAutocueUi();
  if (practice) {
    $("shortcutsHint").innerHTML = `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> mixes ·
       <span class="kbd">←</span>/<span class="kbd">→</span> beat ·
       Seek on a transition to jump −20s ·
       <span class="kbd">?</span> keys`;
  } else if (review) {
    $("shortcutsHint").innerHTML = `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> tracks ·
       <span class="kbd">←</span>/<span class="kbd">→</span> beat ·
       <span class="kbd">1</span>–<span class="kbd">9</span> jump cues ·
       <span class="kbd">L</span> loop ·
       <span class="kbd">C</span> cue · <span class="kbd">O</span> loop place ·
       <span class="kbd">N</span> normal speed ·
       <span class="kbd">G</span> ones ·
       <span class="kbd">A</span>/<span class="kbd">S</span> approve/skip ·
       <span class="kbd">?</span> keys`;
  } else {
    $("shortcutsHint").innerHTML = `Shortcuts: <span class="kbd">Space</span> play/pause ·
       <span class="kbd">J</span>/<span class="kbd">K</span> tracks ·
       <span class="kbd">←</span>/<span class="kbd">→</span> beat ·
       <span class="kbd">1</span>–<span class="kbd">9</span> jump cues ·
       <span class="kbd">L</span> loop ·
       <span class="kbd">C</span> cue · <span class="kbd">O</span> loop place ·
       <span class="kbd">N</span> normal speed ·
       <span class="kbd">G</span> ones ·
       <span class="kbd">⌘</span>+<span class="kbd">Enter</span> sort ·
       <span class="kbd">?</span> keys`;
  }

  document.querySelectorAll("#modeSeg button").forEach((b) => {
    const on = b.dataset.mode === state.mode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const t = currentTrack();
  document.body.classList.toggle("track-is-cued", Boolean(t && t.is_cued));
  document.body.classList.toggle("has-track", Boolean(t));
  updatePipelineStrip();
  syncSortButtonState();
}

async function setMode(mode) {
  if (mode === "sort") mode = "add_cues";
  if (
    mode !== "sort" &&
    mode !== "add_cues" &&
    mode !== "practice" &&
    mode !== "best_set" &&
    mode !== "set_overview" &&
    mode !== "recs" &&
    mode !== "assemble" &&
    mode !== "stems"
  )
    return;
  if (state.mode === mode) {
    if (
      !state.tracks.length &&
      !isRecsMode() &&
      !isAssembleMode() &&
      !isStemsMode() &&
      !isBestSetMode()
    ) {
      loadTracks();
    }
    return;
  }
  // Leave recs → stop live polls
  stopRecsNowPlayingPoll();
  stopRecsPoll();
  stopAssemblePoll();
  stopStemsPoll();
  state.mode = mode;
  if (mode === "set_overview" && !state.setDirFilter) state.setDirFilter = "pajamathon";
  if (mode === "set_overview" && !state.setApprovalFilter) state.setApprovalFilter = "all";
  state.recommendation = null;
  state.selectedLane = "";
  state.recommendedLane = "";
  state.selectedPath = "";
  state.selectedPathLibrary = "";
  state.readinessFilter = "all";
  state.tracks = [];
  state.index = 0;
  state.practiceDetail = null;
  state.practiceMixPath = "";
  state.practiceMixes = [];
  state.trackMeta = null;
  state.gridPreflight = null;
  state.trackGen += 1;
  // Invalidate any in-flight /api/tracks from the previous mode (Sort loads
  // can finish after Add Cues is selected and used to flood "Not cued").
  state.tracksLoadGen += 1;
  state.waveform = null;
  if (state.waveformAbort) state.waveformAbort.abort();
  if (state.recommendAbort) state.recommendAbort.abort();
  if (state.metaAbort) state.metaAbort.abort();
  if (state.waveformDebounce) clearTimeout(state.waveformDebounce);
  document.querySelectorAll("#readinessFilter button").forEach((b) => {
    b.classList.toggle("active", b.dataset.filter === "all");
  });
  $("countsBadge").textContent = "Loading…";
  $("countsBadge").className = "badge neutral";
  $("trackList")?.classList.add("list-loading");
  applyModeUi();
  document.body.classList.add("is-mode-loading");
  // Clear stage immediately so mode switches never show the previous mode's track.
  try {
    clearSelectedDests();
  } catch {
    state.selectedDests = [];
  }
  renderTrackList();
  renderPlayer();
  if (typeof renderReviewPanel === "function") {
    try {
      renderReviewPanel();
    } catch {
      /* ignore during boot */
    }
  }
  resetWorkspaceScroll();
  setStatus(
    isPracticeMode()
      ? "Loading practice mixes…"
      : isStemsMode()
        ? "Stem vocal check"
        : isRecsMode()
          ? "Watching VirtualDJ · recs refresh automatically"
          : isAssembleMode()
            ? "Assemble a house crate for the event"
            : isReviewMode()
              ? "Loading Add Cues…"
              : "Loading Ready for Sort…"
  );
  try {
    if (isPracticeMode()) {
      renderPracticePanel();
      setPlayerLoading(false);
      await loadPracticeMixes();
    } else if (isBestSetMode()) {
      setPlayerLoading(false);
      state.tracks = [];
      state.index = 0;
      renderTrackList();
      setStatus("Loading best transitions…");
      await loadBestPracticeScores();
    } else if (isRecsMode()) {
      setPlayerLoading(false);
      state.tracks = [];
      state.index = 0;
      renderTrackList();
      setStatus("Watching VirtualDJ · recs refresh automatically");
      showRecsSkeletons("Looking up now-playing…");
      await refreshRecsNowPlaying({ loadAudio: false, forceAuto: true });
      startRecsNowPlayingPoll();
    } else if (isAssembleMode()) {
      setPlayerLoading(false);
      state.tracks = [];
      state.index = 0;
      renderTrackList();
      setStatus("Assemble a house crate for the event");
      renderAssembleMixTuners();
      await loadAssemblePreview();
    } else if (isStemsMode()) {
      setPlayerLoading(false);
      state.tracks = [];
      state.index = 0;
      renderTrackList();
      setStatus("Stem vocal check");
      await loadStemsTab();
    } else {
      setWaveformStatus("Select a track");
      setPlayerLoading(false);
      await loadTracks();
      await hydrateAutocueJobs();
      await loadFolders();
    }
  } finally {
    document.body.classList.remove("is-mode-loading");
    updatePipelineStrip();
    if (isPracticeMode()) {
      document.body.classList.add("practice-stack-layout");
      schedulePracticeWaveRedraw();
    } else {
      document.body.classList.remove("practice-stack-layout");
    }
  }
  requestAnimationFrame(resetWorkspaceScroll);
  if (isPracticeMode()) schedulePracticeWaveRedraw();
}

async function loadFolders() {
  const data = await api(`/api/folders/${encodeURIComponent(state.library)}`);
  state.folders = data.folders || [];
  state.newFolders = data.new_folders || state.newFolders || null;
  state.folderTrees = data.trees || null;
  await loadHouseColors();
  renderFolders();
  updatePathHint();
}

async function loadTrackPlacements(track, { force = false } = {}) {
  if (!track?.path) return;
  const path = track.path;
  const liveStart = state.tracks.find((t) => t.path === path) || track;
  if (!force && (liveStart.placementsLoaded || liveStart.placementsLoading)) {
    if (currentTrack()?.path === path) renderPlacementCard(currentTrack());
    return;
  }
  liveStart.placementsLoading = true;
  liveStart.placementsError = "";
  if (currentTrack()?.path === path) renderPlacementCard(currentTrack());
  try {
    const data = await api(
      `/api/track-placements?path=${encodeURIComponent(path)}`,
      { timeoutMs: 45000 }
    );
    const live = state.tracks.find((t) => t.path === path);
    if (!live) return;
    if (data.placements) live.placements = data.placements;
    live.placementsLoaded = true;
    live.placementsLoading = false;
    live.placementsError = "";
    if (currentTrack()?.path === path) {
      renderPlacementCard(currentTrack());
      renderTrackList();
    }
  } catch (err) {
    const live = state.tracks.find((t) => t.path === path);
    if (live) {
      live.placementsLoading = false;
      live.placementsError = err.message || "Could not look up library copies";
    }
    if (currentTrack()?.path === path) {
      renderPlacementCard(currentTrack());
    }
  }
}

async function selectTrack(index) {
  {
    // A re-select of the track that is already loaded, right after a seek click,
    // must not reset the player or reload the waveform.
    const same = state.tracks?.[index];
    if (
      same &&
      Date.now() < (state.seekGuardUntil || 0) &&
      $("audio")?.dataset.path === same.path &&
      waveformLoadedFor(same)
    ) {
      state.index = index;
      return;
    }
  }
  state.index = index;
  state.trackGen += 1;
  state.genForPath = state.tracks?.[index]?.path || null;
  // Dest is per-track. Never keep the last song's artist/folder as a global dest.
  try { clearSelectedDests(); } catch { state.selectedDests = []; state.selectedPath = ""; state.selectedPathLibrary = ""; }
  const selected = currentTrack();
  if (
    state.lastCueCopy &&
    (!selected || state.lastCueCopy.sourcePath !== selected.path)
  ) {
    state.lastCueCopy = null;
  }
  if (selected) {
    loadTrackPlacements(selected);
  }
  updatePipelineStrip();
  state.recommendation = null;
  state.trackMeta = null;
  state.activeLoopKey = null;
  stopLoopWatch();
  // Leave grid-align / place-cue without writing when switching tracks.
  if (state.gridAlignMode) {
    state.gridAlignMode = false;
    state.gridAlignPlan = null;
    state.gridAlignAnchor = null;
    state.gridAlignOriginal = null;
    state.gridAlignDragging = false;
    syncGridAlignUi();
  }
  if (state.placeCueMode) cancelPlaceCueMode();
  if (state.placeLoopMode) cancelPlaceLoopMode();
  if (state.metaAbort) state.metaAbort.abort();
  // Immediate feedback before any async work
  state.waveform = null;
  resetWaveZoom();
  resetWorkspaceScroll();
  // Stop previous mix so seeks don't hit the wrong file mid-switch
  const audio = $("audio");
  if (audio) {
    try {
      audio.pause();
    } catch {
      /* ignore */
    }
  }
  if (isPracticeMode()) {
    // Clear detail until the new mix loads (prevents wrong-mix seek targets)
    if (state.practiceMixPath !== currentTrack()?.path) {
      state.practiceDetail = null;
    }
    setPracticeWaveStatus("Loading waveform…");
    drawPracticeWaveform();
  } else {
    setWaveformStatus("Loading waveform…");
    drawWaveform();
  }
  setPlayerLoading(true);
  renderTrackList();
  renderPlayer();
  if (isSetOverviewMode()) renderSetOverviewRail();
  // AutoCue busy state is per-track — refresh labels when switching.
  if (!isPracticeMode()) {
    syncAutocueUi();
    updateApproveButtons();
  }
  const track = currentTrack();
  if (isPracticeMode() && track) {
    await loadPracticeDetail(track.path);
  } else if (
    track &&
    !isPracticeMode() &&
    !isRecsMode() &&
    !isAssembleMode()
  ) {
    requestRecommendation(track);
  }
}

function practiceMixAsTrack(mix) {
  return {
    path: mix.path,
    name: mix.name,
    is_cued: true,
    duration: mix.duration_sec,
    is_practice_mix: true,
    cues: { points: [], cue_count: 0, loop_count: 0 },
  };
}

async function loadPracticeMixes() {
  const listEl = $("trackList");
  const loadGen = ++state.tracksLoadGen;
  if (listEl) listEl.classList.add("list-loading");
  setStatus("Loading practice mixes…");
  try {
    const data = await api("/api/practice/sets");
    if (loadGen !== state.tracksLoadGen || !isPracticeMode()) return;
    state.practiceMixes = data.mixes || [];
    state.practiceDb = data.transitions_db || null;
    state.tracks = state.practiceMixes.map(practiceMixAsTrack);
    state.index = 0;
    $("countsBadge").textContent = `${state.tracks.length} mixes`;
    $("countsBadge").className = "badge ok";
    renderPracticeDbBadge();
    renderTrackList();
    if (state.tracks.length) {
      await selectTrack(0);
    } else {
      state.practiceDetail = null;
      renderPracticePanel();
      setPlayerLoading(false);
      setStatus("No practice mixes found in Music/Mixes");
    }
    setStatus(`Practice · ${state.tracks.length} mixes`);
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    if (listEl && loadGen === state.tracksLoadGen) {
      listEl.classList.remove("list-loading");
    }
  }
}

function renderPracticeMixList() {
  const root = $("trackList");
  if (!root) return;
  const q = (state.trackSearch || "").trim().toLowerCase();
  const indexes = state.tracks
    .map((t, i) => i)
    .filter((i) => {
      if (!q) return true;
      return (state.tracks[i].name || "").toLowerCase().includes(q);
    });
  if (!state.tracks.length) {
    root.innerHTML = `<div class="empty">No practice mixes in ~/Music/Mixes.</div>`;
    return;
  }
  if (!indexes.length) {
    root.innerHTML = `<div class="empty">No mixes match this search.</div>`;
    return;
  }
  root.innerHTML = indexes
    .map((i) => {
      const t = state.tracks[i];
      const mix = state.practiceMixes[i] || {};
      const active = i === state.index ? "active" : "";
      const dur =
        mix.duration_sec != null
          ? `<span class="mix-dur">${formatClock(mix.duration_sec)}</span>`
          : "";
      const flag = mix.exclude_from_best
        ? `<span class="badge warn">not in Best</span>`
        : mix.is_practice
          ? `<span class="badge ok">practice</span>`
          : `<span class="badge neutral">mix</span>`;
      return `<button type="button" class="practice-mix-row ${active}" data-index="${i}">
        <strong>${escapeHtml(t.name)}</strong>
        <div class="track-row-meta">${flag} ${dur}</div>
      </button>`;
    })
    .join("");
  root.querySelectorAll("button.practice-mix-row[data-index]").forEach((btn) => {
    btn.addEventListener("click", () => selectTrack(Number(btn.dataset.index)));
  });
}

function renderPracticeDbBadge() {
  const el = $("practiceDbBadge");
  if (!el) return;
  const db = state.practiceDb;
  if (!db || db.error) {
    el.textContent = db?.error
      ? `Transitions DB error: ${db.error}`
      : "Transitions DB —";
    el.className = "badge warn practice-db-badge";
    return;
  }
  el.textContent = `Notes ${db.note_edges ?? 0} · History ${db.history_edges ?? 0}`;
  el.className = "badge ok practice-db-badge";
  el.title = db.db_path || "";
}

function scoreTone(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "";
  if (v >= 7.5) return "good";
  if (v >= 5.5) return "mid";
  return "bad";
}

function scorePill(label, value, { overall = false } = {}) {
  if (value == null || value === "") {
    return `<span class="score-pill">${escapeHtml(label)} —</span>`;
  }
  const n = Number(value);
  const tone = scoreTone(n);
  const cls = `score-pill ${overall ? "overall" : ""} ${tone}`.trim();
  return `<span class="${cls}">${escapeHtml(label)} ${n.toFixed(1)}</span>`;
}

function mergeScoresIntoDetail(detail, results) {
  if (!detail?.transitions || !results?.length) return detail;
  const byIdx = new Map(
    results
      .filter((r) => r.transition_index != null)
      .map((r) => [Number(r.transition_index), r])
  );
  const transitions = detail.transitions.map((tx) => {
    const s = byIdx.get(Number(tx.index));
    if (!s || s.error) return tx;
    return {
      ...tx,
      score: {
        overall: s.overall,
        smoothness: s.smoothness,
        creativity: s.creativity,
        flow: s.flow,
        energy_match: s.energy_match,
        comments: s.comments,
        save_for_set: s.save_for_set,
        model: s.model,
        strengths: s.strengths || [],
        improvements: s.improvements || [],
        better_option_track: s.better_option_track || "",
        better_option_reason: s.better_option_reason || "",
        better_option_source: s.better_option_source || "",
        better_option_confidence: s.better_option_confidence,
        clip_start_sec: s.clip_start_sec,
        clip_duration_sec: s.clip_duration_sec,
        cached: Boolean(s.cached),
      },
    };
  });
  return { ...detail, transitions };
}

function sortedPracticeTransitions(txs) {
  const list = [...(txs || [])];
  const sort = state.practiceTxSort || "order";
  if (sort === "score") {
    list.sort(
      (a, b) =>
        (Number(b.score?.overall) || -1) - (Number(a.score?.overall) || -1) ||
        a.index - b.index
    );
  } else if (sort === "save") {
    list.sort((a, b) => {
      const as = a.score?.save_for_set ? 1 : 0;
      const bs = b.score?.save_for_set ? 1 : 0;
      if (bs !== as) return bs - as;
      return (Number(b.score?.overall) || -1) - (Number(a.score?.overall) || -1);
    });
  } else {
    list.sort((a, b) => a.index - b.index);
  }
  return list;
}

function renderPracticeSummary() {
  const el = $("practiceSummary");
  if (!el) return;
  const d = state.practiceDetail;
  const summary = state.practiceSummary;
  const scored = (d?.transitions || []).filter((t) => t.score?.overall != null);
  if (!scored.length && !summary) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  const avg =
    summary?.avg_overall != null
      ? summary.avg_overall
      : scored.length
        ? (
            scored.reduce((s, t) => s + Number(t.score.overall), 0) / scored.length
          ).toFixed(1)
        : "—";
  const saveN =
    summary?.save_for_set?.length ??
    scored.filter((t) => t.score?.save_for_set).length;
  const top = summary?.top?.[0] || scored.sort(
    (a, b) => Number(b.score?.overall) - Number(a.score?.overall)
  )[0];
  const topLabel = top
    ? `${top.from_track || top.from} → ${top.to_track || top.to}`.replace(
        /undefined/g,
        ""
      )
    : "—";
  const topScore = top?.overall ?? top?.score?.overall;
  el.innerHTML = `
    <div class="practice-summary-card">
      <div class="label">Avg score</div>
      <div class="value">${escapeHtml(String(avg))}</div>
      <div class="hint">${scored.length} scored transitions</div>
    </div>
    <div class="practice-summary-card">
      <div class="label">Save for set</div>
      <div class="value">${saveN}</div>
      <div class="hint">Gemini keepers</div>
    </div>
    <div class="practice-summary-card">
      <div class="label">Best blend</div>
      <div class="value" style="font-size:1rem;line-height:1.3">${
        topScore != null ? Number(topScore).toFixed(1) : "—"
      }</div>
      <div class="hint">${escapeHtml(
        typeof topLabel === "string" ? topLabel.slice(0, 64) : "—"
      )}</div>
    </div>`;
}

function renderPracticeAnalyzeStatus() {
  const el = $("practiceAnalyzeStatus");
  const btn = $("practiceAnalyzeBtn");
  const job = state.practiceAnalyzeJob;
  if (!el) return;
  if (!job) {
    el.hidden = true;
    if (btn) {
      btn.disabled = !state.practiceDetail?.transitions?.length;
      btn.textContent = "Analyze with Gemini";
    }
    return;
  }
  el.hidden = false;
  if (job.status === "running" || job.status === "queued") {
    const pct = job.total ? Math.round((100 * (job.done || 0)) / job.total) : 0;
    el.className = "badge warn";
    el.innerHTML = `${escapeHtml(String(job.current || "Listening"))} · ${
      job.done || 0
    }/${job.total || "?"} (${pct}%)` +
      `<div class="practice-progress"><i style="width:${pct}%"></i></div>`;
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Listening…";
    }
  } else if (job.status === "done") {
    el.className = "badge ok";
    const cached = job.summary?.cached ?? 0;
    const scored = job.summary?.scored ?? job.done ?? 0;
    el.textContent =
      cached && cached === scored
        ? `Loaded ${scored} saved scores`
        : `Scored ${scored}` + (cached ? ` · ${cached} from save` : "");
    if (btn) {
      btn.disabled = !state.practiceDetail?.transitions?.length;
      btn.textContent = "Re-analyze all";
    }
  } else if (job.status === "error") {
    el.className = "badge bad";
    el.textContent = `Analysis failed: ${job.error || "unknown"}`;
    if (btn) {
      btn.disabled = !state.practiceDetail?.transitions?.length;
      btn.textContent = "Retry analysis";
    }
  }
}


function syncPracticeViewToggle() {
  // Best for set is its own page now. Practice stays on the mix view.
  const mixView = $("practiceMixView");
  if (mixView) mixView.hidden = false;
  const analyzeBtn = $("practiceAnalyzeBtn");
  if (analyzeBtn) analyzeBtn.hidden = false;
  const txSort = $("practiceTxSort");
  if (txSort) txSort.hidden = false;
}

async function setPracticeView(view) {
  if (view === "best") {
    await setMode("best_set");
    return;
  }
  state.practiceView = "mix";
  syncPracticeViewToggle();
  if (isPracticeMode()) renderPracticePanel();
}

function bestSetStatusLine() {
  const all = state.practiceBestItems || [];
  const hide = Boolean(state.practiceBestHidePlayed);
  const visible = visibleBestPracticeItems(all, hide);
  const hidden = bestPracticeHiddenCount(all, hide);
  if (hidden) {
    return `Best for set · ${visible.length} transitions · ${hidden} hidden (already played)`;
  }
  return `Best for set · ${visible.length} transitions (Gemini + priority)`;
}

async function loadBestPracticeScores() {
  state.practiceBestLoading = true;
  renderPracticeBestList();
  try {
    const params = new URLSearchParams({
      prefix: "pj",
      min_overall: "7.0",
      saved_only: "false",
      min_priority: "0",
      hide_live_played: "false",
    });
    const data = await api(`/api/practice/best?${params}`);
    state.practiceBestItems = data.items || [];
    state.practiceBestLivePlayedError = data.live_played_error || "";
    renderPracticeBestList();
    if (state.practiceBestLivePlayedError) {
      setStatus(
        "Hide played could not read the live set — showing every keeper.",
        "error"
      );
    } else {
      setStatus(bestSetStatusLine());
    }
  } catch (err) {
    setStatus(err.message || String(err), "error");
    state.practiceBestItems = [];
    renderPracticeBestList();
  } finally {
    state.practiceBestLoading = false;
    renderPracticeBestList();
  }
}

function renderBestSetHidePlayedBtn() {
  const btn = $("bestSetHidePlayedBtn");
  if (!btn) return;
  const on = Boolean(state.practiceBestHidePlayed);
  const err = state.practiceBestLivePlayedError || "";
  const liveN = (state.practiceBestItems || []).filter((it) => it.live_played).length;
  btn.classList.toggle("is-on", on);
  btn.disabled = Boolean(err);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Hiding played" : "Hide played";
  if (err) {
    btn.title = "Could not read Played folder / Friday–Saturday history.";
    return;
  }
  btn.title = on
    ? `${liveN} transition${liveN === 1 ? "" : "s"} hidden — already played in the live set. Click to show them.`
    : `Hide transitions that use a song already played in the live set (${liveN} now).`;
}

async function toggleBestSetHidePlayed() {
  state.practiceBestHidePlayed = !state.practiceBestHidePlayed;
  try {
    localStorage.setItem(
      "musicSorter.bestSetHidePlayed",
      state.practiceBestHidePlayed ? "1" : "0"
    );
  } catch {
    /* ignore */
  }
  if (state.practiceBestHidePlayed) {
    await loadBestPracticeScores();
    return;
  }
  renderPracticeBestList();
  if (isBestSetMode()) setStatus(bestSetStatusLine());
}

function renderPracticeBestList() {
  const el = $("practiceBestList");
  const countEl = $("practiceBestCount");
  if (!el) return;
  const all = state.practiceBestItems || [];
  const hide = Boolean(state.practiceBestHidePlayed);
  const items = visibleBestPracticeItems(all, hide);
  if (countEl) countEl.textContent = String(items.length);
  renderBestSetHidePlayedBtn();
  if (state.practiceBestLoading && !all.length) {
    el.innerHTML = `<div class="empty">Loading best transitions…</div>`;
    return;
  }
  if (!all.length) {
    el.innerHTML = `<div class="empty">No keepers yet for pj mixes (save for set, overall ≥ 7, or priority ≥ 1).</div>`;
    return;
  }
  if (!items.length) {
    el.innerHTML = `<div class="empty">All keepers here were already played in the live set. Turn off Hide played to see them.</div>`;
    return;
  }
  el.innerHTML = items
    .map((item) => {
      const overall = item.overall;
      const isSave = Boolean(item.save_for_set);
      const priority = Number(item.priority) || 0;
      const pills = `
        <div class="practice-best-score">
          ${scorePill("Overall", overall, { overall: true })}
          <div class="practice-tx-scores">
            ${scorePill("Smooth", item.smoothness)}
            ${scorePill("Flow", item.flow)}
            ${
              isSave
                ? `<span class="badge ok">Save</span>`
                : `<span class="badge neutral">Scout</span>`
            }
          </div>
        </div>`;
      const priBtns = [1, 2, 3, 4, 5]
        .map(
          (n) =>
            `<button type="button" class="practice-priority-btn ${
              priority === n ? "active" : ""
            }" data-id="${item.id}" data-priority="${n}" title="Priority ${n}${
              priority === n ? " (click again to clear)" : ""
            }">${n}</button>`
        )
        .join("");
      return `<article class="practice-best-row ${isSave ? "is-save" : ""}" data-id="${item.id}">
        ${pills}
        <div class="practice-best-main">
          <div class="practice-best-pair">
            <span>${escapeHtml(item.from_track || "")}</span>
            <span class="arrow">→</span>
            <span>${escapeHtml(item.to_track || "")}</span>
          </div>
          <div class="practice-best-mix">${escapeHtml(item.mix_name || "")}${
            item.at_sec != null ? ` · @ ${formatClock(item.at_sec)}` : ""
          }</div>
          ${
            item.comments
              ? `<div class="practice-tx-comments">${escapeHtml(item.comments)}</div>`
              : ""
          }
        </div>
        <div class="practice-best-actions">
          <div class="practice-priority" title="Priority tier (5 = must remember)">${priBtns}</div>
          <button type="button" class="btn primary practice-best-play" data-id="${item.id}">▶ Play</button>
        </div>
      </article>`;
    })
    .join("");

  const playingId = state.practicePlayingBestId;
  if (playingId != null) {
    const playing = el.querySelector(`.practice-best-row[data-id="${playingId}"]`);
    if (playing) playing.classList.add("is-playing");
  }

  el.querySelectorAll(".practice-priority-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.id);
      const n = Number(btn.dataset.priority);
      const cur = Number(
        (state.practiceBestItems || []).find((x) => Number(x.id) === id)
          ?.priority || 0
      );
      // Toggle off if clicking the active tier
      updateBestPriority(id, cur === n ? 0 : n);
    });
  });
  el.querySelectorAll(".practice-best-play").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.id);
      const row = (state.practiceBestItems || []).find(
        (x) => Number(x.id) === id
      );
      if (row) playBestPracticeItem(row);
    });
  });
}

async function updateBestPriority(id, priority) {
  try {
    const data = await api("/api/practice/score", {
      method: "POST",
      body: JSON.stringify({ id, priority }),
    });
    const updated = data.score;
    // Optimistic local reorder using server list rules
    state.practiceBestItems = (state.practiceBestItems || []).map((it) =>
      Number(it.id) === Number(id) ? { ...it, ...updated } : it
    );
    state.practiceBestItems.sort((a, b) => {
      const pr = (Number(b.priority) || 0) - (Number(a.priority) || 0);
      if (pr) return pr;
      const ov =
        (Number(b.overall) || -1) - (Number(a.overall) || -1);
      if (ov) return ov;
      const sv = (b.save_for_set ? 1 : 0) - (a.save_for_set ? 1 : 0);
      if (sv) return sv;
      const an = String(b.analyzed_at || "").localeCompare(
        String(a.analyzed_at || "")
      );
      if (an) return an;
      return String(a.mix_name || "").localeCompare(String(b.mix_name || ""));
    });
    // Drop rows that no longer match default inclusion if priority cleared
    // and they wouldn't otherwise qualify — reload to stay truthful.
    await loadBestPracticeScores();
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

async function playBestPracticeItem(item) {
  const mixPath = item.mix_path;
  if (!mixPath) {
    setStatus("Missing mix path for this transition.", "error");
    return;
  }
  const audio = $("audio");
  if (!audio) {
    setStatus("No audio element — refresh the page.", "error");
    return;
  }
  const list = $("practiceBestList");
  const panel = $("bestSetPanel");
  const listScroll = list ? list.scrollTop : 0;
  const panelScroll = panel ? panel.scrollTop : 0;

  state.practicePlayingBestId = item.id;
  const now = $("bestSetNow");
  const nowTitle = $("bestSetNowTitle");
  const nowMeta = $("bestSetNowMeta");
  if (now) now.hidden = false;
  if (nowTitle) {
    nowTitle.textContent = `${item.from_track || "?"} → ${item.to_track || "?"}`;
  }
  if (nowMeta) {
    const clock =
      item.at_sec != null && typeof formatClock === "function"
        ? formatClock(item.at_sec)
        : "";
    nowMeta.textContent = `${item.mix_name || ""}${clock ? ` · @ ${clock}` : ""} · −20s`;
  }
  if (list) {
    list.querySelectorAll(".practice-best-row.is-playing").forEach((el) => {
      el.classList.remove("is-playing");
    });
    const row = list.querySelector(`.practice-best-row[data-id="${item.id}"]`);
    if (row) row.classList.add("is-playing");
  }

  if (audio.dataset.path !== mixPath) {
    audio.pause();
    try {
      audio.removeAttribute("src");
      audio.load();
    } catch {
      /* ignore */
    }
    audio.dataset.path = mixPath;
    audio.src = `/api/audio?path=${encodeURIComponent(mixPath)}`;
  }
  seekPracticeTransition(Number(item.at_sec) || 0, {
    play: true,
    index: item.transition_index,
  });
  updateTransportUi();
  if (list) list.scrollTop = listScroll;
  if (panel) panel.scrollTop = panelScroll;
}

function renderPracticeExcludeBestBtn() {
  const btn = $("practiceExcludeBestBtn");
  if (!btn) return;
  const d = state.practiceDetail;
  const hasMix = Boolean(d?.path || state.practiceMixPath);
  btn.hidden = !hasMix || isBestSetMode();
  const on = Boolean(d?.exclude_from_best);
  btn.classList.toggle("is-on", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Excluded from Best" : "Exclude from Best";
  btn.title = on
    ? "This mix stays on Practice. Click to list its transitions on Best for set again."
    : "Keep reviewing this mix, but do not list its transitions on Best for set.";
}

async function togglePracticeExcludeFromBest() {
  const path = state.practiceDetail?.path || state.practiceMixPath;
  if (!path) {
    setStatus("Select a practice mix first.", "error");
    return;
  }
  const next = !Boolean(state.practiceDetail?.exclude_from_best);
  try {
    const data = await api("/api/practice/mix-settings", {
      method: "POST",
      body: JSON.stringify({ path, exclude_from_best: next }),
    });
    const excluded = Boolean(data.exclude_from_best);
    if (state.practiceDetail) {
      state.practiceDetail = { ...state.practiceDetail, exclude_from_best: excluded };
    }
    const name = state.practiceDetail?.name || "";
    state.practiceMixes = (state.practiceMixes || []).map((m) =>
      m.path === path || m.name === name
        ? { ...m, exclude_from_best: excluded }
        : m
    );
    renderPracticePanel();
    renderTrackList();
    setStatus(
      excluded
        ? "Excluded from Best for set — still here to review."
        : "This mix can appear on Best for set again."
    );
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

function renderPracticePanel() {
  const meta = $("practiceSetMeta");
  const tracksEl = $("practiceTrackList");
  const txEl = $("practiceTransitionList");
  const analyzeBtn = $("practiceAnalyzeBtn");
  if (!meta || !tracksEl || !txEl) return;
  renderPracticeDbBadge();
  renderPracticeAnalyzeStatus();
  renderPracticeExcludeBestBtn();
  syncPracticeViewToggle();
  if (isBestSetMode()) {
    renderPracticeBestList();
    return;
  }

  const d = state.practiceDetail;
  if (!d) {
    meta.className = "practice-set-meta empty";
    meta.textContent = "Select a practice mix on the left to review transitions.";
    tracksEl.innerHTML = "";
    txEl.innerHTML = "";
    if ($("practiceTrackCount")) $("practiceTrackCount").textContent = "0";
    if ($("practiceSummary")) {
      $("practiceSummary").hidden = true;
      $("practiceSummary").innerHTML = "";
    }
    if (analyzeBtn) analyzeBtn.disabled = true;
    renderPracticeExcludeBestBtn();
    return;
  }

  if (analyzeBtn) {
    const busy =
      state.practiceAnalyzeJob &&
      ["running", "queued"].includes(state.practiceAnalyzeJob.status);
    analyzeBtn.disabled = busy || !(d.transitions || []).length;
  }

  const dur = d.duration_sec != null ? formatClock(d.duration_sec) : "—";
  const scoredN = (d.transitions || []).filter((t) => t.score?.overall != null).length;
  const txN = d.transition_count || (d.transitions || []).length || 0;
  meta.className = "practice-set-meta compact";
  // Stage already shows the mix name — keep a single stats row here.
  meta.innerHTML = `
    <div class="set-stats">
      <span class="badge ok">${d.track_count || 0} tracks</span>
      <span class="badge ${txN ? "ok" : "neutral"}">${txN} transitions</span>
      <span class="badge neutral">${escapeHtml(dur)}</span>
      <span class="badge ${scoredN ? "ok" : "neutral"}">${scoredN} scored</span>
      ${
        d.exclude_from_best
          ? `<span class="badge warn">Real set · not in Best</span>`
          : ""
      }
    </div>
    ${
      txN
        ? ""
        : `<p class="hint practice-empty-hint">Need at least 2 named VDJ cues on this mix to build transitions.</p>`
    }`;

  renderPracticeSummary();

  const tracks = d.tracks || [];
  if ($("practiceTrackCount")) {
    $("practiceTrackCount").textContent = String(tracks.length);
  }
  tracksEl.innerHTML = tracks.length
    ? tracks
        .map(
          (t) => `<div class="practice-track-row">
            <span class="idx">${(t.index ?? 0) + 1}</span>
            <span class="time">${formatClock(t.pos_sec)}</span>
            <span>${escapeHtml(t.name)}</span>
          </div>`
        )
        .join("")
    : `<div class="empty">No named cues on this mix in VirtualDJ. Name hotcues on the recording (track titles) so we can build a tracklist.</div>`;

  const txs = sortedPracticeTransitions(d.transitions || []);
  if (!txs.length) {
    txEl.innerHTML = `<div class="empty">Need at least 2 named cues to show transitions.</div>`;
    return;
  }

  txEl.innerHTML = txs
    .map((tx, i) => {
      const s = tx.score || {};
      const overall = s.overall;
      const isSave = Boolean(s.save_for_set);
      const isWeak = overall != null && Number(overall) < 5.5;
      const cardCls = `practice-tx ${isSave ? "is-save" : ""} ${isWeak ? "is-weak" : ""}`.trim();

      const scoreHtml =
        overall != null
          ? `<div class="practice-tx-scores">
              ${scorePill("Overall", overall, { overall: true })}
              ${scorePill("Smooth", s.smoothness)}
              ${scorePill("Creative", s.creativity)}
              ${scorePill("Flow", s.flow)}
              ${scorePill("Energy", s.energy_match)}
              ${
                isSave
                  ? `<span class="badge ok">Save for set</span>`
                  : `<span class="badge neutral">Practice more</span>`
              }
            </div>`
          : `<div class="practice-tx-scores"><span class="score-pill">Not scored yet</span></div>`;

      const comments = s.comments
        ? `<div class="practice-tx-comments">${escapeHtml(s.comments)}</div>`
        : "";

      const better =
        s.better_option_track
          ? `<div class="practice-better-option">
              <div class="practice-better-kicker">Better option from your history/notes</div>
              <div class="practice-better-track">${escapeHtml(s.better_option_track)}</div>
              <div class="practice-better-reason">${escapeHtml(
                s.better_option_reason || ""
              )}</div>
              <div class="practice-better-meta">
                ${
                  s.better_option_source
                    ? `<span class="badge neutral">${escapeHtml(
                        s.better_option_source
                      )}</span>`
                    : ""
                }
                ${
                  s.better_option_confidence != null
                    ? `<span class="badge neutral">${Math.round(
                        Number(s.better_option_confidence) * 100
                      )}% conf.</span>`
                    : ""
                }
              </div>
            </div>`
          : "";

      const strengths = s.strengths || [];
      const improvements = s.improvements || [];
      const bullets =
        strengths.length || improvements.length
          ? `<div class="practice-tx-bullets">
              <div>
                <div class="col-title">Strengths</div>
                <ul>${
                  strengths.length
                    ? strengths.map((x) => `<li>${escapeHtml(x)}</li>`).join("")
                    : "<li>—</li>"
                }</ul>
              </div>
              <div>
                <div class="col-title">Improve</div>
                <ul>${
                  improvements.length
                    ? improvements.map((x) => `<li>${escapeHtml(x)}</li>`).join("")
                    : "<li>—</li>"
                }</ul>
              </div>
            </div>`
          : "";

      const alts = tx.alternatives || [];
      const altHtml = alts.length
        ? `<div class="practice-alts-title">Other options (notes + history)</div>
           <div class="practice-alts">${alts
             .map((a) => {
               const cnt =
                 a.count > 0
                   ? `<span class="practice-alt-count">×${a.count}</span>`
                   : a.vibe
                     ? `<span class="practice-alt-count">${escapeHtml(a.vibe)}</span>`
                     : "";
               const note =
                 a.note || a.vibe
                   ? `<span class="practice-alt-note">${escapeHtml(
                       [a.vibe && `Vibe: ${a.vibe}`, a.note]
                         .filter(Boolean)
                         .join(" · ")
                     )}</span>`
                   : "";
               return `<div class="practice-alt ${a.is_actual ? "is-actual" : ""}">
                <span class="practice-alt-source">${escapeHtml(a.source || "")}</span>
                <span class="practice-alt-label">${escapeHtml(a.to_label || "")}${note}</span>
                ${cnt}
              </div>`;
             })
             .join("")}</div>`
        : `<div class="practice-alt-empty">No note/history options matched the outgoing track.</div>`;

      return `<article class="${cardCls}" id="practice-tx-${escapeHtml(String(tx.index ?? i))}" data-at="${tx.at_sec}" data-index="${tx.index}">
        <div class="practice-tx-top">
          <div class="practice-tx-pair">
            <div class="practice-tx-from">${escapeHtml(tx.from_track)}</div>
            <div class="practice-tx-arrow-row">↓ transition · #${(tx.index ?? 0) + 1}</div>
            <div class="practice-tx-to">${escapeHtml(tx.to_track)}</div>
          </div>
          ${scoreHtml}
        </div>
        <div class="practice-tx-meta">
          <span>@ ${formatClock(tx.at_sec)}</span>
          <span>gap ~${formatClock(tx.duration_est_sec)}</span>
          <button type="button" class="btn primary practice-seek-btn" data-at="${tx.at_sec}" data-index="${tx.index}">▶ Play blend</button>
        </div>
        ${comments}
        ${better}
        ${bullets}
        ${altHtml}
      </article>`;
    })
    .join("");

  txEl.querySelectorAll(".practice-seek-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      seekPracticeTransition(Number(btn.dataset.at) || 0, {
        index: btn.dataset.index,
      });
    });
  });
  // Clicking a card body focuses it (without always restarting audio).
  txEl.querySelectorAll("article.practice-tx").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input")) return;
      focusPracticeTransitionCard(
        Number(card.dataset.at) || 0,
        card.dataset.index
      );
    });
  });

  document.querySelectorAll("#practiceTxSort button").forEach((b) => {
    b.classList.toggle("active", b.dataset.txSort === state.practiceTxSort);
  });

  // Markers depend on transition list + scores
  drawPracticeWaveform();
}

async function loadPracticeDetail(path) {
  if (!path) return;
  // Don't interrupt an in-flight job for the same mix
  const sameJobRunning =
    state.practiceAnalyzeJob &&
    state.practiceAnalyzeJob.mix_path === path &&
    ["running", "queued"].includes(state.practiceAnalyzeJob.status);

  state.practiceMixPath = path;
  if (!sameJobRunning) state.practiceSummary = null;
  setStatus("Loading set analysis…");
  try {
    const data = await api(
      `/api/practice/set?path=${encodeURIComponent(path)}`
    );
    if (state.practiceMixPath !== path || !isPracticeMode()) return;
    state.practiceDetail = data;
    // Merge live job results if any
    if (
      state.practiceAnalyzeJob?.mix_path === path &&
      state.practiceAnalyzeJob.results
    ) {
      state.practiceDetail = mergeScoresIntoDetail(
        data,
        state.practiceAnalyzeJob.results
      );
      if (state.practiceAnalyzeJob.summary) {
        state.practiceSummary = state.practiceAnalyzeJob.summary;
      }
    }
    renderPracticePanel();
    drawPracticeWaveform();
    const meta = $("playerMeta");
    const track = currentTrack();
    if (meta && track) meta.innerHTML = buildPracticePlayerMetaHtml(track);

    const txs = state.practiceDetail.transitions || [];
    const scored = txs.filter((t) => t.score?.overall != null).length;
    const pending = txs.length - scored;
    setStatus(
      `${data.name}: ${data.track_count} tracks · ${data.transition_count} transitions` +
        (scored ? ` · ${scored} saved scores` : "") +
        (pending ? ` · ${pending} to analyze` : "")
    );

    // Auto-score any transitions not yet saved (never re-runs completed ones).
    if (!sameJobRunning && pending > 0) {
      await startPracticeAnalyze({ force: false });
    } else if (!sameJobRunning && scored > 0 && pending === 0) {
      // Build summary from saved scores for the header cards
      const list = txs
        .filter((t) => t.score?.overall != null)
        .map((t) => ({
          ...t.score,
          from_track: t.from_track,
          to_track: t.to_track,
          transition_index: t.index,
        }));
      const avg =
        list.reduce((s, r) => s + Number(r.overall), 0) / (list.length || 1);
      state.practiceSummary = {
        scored: list.length,
        cached: list.length,
        avg_overall: Math.round(avg * 10) / 10,
        top: [...list].sort((a, b) => Number(b.overall) - Number(a.overall)).slice(0, 5),
        save_for_set: list.filter((r) => r.save_for_set),
        better_options: list.filter((r) => r.better_option_track),
      };
      renderPracticePanel();
    }
  } catch (err) {
    state.practiceDetail = null;
    renderPracticePanel();
    setStatus(err.message || String(err), "error");
  } finally {
    setPlayerLoading(false);
  }
}

async function rebuildTransitionsDb() {
  setStatus("Rebuilding transitions database…");
  try {
    const stats = await api("/api/transitions/rebuild", { method: "POST" });
    state.practiceDb = stats;
    renderPracticeDbBadge();
    if (state.practiceMixPath) {
      await loadPracticeDetail(state.practiceMixPath);
    }
    setStatus(
      `Transitions DB rebuilt · notes ${stats.note_edges ?? stats.imported_notes ?? 0} · history ${stats.history_edges ?? stats.imported_history ?? 0}`
    );
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

function stopPracticeAnalyzePoll() {
  if (state.practiceAnalyzeTimer) {
    clearInterval(state.practiceAnalyzeTimer);
    state.practiceAnalyzeTimer = null;
  }
}

async function pollPracticeAnalyzeJob() {
  const job = state.practiceAnalyzeJob;
  if (!job?.id) return;
  try {
    const data = await api(`/api/practice/analyze/${encodeURIComponent(job.id)}`);
    const j = data.job;
    if (!j) return;
    state.practiceAnalyzeJob = j;
    if (j.results && state.practiceDetail && j.mix_path === state.practiceMixPath) {
      state.practiceDetail = mergeScoresIntoDetail(state.practiceDetail, j.results);
    }
    if (j.summary) state.practiceSummary = j.summary;
    renderPracticePanel();
    if (j.status === "done" || j.status === "error") {
      stopPracticeAnalyzePoll();
      if (j.status === "done") {
        setStatus(
          `Gemini analysis done · avg ${j.summary?.avg_overall ?? "—"} · ${
            j.summary?.save_for_set?.length ?? 0
          } save for set`
        );
      } else {
        setStatus(j.error || "Analysis failed", "error");
      }
    }
  } catch (err) {
    stopPracticeAnalyzePoll();
    setStatus(err.message || String(err), "error");
  }
}

async function startPracticeAnalyze({ force = false } = {}) {
  const path = state.practiceMixPath || currentTrack()?.path;
  if (!path) {
    setStatus("Select a practice mix first.", "error");
    return;
  }
  const txs = state.practiceDetail?.transitions || [];
  const n = txs.length;
  if (!n) {
    setStatus("No transitions to analyze on this mix.", "error");
    return;
  }
  // Already fully scored and not forcing — nothing to do
  const pending = txs.filter((t) => t.score?.overall == null).length;
  if (!force && pending === 0) {
    setStatus(`All ${n} transitions already scored (saved).`);
    return;
  }
  // Don't stack jobs for the same mix
  if (
    state.practiceAnalyzeJob &&
    state.practiceAnalyzeJob.mix_path === path &&
    ["running", "queued"].includes(state.practiceAnalyzeJob.status)
  ) {
    return;
  }
  stopPracticeAnalyzePoll();
  setStatus(
    force
      ? `Re-analyzing all ${n} transitions with Gemini…`
      : `Gemini listening to ${pending} new transition${pending === 1 ? "" : "s"} (${n - pending} already saved)…`
  );
  try {
    const data = await api("/api/practice/analyze", {
      method: "POST",
      body: JSON.stringify({ path, force }),
    });
    state.practiceAnalyzeJob = data.job;
    renderPracticeAnalyzeStatus();
    state.practiceAnalyzeTimer = setInterval(pollPracticeAnalyzeJob, 1500);
    await pollPracticeAnalyzeJob();
  } catch (err) {
    setStatus(err.message || String(err), "error");
    renderPracticeAnalyzeStatus();
  }
}

async function markSetOverviewMustPlay() {
  const track = selectedQueueTrack() || currentTrack();
  if (!track || !isPajamathonSetQueueTrack(track)) {
    setStatus("Must Play is only for Sets/Pajamathon copies.", "error");
    return;
  }
  try {
    setStatus(`Must Play · ${trackDisplayTitle(track)}…`);
    await api("/api/must-play-set", {
      method: "POST",
      body: JSON.stringify({ path: track.path }),
    });
    markTrackMustPlay(track);
    setStatus(`Must Play · ${trackDisplayTitle(track)}`, "success");
    renderTrackList();
    renderSetOverviewRail();
    await loadTracks({ keepPath: track.path, skipStatus: true });
    markTrackMustPlay(track);
    renderTrackList();
    renderSetOverviewRail();
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

async function approveSetOverviewCues() {
  return approveSetCues({ stick: true });
}

async function approveSetCues({ stick = false } = {}) {
  const track = selectedQueueTrack() || currentTrack();
  if (!track || !isPajamathonSetQueueTrack(track)) return;
  const stay = stick || isSetOverviewMode();
  if (!track.is_cued) {
    setStatus("Add cue points before approving this set file.", "error");
    return;
  }
  try {
    setStatus(`Approving cues on ${trackDisplayTitle(track)}…`);
    await api("/api/approve-set-cues", {
      method: "POST",
      body: JSON.stringify({ path: track.path }),
    });
    markTrackKirillApproved(track);
    setStatus(
      stay
        ? `Kirill approved · ${trackDisplayTitle(track)}`
        : `Approved · ${trackDisplayTitle(track)} leaves the open Pajamathon list`,
      "success"
    );
    if (stay) {
      renderTrackList();
      renderSetOverviewRail();
    }
    await loadTracks({ keepPath: track.path, skipStatus: true });
    markTrackKirillApproved(track);
    if (stay) {
      renderTrackList();
      renderSetOverviewRail();
      return;
    }
    const still = currentTrack();
    if (still && trackIsKirillApproved(still)) {
      skipToNextReviewTrack();
    }
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

async function promoteTrack(destinationStage, { requireCued = null } = {}) {
  const track = currentTrack();
  if (!track) return;
  if (state.promoteInFlight) return;

  if (isPajamathonSetQueueTrack(track)) {
    setStatus(
      "This file is already in the Pajamathon set. Cue it in place — Move to Ready is only for Add Cues inbox tracks.",
      "error"
    );
    return;
  }

  if (destinationStage === "ready_for_sort" && !track.is_cued) {
    setStatus("Cannot approve: track has no VDJ cue points yet.", "error");
    return;
  }

  state.promoteInFlight = true;
  try {
  const allowRunning = (await isVdjRunningFresh())
    ? await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "This move may be overwritten when VirtualDJ quits. Close it before continuing whenever possible.",
        confirmLabel: "Move anyway",
        tone: "warning",
      })
    : false;
  if (state.health?.virtualdj_running && !allowRunning) {
    setStatus("Close VirtualDJ, then promote.", "error");
    return;
  }

  const labels = {
    ready_for_sort: "Ready for Sort",
    no_cues_found: "Couldn't cue — parked",
    low_quality_skip: "Low quality — skipped",
    ac_low_quality: "AutoCue quality bad — parked",
  };
  setStatus(`Moving ${track.name} → ${labels[destinationStage] || destinationStage}…`);
  updateApproveButtons();
  ["approveBtn", "approveBtnSide"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = true;
  });

    const body = {
      path: track.path,
      destination_stage: destinationStage,
      allow_vdj_running: Boolean(allowRunning),
    };
    if (requireCued !== null) body.require_cued = requireCued;
    const data = await api("/api/promote", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const r = data.result;
    // Refresh list without clobbering status; apply handoff after load.
    await loadTracks({ skipStatus: true });
    updatePipelineStrip();
    const handoff = (
      globalThis.MusicSorterStatusHandoff || window.MusicSorterStatusHandoff
    ).composePromoteSuccessHandoff(r, destinationStage);
    setStatus(
      handoff.message,
      handoff.kind,
      handoff.action
        ? {
            label: handoff.action.label,
            gotoMode: handoff.action.gotoMode,
            onClick: () => setMode(handoff.action.gotoMode),
          }
        : null
    );
  } catch (err) {
    setStatus(err.message, "error");
    updateApproveButtons();
  } finally {
    state.promoteInFlight = false;
  }
}

function skipToNextReviewTrack() {
  const indexes = filteredTrackIndexes();
  if (!indexes.length) return;
  const pos = indexes.indexOf(state.index);
  const next = indexes[pos + 1] ?? indexes[0];
  if (next !== state.index) selectTrack(next);
}

async function requestRecommendation(track, { force = false } = {}) {
  if (!track) return;
  if (state.recommendAbort) state.recommendAbort.abort();
  const controller = new AbortController();
  state.recommendAbort = controller;
  state.recommendation = null;
  state.recommendationPath = track.path;
  renderRecommendation();

  try {
    const data = await api("/api/recommend", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        preferred_library: "House",
        force: Boolean(force),
      }),
      signal: controller.signal,
    });
    if (currentTrack()?.path !== track.path) return;
    state.recommendation = data.recommendation;
    state.recommendationPath = track.path;
    state.recommendedLane = "";
    renderRecommendation();
    if (data.ok && data.recommendation) {
      renderFolders();
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    if (currentTrack()?.path !== track.path) return;
    state.recommendationPath = track.path;
    state.recommendation = {
      error: err.message,
      library: state.library,
      relative_path: "",
      confidence: 0,
    };
    renderRecommendation();
  }
}

async function sortSelected() {
  try {
    return await sortSelectedImpl({ sauna: alsoSaunaFest() });
  } catch (err) {
    state.sortInFlight = false;
    setCopyAdvanceLock(false);
    loudNotice(`The copy did not start: ${err?.message || err}`, "error");
  }
}

async function sortSelectedImpl({ sauna }) {
  if (state.copyAdvanceLock) {
    loudNotice("The next song is still loading after the last copy — wait a moment, then copy.", "warn");
    return;
  }
  if (state.sortInFlight) {
    loudNotice("A copy is already running — wait for it to finish.", "warn");
    return;
  }
  const track = resolveSortTrack();
  if (!track) {
    loudNotice("Select a track in the list, or wait for it to finish loading.", "error");
    return;
  }
  const sortCap = captureSong(); // song identity at CLICK time — never the post-advance song
  if (!guardSong(sortCap, "Copy")) return;
  if (!songCopyReady(track)) {
    loudNotice(`Copy: “${trackDisplayTitle(track)}” is still loading - try again in a second. Nothing was changed.`, "error");
    return;
  }
  let dests = state.selectedDests || [];
  if (!dests.length && state.selectedPath) {
    // The button label shows the picked folder (tags / chips set selectedPath): use it as the destination.
    const ex = existingHouseFolderFor(state.selectedPath);
    const path = ex || cleanRelPath(state.selectedPath);
    dests = [{ library: "House", path, key: destKey("House", path), newFolder: !ex }];
    state.selectedDests = dests;
  }
  if (!dests.length) {
    loudNotice("Pick the Gemini rec or a House folder first — nothing is selected as the destination.", "error");
    return;
  }
  if (!track.is_cued) {
    loudNotice("Cannot copy: this track is not cued yet.", "error");
    return;
  }

  if (dests[0] && dests[0].newFolder) {
    // Stale "new folder" flag (it was created by an earlier sort / already exists): use the real folder.
    const existing = existingHouseFolderFor(dests[0].path);
    if (existing) {
      dests[0] = { ...dests[0], path: existing, key: destKey("House", existing), newFolder: false };
      state.selectedDests = dests;
      state.selectedPath = existing;
      updateSelectionLabels();
      setStatus(`Using the existing folder House / ${existing}.`);
    }
  }
  const isNewDest = Boolean(dests[0] && dests[0].newFolder);
  if (isNewDest) {
    const okNew = await showConfirmDialog({
      title: "Create a NEW House folder?",
      track: trackDisplayTitle(track),
      message: `House / ${dests[0].path} does not exist yet. It will be created now.`,
      note: sauna
        ? "The track is COPIED into it and into Sets/Sauna Fest; the original stays."
        : "The track is COPIED into it; the original stays.",
      confirmLabel: "Create folder + copy",
      tone: "accent",
    });
    if (!okNew) {
      loudNotice("Copy cancelled — no folder was created and nothing was copied.", "warn");
      return;
    }
  }

  state.sortInFlight = true;
  const destLabel = dests.map((d) => `House/${d.path}`).join(", ");
  startCopyFeedback(trackDisplayTitle(track), destLabel);
  sortBtnBusy(true);
  try {
  const allowRunning = (await isVdjRunningFresh())
    ? await showConfirmDialog({
        title: "VirtualDJ is still open",
        track: trackDisplayTitle(track),
        message:
          "Copy-sorting writes database.xml and may be overwritten when VirtualDJ quits. Close it before continuing.",
        confirmLabel: "Copy anyway",
        tone: "warning",
      })
    : false;

  if (!isReadonlyBuild() && state.health?.virtualdj_running && !allowRunning) {
    loudNotice("Close VirtualDJ, then copy.", "error");
    return;
  }

  if (!guardSong(sortCap, "Copy")) return; // a dialog was open: still the same song?
  setStatus(`Copying ${track.name} → ${destLabel} (original stays)…`);
  quickConfirm(`Copying to ${destLabel}… please don't switch songs`);

    const sortBody = JSON.stringify({
      path: track.path,
      library: "House",
      relative_folder: dests[0].newFolder ? cleanRelPath(dests[0].path) : ensureSortFolder(dests[0].path),
      destinations: [
        {
          library: "House",
          relative_folder: dests[0].newFolder ? cleanRelPath(dests[0].path) : ensureSortFolder(dests[0].path),
          new_folder: Boolean(dests[0].newFolder),
        },
      ],
      dry_run: isReadonlyBuild(),
      allow_vdj_running: Boolean(allowRunning),
      also_sauna_fest: Boolean(sauna),
    });
    const data = await api("/api/sort", { method: "POST", body: sortBody });
    const r = data.result || {};
    if (isReadonlyBuild() || r.dry_run) {
      setStatus(
        `Read-only dry run — nothing copied. Would COPY TO ${sauna ? "Sets/Sauna Fest/… + " : ""}House / ${dests[0].path} (original stays)`,
        "success"
      );
      return;
    }
    const archiveBits = [];
    const libBits = (r.library_dests || [])
      .map((d) => `${d.library}/${d.relative_folder || ""}`.replace(/\/$/, ""))
      .join(" + ");
    if (libBits) archiveBits.push(libBits);
    if (r.cues_sorted_copied) archiveBits.push("copied to Cues Sorted");
    else if (r.cues_sorted_already_present) archiveBits.push("already in Cues Sorted");
    if (r.cues_sorted_db_cloned) archiveBits.push("Cues Sorted VDJ entry cloned");
    if (r.sets_cues_copied) archiveBits.push(`cues copied to Pajamathon (${r.sets_cues_copied})`);
    else if ((r.sets_cues_skipped || 0) > 0) {
      archiveBits.push("Pajamathon already cued");
    }
    if (isSetOverviewMode()) {
      if (r.lane) {
        track.lane = r.lane;
        track.user_color = r.lane_color || track.user_color;
        track.needs_sort = false;
      }
      const dests = r.library_dests || [];
      if (dests.length) {
        track.placements = track.placements || { library: [], cues_sorted: [], sets: [] };
        const rel = dests[0].relative_folder || "";
        const destPath = dests[0].path || r.dest_path;
        if (destPath) {
          track.placements.library = [
            { path: destPath, relative_path: rel ? `${rel}/${track.name}` : track.name, is_cued: true },
            ...(track.placements.library || []).filter((h) => h.path !== destPath),
          ];
        }
        if (r.cues_sorted_path) {
          track.placements.cues_sorted = [
            { path: r.cues_sorted_path, relative_path: rel ? `${rel}/${track.name}` : track.name, is_cued: true },
            ...(track.placements.cues_sorted || []).filter((h) => h.path !== r.cues_sorted_path),
          ];
        }
      }
      renderSetOverviewList(filteredTrackIndexes());
      if (typeof renderSetOverviewRail === "function") renderSetOverviewRail();
    }
    clearSelectedDests();
    clearFailedCopy(track.path); // R-98: the earlier "copy NOT saved" badge for this song is over
    // The copy is registered: say so NOW, by name (not after the next song has loaded), then advance.
    quickConfirm(`Copied “${trackDisplayTitle(track)}” → ${destLabel} ✓`);
    // R-82(c): lock Copy until the song that APPEARS next is fully settled — a ~150ms click must not copy it.
    if (!isSetOverviewMode()) {
      setCopyAdvanceLock(true);
      dropSongState(track.path);
    }
    // Refresh without clobbering status; remaining count comes from post-load list.
    await loadTracks({ skipStatus: true, keepPath: track.path });
    await loadFolders();
    if (isSetOverviewMode()) {
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
        requestRecommendation(currentTrack());
      }
      if (typeof renderSetOverviewRail === "function") renderSetOverviewRail();
    }
    updatePipelineStrip();
    const relDest = (r.library_dests && r.library_dests[0] && r.library_dests[0].relative_folder) || destLabel;
    const copyMsg = r.skipped_existing
      ? `“${trackDisplayTitle(track)}” is already in ${sauna ? `Sets/Sauna Fest/${relDest} and ` : ""}House/${relDest} - skipped, nothing was overwritten. The original stays where it was.`
      : `Copied “${trackDisplayTitle(track)}” to ${sauna ? `Sets/Sauna Fest/${relDest} and ` : ""}House/${relDest}. The original stays where it was; cues, loops and beat grid came along and the copy was checked.` +
        (r.new_folder_created ? " The new folder was created." : "");
    const handoffApi = globalThis.MusicSorterStatusHandoff || window.MusicSorterStatusHandoff;
    const handoff = isSetOverviewMode()
      ? handoffApi.composeSetSortSuccessHandoff(r)
      : handoffApi.composeSortSuccessHandoff(r, state.tracks.length, archiveBits);
    // House copy-sort: our message is authoritative ("COPIED", original kept); the handoff
    // only contributes its follow-up action (e.g. back to Add Cues when the queue is empty).
    setStatus(
      copyMsg,
      "success",
      handoff.action
        ? {
            label: handoff.action.label,
            gotoMode: handoff.action.gotoMode,
            onClick: () => setMode(handoff.action.gotoMode),
          }
        : null
    );
  } catch (err) {
    state.lastCopyFailed = true;
    const rawMsg = String((err && err.message) || err || "");
    const gridAction =
      /beat ?grid|unconfirmed/i.test(rawMsg) && track && track.path
        ? {
            label: "Grid is correct - copy again",
            onClick: () => {
              const cur = currentTrack();
              if (!cur || cur.path !== track.path) {
                loudNotice(`Open “${trackDisplayTitle(track)}” first, then press Grid is correct.`, "warn");
                return;
              }
              confirmGridManually(cur, { forCopy: true });
              sortSelectedImpl({ sauna: alsoSaunaFest() });
            },
          }
        : null;
    loudNotice(plainCopyError(err, track), "error", gridAction);
  } finally {
    state.sortInFlight = false;
    const tookSecs = stopCopyFeedback(true);
    if (state.copyAdvanceLock) {
      // Keep buttons disabled while the next song settles; then unlock.
      sortBtnBusy(true);
      try {
        for (const id of ["sortBtn", "approveBtnSide"]) {
          const el = $(id);
          if (!el) continue;
          el.disabled = true;
          el.textContent = "Loading next song…";
        }
      } catch { /* ignore */ }
      waitSongSettledForCopy(12000).finally(() => {
        setCopyAdvanceLock(false);
        sortBtnBusy(false);
        try { updateApproveButtons(); } catch { /* ignore */ }
      });
    } else {
      sortBtnBusy(false);
      try { updateApproveButtons(); } catch { /* ignore */ }
    }
    if (tookSecs >= 3 && !state.lastCopyFailed) quickConfirm(`Copy finished in ${tookSecs} s`);
    state.lastCopyFailed = false;
  }
}

/* ===== Copy feedback: the click is answered at once, with a spinner, elapsed time and what is happening ===== */
const copyFeedback = { timer: null, startedAt: 0, name: "", dest: "" };
function copyElapsedText() {
  const secs = Math.max(0, Math.floor((Date.now() - copyFeedback.startedAt) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}
function applyCopyBusyToButtons() {
  if (state.copyAdvanceLock) {
    for (const id of ["sortBtn", "approveBtnSide"]) {
      const el = $(id);
      if (!el) continue;
      el.disabled = true;
      el.classList.add("is-copying");
      if (!state.sortInFlight) el.textContent = "Loading next song…";
    }
    return;
  }
  if (!state.sortInFlight || !copyFeedback.startedAt) return;
  for (const id of ["sortBtn", "approveBtnSide"]) {
    const el = $(id);
    if (!el) continue;
    el.disabled = true;
    el.classList.add("is-copying");
    el.textContent = `Copying… ${copyElapsedText()}`;
  }
}
function updateCopyFeedback() {
  let bar = document.getElementById("copyProgress");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "copyProgress";
    bar.className = "copy-progress";
    bar.setAttribute("role", "status");
    bar.setAttribute("aria-live", "polite");
    document.body.appendChild(bar);
  }
  bar.replaceChildren();
  const spin = document.createElement("span");
  spin.className = "copy-spinner";
  spin.setAttribute("aria-hidden", "true");
  const txt = document.createElement("span");
  txt.className = "copy-progress-text";
  txt.textContent =
    `Copying “${copyFeedback.name}” to ${String(copyFeedback.dest).replace(/\s*\/\s*/g, "/")}… this can take a minute. ` +
    `Please don't switch songs. (${copyElapsedText()})`;
  bar.append(spin, txt);
  applyCopyBusyToButtons();
}
function startCopyFeedback(name, dest) {
  stopCopyFeedback(false);
  copyFeedback.startedAt = Date.now();
  copyFeedback.name = name || "song";
  copyFeedback.dest = dest || "House";
  updateCopyFeedback();
  copyFeedback.timer = setInterval(updateCopyFeedback, 250);
}
function stopCopyFeedback(report = true) {
  clearInterval(copyFeedback.timer);
  copyFeedback.timer = null;
  const took = copyFeedback.startedAt ? Math.round((Date.now() - copyFeedback.startedAt) / 1000) : 0;
  copyFeedback.startedAt = 0;
  document.getElementById("copyProgress")?.remove();
  for (const id of ["sortBtn", "approveBtnSide"]) $(id)?.classList.remove("is-copying");
  return report ? took : 0;
}

/* Server errors in plain words, with what to do next. */
function plainSortReason(raw) {
  const r = String(raw || "");
  if (/beat ?grid|unconfirmed/i.test(r)) return "the beat grid hasn't been confirmed yet - press “Grid is correct”, then copy again";
  if (/not found|does not exist|not an existing|not inside the house/i.test(r)) return "that destination folder isn't available - pick a folder from the House list";
  return r;
}

/* R-98: a copy that failed before and has now worked leaves nothing red behind (in memory or after a reload). */
function clearFailedCopy(path) {
  if (!path) return;
  const before = (state.failedEdits || []).length;
  state.failedEdits = (state.failedEdits || []).filter((f) => !(f && f.apiPath === "/api/sort" && f.path === path));
  if (state.loudNotice && /Copy (stopped|failed)/.test(String(state.loudNotice.text || ""))) {
    state.loudNotice = null;
  }
  persistFailedEdits();
  if (before !== state.failedEdits.length || true) renderSaveBadges();
}

function plainCopyError(err, track) {
  const raw = String((err && err.message) || err || "unknown error");
  const name = track ? trackDisplayTitle(track) : "this song";
  if (/beat ?grid|unconfirmed/i.test(raw)) {
    return `Copy stopped: the beat grid on “${name}” hasn't been confirmed yet. Press “Grid is correct” (or Align the grid), then copy again.`;
  }
  if (/not found|does not exist|not an existing|not inside the house/i.test(raw)) {
    return `Copy stopped: that destination folder isn't available. Pick a folder from the House list (or use New folder), then copy again.`;
  }
  if (/group folder/i.test(raw)) return `Copy stopped: ${raw}`;
  return `Copy failed for “${name}”: ${raw}`;
}

function sortBtnBusy(busy) {
  const sortBtn = $("sortBtn");
  if (sortBtn) {
    if (busy) sortBtn.dataset.busy = "1";
    else delete sortBtn.dataset.busy;
  }
  syncSortButtonState();
  if (sortBtn && busy) {
    sortBtn.disabled = true;
    sortBtn.textContent = copyFeedback.startedAt ? `Copying… ${copyElapsedText()}` : "Copying…";
  }
  const demoteBtn = $("demoteReadyBtn");
  if (demoteBtn) {
    demoteBtn.disabled = busy || !currentTrack() || isReviewMode();
    if (!busy) demoteBtn.textContent = "Back to Add Cues";
  }
  const removeBtn = $("removeReadyBtn");
  if (removeBtn) {
    removeBtn.disabled = busy || !currentTrack();
    if (!busy) removeBtn.textContent = "Trash from Ready";
  }
}

async function removeFromReadyOnly() {
  const track = currentTrack();
  if (!track) return;
  if (isReviewMode()) {
    setStatus("Switch to Sort mode to remove from Ready for Sort.", "error");
    return;
  }
  const ok = await showConfirmDialog({
    title: "Trash from Ready?",
    track: trackDisplayTitle(track),
    message:
      "This track will not be copied into any House folder. Audio goes to Trash and its VirtualDJ Song entry is removed.",
    note: "Recoverable from Trash only until emptied. Close VirtualDJ first when possible.",
    confirmLabel: "Trash file + VDJ entry",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Removing the VirtualDJ Song while VDJ is open may be overwritten on quit.",
      confirmLabel: "Trash anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then trash from Ready.", "error");
      return;
    }
  }

  sortBtnBusy(true);
  setStatus(`Trashing ${track.name} from Ready for Sort…`);
  try {
    const data = await api("/api/remove-ready", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        to_trash: true,
        allow_vdj_running: Boolean(allowRunning),
        remove_from_database: true,
      }),
    });
    setStatus(
      `Trashed from Ready: ${data.result?.name || track.name}`,
      "success"
    );
    state.selectedPath = "";
    dropSongState(track.path, { dropFailed: true });
    await loadTracks();
    await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    sortBtnBusy(false);
  }
}

/** Delete Add Cues audio + stems and wipe VirtualDJ Song (cues/loops) for that path. */
async function deleteAddCuesTrack() {
  const track = currentTrack();
  if (!track) return;
  if (!isReviewMode()) {
    setStatus("Switch to Add Cues to delete a track from the cue queue.", "error");
    return;
  }

  const cueN = track.cues?.cue_count ?? 0;
  const loopN = track.cues?.loop_count ?? 0;
  const setFile = isPajamathonSetQueueTrack(track);
  const ok = await showConfirmDialog({
    title: setFile ? "Delete from Pajamathon?" : "Delete from Add Cues?",
    track: trackDisplayTitle(track),
    message: setFile
      ? "This removes the Pajamathon set copy and its VirtualDJ entry (cues and loops for this path). Library copies are not touched."
      : "This permanently removes the track from Add Cues and deletes its VirtualDJ entry for this path. The Sets/Pajamathon copy stays if you already added it.",
    note: `Audio${track.stems_path ? " + stems" : ""} → Trash (${cueN} cues, ${loopN} loops). Close VirtualDJ first if it is open.`,
    confirmLabel: "Delete to Trash",
    tone: "danger",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Deleting the database entry while VirtualDJ is open can be overwritten on quit. Close it first when possible.",
      confirmLabel: "Delete anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then delete the track.", "error");
      return;
    }
  }

  sortBtnBusy(true);
  setStatus(`Deleting ${track.name}…`);
  try {
    const data = await api("/api/delete-add-cues", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        to_trash: true,
        allow_vdj_running: allowRunning,
      }),
    });
    const r = data.result || {};
    const dbPart = r.database?.removed_from_db
      ? ` · VDJ entry removed (${r.had_cues || 0} cues, ${r.had_loops || 0} loops)`
      : r.in_database === false
        ? " · not in VDJ database"
        : "";
    const linkPart =
      r.kept_hardlinks > 0
        ? ` · kept ${r.kept_hardlinks} other hard-link${
            r.kept_hardlinks === 1 ? "" : "s"
          } (library/inbox)`
        : "";
    const destPart = r.unlink_only ? "unlinked set name" : "Trash";
    setStatus(
      `Deleted → ${destPart}: ${r.name || track.name}${dbPart}${linkPart}`,
      "success"
    );
    state.recommendation = null;
    dropSongState(track.path, { dropFailed: true });
    await loadTracks({ skipStatus: true });
    setStatus(
      `Deleted → ${destPart}: ${r.name || track.name}${dbPart}${linkPart}`,
      "success"
    );
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    sortBtnBusy(false);
  }
}

async function demoteReadyToAddCues() {
  const track = currentTrack();
  if (!track) return;
  if (isReviewMode()) {
    setStatus("Switch to Sort mode to send tracks back to Add Cues.", "error");
    return;
  }

  const ok = await showConfirmDialog({
    title: "Send back to Add Cues?",
    track: trackDisplayTitle(track),
    message:
      "This track will leave Ready for Sort and return to Add Cues for re-review.",
    note:
      "File moves to Add Cues / Back from Ready. VirtualDJ cues and loops stay — only the FilePath is retargeted. Close VirtualDJ first if it is open.",
    confirmLabel: "Back to Add Cues",
    tone: "warning",
  });
  if (!ok) return;

  let allowRunning = false;
  if (await isVdjRunningFresh()) {
    allowRunning = await showConfirmDialog({
      title: "VirtualDJ is still open",
      track: trackDisplayTitle(track),
      message:
        "Path changes may be overwritten when VirtualDJ quits. Close it first when possible.",
      confirmLabel: "Continue anyway",
      tone: "warning",
    });
    if (!allowRunning) {
      setStatus("Close VirtualDJ, then send back to Add Cues.", "error");
      return;
    }
  }

  sortBtnBusy(true);
  const demoteBtn = $("demoteReadyBtn");
  if (demoteBtn) {
    demoteBtn.disabled = true;
    demoteBtn.textContent = "Sending…";
  }
  setStatus(`Sending ${track.name} back to Add Cues…`);
  try {
    const data = await api("/api/demote-ready", {
      method: "POST",
      body: JSON.stringify({
        path: track.path,
        allow_vdj_running: Boolean(allowRunning),
        subfolder: "Back from Ready",
      }),
    });
    const dest = data.result?.dest_path || "";
    const short = dest
      ? dest.split("/").slice(-3).join("/")
      : "Add Cues / Back from Ready";
    setStatus(`Back to Add Cues · ${short}`, "success");
    state.selectedPath = "";
    await loadTracks();
    await loadFolders();
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    sortBtnBusy(false);
    if (demoteBtn) {
      demoteBtn.disabled = false;
      demoteBtn.textContent = "Back to Add Cues";
    }
  }
}

async function createFolder() {
  const name = $("newFolderName").value.trim();
  if (!name) {
    setStatus("Enter a folder name.", "error");
    return;
  }
  try {
    // Create under the library of the last-clicked folder, or all (Both).
    const createLib = "House";
    const data = await api("/api/folders", {
      method: "POST",
      body: JSON.stringify({
        library: createLib,
        name,
        parent_relative_path: state.selectedPath || "",
      }),
    });
    $("newFolderName").value = "";
    setStatus(`Created ${state.library}/${data.folder.relative_path}`, "success");
    // Expand parent and select new folder
    if (data.folder.parent_relative_path) {
      state.expanded.add(data.folder.parent_relative_path);
    }
    await loadFolders();
    selectFolder(data.folder.relative_path, { expand: true });
  } catch (err) {
    setStatus(err.message, "error");
  }
}

function bindUi() {
  bindStemsUi();
  const exactCueJump = $("exactCueJump");
  if (exactCueJump) {
    exactCueJump.addEventListener("change", () => {
      setExactCueJump(exactCueJump.checked);
    });
    syncExactCueJumpUi();
  }
  const shortcutsHelpBtn = $("shortcutsHelpBtn");
  if (shortcutsHelpBtn) {
    shortcutsHelpBtn.addEventListener("click", () => toggleKeyboardOverlay());
  }
  const overlay = $("keyboardOverlay");
  if (overlay) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) setKeyboardOverlayOpen(false);
    });
  }
  const overlayClose = $("keyboardOverlayClose");
  if (overlayClose) {
    overlayClose.addEventListener("click", () => setKeyboardOverlayOpen(false));
  }

  // Cue/loop list tabs — bind early (must not depend on later listeners succeeding).
  const cueKindFilter = $("cueKindFilter");
  if (cueKindFilter) {
    cueKindFilter.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-cue-filter]");
      if (!btn || !cueKindFilter.contains(btn)) return;
      e.preventDefault();
      setCueListFilter(btn.getAttribute("data-cue-filter") || "all");
    });
    syncCueKindFilterUi();
  }

  const notesEl = $("vdjNotes");
  if (notesEl) {
    notesEl.addEventListener("input", () => scheduleNotesSave());
    notesEl.addEventListener("blur", () => {
      if (!state.notesDirty) return;
      if (state.notesSaveTimer) {
        clearTimeout(state.notesSaveTimer);
        state.notesSaveTimer = null;
      }
      const track = currentTrack();
      if (track && state.notesPath === track.path) {
        const gen = ++state.notesSaveGen;
        saveVdjNotes(track.path, notesEl.value, gen);
      }
    });
  }

  document.querySelectorAll("#accentPicker [data-accent-theme]").forEach((button) => {
    button.addEventListener("click", () => applyAccentTheme(button.dataset.accentTheme));
  });

  document.querySelectorAll("#schemePicker [data-color-scheme]").forEach((button) => {
    button.addEventListener("click", () => applyColorScheme(button.dataset.colorScheme));
  });

  document.querySelectorAll("#modeSeg button").forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });

  document.querySelectorAll("#libraryPathSeg button[data-library]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.library = btn.dataset.library;
      document.querySelectorAll("#libraryPathSeg button[data-library]").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      // House profile: single library; never clear
      // the selected destination.
      updatePathHint();
      updateSelectionLabels();
      await loadFolders();
    });
  });

  bindHouseTools();
  loadCrateFilter();
  syncCrateFilterUi();
  updateCueingFilterUi();
  document.querySelectorAll("#crateFilter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      setCrateFilter(btn.dataset.crate || "all");
    });
  });
  $("setOverviewCopyBtn")?.addEventListener("click", () => {
    copySetOverviewCues(state.index);
  });
  $("setOverviewSendBackBtn")?.addEventListener("click", sendBackSetOverview);
  $("setOverviewRemoveBtn")?.addEventListener("click", removeSetOverviewCopy);
  $("setOverviewApproveBtn")?.addEventListener("click", approveSetOverviewCues);
  $("setOverviewMustPlayBtn")?.addEventListener("click", markSetOverviewMustPlay);

  document.querySelectorAll("#setApprovalFilter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.setApprovalFilter = btn.getAttribute("data-set-approval") || btn.dataset.setApproval || "all";
      document.querySelectorAll("#setApprovalFilter button").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
      }
      renderTrackList();
      renderSetOverviewRail();
      updatePipelineStrip();
    });
  });

  document.querySelectorAll("#readinessFilter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.readinessFilter = btn.dataset.filter;
      document.querySelectorAll("#readinessFilter button").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      const indexes = filteredTrackIndexes();
      if (indexes.length && !indexes.includes(state.index)) {
        state.index = indexes[0];
        renderPlayer();
      } else {
        // Filter alone changes Add cues vs Retry cues labels.
        syncAutocueUi();
      }
      renderTrackList();
      updateBatchAddCuesButton();
    });
  });

  $("batchAddCuesBtn")?.addEventListener("click", () => batchAddCuesForNotCued("all"));
  $("batchPajamathonCuesBtn")?.addEventListener("click", () =>
    batchAddCuesForNotCued("pajamathon")
  );
  $("batchFixGridsBtn")?.addEventListener("click", () => batchFixPajamathonGrids());
  $("practiceRebuildBtn")?.addEventListener("click", rebuildTransitionsDb);
  // Manual button forces a full re-score; auto-load uses force:false (saved scores kept).
  $("practiceAnalyzeBtn")?.addEventListener("click", () =>
    startPracticeAnalyze({ force: true })
  );
  $("practiceExcludeBestBtn")?.addEventListener("click", () =>
    togglePracticeExcludeFromBest()
  );
  $("bestSetHidePlayedBtn")?.addEventListener("click", () =>
    toggleBestSetHidePlayed()
  );
  bindPracticeWaveInteractions();
  document.querySelectorAll("#practiceTxSort button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.practiceTxSort = btn.dataset.txSort || "order";
      document.querySelectorAll("#practiceTxSort button").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      renderPracticePanel();
    });
  });
  document.querySelectorAll("#practiceViewToggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      setPracticeView(btn.dataset.practiceView || "mix");
    });
  });

  resetFolderPickerFilter();
  window.addEventListener("pageshow", resetFolderPickerFilter);
  $("folderFilter").addEventListener("input", (e) => {
    state.filter = e.target.value.trim();
    renderFolders();
  });

  $("trackSearch")?.addEventListener("input", (e) => {
    state.trackSearch = e.target.value || "";
    const indexes = filteredTrackIndexes();
    if (indexes.length && !indexes.includes(state.index)) {
      state.index = indexes[0];
      state.trackGen += 1;
      renderPlayer();
    }
    renderTrackList();
  });

  $("actionsLogBtn")?.addEventListener("click", async () => {
    const panel = $("actionsLogPanel");
    if (!panel) return;
    if (!panel.hidden) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    await loadActionsLogPanel();
  });
  $("actionsLogClose")?.addEventListener("click", () => {
    const panel = $("actionsLogPanel");
    if (panel) panel.hidden = true;
  });

  $("sortBtn").addEventListener("click", sortSelected);
  const sChk = $("alsoSaunaChk");
  if (sChk) {
    try {
      if (localStorage.getItem("alsoSaunaFest") === "0") sChk.checked = false;
    } catch (_e) {}
    sChk.addEventListener("change", () => {
      try {
        localStorage.setItem("alsoSaunaFest", sChk.checked ? "1" : "0");
      } catch (_e) {}
      updateApproveButtons();
    });
  }
  $("demoteReadyBtn")?.addEventListener("click", demoteReadyToAddCues);
  $("removeReadyBtn").addEventListener("click", removeFromReadyOnly);
  AUTO_CUE_SCOPE_BUTTONS.forEach((spec) => {
    const el = $(spec.id);
    if (!el) return;
    el.addEventListener("click", () => retryCuesForCurrentTrack(spec.scope));
  });
  $("createFolderBtn").addEventListener("click", createFolder);
  $("newFolderName").addEventListener("keydown", (e) => {
    if (e.key === "Enter") createFolder();
  });
  $("refreshBtn").addEventListener("click", async () => {
    await loadHealth();
    if (isRecsMode()) {
      await forceRefreshRecs();
      return;
    }
    if (isPracticeMode()) {
      await loadPracticeMixes();
      setStatus("Practice mixes refreshed.");
      return;
    }
    if (isStemsMode()) {
      await loadStemsTab();
      setStatus("Stem inventory refreshed.");
      return;
    }
    await loadTracks({ keepPath: currentTrack()?.path });
    await hydrateAutocueJobs();
    await loadFolders();
    setStatus("Refreshed.");
  });
  $("rerunRecBtn").addEventListener("click", () => {
    if (isPracticeMode()) return;
    const t = resolveSortTrack();
    if (!t) return;
    requestRecommendation(t, { force: true });
  });

  $("approveBtn").addEventListener("click", () => {
    if (isSetOverviewMode()) {
      sortSelected();
      return;
    }
    if (isPajamathonSetQueueTrack(currentTrack())) {
      approveSetCues();
      return;
    }
    sortSelected();
  });
  $("approveBtnSide").addEventListener("click", () => {
    if (isSetOverviewMode()) {
      sortSelected();
      return;
    }
    if (isPajamathonSetQueueTrack(currentTrack())) {
      if (trackReadinessStatus(currentTrack()) === "approved") {
        skipToNextReviewTrack();
        return;
      }
      approveSetCues();
      return;
    }
    sortSelected();
  });
  $("skipBtn").addEventListener("click", skipToNextReviewTrack);
  $("toNoCuesBtn").addEventListener("click", () =>
    promoteTrack("no_cues_found", { requireCued: false })
  );
  $("toLowSkipBtn").addEventListener("click", () =>
    promoteTrack("low_quality_skip", { requireCued: false })
  );
  $("toAcLowBtn").addEventListener("click", () =>
    promoteTrack("ac_low_quality", { requireCued: false })
  );
  // Event delegation so Delete stays wired even if the panel re-renders.
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof Element)) return;
    const btn = t.closest("#deleteAddCuesBtn");
    if (!btn || btn.hasAttribute("disabled") || btn.disabled) return;
    ev.preventDefault();
    deleteAddCuesTrack();
  });

  const audio = $("audio");
  audio.addEventListener("timeupdate", () => {
    maybeLoopPlayback();
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("loadedmetadata", () => {
    renderCues();
    updatePlayhead();
    drawWaveform();
    updateTransportUi();
  });
  audio.addEventListener("seeked", () => {
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("durationchange", updateTransportUi);
  audio.addEventListener("pause", () => {
    stopLoopWatch();
    stopPlayheadWatch();
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("play", () => {
    if (state.loopPlaybackOn) startLoopWatch();
    startPlayheadWatch();
    updatePlayhead();
    updateTransportUi();
  });
  audio.addEventListener("ended", () => {
    // If looping a region near the end, wrap instead of stopping.
    if (state.loopPlaybackOn && state.activeLoopKey) {
      maybeLoopPlayback();
      if (!audio.paused) return;
    }
    stopLoopWatch();
    updateTransportUi();
  });
  audio.addEventListener("volumechange", () => {
    const volume = $("transportVolume");
    if (volume && Math.abs(Number(volume.value) - audio.volume) > 0.01) {
      volume.value = String(audio.volume);
    }
  });

  function toggleStagePlayback() {
    if (!audio.src) return;
    if (audio.paused) playAudio(audio).catch(() => {});
    else audio.pause();
  }
  function stopStagePlayback() {
    if (!audio.src) return;
    audio.pause();
    if (isBestSetMode() && state.practicePlayingBestId != null) {
      const item = (state.practiceBestItems || []).find(
        (x) => Number(x.id) === Number(state.practicePlayingBestId)
      );
      if (item) {
        seekPracticeTransition(Number(item.at_sec) || 0, {
          play: false,
          index: item.transition_index,
        });
        updateTransportUi();
        return;
      }
    }
    try {
      audio.currentTime = 0;
    } catch {
      /* ignore */
    }
    updateTransportUi();
  }
  $("playPauseBtn")?.addEventListener("click", () => toggleStagePlayback());
  $("bestSetPauseBtn")?.addEventListener("click", () => toggleStagePlayback());
  $("bestSetStopBtn")?.addEventListener("click", () => stopStagePlayback());
  $("quietSessionChip")?.addEventListener("click", () => disableQuietSession());
  $("previousTrackBtn")?.addEventListener("click", () => stepTrack(-1));
  $("nextTrackBtn")?.addEventListener("click", () => stepTrack(1));
  $("transportProgress")?.addEventListener("input", (e) => {
    const nextTime = Number(e.target.value);
    if (!Number.isFinite(nextTime)) return;
    audio.currentTime = nextTime;
    updateTransportUi();
  });
  $("transportVolume")?.addEventListener("input", (e) => {
    audio.volume = Math.min(1, Math.max(0, Number(e.target.value) || 0));
  });

  $("cueTimeline").addEventListener("click", (e) => {
    if (e.target.classList.contains("cue-marker")) return;
    const track = currentTrack();
    if (!track) return;
    const rect = $("cueTimeline").getBoundingClientRect();
    const x = e.clientX - rect.left;
    const usable = rect.width - 20;
    if (usable <= 0) return;
    const ratio = Math.min(1, Math.max(0, (x - 10) / usable));
    const duration = waveformDuration(track, audio) || trackDuration(track, audio);
    if (!duration) return;
    jumpToCue(ratio * duration);
  });

  $("waveformWrap").addEventListener("click", seekFromWaveformEvent);
  // passive:false so we can prevent page scroll while zooming the wave
  $("waveformWrap").addEventListener("wheel", onWaveformWheel, { passive: false });
  $("waveformWrap").addEventListener("dblclick", () => {
    if (state.gridAlignMode || state.placeCueMode || state.placeLoopMode) return;
    resetWaveZoom();
    drawWaveform();
  });
  // Beatgrid align: drag ones on the wave
  // Loop move: drag a loop band / start handle
  $("waveformWrap").addEventListener("pointerdown", (e) => {
    if (hitTestWaveCueChrome(e.clientX, e.clientY)) {
      state.waveSeekTime = null;
      return;
    }
    if (onGridAlignPointerDown(e)) {
      state.waveSeekTime = null;
      e.stopPropagation();
      return;
    }
    if (!state.placeCueMode && !state.placeLoopMode && onLoopDragPointerDown(e)) {
      state.waveSeekTime = null;
      e.stopPropagation();
      return;
    }
    snapshotWaveSeekTime(e.clientX);
  });
  $("waveformWrap").addEventListener("pointermove", (e) => {
    onGridAlignPointerMove(e);
    onLoopDragPointerMove(e);
    if (state.placeCueMode && !state.loopDrag) {
      updatePlaceCuePreview(e.clientX, e.shiftKey);
    }
    if (state.placeLoopMode && !state.loopDrag) {
      updatePlaceLoopPreview(e.clientX, e.shiftKey);
    }
  });
  $("waveformWrap").addEventListener("contextmenu", onWaveformContextMenu);
  $("waveformWrap").addEventListener("pointerup", (e) => {
    onGridAlignPointerUp(e);
    onLoopDragPointerUp(e);
  });
  $("waveformWrap").addEventListener("pointercancel", (e) => {
    onGridAlignPointerUp(e);
    onLoopDragPointerUp(e);
  });
  // Cursor hint when hovering a draggable cue or loop
  $("waveformWrap").addEventListener("pointermove", (e) => {
    if (state.gridAlignMode || state.loopDrag || state.placeCueMode || state.placeLoopMode) return;
    const wrap = $("waveformWrap");
    if (!wrap) return;
    const chrome = hitTestWaveCueChrome(e.clientX, e.clientY);
    const cueHit = chrome ? null : hitTestCueAtClientX(e.clientX);
    const loopHit = chrome || cueHit ? null : hitTestLoopAtClientX(e.clientX);
    wrap.classList.toggle("cue-chrome-hover", Boolean(chrome));
    wrap.classList.toggle("cue-hover", Boolean(cueHit));
    wrap.classList.toggle("loop-hover", Boolean(loopHit));
  });
  $("waveformWrap").addEventListener("pointerleave", () => {
    $("waveformWrap")?.classList.remove("loop-hover", "cue-hover", "cue-chrome-hover");
    if (state.placeCueMode) {
      state.placeCuePreview = null;
      drawWaveform();
    }
    if (state.placeLoopMode) {
      state.placeLoopPreview = null;
      drawWaveform();
    }
  });
  window.addEventListener("resize", () => drawWaveform());

  $("zoukSpeedBtn").addEventListener("click", enableZoukSpeed);
  $("normalSpeedBtn").addEventListener("click", enableNormalSpeed);
  $("halfBpmBtn")?.addEventListener("click", toggleHalfBpm);
  $("beatOnesBtn")?.addEventListener("click", toggleBeatOnes);
  $("gridAlignBtn")?.addEventListener("click", () => {
    if (state.gridAlignMode) cancelGridAlignMode();
    else openGridAlignMode();
  });
  $("autoAlignGridBtn")?.addEventListener("click", () => attemptAutoGridAlign());
  $("gridAlignCancelBtn")?.addEventListener("click", cancelGridAlignMode);
  $("gridAlignApplyBtn")?.addEventListener("click", applyGridAlign);
  $("placeCueBtn")?.addEventListener("click", togglePlaceCueMode);
  $("undoEditBtn")?.addEventListener("click", () => historyUndo());
  $("redoEditBtn")?.addEventListener("click", () => historyRedo());
  $("placeCueDoneBtn")?.addEventListener("click", cancelPlaceCueMode);
  $("placeLoopBtn")?.addEventListener("click", togglePlaceLoopMode);
  $("placeLoopDoneBtn")?.addEventListener("click", cancelPlaceLoopMode);
  $("gridNudgeBarLeft")?.addEventListener("click", () => nudgeGridAlignBeats(-4));
  $("gridNudgeBeatLeft")?.addEventListener("click", () => nudgeGridAlignBeats(-1));
  $("gridNudgeBeatRight")?.addEventListener("click", () => nudgeGridAlignBeats(1));
  $("gridNudgeBarRight")?.addEventListener("click", () => nudgeGridAlignBeats(4));
  $("loopPlayBtn")?.addEventListener("change", () => {
    const on = Boolean($("loopPlayBtn")?.checked);
    if (on !== Boolean(state.loopPlaybackOn)) toggleLoopPlayback();
  });
  // Restore ones overlay preference (default on)
  try {
    const saved = localStorage.getItem("musicSorter.showBeatOnes");
    if (saved === "0") state.showBeatOnes = false;
    else if (saved === "1") state.showBeatOnes = true;
  } catch {
    /* ignore */
  }
  try {
    const hidePlayed = localStorage.getItem("musicSorter.bestSetHidePlayed");
    if (hidePlayed === "1") state.practiceBestHidePlayed = true;
    else if (hidePlayed === "0") state.practiceBestHidePlayed = false;
  } catch {
    /* ignore */
  }
  syncBeatOnesBtn();
  $("targetBpmInput").addEventListener("change", () => {
    state.targetBpm = Number($("targetBpmInput").value) || 75;
    if (state.zoukSpeedOn || state.playbackRate < 0.98) enableZoukSpeed();
    else updateSpeedUi();
  });
  document.querySelectorAll(".speed-preset[data-target-bpm]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const n = Number(btn.dataset.targetBpm);
      if (!Number.isFinite(n)) return;
      const input = $("targetBpmInput");
      if (input) input.value = String(n);
      state.targetBpm = n;
      enableZoukSpeed();
      updateSpeedUi();
    });
  });
  $("speedSlider").addEventListener("input", (e) => {
    state.zoukSpeedOn = false;
    applyPlaybackRate(e.target.value);
  });
  // Some browsers reset playbackRate after play() — reassert.
  $("audio").addEventListener("play", () => {
    if ($("audio").playbackRate !== state.playbackRate) {
      $("audio").playbackRate = state.playbackRate;
    }
    updateTransportUi();
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select")) return;
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === "z" && undoToasts.length) {
      e.preventDefault();
      undoLatestDeleteToast();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) historyRedo();
      else historyUndo();
      return;
    }
    if (e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === "y") {
      e.preventDefault();
      historyRedo();
      return;
    }
    if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
      e.preventDefault();
      toggleKeyboardOverlay();
      return;
    }
    if (e.key === "Escape" && isKeyboardOverlayOpen()) {
      e.preventDefault();
      setKeyboardOverlayOpen(false);
      return;
    }
    if (e.code === "Space") {
      e.preventDefault();
      if (!audio.src) return;
      if (audio.paused) playAudio(audio).catch(() => {});
      else audio.pause();
    } else if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      stepTrack(1);
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      stepTrack(-1);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      seekByBeat(-1, e.shiftKey);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      seekByBeat(1, e.shiftKey);
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      sortSelected();
    } else if (isReviewMode() && (e.key === "a" || e.key === "A")) {
      e.preventDefault();
      if (isPajamathonSetQueueTrack(currentTrack())) {
        approveSetCues();
      } else {
        sortSelected();
      }
    } else if (isReviewMode() && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      skipToNextReviewTrack();
    } else if (e.key === "z" || e.key === "Z") {
      e.preventDefault();
      enableZoukSpeed();
    } else if (e.key === "n" || e.key === "N") {
      e.preventDefault();
      enableNormalSpeed();
    } else if (e.key === "h" || e.key === "H") {
      e.preventDefault();
      toggleHalfBpm();
    } else if (e.key === "l" || e.key === "L") {
      e.preventDefault();
      toggleLoopPlayback();
    } else if (e.key === "g" || e.key === "G") {
      e.preventDefault();
      toggleBeatOnes();
    } else if (e.key === "c" || e.key === "C") {
      e.preventDefault();
      togglePlaceCueMode();
    } else if (e.key === "o" || e.key === "O") {
      e.preventDefault();
      togglePlaceLoopMode();
    } else if (e.key === "Escape") {
      if (state.placeCueMode) {
        e.preventDefault();
        cancelPlaceCueMode();
      } else if (state.placeLoopMode) {
        e.preventDefault();
        cancelPlaceLoopMode();
      } else if (state.gridAlignMode) {
        e.preventDefault();
        cancelGridAlignMode();
      }
    } else if (/^[1-9]$/.test(e.key)) {
      const points = filteredCuePoints(currentTrack()?.cues?.points || []);
      const point = points[Number(e.key) - 1];
      if (point) {
        e.preventDefault();
        jumpToCue(point.pos, point, e);
      }
    }
  });
}

async function boot() {
  applyAccentTheme(storedAccentTheme(), { persist: false });
  applyColorScheme(storedColorScheme(), { persist: false });
  applyQuietSession();
  try {
    bindUi();
  } catch {
    /* keep booting */
  }
  try {
    bindAssembleUi();
  } catch {
    document.body.addEventListener("click", onAssembleChromeClick);
  }
  try {
    bindRecsUi();
  } catch {
    /* keep booting */
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("mode") === "add_cues" || params.get("add") === "1") {
    state.mode = "add_cues";
  }
  if (params.get("mode") === "set_overview") {
    state.mode = "set_overview";
  }
  if (params.get("mode") === "stems") {
    state.mode = "stems";
  }
  window.addEventListener("resize", () => {
    if (isPracticeMode()) schedulePracticeWaveRedraw();
  });
  applyModeUi();
  $("trackList")?.classList.add("list-loading");
  renderTrackList();
  try {
    restoreRememberedAutocueJobs();
    syncAutocueUi();
    await loadHealth();
    if (isStemsMode()) {
      await loadStemsTab();
      setStatus("Stem vocal check");
    } else {
      await loadTracks();
      await hydrateAutocueJobs();
      if (!isReviewMode()) {
        await loadFolders();
        selectFolder("");
      } else {
        // House fork: the House folder list is static, so load it up front.
        await loadFolders().catch(() => {});
      }
      setStatus("Ready. Use Sort, Add Cues, or Practice modes · Space / J/K");
    }
    requestAnimationFrame(resetWorkspaceScroll);
  } catch (err) {
    setStatus(err.message, "error");
  }
}

boot();



/* ── Transition recommendations tab ─────────────────────────────────────── */

state.recsNow = null;
state.recsJobId = null;
state.recsPollTimer = null; // job status poll while Gemini runs
state.recsNowPollTimer = null; // VDJ now-playing stamp (250ms)
state.recsResult = null;
state.recsNowPollInFlight = false;
state.recsNowSeq = 0; // ignore stale now-playing responses
state.recsRetryTimer = null;
state.recsJobRunning = false;
state.recsAutoForPath = ""; // last path we auto-fetched recs for
state.recsPendingPath = ""; // path change while a job is running
state.recsNowStampKey = "";

const RECS_NOW_STAMP_MS = 250;
const RECS_NOW_POLL_MS = 250;

function stopRecsPoll() {
  if (state.recsPollTimer) {
    clearInterval(state.recsPollTimer);
    state.recsPollTimer = null;
  }
}

function stopRecsNowPlayingPoll() {
  if (state.recsNowPollTimer) {
    clearInterval(state.recsNowPollTimer);
    state.recsNowPollTimer = null;
  }
  state.recsNowPollDueAt = 0;
  renderRecsPollCountdown();
}

function recsPollSecondsLeft() {
  const due = Number(state.recsNowPollDueAt || 0);
  if (!due) return 0;
  return Math.max(0, Math.ceil((due - Date.now()) / 1000));
}

function renderRecsPollCountdown() {
  const el = $("recsPollCountdown");
  if (!el) return;
  if (!isRecsMode() || !state.recsNowPollTimer) {
    el.textContent = "Live";
    return;
  }
  el.textContent = "Live";
}

function armRecsNowPlayingPoll() {
  state.recsNowPollDueAt = Date.now() + RECS_NOW_POLL_MS;
  renderRecsPollCountdown();
}

async function pollRecsNowStamp() {
  try {
    const data = await api("/api/recs/now-playing/stamp", { timeoutMs: 1500 });
    const key = `${data.path || ""}|${data.lastplay || 0}|${data.mtime || 0}`;
    if (key === state.recsNowStampKey && state.recsNow) return;
    if (state.recsNowPollInFlight) return;
    state.recsNowStampKey = key;
    await refreshRecsNowPlaying({ quiet: true, loadAudio: false });
  } catch (_) {
    /* next stamp tick retries */
  }
}

function startRecsNowPlayingPoll() {
  stopRecsNowPlayingPoll();
  if (!isRecsMode()) return;
  state.recsNowStampKey = "";
  refreshRecsNowPlaying({ quiet: true, loadAudio: false });
  armRecsNowPlayingPoll();
  state.recsNowPollTimer = setInterval(() => {
    if (!isRecsMode()) {
      stopRecsNowPlayingPoll();
      return;
    }
    renderRecsPollCountdown();
    armRecsNowPlayingPoll();
    if (state.recsNowPollInFlight) return;
    pollRecsNowStamp();
  }, RECS_NOW_STAMP_MS);
}

/** Auto-run Gemini energy buckets when now-playing is new or changed. */
function maybeAutoGenerateRecs(np, { force = false } = {}) {
  if (!isRecsMode()) return;
  const path = np?.path || "";
  if (!path) return;
  if (!force && path === state.recsAutoForPath && state.recsResult) return;
  if (state.recsJobRunning && !force) {
    // Queue this path; finish handler will re-run if still needed
    state.recsPendingPath = path;
    return;
  }
  if (force && state.recsJobRunning) {
    stopRecsPoll();
    state.recsJobRunning = false;
  }
  generateTransitionRecs({ auto: !force });
}

function showRecsSkeletons(label) {
  const buckets = $("recsBuckets");
  if (buckets) buckets.hidden = false;
  const msg = escapeHtml(label || "Ranking…");
  ["recsHigher", "recsSame", "recsLower"].forEach((id) => {
    const el = $(id);
    if (el) el.innerHTML = `<div class="recs-skel">${msg}</div><div class="recs-skel"></div>`;
  });
}

function setRecsStatus(msg, kind = "") {
  const el = $("recsStatus");
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    el.className = "recs-status";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.className = `recs-status ${kind}`.trim();
}

function recsLastPlayLabel(np) {
  const raw = np?.lastplay_unix || np?.lastplay;
  if (!raw) return "";
  const ts = Number(raw);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  const ageSec = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (ageSec < 15) return "just played";
  if (ageSec < 60) return `played ${ageSec}s ago`;
  if (ageSec < 3600) return `played ${Math.round(ageSec / 60)}m ago`;
  return `played ${Math.round(ageSec / 3600)}h ago`;
}

function renderRecsNowCard(np) {
  const title = $("recsNowTitle");
  const meta = $("recsNowMeta");
  const hint = $("recsNowHint");
  const timingEl = $("recsNowTiming");
  if (!title || !meta) return;
  if (!np) {
    title.textContent = "No recent VDJ play";
    meta.innerHTML = "";
    if (timingEl) {
      timingEl.hidden = true;
      timingEl.innerHTML = "";
    }
    if (hint) {
      hint.hidden = false;
      hint.textContent =
        "Play a track in VirtualDJ or STAGE — this view follows now-playing.";
    }
    return;
  }
  const label =
    np.artist && np.title
      ? `${np.artist} — ${np.title}`
      : np.title || np.name || "Unknown track";
  title.textContent = label;
  const chips = [];
  if (np.bpm) chips.push(`<span class="badge ok">${Number(np.bpm).toFixed(0)} BPM</span>`);
  if (np.key) chips.push(`<span class="badge neutral">${escapeHtml(np.key)}${np.camelot ? ` · ${escapeHtml(np.camelot)}` : ""}</span>`);
  if (np.genre) {
    const guessed = np.genre_source === "gemini";
    const genreTitle = guessed
      ? "Gemini genre guess (path/tag were unclear)"
      : np.genre_source === "path"
        ? "Genre from library folder"
        : "VDJ Genre tag";
    chips.push(
      `<span class="badge genre${guessed ? " guessed" : ""}" title="${genreTitle}">${escapeHtml(np.genre)}${guessed ? " · guessed" : ""}</span>`
    );
  }
  if (np.vibe) chips.push(`<span class="badge vibe" title="Folder vibe">${escapeHtml(np.vibe)}</span>`);
  if (np.is_cued) chips.push(`<span class="badge ok">${np.cue_count || 0} cues</span>`);
  else chips.push(`<span class="badge warn">not cued</span>`);
  const played = recsLastPlayLabel(np);
  if (played) chips.push(`<span class="badge neutral" title="VDJ last play">${escapeHtml(played)}</span>`);
  meta.innerHTML = chips.join("");
  if (timingEl) {
    const windows = Array.isArray(np.mix_windows) ? np.mix_windows : [];
    if (windows.length) {
      timingEl.hidden = false;
      timingEl.innerHTML = windows
        .slice(0, 2)
        .map((w) => {
          const missing = (w.missing || []).join(" + ");
          const present = (w.present || []).join(" + ");
          const hole = missing
            ? `needs ${escapeHtml(missing)}`
            : present
              ? escapeHtml(present)
              : "mix window";
          return `<div class="recs-timing-line" title="Outgoing frequency hole">
            <span class="recs-timing-clock">${escapeHtml(w.time || "")}</span>
            <span class="recs-timing-pair">${escapeHtml(w.label || "section")}</span>
            <span class="badge timing">${hole}</span>
          </div>`;
        })
        .join("");
    } else {
      timingEl.hidden = true;
      timingEl.innerHTML = "";
    }
  }
  if (hint) {
    hint.hidden = true;
  }
}

function recPathIsPajamathonSet(path, library) {
  const lib = String(library || "");
  const p = String(path || "");
  if (lib === "Pajamathon") return true;
  if (lib === "Sets" && /pajamathon/i.test(p)) return true;
  return /\/Sets\/Pajamathon/i.test(p);
}

function recSetIdentityKeys(p) {
  const keys = [];
  const artist = String(p?.artist || "").trim();
  const title = String(p?.title || "").trim();
  const label = `${artist} ${title}`.trim().toLowerCase().replace(/\s+/g, " ");
  if (label) keys.push(`label:${label}`);
  const rawName =
    String(p?.name || "").trim() ||
    String(p?.path || p?.relative_path || "")
      .split(/[/\\]/)
      .pop() ||
    "";
  let stem = rawName.replace(/\.(m4a|mp3|flac|wav|aiff?)$/i, "");
  stem = stem.replace(/^\d+[\s.\-]+/, "").trim().toLowerCase().replace(/\s+/g, " ");
  if (stem) {
    keys.push(`name:${stem}`);
    if (/\s[-–—]\s/.test(stem)) keys.push(`label:${stem}`);
  }
  return keys;
}

function recIsInSet(p) {
  // In-set = a Sets/Pajamathon FilePath exists, not the rec's folder.
  if (p?.in_set === true) return true;
  if (recPathIsPajamathonSet(p?.path || p?.relative_path, p?.library)) return true;
  const keys = recSetIdentityKeys(p);
  if (!keys.length) return false;
  const have = new Set(keys);
  for (const t of state.tracks || []) {
    if (!recPathIsPajamathonSet(t.path || t.relative_path, t.library || t.group)) continue;
    if (recSetIdentityKeys(t).some((k) => have.has(k))) return true;
  }
  return false;
}

function renderRecsBucket(el, picks) {
  if (!el) return;
  if (!picks?.length) {
    el.innerHTML = `<div class="empty recs-empty">No picks in this bucket.</div>`;
    return;
  }
  const ordered = [...picks].sort((a, b) => Number(recIsInSet(b)) - Number(recIsInSet(a)));
  el.innerHTML = ordered
    .map((p) => {
      const conf = p.confidence != null ? Math.round(Number(p.confidence) * 100) : null;
      const genreLabel = p.genre || "";
      const vibeLabel = p.vibe || "";
      const timing = p.timing || null;
      const fills = (timing?.fills || []).join(" + ");
      const timingHtml = timing
        ? `<div class="recs-card-timing" title="${escapeHtml(timing.summary || "")}">
            <span class="recs-timing-clock">${escapeHtml(timing.out_time || "")} → ${escapeHtml(timing.in_time || "")}</span>
            <span class="recs-timing-pair">${escapeHtml(timing.out_label || "out")} → ${escapeHtml(timing.in_label || "in")}</span>
            ${fills ? `<span class="badge timing">fills ${escapeHtml(fills)}</span>` : ""}
          </div>`
        : "";
      const inSet = recIsInSet(p);
      return `<article class="recs-card${inSet ? " recs-card-inset" : ""}" data-path="${escapeHtml(p.path || "")}">
        <div class="recs-card-title">${escapeHtml(p.artist || "")}${p.artist && p.title ? " — " : ""}${escapeHtml(p.title || p.name || "Track")}</div>
        <div class="recs-card-meta">
          ${inSet ? `<span class="badge recs-inset">in set</span>` : ""}
          ${p.bpm != null ? `<span class="badge ok">${Number(p.bpm).toFixed(0)} BPM</span>` : ""}
          ${p.key ? `<span class="badge neutral">${escapeHtml(p.key)}${p.camelot ? ` · ${escapeHtml(p.camelot)}` : ""}</span>` : ""}
          ${genreLabel ? `<span class="badge genre" title="Genre">${escapeHtml(genreLabel)}</span>` : ""}
          ${vibeLabel ? `<span class="badge vibe" title="Folder vibe">${escapeHtml(vibeLabel)}</span>` : !genreLabel ? `<span class="badge warn">genre ?</span>` : ""}
          ${p.library ? `<span class="badge neutral">${escapeHtml(p.library)}</span>` : ""}
          ${p.history_count ? `<span class="badge ok">history ×${p.history_count}</span>` : ""}
          ${conf != null ? `<span class="badge neutral">${conf}%</span>` : ""}
        </div>
        ${timingHtml}
        <p class="recs-card-reason">${escapeHtml(p.reason || "")}</p>
        <div class="recs-card-path" title="${escapeHtml(p.path || "")}">${escapeHtml(p.relative_path || p.name || "")}</div>
      </article>`;
    })
    .join("");
}

function renderRecsFilterBar(result) {
  const bar = $("recsFilterBar");
  const label = $("recsFilterLabel");
  const count = $("recsFilterCount");
  const detail = $("recsFilterDetail");
  if (!bar) return;
  const n = result?.candidates_considered ?? 0;
  const filters = result?.filters || {};
  const src = result?.source || state.recsNow || {};
  const bpmTol = filters.bpm_tolerance ?? 5;
  const filterLabel =
    filters.label || `In-key · ±${bpmTol} BPM · cued`;
  bar.hidden = false;
  if (label) label.textContent = filterLabel;
  if (count) count.textContent = `${n} match${n === 1 ? "" : "es"}`;
  const removedEl = $("recsRemovedPlays");
  if (removedEl) {
    const removedLabel = String(filters.removed_recent_label || "").trim();
    removedEl.hidden = !removedLabel;
    removedEl.textContent = removedLabel;
  }
  if (detail) {
    const bits = [];
    if (src.bpm != null) bits.push(`${Number(src.bpm).toFixed(0)} BPM`);
    if (src.key) bits.push(`${src.key}${src.camelot ? ` · ${src.camelot}` : ""}`);
    if (src.genre) {
      bits.push(
        src.genre_source === "gemini" ? `${src.genre} (guessed)` : src.genre
      );
    } else if (src.vibe) bits.push(src.vibe);
    bits.push("→ genre-aware higher / same / lower");
    detail.textContent = bits.join(" · ");
  }
}

function renderRecsResult(result) {
  state.recsResult = result;
  renderRecsFilterBar(result);
  const recs = result?.recommendations || {};
  const buckets = $("recsBuckets");
  if (buckets) buckets.hidden = false;
  renderRecsBucket($("recsHigher"), recs.higher_energy || []);
  renderRecsBucket($("recsSame"), recs.same_energy || []);
  renderRecsBucket($("recsLower"), recs.lower_energy || []);
  const notes = $("recsNotes");
  if (notes) {
    const n = recs.notes || "";
    notes.hidden = !n;
    notes.textContent = n;
  }
  const hist = result?.history_options || [];
  const drawer = $("recsHistoryDrawer");
  const body = $("recsHistoryBody");
  const count = $("recsHistoryCount");
  if (drawer && body) {
    drawer.hidden = !hist.length;
    if (count) count.textContent = String(hist.length);
    body.innerHTML = hist
      .map(
        (h) =>
          `<div class="recs-hist-row"><span class="badge neutral">${escapeHtml(
            h.source || ""
          )}</span> <strong>${escapeHtml(h.to_label || "")}</strong> ${
            h.count ? `<span class="badge ok">×${h.count}</span>` : ""
          } ${h.note ? `<span class="subtitle">${escapeHtml(h.note)}</span>` : ""}</div>`
      )
      .join("");
  }
}

async function refreshRecsNowPlaying({
  loadAudio = false,
  quiet = false,
  skipAuto = false,
  forceAuto = false,
} = {}) {
  // Quiet polls yield to an in-flight read. Manual/force always starts a new one.
  if (state.recsNowPollInFlight && quiet && !forceAuto) return state.recsNow;
  state.recsNowPollInFlight = true;
  const seq = ++state.recsNowSeq;
  const prevPath = state.recsNow?.path || "";
  if (!quiet) setRecsStatus("Reading VirtualDJ…");
  try {
    const qs = forceAuto ? "fast=1&refresh=1" : "fast=1";
    const data = await api(`/api/recs/now-playing?${qs}`, { timeoutMs: 4000 });
    if (seq !== state.recsNowSeq) return state.recsNow;
    const np = data.now_playing;
    const changed = (np?.path || "") !== prevPath;
    state.recsNow = np;
    renderRecsNowCard(np);
    // Fill BPM/key/genre without blocking Refresh
    if (np?.path) {
      api("/api/recs/now-playing", { timeoutMs: 12000 })
        .then((full) => {
          if (seq !== state.recsNowSeq) return;
          const rich = full.now_playing;
          if (!rich?.path || rich.path !== state.recsNow?.path) return;
          state.recsNow = rich;
          renderRecsNowCard(rich);
          if (isRecsMode()) renderTrackList();
          updatePipelineStrip();
        })
        .catch(() => {});
    }

    if (!quiet || changed || !np) {
      setRecsStatus(
        np
          ? `Now playing · ${np.artist ? np.artist + " — " : ""}${np.title || np.name}${
              np.key || np.bpm != null
                ? ` · ${np.bpm != null ? Number(np.bpm).toFixed(0) + " BPM" : ""}${
                    np.key ? (np.bpm != null ? " · " : "") + np.key : ""
                  }`
                : ""
            }`
          : "No VDJ history yet — play something in VirtualDJ or STAGE.",
        np ? "ok" : "warn"
      );
    }

    // Recs rail is driven by recsNow — don't fake a Sort-queue track (NaN MB).
    if (isRecsMode()) {
      renderTrackList();
    }
    updatePipelineStrip();

    // Auto-fetch higher/same/lower when track appears, changes, or user forced
    if (
      !skipAuto &&
      np?.path &&
      (forceAuto ||
        changed ||
        !state.recsResult ||
        state.recsAutoForPath !== np.path)
    ) {
      maybeAutoGenerateRecs(np, { force: forceAuto });
    }
    if (state.recsRetryTimer) {
      clearTimeout(state.recsRetryTimer);
      state.recsRetryTimer = null;
    }
    return np;
  } catch (err) {
    if (!quiet) setRecsStatus(err.message || String(err), "error");
    if (isRecsMode() && !state.recsRetryTimer) {
      state.recsRetryTimer = setTimeout(() => {
        state.recsRetryTimer = null;
        if (isRecsMode()) refreshRecsNowPlaying({ quiet: true, loadAudio: false });
      }, 2000);
    }
    return null;
  } finally {
    if (seq === state.recsNowSeq) state.recsNowPollInFlight = false;
  }
}

function _setRecsGenerateBtnIdle() {
  const btn = $("recsGenerateBtn");
  if (!btn) return;
  btn.disabled = false;
  btn.textContent = "Refresh";
}

async function generateTransitionRecs({ auto = false } = {}) {
  if (state.recsJobRunning && auto) {
    if (state.recsNow?.path) state.recsPendingPath = state.recsNow.path;
    return;
  }
  // Manual click always cancels in-flight poll and starts a new job
  stopRecsPoll();
  state.recsJobRunning = true;
  const pathForJob = state.recsNow?.path || null;
  const btn = $("recsGenerateBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Refreshing…";
  }
  showRecsSkeletons(auto ? "Ranking energy…" : "Refreshing recs…");
  setRecsStatus(
    auto
      ? "Filtering in-key ±5 BPM, ranking higher / same / lower…"
      : "Refreshing now-playing and ranking recs…",
    "running"
  );
  if (isRecsMode()) {
    setStatus("Ranking next tracks…");
  }
  try {
    // Always re-read VDJ history. Do not rank a stale recsNow after a failed fetch.
    const np = await refreshRecsNowPlaying({
      loadAudio: false,
      quiet: true,
      skipAuto: true,
      forceAuto: true,
    });
    const body = {
      path: np?.path || state.recsNow?.path || null,
      use_gemini: true,
      force_rescan: false,
      sync: false,
    };
    if (!body.path) {
      throw new Error("No now-playing track — play something in VirtualDJ.");
    }
    const data = await api("/api/recs/transitions", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (data.sync && data.result) {
      state.recsAutoForPath = body.path;
      state.recsJobRunning = false;
      renderRecsResult(data.result);
      const removedSync = data.result.filters?.removed_recent_label || "";
      setRecsStatus(
        `Ready · ${data.result.candidates_considered || 0} in-key ±5 BPM · ${
          data.result.recommendations?.model || ""
        }${removedSync ? ` · ${removedSync}` : ""}`,
        "ok"
      );
      _setRecsGenerateBtnIdle();
      return;
    }
    const job = data.job;
    if (!job?.id) throw new Error("No job returned");
    state.recsJobId = job.id;
    setRecsStatus(job.message || "Running…", "running");
    state.recsPollTimer = setInterval(async () => {
      try {
        const res = await api(`/api/recs/transitions/${job.id}`);
        const j = res.job;
        if (!j) return;
        // Ignore stale job polls if a newer job superseded this one
        if (state.recsJobId && state.recsJobId !== job.id) return;
        setRecsStatus(
          j.message || j.status,
          j.status === "error" ? "error" : "running"
        );
        if (j.status === "ok" || j.status === "error") {
          stopRecsPoll();
          state.recsJobRunning = false;
          _setRecsGenerateBtnIdle();
          if (j.status === "ok" && j.result) {
            state.recsAutoForPath = body.path || j.result?.source?.path || "";
            const src = j.result.source;
            const jobPath = src?.path || body.path || "";
            const nowTs = Number(state.recsNow?.lastplay_unix || 0);
            const srcTs = Number(src?.lastplay_unix || 0);
            const recsIsNewer =
              state.recsNow?.path &&
              state.recsNow.path !== jobPath &&
              (nowTs === 0 || srcTs === 0 || nowTs >= srcTs);
            // Don't clobber a newer now-playing the user just started
            if (src && !recsIsNewer && (!state.recsNow?.path || state.recsNow.path === jobPath)) {
              state.recsNow = src;
              renderRecsNowCard(src);
            }
            renderRecsResult(j.result);
            const n = j.result.candidates_considered || 0;
            const removed = j.result.filters?.removed_recent_label || "";
            setRecsStatus(
              `Ready · ${n} in-key ±5 BPM matches · higher / same / lower ranked · ${
                j.result.recommendations?.model || ""
              }${removed ? ` · ${removed}` : ""}`,
              "ok"
            );
          } else {
            setRecsStatus(j.error || j.message || "Failed", "error");
          }
          // If now-playing changed during the job, auto-run again
          const pending = state.recsPendingPath;
          state.recsPendingPath = "";
          if (
            pending &&
            pending !== state.recsAutoForPath &&
            isRecsMode()
          ) {
            maybeAutoGenerateRecs({ path: pending }, { force: true });
          }
        }
      } catch (err) {
        if (state.recsJobId && state.recsJobId !== job.id) return;
        stopRecsPoll();
        state.recsJobRunning = false;
        _setRecsGenerateBtnIdle();
        setRecsStatus(err.message || String(err), "error");
      }
    }, 900);
  } catch (err) {
    state.recsJobRunning = false;
    _setRecsGenerateBtnIdle();
    setRecsStatus(err.message || String(err), "error");
  }
}

async function forceRefreshRecs() {
  stopRecsPoll();
  state.recsJobRunning = false;
  state.recsAutoForPath = "";
  state.recsPendingPath = "";
  state.recsNowPollInFlight = false;
  if (isRecsMode()) {
    if (!state.recsNowPollTimer) startRecsNowPlayingPoll();
    else armRecsNowPlayingPoll();
  }
  setRecsStatus("Picking up now-playing…", "running");
  showRecsSkeletons("Picking up track…");
  _setRecsGenerateBtnIdle();
  const np = await refreshRecsNowPlaying({
    loadAudio: false,
    quiet: false,
    forceAuto: true,
  });
  if (!np) {
    setRecsStatus("No VDJ history play found — load a track in VirtualDJ.", "warn");
    _setRecsGenerateBtnIdle();
  }
}

function bindRecsUi() {
  const gen = $("recsGenerateBtn");
  if (gen && !gen.dataset.bound) {
    gen.dataset.bound = "1";
    gen.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      forceRefreshRecs();
    });
  }
  const ref = $("recsRefreshNowBtn");
  if (ref && !ref.dataset.bound) {
    ref.dataset.bound = "1";
    ref.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      forceRefreshRecs();
    });
  }
}

state.assembleJob = null;
state.assemblePollTimer = null;
state.assemblePollSeq = 0;
state.assemblePreview = null;
state.assemblePlaylistSort = "crate";
state.assembleLaneShares = null;
state.assembleMinFit = null;
state.assembleMixTimer = null;
state.assembleMixPrefsTimer = null;

function renderAssembleRail() {
  const root = $("trackList");
  if (!root) return;
  const newest = state.assemblePreview?.newest || [];
  const total = state.assemblePreview?.total;
  if (!newest.length) {
    root.innerHTML = emptyStateHtml({
      icon: "☰",
      title: "House crate",
      copy: "Newest tracks will list here. Assemble scores them for Pajamathon.",
      ctaLabel: "",
      ctaMode: "",
    });
    return;
  }
  root.innerHTML = `<div class="assemble-rail-head">Newest · ${total || newest.length}</div>${newest
    .map(
      (t) => `<div class="track assemble-rail-track">
        <div class="track-title">${escapeHtml(t.title || t.name || "")}</div>
        <div class="track-sub">${escapeHtml(t.artist || t.relative_path || "")}</div>
      </div>`
    )
    .join("")}`;
}

function setAssembleStatus(msg, kind = "") {
  const el = $("assembleStatus");
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.className = `recs-status ${kind}`.trim();
}

function assembleSongKey(t) {
  return MusicSorterAssemble.assembleSongKey(t);
}

function uniqueAssembleTracks(tracks) {
  return MusicSorterAssemble.uniqueAssembleTracks(tracks);
}

function assembleFitPercent(track) {
  return MusicSorterAssemble.assembleFitPercent(track);
}

function sortAssemblePlaylist(tracks, mode) {
  return MusicSorterAssemble.sortAssemblePlaylist(tracks, mode);
}

function syncAssemblePlaylistSortUi() {
  const mode = state.assemblePlaylistSort || "crate";
  document.querySelectorAll("#assemblePlaylistSort button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.plSort === mode);
  });
}

const ASSEMBLE_LANES = MusicSorterAssemble.ASSEMBLE_LANES;
const ASSEMBLE_SHARE_STORE = MusicSorterAssemble.ASSEMBLE_SHARE_STORE;
const ASSEMBLE_MIN_FIT_STORE = MusicSorterAssemble.ASSEMBLE_MIN_FIT_STORE;
const ASSEMBLE_DEFAULT_MIN_FIT = MusicSorterAssemble.ASSEMBLE_DEFAULT_MIN_FIT;

function defaultAssembleShares() {
  return MusicSorterAssemble.defaultAssembleShares();
}

function normalizeClientShares(raw) {
  return MusicSorterAssemble.normalizeClientShares(raw);
}

function sharesToPercents(shares) {
  return MusicSorterAssemble.sharesToPercents(shares);
}

function readAssembleMixSharesFromDom() {
  const root = $("assembleMixLanes");
  if (!root) return null;
  const raw = {};
  let any = false;
  root.querySelectorAll("input.assemble-mix-pct").forEach((el) => {
    const n = Number(el.value || 0);
    raw[el.dataset.lane] = n;
    if (n > 0) any = true;
  });
  return any ? normalizeClientShares(raw) : null;
}

function loadAssembleShares() {
  try {
    const stored = JSON.parse(localStorage.getItem(ASSEMBLE_SHARE_STORE) || "null");
    if (stored && typeof stored === "object") return normalizeClientShares(stored);
  } catch {
    /* ignore */
  }
  const saved = state.assemblePreview?.mix_prefs;
  if (saved?.saved && saved.lane_shares) return normalizeClientShares(saved.lane_shares);
  const fromDom = readAssembleMixSharesFromDom();
  if (fromDom) return fromDom;
  const preview = state.assemblePreview?.defaults?.lane_shares;
  if (preview) return normalizeClientShares(preview);
  return defaultAssembleShares();
}

function persistAssembleShares(shares, opts) {
  const syncServer = !opts || opts.syncServer !== false;
  state.assembleLaneShares = normalizeClientShares(shares);
  try {
    localStorage.setItem(ASSEMBLE_SHARE_STORE, JSON.stringify(state.assembleLaneShares));
  } catch {
    /* ignore */
  }
  if (syncServer) scheduleSaveAssembleMixPrefs();
}

function scheduleSaveAssembleMixPrefs() {
  if (state.assembleMixPrefsTimer) clearTimeout(state.assembleMixPrefsTimer);
  state.assembleMixPrefsTimer = setTimeout(() => {
    state.assembleMixPrefsTimer = null;
    saveAssembleMixPrefs();
  }, 280);
}

async function saveAssembleMixPrefs() {
  const shares = state.assembleLaneShares || readAssembleMixShares();
  const minFit = state.assembleMinFit ?? readAssembleMinFit();
  try {
    await api("/api/assemble/mix-prefs", {
      method: "POST",
      body: JSON.stringify({
        lane_shares: sharesToPercents(shares),
        min_fit: minFit,
      }),
      timeoutMs: 8000,
    });
  } catch {
    /* keep local copy even if Notes write fails */
  }
}

function applySavedMixPrefs(prefs) {
  if (!prefs?.saved || !prefs.lane_shares) return;
  if (document.activeElement?.closest?.("#assembleMix")) return;
  try {
    if (localStorage.getItem(ASSEMBLE_SHARE_STORE)) return;
  } catch {
    /* ignore */
  }
  persistAssembleShares(prefs.lane_shares, { syncServer: false });
  if (prefs.min_fit != null) persistAssembleMinFit(prefs.min_fit, { syncServer: false });
  writeAssembleMixTuners(state.assembleLaneShares);
  writeAssembleMinFit(state.assembleMinFit);
}

function normalizeClientMinFit(raw) {
  return MusicSorterAssemble.normalizeClientMinFit(raw);
}

function loadAssembleMinFit() {
  try {
    const stored = localStorage.getItem(ASSEMBLE_MIN_FIT_STORE);
    if (stored != null && stored !== "") return normalizeClientMinFit(stored);
  } catch {
    /* ignore */
  }
  const saved = state.assemblePreview?.mix_prefs;
  if (saved?.saved && saved.min_fit != null) return normalizeClientMinFit(saved.min_fit);
  const preview = state.assemblePreview?.defaults?.min_fit;
  if (preview != null) return normalizeClientMinFit(preview);
  return ASSEMBLE_DEFAULT_MIN_FIT;
}

function persistAssembleMinFit(value, opts) {
  const syncServer = !opts || opts.syncServer !== false;
  state.assembleMinFit = normalizeClientMinFit(value);
  try {
    localStorage.setItem(ASSEMBLE_MIN_FIT_STORE, String(state.assembleMinFit));
  } catch {
    /* ignore */
  }
  if (syncServer) scheduleSaveAssembleMixPrefs();
}

function readAssembleMinFit() {
  const el = $("assembleMinFitNum") || $("assembleMinFit");
  if (!el) return state.assembleMinFit ?? loadAssembleMinFit();
  return normalizeClientMinFit(el.value);
}

function writeAssembleMinFit(value) {
  const frac = normalizeClientMinFit(value);
  const pct = Math.round(frac * 100);
  const range = $("assembleMinFit");
  const num = $("assembleMinFitNum");
  if (range) range.value = String(pct);
  if (num) num.value = String(pct);
}

function readAssembleMixShares() {
  const root = $("assembleMixLanes");
  if (!root) return state.assembleLaneShares || loadAssembleShares();
  const raw = {};
  root.querySelectorAll("input.assemble-mix-pct").forEach((el) => {
    raw[el.dataset.lane] = Number(el.value || 0);
  });
  return normalizeClientShares(raw);
}

function syncAssembleMixSum() {
  const root = $("assembleMixLanes");
  const sumEl = $("assembleMixSum");
  if (!root || !sumEl) return;
  let sum = 0;
  root.querySelectorAll("input.assemble-mix-pct").forEach((el) => {
    sum += Number(el.value || 0);
  });
  const leftover = 100 - sum;
  sumEl.textContent =
    leftover === 0 ? "100%" : leftover > 0 ? `${sum}% · ${leftover}% leftover` : `${sum}%`;
  sumEl.classList.toggle("assemble-mix-sum-warn", leftover < 0);
}

function writeAssembleMixTuners(shares) {
  const pcts = sharesToPercents(shares);
  document.querySelectorAll("#assembleMixLanes input.assemble-mix-pct").forEach((el) => {
    el.value = String(pcts[el.dataset.lane] || 0);
  });
  syncAssembleMixSum();
}

function renderAssembleMixTuners() {
  const root = $("assembleMixLanes");
  if (!root) return;
  if (!state.assembleLaneShares) state.assembleLaneShares = loadAssembleShares();
  if (!root.querySelector("input.assemble-mix-pct")) {
    const pcts = sharesToPercents(state.assembleLaneShares);
    root.innerHTML = ASSEMBLE_LANES.map(([id, label]) => {
      const pct = pcts[id] || 0;
      return `<label class="assemble-mix-lane">
        <span class="assemble-mix-name">${label}</span>
        <input type="number" class="assemble-mix-pct" min="0" max="100" step="1" value="${pct}" data-lane="${id}" aria-label="${label} target percent" />
        <span class="subtitle">%</span>
        <span class="assemble-mix-actual" data-lane="${id}">—</span>
      </label>`;
    }).join("");
  }
  writeAssembleMixTuners(state.assembleLaneShares);
}

function updateAssembleMixActuals(mix, playlistLen) {
  const total =
    playlistLen ||
    Object.values(mix || {}).reduce((a, b) => a + Number(b || 0), 0);
  document.querySelectorAll(".assemble-mix-actual").forEach((el) => {
    const n = Number((mix || {})[el.dataset.lane] || 0);
    const pct = total ? Math.round((n / total) * 100) : 0;
    el.textContent = total ? `${pct}% now` : "—";
  });
}

function renderAssembleLists(result) {
  const playlist = result?.playlist || [];
  const ranked = result?.ranked || [];
  const mix = result?.mix || {};
  updateAssembleMixActuals(mix, playlist.length);
  if (
    state.assembleMinFit == null &&
    result?.min_fit != null &&
    !document.activeElement?.closest?.(".assemble-min-fit")
  ) {
    persistAssembleMinFit(result.min_fit, { syncServer: false });
    writeAssembleMinFit(result.min_fit);
  }
  const pl = $("assemblePlaylist");
  const rk = $("assembleRanked");
  const pc = $("assemblePlaylistCount");
  const rc = $("assembleRankedCount");
  if (pc) pc.textContent = String(playlist.length);
  if (rc) rc.textContent = `${result?.scored_total || ranked.length} scored`;
  if (pl) {
    const uniquePl = sortAssemblePlaylist(
      uniqueAssembleTracks(playlist),
      state.assemblePlaylistSort || "crate"
    );
    if (pc) pc.textContent = String(uniquePl.length);
    syncAssemblePlaylistSortUi();
    pl.innerHTML = uniquePl
      .map((t, i) => {
        const newest = t.newest ? `<span class="badge ok">new</span>` : "";
        return `<article class="assemble-card">
          <div class="assemble-card-idx">${i + 1}</div>
          <div>
            <div class="recs-card-title">${escapeHtml(t.artist || "")}${t.artist && t.title ? " — " : ""}${escapeHtml(t.title || t.name || "")}</div>
            <div class="recs-card-meta">
              ${t.bpm != null ? `<span class="badge ok">${Number(t.bpm).toFixed(0)} BPM</span>` : ""}
              ${t.vibe ? `<span class="badge vibe">${escapeHtml(t.vibe)}</span>` : ""}
              ${t.lane ? `<span class="badge genre">${escapeHtml(t.lane)}</span>` : ""}
              ${newest}
              <span class="badge timing">${assembleFitPercent(t)}% fit</span>
            </div>
            <p class="recs-card-reason">${escapeHtml(t.reason || "")}</p>
          </div>
        </article>`;
      })
      .join("");
  }
  if (rk) {
    const uniqueRanked = sortAssemblePlaylist(
      uniqueAssembleTracks(ranked),
      state.assemblePlaylistSort === "fit" ? "fit" : "crate"
    );
    rk.innerHTML = uniqueRanked
      .map((t) => {
        const verdict = t.verdict || "";
        return `<article class="assemble-card assemble-card-rank">
          <div>
            <div class="recs-card-title">${escapeHtml(t.artist || "")}${t.artist && t.title ? " — " : ""}${escapeHtml(t.title || t.name || "")}</div>
            <div class="recs-card-meta">
              <span class="badge ${verdict === "keep" ? "ok" : verdict === "skip" ? "warn" : "neutral"}">${escapeHtml(verdict || "—")}</span>
              <span class="badge timing">${assembleFitPercent(t)}%</span>
              ${t.vibe ? `<span class="badge vibe">${escapeHtml(t.vibe)}</span>` : ""}
            </div>
            <p class="recs-card-reason">${escapeHtml(t.reason || "")}</p>
          </div>
        </article>`;
      })
      .join("");
  }
  const files = result?.files;
  const fileEl = $("assembleFiles");
  if (fileEl) {
    if (files?.folder || files?.cues) {
      fileEl.hidden = false;
      const cueMsg = files.cues?.message ? ` · ${files.cues.message}` : "";
      fileEl.textContent = files.folder
        ? `Set folder · ${files.count || playlist.length} songs → ${files.folder}${cueMsg}`
        : `Wrote ${files.count || playlist.length} songs → ${files.cues}`;
    } else {
      fileEl.hidden = true;
    }
  }
}

function assembleJobBusy(job) {
  return MusicSorterAssemble.assembleJobBusy(job);
}

function unstickAssembleJob(job, message) {
  stopAssemblePoll();
  const next = job && typeof job === "object" ? { ...job } : { ...(state.assembleJob || {}) };
  if (assembleJobBusy(next)) next.status = "ok";
  next.message =
    message ||
    next.message ||
    "Scoring stopped. Click Assemble to continue — saved evals stay.";
  renderAssembleJob(next);
  setAssembleStatus(next.message, "");
}

function renderAssembleJob(job) {
  state.assembleJob = job;
  const prog = $("assembleProgress");
  const label = $("assembleProgressLabel");
  const count = $("assembleProgressCount");
  const fill = $("assembleBarFill");
  const stopBtn = $("assembleStopBtn");
  const startBtn = $("assembleStartBtn");
  if (prog) prog.hidden = !job;
  if (label) label.textContent = job?.message || "Idle";
  const total = job?.total || 0;
  const scored = job?.scored || 0;
  if (count) count.textContent = total ? `${scored} / ${total}` : "—";
  if (fill) {
    const pct = total ? Math.min(100, Math.round((scored / total) * 100)) : 0;
    fill.style.width = `${pct}%`;
  }
  const busy = assembleJobBusy(job);
  if (stopBtn) stopBtn.hidden = !busy;
  if (startBtn) {
    startBtn.disabled = busy;
    startBtn.textContent = busy ? "Scoring…" : "Assemble Pajamathon";
  }
  if (job?.result) {
    renderAssembleLists(job.result);
  } else if (state.assemblePreview?.result) {
    renderAssembleLists(state.assemblePreview.result);
  }
  if (job?.status === "error") setAssembleStatus(job.error || job.message, "error");
  else if (job?.status === "ok") setAssembleStatus(job.message, "ok");
  else if (job?.status === "running") setAssembleStatus(job.message, "running");
  else setAssembleStatus(job?.message || "", "");
  if (isAssembleMode()) {
    renderTrackList();
    updatePipelineStrip();
  }
}

function stopAssemblePoll() {
  state.assemblePollSeq += 1;
  if (state.assemblePollTimer) {
    clearInterval(state.assemblePollTimer);
    state.assemblePollTimer = null;
  }
}

async function recoverAssembleJob(seq) {
  const latest = await api("/api/assemble/latest", { timeoutMs: 4000 }).catch(() => null);
  if (seq !== state.assemblePollSeq) return true;
  const job = latest?.job;
  if (assembleJobBusy(job)) {
    if (!job.result && (state.assembleJob?.result || state.assemblePreview?.result)) {
      job.result = state.assembleJob?.result || state.assemblePreview?.result;
    }
    renderAssembleJob(job);
    startAssemblePoll(job.id);
    return true;
  }
  return false;
}

function startAssemblePoll(jobId) {
  stopAssemblePoll();
  if (!jobId) return;
  const seq = state.assemblePollSeq;
  state.assemblePollTimer = setInterval(async () => {
    if (seq !== state.assemblePollSeq) return;
    try {
      const data = await api(`/api/assemble/status/${jobId}`, { timeoutMs: 8000 });
      if (seq !== state.assemblePollSeq) return;
      renderAssembleJob(data.job);
      if (data.job && !assembleJobBusy(data.job)) {
        if (seq === state.assemblePollSeq) stopAssemblePoll();
      }
    } catch (err) {
      if (seq !== state.assemblePollSeq) return;
      const msg = err.message || String(err);
      if (/404|Unknown assemble job/i.test(msg)) {
        const recovered = await recoverAssembleJob(seq);
        if (seq !== state.assemblePollSeq) return;
        if (recovered) return;
        unstickAssembleJob(
          state.assembleJob,
          "That assemble run ended — lists still show saved evals. Click Assemble to continue."
        );
        return;
      }
      if (seq !== state.assemblePollSeq) return;
      setAssembleStatus(msg, "error");
    }
  }, 1200);
}

async function loadAssemblePreview() {
  try {
    const data = await api("/api/assemble/preview?library=House", { timeoutMs: 60000 });
    state.assemblePreview = data;
    applySavedMixPrefs(data.mix_prefs);
    const brief = $("assembleBrief");
    if (brief && !brief.value.trim() && data.event?.brief) brief.value = data.event.brief;
    const name = $("assembleEventName");
    if (name && !name.value.trim() && data.event?.name) name.value = data.event.name;
    renderTrackList();
    const latest = await api("/api/assemble/latest", { timeoutMs: 4000 }).catch(() => null);
    const liveJob = assembleJobBusy(state.assembleJob);
    if (latest?.job) {
      if (!latest.job.result && data.result) latest.job.result = data.result;
      const keepNewer =
        liveJob &&
        state.assembleJob?.id &&
        latest.job.id !== state.assembleJob.id &&
        (latest.job.created_at || 0) < (state.assembleJob.created_at || 0);
      if (!keepNewer) {
        renderAssembleJob(latest.job);
        if (assembleJobBusy(latest.job)) startAssemblePoll(latest.job.id);
      }
    } else if (liveJob) {
      unstickAssembleJob(
        state.assembleJob,
        "Scoring stopped. Click Assemble to continue — saved evals stay."
      );
    }
    $("countsBadge").textContent =
      data.cached_evals != null
        ? `${data.cached_evals} cached · ${data.unique_songs || data.total || 0} songs`
        : `${data.total || 0} tracks`;
    if (
      data.result &&
      !(latest?.job && latest.job.result) &&
      !assembleJobBusy(state.assembleJob)
    ) {
      renderAssembleLists(data.result);
      state.assembleJob = {
        status: "ok",
        message: `${data.result.scored_total} saved evals loaded`,
        result: data.result,
        event_name: data.result.event_name,
      };
    }
    const jobBusy = assembleJobBusy(latest?.job);
    if (data.cached_evals && !jobBusy) {
      setAssembleStatus(
        `${data.cached_evals} saved evals in the lists — next run skips those LLM calls`,
        "ok"
      );
    }
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function startAssemble() {
  try {
    const eventName = $("assembleEventName")?.value?.trim() || "Pajamathon";
    const brief = $("assembleBrief")?.value?.trim() || "";
    const target = Number($("assembleTarget")?.value || 400);
    const chunk = Number($("assembleChunk")?.value || 16);
    const scanAll = Boolean($("assembleScanAll")?.checked);
    setAssembleStatus("Starting library scan…", "running");
    const previousResult = state.assembleJob?.result || state.assemblePreview?.result;
    const laneShares = readAssembleMixShares();
    persistAssembleShares(laneShares);
    const data = await api("/api/assemble/start", {
      method: "POST",
      body: JSON.stringify({
        event_name: eventName,
        brief,
        library: "House",
        chunk_size: chunk,
        target,
        use_gemini: true,
        scan_all: scanAll,
        lane_shares: sharesToPercents(laneShares),
        min_fit: readAssembleMinFit(),
      }),
      timeoutMs: 15000,
    });
    if (data.job && !data.job.result && previousResult) {
      data.job.result = previousResult;
    }
    renderAssembleJob(data.job);
    if (data.job?.id) startAssemblePoll(data.job.id);
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function stopAssemble() {
  const id = state.assembleJob?.id;
  if (!id) return;
  try {
    const data = await api(`/api/assemble/stop/${id}`, { method: "POST", timeoutMs: 8000 });
    renderAssembleJob(data.job);
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function exportAssembleFolder() {
  setAssembleStatus("Writing Sets/Pajamathon 2026…", "running");
  try {
    const data = await api("/api/assemble/export", { method: "POST", timeoutMs: 120000 });
    if (state.assembleJob?.result) {
      state.assembleJob.result.files = data.files;
      renderAssembleJob(state.assembleJob);
    }
    setAssembleStatus(
      `Set folder ready · ${data.files?.count || 0} songs → ${data.files?.folder || ""}`,
      "ok"
    );
  } catch (err) {
    setAssembleStatus(err.message || String(err), "error");
  }
}

async function applyAssembleMix() {
  const shares = readAssembleMixShares();
  const minFit = readAssembleMinFit();
  persistAssembleShares(shares);
  persistAssembleMinFit(minFit);
  writeAssembleMixTuners(shares);
  writeAssembleMinFit(minFit);
  const eventName = $("assembleEventName")?.value?.trim() || "Pajamathon";
  const target = Number($("assembleTarget")?.value || 400);
  try {
    const data = await api("/api/assemble/rebalance", {
      method: "POST",
      body: JSON.stringify({
        event_name: eventName,
        target,
        lane_shares: sharesToPercents(shares),
        min_fit: minFit,
      }),
      timeoutMs: 20000,
    });
    if (data.result) {
      if (state.assembleJob) {
        state.assembleJob.result = data.result;
        if (data.job?.lane_shares) state.assembleJob.lane_shares = data.job.lane_shares;
        if (data.job?.min_fit != null) state.assembleJob.min_fit = data.job.min_fit;
      }
      renderAssembleLists(data.result);
    }
    const pct = Math.round(minFit * 100);
    setAssembleStatus(`Playlist rebuilt · min ${pct}% fit`, "ok");
  } catch (err) {
    const msg = err.message || String(err);
    if (/No assembled playlist/i.test(msg)) return;
    setAssembleStatus(msg, "error");
  }
}

function scheduleAssembleMixApply() {
  if (state.assembleMixTimer) clearTimeout(state.assembleMixTimer);
  state.assembleMixTimer = setTimeout(() => {
    state.assembleMixTimer = null;
    applyAssembleMix();
  }, 450);
}

function onAssembleChromeClick(e) {
  const t = e.target;
  if (!t || typeof t.closest !== "function") return;
  if (t.closest("#assembleStartBtn")) {
    e.preventDefault();
    startAssemble();
    return;
  }
  if (t.closest("#assembleStopBtn")) {
    e.preventDefault();
    stopAssemble();
    return;
  }
  if (t.closest("#assembleExportBtn")) {
    e.preventDefault();
    exportAssembleFolder();
    return;
  }
  if (t.closest("#assembleMixApply")) {
    e.preventDefault();
    if (state.assembleMixTimer) {
      clearTimeout(state.assembleMixTimer);
      state.assembleMixTimer = null;
    }
    applyAssembleMix();
    return;
  }
  if (t.closest("#assembleMixReset")) {
    e.preventDefault();
    persistAssembleShares(defaultAssembleShares());
    persistAssembleMinFit(ASSEMBLE_DEFAULT_MIN_FIT);
    writeAssembleMixTuners(state.assembleLaneShares);
    writeAssembleMinFit(state.assembleMinFit);
    applyAssembleMix();
    return;
  }
  const sortBtn = t.closest("#assemblePlaylistSort button");
  if (sortBtn) {
    e.preventDefault();
    const mode = sortBtn.getAttribute("data-pl-sort") || "crate";
    state.assemblePlaylistSort = mode;
    try {
      localStorage.setItem("assemblePlaylistSort", mode);
    } catch {
      /* ignore */
    }
    syncAssemblePlaylistSortUi();
    const result = state.assembleJob?.result || state.assemblePreview?.result;
    if (result) renderAssembleLists(result);
  }
}

function bindAssembleUi() {
  if (!document.body.dataset.assembleChromeBound) {
    document.body.dataset.assembleChromeBound = "1";
    document.body.addEventListener("click", onAssembleChromeClick);
  }
  if (!state.assemblePlaylistSort) {
    try {
      state.assemblePlaylistSort = localStorage.getItem("assemblePlaylistSort") || "crate";
    } catch {
      state.assemblePlaylistSort = "crate";
    }
  }
  renderAssembleMixTuners();
  if (state.assembleMinFit == null) state.assembleMinFit = loadAssembleMinFit();
  writeAssembleMinFit(state.assembleMinFit);
  const minFit = $("assembleMinFit");
  const minFitNum = $("assembleMinFitNum");
  const bindMinFit = (el) => {
    if (!el || el.dataset.bound) return;
    el.dataset.bound = "1";
    el.addEventListener("input", () => {
      writeAssembleMinFit(el.value);
      persistAssembleMinFit(el.value);
      persistAssembleShares(readAssembleMixShares());
      scheduleAssembleMixApply();
    });
  };
  bindMinFit(minFit);
  bindMinFit(minFitNum);
  const lanes = $("assembleMixLanes");
  if (lanes && !lanes.dataset.bound) {
    lanes.dataset.bound = "1";
    const onMixEdit = (e) => {
      const el = e.target;
      if (!(el instanceof HTMLInputElement) || !el.classList.contains("assemble-mix-pct")) return;
      persistAssembleShares(readAssembleMixShares());
      persistAssembleMinFit(readAssembleMinFit());
      syncAssembleMixSum();
      if (e.type === "change") scheduleAssembleMixApply();
    };
    lanes.addEventListener("input", onMixEdit);
    lanes.addEventListener("change", onMixEdit);
  }
}

try {
  bindAssembleUi();
} catch {
  document.body.addEventListener("click", onAssembleChromeClick);
}

function stopStemsPoll() {
  if (state.stemAuditTimer) {
    clearInterval(state.stemAuditTimer);
    state.stemAuditTimer = null;
  }
}

function renderStemsRail() {
  const root = $("trackList");
  if (!root) return;
  const inv = state.stemInventory;
  const job = state.stemAuditJob;
  const n = inv && inv.sidecar_count;
  const broken = (job && job.broken_count) || 0;
  root.innerHTML = `<div class="stems-rail">
      <div class="stems-rail-kicker">Sidecars</div>
      <strong>${n == null ? "—" : n}</strong>
      <div class="subtitle">${broken} vocal-layer holes</div>
    </div>`;
}

function selectedStemSidecars() {
  return Array.from(document.querySelectorAll("#stemsBrokenTable input[data-stems-path]:checked"))
    .map((el) => el.getAttribute("data-stems-path") || "")
    .filter(Boolean);
}

function syncStemsDeleteButton() {
  const btn = $("stemsDeleteBtn");
  if (!btn) return;
  const n = selectedStemSidecars().length;
  btn.disabled = n === 0 || MusicSorterStems.stemsJobBusy(state.stemAuditJob);
  btn.textContent = n ? `Delete ${n} sidecar${n === 1 ? "" : "s"}` : "Delete selected sidecars";
}

function renderStemsPanel() {
  const job = state.stemAuditJob;
  const inv = state.stemInventory;
  const hint = $("stemsInventoryHint");
  if (hint) {
    const n = inv && inv.sidecar_count;
    if (n == null) {
      hint.textContent = "Looking up .vdjstems sidecars…";
    } else {
      const fresh = inv.unscanned_count;
      const done = inv.scanned_count;
      const extra =
        fresh == null
          ? ""
          : ` · ${done || 0} already scanned · ${fresh} new`;
      hint.textContent = `${n} .vdjstems sidecars under DJ Music${extra}. Scan new skips unchanged sidecars.`;
    }
  }
  const stats = $("stemsStats");
  if (stats) {
    const checked = job ? job.checked || 0 : 0;
    const broken = job ? job.broken_count || 0 : 0;
    const ok = job ? job.ok_count || 0 : 0;
    const errors = job ? job.error_count || 0 : 0;
    stats.innerHTML = [
      ["Checked", checked, ""],
      ["Broken", broken, broken ? " is-bad" : ""],
      ["OK", ok, ""],
      ["Errors", errors, errors ? " is-bad" : ""],
    ]
      .map(
        ([label, value, cls]) =>
          `<div class="stems-stat${cls}"><strong>${value}</strong><span>${label}</span></div>`
      )
      .join("");
  }
  const busy = MusicSorterStems.stemsJobBusy(job);
  const prog = $("stemsProgress");
  if (prog) prog.hidden = !job;
  const label = $("stemsProgressLabel");
  if (label) label.textContent = job?.message || "Idle";
  const count = $("stemsProgressCount");
  const total = job?.total || 0;
  const checked = job?.checked || 0;
  if (count) count.textContent = total ? `${checked} / ${total}` : "—";
  const fill = $("stemsProgressFill");
  if (fill) {
    const pct = total ? Math.min(100, Math.round((checked / total) * 100)) : busy ? 4 : 0;
    fill.style.width = `${pct}%`;
  }
  const cancelBtn = $("stemsCancelBtn");
  if (cancelBtn) cancelBtn.hidden = !busy;
  const scanAll = $("stemsScanAllBtn");
  const scanCued = $("stemsScanCuedBtn");
  const checkBtn = $("stemsCheckBtn");
  if (scanAll) {
    scanAll.disabled = busy;
    scanAll.textContent = busy ? "Scanning…" : "Scan new";
  }
  if (scanCued) scanCued.disabled = busy;
  if (checkBtn) checkBtn.disabled = busy;
  const rows = [];
  if (state.stemCheckResult) rows.push(state.stemCheckResult);
  const seen = new Set(rows.map((r) => r.stems_path || r.audio_path));
  for (const r of job?.broken || []) {
    const key = r.stems_path || r.audio_path;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(r);
  }
  for (const r of job?.errors || []) {
    const key = r.stems_path || r.audio_path;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(r);
  }
  const table = $("stemsBrokenTable");
  if (table) {
    if (!rows.length) {
      table.innerHTML = `<div class="stems-empty">${
        job && job.status === "ok" ? "No vocal-layer holes in this scan." : "Run a scan to list flagged files."
      }</div>`;
    } else {
      const root = job?.root || inv?.root || "";
      table.innerHTML = `<table>
        <thead>
          <tr>
            <th></th>
            <th>File</th>
            <th>Holes</th>
            <th>Range</th>
          </tr>
        </thead>
        <tbody>
          ${rows
            .map((r) => {
              const sidecar = r.stems_path || `${r.audio_path || ""}.vdjstems`;
              const rel = MusicSorterStems.formatStemRel(r.audio_path || sidecar, root);
              const holes = r.error
                ? escapeHtml(r.error)
                : `${r.hole_seconds || 0}s`;
              const range = r.error ? "—" : MusicSorterStems.formatHoles(r.holes || []);
              const flag = r.broken ? "broken" : r.error ? "error" : "ok";
              return `<tr data-flag="${flag}">
                <td><input type="checkbox" data-stems-path="${escapeHtml(sidecar)}" ${
                  r.broken || r.error ? "" : "disabled"
                } /></td>
                <td class="stems-path">${escapeHtml(rel)}</td>
                <td>${escapeHtml(String(holes))}</td>
                <td class="stems-holes">${escapeHtml(range)}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
      table.querySelectorAll("input[data-stems-path]").forEach((el) => {
        el.addEventListener("change", syncStemsDeleteButton);
      });
    }
  }
  syncStemsDeleteButton();
  const badge = $("countsBadge");
  if (badge && isStemsMode()) {
    const broken = job ? job.broken_count || 0 : 0;
    badge.textContent = job ? `${broken} broken` : inv ? `${inv.sidecar_count} stems` : "Stems";
    badge.className = broken ? "badge warn" : "badge ok";
  }
  if (isStemsMode()) {
    renderTrackList();
    updatePipelineStrip();
  }
}

async function pollStemAuditJob(jobId) {
  if (!jobId) return;
  try {
    const data = await api(`/api/stems/audit/${encodeURIComponent(jobId)}`, { timeoutMs: 8000 });
    if (data.job) {
      state.stemAuditJob = data.job;
      renderStemsPanel();
      if (!MusicSorterStems.stemsJobBusy(data.job)) stopStemsPoll();
    }
  } catch (err) {
    const gone = /not found|404|Unknown stem/i.test(String(err.message || ""));
    if (gone) {
      stopStemsPoll();
      if (state.stemAuditJob && MusicSorterStems.stemsJobBusy(state.stemAuditJob)) {
        state.stemAuditJob.status = "ok";
        state.stemAuditJob.message = "Scan process ended — last results kept.";
        renderStemsPanel();
      }
      return;
    }
    if (isStemsMode()) setStatus(err.message || "Stem scan poll failed", "error");
  }
}

function startStemsPoll(jobId) {
  stopStemsPoll();
  if (!jobId) return;
  state.stemAuditTimer = setInterval(() => {
    pollStemAuditJob(jobId);
  }, 700);
  pollStemAuditJob(jobId);
}

async function loadStemsTab() {
  try {
    const [inv, latest] = await Promise.all([
      api("/api/stems/inventory", { timeoutMs: 20000 }),
      api("/api/stems/audit", { timeoutMs: 8000 }).catch(() => null),
    ]);
    state.stemInventory = inv;
    if (latest && latest.job) state.stemAuditJob = latest.job;
    renderStemsPanel();
    if (
      MusicSorterStems.stemsJobBusy(state.stemAuditJob) &&
      state.stemAuditJob.id &&
      state.stemAuditJob.id !== "saved"
    ) {
      startStemsPoll(state.stemAuditJob.id);
    }
  } catch (err) {
    setStatus(err.message || "Could not load stem inventory", "error");
    renderStemsPanel();
  }
}

async function startStemAudit(scope) {
  try {
    const data = await api("/api/stems/audit", {
      method: "POST",
      body: JSON.stringify({ scope, skip_scanned: true }),
      timeoutMs: 15000,
    });
    state.stemCheckResult = null;
    state.stemAuditJob = data.job;
    renderStemsPanel();
    if (data.job?.id) startStemsPoll(data.job.id);
    setStatus(scope === "cued" ? "Scanning new cued stems…" : "Scanning new stems…");
  } catch (err) {
    setStatus(err.message || "Could not start stem scan", "error");
  }
}

async function cancelStemAudit() {
  const id = state.stemAuditJob?.id;
  if (!id) return;
  try {
    const data = await api(`/api/stems/audit/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      timeoutMs: 8000,
    });
    if (data.job) state.stemAuditJob = data.job;
    renderStemsPanel();
  } catch (err) {
    setStatus(err.message || "Could not cancel", "error");
  }
}

async function checkStemPath() {
  const input = $("stemsCheckPath");
  const path = (input && input.value ? input.value : "").trim();
  if (!path) {
    setStatus("Paste an audio path to check.", "error");
    return;
  }
  try {
    const data = await api("/api/stems/check", {
      method: "POST",
      body: JSON.stringify({ path }),
      timeoutMs: 120000,
    });
    state.stemCheckResult = data.track;
    renderStemsPanel();
    if (data.track?.broken) {
      setStatus(`Broken vocal stem · ${data.track.hole_seconds || 0}s holes`, "error");
    } else if (data.track?.error) {
      setStatus(data.track.error, "error");
    } else {
      setStatus("Vocal stem looks clean on this file.", "ok");
    }
  } catch (err) {
    setStatus(err.message || "Check failed", "error");
  }
}

async function deleteSelectedStemSidecars() {
  const paths = selectedStemSidecars();
  if (!paths.length) return;
  const ok = await showConfirmDialog({
    title: "Delete stem sidecars?",
    message: `Remove ${paths.length} .vdjstems file${paths.length === 1 ? "" : "s"}. Audio stays. VirtualDJ may recreate them on load.`,
    confirmLabel: "Delete sidecars",
    tone: "danger",
  });
  if (!ok) return;
  try {
    const data = await api("/api/stems/delete", {
      method: "POST",
      body: JSON.stringify({ paths }),
      timeoutMs: 20000,
    });
    const gone = new Set(data.paths || []);
    if (state.stemAuditJob?.broken) {
      state.stemAuditJob.broken = state.stemAuditJob.broken.filter(
        (r) => !gone.has(r.stems_path)
      );
      state.stemAuditJob.broken_count = state.stemAuditJob.broken.length;
    }
    if (state.stemCheckResult && gone.has(state.stemCheckResult.stems_path)) {
      state.stemCheckResult = null;
    }
    setStatus(`Deleted ${data.deleted || 0} sidecar${data.deleted === 1 ? "" : "s"}.`, "ok");
    await loadStemsTab();
  } catch (err) {
    setStatus(err.message || "Delete failed", "error");
  }
}

function bindStemsUi() {
  if (document.body.dataset.stemsUiBound) return;
  document.body.dataset.stemsUiBound = "1";
  $("stemsScanAllBtn")?.addEventListener("click", () => startStemAudit("all"));
  $("stemsScanCuedBtn")?.addEventListener("click", () => startStemAudit("cued"));
  $("stemsCancelBtn")?.addEventListener("click", () => cancelStemAudit());
  $("stemsCheckBtn")?.addEventListener("click", () => checkStemPath());
  $("stemsDeleteBtn")?.addEventListener("click", () => deleteSelectedStemSidecars());
  $("stemsCheckPath")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") checkStemPath();
  });
}

try {
  bindStemsUi();
} catch {
  /* stems panel missing in tests that don't load HTML */
}
