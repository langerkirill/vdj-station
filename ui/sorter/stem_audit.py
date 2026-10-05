"""Background stem vocal-hole audits for the Music Sorter Stems tab."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .autocue_path import ensure_autocue_on_path
from .config import DJ_NOTES_ROOT, MUSIC_ROOT, VDJ_DATABASE

ensure_autocue_on_path()

from vdj_cuer.stem_vocal_holes import (  # noqa: E402
    audit_stem_file,
    cued_audio_paths_from_database,
    list_stem_audio_paths,
    StemVocalAudit,
)

SNAPSHOT_PATH = DJ_NOTES_ROOT / "stem_vocal_audit.json"
VALID_SCOPES = {"all", "cued"}

_jobs: dict[str, "StemAuditJob"] = {}
_cancel: set[str] = set()
_jobs_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def assert_under_music_root(path: Path) -> Path:
    """Refuse anything outside DJ Music (sidecars and audio)."""
    root = MUSIC_ROOT.expanduser().resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path must be under DJ Music") from exc
    return resolved


def stem_inventory(root: Path | None = None) -> dict[str, Any]:
    base = Path(root) if root is not None else MUSIC_ROOT
    base = base.expanduser().resolve()
    paths = list_stem_audio_paths(base) if base.is_dir() else []
    scanned = _load_scanned_index()
    unscanned = sum(1 for audio in paths if not _is_already_scanned(audio, scanned))
    return {
        "root": str(base),
        "sidecar_count": len(paths),
        "scanned_count": max(0, len(paths) - unscanned),
        "unscanned_count": unscanned,
    }


def _sidecar_from_user_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.name.endswith(".vdjstems"):
        sidecar = path.resolve()
    else:
        sidecar = Path(f"{path.resolve()}.vdjstems")
    assert_under_music_root(sidecar)
    if not sidecar.name.endswith(".vdjstems"):
        raise ValueError("Refusing to delete a non-stem file")
    if not sidecar.is_file():
        raise ValueError(f"No .vdjstems sidecar for {path}")
    return sidecar


def delete_stem_sidecars(paths: list[str]) -> dict[str, Any]:
    """Unlink .vdjstems sidecars only. Never deletes audio."""
    deleted = 0
    removed: list[str] = []
    skipped: list[str] = []
    last_error: Exception | None = None
    for raw in paths:
        try:
            sidecar = _sidecar_from_user_path(raw)
        except ValueError as exc:
            skipped.append(str(raw))
            last_error = exc
            continue
        sidecar.unlink()
        deleted += 1
        removed.append(str(sidecar))
    if deleted == 0 and last_error is not None:
        raise last_error
    if removed:
        _forget_deleted_sidecars(set(removed))
    return {"deleted": deleted, "paths": removed, "skipped": skipped}


def check_one_stem(raw: str) -> dict[str, Any]:
    path = Path(raw).expanduser().resolve()
    assert_under_music_root(path)
    if path.name.endswith(".vdjstems"):
        audio = path.with_name(path.name[: -len(".vdjstems")])
    else:
        audio = path
    return audit_stem_file(audio).to_dict()


def list_job_targets(
    scope: str,
    root: Path,
    *,
    skip_scanned: bool = True,
) -> list[Path]:
    base = root.expanduser().resolve()
    if scope == "cued":
        targets: list[Path] = []
        if VDJ_DATABASE.is_file():
            for raw in cued_audio_paths_from_database(VDJ_DATABASE):
                audio = Path(raw)
                if not audio.is_file():
                    continue
                if not Path(f"{audio}.vdjstems").is_file():
                    continue
                try:
                    audio.resolve().relative_to(base)
                except ValueError:
                    continue
                targets.append(audio)
    else:
        targets = list_stem_audio_paths(base)
    if not skip_scanned:
        return targets
    scanned = _load_scanned_index()
    return [audio for audio in targets if not _is_already_scanned(audio, scanned)]


@dataclass
class StemAuditJob:
    id: str
    status: str
    scope: str
    root: str
    total: int = 0
    checked: int = 0
    skipped: int = 0
    ok_count: int = 0
    broken_count: int = 0
    error_count: int = 0
    skip_scanned: bool = True
    current_path: str = ""
    message: str = ""
    broken: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    scanned: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "scope": self.scope,
            "root": self.root,
            "total": self.total,
            "checked": self.checked,
            "skipped": self.skipped,
            "ok_count": self.ok_count,
            "broken_count": self.broken_count,
            "error_count": self.error_count,
            "skip_scanned": self.skip_scanned,
            "scanned_count": len(self.scanned),
            "current_path": self.current_path,
            "message": self.message,
            "broken": list(self.broken),
            "errors": list(self.errors),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


def get_stem_audit_job(job_id: str) -> Optional[dict[str, Any]]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    return job.to_dict() if job else None


def latest_stem_audit_job() -> Optional[dict[str, Any]]:
    with _jobs_lock:
        live = max(_jobs.values(), key=lambda j: j.created_at) if _jobs else None
    if live:
        return live.to_dict()
    snap = _load_snapshot_job()
    return _mark_orphaned_snapshot(snap) if snap else None


def cancel_stem_audit_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        _cancel.add(job_id)
        if job.status in {"queued"}:
            job.status = "cancelled"
            job.message = "Cancelled"
            job.finished_at = time.time()
    return job.to_dict()


def start_stem_audit_job(
    *,
    scope: str = "all",
    root: str | Path | None = None,
    workers: int = 1,
    skip_scanned: bool = True,
) -> dict[str, Any]:
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unknown stem audit scope: {scope}")
    base = Path(root).expanduser().resolve() if root else MUSIC_ROOT.expanduser().resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Scan root missing: {base}")
    _ = max(1, int(workers or 1))
    previous_scanned = _load_scanned_index() if skip_scanned else {}
    previous_job = _load_snapshot_job() if skip_scanned else None
    with _jobs_lock:
        for existing in _jobs.values():
            if existing.status in {"running", "queued"}:
                return existing.to_dict()
        job = StemAuditJob(
            id=uuid.uuid4().hex[:12],
            status="queued",
            scope=scope,
            root=str(base),
            skip_scanned=bool(skip_scanned),
            created_at=time.time(),
            message="Queued stem scan…",
            scanned=dict(previous_scanned),
        )
        if skip_scanned and previous_job:
            job.broken = [
                dict(row)
                for row in (previous_job.get("broken") or [])
                if isinstance(row, dict)
            ]
            job.errors = [
                dict(row)
                for row in (previous_job.get("errors") or [])
                if isinstance(row, dict)
            ]
            job.broken_count = len(job.broken)
            job.error_count = len(job.errors)
            job.ok_count = int(previous_job.get("ok_count") or 0)
        _jobs[job.id] = job
    thread = threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"stem-audit-{job.id}")
    thread.start()
    return job.to_dict()


def _sidecar_mtime_ns(audio: Path) -> int:
    sidecar = Path(f"{audio}.vdjstems") if not str(audio).endswith(".vdjstems") else audio
    try:
        return int(sidecar.stat().st_mtime_ns)
    except OSError:
        return 0


def _is_already_scanned(audio: Path, scanned: dict[str, dict[str, Any]]) -> bool:
    key = _sidecar_key(f"{audio}.vdjstems")
    prev = scanned.get(key) or {}
    if not prev:
        return False
    return int(prev.get("mtime_ns") or 0) == _sidecar_mtime_ns(audio)


def _load_scanned_index() -> dict[str, dict[str, Any]]:
    with _jobs_lock:
        live = max(_jobs.values(), key=lambda j: j.created_at) if _jobs else None
        if live and live.scanned:
            return dict(live.scanned)
    snap = _read_snapshot()
    scanned = snap.get("scanned") if isinstance(snap, dict) else None
    if isinstance(scanned, dict):
        return {
            str(key): dict(val)
            for key, val in scanned.items()
            if isinstance(val, dict)
        }
    return {}


def _drop_sidecar_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        if _row_sidecar_key(row) == key:
            continue
        kept.append(row)
    return kept


def _record(job: StemAuditJob, report: StemVocalAudit) -> None:
    payload = report.to_dict()
    key = _sidecar_key(report.stems_path or f"{report.audio_path}.vdjstems")
    mtime = _sidecar_mtime_ns(Path(report.audio_path))
    status = "broken" if report.broken else "error" if report.error else "ok"
    with _jobs_lock:
        prev_status = (job.scanned.get(key) or {}).get("status")
        job.checked += 1
        job.current_path = report.audio_path
        job.scanned[key] = {"mtime_ns": mtime, "status": status}
        if report.broken:
            job.broken = _drop_sidecar_rows(job.broken, key)
            job.broken.append(payload)
            job.errors = _drop_sidecar_rows(job.errors, key)
            if prev_status == "ok":
                job.ok_count = max(0, job.ok_count - 1)
        elif report.error:
            job.errors = _drop_sidecar_rows(job.errors, key)
            job.errors.append(payload)
            job.broken = _drop_sidecar_rows(job.broken, key)
            if prev_status == "ok":
                job.ok_count = max(0, job.ok_count - 1)
        else:
            job.broken = _drop_sidecar_rows(job.broken, key)
            job.errors = _drop_sidecar_rows(job.errors, key)
            if prev_status != "ok":
                job.ok_count += 1
        job.broken_count = len(job.broken)
        job.error_count = len(job.errors)
        job.message = f"{job.checked}/{job.total} · {Path(report.audio_path).name}"


def _persist(job: StemAuditJob) -> None:
    payload = {
        "generated_at": _now_iso(),
        "checked": job.checked,
        "broken": job.broken_count,
        "errors": job.error_count,
        "scanned": dict(job.scanned),
        "tracks": list(job.broken) + list(job.errors),
        "job": job.to_dict(),
    }
    try:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _sidecar_key(raw: str | None) -> str:
    if not raw:
        return ""
    path = Path(str(raw)).expanduser()
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _row_sidecar_key(row: dict[str, Any]) -> str:
    stems = row.get("stems_path") or ""
    if stems:
        return _sidecar_key(str(stems))
    audio = row.get("audio_path") or ""
    if audio:
        return _sidecar_key(f"{audio}.vdjstems")
    return ""


def _drop_sidecars_from_rows(
    rows: list[dict[str, Any]],
    gone: set[str],
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        key = _row_sidecar_key(row)
        if key and key in gone:
            continue
        kept.append(row)
    return kept


def _forget_deleted_sidecars(gone: set[str]) -> None:
    gone_keys = {_sidecar_key(p) for p in gone}
    with _jobs_lock:
        jobs = list(_jobs.values())
    for job in jobs:
        with _jobs_lock:
            job.broken = _drop_sidecars_from_rows(job.broken, gone_keys)
            job.errors = _drop_sidecars_from_rows(job.errors, gone_keys)
            job.broken_count = len(job.broken)
            job.error_count = len(job.errors)
            for key in gone_keys:
                job.scanned.pop(key, None)
    live = max(jobs, key=lambda j: j.created_at) if jobs else None
    if live:
        _persist(live)
        return
    snap = _load_snapshot_job()
    if not snap:
        return
    snap["broken"] = _drop_sidecars_from_rows(list(snap.get("broken") or []), gone_keys)
    snap["errors"] = _drop_sidecars_from_rows(list(snap.get("errors") or []), gone_keys)
    snap["broken_count"] = len(snap["broken"])
    snap["error_count"] = len(snap["errors"])
    fake = StemAuditJob(
        id=str(snap.get("id") or "saved"),
        status=str(snap.get("status") or "ok"),
        scope=str(snap.get("scope") or "all"),
        root=str(snap.get("root") or MUSIC_ROOT),
        total=int(snap.get("total") or snap.get("checked") or 0),
        checked=int(snap.get("checked") or 0),
        ok_count=int(snap.get("ok_count") or 0),
        broken_count=int(snap["broken_count"]),
        error_count=int(snap["error_count"]),
        message=str(snap.get("message") or ""),
        broken=list(snap["broken"]),
        errors=list(snap["errors"]),
        created_at=float(snap.get("created_at") or 0),
        finished_at=float(snap.get("finished_at") or 0),
    )
    _persist(fake)


def _mark_orphaned_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") not in {"running", "queued"}:
        return job
    with _jobs_lock:
        live_busy = {
            j.id
            for j in _jobs.values()
            if j.status in {"running", "queued"}
        }
    if job.get("id") in live_busy:
        return job
    marked = dict(job)
    marked["status"] = "ok"
    marked["message"] = "Scan stopped — last results kept."
    return marked


def _read_snapshot() -> Optional[dict[str, Any]]:
    if not SNAPSHOT_PATH.is_file():
        return None
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_snapshot_job() -> Optional[dict[str, Any]]:
    data = _read_snapshot()
    if not data:
        return None
    job = data.get("job")
    if isinstance(job, dict):
        if "scanned" not in job and isinstance(data.get("scanned"), dict):
            job = dict(job)
            job["scanned"] = data["scanned"]
        return job
    tracks = data.get("tracks") or []
    if not isinstance(tracks, list):
        return None
    broken = [t for t in tracks if isinstance(t, dict) and t.get("broken")]
    errors = [t for t in tracks if isinstance(t, dict) and t.get("error")]
    checked = int(data.get("checked") or 0)
    return {
        "id": "saved",
        "status": "ok",
        "scope": "all",
        "root": str(MUSIC_ROOT),
        "total": checked,
        "checked": checked,
        "ok_count": max(0, checked - len(broken) - len(errors)),
        "broken_count": int(data.get("broken") or len(broken)),
        "error_count": int(data.get("errors") or len(errors)),
        "current_path": "",
        "message": f"Last saved scan · {data.get('generated_at') or ''}".strip(),
        "broken": broken,
        "errors": errors,
        "created_at": 0.0,
        "finished_at": 0.0,
    }


def _finish(job: StemAuditJob, status: str, message: str) -> None:
    job.message = message
    job.finished_at = time.time()
    job.status = status
    _cancel.discard(job.id)
    _persist(job)


def _run_job(job: StemAuditJob) -> None:
    if job.id in _cancel:
        _finish(job, "cancelled", "Cancelled")
        return
    job.status = "running"
    job.message = "Listing .vdjstems…"
    try:
        all_targets = list_job_targets(
            job.scope, Path(job.root), skip_scanned=False
        )
        targets = list_job_targets(
            job.scope, Path(job.root), skip_scanned=job.skip_scanned
        )
        job.skipped = max(0, len(all_targets) - len(targets))
        job.total = len(targets)
        if not targets:
            already = job.skipped or len(job.scanned)
            _finish(job, "ok", f"Nothing new · {already} already checked")
            return
        job.message = f"0/{job.total}"
        for path in targets:
            if job.id in _cancel:
                _finish(job, "cancelled", "Cancelled")
                return
            _record(job, audit_stem_file(path))
        extra = f" · {job.skipped} skipped" if job.skipped else ""
        _finish(
            job,
            "ok",
            (
                f"{job.broken_count} broken · {job.ok_count} ok · "
                f"{job.error_count} errors · {job.checked} new{extra}"
            ),
        )
    except Exception as exc:
        _finish(job, "error", str(exc)[:300])
