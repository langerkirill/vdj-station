#!/usr/bin/env python3
"""Local Music Sorter — sort cued tracks + review Add Cues before Ready for Sort."""

from __future__ import annotations

import logging
import mimetypes
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

# HOUSE FORK: profile + read-only guards load FIRST so nothing can write before
# the filesystem guard is armed.
from sorter import profile as _profile
from sorter import fs_guard as _fs_guard
from sorter import sorted_state as _sorted_state

_fs_guard.install()

from sorter.config import (
    DJ_NOTES_ROOT,
    ADD_CUES,
    CUE_STAGES,
    CUES_ROOT,
    CUES_SORTED,
    LIBRARIES,
    MIXES_ROOT,
    READY_FOR_SORT,
    SETS_ROOT,
    VDJ_DATABASE,
)
from sorter.library import (
    add_cues_section,
    cached_placement_indexes,
    create_folder,
    find_cues_sorted_matches,
    find_library_matches,
    find_set_matches,
    invalidate_placement_indexes,
    add_cues_tracks_by_crate,
    drop_add_cues_vdj_ghosts,
    inode_keys_for_paths,
    is_pajamathon_set_audio,
    list_add_cues_tracks,
    list_pajamathon_set_tracks,
    is_must_play_folder_path,
    list_all_set_tracks,
    list_libraries,
    list_library_tree,
    list_ready_tracks,
)

UI_BUILD = "20261005-house-sauna-fest11"
from sorter.pajamathon_set_sync import sync_pajamathon_set_deletes
from sorter.recommend import get_recommender
from sorter.safe_write import (
    session_failures as _session_save_failures,
    with_save_status,
)
from sorter import deleted_markers as _deleted_markers
from sorter import house_folders as _house_folders
from sorter import llm as sorter_llm
from sorter.autocue_path import ensure_autocue_on_path
from sorter.cue_readiness import assess_cue_readiness
from sorter.lanes import lane_from_user_color
from sorter.set_must_play import (
    has_must_play,
    mark_must_play,
    must_play_file_paths,
)
from sorter.set_approval import (

    apply_set_review_status,
    approve_set_cues,
    approved_file_paths,
    has_approval,
    is_approved,
    revoke_set_approval,
)
from sorter.relocate import (
    add_track_to_event_set,
    add_track_to_must_play,
    copy_cues_to_placement,
    copy_cues_to_placements,
    delete_add_cues_track,
    delete_library_placement,
    demote_ready_to_add_cues,
    is_virtualdj_running,
    promote_add_cues_track,
    remove_from_ready_for_sort,
    remove_set_copy,
    send_set_copy_to_add_cues,
    sort_track,
    summarize_cues,
    summarize_cues_for_paths,
)

ensure_autocue_on_path()
# Load GEMINI_API_KEY from Desktop/src .env paths at UI boot (not only CWD).
try:
    from vdj_cuer.common import load_gemini_api_key  # noqa: E402

    load_gemini_api_key()
except Exception:
    pass
from vdj_database_safety import (  # noqa: E402
    ensure_healthy_vdj_database,
    quick_database_fingerprint,
    snapshot_last_good_database,
)
from sorter.action_log import (
    HISTORICAL_SORTS_2026_07_28,
    append_action,
    log_path,
    read_actions,
    seed_historical_sorts,
)
from sorter.audio_meta import probe_audio_meta
from sorter.autocue_retry import (
    get_batch,
    get_job,
    list_batches,
    list_jobs,
    max_concurrent_jobs,
    restore_jobs,
    retry_history_for_path,
    start_batch_retry_cues,
    start_retry_cues,
    summarize_retry_history,
)
from sorter.bpm_edit import halve_track_bpm
from sorter.cue_edit import (
    add_cue_point,
    add_loop_point,
    delete_cue_point,
    restore_poi_point,
    scale_loop_point,
    set_cue_jumpable,
    set_poi_color,
    set_poi_position,
)
from sorter.grid_batch import (
    attempt_grid_align,
    get_grid_fix_batch,
    list_grid_fix_batches,
    start_batch_grid_fix,
)
from sorter.grid_edit import set_beatgrid_anchor
from sorter.grid_preflight import assess_grid_for_autocue, preflight_from_cues
from sorter.notes_edit import set_track_comment
from sorter.poi_rename import set_poi_name
from sorter.undo import undo_action
from sorter.waveform import build_waveform
from sorter.practice_sets import (
    all_tracks_across_mixes,
    get_practice_set_detail,
    list_practice_mixes,
)
from sorter.practice_analyze import get_analyze_job, start_analyze_job
from sorter.stem_audit import (
    cancel_stem_audit_job,
    check_one_stem,
    delete_stem_sidecars,
    get_stem_audit_job,
    latest_stem_audit_job,
    start_stem_audit_job,
    stem_inventory,
)
from sorter.live_set_played import filter_best_items_hide_live_played
from sorter.transitions_db import (
    annotate_mixes_exclude_from_best,
    ensure_database,
    list_best_practice_scores,
    lookup_options,
    rebuild_database,
    set_practice_mix_exclude,
    update_practice_score,
)

log = logging.getLogger("music-sorter")
app = FastAPI(title="Music Sorter", version="0.2.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _seed_action_log() -> None:
    """Startup hooks. House fork: no restore_jobs / sideview watch / historical seed."""
    import sys

    import vdj_database_safety as _safety

    print(
        "[house-fork] profile=%s readonly=%s notes=%s\n"
        "[house-fork] app.py        = %s\n"
        "[house-fork] sorter.config = %s\n"
        "[house-fork] vdj_database_safety = %s\n"
        "[house-fork] vdj_cuer      = %s"
        % (
            _profile.PROFILE,
            _profile.readonly(),
            DJ_NOTES_ROOT,
            __file__,
            sys.modules["sorter.config"].__file__,
            _safety.__file__,
            getattr(sys.modules.get("vdj_cuer"), "__file__", "(not imported)"),
        ),
        flush=True,
    )
    if _profile.IS_HOUSE:
        # Skipped on purpose: restore_jobs (would resume AutoCue jobs), the
        # Sideview recs watcher (writes VDJ My Lists), seed_historical_sorts.
        print("[house-fork] skipped startup hooks: restore_jobs, sideview watch, seed_historical_sorts", flush=True)
        return
    try:
        seed_historical_sorts(HISTORICAL_SORTS_2026_07_28)
    except OSError:
        pass
    try:
        from sorter.vdj_sideview_watch import start_sideview_recs_watch

        start_sideview_recs_watch()
    except Exception:
        pass
    try:
        n = restore_jobs(resume=True)
        if n:
            print(f"AutoCue: resumed {n} persisted job(s)")
    except Exception as exc:
        print(f"⚠️  AutoCue job restore failed: {exc}")


# --- HOUSE FORK: read-only enforcement ------------------------------------
# POST endpoints with no dry-run mode are refused outright while read-only.
READONLY_BLOCKED_POSTS = {
    "/api/folders",
    "/api/undo",
    "/api/grid-fix/batch",
    "/api/grid-align/attempt",
    "/api/set-beatgrid",
    "/api/scale-loop",
    "/api/set-cue-color",
    "/api/move-poi",
    "/api/add-cue",
    "/api/add-loop",
    "/api/rename-poi",
    "/api/delete-cue",
    "/api/notes",
    "/api/halve-bpm",
    "/api/retry-cues",
    "/api/retry-cues/batch",
    "/api/approve-set-cues",
    "/api/must-play-set",
    "/api/stems/delete",
    "/api/stems/audit",
    "/api/stems/check",
    "/api/assemble/export",
}


@app.middleware("http")
async def _readonly_gate(request: Request, call_next):
    if (
        _profile.readonly()
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.url.path in READONLY_BLOCKED_POSTS
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": _profile.READONLY_MESSAGE,
                "readonly": True,
                "blocked": request.url.path,
            },
        )
    return await call_next(request)


class _DryRunWhenReadonly(BaseModel):
    """Request base: forces dry_run=True while MUSIC_SORTER_READONLY is on."""

    @model_validator(mode="after")
    def _force_dry_run(self):
        if _profile.readonly() and "dry_run" in type(self).model_fields:
            object.__setattr__(self, "dry_run", True)
        return self


class SortDestination(BaseModel):
    """One House/Zouk folder target (relative path under that library root)."""

    library: str  # House | Zouk | Both (Both expands to both libs at this folder)
    relative_folder: str
    new_folder: bool = False  # House fork: relative_folder is a NEW folder to create on first sort


class SortRequest(_DryRunWhenReadonly):
    path: str
    # Optional when relative_folder is set. Color paints after sort.
    lane: str = ""
    # Legacy single-target fields (still supported).
    library: str = "House"
    relative_folder: str = ""
    # Preferred: one or more destinations (House and/or Zouk, any folders).
    destinations: Optional[list[SortDestination]] = None
    dry_run: bool = False
    allow_vdj_running: bool = False
    # House fork: every sort ALSO copies into Sets/Sauna Fest/<same subfolder> (toggle in the UI).
    also_sauna_fest: bool = True
    # TEST ONLY: a scratch set folder name (must start with 'ZZ TEST '); the UI never sends it.
    set_folder_name: str = ""


class SaunaFestSortRequest(SortRequest):
    pass


class CreateFolderRequest(BaseModel):
    library: str
    name: str
    parent_relative_path: str = ""


class RecommendRequest(BaseModel):
    path: str
    preferred_library: Optional[str] = None
    force: bool = False


class PromoteRequest(_DryRunWhenReadonly):
    path: str
    destination_stage: str = "ready_for_sort"
    dry_run: bool = False
    allow_vdj_running: bool = False
    require_cued: Optional[bool] = None


class RemoveReadyRequest(_DryRunWhenReadonly):
    path: str
    dry_run: bool = False
    to_trash: bool = True
    allow_vdj_running: bool = False
    create_backup: bool = True
    remove_from_database: bool = True


class DeleteAddCuesRequest(_DryRunWhenReadonly):
    """Trash/delete an Add Cues track + remove its VDJ Song (cues/loops)."""

    path: str
    dry_run: bool = False
    to_trash: bool = True
    allow_vdj_running: bool = False


class RemoveSetCopyRequest(_DryRunWhenReadonly):
    """Delete the Sets/Pajamathon copy only — siblings stay."""

    path: str
    dry_run: bool = False
    to_trash: bool = True
    allow_vdj_running: bool = False


class SendBackSetRequest(_DryRunWhenReadonly):
    """Relocate a Sets/Pajamathon copy to Add Cues/Pajamathon."""

    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False


class DeletePlacementRequest(_DryRunWhenReadonly):
    """Delete a House/Zouk/Cues Sorted copy + its VDJ Song (cues/loops)."""

    path: str  # full path of the library/archive placement
    dry_run: bool = False
    to_trash: bool = True
    allow_vdj_running: bool = False


class CopyCuesRequest(_DryRunWhenReadonly):
    """Copy Ready/Add Cues markers onto an existing library or Sets copy."""

    source: str
    dest: str
    overwrite: bool = False
    dry_run: bool = False
    allow_vdj_running: bool = False
    create_backup: bool = True


class CopyCuesAllRequest(_DryRunWhenReadonly):
    """Copy Ready/Add Cues markers onto every listed library/archive/Sets copy."""

    source: str
    dests: list[str]
    overwrite: bool = False
    dry_run: bool = False
    allow_vdj_running: bool = False
    create_backup: bool = True


class AddToSetRequest(_DryRunWhenReadonly):
    """Copy a Ready/Add Cues track into Sets/Pajamathon + clone VDJ cues."""

    path: str
    event_name: str = ""
    dry_run: bool = False
    allow_vdj_running: bool = False
    create_backup: bool = True


class DemoteReadyRequest(_DryRunWhenReadonly):
    """Kick a Ready for Sort track back to Add Cues."""

    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False
    subfolder: str = "Back from Ready"


class RetryCuesRequest(_DryRunWhenReadonly):
    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False
    require_grid: bool = True
    deep_grid_check: bool = True
    # all/both | cues | loops — mirrors AutoCue --cues-only / --loops-only
    write_scope: str = "all"


class BatchRetryCuesRequest(_DryRunWhenReadonly):
    """Batch AutoCue. Prefer paths, or filter=not_cued to take current Add Cues queue."""

    paths: list[str] = []
    filter: Optional[str] = None  # not_cued | pajamathon_not_cued | pajamathon_needs_loops
    dry_run: bool = False
    allow_vdj_running: bool = False
    require_grid: bool = True
    deep_grid_check: bool = False
    write_scope: str = "all"
    model_name: Optional[str] = None


class UndoRequest(_DryRunWhenReadonly):
    action_id: str
    dry_run: bool = False
    allow_vdj_running: bool = False


class GridPreflightRequest(BaseModel):
    path: str
    deep: bool = True


class DeleteCueRequest(_DryRunWhenReadonly):
    path: str
    kind: str  # "cue" | "loop"
    pos: float
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None  # VDJ loop Slot — helps when Num is always -1
    dry_run: bool = False
    allow_vdj_running: bool = False


class RestoreMarkerRequest(_DryRunWhenReadonly):
    """Undo a delete: put back the exact marker remembered under ``id``."""

    id: str
    dry_run: bool = False
    allow_vdj_running: bool = False


class ScaleLoopRequest(_DryRunWhenReadonly):
    """Halve or double a loop's Size (beats) in VirtualDJ."""

    path: str
    pos: float
    factor: float  # 0.5 = half, 2.0 = double
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class SetCueColorRequest(_DryRunWhenReadonly):
    """Change Color on one cue or loop POI."""

    path: str
    kind: str  # cue | loop
    pos: float
    color: str  # blue | lightblue | green | purple | yellow | orange (or raw VDJ int)
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class MovePoiRequest(_DryRunWhenReadonly):
    """Move a cue or loop to a new start time (seconds)."""

    path: str
    kind: str  # cue | loop
    pos: float  # current position (to find the POI)
    new_pos: float  # new start time
    num: Optional[str] = None
    name: Optional[str] = None
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class AddCueRequest(_DryRunWhenReadonly):
    """Place a new cue at a time (seconds). Snapping is done by the client."""

    path: str
    pos: float
    name: Optional[str] = None
    color: str = "green"
    dry_run: bool = False
    allow_vdj_running: bool = False


class AddLoopRequest(_DryRunWhenReadonly):
    """Place a new loop at a time (seconds). Snapping is done by the client."""

    path: str
    pos: float
    name: Optional[str] = None
    color: str = "green"
    beats: float = 8.0
    dry_run: bool = False
    allow_vdj_running: bool = False


class RenamePoiRequest(_DryRunWhenReadonly):
    """Rename a cue or loop Name attribute in VirtualDJ."""

    path: str
    kind: str  # cue | loop
    pos: float
    new_name: str
    num: Optional[str] = None
    name: Optional[str] = None  # current name (for matching)
    slot: Optional[str] = None
    dry_run: bool = False
    allow_vdj_running: bool = False


class ApproveSetCuesRequest(BaseModel):
    """Human sign-off for a Sets/Pajamathon file (does not move audio)."""

    path: str


class MustPlaySetRequest(BaseModel):
    """Must Play stamp for a Sets/Pajamathon file; copies into Must Play/."""

    path: str


class NotesRequest(_DryRunWhenReadonly):
    path: str
    comment: str = ""
    dry_run: bool = False
    allow_vdj_running: bool = True  # live typing often happens with VDJ open
    create_backup: bool = False


class HalveBpmRequest(_DryRunWhenReadonly):
    path: str
    dry_run: bool = False
    allow_vdj_running: bool = False
    # True = restore double-time (×2) if you halved by mistake
    double_instead: bool = False


class SetBeatgridRequest(_DryRunWhenReadonly):
    """Write a new downbeat time into VDJ Scan Phase + beatgrid POI."""

    path: str
    anchor_seconds: float
    dry_run: bool = False
    allow_vdj_running: bool = False


class GridFixBatchRequest(_DryRunWhenReadonly):
    """Analyze (and optionally write) BPM half + bar-1 phase for many tracks."""

    paths: list[str] = []
    filter: Optional[str] = None  # pajamathon
    apply: bool = True
    dry_run: bool = False
    allow_vdj_running: bool = False


class GridAlignAttemptRequest(_DryRunWhenReadonly):
    """Run automatic 1-finding on one track (preview or write)."""

    path: str
    apply: bool = False
    dry_run: bool = False
    allow_vdj_running: bool = False


def _placement_with_cue_status(
    hit: dict[str, Any],
    cue_index: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Attach VDJ cue status for a library / Cues Sorted path."""
    cues = (cue_index or {}).get(hit["path"]) if cue_index is not None else None
    if cues is None and cue_index is None:
        cues = summarize_cues(hit["path"])
    if cues is None:
        return {
            **hit,
            "is_cued": False,
            "in_database": False,
            "cue_count": 0,
            "loop_count": 0,
            "has_beatgrid": False,
            "bpm": None,
            "cue_status": "unknown",
            "points": [],
        }
    return {
        **hit,
        "is_cued": cues.is_cued,
        "in_database": cues.in_database,
        "cue_count": cues.cue_count,
        "loop_count": cues.loop_count,
        "has_beatgrid": cues.has_beatgrid,
        "bpm": cues.bpm,
        "cue_status": (
            "cued"
            if cues.is_cued
            else ("in_database_uncued" if cues.in_database else "missing_from_database")
        ),
        "points": [pt.to_dict() if hasattr(pt, "to_dict") else pt for pt in (cues.points or [])],
    }


def _autocue_match_payload(path: str, cues: Any) -> dict[str, Any]:
    try:
        from vdj_cuer.ml.match import assess_autocue_match
        return assess_autocue_match(path, cues)
    except Exception:
        return {
            "matches": False,
            "autocue_matches": False,
            "status": "unknown",
            "reason": "Compare unavailable",
        }


def _enrich_track(
    track_dict: dict[str, Any],
    *,
    review: bool = False,
    include_placements: bool = True,
    placement_index: Optional[dict[str, list[dict[str, str]]]] = None,
    set_index: Optional[dict[str, list[dict[str, str]]]] = None,
    cue_index: Optional[dict[str, Any]] = None,
    retry_history: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    cues = (cue_index or {}).get(track_dict["path"]) if cue_index is not None else None
    if cues is None:
        cues = summarize_cues(track_dict["path"])
    track_dict["cues"] = cues.to_dict()
    track_dict["is_cued"] = cues.is_cued
    user_color = getattr(cues, "user_color", "") or ""
    track_dict["user_color"] = user_color
    track_dict["lane"] = lane_from_user_color(user_color)
    # House fork: no lanes — a cued track is "unsorted" until it is copied into a House folder.
    track_dict["needs_sort"] = bool(cues.is_cued and not track_dict["lane"])
    # Surface BPM + Camelot key at the top level for the list rows / filters / sorts.
    track_dict["bpm"] = cues.bpm if cues.bpm else None
    track_dict["camelot"] = (cues.camelot or "") if cues.in_database else ""
    track_dict["key"] = getattr(cues, "key", "") or ""
    track_dict["status"] = (
        "cued"
        if cues.is_cued
        else ("in_database_uncued" if cues.in_database else "missing_from_database")
    )
    if include_placements:
        # Optional shared basename index avoids per-track library rglobs.
        cues_sorted = [
            _placement_with_cue_status(h, cue_index)
            for h in find_cues_sorted_matches(
                track_dict["name"], index=placement_index
            )
        ]
        library_hits = [
            _placement_with_cue_status(h, cue_index)
            for h in find_library_matches(
                track_dict["name"], index=placement_index
            )
        ]
        set_hits = [
            _placement_with_cue_status(h, cue_index)
            for h in find_set_matches(track_dict["name"], index=set_index)
        ]
        # Exclude the track's own Add Cues / Ready / House path if it
        # collides. Keep a Sets/Pajamathon self-hit — Add Cues Pajamathon
        # is often the event file itself, and hiding it looks like the
        # song is missing from the crate Finder just showed.
        src = str(Path(track_dict["path"]).expanduser().resolve())
        library_hits = [
            h
            for h in library_hits
            if str(Path(h["path"]).expanduser().resolve()) != src
        ]
        cues_sorted = [
            h
            for h in cues_sorted
            if str(Path(h["path"]).expanduser().resolve()) != src
        ]
        track_dict["placements"] = {
            "in_cues_sorted": len(cues_sorted) > 0,
            "cues_sorted": cues_sorted,
            "in_library": len(library_hits) > 0,
            "library": library_hits,
            "in_sets": len(set_hits) > 0,
            "sets": set_hits,
            "already_sorted": len(cues_sorted) > 0 or len(library_hits) > 0,
            "any_library_cued": any(h.get("is_cued") for h in library_hits),
            "any_archive_cued": any(h.get("is_cued") for h in cues_sorted),
            "any_set_cued": any(h.get("is_cued") for h in set_hits),
        }
    else:
        track_dict["placements"] = {
            "in_cues_sorted": False,
            "cues_sorted": [],
            "in_library": False,
            "library": [],
            "in_sets": False,
            "sets": [],
            "already_sorted": False,
            "any_library_cued": False,
            "any_archive_cued": False,
            "any_set_cued": False,
        }
    if review:
        readiness = assess_cue_readiness(cues)
        if is_pajamathon_set_audio(track_dict["path"]):
            approved = has_approval(track_dict["path"])
            track_dict["set_approved"] = approved
            track_dict["must_play"] = has_must_play(track_dict["path"])
            readiness = apply_set_review_status(
                readiness, approved=approved, is_cued=cues.is_cued
            )
        else:
            track_dict["set_approved"] = False
            track_dict["must_play"] = False
        track_dict["readiness"] = readiness
        # Fast structural grid preflight for list badges (no ffmpeg).
        track_dict["grid"] = preflight_from_cues(cues, track_dict["path"])
        match = _autocue_match_payload(track_dict["path"], cues)
        track_dict["autocue_match"] = match
        track_dict["autocue_matches"] = bool(match.get("matches"))
        hist = retry_history_for_path(track_dict["path"], retry_history or {})
        track_dict["retry_history"] = hist or {
            "kind": None,
            "tried_cues": False,
            "tried_loops": False,
            "tried_both": False,
            "scopes": [],
            "last_ts": None,
        }
    return track_dict



def _written_copy_match(track_dict: dict[str, Any]) -> dict[str, Any]:
    """Compare this file's cues to its Cues Sorted / library copy."""
    placements = track_dict.get("placements") or {}
    src = str(Path(track_dict.get("path") or "").expanduser().resolve())
    siblings = []
    for hit in list(placements.get("add_cues") or []) + list(placements.get("cues_sorted") or []) + list(placements.get("library") or []):
        try:
            if str(Path(hit.get("path") or "").expanduser().resolve()) == src:
                continue
        except Exception:
            continue
        siblings.append(hit)
    if not siblings:
        return {
            "matches": False,
            "status": "no_copy",
            "reason": "No Cues Sorted / library copy",
        }
    hit = next((h for h in siblings if h.get("is_cued")), siblings[0])
    cues = track_dict.get("cues") or {}
    actual = cues.get("points") or []
    written = hit.get("points") or []
    try:
        from vdj_cuer.ml.match import compare_cue_sets
        result = compare_cue_sets(
            actual,
            written,
            actual_loops=actual,
            proposed_loops=written,
            bpm=cues.get("bpm") or hit.get("bpm"),
        )
        if result.get("status") == "no_proposal":
            result["reason"] = "Written copy has no cues"
        elif result.get("status") == "match":
            result["reason"] = "Matches the written copy"
        elif result.get("status") == "near":
            result["reason"] = result.get("reason") or "Same cues, within a bar"
        return result
    except Exception:
        return {
            "matches": False,
            "status": "unknown",
            "reason": "Compare unavailable",
        }


def _assert_under_cues(path: Path) -> Path:
    audio = path.expanduser().resolve()
    allowed_roots = [CUES_ROOT.resolve(), MIXES_ROOT.resolve(), SETS_ROOT.resolve()]
    for root in allowed_roots:
        try:
            audio.relative_to(root)
            return audio
        except ValueError:
            continue
    raise HTTPException(
        status_code=403,
        detail="Audio must be under the Cues, Sets, or Mixes folder",
    )


def _cached_placement_indexes() -> tuple[
    dict[str, list[dict[str, str]]],
    dict[str, list[dict[str, str]]],
]:
    return cached_placement_indexes()


@app.get("/api/save-failures")
def get_save_failures() -> dict[str, Any]:
    """Edits refused/failed since this server started (also shown in the UI)."""
    return {"ok": True, "failures": _session_save_failures()}


@app.get("/api/house-folder-suggest")
def get_house_folder_suggest(tag: str = Query("")) -> dict[str, Any]:
    """What a Gemini tag chip may offer ('New folder: <Tag>' / existing similar / cap reached).
    Pure lookup: creates nothing; the folder only comes into being at Sort time."""
    return {"ok": True, **_house_folders.suggest_tag_folder(tag)}


@app.get("/api/house-folder-colors")
def get_house_folder_colors() -> dict[str, Any]:
    """Read-only: per-House-folder song colors (legend + chips) from house_folder_colors.json."""
    from sorter import house_colors

    table = house_colors.load()
    return {
        "ok": True,
        "exists": table["exists"],
        "folders": [{"folder": k, **v} for k, v in sorted(table["folders"].items())],
        "default": table["default"],
    }


@app.get("/api/new-house-folders")
def get_new_house_folders() -> dict[str, Any]:
    """How many 'New folder in House' folders were created (no cap)."""
    return {"ok": True, **_house_folders.new_folder_state()}


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Must stay cheap — UI boot waits on this before loading tracks."""
    return {
        "ok": True,
        "ready_for_sort": str(READY_FOR_SORT),
        "add_cues": str(ADD_CUES),
        "cues_root": str(CUES_ROOT),
        "ready_exists": READY_FOR_SORT.is_dir(),
        "vdj_database": str(VDJ_DATABASE),
        "vdj_database_exists": VDJ_DATABASE.is_file(),
        "virtualdj_running": is_virtualdj_running(),
        "libraries": list_libraries(),
        "stage_counts": {},
        "ui_build": UI_BUILD,
        "sets_root": str(SETS_ROOT),
        "profile": _profile.public_profile(),
        "readonly": _profile.readonly(),
        "gemini_model": sorter_llm.PREFERRED_SORTER_MODEL,
        "action_log": str(log_path()),
        "fs_guard": {
            k: v for k, v in _fs_guard.status().items() if k != "blocked_recent"
        },
        "code_root": str(Path(__file__).resolve().parent.parent),
    }


def _add_cues_work_tracks(crate: str = "all"):
    """Add Cues work items minus leftover hardlinks that have no VDJ Song.

    Used by the track list and by batch AutoCue / grid-fix so a ghost path
    cannot be queued after it is hidden from the UI.
    """
    add_tracks = add_cues_tracks_by_crate(crate)
    if _profile.IS_HOUSE:
        # House sort COPIES (original stays): hide what is already sorted (sorted.json).
        add_tracks = _sorted_state.drop_sorted(add_tracks)
    set_tracks = list_pajamathon_set_tracks()
    cue_index = summarize_cues_for_paths(
        [t.path for t in add_tracks] + [t.path for t in set_tracks]
    )
    add_tracks = drop_add_cues_vdj_ghosts(
        add_tracks,
        in_database_by_path={
            path: bool(summary.in_database) for path, summary in cue_index.items()
        },
        set_inodes=inode_keys_for_paths(t.path for t in set_tracks),
    )
    return add_tracks, set_tracks, cue_index


@app.get("/api/tracks")
def get_tracks(
    mode: str = Query("sort"),
    crate: Optional[str] = Query(
        None,
        description="add_cues only: limit to one top-level Add Cues folder (e.g. 'Sauna Fest House')",
    ),
) -> dict[str, Any]:
    """
    mode=sort → Ready for Sort (flat)
    mode=add_cues → Add Cues recursive review queue
    """
    if mode == "add_cues":
        # List load must stay fast. Skip House/Zouk rglob here — placements
        # load when a track is selected via /api/track-placements.
        add_tracks, set_tracks, cue_index = _add_cues_work_tracks("all")
        crate_names = sorted({t.group for t in add_tracks if t.group})
        if crate:
            add_tracks = [t for t in add_tracks if t.group == crate]
        raw = [t.to_dict() for t in add_tracks]
        seen = {t["path"] for t in raw}
        for ready in ([] if crate else list_ready_tracks()):
            payload = ready.to_dict()
            payload["section"] = "ready"
            if payload["path"] not in seen:
                raw.append(payload)
                seen.add(payload["path"])
        for set_track in ([] if (crate or _profile.IS_HOUSE) else set_tracks):
            payload = set_track.to_dict()
            payload["section"] = "in_set"
            if payload["path"] not in seen:
                raw.append(payload)
                seen.add(payload["path"])
        retry_hist = summarize_retry_history()
        placement_index, set_index = _cached_placement_indexes()
        extra_paths: list[str] = []
        for t in raw:
            extra_paths.extend(
                h["path"]
                for h in find_cues_sorted_matches(t["name"], index=placement_index)
            )
            extra_paths.extend(
                h["path"]
                for h in find_library_matches(t["name"], index=placement_index)
            )
            extra_paths.extend(
                h["path"] for h in find_set_matches(t["name"], index=set_index)
            )
        if extra_paths:
            cue_index.update(summarize_cues_for_paths(extra_paths))
        tracks = [
            _enrich_track(
                t,
                review=True,
                include_placements=True,
                placement_index=placement_index,
                set_index=set_index,
                cue_index=cue_index,
                retry_history=retry_hist,
            )
            for t in raw
        ]
        ready_n = sum(1 for t in tracks if t.get("readiness", {}).get("ready"))
        partial_n = sum(
            1 for t in tracks if t.get("readiness", {}).get("status") == "partial"
        )
        not_cued_n = sum(
            1
            for t in tracks
            if t.get("readiness", {}).get("status") in {"not_cued", "missing"}
        )
        paj_tracks = [t for t in tracks if t.get("section") == "pajamathon"]
        paj_not_cued = sum(
            1
            for t in paj_tracks
            if t.get("readiness", {}).get("status") in {"not_cued", "missing"}
        )
        retried_cues_n = sum(
            1 for t in tracks if (t.get("retry_history") or {}).get("kind") == "cues"
        )
        retried_loops_n = sum(
            1 for t in tracks if (t.get("retry_history") or {}).get("kind") == "loops"
        )
        retried_both_n = sum(
            1 for t in tracks if (t.get("retry_history") or {}).get("kind") == "both"
        )
        return {
            "mode": "add_cues",
            "source": str(ADD_CUES),
            "crate": crate or "",
            "crates": crate_names,
            "default_crate": _profile.DEFAULT_ADD_CUES_CRATE if _profile.IS_HOUSE else "",
            "tracks": tracks,
            "counts": {
                "total": len(tracks),
                "ready": ready_n,
                "partial": partial_n,
                "not_cued": not_cued_n,
                "cued": sum(1 for t in tracks if t["is_cued"]),
                "uncued": sum(1 for t in tracks if not t["is_cued"]),
                "pajamathon": len(paj_tracks),
                "pajamathon_not_cued": paj_not_cued,
                "inbox": len(tracks) - len(paj_tracks),
                "retried_cues": retried_cues_n,
                "retried_loops": retried_loops_n,
                "retried_both": retried_both_n,
            },
        }


    if mode == "set_overview":
        raw_set = [
            t
            for t in list_all_set_tracks()
            if not is_must_play_folder_path(t.relative_path or t.path)
        ]
        placement_index, set_index = _cached_placement_indexes()
        add_by_name = {}
        for add in list_add_cues_tracks():
            add_by_name.setdefault(add.name.lower(), []).append(add)
        # List load must stay fast (same as Add Cues). Sibling cue compares
        # load when a track is selected — scanning every library match here
        # froze Set Overview while AutoCue was running.
        cue_index = summarize_cues_for_paths([t.path for t in raw_set])
        tracks = []
        different_n = 0
        for t in raw_set:
            enriched = _enrich_track(
                t.to_dict(),
                review=True,
                include_placements=True,
                placement_index=placement_index,
                set_index=set_index,
                cue_index=cue_index,
            )
            add_hits = [
                _placement_with_cue_status(
                    {
                        "path": add.path,
                        "relative_path": add.relative_path,
                        "root_name": "Add Cues",
                    },
                    cue_index,
                )
                for add in add_by_name.get(t.name.lower(), [])
                if add.path != t.path
            ]
            enriched.setdefault("placements", {})["add_cues"] = add_hits
            match = _written_copy_match(enriched)
            enriched["written_match"] = match
            enriched["written_matches"] = bool(match.get("matches"))
            if match.get("status") in {"mismatch", "no_copy", "no_proposal", "not_cued"}:
                different_n += 1
            tracks.append(enriched)
        approved_paths = approved_file_paths()
        approved_set = set(approved_paths)
        mp_paths = must_play_file_paths()
        mp_set = set(mp_paths)
        for tr in tracks:
            if tr.get("path") in approved_set:
                tr["set_approved"] = True
            if tr.get("path") in mp_set:
                tr["must_play"] = True
        approved_n = sum(1 for t in tracks if t.get("set_approved"))
        return {
            "mode": "set_overview",
            "source": str(SETS_ROOT),
            "tracks": tracks,
            "approved_paths": approved_paths,
            "must_play_paths": mp_paths,
            "counts": {
                "total": len(tracks),
                "cued": sum(1 for t in tracks if t.get("is_cued")),
                "uncued": sum(1 for t in tracks if not t.get("is_cued")),
                "same": sum(1 for t in tracks if t.get("written_matches")),
                "different": different_n,
                "approved": approved_n,
                "not_approved": len(tracks) - approved_n,
            },
        }

    raw_ready = list_ready_tracks()
    cue_index = summarize_cues_for_paths([t.path for t in raw_ready])
    tracks = [
        _enrich_track(
            t.to_dict(),
            include_placements=False,
            cue_index=cue_index,
        )
        for t in raw_ready
    ]
    cued = sum(1 for t in tracks if t["is_cued"])
    return {
        "mode": "sort",
        "source": str(READY_FOR_SORT),
        "tracks": tracks,
        "counts": {
            "total": len(tracks),
            "cued": cued,
            "uncued": len(tracks) - cued,
        },
    }


@app.get("/api/track-placements")
def get_track_placements(path: str = Query(...)) -> dict[str, Any]:
    """Lazy House/Zouk/Sets placement lookup for the selected track."""
    audio = Path(path).expanduser()
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    placement_index, set_index = _cached_placement_indexes()
    cue_paths = [str(audio)]
    for hit in find_cues_sorted_matches(audio.name, index=placement_index):
        cue_paths.append(hit["path"])
    for hit in find_library_matches(audio.name, index=placement_index):
        cue_paths.append(hit["path"])
    for hit in find_set_matches(audio.name, index=set_index):
        cue_paths.append(hit["path"])
    cue_index = summarize_cues_for_paths(cue_paths)
    enriched = _enrich_track(
        {"path": str(audio), "name": audio.name},
        include_placements=True,
        placement_index=placement_index,
        set_index=set_index,
        cue_index=cue_index,
    )
    return {"ok": True, "path": str(audio), "placements": enriched.get("placements")}


@app.get("/api/libraries")
def get_libraries() -> dict[str, Any]:
    return {"libraries": list_libraries()}


@app.get("/api/folders/{library_name}")
def get_folders(library_name: str, max_depth: int = Query(4, ge=1, le=6)) -> dict[str, Any]:
    try:
        return list_library_tree(library_name, max_depth=max_depth)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/folders")
def post_create_folder(body: CreateFolderRequest) -> dict[str, Any]:
    try:
        created = create_folder(
            body.library,
            name=body.name,
            parent_relative_path=body.parent_relative_path,
        )
        append_action(
            "create_folder",
            name=body.name,
            dest_path=created.get("absolute_path"),
            details={
                "library": body.library,
                "relative_path": created.get("relative_path"),
                "parent_relative_path": body.parent_relative_path,
            },
        )
        return {"ok": True, "folder": created}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/actions")
def get_actions(
    limit: int = Query(100, ge=1, le=1000),
    action: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Newest-first durable action log (sorts, promotes, removes, retries)."""
    rows = read_actions(limit=limit, action=action)
    # Mark which sort/promote rows already have an undo entry.
    undone_ids = {
        (r.get("details") or {}).get("original_id")
        for r in read_actions(limit=2000, action="undo")
        if (r.get("details") or {}).get("original_id")
    }
    for row in rows:
        if row.get("action") in {"sort", "promote"} and row.get("success", True):
            row["undoable"] = row.get("id") not in undone_ids
            row["undone"] = row.get("id") in undone_ids
        else:
            row["undoable"] = False
            row["undone"] = False
    return {
        "ok": True,
        "log_path": str(log_path()),
        "count": len(rows),
        "actions": rows,
    }


@app.post("/api/undo")
def post_undo(body: UndoRequest) -> dict[str, Any]:
    """Reverse a logged sort or promote (file + VDJ FilePath)."""
    try:
        result = undo_action(
            body.action_id,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "undo",
            success=False,
            error=str(exc),
            details={"original_id": body.action_id},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/grid-preflight")
def get_grid_preflight(
    path: str = Query(...),
    deep: bool = Query(True),
) -> dict[str, Any]:
    """Beatgrid readiness check before AutoCue (deep=onset verify)."""
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    assessment = assess_grid_for_autocue(path, deep=deep)
    return {"ok": True, "preflight": assessment}


@app.post("/api/grid-fix/batch")
def post_grid_fix_batch(body: GridFixBatchRequest) -> dict[str, Any]:
    """Halve double-time BPM and snap the 1 (mod 4 beats) for many tracks."""
    paths = list(body.paths or [])
    if body.filter == "pajamathon" or not paths:
        if not paths:
            work, _set_tracks, _cues = _add_cues_work_tracks("pajamathon")
            paths = [track.path for track in work]
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="No tracks to grid-fix (pass paths or filter=pajamathon)",
        )
    try:
        batch = start_batch_grid_fix(
            paths,
            apply=body.apply,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        append_action(
            "grid_fix_batch",
            name=f"{len(paths)} tracks",
            details={
                "batch_id": batch.id,
                "total": len(paths),
                "filter": body.filter,
                "apply": body.apply,
                "dry_run": body.dry_run,
            },
        )
        return {"ok": True, "batch": batch.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/grid-fix/batch/{batch_id}")
def get_grid_fix_batch_route(batch_id: str) -> dict[str, Any]:
    batch = get_grid_fix_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Grid-fix batch not found")
    return {"ok": True, "batch": batch.to_dict()}


@app.get("/api/grid-fix")
def get_grid_fix_jobs() -> dict[str, Any]:
    return {"ok": True, "batches": list_grid_fix_batches()}


@app.post("/api/grid-align/attempt")
def post_grid_align_attempt(body: GridAlignAttemptRequest) -> dict[str, Any]:
    """Stem/onset automatic 1-find for the current track."""
    try:
        result = attempt_grid_align(
            body.path,
            apply=body.apply,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if body.apply and result.get("applied") and not body.dry_run:
            plan = result.get("plan") or {}
            append_action(
                "grid_align_attempt",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "action": plan.get("action"),
                    "anchor_before": plan.get("anchor_before"),
                    "anchor_after": plan.get("anchor_after"),
                    "halve": plan.get("halve"),
                    "reason": plan.get("reason"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "grid_align_attempt",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/set-beatgrid")
@with_save_status("/api/set-beatgrid")
def post_set_beatgrid(body: SetBeatgridRequest) -> dict[str, Any]:
    """Drag-align: write a new '1' (downbeat) into VirtualDJ for this track."""
    try:
        result = set_beatgrid_anchor(
            body.path,
            anchor_seconds=float(body.anchor_seconds),
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("changes") or {}
            append_action(
                "set_beatgrid",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "anchor": result.get("anchor"),
                    "phase_before": ch.get("phase_before"),
                    "beatgrid_before": ch.get("beatgrid_before"),
                    "scan_phase": result.get("scan_phase"),
                    "beatgrid_pos": result.get("beatgrid_pos"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "set_beatgrid",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"anchor": body.anchor_seconds},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/scale-loop")
@with_save_status("/api/scale-loop")
def post_scale_loop(body: ScaleLoopRequest) -> dict[str, Any]:
    """Scale a loop Size in VirtualDJ: 0.5 = half, 2 = double, or any ratio newBeats/oldBeats
    (phrase-snapped end-edge resize). Result is clamped to 1..256 beats."""
    factor = float(body.factor)
    if not (1.0 / 64.0 <= factor <= 64.0):
        raise HTTPException(
            status_code=400, detail="factor must be between 1/64 and 64 (0.5 = half, 2 = double)"
        )
    try:
        result = scale_loop_point(
            body.path,
            pos=body.pos,
            factor=factor,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "scale_loop",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "factor": factor,
                    "pos": body.pos,
                    "size_before": ch.get("size_before"),
                    "size_after": ch.get("size_after"),
                    "name": ch.get("name"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "scale_loop",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"factor": body.factor, "pos": body.pos},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/set-cue-color")
@with_save_status("/api/set-cue-color")
def post_set_cue_color(body: SetCueColorRequest) -> dict[str, Any]:
    """Change the Color of one cue or loop in VirtualDJ."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = set_poi_color(
            body.path,
            kind=kind,
            pos=body.pos,
            color=body.color,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "set_cue_color",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos": body.pos,
                    "color_name": ch.get("color_name"),
                    "color_before": ch.get("color_before"),
                    "color_after": ch.get("color_after"),
                    "marker_name": ch.get("name"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "set_cue_color",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"kind": kind, "pos": body.pos, "color": body.color},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/move-poi")
@with_save_status("/api/move-poi")
def post_move_poi(body: MovePoiRequest) -> dict[str, Any]:
    """Move a cue or loop start time in VirtualDJ (drag-to-reposition)."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = set_poi_position(
            body.path,
            kind=kind,
            pos=float(body.pos),
            new_pos=float(body.new_pos),
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "move_poi",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos_before": ch.get("pos_before"),
                    "pos_after": ch.get("pos_after"),
                    "marker_name": ch.get("name"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "move_poi",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "kind": kind,
                "pos": body.pos,
                "new_pos": body.new_pos,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/add-cue")
@with_save_status("/api/add-cue")
def post_add_cue(body: AddCueRequest) -> dict[str, Any]:
    """Insert one cue at pos (seconds). Does not strip existing markers."""
    try:
        result = add_cue_point(
            body.path,
            pos=float(body.pos),
            name=body.name,
            color=body.color or "green",
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "add_cue",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "pos": ch.get("pos", body.pos),
                    "marker_name": ch.get("name"),
                    "num": ch.get("num"),
                    "color_name": ch.get("color_name"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "add_cue",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"pos": body.pos, "name": body.name},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/add-loop")
@with_save_status("/api/add-loop")
def post_add_loop(body: AddLoopRequest) -> dict[str, Any]:
    """Insert one loop at pos (seconds). Does not strip existing markers."""
    try:
        result = add_loop_point(
            body.path,
            pos=float(body.pos),
            name=body.name,
            color=body.color or "green",
            beats=float(body.beats or 8.0),
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "add_loop",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "pos": ch.get("pos", body.pos),
                    "marker_name": ch.get("name"),
                    "slot": ch.get("slot"),
                    "beats": ch.get("beats"),
                    "color_name": ch.get("color_name"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "add_loop",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"pos": body.pos, "name": body.name},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/rename-poi")
@with_save_status("/api/rename-poi")
def post_rename_poi(body: RenamePoiRequest) -> dict[str, Any]:
    """Rename one cue or loop Name in VirtualDJ for this track."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = set_poi_name(
            body.path,
            kind=kind,
            pos=float(body.pos),
            new_name=body.new_name,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            ch = result.get("change") or {}
            append_action(
                "rename_poi",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos": body.pos,
                    "name_before": ch.get("name_before"),
                    "name_after": ch.get("name_after"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "rename_poi",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "kind": kind,
                "pos": body.pos,
                "new_name": body.new_name,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/deleted-markers")
def get_deleted_markers(path: str = Query(...)) -> dict[str, Any]:
    """Read-only: cues/loops the user deleted for this song (AutoCue retry filter)."""
    markers = _deleted_markers.list_for_path(path)
    return {
        "ok": True,
        "path": path,
        "markers": [{k: v for k, v in m.items() if k != "raw"} for m in markers],
    }


@app.post("/api/restore-deleted-marker")
@with_save_status("/api/restore-deleted-marker")
def post_restore_deleted_marker(body: RestoreMarkerRequest) -> dict[str, Any]:
    """Undo a delete: re-add the exact removed marker and forget the record."""
    rec = _deleted_markers.get_record(body.id)
    if rec is None or not rec.get("raw"):
        raise HTTPException(status_code=404, detail="Nothing to restore for that delete")
    if body.dry_run:
        return {"ok": True, "dry_run": True, "path": rec["path"], "marker": rec["key"]}
    try:
        result = restore_poi_point(
            rec["path"], raw=rec["raw"], allow_vdj_running=body.allow_vdj_running
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _deleted_markers.remove_record(body.id)
    append_action(
        "restore_cue",
        source_path=rec["path"],
        name=Path(rec["path"]).name,
        details={"kind": rec.get("kind"), "pos": rec.get("pos"), "name": rec.get("name")},
    )
    return {"ok": True, "result": result}


@app.post("/api/delete-cue")
@with_save_status("/api/delete-cue")
def post_delete_cue(body: DeleteCueRequest) -> dict[str, Any]:
    """Delete one manual cue or loop from VirtualDJ for this track."""
    kind = (body.kind or "").strip().lower()
    if kind not in {"cue", "loop"}:
        raise HTTPException(status_code=400, detail="kind must be 'cue' or 'loop'")
    try:
        result = delete_cue_point(
            body.path,
            kind=kind,
            pos=body.pos,
            num=body.num,
            name=body.name,
            slot=body.slot,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            append_action(
                "delete_cue",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "kind": kind,
                    "pos": body.pos,
                    "num": body.num,
                    "removed": result.get("removed"),
                    "cue_count_after": result.get("cue_count_after"),
                    "loop_count_after": result.get("loop_count_after"),
                    "database_backup": result.get("database_backup"),
                },
            )
            removed = result.get("removed")
            if isinstance(removed, dict):
                try:
                    entry = _deleted_markers.record_deleted(body.path, removed)
                    result = {**result, "deleted_marker_id": entry["id"]}
                except Exception:  # memory is best-effort; never fail the delete
                    logging.getLogger(__name__).exception("deleted-marker record failed")
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "delete_cue",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"kind": kind, "pos": body.pos},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/notes")
def get_notes(path: str = Query(...)) -> dict[str, Any]:
    """Read VirtualDJ Comment notes for a track."""
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    cues = summarize_cues(path)
    return {
        "ok": True,
        "path": path,
        "in_database": cues.in_database,
        "comment": cues.comment or "",
    }


@app.post("/api/notes")
@with_save_status("/api/notes")
def post_notes(body: NotesRequest) -> dict[str, Any]:
    """Live-update VirtualDJ <Comment> notes for a track."""
    try:
        result = set_track_comment(
            body.path,
            body.comment,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
        )
        # Don't spam action log on every keystroke; only log non-empty changes
        # when not a no-op. (Optional: skip log entirely for live updates.)
        if not body.dry_run and not result.get("unchanged"):
            append_action(
                "update_notes",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "comment_len": len(result.get("comment") or ""),
                    "vdj_running": is_virtualdj_running(),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/halve-bpm")
@with_save_status("/api/halve-bpm")
def post_halve_bpm(body: HalveBpmRequest) -> dict[str, Any]:
    """
    Halve VDJ musical BPM for a track (double-time fix: 136 → 68).

    Rewrites Scan/Tags @Bpm in database.xml. Close VirtualDJ first.
    """
    try:
        result = halve_track_bpm(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            double_instead=body.double_instead,
        )
        if not body.dry_run:
            append_action(
                "double_bpm" if body.double_instead else "halve_bpm",
                source_path=body.path,
                name=Path(body.path).name,
                details={
                    "bpm_before": result.get("bpm_before"),
                    "bpm_after": result.get("bpm_after"),
                    "changes": result.get("changes"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "halve_bpm",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/cues")
def get_cues(path: str = Query(...)) -> dict[str, Any]:
    if not Path(path).is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    cues = summarize_cues(path)
    payload = cues.to_dict()
    payload["readiness"] = assess_cue_readiness(cues)
    return payload


@app.post("/api/recommend")
def post_recommend(body: RecommendRequest) -> dict[str, Any]:
    path = Path(body.path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Track file not found")
    try:
        result = get_recommender().recommend(
            path,
            force=body.force,
            preferred_library=body.preferred_library,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = result.to_dict()
    if result.error:
        return {"ok": False, "recommendation": payload}
    return {"ok": True, "recommendation": payload}


@app.post("/api/sort")
@with_save_status("/api/sort")
def post_sort(body: SortRequest) -> dict[str, Any]:
    try:
        from sorter.lanes import (
            ensure_sort_folder,
            folder_for_lane,
            lane_from_folder_path,
            normalize_lane,
        )

        if _profile.IS_HOUSE:
            # House fork: sort COPIES (original stays). Destination = an existing House
            # subfolder, or a validated NEW folder (capped at 3 without asking Kirill).
            from sorter.house_folders import resolve_destination_rel

            lane = None
            body.library = "House"
            if body.destinations:
                dest_payload = [
                    {
                        "library": "House",
                        "relative_folder": resolve_destination_rel(
                            d.relative_folder, new_folder=d.new_folder
                        ),
                        "new_folder": bool(d.new_folder),
                    }
                    for d in body.destinations
                ]
                body.relative_folder = dest_payload[0]["relative_folder"]
            else:
                if not (body.relative_folder or "").strip():
                    raise ValueError(
                        "Pick a House folder (or 'New folder in House') to copy into."
                    )
                body.relative_folder = resolve_destination_rel(body.relative_folder)
                dest_payload = None
        else:
            lane = normalize_lane(body.lane)
            has_folder = bool((body.relative_folder or "").strip() or body.destinations)
            if not lane and not has_folder:
                raise ValueError("Pick a lane before sorting (white stays unsorteable)")
            if not has_folder:
                body.library = "Zouk"
                body.relative_folder = folder_for_lane(lane)
            body.relative_folder = ensure_sort_folder(body.relative_folder, lane)
            dest_payload = None
            if body.destinations:
                dest_payload = [
                    {
                        "library": d.library,
                        "relative_folder": ensure_sort_folder(d.relative_folder, lane),
                    }
                    for d in body.destinations
                ]
            if not lane:
                lane = lane_from_folder_path(body.relative_folder)
            if not lane and dest_payload:
                for dest in dest_payload:
                    lane = lane_from_folder_path(dest.get("relative_folder"))
                    if lane:
                        break
        if _profile.IS_HOUSE and body.also_sauna_fest:
            from sorter.relocate import sauna_fest_sort_track

            result = sauna_fest_sort_track(
                body.path,
                relative_folder=body.relative_folder,
                destinations=dest_payload,
                dry_run=body.dry_run,
                set_folder_name=body.set_folder_name or None,
            )
        else:
            result = sort_track(
                body.path,
                library_name=body.library,
                relative_folder=body.relative_folder,
                destinations=dest_payload,
                dry_run=body.dry_run,
                allow_vdj_running=body.allow_vdj_running,
                lane=lane,
            )
        payload = result.to_dict()
        if _profile.readonly():
            payload["readonly_forced_dry_run"] = True
            payload["readonly_message"] = _profile.READONLY_MESSAGE
        if not body.dry_run:
            invalidate_placement_indexes()
            if _profile.IS_HOUSE and not _profile.readonly() and result.saved:
                _sorted_state.record_sorted(
                    body.path,
                    [d["path"] for d in (result.library_dests or []) if d.get("path")]
                    + list(result.sets_paths or []),
                    kind="sauna_fest" if body.also_sauna_fest else "house",
                )
            append_action(
                "sort",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "library_mode": body.library,
                    "relative_folder": body.relative_folder,
                    "destinations": dest_payload
                    or [
                        {
                            "library": body.library,
                            "relative_folder": body.relative_folder,
                        }
                    ],
                    "library_dests": payload.get("library_dests"),
                    "cues_sorted_path": payload.get("cues_sorted_path"),
                    "cues_sorted_copied": payload.get("cues_sorted_copied"),
                    "cues_sorted_db_cloned": payload.get("cues_sorted_db_cloned"),
                    "sets_cues_copied": payload.get("sets_cues_copied"),
                    "sets_paths": payload.get("sets_paths"),
                    "set_copies": list(result.sets_paths or []),
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                    "lane": lane,
                },
            )
        return {"ok": True, "result": payload}
    except PermissionError as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "sort",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={
                "library_mode": body.library,
                "relative_folder": body.relative_folder,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/sort-sauna-fest")
@with_save_status("/api/sort-sauna-fest")
def post_sort_sauna_fest(body: SaunaFestSortRequest) -> dict[str, Any]:
    """HOUSE FORK: copy a song into Sets/Sauna Fest AND a House folder (all-or-nothing)."""
    from sorter.config import SAUNA_FEST_SET_NAME
    from sorter.house_folders import resolve_destination_rel
    from sorter.relocate import sauna_fest_sort_track, sauna_fest_set_dir

    def _log_fail(exc: Exception) -> None:
        append_action(
            "sort_sauna_fest",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"relative_folder": body.relative_folder},
        )

    try:
        if not _profile.IS_HOUSE:
            raise ValueError("Add to Sauna Fest only exists in the House fork.")
        if body.destinations:
            dest_payload = [
                {
                    "library": "House",
                    "relative_folder": resolve_destination_rel(
                        d.relative_folder, new_folder=d.new_folder
                    ),
                    "new_folder": bool(d.new_folder),
                }
                for d in body.destinations
            ]
            rel = dest_payload[0]["relative_folder"]
        else:
            if not (body.relative_folder or "").strip():
                raise ValueError("Pick a House folder (or 'New folder in House') first.")
            rel = resolve_destination_rel(body.relative_folder)
            dest_payload = None
        set_dir = sauna_fest_set_dir(body.set_folder_name or None)
        result = sauna_fest_sort_track(
            body.path,
            relative_folder=rel,
            destinations=dest_payload,
            dry_run=body.dry_run,
            set_folder_name=body.set_folder_name or None,
        )
        payload = result.to_dict()
        payload["sauna_fest_dir"] = str(set_dir)
        payload["sauna_fest_path"] = (list(result.sets_paths or []) or [None])[0]
        if _profile.readonly():
            payload["readonly_forced_dry_run"] = True
            payload["readonly_message"] = _profile.READONLY_MESSAGE
        if not body.dry_run and not _profile.readonly():
            invalidate_placement_indexes()
            copies = [d["path"] for d in (result.library_dests or []) if d.get("path")]
            copies += list(result.sets_paths or [])
            if result.saved:
                _sorted_state.record_sorted(body.path, copies, kind="sauna_fest")
            append_action(
                "sort_sauna_fest",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "relative_folder": rel,
                    "library_dests": payload.get("library_dests"),
                    "set_copies": list(result.sets_paths or []),
                    "set_folder": SAUNA_FEST_SET_NAME if not body.set_folder_name else body.set_folder_name,
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                },
            )
        return {"ok": True, "result": payload}
    except PermissionError as exc:
        _log_fail(exc)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        _log_fail(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        _log_fail(exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        _log_fail(exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/remove-ready")
def post_remove_ready(body: RemoveReadyRequest) -> dict[str, Any]:
    """Remove from Ready for Sort only — no library or Cues Sorted placement."""
    try:
        result = remove_from_ready_for_sort(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
            remove_from_database=body.remove_from_database,
        )
        if not body.dry_run:
            append_action(
                "remove_ready",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={"to_trash": body.to_trash, "removed": result.get("removed")},
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "remove_ready",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/delete-add-cues")
def post_delete_add_cues(body: DeleteAddCuesRequest) -> dict[str, Any]:
    """
    Delete an Add Cues track entirely: audio + stems to Trash, and remove the
    VirtualDJ Song entry (cues + loops for that path). Inbox deletes do not
    remove the Sets/Pajamathon copy — use Delete from Pajamathon for that.
    """
    try:
        result = delete_add_cues_track(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
            allow_vdj_running=body.allow_vdj_running,
        )
        source = Path(body.path)
        resolved = source.expanduser().resolve()
        # Deleting the live Sets/Pajamathon copy must not snapshot-sync —
        # sibling hard-links (Zouk / Add Cues inbox) share a basename key.
        if not is_pajamathon_set_audio(resolved):
            try:
                relative = resolved.relative_to(ADD_CUES.resolve())
            except ValueError:
                relative = Path(source.name)
            if add_cues_section(relative_path=str(relative)) == "pajamathon":
                result["set_sync"] = sync_pajamathon_set_deletes(
                    extra_deleted=[source.name],
                    dry_run=body.dry_run,
                    to_trash=body.to_trash,
                    # Inbox delete is cleanup after cueing / Add To Set.
                    # Set copies are removed only via Delete from Pajamathon.
                    propagate_to_set=False,
                )
        if not body.dry_run:
            append_action(
                "delete_add_cues",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={
                    "to_trash": body.to_trash,
                    "removed_files": result.get("removed_files"),
                    "database": result.get("database"),
                    "had_cues": result.get("had_cues"),
                    "had_loops": result.get("had_loops"),
                    "database_backup": result.get("database_backup"),
                    "set_sync": result.get("set_sync"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        append_action(
            "delete_add_cues",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "delete_add_cues",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc



@app.post("/api/remove-set-copy")
def post_remove_set_copy(body: RemoveSetCopyRequest) -> dict[str, Any]:
    """Delete the Sets copy only. Zouk / Cues Sorted / Add Cues siblings stay."""
    try:
        result = remove_set_copy(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            invalidate_placement_indexes()
            append_action(
                "remove_set_copy",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={
                    "to_trash": result.get("to_trash"),
                    "unlink_only": result.get("unlink_only"),
                    "kept_hardlinks": result.get("kept_hardlinks"),
                    "database": result.get("database"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "remove_set_copy",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/send-back-set")
def post_send_back_set(body: SendBackSetRequest) -> dict[str, Any]:
    """Relocate Sets/Pajamathon copy to Add Cues/Pajamathon. No sibling deletes."""
    try:
        result = send_set_copy_to_add_cues(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            invalidate_placement_indexes()
            append_action(
                "send_back_set",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={
                    "dest_path": result.get("dest_path"),
                    "already_in_inbox": result.get("already_in_inbox"),
                    "database_updated": result.get("database_updated"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "send_back_set",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/delete-placement")
def post_delete_placement(body: DeletePlacementRequest) -> dict[str, Any]:
    """
    Delete an Already-in-library placement (House/Zouk/Cues Sorted file)
    and remove its VirtualDJ Song entry (cues + loops for that path).
    """
    try:
        result = delete_library_placement(
            body.path,
            dry_run=body.dry_run,
            to_trash=body.to_trash,
            allow_vdj_running=body.allow_vdj_running,
        )
        if not body.dry_run:
            invalidate_placement_indexes()
            append_action(
                "delete_placement",
                source_path=body.path,
                name=result.get("name") or Path(body.path).name,
                details={
                    "root_name": result.get("root_name"),
                    "relative_path": result.get("relative_path"),
                    "to_trash": body.to_trash,
                    "removed_files": result.get("removed_files"),
                    "database": result.get("database"),
                    "had_cues": result.get("had_cues"),
                    "had_loops": result.get("had_loops"),
                    "database_backup": result.get("database_backup"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "delete_placement",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/copy-cues")
def post_copy_cues(body: CopyCuesRequest) -> dict[str, Any]:
    """Copy Ready/Add Cues markers onto a House/Zouk/Cues Sorted/Sets copy."""
    try:
        result = copy_cues_to_placement(
            body.source,
            body.dest,
            overwrite=body.overwrite,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
        )
        if not body.dry_run:
            append_action(
                "copy_cues",
                source_path=body.source,
                dest_path=body.dest,
                name=Path(body.dest).name,
                details={
                    "mode": result.get("mode"),
                    "root_name": result.get("root_name"),
                    "relative_path": result.get("relative_path"),
                    "copied_cues": result.get("copied_cues"),
                    "copied_loops": result.get("copied_loops"),
                    "overwrote": result.get("overwrote"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "copy_cues",
            source_path=body.source,
            dest_path=body.dest,
            name=Path(body.dest).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/copy-cues-all")
def post_copy_cues_all(body: CopyCuesAllRequest) -> dict[str, Any]:
    """Copy Ready/Add Cues markers onto every listed existing copy."""
    try:
        result = copy_cues_to_placements(
            body.source,
            body.dests,
            overwrite=body.overwrite,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
        )
        if not body.dry_run:
            append_action(
                "copy_cues_all",
                source_path=body.source,
                name=Path(body.source).name,
                details={
                    "copied": result.get("copied"),
                    "skipped": result.get("skipped"),
                    "failed": result.get("failed"),
                    "overwrite": result.get("overwrite"),
                    "dests": [item.get("dest_path") for item in result.get("results") or []],
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "copy_cues_all",
            source_path=body.source,
            name=Path(body.source).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/add-to-set")
def post_add_to_set(body: AddToSetRequest) -> dict[str, Any]:
    """Copy this queue track into Sets/Pajamathon and clone its VirtualDJ cues."""
    try:
        result = add_track_to_event_set(
            body.path,
            event_name=body.event_name,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            create_backup=body.create_backup,
        )
        if not body.dry_run:
            invalidate_placement_indexes()
        if not body.dry_run and not result.get("already_exists"):
            append_action(
                "add_to_set",
                source_path=body.path,
                dest_path=result.get("dest_path"),
                name=Path(body.path).name,
                details={
                    "event": result.get("event"),
                    "relative_path": result.get("relative_path"),
                    "copied_cues": result.get("copied_cues"),
                    "copied_loops": result.get("copied_loops"),
                },
            )
        return {"ok": True, "result": result}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "add_to_set",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/retry-cues")
def post_retry_cues(body: RetryCuesRequest) -> dict[str, Any]:
    """
    Re-run vdj-automatic-cuer on a track with bad/missing cues.

    Returns a job id; poll GET /api/retry-cues/{job_id} until status is ok/error/skipped.
    Skipped = beatgrid preflight blocked AutoCue (fix grid in VDJ first).
    """
    try:
        job = start_retry_cues(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            require_grid=body.require_grid,
            deep_grid_check=body.deep_grid_check,
            write_scope=body.write_scope,
        )
        append_action(
            "retry_cues",
            source_path=body.path,
            name=Path(body.path).name,
            success=job.status != "skipped",
            error=job.message if job.status == "skipped" else None,
            details={
                "job_id": job.id,
                "dry_run": body.dry_run,
                "status": job.status,
                "write_scope": job.write_scope,
                "preflight": job.preflight,
            },
        )
        return {"ok": True, "job": job.to_dict()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "retry_cues",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/retry-cues/batch")
def post_retry_cues_batch(body: BatchRetryCuesRequest) -> dict[str, Any]:
    """
    Queue AutoCue for many tracks. Up to MUSIC_SORTER_AUTOCUE_CONCURRENCY
    songs analyze in parallel; database.xml writes stay one-at-a-time.

    filter=not_cued → all Add Cues tracks with readiness not_cued/missing.
    filter=pajamathon_not_cued → same, but only Add Cues/Pajamathon.
    filter=pajamathon_needs_loops → Pajamathon tracks with fewer than 2 loops.
    """
    paths = list(body.paths or [])
    if body.filter in {
        "not_cued",
        "pajamathon_not_cued",
        "needs_loops",
        "pajamathon_needs_loops",
    }:
        crate = "pajamathon" if "pajamathon" in body.filter else "all"
        raw, _set_tracks, summaries = _add_cues_work_tracks(crate)
        paths = []
        want_loops = body.filter in {"needs_loops", "pajamathon_needs_loops"}
        for t in raw:
            cues = summaries.get(t.path)
            if want_loops:
                loops = int(getattr(cues, "loop_count", 0) or 0) if cues else 0
                if loops < 2:
                    paths.append(t.path)
                continue
            readiness = assess_cue_readiness(cues) if cues is not None else {}
            if readiness.get("status") in {"not_cued", "missing"}:
                paths.append(t.path)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail=(
                "No tracks to queue for AutoCue "
                "(pass paths or filter=not_cued / pajamathon_not_cued / "
                "pajamathon_needs_loops)"
            ),
        )

    try:
        batch = start_batch_retry_cues(
            paths,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            require_grid=body.require_grid,
            deep_grid_check=body.deep_grid_check,
            write_scope=body.write_scope,
            model_name=body.model_name,
        )
        append_action(
            "retry_cues_batch",
            name=f"{len(paths)} tracks",
            details={
                "batch_id": batch.id,
                "total": len(paths),
                "filter": body.filter,
                "dry_run": body.dry_run,
                "write_scope": body.write_scope,
            },
        )
        return {"ok": True, "batch": batch.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/retry-cues")
def get_retry_cues_jobs() -> dict[str, Any]:
    return {
        "ok": True,
        "jobs": list_jobs(),
        "batches": list_batches(),
        "max_concurrent": max_concurrent_jobs(),
    }


@app.get("/api/retry-cues/batch/{batch_id}")
def get_retry_cues_batch(batch_id: str) -> dict[str, Any]:
    batch = get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {"ok": True, "batch": batch.to_dict()}


@app.get("/api/retry-cues/{job_id}")
def get_retry_cues_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/approve-set-cues")
def post_approve_set_cues(body: ApproveSetCuesRequest) -> dict[str, Any]:
    """Mark a Sets/Pajamathon file's cues as human-approved."""
    try:
        cues = summarize_cues(body.path)
        rec = approve_set_cues(
            body.path, cue_count=cues.cue_count, loop_count=cues.loop_count
        )
        append_action(
            "approve_set_cues",
            source_path=body.path,
            name=Path(body.path).name,
            details={
                "cue_count": rec.get("cue_count"),
                "loop_count": rec.get("loop_count"),
            },
        )
        return {"ok": True, "result": rec}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/api/must-play-set")
def post_must_play_set(body: MustPlaySetRequest) -> dict[str, Any]:
    """Stamp Must Play and copy the file into Sets/Pajamathon/Must Play."""
    try:
        rec = mark_must_play(body.path)
        append_action(
            "must_play_set",
            source_path=body.path,
            name=Path(body.path).name,
            details={
                "key": rec.get("key"),
                "folder_copy": rec.get("folder_copy"),
            },
        )
        return {"ok": True, "result": rec}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@app.post("/api/promote")
def post_promote(body: PromoteRequest) -> dict[str, Any]:
    """Move an Add Cues track into Ready for Sort (or another cue stage)."""
    try:
        result = promote_add_cues_track(
            body.path,
            destination_stage=body.destination_stage,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            require_cued=body.require_cued,
        )
        payload = result.to_dict()
        if not body.dry_run:
            append_action(
                "promote",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "destination_stage": body.destination_stage,
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                },
            )
        return {"ok": True, "result": payload}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        append_action(
            "promote",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"destination_stage": body.destination_stage},
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        append_action(
            "promote",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"destination_stage": body.destination_stage},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "promote",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
            details={"destination_stage": body.destination_stage},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/demote-ready")
def post_demote_ready(body: DemoteReadyRequest) -> dict[str, Any]:
    """Send a Ready for Sort track back to Add Cues for re-review."""
    try:
        result = demote_ready_to_add_cues(
            body.path,
            dry_run=body.dry_run,
            allow_vdj_running=body.allow_vdj_running,
            subfolder=body.subfolder,
        )
        payload = result.to_dict()
        if not body.dry_run:
            append_action(
                "demote_ready",
                source_path=body.path,
                dest_path=result.dest_path,
                name=Path(body.path).name,
                details={
                    "subfolder": body.subfolder,
                    "database_updated": payload.get("database_updated"),
                    "stems_moved": payload.get("stems_moved"),
                },
            )
        return {"ok": True, "result": payload}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        append_action(
            "demote_ready",
            source_path=body.path,
            name=Path(body.path).name,
            success=False,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/practice/sets")
def practice_sets() -> dict[str, Any]:
    """List practice mix recordings (Music/Mixes)."""
    try:
        db_stats = ensure_database()
    except Exception as exc:
        db_stats = {"error": str(exc)}
    mixes = list_practice_mixes()
    try:
        mixes = annotate_mixes_exclude_from_best(mixes)
    except Exception:
        mixes = [{**m, "exclude_from_best": False} for m in mixes]
    return {
        "mixes": mixes,
        "mixes_root": str(MIXES_ROOT),
        "transitions_db": db_stats,
    }


@app.get("/api/practice/set")
def practice_set_detail(path: str = Query(...)) -> dict[str, Any]:
    """Tracklist + transitions + alternate options for one practice mix."""
    try:
        ensure_database()
        return get_practice_set_detail(path, include_alternatives=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/practice/tracks")
def practice_all_tracks() -> dict[str, Any]:
    """Union of tracks played across recent practice mixes."""
    try:
        return all_tracks_across_mixes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PracticeAnalyzeRequest(BaseModel):
    path: str
    force: bool = False
    max_transitions: Optional[int] = None


@app.post("/api/practice/analyze")
def practice_analyze(req: PracticeAnalyzeRequest) -> dict[str, Any]:
    """Start Gemini analysis of transitions in a practice mix (background job)."""
    try:
        ensure_database()
        job = start_analyze_job(
            req.path,
            force=req.force,
            max_transitions=req.max_transitions,
        )
        return {"job": job}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/practice/analyze/{job_id}")
def practice_analyze_status(job_id: str) -> dict[str, Any]:
    job = get_analyze_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown analyze job")
    return {"job": job}


class PracticeScoreUpdateRequest(BaseModel):
    id: Optional[int] = None
    mix_path: Optional[str] = None
    transition_index: Optional[int] = None
    priority: Optional[int] = None
    save_for_set: Optional[bool] = None


class PracticeMixSettingsRequest(BaseModel):
    path: str
    exclude_from_best: bool


@app.get("/api/practice/best")
def practice_best(
    prefix: str = Query("pj"),
    min_overall: float = Query(7.0),
    saved_only: bool = Query(False),
    min_priority: int = Query(0, ge=0, le=5),
    hide_live_played: bool = Query(False),
) -> dict[str, Any]:
    """Cross-mix shortlist from Gemini rankings + user priority tiers."""
    try:
        ensure_database()
        items = list_best_practice_scores(
            prefix=prefix,
            min_overall=min_overall,
            saved_only=saved_only,
            min_priority=min_priority,
        )
        hidden = 0
        live_played_error = ""
        try:
            items, hidden = filter_best_items_hide_live_played(
                items, hide=hide_live_played
            )
        except Exception as exc:
            log.exception("Failed to annotate live-set plays for Best for set")
            live_played_error = str(exc)
        return {
            "items": items,
            "count": len(items),
            "hidden_live_played": hidden,
            "hide_live_played": hide_live_played,
            "live_played_error": live_played_error,
            "prefix": prefix,
            "min_overall": min_overall,
            "saved_only": saved_only,
            "min_priority": min_priority,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/practice/mix-settings")
def practice_mix_settings(req: PracticeMixSettingsRequest) -> dict[str, Any]:
    """Exclude a real set from Best for set while keeping Practice review."""
    mix = Path(req.path).expanduser().resolve()
    if not mix.is_file():
        raise HTTPException(status_code=404, detail="Mix not found")
    try:
        ensure_database()
        row = set_practice_mix_exclude(str(mix), req.exclude_from_best)
        return {"ok": True, **row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/practice/score")
def practice_score_update(req: PracticeScoreUpdateRequest) -> dict[str, Any]:
    """Update manual priority (0–5) and/or save_for_set on a scored transition."""
    if req.priority is None and req.save_for_set is None:
        raise HTTPException(
            status_code=400, detail="Provide priority and/or save_for_set"
        )
    if req.priority is not None and not (0 <= int(req.priority) <= 5):
        raise HTTPException(status_code=400, detail="priority must be 0–5")
    if req.id is None and (not req.mix_path or req.transition_index is None):
        raise HTTPException(
            status_code=400, detail="Provide id or mix_path+transition_index"
        )
    try:
        ensure_database()
        row = update_practice_score(
            id=req.id,
            mix_path=req.mix_path,
            transition_index=req.transition_index,
            priority=req.priority,
            save_for_set=req.save_for_set,
        )
        return {"score": row}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=(str(exc.args[0]) if exc.args else str(exc))) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/transitions/options")
def transition_options(
    from_track: str = Query(..., min_length=1),
    limit: int = Query(12, ge=1, le=40),
) -> dict[str, Any]:
    """Known alternate destinations for a track from notes + history."""
    try:
        ensure_database()
        opts = lookup_options(from_track, limit=limit)
        return {"from_track": from_track, "options": opts, "count": len(opts)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/transitions/rebuild")
def transitions_rebuild() -> dict[str, Any]:
    """Re-import transition notes + dj_transitions.csv into SQLite."""
    try:
        return rebuild_database()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/transitions/stats")
def transitions_stats() -> dict[str, Any]:
    try:
        return ensure_database()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Live transition recommendations (VDJ now-playing → energy buckets) ─────


class TransitionRecsRequest(BaseModel):
    path: Optional[str] = None  # override now-playing
    use_gemini: bool = True
    force_rescan: bool = False
    sync: bool = False  # if true, block until complete (tests / small libs)


@app.get("/api/recs/now-playing/stamp")
def recs_now_playing_stamp() -> dict[str, Any]:
    """Cheap now-playing fingerprint so Recs can follow without a 1s enrich poll."""
    from sorter.vdj_now_playing import now_playing_stamp

    try:
        stamp = now_playing_stamp()
        return {"ok": True, **stamp}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/recs/now-playing")
def recs_now_playing(
    fast: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    """Most recently played track from VirtualDJ History (enriched with BPM/key)."""
    from sorter.vdj_now_playing import format_lastplay, get_now_playing

    try:
        np = get_now_playing(
            enrich=not fast,
            prefer_latest_file=True,
            force_rescan=refresh,
        )
        if np is None:
            return {"ok": True, "now_playing": None, "message": "No VDJ history plays found"}
        payload = np.to_dict()
        payload["lastplay_iso"] = format_lastplay(np.lastplay_unix)
        return {"ok": True, "now_playing": payload}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/recs/transitions")
def recs_transitions(body: TransitionRecsRequest) -> dict[str, Any]:
    """
    Recommend higher / same / lower energy next tracks.

    Filters cued library songs to ±BPM and Camelot-compatible keys, merges
    history/notes, then ranks with Gemini (or heuristics).
    """
    from sorter.transition_recs import get_job, recommend_transitions, start_recommend_job

    try:
        if body.sync:
            result = recommend_transitions(
                path=body.path,
                use_gemini=body.use_gemini,
                force_rescan=body.force_rescan,
            )
            return {"ok": True, "sync": True, "result": result}
        job = start_recommend_job(
            path=body.path,
            use_gemini=body.use_gemini,
            force_rescan=body.force_rescan,
        )
        return {"ok": True, "sync": False, "job": job.to_dict()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/recs/transitions/{job_id}")
def recs_transitions_status(job_id: str) -> dict[str, Any]:
    from sorter.transition_recs import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown recommendation job")
    return {"job": job.to_dict()}


@app.get("/api/recs/sideview")
def recs_sideview_status() -> dict[str, Any]:
    """Where VDJ Sideview rec lists live, and whether shortcuts are pinned."""
    from sorter.vdj_sideview_recs import (
        BUCKET_FOLDERS,
        COMBINED_NAME,
        VDJ_MYLISTS,
        ensure_sideview_shortcuts,
    )

    names = [COMBINED_NAME, *BUCKET_FOLDERS.values()]
    files = {
        name: {
            "path": str(VDJ_MYLISTS / f"{name}.vdjfolder"),
            "exists": (VDJ_MYLISTS / f"{name}.vdjfolder").is_file(),
        }
        for name in names
    }
    paths = [VDJ_MYLISTS / f"{name}.vdjfolder" for name in names]
    shortcuts = ensure_sideview_shortcuts(paths)
    return {"ok": True, "mylists": str(VDJ_MYLISTS), "files": files, "shortcuts": shortcuts}


class AssembleRequest(BaseModel):
    event_name: str = "Pajamathon"
    brief: str = ""
    library: str = "Zouk"
    chunk_size: int = 16
    target: int = 400
    use_gemini: bool = True
    scan_all: bool = False
    lane_shares: Optional[dict[str, float]] = None
    min_fit: Optional[float] = None


class AssembleRebalanceRequest(BaseModel):
    event_name: str = "Pajamathon"
    target: int = 400
    lane_shares: Optional[dict[str, float]] = None
    min_fit: Optional[float] = None


class AssembleMixPrefsRequest(BaseModel):
    lane_shares: Optional[dict[str, float]] = None
    min_fit: Optional[float] = None


@app.get("/api/assemble/preview")
def assemble_preview(library: str = Query("Zouk")) -> dict[str, Any]:
    from sorter.playlist_assemble import preview_library

    try:
        return preview_library(library)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/assemble/start")
def assemble_start(body: AssembleRequest) -> dict[str, Any]:
    from sorter.playlist_assemble import start_assemble_job

    try:
        job = start_assemble_job(
            event_name=body.event_name,
            brief=body.brief or None,
            library=body.library,
            chunk_size=body.chunk_size,
            target=body.target,
            use_gemini=body.use_gemini,
            scan_all=body.scan_all,
            lane_shares=body.lane_shares,
            min_fit=body.min_fit,
        )
        return {"ok": True, "job": job.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/assemble/status/{job_id}")
def assemble_status(job_id: str) -> dict[str, Any]:
    from sorter.playlist_assemble import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown assemble job")
    return {"job": job.to_dict()}


@app.post("/api/assemble/stop/{job_id}")
def assemble_stop(job_id: str) -> dict[str, Any]:
    from sorter.playlist_assemble import cancel_job

    job = cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown assemble job")
    return {"ok": True, "job": job.to_dict()}


@app.get("/api/assemble/latest")
def assemble_latest() -> dict[str, Any]:
    from sorter.playlist_assemble import latest_job

    job = latest_job()
    return {"ok": True, "job": job.to_dict() if job else None}


@app.post("/api/assemble/rebalance")
def assemble_rebalance(body: AssembleRebalanceRequest) -> dict[str, Any]:
    from sorter.playlist_assemble import rebalance_latest_playlist

    try:
        return rebalance_latest_playlist(
            shares=body.lane_shares,
            target=body.target,
            event_name=body.event_name,
            min_fit=body.min_fit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/assemble/mix-prefs")
def assemble_mix_prefs_get() -> dict[str, Any]:
    from sorter.playlist_assemble import load_mix_prefs

    prefs = load_mix_prefs()
    return {"ok": True, **prefs}


@app.post("/api/assemble/mix-prefs")
def assemble_mix_prefs_set(body: AssembleMixPrefsRequest) -> dict[str, Any]:
    from sorter.playlist_assemble import save_mix_prefs

    prefs = save_mix_prefs(body.lane_shares, body.min_fit)
    return {"ok": True, **prefs}


@app.post("/api/assemble/export")
def assemble_export() -> dict[str, Any]:
    from sorter.playlist_assemble import export_latest_playlist

    try:
        files = export_latest_playlist()
        return {"ok": True, "files": files}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/cover")
def get_cover(
    path: str = Query(...),
    artist: str = Query(""),
    title: str = Query(""),
) -> Response:
    """Album cover for the song header. Read-only: VDJ cover cache first, then embedded art
    (ffmpeg extract into our own cache dir). 204 (no content) means "keep the placeholder"."""
    from sorter.cover_art import find_cover

    hit = find_cover(path, artist=artist, title=title)
    if hit is None:
        # "No art" is a normal answer, not an error: 204 keeps the placeholder with no 404 in the log/console.
        return Response(status_code=204, headers={"Cache-Control": "private, max-age=300"})
    suffix = hit.suffix.lower()
    media = "image/png" if suffix == ".png" else "image/jpeg"
    return FileResponse(
        hit,
        media_type=media,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/audio")
def get_audio(path: str = Query(...)) -> FileResponse:
    audio = _assert_under_cues(Path(path))
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    mime, _ = mimetypes.guess_type(str(audio))
    return FileResponse(
        path=str(audio),
        media_type=mime or "application/octet-stream",
        filename=audio.name,
    )


@app.get("/api/waveform")
def get_waveform(
    path: str = Query(...),
    bins: int = Query(900, ge=64, le=2500),
) -> dict[str, Any]:
    """Peak envelope for the interactive cue waveform view."""
    audio = _assert_under_cues(Path(path))
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    try:
        return build_waveform(audio, bins=bins)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"ffmpeg/ffprobe failed: {exc.stderr.decode(errors='replace') if exc.stderr else exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/meta")
def get_meta(path: str = Query(...)) -> dict[str, Any]:
    """Bitrate / codec metadata for the selected track."""
    audio = _assert_under_cues(Path(path))
    if not audio.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    try:
        return probe_audio_meta(audio)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"ffprobe failed: {exc.stderr if exc.stderr else exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class StemAuditRequest(BaseModel):
    scope: str = "all"
    skip_scanned: bool = True


class StemCheckRequest(BaseModel):
    path: str


class StemDeleteRequest(BaseModel):
    paths: list[str]


@app.get("/api/stems/inventory")
def stems_inventory() -> dict[str, Any]:
    try:
        return {"ok": True, **stem_inventory()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/stems/audit")
def stems_audit_start(body: StemAuditRequest) -> dict[str, Any]:
    try:
        job = start_stem_audit_job(
            scope=body.scope, skip_scanned=body.skip_scanned
        )
        return {"ok": True, "job": job}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/stems/audit")
def stems_audit_latest() -> dict[str, Any]:
    return {"ok": True, "job": latest_stem_audit_job()}


@app.get("/api/stems/audit/{job_id}")
def stems_audit_status(job_id: str) -> dict[str, Any]:
    job = get_stem_audit_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown stem audit job")
    return {"ok": True, "job": job}


@app.post("/api/stems/audit/{job_id}/cancel")
def stems_audit_cancel(job_id: str) -> dict[str, Any]:
    try:
        job = cancel_stem_audit_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown stem audit job") from None
    return {"ok": True, "job": job}


@app.post("/api/stems/check")
def stems_check(body: StemCheckRequest) -> dict[str, Any]:
    try:
        return {"ok": True, "track": check_one_stem(body.path)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/stems/delete")
def stems_delete(body: StemDeleteRequest) -> dict[str, Any]:
    if not body.paths:
        raise HTTPException(status_code=400, detail="No sidecar paths to delete")
    try:
        result = delete_stem_sidecars(body.paths)
        append_action(
            "delete_stems",
            name=f"{result['deleted']} sidecars",
            details={"paths": result.get("paths") or []},
        )
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.middleware("http")
async def no_store_ui_assets(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in {"/", "/index.html"} or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
_STATIC_MEM: dict[str, tuple[int, int, bytes]] = {}  # resolved path -> (mtime_ns, size, bytes)
_STATIC_MEM_MAX = 6 * 1024 * 1024


def _static_response(file_path: Path) -> Response:
    """Serve a UI asset straight from memory on the event loop (no worker thread).

    The page, its CSS and JS must keep loading even when every worker thread is busy (a long save, an
    ML ingest, a copy). Starlette's StaticFiles / sync endpoints need a worker thread for each file, so a
    saturated pool left the page unstyled and the API fetches failing. A stat() + cached bytes cannot block.
    """
    st = file_path.stat()
    if st.st_size > _STATIC_MEM_MAX:
        return FileResponse(file_path, headers=_NO_STORE)
    key = str(file_path)
    hit = _STATIC_MEM.get(key)
    if hit is None or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
        hit = (st.st_mtime_ns, st.st_size, file_path.read_bytes())
        _STATIC_MEM[key] = hit
    media, _ = mimetypes.guess_type(str(file_path))
    if file_path.suffix == ".js":
        media = "text/javascript"
    return Response(content=hit[2], media_type=media or "application/octet-stream", headers=_NO_STORE)


@app.get("/api/ping")
async def api_ping() -> dict[str, Any]:
    """Event-loop-only liveness probe (never waits for a worker thread): the UI's 'server back?' check."""
    return {"ok": True, "ui_build": UI_BUILD}


@app.get("/")
async def index() -> Response:
    return _static_response(STATIC_DIR / "index.html")


@app.get("/static/{rel_path:path}")
async def static_asset(rel_path: str) -> Response:
    base = STATIC_DIR.resolve()
    target = (STATIC_DIR / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found") from None
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return _static_response(target)


@app.on_event("startup")
async def _more_worker_threads() -> None:
    """Default is 40 worker threads; long saves/copies hold some, so keep plenty free for quick reads."""
    try:
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = 200
    except Exception:  # pragma: no cover - tuning only
        logging.getLogger(__name__).exception("could not raise the worker thread limit")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
