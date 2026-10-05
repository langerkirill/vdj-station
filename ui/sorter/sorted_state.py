"""Persisted 'already sorted' state for the Add Cues list (HOUSE FORK).

House sort COPIES, so the original stays in Add Cues. The list therefore needs its own
memory of 'this one is done': ``sorted.json`` in the House notes dir.

An entry is keyed by the source path and remembers the source's size + mtime and the
copies made. A row is hidden only while ALL of these hold:
  * the source file is unchanged (same size and mtime) - a re-added/replaced file shows again;
  * every recorded copy still exists on disk.
Nothing here touches audio or database.xml; deleting sorted.json simply makes rows reappear.
The first load back-fills entries from successful ``sort`` / ``sort_sauna_fest`` actions in
the action log (only where the source predates the action and the copies exist).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config as _config

SORTED_FILE = "sorted.json"
_lock = threading.Lock()
_SORT_ACTIONS = {"sort", "sort_sauna_fest"}


def _path() -> Path:
    return Path(_config.DJ_NOTES_ROOT) / SORTED_FILE


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"seeded": False, "entries": {}}


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sorted.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _sig(source: Path) -> tuple[int, int] | None:
    try:
        st = source.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def record_sorted(source: str | Path, copies: Iterable[str | Path], *, kind: str = "house") -> None:
    src = Path(source)
    sig = _sig(src)
    if sig is None:
        return
    with _lock:
        data = _load()
        data["entries"][str(src)] = {
            "size": sig[0],
            "mtime_ns": sig[1],
            "copies": [str(c) for c in copies],
            "kind": kind,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        _save(data)


def _entry_active(path: str, entry: dict[str, Any]) -> bool:
    sig = _sig(Path(path))
    if sig is None or (sig[0], sig[1]) != (entry.get("size"), entry.get("mtime_ns")):
        return False
    copies = entry.get("copies") or []
    return bool(copies) and all(Path(c).is_file() for c in copies)


def _seed_from_action_log(data: dict[str, Any]) -> None:
    """One-time back-fill from the action log (never overwrites an existing entry)."""
    try:
        lines = (Path(_config.DJ_NOTES_ROOT) / "music-sorter-actions.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("action") not in _SORT_ACTIONS or not rec.get("success"):
            continue
        src = rec.get("source_path")
        details = rec.get("details") or {}
        copies = [d.get("path") for d in (details.get("library_dests") or []) if d.get("path")]
        copies += [p for p in (details.get("set_copies") or []) if p]
        if not src or not copies or src in data["entries"]:
            continue
        sig = _sig(Path(src))
        if sig is None or not all(Path(c).is_file() for c in copies):
            continue
        try:
            logged = datetime.fromisoformat(rec["ts"]).timestamp()
        except (KeyError, ValueError):
            continue
        if Path(src).stat().st_mtime > logged:
            continue  # source was replaced after that sort
        data["entries"][src] = {
            "size": sig[0],
            "mtime_ns": sig[1],
            "copies": copies,
            "kind": "backfill",
            "ts": rec["ts"],
        }


def sorted_paths(paths: Iterable[str]) -> set[str]:
    """Subset of ``paths`` that are sorted (hide from Add Cues)."""
    with _lock:
        data = _load()
        if not data.get("seeded"):
            _seed_from_action_log(data)
            data["seeded"] = True
            _save(data)
    entries = data["entries"]
    return {p for p in paths if p in entries and _entry_active(p, entries[p])}


def drop_sorted(tracks: list[Any]) -> list[Any]:
    done = sorted_paths([t.path for t in tracks])
    return [t for t in tracks if t.path not in done] if done else tracks
