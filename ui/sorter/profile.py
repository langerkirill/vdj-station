"""Runtime profile for the Music Sorter (HOUSE FORK).

Environment:
  MUSIC_SORTER_PROFILE   house (fork default) | zouk (legacy shape, unsupported in this fork)
  MUSIC_SORTER_READONLY  1/true/yes/on => the process never writes VDJ data or moves/copies
                         library files. Sort/promote/delete paths become dry-run only.
  MUSIC_SORTER_NOTES_DIR notes/cache directory (default ~/Music/DJ/Notes/House-8788 for house)
"""

from __future__ import annotations

import os
from pathlib import Path

PROFILE = (os.environ.get("MUSIC_SORTER_PROFILE") or "house").strip().lower() or "house"
IS_HOUSE = PROFILE == "house"

_TRUTHY = {"1", "true", "yes", "on"}


def readonly() -> bool:
    """Evaluated live so tests can toggle it with monkeypatch.setenv."""
    return os.environ.get("MUSIC_SORTER_READONLY", "").strip().lower() in _TRUTHY


class ReadOnlyError(RuntimeError):
    """Raised when a write is attempted while MUSIC_SORTER_READONLY is on."""


READONLY_MESSAGE = (
    "Read-only (VDJ open): MUSIC_SORTER_READONLY=1 — this build never writes "
    "VirtualDJ data or moves/copies/deletes library files."
)


def assert_not_readonly(action: str = "write") -> None:
    if readonly():
        raise ReadOnlyError(f"{READONLY_MESSAGE} Blocked: {action}.")


# --- House defaults -------------------------------------------------------

# Sort destinations are the EXISTING subfolders of the House library
# (/Music/DJ/Music/House: Amped, Bassy, Chill/..., Energy/...), plus an explicit
# "New folder in House" option (max 3 created without asking Kirill; see
# sorter.house_folders). Sort COPIES the track and keeps the original.

# Default Add Cues crate shown first / pre-selected.
DEFAULT_ADD_CUES_CRATE = "Sauna Fest House"

# Organic house target tempo.
TARGET_BPM = 120.0
TARGET_BPM_TOLERANCE = 5.0  # => 115–125
TARGET_BPM_MIN = TARGET_BPM - TARGET_BPM_TOLERANCE
TARGET_BPM_MAX = TARGET_BPM + TARGET_BPM_TOLERANCE

GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_FALLBACKS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)

GENRE_LABEL = "organic house / melodic house / deep house"


def house_sort_folders() -> list[str]:
    """Relative paths of the real House subfolders (nested ok)."""
    from . import house_folders

    return house_folders.list_existing_folders()


def _new_folder_state() -> dict:
    from . import house_folders

    return house_folders.new_folder_state()


def public_profile() -> dict:
    """Small dict for /api/health + the UI."""
    return {
        "name": PROFILE,
        "readonly": readonly(),
        "readonly_message": READONLY_MESSAGE if readonly() else "",
        "sort_folders": house_sort_folders() if IS_HOUSE else [],
        "new_folders": _new_folder_state() if IS_HOUSE else None,
        "sort_copies": True if IS_HOUSE else False,
        "default_add_cues_crate": DEFAULT_ADD_CUES_CRATE if IS_HOUSE else "",
        "target_bpm": TARGET_BPM if IS_HOUSE else None,
        "target_bpm_min": TARGET_BPM_MIN if IS_HOUSE else None,
        "target_bpm_max": TARGET_BPM_MAX if IS_HOUSE else None,
        "gemini_model": GEMINI_MODEL if IS_HOUSE else None,
        "notes_dir": os.environ.get("MUSIC_SORTER_NOTES_DIR", ""),
    }


def readonly_dry_run(fn):
    """Decorator: when read-only, force ``dry_run=True`` on a mutator (no I/O happens)."""
    import functools
    import inspect

    sig = inspect.signature(fn)
    if "dry_run" not in sig.parameters:
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if readonly():
            bound = sig.bind_partial(*args, **kwargs)
            bound.arguments["dry_run"] = True
            return fn(*bound.args, **bound.kwargs)
        return fn(*args, **kwargs)

    wrapper.__readonly_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper


def wrap_module_mutators(namespace: dict, module_name: str) -> list[str]:
    """Apply ``readonly_dry_run`` to every function in ``namespace`` that defines dry_run."""
    import inspect

    wrapped: list[str] = []
    for name, obj in list(namespace.items()):
        if (
            inspect.isfunction(obj)
            and obj.__module__ == module_name
            and "dry_run" in inspect.signature(obj).parameters
            and not getattr(obj, "__readonly_wrapped__", False)
        ):
            namespace[name] = readonly_dry_run(obj)
            wrapped.append(name)
    return wrapped
