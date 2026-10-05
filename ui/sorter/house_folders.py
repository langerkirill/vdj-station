"""House sort destinations (HOUSE FORK).

Destinations are the EXISTING subfolders of ``Music/House`` (nested ok), plus an
explicit "New folder in House" option. New folders are:
  * created only on the first real sort into them (never on dry-run / read-only),
  * validated (no separators, no '..', not an existing name, only directly under
    House or under an existing House subfolder),
  * NOT capped any more (Kirill lifted the 3-folder limit); the created folders are still listed
    in the House notes dir for reference.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import config as _config

MAX_NEW_FOLDERS = None  # no cap on how many House folders can be created or picked
NEW_FOLDERS_FILE = "new_house_folders.json"
MAX_NAME_LEN = 60
LIST_MAX_DEPTH = 3

_lock = threading.Lock()


class NewFolderCapError(ValueError):
    """Kept for old imports; nothing raises it now that the new-folder cap is gone."""


def house_root() -> Path:
    return Path(_config.LIBRARIES["House"])


def _skip(name: str) -> bool:
    return name.startswith(".") or name in _config.LIBRARY_SKIP_DIR_NAMES


def _child_dirs(directory: Path) -> list[Path]:
    try:
        kids = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    return [k for k in kids if k.is_dir() and not _skip(k.name)]


def list_existing_folders(max_depth: int = LIST_MAX_DEPTH) -> list[str]:
    """Flat, sorted list of relative posix paths of real House subfolders."""
    root = house_root()
    out: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for kid in _child_dirs(directory):
            out.append(kid.relative_to(root).as_posix())
            walk(kid, depth + 1)

    if root.is_dir():
        walk(root, 1)
    return out


def _clean_parts(rel: str | None) -> list[str]:
    original = str(rel or "").replace("\\", "/")
    raw = original.strip().strip("/")
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    # A full path (e.g. "/Volumes/Music/DJs/House/Energy/Housey", pasted or suggested by an AI) is never taken
    # literally: everything must resolve UNDER the House library root, so keep only what follows its "House" folder.
    if original.strip().startswith("/") or (parts and parts[0] in {"Volumes", "Users"}):
        root_name = house_root().name.lower()
        idx = max((i for i, p in enumerate(parts) if p.lower() == root_name), default=-1)
        if idx < 0:
            raise ValueError(
                f"'{original.strip()}' is not inside the House library ({house_root()}). "
                "Pick a folder from the list or use 'New folder in House'."
            )
        parts = parts[idx + 1 :]
    return parts


def canonical_existing_folder(rel: str | None) -> str:
    """Return the on-disk-cased relative path of an EXISTING House subfolder."""
    parts = _clean_parts(rel)
    if not parts:
        raise ValueError("Pick a House folder (or use 'New folder in House').")
    if any(p in {".", ".."} for p in parts):
        raise ValueError("Invalid House folder path.")
    current = house_root()
    canon: list[str] = []
    for part in parts:
        match = None
        for kid in _child_dirs(current):
            if kid.name.lower() == part.lower():
                match = kid
                break
        if match is None:
            raise ValueError(
                f"'{'/'.join(parts)}' is not an existing House subfolder. "
                "Pick one from the list or use 'New folder in House'."
            )
        canon.append(match.name)
        current = match
    return "/".join(canon)


def _validate_folder_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("New folder name cannot be empty.")
    if cleaned in {".", ".."} or ".." in cleaned:
        raise ValueError("New folder name cannot contain '..'.")
    if any(ch in cleaned for ch in ("/", "\\", ":", "\0")):
        raise ValueError("New folder name cannot contain path separators or ':'.")
    if cleaned.startswith("."):
        raise ValueError("Hidden folder names are not allowed.")
    if cleaned in _config.LIBRARY_SKIP_DIR_NAMES:
        raise ValueError(f"Reserved folder name: {cleaned}")
    if len(cleaned) > MAX_NAME_LEN:
        raise ValueError(f"New folder name is too long (max {MAX_NAME_LEN}).")
    return cleaned


def validate_new_folder(parent_rel: str | None, name: str) -> tuple[str, str]:
    """Validate a NEW House folder. Returns (canonical_parent_rel, name). Creates nothing."""
    cleaned = _validate_folder_name(name)
    parent = _clean_parts(parent_rel)
    canon_parent = canonical_existing_folder("/".join(parent)) if parent else ""
    existing_names = {Path(p).name.lower() for p in list_existing_folders(LIST_MAX_DEPTH + 1)}
    if cleaned.lower() in existing_names:
        raise ValueError(
            f"A House folder named '{cleaned}' already exists — pick it from the list."
        )
    return canon_parent, cleaned


def split_new_folder_path(rel: str) -> tuple[str, str]:
    parts = _clean_parts(rel)
    if not parts:
        raise ValueError("New folder name cannot be empty.")
    return "/".join(parts[:-1]), parts[-1]


# Group folders: they only hold sub-folders (Chill/Journey, Energy/Housey, ...). A song is always copied into one
# of the real sub-folders, never into the group folder itself.
GROUP_ROOT_FOLDERS = ("Chill", "Energy")


def group_root_subfolders(canon: str) -> list[str]:
    root = house_root() / canon
    return [f"{canon}/{k.name}" for k in _child_dirs(root)] if root.is_dir() else []


def refuse_group_root(canon: str) -> str:
    if canon.strip("/").lower() in {g.lower() for g in GROUP_ROOT_FOLDERS}:
        subs = group_root_subfolders(canon.strip("/"))
        hint = f" Pick one of: {', '.join(subs)}." if subs else ""
        raise ValueError(
            f"'{canon}' is a group folder, not a destination.{hint}"
        )
    return canon


def resolve_destination_rel(rel: str, *, new_folder: bool = False) -> str:
    """Canonical relative House path for a sort destination (validates, creates nothing)."""
    if new_folder:
        existing = existing_folder_for_new(rel)
        if existing:  # the folder (or a same-named one) is already there: just use it
            return refuse_group_root(existing)
        parent, name = split_new_folder_path(rel)
        canon_parent, cleaned = validate_new_folder(parent, name)
        return f"{canon_parent}/{cleaned}" if canon_parent else cleaned
    return refuse_group_root(canonical_existing_folder(rel))


def existing_folder_for_new(rel: str) -> str | None:
    """A sort must never fail because the 'new' folder already exists (stale 'new' flag after the
    first sort created it, different case, or a similar name). Returns the EXISTING canonical
    relative path to use instead, or None when the folder really is new."""
    try:
        return canonical_existing_folder(rel)  # exact path, any letter case
    except ValueError:
        pass
    parts = _clean_parts(rel)
    if not parts:
        return None
    leaf = parts[-1]
    want = leaf.lower()
    same_leaf = [p for p in list_existing_folders(LIST_MAX_DEPTH + 1) if p.rsplit("/", 1)[-1].lower() == want]
    if same_leaf:
        return same_leaf[0]
    return find_similar_existing(leaf)


# --- persisted "new folders created" counter ---------------------------------

def _state_path() -> Path:
    return Path(_config.DJ_NOTES_ROOT) / NEW_FOLDERS_FILE


def new_folder_state() -> dict[str, Any]:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        folders = [str(f) for f in data.get("folders", [])]
    except (OSError, ValueError, AttributeError):
        folders = []
    return {
        "count": len(folders),
        "max": None,
        "remaining": None,  # unlimited
        "unlimited": True,
        "folders": folders,
    }


def _write_state(folders: list[str]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".new_house_folders.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"max": None, "folders": folders}, fh, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def assert_new_folder_allowed() -> None:
    """No cap: creating another House folder is always allowed."""
    return None


def claim_new_folder(rel: str) -> Path:
    """Create the validated NEW folder and persist the count. Call only on a real sort."""
    with _lock:
        canon = resolve_destination_rel(rel, new_folder=True)
        path = house_root() / canon
        if path.is_dir():  # already exists: nothing to create, nothing counted
            return path
        assert_new_folder_allowed()
        path.mkdir(parents=False, exist_ok=False)
        folders = new_folder_state()["folders"] + [canon]
        _write_state(folders)
        try:  # every new folder gets its own distinct folder color (never blocks the sort)
            from . import house_colors

            house_colors.ensure_folder_color(canon)
        except Exception:  # pragma: no cover - cosmetic only
            pass
        return path


def release_new_folder(rel: str) -> None:
    """Undo a claim after a failed sort (removes the folder only if still empty)."""
    with _lock:
        canon = "/".join(_clean_parts(rel))
        path = house_root() / canon
        try:
            path.rmdir()
        except OSError:
            return
        folders = [f for f in new_folder_state()["folders"] if f != canon]
        _write_state(folders)


# --- tag chip -> "New folder: <Tag>" suggestion (creates NOTHING) -------------------

def tag_to_folder_name(tag: str) -> str:
    """Title Case, filesystem-safe folder name for a Gemini tag ('' if nothing usable)."""
    text = unicodedata.normalize("NFKC", str(tag or ""))
    text = re.sub(r"[\x00-\x1f/\\:<>\"|?*]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lstrip(".").strip()
    words = []
    for w in text.split(" "):
        parts = [p[:1].upper() + p[1:].lower() for p in w.split("-")]
        words.append("-".join(parts))
    name = " ".join(words).strip()[:MAX_NAME_LEN].strip()
    if name in _config.LIBRARY_SKIP_DIR_NAMES or name in {".", ".."}:
        return ""
    return name


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_similar_existing(name: str) -> str | None:
    """An EXISTING House folder (any depth) whose leaf equals ``name`` ignoring case,
    spacing/punctuation and a trailing plural 's' (Hypnotic ~ hypnotic ~ Hypnotics)."""
    want = _key(name)
    if not want:
        return None
    for rel in list_existing_folders(LIST_MAX_DEPTH + 1):
        leaf = _key(rel.rsplit("/", 1)[-1])
        if leaf and (leaf == want or leaf + "s" == want or leaf == want + "s"):
            return rel
    return None


def suggest_tag_folder(tag: str) -> dict[str, Any]:
    """What a tag chip may offer. Pure lookup - never creates a folder or touches the counter."""
    state = new_folder_state()
    base = {
        "tag": tag,
        "count": state["count"],
        "max": state["max"],
        "remaining": state["remaining"],
        "folders": state["folders"],
    }
    name = tag_to_folder_name(tag)
    if not name:
        return {**base, "status": "invalid", "name": "", "message": "That tag cannot be a folder name."}
    existing = find_similar_existing(name)
    if existing:
        return {
            **base,
            "status": "similar_exists",
            "name": name,
            "existing": existing,
            "message": f"A similar House folder already exists: {existing} - use it instead.",
        }
    return {**base, "status": "new", "name": name, "message": f"New folder: {name}"}
