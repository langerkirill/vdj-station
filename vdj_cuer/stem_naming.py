"""Kirill 2026-10-04: names follow the stems; a loop and a cue never share a spot.

* A cue that sits on a loop's start is dropped (the loop already jumps there).
* Names come from the marker's own color (= its stems), never a rotating list:
  blue Breakdown/Outro, orange Vocal, yellow Chorus (vocals + drums), green
  Groove/Drop, purple Drums. Repeats get a different descriptive name, not " 2".
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import List, Optional, Sequence, Tuple

CUE_POOL = {
    "blue": ["Breakdown", "Synth Break", "Atmosphere", "Bridge", "Pad Break"],
    "orange": ["Vocal Break", "Vocal", "Vocal Bridge", "Vocal Hook"],
    "yellow": ["Chorus", "Vocal Groove", "Vocal Drop", "Hook"],
    "green": ["Groove", "Synth Groove", "Lift", "Bass Groove", "Peak", "Rise"],
    "purple": ["Drum Groove", "Drums", "Drum Drop", "Beat Break"],
}
LOOP_POOL = {
    "blue": ["Melody Loop", "Synth Loop", "Pad Loop", "Atmosphere Loop"],
    "orange": ["Vocal Loop", "Vocal Hook Loop"],
    "yellow": ["Vocal Groove Loop", "Chorus Loop", "Hook Loop"],
    "green": ["Groove Loop", "Bass Loop", "Lead Loop", "Synth Groove Loop"],
    "purple": ["Drum Loop", "Beat Loop", "Percussion Loop"],
}


def _pick(pool: Sequence[str], used: set, fallback: str) -> str:
    for name in pool:
        if name not in used:
            return name
    return fallback


def drop_cues_under_loops(cues, loops, *, eps: float = 0.25):
    kept = [c for c in cues if not any(abs(c.position - l.position) <= eps for l in loops)]
    return kept, [c for c in cues if c not in kept]


def name_from_stems(
    cues: List,
    loops: List,
    *,
    color_values: dict,
    first_one: Optional[float] = None,
    drop_positions=None,
):
    """Return (cues, loops) renamed from their colors. Position order drives context."""
    used: set = set()
    out_cues = []
    ordered = sorted(cues, key=lambda c: c.position)
    last_idx = len(ordered) - 1
    prev_color: Optional[str] = None
    for i, c in enumerate(ordered):
        col = c.color_name
        if i == 0 and (first_one is None or abs(c.position - first_one) <= 0.5):
            name = {"blue": "Intro", "orange": "Vocal Intro"}.get(col, "Beat Entry")
        elif i == last_idx and col == "blue":
            name = "Outro"
        elif col == "green" and "Drop" not in used and (
            prev_color in ("blue", "orange")
            or (drop_positions and any(abs(c.position - p) <= 0.25 for p in drop_positions))
        ):
            # a green Drop = a new high-energy section after a quieter one (no vocals,
            # else it would be yellow); sections whose drums stop are blue and never get here.
            name = "Drop"
        elif col == "green" and prev_color == "purple" and "Build" not in used:
            name = "Build"
        else:
            name = _pick(CUE_POOL.get(col, CUE_POOL["green"]), used, "Groove " + str(i + 1))
        if name in used:
            name = _pick(CUE_POOL.get(col, CUE_POOL["green"]), used, name + " " + str(i + 1))
        used.add(name)
        prev_color = col
        out_cues.append(c if name == c.name else replace(c, name=name))
    lused: set = set()
    out_loops = []
    for l in sorted(loops, key=lambda x: x.position):
        pool = LOOP_POOL.get(l.color_name, LOOP_POOL["green"])
        name = _pick(pool, lused, l.name)
        lused.add(name)
        out_loops.append(l if name == l.name else replace(l, name=name))
    return out_cues, out_loops


_VOCAL_WORDS = re.compile(r"\b(vocals?|chorus|hook|vox|singer|singing)\b", re.I)


def enforce_vocal_names(cues: List, loops: List):
    """Kirill 2026-10-04: Vocal/Chorus/Hook only on markers the stems judged vocal.

    A marker whose color is not yellow/orange (= the rule found no singer there) may not
    carry those words, whatever Gemini/ML/the pools proposed. It is renamed from its
    own color's pool, keeping names distinct.
    """
    def fix(items, pools, label):
        used = {i.name for i in items}
        out = []
        for it in items:
            if it.color_name in ("yellow", "orange") or not _VOCAL_WORDS.search(it.name or ""):
                out.append(it)
                continue
            pool = pools.get(it.color_name, pools["green"])
            name = _pick(pool, used, label + " " + str(len(out) + 1))
            used.discard(it.name)
            used.add(name)
            out.append(replace(it, name=name))
        return out

    return fix(list(cues), CUE_POOL, "Groove"), fix(list(loops), LOOP_POOL, "Groove Loop")
