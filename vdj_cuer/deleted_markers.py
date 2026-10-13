"""Read-only respect for cues/loops the user deleted in the Music Sorter UI.

The fest9 UI records deletes in ``deleted_markers.json`` (see
``ui/sorter/deleted_markers.py`` in the fest9 fork). AutoCue must not recreate
those markers on retry / fresh write. This module reads the JSON directly so
``vdj_cuer`` works even when the fest9 UI package is not on ``sys.path``.

Schema (version 1): ``{"version": 1, "markers": [{"path", "kind", "name",
"pos", "size", "raw", "deleted_at", ...}, ...]}``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# Matches fest9 UI and cue_hygiene dedupe / drop_cues_under_loops.
POS_EPS = 0.25

_DEFAULT_NOTES = Path.home() / "Music" / "DJ" / "Notes"
_FILE = "deleted_markers.json"


def resolve_store_path() -> Optional[Path]:
    """Return the deleted-markers JSON path, or None if nothing is configured.

    Order:
      1. ``AUTOCUE_DELETED_MARKERS_PATH`` (exact file) when set and non-empty
      2. ``$DJ_NOTES_ROOT/House-8788/deleted_markers.json`` if that file exists
      3. ``$DJ_NOTES_ROOT/deleted_markers.json`` if that file exists
      4. Otherwise the House-8788 candidate (caller may get an empty list)
    ``DJ_NOTES_ROOT`` defaults to ``~/Music/DJ/Notes``.
    """
    override = (os.environ.get("AUTOCUE_DELETED_MARKERS_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    root = Path(
        (os.environ.get("DJ_NOTES_ROOT") or "").strip() or _DEFAULT_NOTES
    ).expanduser()
    house = root / "House-8788" / _FILE
    flat = root / _FILE
    if house.is_file():
        return house
    if flat.is_file():
        return flat
    return house


def _load_markers(store: Optional[Path] = None) -> List[dict[str, Any]]:
    path = store if store is not None else resolve_store_path()
    if path is None:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("markers"), list):
        return []
    return [m for m in data["markers"] if isinstance(m, dict)]


def _norm_path(p: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(p)))
    except (TypeError, ValueError, OSError):
        return str(p or "")


def _paths_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    na, nb = _norm_path(a), _norm_path(b)
    if na == nb:
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def list_for_path(audio_path: str, store: Optional[Path] = None) -> List[dict[str, Any]]:
    """Deleted-marker records for ``audio_path`` (exact / abspath / samefile)."""
    return [m for m in _load_markers(store) if _paths_match(str(m.get("path") or ""), audio_path)]


def _pos_of(record_or_poi: Any) -> Optional[float]:
    if isinstance(record_or_poi, dict):
        raw = record_or_poi.get("pos")
    else:
        raw = getattr(record_or_poi, "position", None)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _kind_of(record_or_poi: Any) -> str:
    if isinstance(record_or_poi, dict):
        k = str(record_or_poi.get("kind") or "cue")
    else:
        k = str(getattr(record_or_poi, "kind", "cue") or "cue")
    return "loop" if k == "loop" else "cue"


def _name_of(record_or_poi: Any) -> str:
    if isinstance(record_or_poi, dict):
        return str(record_or_poi.get("name") or "")
    return str(getattr(record_or_poi, "name", "") or "")


def matches_deleted(
    poi: Any,
    deleted: Sequence[dict[str, Any]],
    *,
    eps: float = POS_EPS,
) -> Optional[dict[str, Any]]:
    """Return the deleted record that matches ``poi``, or None.

    Match = same kind + position within ``eps``. When both sides have a non-empty
    name and those names differ, still match (position+kind is enough) — the
    name is only a preference for choosing among several hits.
    """
    pos = _pos_of(poi)
    if pos is None:
        return None
    kind = _kind_of(poi)
    poi_name = _name_of(poi).strip().lower()
    hits: List[dict[str, Any]] = []
    for rec in deleted:
        if _kind_of(rec) != kind:
            continue
        rpos = _pos_of(rec)
        if rpos is None or abs(rpos - pos) > eps:
            continue
        hits.append(rec)
    if not hits:
        return None
    if poi_name:
        named = [h for h in hits if _name_of(h).strip().lower() == poi_name]
        if named:
            return named[0]
    return hits[0]


def filter_deleted(
    cues: Iterable[Any],
    loops: Iterable[Any],
    audio_path: str,
    *,
    store: Optional[Path] = None,
    eps: float = POS_EPS,
) -> Tuple[List[Any], List[Any], List[str]]:
    """Drop cues/loops that match a deleted record for ``audio_path``.

    Returns ``(kept_cues, kept_loops, drop_messages)``. Messages are suitable
    for logging (e.g. ``"cue 'Outro' @ 168.126 (user-deleted)"``).
    """
    deleted = list_for_path(audio_path, store=store)
    if not deleted:
        return list(cues), list(loops), []

    kept_cues: List[Any] = []
    kept_loops: List[Any] = []
    msgs: List[str] = []

    for cue in cues:
        hit = matches_deleted(cue, deleted, eps=eps)
        if hit is None:
            kept_cues.append(cue)
            continue
        name = _name_of(cue) or _name_of(hit) or "cue"
        pos = _pos_of(cue)
        msgs.append(f"cue '{name}' @ {pos:.3f} (user-deleted)")

    for loop in loops:
        hit = matches_deleted(loop, deleted, eps=eps)
        if hit is None:
            kept_loops.append(loop)
            continue
        name = _name_of(loop) or _name_of(hit) or "loop"
        pos = _pos_of(loop)
        msgs.append(f"loop '{name}' @ {pos:.3f} (user-deleted)")

    return kept_cues, kept_loops, msgs
