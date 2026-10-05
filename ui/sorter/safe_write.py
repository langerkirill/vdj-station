"""Single-writer, verified database.xml Song writes (HOUSE FORK).

Every cue/loop/name/color/BPM/notes edit and every copy-sort clone goes through
here. One call = ONE critical section under the cross-process flock:

  1. refuse if VirtualDJ is running, if another writer touched database.xml within
     the last 20 s, or if a ``*vdjdev*`` backup is newer than 60 s;
  2. re-read database.xml INSIDE the lock (never a cached/pre-lock parse);
  3. refuse if the target Song changed on disk since the caller read it;
  4. take ONE timestamped backup per server session before the first write;
  5. splice only the target Song, write atomically (tempfile + validate + replace);
  6. read the file back and confirm the change landed -> ``saved`` True/False + reason.

A failure raises ``SaveFailed`` (a RuntimeError -> HTTP 409 with the reason).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from .autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()

import vdj_database_safety as V  # noqa: E402

FOREIGN_WRITE_WINDOW_S = 4.0  # VDJ Dev writes every ~15 s under the same flock; 20 s starved every edit
VDJDEV_BACKUP_WINDOW_S = 5.0
DEFAULT_BACKUP_DIR = Path.home() / "Music" / "DJ" / "Notes" / "backups-8788-write"


class SaveFailed(RuntimeError):
    """A save was refused or did not verify. ``reason`` is shown to the user."""

    def __init__(self, reason: str, *, code: str = "save_failed"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


_state: dict[str, Any] = {
    "session_backup": None,  # path of the one backup taken this session
    "own_mtime_ns": None,  # mtime_ns of database.xml right after OUR last write
    "failures": [],  # [{ts, endpoint, reason}] this session
}
_state_lock = threading.Lock()
_tl = threading.local()


def _window(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except ValueError:
        return default


def backup_dir() -> Path:
    override = os.environ.get("MUSIC_SORTER_WRITE_BACKUP_DIR", "").strip()
    return Path(override).expanduser() if override else DEFAULT_BACKUP_DIR


def reset_session_for_tests() -> None:
    with _state_lock:
        _state.update(session_backup=None, own_mtime_ns=None, failures=[])


def _norm(xml: str) -> str:
    return xml.replace("\r\n", "\n").strip()


def _guards(db: Path) -> None:
    try:
        V.assert_safe_to_write_vdj_database()
    except RuntimeError as exc:  # VirtualDJRunningError / ReadOnlyModeError
        raise SaveFailed(str(exc), code="vdj_running") from exc
    now = time.time()
    st = db.stat()
    win = _window("MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S", FOREIGN_WRITE_WINDOW_S)
    age = now - st.st_mtime
    if win > 0 and 0 <= age < win and st.st_mtime_ns != _state["own_mtime_ns"]:
        raise SaveFailed(
            f"database.xml was changed by another writer {age:.0f}s ago "
            f"(VirtualDJ Dev / another editor). Nothing was written — wait "
            f"{int(win - age) + 1}s and retry.",
            code="foreign_writer",
        )
    bwin = _window("MUSIC_SORTER_VDJDEV_BACKUP_WINDOW_S", VDJDEV_BACKUP_WINDOW_S)
    if bwin > 0:
        for p in glob.glob(str(db.parent / f"{db.name}.backup.*vdjdev*")):
            try:
                bage = now - os.stat(p).st_mtime
            except OSError:
                continue
            if 0 <= bage < bwin:
                raise SaveFailed(
                    f"A VirtualDJ Dev backup is only {bage:.0f}s old "
                    f"({os.path.basename(p)}) — VDJ Dev is writing. Nothing was "
                    f"written; retry in about {int(bwin - bage) + 1}s.",
                    code="vdjdev_active",
                )


def _ensure_session_backup(db: Path) -> str:
    """One timestamped backup per server session, before the first write."""
    with _state_lock:
        existing = _state["session_backup"]
    if existing:
        return existing
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{db.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.session-first-write"
    shutil.copy2(db, dest)
    with _state_lock:
        _state["session_backup"] = str(dest)
    return str(dest)


def _crlf(xml: str, newline: str) -> str:
    if V.legacy_fixture_mode():
        return xml.replace("\r\n", "\n").replace("\n", "\r\n") if newline == "\r\n" else xml
    # VDJ's exact Song form: CRLF, children at 2 spaces, Comment right after Infos.
    return V.canonical_song_block(xml)


def _row(song_xml: str, newline: str) -> str:
    """One whole Song row as VDJ writes it: ' <Song ...>' .. ' </Song>' + CRLF."""
    if V.legacy_fixture_mode():
        row = _crlf(song_xml.strip(), newline)
        return row if row.endswith(newline) else row + newline
    return " " + V.canonical_song_block(song_xml) + "\r\n"


def _same_song(a: str, b: str) -> bool:
    if V.legacy_fixture_mode():
        return _norm(a) == _norm(b)
    return V.canonical_song_block(a) == V.canonical_song_block(b)


def _set_last(**info: Any) -> None:
    _tl.last = info


def _fail(reason: str, code: str = "save_failed") -> "SaveFailed":
    _set_last(saved=False, reason=reason, backup=_state["session_backup"], code=code)
    return SaveFailed(reason, code=code)


STALE_RETRIES = 3  # whole-edit re-reads after a "song changed while saving" refusal
RETRY_CODES = frozenset({"foreign_writer", "vdjdev_active"})
FOREIGN_RETRY_BUDGET_S = 40.0  # total time one edit may wait for VDJ Dev to go quiet
FOREIGN_RETRY_POLL_S = 1.5


def _retry_foreign(fn: Callable) -> Callable:
    """Queue-and-retry a write that was refused ONLY because a foreign writer
    (VirtualDJ Dev) wrote recently. The lock is NOT held while waiting. Each
    attempt re-acquires the cross-process lock, re-reads database.xml and
    rewrites only the target Song, so VDJ Dev's songs are never clobbered.
    VDJ-running, corrupt, stale, shrink, etc. refusals are never retried."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        budget = _window("MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S", FOREIGN_RETRY_BUDGET_S)
        poll = max(0.01, _window("MUSIC_SORTER_FOREIGN_RETRY_POLL_S", FOREIGN_RETRY_POLL_S))
        deadline = time.monotonic() + budget
        while True:
            try:
                return fn(*args, **kwargs)
            except SaveFailed as exc:
                if exc.code not in RETRY_CODES or time.monotonic() + poll > deadline:
                    raise
                time.sleep(poll)

    return wrapper


@_retry_foreign
def safe_rewrite_song(
    database_path: os.PathLike | str,
    path_in_db: str,
    new_song: str,
    *,
    base_song: Optional[str] = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Replace ONE Song block (re-read under the lock) and verify it landed."""
    db = Path(database_path)
    try:
        with V.vdj_database_exclusive_lock(db):
            _guards(db)
            content = V.read_vdj_database_text(db)  # re-read INSIDE the lock
            span = V._find_song_span(content, path_in_db)
            if span is None:
                raise SaveFailed(f"Song not found in database: {path_in_db}", code="no_song")
            start, end = span
            current = content[start:end]
            if base_song is not None and _norm(current) != _norm(base_song):
                raise SaveFailed(
                    "The song changed on disk while this edit was being saved (another "
                    "tab or VirtualDJ Dev). Nothing was written — try the edit again.",
                    code="stale",
                )
            newline = V._detect_newline(content)
            song_xml = _crlf(new_song, newline)
            if len(current) >= 400 and len(song_xml) < int(len(current) * 0.5):
                raise SaveFailed(
                    "Refusing song rewrite that shrinks the Song block by more than 50%",
                    code="shrink",
                )
            original_stats = V._lightweight_rewrite_stats(content, db) if validate else None
            backup = _ensure_session_backup(db)
            stats = V._replace_database_parts_locked(
                db,
                (content[:start], song_xml, content[end:]),
                original_stats,
                stats_fn=V._lightweight_content_stats if validate else None,
            )
            own = db.stat().st_mtime_ns
            with _state_lock:
                _state["own_mtime_ns"] = own
            back = V.read_vdj_database_text(db)
            span2 = V._find_song_span(back, path_in_db)
            if span2 is None or not _same_song(back[span2[0]:span2[1]], song_xml):
                raise SaveFailed(
                    "Write finished but the read-back from database.xml does not "
                    "match the edit — treat it as NOT saved and reload.",
                    code="readback_mismatch",
                )
    except SaveFailed as exc:
        raise _fail(exc.reason, exc.code)
    except Exception as exc:
        raise _fail(f"{type(exc).__name__}: {exc}", "error") from exc
    _set_last(saved=True, reason="verified in database.xml", backup=backup, code="ok")
    return {**stats, "saved": True, "backup": backup}


@_retry_foreign
def safe_clone_song(
    database_path: os.PathLike | str,
    source_path_in_db: str,
    new_path_in_db: str,
    *,
    validate: bool = True,
    user_color: Optional[str] = None,
) -> dict[str, Any]:
    """Clone one Song under a new FilePath (cues/loops/beatgrid/color kept, User2 from
    the destination folder), appended before </VirtualDJ_Database>. Skips if present.
    Re-reads under the lock, one session backup first, read-back verified."""
    db = Path(database_path)
    try:
        with V.vdj_database_exclusive_lock(db):
            _guards(db)
            content = V.read_vdj_database_text(db)
            new_norm = V.normalize_database_path(new_path_in_db)
            if V._find_song_span(content, new_norm) is not None:
                _set_last(saved=True, reason="already present — skipped", backup=None, code="skipped")
                return {"cloned": False, "already_present": True, "saved": True}
            span = V._find_song_span(content, V.normalize_database_path(source_path_in_db))
            if span is None:
                raise SaveFailed(
                    f"Source song is not in database.xml: {source_path_in_db}", code="no_song"
                )
            cloned = V.song_xml_with_new_filepath(content[span[0]:span[1]], new_norm)
            if user_color:
                cloned = V.patch_song_infos_and_user2(cloned, user_color=str(user_color))
            newline = V._detect_newline(content)
            cloned = _row(cloned, newline)
            close_idx = content.rfind("</VirtualDJ_Database>")
            if close_idx < 0:
                raise SaveFailed("Database is missing </VirtualDJ_Database>", code="corrupt")
            prefix = content[:close_idx].rstrip(" \t")
            if not prefix.endswith("\n"):
                prefix += newline
            original_stats = V._lightweight_rewrite_stats(content, db) if validate else None
            backup = _ensure_session_backup(db)
            stats = V._replace_database_parts_locked(
                db,
                (prefix, cloned, content[close_idx:]),
                original_stats,
                stats_fn=V._lightweight_content_stats if validate else None,
            )
            own = db.stat().st_mtime_ns
            with _state_lock:
                _state["own_mtime_ns"] = own
            back = V.read_vdj_database_text(db)
            span2 = V._find_song_span(back, new_norm)
            if span2 is None or not _same_song(back[span2[0]:span2[1]], cloned):
                raise SaveFailed(
                    "Clone written but the read-back from database.xml does not match.",
                    code="readback_mismatch",
                )
    except SaveFailed as exc:
        raise _fail(exc.reason, exc.code)
    except Exception as exc:
        raise _fail(f"{type(exc).__name__}: {exc}", "error") from exc
    _set_last(saved=True, reason="clone verified in database.xml", backup=backup, code="ok")
    return {"cloned": True, "already_present": False, "saved": True, "backup": backup, **stats}


@_retry_foreign
def safe_clone_songs(
    database_path: os.PathLike | str,
    source_path_in_db: str,
    targets: list[tuple[str, Optional[str]]],
    *,
    validate: bool = True,
    user_color: Optional[str] = None,
) -> dict[str, Any]:
    """Clone ONE source Song under several new FilePaths in ONE locked write.

    ``targets`` = [(new_path_in_db, directory_sort_path_or_None), ...]. All new Songs
    land together (one atomic file replace) or none do - this is the all-or-nothing DB
    half of 'Add to Sauna Fest'. Targets already present are skipped (never duplicated).
    ``directory_sort_path`` sets where User2 is taken from (a set copy takes the House
    folder's label, like Pajamathon set copies take their origin crate).
    """
    db = Path(database_path)
    try:
        with V.vdj_database_exclusive_lock(db):
            _guards(db)
            content = V.read_vdj_database_text(db)
            span = V._find_song_span(content, V.normalize_database_path(source_path_in_db))
            if span is None:
                raise SaveFailed(
                    f"Source song is not in database.xml: {source_path_in_db}", code="no_song"
                )
            src_xml = content[span[0]:span[1]]
            newline = V._detect_newline(content)
            todo: list[tuple[str, str]] = []
            present: list[str] = []
            for new_path, dsp in targets:
                new_norm = V.normalize_database_path(new_path)
                if V._find_song_span(content, new_norm) is not None:
                    present.append(new_norm)
                    continue
                cloned = V.song_xml_with_new_filepath(src_xml, new_norm, directory_sort_path=dsp)
                if user_color:
                    cloned = V.patch_song_infos_and_user2(cloned, user_color=str(user_color))
                cloned = _row(cloned, newline)
                todo.append((new_norm, cloned))
            if not todo:
                _set_last(saved=True, reason="already present - skipped", backup=None, code="skipped")
                return {"cloned": [], "already_present": present, "saved": True}
            close_idx = content.rfind("</VirtualDJ_Database>")
            if close_idx < 0:
                raise SaveFailed("Database is missing </VirtualDJ_Database>", code="corrupt")
            prefix = content[:close_idx].rstrip(" \t")
            if not prefix.endswith("\n"):
                prefix += newline
            original_stats = V._lightweight_rewrite_stats(content, db) if validate else None
            backup = _ensure_session_backup(db)
            stats = V._replace_database_parts_locked(
                db,
                (prefix, "".join(x for _p, x in todo), content[close_idx:]),
                original_stats,
                stats_fn=V._lightweight_content_stats if validate else None,
            )
            own = db.stat().st_mtime_ns
            with _state_lock:
                _state["own_mtime_ns"] = own
            back = V.read_vdj_database_text(db)
            for new_norm, cloned in todo:
                span2 = V._find_song_span(back, new_norm)
                if span2 is None or not _same_song(back[span2[0]:span2[1]], cloned):
                    raise SaveFailed(
                        "Clone written but the read-back from database.xml does not match.",
                        code="readback_mismatch",
                    )
    except SaveFailed as exc:
        raise _fail(exc.reason, exc.code)
    except Exception as exc:
        raise _fail(f"{type(exc).__name__}: {exc}", "error") from exc
    _set_last(saved=True, reason="clones verified in database.xml", backup=backup, code="ok")
    return {
        "cloned": [p for p, _x in todo],
        "already_present": present,
        "saved": True,
        "backup": backup,
        **stats,
    }


@_retry_foreign
def safe_remove_songs(
    database_path: os.PathLike | str,
    paths_in_db: list[str],
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Remove the Song blocks for exactly ``paths_in_db`` (cleanup of scratch/test entries).

    Same guarantees as the other writers: lock, guards, re-read inside the lock, one
    backup per session, atomic write, read-back (each path must be gone, Song count must
    drop by exactly the number removed). Paths that are not present are reported, not errors.
    """
    db = Path(database_path)
    removed: list[str] = []
    missing: list[str] = []
    try:
        with V.vdj_database_exclusive_lock(db):
            _guards(db)
            content = V.read_vdj_database_text(db)
            before_n = content.count("<Song ")
            cuts: list[tuple[int, int]] = []
            for pth in paths_in_db:
                norm = V.normalize_database_path(pth)
                span = V._find_song_span(content, norm)
                if span is None:
                    missing.append(norm)
                else:
                    cuts.append(span)
                    removed.append(norm)
            if not cuts:
                _set_last(saved=True, reason="nothing to remove", backup=None, code="skipped")
                return {"removed": [], "missing": missing, "saved": True}
            cuts.sort()
            parts: list[str] = []
            last = 0
            for st, en in cuts:
                # also swallow the line break after the block so no blank line is left
                while en < len(content) and content[en] in "\r\n":
                    en += 1
                    if content[en - 1] == "\n":
                        break
                # the removed row's own leading indent goes with it
                seg = content[last:st]
                if seg.endswith((" ", "\t")) and (seg.rstrip(" \t").endswith("\n") or not seg.rstrip(" \t")):
                    seg = seg.rstrip(" \t")
                parts.append(seg)
                last = en
            parts.append(content[last:])
            original_stats = V._lightweight_rewrite_stats(content, db) if validate else None
            if original_stats is not None:
                # intentional removal: expected stats shrink by exactly what is cut
                cut_txt = "".join(content[a:b] for a, b in cuts)
                original_stats = {
                    "size_bytes": max(0, int(original_stats["size_bytes"]) - len(cut_txt.encode("utf-8"))),
                    "song_count": max(0, int(original_stats["song_count"]) - len(cuts)),
                    "cue_loop_count": max(
                        0,
                        int(original_stats["cue_loop_count"])
                        - cut_txt.count('Type="cue"')
                        - cut_txt.count('Type="loop"'),
                    ),
                }
            backup = _ensure_session_backup(db)
            stats = V._replace_database_parts_locked(
                db,
                tuple(parts),
                original_stats,
                stats_fn=V._lightweight_content_stats if validate else None,
            )
            own = db.stat().st_mtime_ns
            with _state_lock:
                _state["own_mtime_ns"] = own
            back = V.read_vdj_database_text(db)
            if back.count("<Song ") != before_n - len(removed) or any(
                V._find_song_span(back, n) is not None for n in removed
            ):
                raise SaveFailed("Removal read-back does not match.", code="readback_mismatch")
    except SaveFailed as exc:
        raise _fail(exc.reason, exc.code)
    except Exception as exc:
        raise _fail(f"{type(exc).__name__}: {exc}", "error") from exc
    _set_last(saved=True, reason="removal verified in database.xml", backup=backup, code="ok")
    return {"removed": removed, "missing": missing, "saved": True, "backup": backup, **stats}


# --- endpoint decorator + session failure list --------------------------------

def last_save() -> Optional[dict[str, Any]]:
    return getattr(_tl, "last", None)


def session_failures() -> list[dict[str, Any]]:
    with _state_lock:
        return list(_state["failures"])


def record_failure(endpoint: str, reason: str, payload: Optional[dict] = None) -> None:
    with _state_lock:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "endpoint": endpoint, "reason": reason}
        if payload:
            entry["payload"] = payload  # the request that failed, so the edit can be re-applied
        _state["failures"].append(entry)
        del _state["failures"][:-200]


def with_save_status(endpoint: str) -> Callable:
    """Decorator for sync write endpoints: add saved/save_reason, log failures."""

    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            res = None
            for attempt in range(STALE_RETRIES + 1):
                _tl.last = None
                try:
                    res = fn(*args, **kwargs)
                    break
                except Exception as exc:
                    last_try = last_save()
                    if (
                        attempt < STALE_RETRIES
                        and last_try is not None
                        and last_try.get("code") == "stale"
                    ):
                        # The song moved on between this edit's read and its write (another tab, VDJ Dev,
                        # or a queued edit of ours). The edit is idempotent and nothing was written, so
                        # re-read and redo it instead of failing.
                        time.sleep(0.2 * (attempt + 1))
                        continue
                    detail = getattr(exc, "detail", None) or str(exc)
                    payload = None
                    for a in list(args) + list(kwargs.values()):
                        dump = getattr(a, "model_dump", None)
                        if callable(dump):
                            try:
                                payload = dump()
                            except Exception:
                                payload = None
                            break
                    record_failure(endpoint, str(detail), payload)
                    raise
            last = last_save()
            if isinstance(res, dict) and res.get("ok") and not _is_dry_run(res):
                if last is not None:
                    res = {
                        **res,
                        "saved": bool(last["saved"]),
                        "save_reason": last["reason"],
                        "save_backup": last.get("backup"),
                    }
                    if not last["saved"]:
                        record_failure(endpoint, last["reason"])
                else:
                    res = {**res, "saved": True, "save_reason": "no database write was needed"}
            return res

        return wrapper

    return deco


def _is_dry_run(res: dict[str, Any]) -> bool:
    if res.get("dry_run"):
        return True
    inner = res.get("result")
    return isinstance(inner, dict) and bool(inner.get("dry_run"))
