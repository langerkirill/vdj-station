"""The 8 song-color lanes Kirill locked for Music Sorter."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import profile as _profile

# Color name → Zouk folder. Confirming a lane sorts here + Cues Sorted + UserColor.
LANE_FOLDERS: dict[str, str] = {
    "blue": "Chill",
    "cyan": "Trancy",
    "green": "Energy",
    "yellow": "Intense",
    "orange": "Lamba",
    "magenta": "Kizouk",
    "pink": "R&B",
    "red": "Remixes",
}

LANE_LABELS: dict[str, str] = {
    "blue": "chill",
    "cyan": "trancy",
    "green": "energy",
    "yellow": "intense",
    "orange": "lamba / afro",
    "magenta": "kizouk",
    "pink": "r&b / hip-hop",
    "red": "remix / classics",
}

# First-folder aliases → lane (Gemini folder rec → color).
_FOLDER_TO_LANE: dict[str, str] = {
    "chill": "blue",
    "beautiful sound": "blue",
    "beautiful": "blue",
    "longing": "blue",
    "mellow": "blue",
    "lounge": "blue",
    "jazzy": "blue",
    "trancy": "cyan",
    "trippy": "cyan",
    "experimental": "cyan",
    "energy": "green",
    "groovy": "green",
    "intense": "yellow",
    "lamba": "orange",
    "tribal": "orange",
    "world": "orange",
    "kizouk": "magenta",
    "neo zouk": "magenta",
    "r&b": "pink",
    "rnb": "pink",
    "jr&b": "pink",
    "hip hoppy": "pink",
    "hiphop": "pink",
    "hip hop": "pink",
    "remixes": "red",
    "classics": "red",
    "nostalgia": "red",
    "pop": "red",
}


def normalize_lane(value: str | None) -> Optional[str]:
    if _profile.IS_HOUSE:
        return None  # House fork: no lanes
    lane = (value or "").strip().lower()
    return lane if lane in LANE_FOLDERS else None


# Chill and Energy are roots only — never a sort dest. Always a child.
ROOTS_NEED_SUBDIR = {"chill", "energy"}
LANE_DEFAULT_SUBDIRS: dict[str, str] = {
    "blue": "Chill/Lounge",
    "green": "Energy/Light",
}


def folder_for_lane(lane: str) -> str:
    if _profile.IS_HOUSE:
        raise ValueError("Lanes are not used in the House build — pick a House folder")
    key = normalize_lane(lane)
    if not key:
        raise ValueError(f"Unknown lane: {lane}")
    return LANE_DEFAULT_SUBDIRS.get(key) or LANE_FOLDERS[key]


def is_root_only_folder(relative_path: str | None) -> bool:
    parts = Path(str(relative_path or "").replace("\\", "/")).parts
    return len(parts) == 1 and parts[0].strip().lower() in ROOTS_NEED_SUBDIR


def ensure_sort_folder(relative_path: str | None, lane: str | None = None) -> str:
    """Keep the clicked leaf. Only deepen Energy/Chill roots. Never remap via lane.

    House fork: the destination MUST be an existing House subfolder.
    """
    if _profile.IS_HOUSE:
        return ensure_house_sort_folder(relative_path)
    raw = str(relative_path or "").strip().replace("\\", "/")
    parts = [p for p in Path(raw).parts if p]
    if len(parts) >= 2:
        return "/".join(parts)
    if parts:
        root_name = parts[0].strip().lower()
        if root_name == "energy":
            return "Energy/Light"
        if root_name == "chill":
            return "Chill/Lounge"
        return parts[0]
    key = normalize_lane(lane)
    if key and key in LANE_DEFAULT_SUBDIRS:
        return LANE_DEFAULT_SUBDIRS[key]
    if key:
        return LANE_FOLDERS[key]
    raise ValueError("Pick a subfolder under Chill or Energy")


def ensure_house_sort_folder(relative_path: str | None, *, new_folder: bool = False) -> str:
    """Return the canonical relative House path of an EXISTING subfolder.

    With ``new_folder=True`` the path is validated as a NEW folder instead (nothing
    is created here; creation happens on the first real sort).
    """
    from . import house_folders

    return house_folders.resolve_destination_rel(
        str(relative_path or ""), new_folder=new_folder
    )


def lane_from_folder_path(relative_path: str | None) -> Optional[str]:
    if _profile.IS_HOUSE:
        return None
    if not relative_path:
        return None
    first = Path(str(relative_path).replace("\\", "/")).parts
    if not first:
        return None
    return _FOLDER_TO_LANE.get(first[0].strip().lower())



# Infos UserColor ARGB ints (alpha 0xFF). White / unset is not a lane.
LANE_USER_COLORS: dict[str, str] = {
    "4278190335": "blue",
    "4278255615": "cyan",
    "4278255360": "green",
    "4294967040": "yellow",
    "4294934272": "orange",
    "4294902015": "magenta",
    "4294941081": "pink",
    "4294901760": "red",
}
_NOT_A_LANE = {"", "1", "4294967295"}


def lane_from_user_color(value: str | None) -> Optional[str]:
    """Proper 8-lane color, or None for VDJ-white / unset / unknown."""
    if _profile.IS_HOUSE:
        return None
    raw = (value or "").strip()
    if not raw or raw in _NOT_A_LANE:
        return None
    return LANE_USER_COLORS.get(raw)
