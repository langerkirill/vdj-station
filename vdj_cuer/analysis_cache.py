"""Durable AutoCue analysis cache — skip Gemini when the file has not changed."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

CACHE_SCHEMA = 2  # loop stability thirds + skip-kick wrap; 0-loop caches must miss
DEFAULT_CACHE_DIR = (
    Path.home() / "Music" / "DJ" / "Music" / "Cues" / ".cache" / "autocue-analysis"
)
# Keep in sync with vdj_cuer.common.TARGET_MIN_LOOPS (avoid importing common here).
MIN_CACHED_LOOPS = 2

# The cached analysis is the finalized result: Gemini's answer PLUS the local stem
# measurements (vocal/drum evidence, gates, precision snap). A cache entry made by older
# code would silently keep old stem decisions after an update (2026-10-08: a re-cue of
# California Dreaming in 8 s reproduced the 10-04 cues). Entries carry a hash of these
# modules' source; any change -> one fresh analysis per song.
_ANALYSIS_MODULES = (
    "gemini_analysis.py", "stems.py", "stem_evidence.py", "stem_color.py", "vocal_gate.py",
    "precision_gate.py", "cue_hygiene.py", "loop_seam_dsp.py", "loop_seam_gemini.py",
    "analysis_postprocess.py", "stem_cue_plan.py", "stem_vocal_holes.py", "common.py",
    "analysis_cache.py",
)
_CODE_ID: Optional[str] = None


def analysis_code_files() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [here / name for name in _ANALYSIS_MODULES if (here / name).is_file()]


def analysis_code_id() -> str:
    """Short hash of the analysis-stage source files (changes on any edit to them)."""
    global _CODE_ID
    if _CODE_ID is None:
        digest = hashlib.sha1()
        for path in analysis_code_files():
            digest.update(path.name.encode("utf-8") + b"\0")
            try:
                digest.update(path.read_bytes())
            except OSError:
                pass
        _CODE_ID = digest.hexdigest()[:12]
    return _CODE_ID


def analysis_is_usable(analysis: Any) -> bool:
    if not isinstance(analysis, dict):
        return False
    cues = analysis.get("measure_changes") or []
    loops = analysis.get("loop_segments") or []
    return bool(cues) or bool(loops)


def analysis_ready_to_reuse(analysis: Any) -> bool:
    """Reuse only analyses that already met the 2-loop bar.

    Caching a 6-cue / 0-loop result skipped stem re-scans, so gate fixes never
    ran on Make You Feel / Swimmers.
    """
    if not analysis_is_usable(analysis):
        return False
    loops = analysis.get("loop_segments") or []
    return len(loops) >= MIN_CACHED_LOOPS


def _env_flag(name: str) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def cache_enabled() -> bool:
    if _env_flag("AUTOCUE_DISABLE_ANALYSIS_CACHE"):
        return False
    return True


def refresh_requested(explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return _env_flag("AUTOCUE_REFRESH_ANALYSIS")


def _fingerprint(audio_path: str | Path) -> Optional[dict[str, Any]]:
    path = Path(audio_path).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    if not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    stems = Path(f"{path}.vdjstems")
    stems_mtime = 0
    if stems.is_file():
        try:
            stems_mtime = int(stems.stat().st_mtime_ns)
        except OSError:
            stems_mtime = 0
    return {
        "schema": CACHE_SCHEMA,
        "path": str(path),
        "audio_mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
        "audio_size": int(stat.st_size),
        "stems_mtime_ns": stems_mtime,
    }


def cache_file_for(audio_path: str | Path, cache_dir: Path | None = None) -> Path:
    resolved = str(Path(audio_path).expanduser().resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()
    return (Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR) / f"{digest}.json"


def load_cached_analysis(
    audio_path: str | Path,
    *,
    model: Optional[str] = None,
    cache_dir: Path | None = None,
    refresh: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    if refresh_requested(refresh) or not cache_enabled():
        return None
    fingerprint = _fingerprint(audio_path)
    if fingerprint is None:
        return None
    path = cache_file_for(audio_path, cache_dir)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    for key in ("path", "audio_mtime_ns", "audio_size", "stems_mtime_ns"):
        if record.get(key) != fingerprint.get(key):
            return None
    if model and record.get("model") and str(record.get("model")) != str(model):
        return None
    analysis = record.get("analysis")
    if not analysis_ready_to_reuse(analysis):
        return None
    if record.get("schema") != CACHE_SCHEMA:
        return None
    if record.get("code") != analysis_code_id():
        print("📦 Cached analysis was made by older AutoCue code; analyzing again")
        return None
    return analysis


def save_cached_analysis(
    audio_path: str | Path,
    analysis: Any,
    *,
    model: Optional[str] = None,
    cache_dir: Path | None = None,
) -> Optional[Path]:
    if not cache_enabled() or not analysis_ready_to_reuse(analysis):
        return None
    fingerprint = _fingerprint(audio_path)
    if fingerprint is None:
        return None
    dest = cache_file_for(audio_path, cache_dir)
    record = {
        **fingerprint,
        "model": model,
        "code": analysis_code_id(),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "analysis": analysis,
    }
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(dest)
    except OSError:
        return None
    return dest


def analyze_with_cache(
    analyze: Callable[[str], Any],
    audio_path: str | Path,
    *,
    model: Optional[str] = None,
    cache_dir: Path | None = None,
    refresh: Optional[bool] = None,
) -> Any:
    """Return a cached analysis or call analyze() and store a usable result."""
    path = str(audio_path)
    hit = load_cached_analysis(
        path, model=model, cache_dir=cache_dir, refresh=refresh
    )
    if hit is not None:
        print("📦 Reusing cached AutoCue analysis (no Gemini upload)")
        return hit
    result = analyze(path)
    if analysis_is_usable(result):
        save_cached_analysis(path, result, model=model, cache_dir=cache_dir)
    return result
