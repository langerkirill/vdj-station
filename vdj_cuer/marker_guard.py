"""Writer guard: never move, rename, recolor, resize or remove Kirill's own markers.

Kirill / Tech Lead 2026-10-12 (R-88): on a re-run AutoCue compares every marker already on
the song with what AutoCue itself LAST WROTE there. A marker that is not exactly AutoCue's
last write (same kind, position, name, color and loop length) is Kirill's and is kept as it
is. AutoCue may only add markers, or replace its OWN markers that nobody touched since.
New proposals never collide with a kept hand marker (same spot within ~0.25 s, the same bar,
or a loop overlapping his loop). deleted_markers.json still blocks re-adds (filtered before).

"What AutoCue last wrote" comes from, in order:
  1. ``autocue_written.json`` next to deleted_markers.json, written at every live job;
  2. for jobs from before that record existed: the first VirtualDJ database backup taken at
     or after the job finished (every later write - AutoCue or Music Sorter - backs the
     database up first, so that backup is the song exactly as AutoCue left it);
  3. neither: EVERY existing marker counts as Kirill's (safe default).
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

POS_TOL = 0.002          # seconds: an untouched marker sits exactly where AutoCue put it
SPOT_EPS = 0.25          # a new marker this close to a kept hand marker is a collision
RECORD_FILE = "autocue_written.json"
JOBS_FILE = "autocue_jobs.json"
_VDJ_DIR = Path.home() / "Library" / "Application Support" / "VirtualDJ"


# --------------------------------------------------------------------------- record store
def _notes_dir() -> Path:
    from .deleted_markers import resolve_store_path

    p = resolve_store_path()
    return Path(p).parent if p else Path.home() / "Music" / "DJ" / "Notes" / "House-8788"


def record_path() -> Path:
    override = (os.environ.get("AUTOCUE_WRITTEN_RECORD_PATH") or "").strip()
    return Path(override).expanduser() if override else _notes_dir() / RECORD_FILE


def _norm(p: str) -> str:
    from .deleted_markers import _norm_path

    return _norm_path(p)


def fingerprint(kind: str, position: float, name: str, color: Any, length_beats: Any = None) -> Dict[str, Any]:
    return {
        "kind": "loop" if str(kind).lower() == "loop" else "cue",
        "position": round(float(position), 6),
        "name": str(name or ""),
        "color": str(color or ""),
        "length_beats": (float(length_beats) if str(kind).lower() == "loop" and length_beats is not None else None),
    }


def _fp_of(m: Any) -> Dict[str, Any]:
    import html

    fp = _fp_raw(m)
    fp["name"] = html.unescape(fp["name"])  # DB attrs are XML-escaped; PreparedPoi names are not
    return fp


def _fp_raw(m: Any) -> Dict[str, Any]:
    if isinstance(m, dict):
        return fingerprint(m.get("kind", "cue"), m.get("position", 0.0), m.get("name", ""),
                           m.get("color", m.get("color_value", "")), m.get("length_beats"))
    return fingerprint(getattr(m, "kind", "cue"), getattr(m, "position", 0.0), getattr(m, "name", ""),
                       getattr(m, "color_value", getattr(m, "color", "")), getattr(m, "length_beats", None))


def same_marker(a: Any, b: Any) -> bool:
    fa, fb = _fp_of(a), _fp_of(b)
    if fa["kind"] != fb["kind"] or fa["name"] != fb["name"] or fa["color"] != fb["color"]:
        return False
    if abs(fa["position"] - fb["position"]) > POS_TOL:
        return False
    if fa["kind"] == "loop":
        la, lb = fa["length_beats"], fb["length_beats"]
        if la is None or lb is None or abs(float(la) - float(lb)) > 1e-6:
            return False
    return True


def _load_record(store: Optional[Path] = None) -> Dict[str, Any]:
    path = store or record_path()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "songs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("songs"), dict):
        return {"version": 1, "songs": {}}
    return data


def save_written(audio_path: str, markers: Iterable[Any], *, job_id: str = "",
                 store: Optional[Path] = None) -> Path:
    """Record exactly what AutoCue wrote for this song (call only after a LIVE write)."""
    path = Path(store or record_path())
    data = _load_record(path)
    data["songs"][_norm(audio_path)] = {
        "path": audio_path,
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "job_id": job_id,
        "markers": [_fp_of(m) for m in markers],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- fallback: backups
def _backup_stamp(p: str) -> Optional[datetime]:
    m = re.search(r"(\d{8}_\d{6})", os.path.basename(p))
    return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S") if m else None


def _last_live_job(audio_path: str, jobs_file: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = jobs_file or (_notes_dir() / JOBS_FILE)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    items = data if isinstance(data, list) else (data.get("jobs") if isinstance(data, dict) else None)
    if isinstance(data, dict) and items is None:
        items = list(data.values())
    best = None
    target = _norm(audio_path)
    for job in items or []:
        if not isinstance(job, dict) or job.get("dry_run") or job.get("status") != "ok":
            continue
        if _norm(str(job.get("path") or "")) != target or not job.get("finished_at"):
            continue
        if best is None or str(job["finished_at"]) > str(best["finished_at"]):
            best = job
    return best


def _markers_in_db_text(text: str, audio_path: str) -> Optional[List[Dict[str, Any]]]:
    from vdj_database_safety import _find_song_span, extract_manual_pois_from_song_xml

    span = _find_song_span(text, audio_path)
    if span is None:
        return None
    ex = extract_manual_pois_from_song_xml(text[span[0]: span[1]])
    return [_fp_of({**m, "kind": "cue"}) for m in ex.get("cues", [])] + [
        _fp_of({**m, "kind": "loop"}) for m in ex.get("loops", [])
    ]


def written_from_backups(audio_path: str, *, jobs_file: Optional[Path] = None,
                         backup_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    job = _last_live_job(audio_path, jobs_file)
    if job is None:
        return None
    try:
        finished = datetime.fromisoformat(str(job["finished_at"]))
    except ValueError:
        return None
    root = Path(backup_dir or _VDJ_DIR)
    cands = []
    for p in glob.glob(str(root / "database.xml.*")):
        st = _backup_stamp(p)
        if st is not None and st >= finished:
            cands.append((st, p))
    for st, p in sorted(cands):
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        markers = _markers_in_db_text(text, audio_path)
        if markers is not None:
            return {"source": f"backup {os.path.basename(p)} after job {job.get('id')}",
                    "job_id": job.get("id", ""), "markers": markers}
    return None


def last_written(audio_path: str, *, store: Optional[Path] = None, jobs_file: Optional[Path] = None,
                 backup_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """{"source", "markers"} of AutoCue's last write on this song, or None if unknown."""
    rec = _load_record(store)["songs"].get(_norm(audio_path))
    if rec and isinstance(rec.get("markers"), list):
        return {"source": f"{RECORD_FILE} ({rec.get('written_at', '')})", "job_id": rec.get("job_id", ""),
                "markers": rec["markers"]}
    return written_from_backups(audio_path, jobs_file=jobs_file, backup_dir=backup_dir)


# --------------------------------------------------------------------------- the guard
@dataclass
class GuardResult:
    cues: List[Any]
    loops: List[Any]
    kept_hand: List[Any]
    replaced_own: List[Any]
    dropped_new: List[Tuple[Any, str]]
    source: str


def split_existing(existing: Sequence[Any], written: Optional[Sequence[Any]]) -> Tuple[List[Any], List[Any]]:
    """(hand, own_untouched). Unknown history -> everything is Kirill's."""
    if written is None:
        return list(existing), []
    pool = list(written)
    hand, own = [], []
    for m in existing:
        hit = next((w for w in pool if same_marker(m, w)), None)
        if hit is None:
            hand.append(m)
        else:
            pool.remove(hit)
            own.append(m)
    return hand, own


def _bar_index(pos: float, offset: float, bar: float) -> Optional[int]:
    if not bar or bar <= 0:
        return None
    import math

    return int(math.floor((float(pos) - float(offset)) / bar + 1e-6))


def existing_as_prepared(audio_path: str, database_path: str) -> Tuple[List[Any], List[Any]]:
    """The song's markers as PreparedPoi, byte-faithful (raw name, color, length)."""
    import html

    from vdj_database_safety import _find_song_span, extract_manual_pois_from_song_xml, read_vdj_database_text
    from .cue_writer import PreparedPoi

    text = read_vdj_database_text(database_path)
    span = _find_song_span(text, audio_path)
    if span is None:
        return [], []
    ex = extract_manual_pois_from_song_xml(text[span[0]: span[1]])

    def mk(m, kind):
        return PreparedPoi(
            kind=kind, name=html.unescape(str(m.get("name") or "")), position=float(m["position"]),
            color_name="", color_value=str(m.get("color") or ""), elements=["kirill"],
            length_beats=(float(m["length_beats"]) if kind == "loop" and m.get("length_beats") is not None else None),
            jumpable=str(m.get("num", "1")) != "0",
        )

    return [mk(m, "cue") for m in ex.get("cues", [])], [mk(m, "loop") for m in ex.get("loops", [])]


def guard_plan(new_cues: Sequence[Any], new_loops: Sequence[Any], existing_cues: Sequence[Any],
               existing_loops: Sequence[Any], written: Optional[Dict[str, Any]], *, beat: float,
               offset: float = 0.0) -> GuardResult:
    """Final markers to write: Kirill's markers untouched + non-colliding new proposals."""
    wm = written["markers"] if written else None
    hand, own = split_existing(list(existing_cues) + list(existing_loops), wm)
    hand_cues = [m for m in hand if getattr(m, "kind", "cue") != "loop"]
    hand_loops = [m for m in hand if getattr(m, "kind", "cue") == "loop"]
    bar = 4.0 * float(beat or 0.0)
    dropped: List[Tuple[Any, str]] = []

    def loop_span(m) -> Tuple[float, float]:
        beats = float(getattr(m, "length_beats", None) or 16)
        return float(m.position), float(m.position) + beats * float(beat or 0.0)

    cues: List[Any] = []
    for c in new_cues:
        why = ""
        for h in hand_cues:
            if abs(c.position - h.position) <= SPOT_EPS:
                why = f"within {SPOT_EPS}s of Kirill's cue '{h.name}' @ {h.position:.3f}"
            elif bar and _bar_index(c.position, offset, bar) == _bar_index(h.position, offset, bar):
                why = f"same bar as Kirill's cue '{h.name}' @ {h.position:.3f}"
            if why:
                break
        if not why:
            for h in hand_loops:
                if abs(c.position - h.position) <= SPOT_EPS:
                    why = f"on Kirill's loop '{h.name}' @ {h.position:.3f}"
                    break
        (dropped.append((c, why)) if why else cues.append(c))

    loops: List[Any] = []
    for lp in new_loops:
        a, b = loop_span(lp)
        why = ""
        for h in hand_loops:
            ha, hb = loop_span(h)
            if a < hb - 1e-3 and ha < b - 1e-3:
                why = f"overlaps Kirill's loop '{h.name}' @ {h.position:.3f}"
                break
        (dropped.append((lp, why)) if why else loops.append(lp))

    from dataclasses import is_dataclass, replace as _replace

    def _autocue(m):
        return _replace(m, elements=["autocue-kept"]) if is_dataclass(m) and hasattr(m, "elements") else m

    own_cues = [m for m in own if getattr(m, "kind", "cue") != "loop"]
    own_loops = [m for m in own if getattr(m, "kind", "cue") == "loop"]
    if not new_cues:
        cues = [_autocue(m) for m in own_cues]  # nothing new for this side: AutoCue's own markers stay too
        own = [m for m in own if m not in own_cues]
    if not new_loops:
        loops = [_autocue(m) for m in own_loops]
        own = [m for m in own if m not in own_loops]
    src = written["source"] if written else "no record of AutoCue's last write: all existing markers kept as Kirill's"
    cues = distinct_new_names(cues, hand_cues)
    return GuardResult(
        cues=sorted(hand_cues + cues, key=lambda m: m.position),
        loops=sorted(hand_loops + loops, key=lambda m: m.position),
        kept_hand=hand, replaced_own=own, dropped_new=dropped, source=src,
    )


def distinct_new_names(new_cues: Sequence[Any], kept_cues: Sequence[Any]) -> List[Any]:
    """A new cue never repeats the name of a kept cue or an earlier new cue (Besomage
    2026-10-12: Kirill's kept 'Chorus' @ 30.9 plus a new 'Chorus' @ 60.9). The repeat takes
    the next unused name for its color (stem_naming.CUE_POOL), never a ' 2' suffix."""
    from dataclasses import is_dataclass, replace as _replace

    from .stem_naming import CUE_POOL, _pick

    used = {str(getattr(m, "name", "") or "") for m in kept_cues}
    out: List[Any] = []
    for c in sorted(new_cues, key=lambda m: m.position):
        name = str(getattr(c, "name", "") or "")
        if name and name in used and is_dataclass(c):
            pool = CUE_POOL.get(str(getattr(c, "color_name", "") or "").lower(), [])
            alt = _pick(pool, used, "")
            if alt:
                print(f"  🏷️  {name} @ {c.position:.3f} → {alt} (name already used on this song)")
                c = _replace(c, name=alt)
                name = alt
        used.add(name)
        out.append(c)
    return out

