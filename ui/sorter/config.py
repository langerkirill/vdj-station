"""Paths and library definitions for the music sorter."""

from __future__ import annotations

import os
from pathlib import Path

from . import profile as _profile

MUSIC_ROOT = Path.home() / "Music" / "DJ" / "Music"
DJ_ROOT = Path.home() / "Music" / "DJ"
CUES_ROOT = MUSIC_ROOT / "Cues"
READY_FOR_SORT = CUES_ROOT / "Ready For Sort"
ADD_CUES = CUES_ROOT / "Add Cues"
CUES_SORTED = CUES_ROOT / "Cues Sorted"
# The existing House library root (/Music/DJ/Music/House). In the House fork, sort
# COPIES into its existing subfolders (or a validated new one).
HOUSE_ROOT = MUSIC_ROOT / "House"
# TEST SEAM (never set in production): run a scratch server against a scratch House root so a
# "new folder" sort can be exercised without creating anything in the real library.
_house_override = os.environ.get("MUSIC_SORTER_HOUSE_ROOT", "").strip()
if _house_override:
    HOUSE_ROOT = Path(_house_override).expanduser()
# Destinations whose tracks are CUED (keep their cues / count as "sorted"). The House
# fork treats the House library exactly like Cues Sorted. Never include a
# "Cues Sorted House" folder: that folder must not exist or be created.
CUED_DESTINATION_ROOTS: tuple[Path, ...] = (CUES_SORTED, HOUSE_ROOT)
CUED_DESTINATION_NAMES: frozenset[str] = frozenset(p.name for p in CUED_DESTINATION_ROOTS)
NO_CUES_FOUND = CUES_ROOT / "No Cues Found"
AC_LOW_QUALITY = CUES_ROOT / "AC Low Quality"
LOW_QUALITY_SKIP = CUES_ROOT / "Low Quality Skip"
VDJ_DATABASE = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
)
# TEST SEAM (never set in production): point every database.xml read/write at a scratch copy.
_db_override = os.environ.get("MUSIC_SORTER_VDJ_DATABASE", "").strip()
if _db_override:
    VDJ_DATABASE = Path(_db_override).expanduser()

# Practice mixes + curated transition notes
MIXES_ROOT = Path.home() / "Music" / "Mixes"
# The REAL curated notes tree (read-only inputs for the transitions DB rebuild).
_REAL_NOTES_ROOT = DJ_ROOT / "Notes"
# Where THIS process writes caches / DBs / action log. House fork: isolated dir.
_notes_override = os.environ.get("MUSIC_SORTER_NOTES_DIR", "").strip()
if _notes_override:
    DJ_NOTES_ROOT = Path(_notes_override).expanduser()
elif _profile.IS_HOUSE:
    DJ_NOTES_ROOT = _REAL_NOTES_ROOT / "House-8788"
else:
    DJ_NOTES_ROOT = _REAL_NOTES_ROOT
if _profile.IS_HOUSE:
    TRANSITION_NOTES_DIRS = (
        _REAL_NOTES_ROOT / "House" / "Transitions",
        _REAL_NOTES_ROOT / "Transitions",
    )
else:
    TRANSITION_NOTES_DIRS = (
        _REAL_NOTES_ROOT / "Transitions",
        _REAL_NOTES_ROOT / "Zouk" / "Transitions",
        _REAL_NOTES_ROOT / "House" / "Transitions",
    )
VDJ_HISTORY_DIR = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "History"
)
VDJ_CACHE_DB = (
    Path.home() / "Library" / "Application Support" / "VirtualDJ" / "Cache" / "cache.db"
)
DJ_TRANSITIONS_CSV = VDJ_HISTORY_DIR / "dj_transitions.csv"
# Durable SQLite store for notes + history (survives UI restarts)
TRANSITIONS_DB_PATH = DJ_NOTES_ROOT / "transitions.db"

# Cue-pipeline stages used by the Add Cues review view.
CUE_STAGES: dict[str, Path] = {
    "add_cues": ADD_CUES,
    "ready_for_sort": READY_FOR_SORT,
    "no_cues_found": NO_CUES_FOUND,
    "ac_low_quality": AC_LOW_QUALITY,
    "low_quality_skip": LOW_QUALITY_SKIP,
}

# Subfolders under Add Cues that are not music crates.
ADD_CUES_SKIP_DIR_NAMES = {
    ".temp_download",
    "Playlists",
    "low_quality_backups",
    ".backups",
    "__pycache__",
}

# Destination libraries shown in the UI.
# House fork: ONE library — the existing House folder; destinations are restricted
# to its existing subfolders (+ a capped, validated 'New folder in House'). No Zouk library.
if _profile.IS_HOUSE:
    LIBRARIES: dict[str, Path] = {"House": HOUSE_ROOT}
else:
    LIBRARIES = {
        "House": HOUSE_ROOT,
        "Zouk": MUSIC_ROOT / "Zouk",
    }

# Event crates with real audio files (Moon, Silesian, Kizouk, …).
SETS_ROOT = MUSIC_ROOT / "Sets"

# HOUSE FORK "Add to Sauna Fest": the Sauna Fest SET folder (sibling of Sets/Goth, Sets/Kizouk,
# Sets/Pajamathon 2026 ...). Approved by Kirill; created on the first real copy-in.
# Change the name HERE only.
SAUNA_FEST_SET_NAME = "Sauna Fest"
SAUNA_FEST_SET_DIR = SETS_ROOT / SAUNA_FEST_SET_NAME
# Test seam (scratch servers only): send the Sauna Fest mirror copies somewhere else.
_sauna_override = os.environ.get("MUSIC_SORTER_SAUNA_FEST_DIR", "").strip()
if _sauna_override:
    SAUNA_FEST_SET_DIR = Path(_sauna_override).expanduser()
# Test-only escape hatch: the Sauna endpoint accepts a different set folder name ONLY if it
# starts with this prefix (so a scratch test can never touch a real set folder).
SAUNA_FEST_TEST_PREFIX = "ZZ TEST "


def assert_existing_audio(path: Path | str) -> Path:
    """Resolve an audio path that exists. Cue/grid/notes edits are allowed anywhere."""
    audio = Path(path).expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"Audio not found: {audio}")
    return audio

# DJ-utility folders — never candidates for assembled event mixes.
ASSEMBLE_SKIP_DIR_NAMES = {
    "Transitions",
    "30 Utility - Transitions",
}

# Directories that are tooling / junk — never shown as sort destinations.
LIBRARY_SKIP_DIR_NAMES = {
    ".git",
    ".claude",
    "__pycache__",
    "claude_scripts",
    "start",
    "low_quality_backups",
    ".backups",
    "Blvck spotdl",
    "Not Sorted",
    "Stefan Folders",
}

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus"}

# House fork: no Zouk vibe folders (kept as an empty set for import compat).
ZOUK_VIBE_FOLDERS: frozenset[str] = frozenset()
