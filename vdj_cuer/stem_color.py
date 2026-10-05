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

from typing import Dict, Mapping, Tuple

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


def color_from_stems(activity: Mapping[str, str]) -> Tuple[str, str]:
    """Return (color_name, reason) from per-stem activity levels."""
    f = stem_flags(activity)
    drums, vocals, melody = f["drums"], f["vocals"], f["melody"]
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
) -> Dict[str, object]:
    """Color, reason and Drop eligibility from the section onset + step-up.

    ``first_bar_scores`` (the first 4 beats) decides whether drums are really
    present when the section starts (Kirill 2026-10-03: jumping to a section whose
    first bar has no drums leaves a gap).
    """
    kick = float(onset_scores.get("kick", 0.0))
    hat = float(onset_scores.get("hihat", 0.0))
    if first_bar_scores is not None:
        kick = float(first_bar_scores.get("kick", 0.0))
        hat = float(first_bar_scores.get("hihat", 0.0))
    drums = kick >= KICK_ON or hat >= HAT_ON
    f = stem_flags(onset_levels)
    vocals, melody = f["vocals"], f["melody"]
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
