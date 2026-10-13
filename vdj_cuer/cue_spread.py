"""Spacing guard for AutoCue: min gap between cues and song-thirds coverage."""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

MIN_GAP_BEATS = 32.0


def enforce_min_gap(cues: Sequence[Dict], bpm: float, min_gap_beats: float = MIN_GAP_BEATS) -> Tuple[List[Dict], List[Dict]]:
    """Keep cues at least min_gap_beats apart (earlier one wins). Returns (kept, dropped)."""
    gap = (60.0 / float(bpm)) * float(min_gap_beats) - 0.05
    kept: List[Dict] = []
    dropped: List[Dict] = []
    for cue in sorted(cues, key=lambda c: float(c.get("timestamp", 0.0))):
        t = float(cue.get("timestamp", 0.0))
        if kept and t - float(kept[-1].get("timestamp", 0.0)) < gap:
            dropped.append(cue)
        else:
            kept.append(cue)
    return kept, dropped


def thirds_coverage(cues: Sequence[Dict], song_length: float) -> List[int]:
    """Number of cues in each third of the song."""
    counts = [0, 0, 0]
    if song_length <= 0:
        return counts
    for cue in cues:
        idx = min(2, int(3 * float(cue.get("timestamp", 0.0)) / song_length))
        counts[max(0, idx)] += 1
    return counts
