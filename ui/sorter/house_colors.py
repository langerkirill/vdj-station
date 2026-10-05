"""Song-level color per House subfolder (HOUSE FORK).

Reads ``<notes dir>/house_folder_colors.json``::

    {
      "Bassy":   {"name": "Orange", "hex": "#FF8800", "vdj_value": "4294934272"},
      "Vocal":   {"name": "Blue",   "hex": "#0000FF", "vdj_value": 4278190335},
      "default": {"name": "...", "hex": "...", "vdj_value": "..."}   # new folders
    }

``vdj_value`` is VirtualDJ's Infos ``UserColor`` ARGB integer (as text or int).
The default may be keyed ``default`` / ``_default`` / ``__default__``. A missing or
unreadable file means "no automatic color" - nothing is guessed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from . import config

FILE_NAME = "house_folder_colors.json"
DEFAULT_KEYS = ("default", "_default", "__default__")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def colors_path() -> Path:
    return Path(config.DJ_NOTES_ROOT) / FILE_NAME


def _entry(raw: Any) -> Optional[dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    val = raw.get("vdj_value")
    try:
        vdj = str(int(str(val).strip()))
    except (TypeError, ValueError):
        return None
    hexv = str(raw.get("hex") or "").strip()
    if not _HEX_RE.match(hexv):
        # derive RGB from the ARGB int so the UI always matches VDJ
        hexv = "#{:06X}".format(int(vdj) & 0xFFFFFF)
    return {"name": str(raw.get("name") or ""), "hex": hexv.upper(), "vdj_value": vdj}


def load() -> dict[str, Any]:
    """{'exists': bool, 'folders': {folder: entry}, 'default': entry|None}."""
    p = colors_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": False, "folders": {}, "default": None}
    if not isinstance(data, dict):
        return {"exists": False, "folders": {}, "default": None}
    folders: dict[str, dict[str, str]] = {}
    default: Optional[dict[str, str]] = None
    for key, raw in data.items():
        e = _entry(raw)
        if e is None:
            continue
        if str(key).strip().lower() in DEFAULT_KEYS:
            default = e
        else:
            folders[str(key).strip()] = e
    return {"exists": True, "folders": folders, "default": default}


def color_for_folder(rel: str | None, table: Optional[dict[str, Any]] = None) -> Optional[dict[str, str]]:
    """Color for a House destination such as 'Chill/Journey/low_quality_backups' (longest listed prefix wins)."""
    table = table or load()
    parts = [p for p in re.split(r"[\\/]+", str(rel or "")) if p]
    lowered = {k.lower(): v for k, v in table["folders"].items()}
    # LONGEST listed prefix wins (Chill/Journey before Chill); deeper subfolders
    # such as low_quality_backups inherit the nearest listed ancestor.
    for n in range(len(parts), 0, -1):
        cand = "/".join(parts[:n]).lower()
        if cand in lowered:
            return lowered[cand]
    return table["default"] if parts else None


def user_color_for_folder(rel: str | None) -> Optional[str]:
    c = color_for_folder(rel)
    return c["vdj_value"] if c else None


def _rgb(hexv: str) -> tuple[int, int, int]:
    return tuple(int(hexv[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def pick_distinct_color(used_hex: list[str]) -> str:
    """A hex color as far as possible (RGB distance) from every color already used."""
    import colorsys

    used = [_rgb(h) for h in used_hex if _HEX_RE.match(h or "")]
    best, best_d = "#1F8A8A", -1.0
    for hue in range(0, 360, 5):
        for sat, lig in ((0.75, 0.38), (0.55, 0.30), (0.85, 0.55), (0.35, 0.50), (0.9, 0.25)):
            r, g, b = (round(v * 255) for v in colorsys.hls_to_rgb(hue / 360, lig, sat))
            cand = (r, g, b)
            d = min((sum((a - c) ** 2 for a, c in zip(cand, u)) ** 0.5 for u in used), default=999.0)
            if d > best_d:
                best_d, best = d, "#{:02X}{:02X}{:02X}".format(r, g, b)
    return best


def ensure_folder_color(rel: str) -> Optional[dict[str, str]]:
    """Give a NEW House folder its own folder color in house_folder_colors.json (a distinct one, not
    already used). Existing entries are never changed. Returns the entry, or None if it could not be written."""
    import os
    import tempfile

    key = "/".join(p for p in re.split(r"[\\/]+", str(rel or "")) if p)
    if not key:
        return None
    p = colors_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if any(str(k).strip().lower() == key.lower() for k in data):
        return _entry(next(v for k, v in data.items() if str(k).strip().lower() == key.lower()))
    used = [str(v.get("hex") or "") for v in data.values() if isinstance(v, dict)]
    hexv = pick_distinct_color(used)
    entry = {"name": key.rsplit("/", 1)[-1], "hex": hexv, "vdj_value": 0xFF000000 | int(hexv[1:], 16)}
    default = data.get("_default")
    new = {k: v for k, v in data.items() if k not in DEFAULT_KEYS}
    new[key] = entry
    for k in DEFAULT_KEYS:  # keep the silver default last
        if k in data:
            new[k] = data[k]
    try:
        fd, tmp = tempfile.mkstemp(prefix=".house_folder_colors.", dir=str(p.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(new, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        return None
    return _entry(entry)
