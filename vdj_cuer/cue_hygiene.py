"""Cue/loop hygiene rules (Kirill 2026-10-04, round 2).

Pure helpers; ``cue_writer.prepare_song_cues`` and ``stems`` call them.

1. No cue on a half-entered vocal: if the vocal stem is already sounding at the cue
   (or was sung <2 s before it, i.e. a breath inside a phrase) the cue is MOVED to the
   nearest earlier bar [1] within ~2 bars where the vocal is quiet. If no usable break
   is within ~2 bars, SKIP/DROP the new proposal (return None). Hand-kept cues on songs
   Kirill already edited are protected by not rewriting those songs + deleted_markers,
   not by keeping bad new proposals.
2. No cue so near the end that a jump leaves almost no song.
3. Loops: 16 beats minimum (32 preferred, 64 allowed), starting on the phrase [1] of the
   cue they belong to; a loop that sits 1-2 phrases after its section cue is "late".
4. Never two cues at one spot (keep the better-colored one).
5. A "Chorus" at the very end of the song is an Outro/Ending.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

PHRASE_BEATS = 16
MIN_LOOP_BEATS = 16
LOOP_LENGTHS = (64, 32, 16)

# --- 1. half-entered vocal ---------------------------------------------------
# Levels are fractions of the vocal stem's own p95. Calibrated on today's written songs:
# cues Kirill kept on clean onsets (127 @43.3 / @153.5, 131 @48.1, 136 @67.7, 140 @136.4,
# 129 @93.1) never have the vocal above 0.4 in the half second before the cue, while the
# cue he deleted mid-phrase (129 @23.4) has it sung straight through.
MID_WORD_LOUD = 0.40
MID_WORD_NOW = 0.5               # seconds before / after the cue that must both be loud
BREATH_BEFORE = (2.0, 0.75)      # loud singing earlier ...
BREATH_AFTER = 1.0               # ... and again right after the cue => cue sits on a breath
QUIET_REL = 0.15                 # "quiet" = mean below this fraction of p95
QUIET_BEFORE, QUIET_AFTER = 0.5, 1.0
BAR_BEATS = 4
# Kirill/TL 2026-10-05 03:37: only relocate within ~2 bars; never drop for half-entered.
MAX_BACK_BARS = 2
# Legacy alias kept for any callers still passing max_back as phrase count.
MAX_BACK_PHRASES = 4

# --- 2. end of song -----------------------------------------------------------
END_ZONE_SECONDS = 20.0
END_ZONE_BEATS = 32.0            # last 8 bars
OUTRO_MIN_REMAINING = 15.0

# --- 3. loops ------------------------------------------------------------------
LATE_PHRASES = (1, 2)
LATE_TOL_BEATS = 0.35
ANCHOR_EPS = 0.25


def _frames(profile, start: float, dur: float) -> Sequence[float]:
    end = start + dur
    start = max(0.0, start)
    if end <= start:
        return ()
    return profile._window(start, max(profile.frame_seconds, end - start))


def _mean_rel(profile, start: float, dur: float) -> float:
    w = _frames(profile, start, dur)
    if not w or profile.reference_peak <= 0:
        return 0.0
    return (sum(w) / len(w)) / profile.reference_peak


def vocal_is_mid_word(profile, t: float) -> bool:
    """True when loud singing runs through the cue time (not a clean onset after a gap).

    * the half second before AND the half second after the cue are both loud, or
    * loud singing <2 s before, a gap shorter than 0.75 s (a breath), singing again
      within a second after the cue.
    """
    if profile is None or profile.reference_peak <= 0:
        return False
    t = max(0.0, float(t))
    after = _mean_rel(profile, t, MID_WORD_NOW)
    if after < MID_WORD_LOUD:
        # not singing at the cue; a breath mark still counts if singing resumes <1 s later
        if _mean_rel(profile, t, BREATH_AFTER) < MID_WORD_LOUD:
            return False
    if _mean_rel(profile, t - MID_WORD_NOW, MID_WORD_NOW) >= MID_WORD_LOUD and after >= MID_WORD_LOUD:
        return True
    a, b = BREATH_BEFORE
    return (
        _mean_rel(profile, t - a, a - b) >= MID_WORD_LOUD
        and _mean_rel(profile, t, BREATH_AFTER) >= MID_WORD_LOUD
    )


def vocal_is_quiet_at(profile, t: float) -> bool:
    if profile is None or profile.reference_peak <= 0:
        return True
    return _mean_rel(profile, t - QUIET_BEFORE, QUIET_BEFORE + QUIET_AFTER) < QUIET_REL


def clean_break_before(
    profile,
    t: float,
    beat: float,
    *,
    origin: float = 0.0,
    max_back_bars: int = MAX_BACK_BARS,
) -> Optional[float]:
    """Nearest earlier bar [1] within ``max_back_bars`` (~2) where the vocal is quiet.

    TL 2026-10-05: prefer moving over dropping; search is capped at ~2 bars so we do
    not yank a cue several phrases earlier. Candidates are t - k*4 beats for k=1..N.
    """
    step = BAR_BEATS * beat
    if step <= 0:
        return None
    for k in range(1, max_back_bars + 1):
        cand = t - k * step
        if cand < origin - 0.02 or cand < 0:
            break
        if vocal_is_quiet_at(profile, cand) and not vocal_is_mid_word(profile, cand):
            return cand
    return None


def resolve_half_entered(
    profile, t: float, beat: float, *, origin: float = 0.0
) -> Tuple[Optional[float], str]:
    """(new_time or None to skip, reason). Mid-word: move within ~2 bars, else DROP.

    Hand-kept cues (e.g. 132 Vocal Intro / Drop) are protected by not rewriting
    hand-edited songs and by deleted_markers — not by keeping a bad new proposal.
    """
    if not vocal_is_mid_word(profile, t):
        return t, ""
    alt = clean_break_before(profile, t, beat, origin=origin)
    if alt is None:
        return None, "vocal already sounding; no clean break within ~2 bars — skipped"
    return alt, f"vocal already sounding; moved to the clean break {t - alt:.2f}s earlier"


# --- 2 ------------------------------------------------------------------------------
def end_zone_seconds(beat: float) -> float:
    return max(END_ZONE_SECONDS, END_ZONE_BEATS * beat)


def drop_late_cues(cues: List, song_length: Optional[float], beat: float) -> Tuple[List, List]:
    """Drop cues in the last ~20 s / 8 bars unless the genuine blue Outro with >=15 s left."""
    if not song_length or song_length <= 0:
        return list(cues), []
    zone = end_zone_seconds(beat)
    ordered = sorted(cues, key=lambda c: c.position)
    kept, dropped = [], []
    for i, c in enumerate(ordered):
        remaining = song_length - c.position
        if remaining >= zone:
            kept.append(c)
            continue
        is_outro = c.color_name == "blue" and i == len(ordered) - 1 and remaining >= OUTRO_MIN_REMAINING
        (kept if is_outro else dropped).append(c)
    return kept, dropped


# --- 4 ------------------------------------------------------------------------------
_RANK = {"yellow": 4, "orange": 3, "green": 2, "purple": 1, "blue": 0}


def dedupe_cues(cues: List, *, eps: float = 0.25, prefer: Optional[Callable] = None) -> Tuple[List, List]:
    """One cue per spot: within ``eps`` seconds keep the better-colored (then earlier-listed)."""
    key = prefer or (lambda c: _RANK.get(c.color_name, 0))
    kept: List = []
    dropped: List = []
    for c in sorted(cues, key=lambda c: c.position):
        if kept and abs(c.position - kept[-1].position) <= eps:
            if key(c) > key(kept[-1]):
                dropped.append(kept[-1])
                kept[-1] = c
            else:
                dropped.append(c)
        else:
            kept.append(c)
    return kept, dropped


# --- 3 ------------------------------------------------------------------------------
def governing_cue(cue_positions: Sequence[float], loop_start: float) -> Optional[float]:
    """The cue that opens the section the loop sits in (latest cue at/before the loop)."""
    before = [p for p in cue_positions if p <= loop_start + ANCHOR_EPS]
    return max(before) if before else None


def late_by_phrases(
    cue_positions: Sequence[float], loop_start: float, beat: float
) -> Optional[Tuple[float, int]]:
    """(cue_position, k) when the loop starts k (1-2) whole phrases after its section cue.

    None if the loop is already on a cue, or is not an exact phrase multiple after one.
    """
    cue = governing_cue(cue_positions, loop_start)
    if cue is None or abs(loop_start - cue) <= ANCHOR_EPS or beat <= 0:
        return None
    beats = (loop_start - cue) / beat
    for k in LATE_PHRASES:
        if abs(beats - k * PHRASE_BEATS) <= LATE_TOL_BEATS:
            return cue, k
    return None


def fit_loop_beats(
    start: float, beats: int, beat: float, cue_positions: Sequence[float], song_length: Optional[float]
) -> Optional[int]:
    """Largest allowed length <= ``beats`` (>=16) that ends before the next cue / song end."""
    later = [p for p in cue_positions if p > start + 0.5 * beat]
    limit = min(later) if later else None
    if song_length:
        limit = min(limit, song_length) if limit is not None else song_length
    for b in LOOP_LENGTHS:
        if b > beats:
            continue
        if limit is None or start + b * beat <= limit + beat * 0.1:
            return b
    return None


def anchor_late_loop(
    loop_start: float,
    beats: int,
    beat: float,
    cue_positions: Sequence[float],
    song_length: Optional[float],
    seam_ok: Optional[Callable[[float, int], bool]] = None,
) -> Optional[Tuple[float, int, int]]:
    """Move a late loop onto its section cue. Returns (start, beats, k) or None.

    ``seam_ok(start, beats)`` re-tests the wrap at the new spot (the old pass does not
    carry over); lengths 32/16 are tried at the cue before giving up.
    """
    late = late_by_phrases(cue_positions, loop_start, beat)
    if late is None:
        return None
    cue, k = late
    first = fit_loop_beats(cue, beats, beat, cue_positions, song_length)
    if first is None:
        return None
    for b in LOOP_LENGTHS:
        if b > first:
            continue
        if fit_loop_beats(cue, b, beat, cue_positions, song_length) != b:
            continue
        if seam_ok is None or seam_ok(cue, b):
            return cue, b, k
    return None


# --- 5 ------------------------------------------------------------------------------
def rename_final_chorus(cues: List, song_length: Optional[float], beat: float) -> List:
    """A 'Chorus' at the very end of the song is an Outro (blue) or Ending."""
    if not cues or not song_length:
        return list(cues)
    ordered = sorted(cues, key=lambda c: c.position)
    last = ordered[-1]
    if "chorus" not in (last.name or "").lower():
        return list(cues)
    if song_length - last.position > max(40.0, 64 * beat):
        return list(cues)
    new = "Outro" if last.color_name == "blue" else "Ending"
    used = {c.name for c in ordered}
    if new in used:
        new = "Ending" if "Ending" not in used else "Outro 2"
    return [replace(c, name=new) if c is last else c for c in cues]
