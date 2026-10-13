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

from .stem_evidence import VOICE_FINE_HOP_SECONDS

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

# Kirill 2026-10-08 ("voices coming in"): a sung line that STARTS within the last beat before
# the cue (a pickup into the downbeat) and is still sounding at the press is clipped by a jump.
# Measured on the vocal stem's 20 ms voice-band envelope: on Kirill's cue deletes/moves vs kept
# cues this flagged 7/34 rejected and 0/25 kept. A line already running for more than a beat is
# left to the mid-word rule; a line starting within 60 ms of the press is a clean entry.
PICKUP_ON_REL = 0.20             # voiced frame = >= 20% of the stem's own fine p95
PICKUP_GAP_SECONDS = 0.15        # a breath/gap this long separates two lines
PICKUP_MIN_SECONDS = 0.06
PICKUP_MAX_SECONDS = (0.25, 0.60)  # one beat, clamped
PICKUP_LOOKBACK_SECONDS = 2.0

# Kirill 2026-10-08, California Dreaming: "all the cues landed on vocals that were interrupted".
# vocal_is_mid_word reads the 800 Hz envelope, which reads higher voices as quiet (it caught 2 of
# those 6 cues). On the voice band (250-4000 Hz, 20 ms) a cue is SUNG THROUGH when the vocal keeps
# going for the first beat after the press and no fresh, louder line starts on the press (a line
# entering on the downbeat is a clean entry, R-90). Bench (bench/cloud/proposals/
# voiceband-midword, LOSO, two independent verifiers): Kirill's deleted/moved cues flagged 18/58
# vs 12/58, kept cues flagged 3/65 vs 9/65, California Dreaming 6/6 vs 2/6. ENTRY_RATIO is the top
# of the tied plateau (0.65-0.85 give the same bench result; 0.85 flags fewer kept cues off it).
SUNG_POST_BEATS = 1.0            # "the line continues": mean over this many beats after the press
SUNG_POST_LOUD = 0.55            # ... at least this fraction of the stem's own fine p95
SUNG_PRE_PEAK = (0.5, 0.04)      # loudest syllable from t-0.5 s to t-0.04 s ...
SUNG_POST_PEAK = 0.3             # ... vs the loudest in the first 0.3 s after the press
SUNG_ENTRY_RATIO = 0.85          # pre/post below this = a fresh, louder entry on the press (clean)
FRESH_ENTRY_RISE = 0.5           # a landing on a line entry: reaches 0.5 of p95 ...
FRESH_ENTRY_WITHIN = 0.15        # ... within 150 ms of the press, quiet just before it
LANDING_PRESS_QUIET = 0.20       # a landing spot: voice band mean over +-0.25 s below this ...
LANDING_BREATH_LOUD = 0.40       # ... and not loud singing within a beat on BOTH sides (R-89)

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


def _fine(profile):
    fine = getattr(profile, "voice_fine_frames", None)
    ref = float(getattr(profile, "voice_fine_reference", 0.0) or 0.0)
    return (fine, ref) if fine and ref > 0 else (None, 0.0)


def vocal_line_starts_before(profile, t: float, beat: float) -> bool:
    """True when a sung line starts in the last beat before ``t`` and still sounds at ``t``."""
    fine, ref = _fine(profile)
    if fine is None:
        return False
    hop = VOICE_FINE_HOP_SECONDS
    floor = PICKUP_ON_REL * ref
    i_t = int(round(max(0.0, float(t)) / hop))
    press = fine[max(0, i_t - 3): i_t + 3]
    if not press or sum(v >= floor for v in press) / len(press) <= 0.5:
        return False
    gap_frames = int(round(PICKUP_GAP_SECONDS / hop))
    lo = max(0, i_t - int(PICKUP_LOOKBACK_SECONDS / hop))
    i, silent, start_i = i_t, 0, i_t
    while i > lo:
        i -= 1
        if fine[i] >= floor:
            silent, start_i = 0, i
            continue
        silent += 1
        if silent >= gap_frames:
            break
    else:
        return False  # no gap in the lookback: the line is already running (mid-word rule)
    since = (i_t - start_i) * hop
    longest = min(PICKUP_MAX_SECONDS[1], max(PICKUP_MAX_SECONDS[0], float(beat)))
    return PICKUP_MIN_SECONDS <= since <= longest


def _fine_span(fine, a: float, b: float) -> Sequence[float]:
    hop = VOICE_FINE_HOP_SECONDS
    i = max(0, int(round(a / hop)))
    j = max(i + 1, int(round(b / hop)))
    return fine[i:j]


def vocal_sung_through(profile, t: float, beat: float) -> bool:
    """True when the vocal keeps going through the press and no fresh line starts on it."""
    fine, ref = _fine(profile)
    if fine is None or not beat:
        return False
    t = max(0.0, float(t))
    post = _fine_span(fine, t, t + SUNG_POST_BEATS * float(beat))
    if not post or (sum(post) / len(post)) / ref < SUNG_POST_LOUD:
        return False
    a, b = SUNG_PRE_PEAK
    pre = _fine_span(fine, t - a, t - b) if t - b > 0 else ()
    post_pk = _fine_span(fine, t, t + SUNG_POST_PEAK)
    pre_v = max(pre) if pre else 0.0
    post_v = max(post_pk) if post_pk else 0.0
    return pre_v / max(post_v, 1e-9) >= SUNG_ENTRY_RATIO


def _fresh_entry_at(profile, t: float) -> bool:
    """A sung line starts on the press (quiet just before, loud within 150 ms): clean (R-90)."""
    fine, ref = _fine(profile)
    if fine is None:
        return False
    before = _fine_span(fine, t - 0.25, t - PICKUP_MIN_SECONDS)
    after = _fine_span(fine, t, t + FRESH_ENTRY_WITHIN)
    if not before or not after:
        return False
    return (sum(before) / len(before)) / ref < QUIET_REL and max(after) / ref >= FRESH_ENTRY_RISE


def vocal_is_quiet_at(profile, t: float) -> bool:
    if profile is None or profile.reference_peak <= 0:
        quiet = True
    else:
        quiet = _mean_rel(profile, t - QUIET_BEFORE, QUIET_BEFORE + QUIET_AFTER) < QUIET_REL
    fine, ref = _fine(profile)
    if quiet and fine is not None:
        # The low-band envelope misses higher voices; the voice band must be quiet too.
        hop = VOICE_FINE_HOP_SECONDS
        a = max(0, int((t - QUIET_BEFORE) / hop))
        w = fine[a: max(a + 1, int((t + QUIET_AFTER) / hop))]
        quiet = not w or (sum(w) / len(w)) / ref < QUIET_REL
    return quiet


def _mean(xs) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _landing_ok(profile, t: float, beat: float) -> bool:
    """A spot a flagged cue may move to: no vocal problem there, and the voice band is quiet at
    the press (a gap, or a line that has ended) or a line starts on it; a breath is not a gap."""
    if _entry_problem(profile, t, beat):
        return False
    fine, ref = _fine(profile)
    if fine is None:
        return vocal_is_quiet_at(profile, t)
    if _fresh_entry_at(profile, t):
        return True
    if _mean(_fine_span(fine, t - 0.25, t + 0.25)) / ref >= LANDING_PRESS_QUIET:
        return False
    pre = _mean(_fine_span(fine, t - beat, t)) / ref
    post = _mean(_fine_span(fine, t, t + beat)) / ref
    return not (pre >= LANDING_BREATH_LOUD and post >= LANDING_BREATH_LOUD)


def clean_break_before(
    profile,
    t: float,
    beat: float,
    *,
    origin: float = 0.0,
    max_back_bars: int = MAX_BACK_BARS,
) -> Optional[float]:
    """Nearest earlier bar [1] within ``max_back_bars`` (~2) where the vocal is quiet."""
    step = BAR_BEATS * beat
    if step <= 0:
        return None
    for k in range(1, max_back_bars + 1):
        cand = t - k * step
        if cand < origin - 0.02 or cand < 0:
            break
        if _landing_ok(profile, cand, beat):
            return cand
    return None


def clean_break_near(
    profile,
    t: float,
    beat: float,
    *,
    origin: float = 0.0,
    max_bars: int = MAX_BACK_BARS,
) -> Optional[float]:
    """Nearest bar [1] within ``max_bars`` (~2) either side where a jump is clean.

    R-88/R-89: "move to a quiet break within ~2 bars". Nearer first, earlier first at the
    same distance: -1 bar, +1 bar, -2 bars, +2 bars. Clean = no vocal problem at the press and
    the vocal is quiet there or a line starts on it (a breath inside a line is not quiet).
    """
    step = BAR_BEATS * beat
    if step <= 0:
        return None
    for k in range(1, max_bars + 1):
        # R-88 (2026-10-12): earlier only. Kirill reversed both live +1-bar moves (Besomage
        # 30.944->32.819, How Deep 110.168->112.135): a later bar is off his phrase.
        for cand in (t - k * step,):
            if cand < max(0.0, origin) - 1e-6:
                continue
            if _landing_ok(profile, cand, beat):
                return cand
    return None


# R-88 (2026-10-12): Kirill reversed both live moves of the vocal rule. Besomage @30.944 was
# moved +1 bar to 32.819 (he dragged it back to 30.94); How Deep @110.168 was moved +1 bar to
# a silent spot 112.135 (he tried 108.20, then deleted it). Moves are therefore earlier-only
# (see clean_break_near); with no clean earlier bar the NEW proposal is skipped. Kirill's own
# markers (his Chorus @30.94) are never touched: the writer guard (marker_guard) keeps them.
# A "louder line lands on the press" exemption was tried and rejected: it un-flagged 2 Besomage
# cues Kirill had deleted on 10-08 (bench/labels/entry.json, bad_flagged 18 -> 16).


def _entry_problem(profile, t: float, beat: float) -> str:
    fine, _ref = _fine(profile)
    if fine is not None:
        # Voice band available: pickup (line coming in just before) or sung through.
        if vocal_line_starts_before(profile, t, beat):
            return "vocal line coming in just before the cue"
        if vocal_sung_through(profile, t, beat):
            return "vocal sung through the cue"
        return ""
    if vocal_is_mid_word(profile, t):
        return "vocal already sounding"
    if vocal_line_starts_before(profile, t, beat):
        return "vocal line coming in just before the cue"
    return ""


def resolve_half_entered(
    profile, t: float, beat: float, *, origin: float = 0.0
) -> Tuple[Optional[float], str]:
    """(new_time or None to skip, reason). A vocal problem at the press: move to the nearest
    clean bar within ~2 bars either side, else DROP the new proposal (R-88/R-89).

    Hand-kept cues (e.g. 132 Vocal Intro / Drop) are protected by not rewriting
    hand-edited songs and by deleted_markers — not by keeping a bad new proposal.
    """
    why = _entry_problem(profile, t, beat)
    if not why:
        return t, ""
    alt = clean_break_near(profile, t, beat, origin=origin)
    if alt is None:
        return None, f"{why}; no clean break within ~2 bars — skipped"
    side = "earlier" if alt < t else "later"
    return alt, f"{why}; moved to the clean break {abs(t - alt):.2f}s {side}"


MIN_CUE_GAP_BEATS = 32  # R-08


def drop_crowded_moved_cues(
    cues: List, moved_positions, beat: float, min_gap_beats: float = MIN_CUE_GAP_BEATS
) -> Tuple[List, List]:
    """R-08 after the vocal rule moved cues: a moved cue closer than ``min_gap_beats`` to any
    other cue is dropped (the unmoved cue keeps its place). Returns (kept, dropped)."""
    if not beat or not moved_positions:
        return list(cues), []
    gap = float(min_gap_beats) * float(beat) - 0.05
    moved = {round(float(p), 6) for p in moved_positions}
    kept: List = []
    dropped: List = []
    for c in cues:
        if round(float(c.position), 6) in moved and any(
            o is not c and abs(float(o.position) - float(c.position)) < gap for o in cues if o not in dropped
        ):
            dropped.append(c)
        else:
            kept.append(c)
    return kept, dropped


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


SEAM_RETEST_TOL_SECONDS = 0.002


def final_loop_seam_ok(
    tested_start: float,
    tested_beats: int,
    final_start: float,
    final_beats: int,
    retest: Callable[[float, int], bool],
) -> Tuple[bool, str]:
    """Kirill 2026-10-08: the wrap must pass where the loop is WRITTEN.

    The seam gate runs on the candidate; a later grid snap or a shorter fit before the
    song end changes what the wrap sounds like, so any change re-runs ``retest``.
    """
    moved = abs(float(final_start) - float(tested_start)) > SEAM_RETEST_TOL_SECONDS
    resized = int(final_beats) != int(tested_beats)
    if not (moved or resized):
        return True, ""
    if retest(float(final_start), int(final_beats)):
        return True, f"seam re-tested at {final_start:.3f}s x{int(final_beats)}"
    return False, (
        f"seam passed at {tested_start:.3f}s x{int(tested_beats)} but fails where it would be "
        f"written ({final_start:.3f}s x{int(final_beats)})"
    )


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
