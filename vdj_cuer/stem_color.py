"""Deterministic cue color from measured VDJ stem activity.

Colors never come from Gemini when stems exist. The cue's window is measured
per stem (kick/hihat/vocal/bass/instruments, each calibrated to that stem's
own peak by stem_evidence). A stem counts as ON at "medium" or "high"; "low"
is VDJ stem bleed and counts as off.

    vocals + drums               -> yellow
    vocals, no drums             -> orange
    drums + (bass or instruments), no vocals -> green
    drums only                   -> purple
    no drums, no vocals, bass/instruments    -> blue
    everything quiet             -> blue (flagged "quiet")
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional, Tuple

from .stem_evidence import ACTIVITY_RANK

ON_RANK = ACTIVITY_RANK["medium"]


def _on(activity: Mapping[str, str], *stems: str) -> bool:
    return any(ACTIVITY_RANK.get(activity.get(s, "none"), 0) >= ON_RANK for s in stems)


def stem_flags(activity: Mapping[str, str]) -> Dict[str, bool]:
    return {
        "drums": _on(activity, "kick", "hihat"),
        "vocals": _on(activity, "vocal"),
        "melody": _on(activity, "bass", "instruments"),
    }


def color_from_stems(
    activity: Mapping[str, str], band_flags: Optional[Mapping[str, object]] = None
) -> Tuple[str, str]:
    """Return (color_name, reason) from per-stem activity levels.

    ``band_flags`` (stem_evidence.section_band_flags) overrides drums/melody when measured.
    """
    f = stem_flags(activity)
    drums, vocals, melody = f["drums"], f["vocals"], f["melody"]
    if band_flags is not None:
        drums, melody = bool(band_flags["drums"]), bool(band_flags["melody"])
    if vocals and drums:
        return "yellow", "vocals+drums"
    if vocals:
        return "orange", "vocals, no drums"
    if drums and melody:
        return "green", "drums+melody, no vocals"
    if drums:
        return "purple", "drums only"
    if melody:
        return "blue", "melody only, no drums/vocals"
    return "blue", "quiet"


# ---------------------------------------------------------------------------
# Onset rule (Kirill 2026-10-03): color and Drop-naming look at how a section
# OPENS (first 2 bars) and at the step versus the section before it, not at a
# centered average. A section that opens with very little drums is blue
# (breakdown/intro-type) no matter what the drums do later. A cue is only a
# Drop where a NEW high-energy section starts.
# ---------------------------------------------------------------------------
KICK_ON = 0.30       # onset kick score (0..1, relative to the kick stem's peak)
HAT_ON = 0.60        # hihat alone must be this strong to count as drums
DROP_STEP = 0.25     # drum-energy rise versus the previous section


def drum_energy(scores: Mapping[str, float]) -> float:
    return float(scores.get("kick", 0.0)) + 0.5 * float(scores.get("hihat", 0.0))


def classify_onset(
    onset_levels: Mapping[str, str],
    onset_scores: Mapping[str, float],
    prev_scores: Mapping[str, float] | None = None,
    first_bar_scores: Mapping[str, float] | None = None,
    band_flags: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    """Color, reason and Drop eligibility from the section onset + step-up.

    ``first_bar_scores`` (the first 4 beats) decides whether drums are really
    present when the section starts (Kirill 2026-10-03: jumping to a section whose
    first bar has no drums leaves a gap). ``band_flags`` (first-bar kick band +
    instruments voice band, Kirill 2026-10-08) overrides drums/melody when measured.
    """
    kick = float(onset_scores.get("kick", 0.0))
    hat = float(onset_scores.get("hihat", 0.0))
    if first_bar_scores is not None:
        kick = float(first_bar_scores.get("kick", 0.0))
        hat = float(first_bar_scores.get("hihat", 0.0))
    drums = kick >= KICK_ON or hat >= HAT_ON
    f = stem_flags(onset_levels)
    vocals, melody = f["vocals"], f["melody"]
    if band_flags is not None:
        drums, melody = bool(band_flags["drums"]), bool(band_flags["melody"])
    if not drums and vocals:
        color, why = "orange", "vocals at the onset, no drums"
    elif not drums:
        color, why = "blue", "no drums in the first bar (breakdown/intro-type)"
    elif vocals:
        color, why = "yellow", "onset drums+vocals"
    elif melody:
        color, why = "green", "onset drums+melody, no vocals"
    else:
        color, why = "purple", "onset drums only"
    step = drum_energy(onset_scores) - (
        drum_energy(prev_scores) if prev_scores is not None else 0.0
    )
    drop_ok = bool(drums and kick >= KICK_ON
                   and (prev_scores is None or step >= DROP_STEP))
    return {"color": color, "reason": why, "drums": drums, "step": round(step, 3),
            "drop_ok": drop_ok}


# ---------------------------------------------------------------------------
# R-10 (2026-10-12): a "medium" vocal is not a sung vocal by itself.
# The vocal level is promoted to medium when ~20% of the voice band clears 20% of the
# stem's p95, and cues look 4x ahead (32 beats), so backing/bleed or a line that only
# starts bars later painted sections yellow. Kirill's Oct 10 hand fixes (059 @39.96 cue,
# 058 Chorus Loop @31.48, 060 Chorus Loop @421.46 -> non-vocal) all have almost no sung
# frames in the first 2 bars, while the yellows he kept (058 @7.87, 060 @101.47/130.73/
# 193.17/343.41) do. Measured on the vocal stem's 20 ms voice band (cue_hygiene uses it).
# ---------------------------------------------------------------------------
VOCAL_OPEN_BEATS = 8           # judge the section's first 2 bars (onset rule)
VOCAL_SUNG_REL = 0.40          # a sung frame: >= 40% of the stem's own fine p95
CUE_VOCAL_OPEN_MIN = 0.05      # a cue: some real singing in its first 2 bars
LOOP_VOCAL_OPEN_MIN = 0.32     # a loop repeats its opening: the vocal must carry it
# (1.2.0: 0.35 -> 0.32; Kirill kept vocal loops at 0.33-0.349, recolored 058 Chorus Loop at 0.310)
# 1.2.0 retune (TL 2026-10-12, Kirill's kept yellows as answer key): a quieter line also
# counts for a cue. BUNT. 'i need u' Drop @75.02 is 0.04 at 40% but 0.12 at 20% of p95;
# every non-vocal fix (059 @39.96, Dreams 0.30/190.71) is <= 0.01 at 20%.
CUE_VOCAL_SOFT_REL = 0.20
CUE_VOCAL_SOFT_MIN = 0.10


def vocal_opening_share(vocal_profile, start: float, beat: float,
                        beats: int = VOCAL_OPEN_BEATS, rel: float = VOCAL_SUNG_REL) -> Optional[float]:
    """Share of sung (voice band >= 40% p95) 20 ms frames in the first ``beats`` beats."""
    if vocal_profile is None or not beat:
        return None
    fine = getattr(vocal_profile, "voice_fine_frames", None)
    ref = float(getattr(vocal_profile, "voice_fine_reference", 0.0) or 0.0)
    if not fine or ref <= 0:
        return None
    from .stem_evidence import POST_BOUNDARY_OFFSET_SECONDS, VOICE_FINE_HOP_SECONDS

    hop = VOICE_FINE_HOP_SECONDS
    a = max(0, int((float(start) + POST_BOUNDARY_OFFSET_SECONDS) / hop))
    b = max(a + 1, int((float(start) + POST_BOUNDARY_OFFSET_SECONDS + beats * float(beat)) / hop))
    w = fine[a:b]
    if not w:
        return None
    return sum(v >= rel * ref for v in w) / len(w)


def gate_opening_vocal(activity: Mapping[str, str], vocal_profile, start: float,
                       beat: float, kind: str = "cue") -> Tuple[Dict[str, str], str]:
    """Downgrade a medium/high vocal to "low" when the opening has no real singing.

    Returns (activity, reason). Without fine voice-band data the activity is unchanged.
    """
    act = dict(activity)
    if ACTIVITY_RANK.get(act.get("vocal", "none"), 0) < ON_RANK:
        return act, ""
    share = vocal_opening_share(vocal_profile, start, beat)
    if share is None:
        return act, ""
    need = LOOP_VOCAL_OPEN_MIN if kind == "loop" else CUE_VOCAL_OPEN_MIN
    if share < need and kind != "loop":
        soft = vocal_opening_share(vocal_profile, start, beat, rel=CUE_VOCAL_SOFT_REL)
        if soft is not None and soft >= CUE_VOCAL_SOFT_MIN:
            return act, ""
    if share < need:
        act["vocal"] = "low"
        return act, f"vocal {activity.get('vocal')} but only {share:.2f} sung in the first 2 bars (<{need:.2f}): not a vocal section"
    return act, ""
