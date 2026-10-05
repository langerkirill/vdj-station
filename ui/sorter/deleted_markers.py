"""Per-song memory of cues/loops the user deleted in the UI.

Stored in ``<notes dir>/deleted_markers.json`` (atomic writes). AutoCue /
Virtual DJ Developer can read it (``GET /api/deleted-markers?path=``) to avoid
re-creating a marker the user already removed. Undo removes the record.

Each record keeps the exact ``raw`` ``<Poi .../>`` line that was removed so an
Undo can put back the identical cue/loop (name, pos, size, color, num, slot).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config

_LOCK = threading.RLock()
FILE_NAME = "deleted_markers.json"


def store_path() -> Path:
    return Path(config.DJ_NOTES_ROOT) / FILE_NAME


def marker_key(path: str, kind: str, name: Any, pos: Any, size: Any) -> str:
    try:
        pos_s = f"{float(pos):.3f}"
    except (TypeError, ValueError):
        pos_s = str(pos)
    size_s = "" if size in (None, "") else str(size)
    return f"{path}|{kind}|{name or ''}|{pos_s}|{size_s}"


def _load() -> dict[str, Any]:
    p = store_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "markers": []}
    if not isinstance(data, dict) or not isinstance(data.get("markers"), list):
        return {"version": 1, "markers": []}
    return data


def _save(data: dict[str, Any]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def record_deleted(path: str, removed: dict[str, Any]) -> dict[str, Any]:
    """Remember one deleted marker. ``removed`` is delete_cue_point()['removed']."""
    kind = "loop" if str(removed.get("kind")) == "loop" else "cue"
    entry = {
        "id": uuid.uuid4().hex[:12],
        "key": marker_key(path, kind, removed.get("name"), removed.get("pos"), removed.get("size")),
        "path": path,
        "kind": kind,
        "name": removed.get("name"),
        "pos": removed.get("pos"),
        "size": removed.get("size"),
        "num": removed.get("num"),
        "slot": removed.get("slot"),
        "color": removed.get("color"),
        "raw": removed.get("raw"),
        "deleted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with _LOCK:
        data = _load()
        # Same marker deleted twice → keep one record (latest raw wins).
        data["markers"] = [m for m in data["markers"] if m.get("key") != entry["key"]]
        data["markers"].append(entry)
        _save(data)
    return entry


def get_record(marker_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        for m in _load()["markers"]:
            if m.get("id") == marker_id:
                return m
    return None


def remove_record(marker_id: str) -> bool:
    with _LOCK:
        data = _load()
        kept = [m for m in data["markers"] if m.get("id") != marker_id]
        if len(kept) == len(data["markers"]):
            return False
        data["markers"] = kept
        _save(data)
        return True


def list_for_path(path: str) -> list[dict[str, Any]]:
    with _LOCK:
        return [m for m in _load()["markers"] if m.get("path") == path]
