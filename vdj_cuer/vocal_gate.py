"""Track-level "this song has no vocals" gate (Kirill 2026-10-04).

If the analysis (Gemini / ML) or a note says the track is an instrumental, the VDJ vocal
stem is treated as pure separator bleed: no marker may be yellow/orange and no cue or loop
name may say Vocal/Chorus/Hook. Sources, in order:

* ``analysis_data["has_vocals"] is False`` / ``["instrumental"] is True`` (also under
  ``song_structure`` and ``track``), or ``vocals`` given as "none"/"no"/"false".
* An override file ``~/Music/DJ/Notes/vocal-overrides.json`` (or ``$AUTOCUE_VOCAL_OVERRIDES``):
  ``{"instrumental": ["010", "Bliss"], "vocals": ["029"]}`` -- entries match the file's
  leading track number ("010") or any substring of its name. "vocals" wins (it exists for
  songs the stem floor calls instrumental but that do have a singer, e.g. Dreams).
* The audio file name: "(Inst)", "Instrumental" or " Inst" as a whole word, case-insensitive
  (e.g. "Bust (Inst) - 98 BM for AutoCue.wav"). An override "vocals" entry still wins.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .stem_evidence import StemProfile

_FALSE_WORDS = {"none", "no", "false", "0", "instrumental", "n"}


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _FALSE_WORDS:
            return False
        if v in {"yes", "true", "1", "y", "vocals", "vocal"}:
            return True
    return None


def analysis_says_no_vocals(analysis: Any) -> Optional[bool]:
    """True/False when the analysis states it, None when it says nothing."""
    if not isinstance(analysis, dict):
        return None
    scopes = [analysis]
    for key in ("song_structure", "track", "song"):
        sub = analysis.get(key)
        if isinstance(sub, dict):
            scopes.append(sub)
    for scope in scopes:
        if "instrumental" in scope:
            flag = _as_bool(scope["instrumental"])
            if flag is not None and not isinstance(scope["instrumental"], str):
                return flag
            if scope["instrumental"] == "instrumental":
                return True
        for key in ("has_vocals", "vocals", "vocals_present"):
            if key in scope and not isinstance(scope[key], (list, dict)):
                flag = _as_bool(scope[key])
                if flag is not None:
                    return not flag
    return None


def _override_path() -> Path:
    env = os.environ.get("AUTOCUE_VOCAL_OVERRIDES")
    return Path(env).expanduser() if env else Path.home() / "Music" / "DJ" / "Notes" / "vocal-overrides.json"


def _matches(entries: Any, audio_path: str) -> bool:
    name = os.path.basename(audio_path or "")
    m = re.match(r"\s*(\d+)\.", name)
    number = m.group(1) if m else None
    for raw in entries or []:
        token = str(raw).strip()
        if not token:
            continue
        if number is not None and token == number:
            return True
        if token.lower() in name.lower():
            return True
    return False


_INST_NAME_RE = re.compile(r"(?<![A-Za-z0-9])(?:inst|instrumental)(?![A-Za-z0-9])", re.IGNORECASE)


def name_says_instrumental(audio_path: str) -> bool:
    """File name carries '(Inst)', 'Instrumental' or ' Inst' as a whole word (case-insensitive)."""
    stem = os.path.splitext(os.path.basename(audio_path or ""))[0]
    return bool(_INST_NAME_RE.search(stem))


def override_says(audio_path: str) -> Optional[str]:
    """'instrumental', 'vocals' or None from the override file."""
    path = _override_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if _matches(data.get("vocals"), audio_path):
        return "vocals"
    if _matches(data.get("instrumental"), audio_path):
        return "instrumental"
    return None


def track_is_instrumental(analysis: Any, audio_path: str = "") -> bool:
    """Hard gate: True means the stem vocal must be ignored for this song."""
    note = override_says(audio_path)
    if note == "vocals":
        return False
    if note == "instrumental":
        return True
    if name_says_instrumental(audio_path):
        return True
    return analysis_says_no_vocals(analysis) is True


def apply_track_vocal_gate(
    profiles: Dict[str, StemProfile], analysis: Any, audio_path: str = ""
) -> Dict[str, StemProfile]:
    """Return profiles with the vocal stem silenced when the track has no vocals."""
    vocal = profiles.get("vocal")
    if vocal is None or not track_is_instrumental(analysis, audio_path):
        return profiles
    silent = StemProfile.from_frames([0.0] * max(1, len(vocal.frames)), vocal.frame_seconds)
    return {**profiles, "vocal": silent}
