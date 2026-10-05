"""Identity keys for songs already used in the Pajamathon live set.

Best for set can hide transitions that reuse a Friday/Saturday play or a
file already sitting in Sets/Pajamathon*/Played.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import AUDIO_EXTENSIONS, SETS_ROOT
from .library import is_pajamathon_event
from .transition_recs import track_block_keys
from .transitions_db import _split_artist_title


def _fold(text: str) -> str:
    """ASCII-fold so Meridiyün matches Meridiyun in practice labels."""
    s = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _strip_set_index(stem: str) -> str:
    return re.sub(r"^\d+[\s.\-]+", "", stem or "").strip()


def _primary_artist(artist: str) -> str:
    folded = _fold(artist).strip()
    if not folded:
        return ""
    return folded.split(",", 1)[0].strip()


def _artists_compatible(file_artist: str, tag_artist: str) -> bool:
    """True when tags are the same act, or a featured-credit superset."""
    a = _primary_artist(file_artist)
    b = _primary_artist(tag_artist)
    if not a or not b:
        return True
    if a == b:
        return True
    a_tokens = a.split()
    b_tokens = b.split()
    shorter, longer = (
        (a_tokens, b_tokens)
        if len(a_tokens) <= len(b_tokens)
        else (b_tokens, a_tokens)
    )
    return bool(shorter) and longer[: len(shorter)] == shorter


def _artist_title_from_stem(stem: str) -> tuple[str, str]:
    """Split a Played stem; drop duplicated 'Artist - Artist - Title'."""
    folded = _fold(_strip_set_index(stem))
    artist, title = _split_artist_title(folded)
    if artist and title:
        dup = f"{artist} - "
        while title.lower().startswith(dup.lower()):
            title = title[len(dup) :].strip()
    return artist, title


def iter_pajamathon_played_audio(
    sets_root: Path | None = None,
) -> list[Path]:
    """Audio files in each Pajamathon event's Played folder. Stems are skipped."""
    root = Path(sets_root or SETS_ROOT)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for event in sorted(root.iterdir()):
        if not event.is_dir() or not is_pajamathon_event(event.name):
            continue
        played = event / "Played"
        if not played.is_dir():
            continue
        for path in sorted(played.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            out.append(path)
    return out


def keys_for_played_file(path: Path | str) -> set[str]:
    """Block keys for one Played-folder file (numbered stem + artist/title)."""
    audio = Path(path)
    name = audio.name
    folded = _fold(name)
    keys = track_block_keys(path=str(audio), name=name)
    if folded != name:
        keys |= track_block_keys(path=str(audio), name=folded)
    artist, title = _artist_title_from_stem(audio.stem)
    if artist and title:
        keys |= track_block_keys(
            artist=artist,
            title=title,
            name=f"{artist} - {title}",
        )
    return keys


def keys_for_history_play(path: str, artist: str, title: str) -> set[str]:
    """Filename identity always. Tag identity only if it is the same act."""
    keys: set[str] = set()
    if path:
        keys |= keys_for_played_file(path)
    file_artist, _file_title = _artist_title_from_stem(Path(path).stem if path else "")
    if artist and title and not _artists_compatible(file_artist, artist):
        return keys
    artist_f = _fold(artist)
    title_f = _fold(title)
    display = (
        f"{artist_f} - {title_f}".strip(" -")
        if artist_f and title_f
        else (title_f or _fold(Path(path).name if path else ""))
    )
    if not display:
        return keys
    keys |= track_block_keys(
        path=path or "",
        artist=artist_f,
        title=title_f,
        name=display,
    )
    if artist != artist_f or title != title_f:
        raw_display = (
            f"{artist} - {title}".strip(" -")
            if (artist or "").strip() and (title or "").strip()
            else (title or (Path(path).name if path else ""))
        )
        keys |= track_block_keys(
            path=path or "",
            artist=artist or "",
            title=title or "",
            name=raw_display,
        )
    return keys


def practice_label_block_keys(label: str) -> set[str]:
    """Block keys for a Best-for-set from_track / to_track label."""
    text = (label or "").strip()
    if not text:
        return set()
    folded = _fold(text)
    keys = track_block_keys(name=text)
    if folded != text:
        keys |= track_block_keys(name=folded)
    artist, title = _split_artist_title(folded)
    if artist and title:
        keys |= track_block_keys(
            artist=artist,
            title=title,
            name=f"{artist} - {title}",
        )
    return keys


def live_set_played_block_keys(
    *,
    sets_root: Path | None = None,
    played_paths: Iterable[Path | str] | None = None,
    history_keys: set[str] | None = None,
) -> set[str]:
    """Union of Played-folder files and this weekend's Friday/Saturday plays."""
    keys: set[str] = set()
    paths: Iterable[Path | str]
    if played_paths is not None:
        paths = played_paths
    else:
        paths = iter_pajamathon_played_audio(sets_root=sets_root)
    for raw in paths:
        keys |= keys_for_played_file(raw)
    if history_keys is not None:
        keys |= set(history_keys)
    else:
        from .vdj_now_playing import recent_history_play_groups

        groups = recent_history_play_groups()
        for _lp, path, artist, title in groups.get("all") or []:
            keys |= keys_for_history_play(path, artist, title)
    return keys


def transition_uses_live_played(
    from_track: str,
    to_track: str,
    played_keys: set[str],
) -> bool:
    """True when either side of the blend was already used in the live set."""
    if not played_keys:
        return False
    if practice_label_block_keys(from_track) & played_keys:
        return True
    return bool(practice_label_block_keys(to_track) & played_keys)


def annotate_best_items_live_played(
    items: list[dict[str, Any]],
    *,
    played_keys: Optional[set[str]] = None,
    sets_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Copy Best-for-set rows and stamp live_played without mutating input."""
    keys = (
        played_keys
        if played_keys is not None
        else live_set_played_block_keys(sets_root=sets_root)
    )
    out: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        from_hit = bool(
            practice_label_block_keys(str(row.get("from_track") or "")) & keys
        )
        to_hit = bool(
            practice_label_block_keys(str(row.get("to_track") or "")) & keys
        )
        row["from_live_played"] = from_hit
        row["to_live_played"] = to_hit
        row["live_played"] = from_hit or to_hit
        out.append(row)
    return out


def filter_best_items_hide_live_played(
    items: list[dict[str, Any]],
    *,
    hide: bool,
    played_keys: Optional[set[str]] = None,
    sets_root: Path | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, hidden_count). When hide is false, rows stay, still annotated."""
    annotated = annotate_best_items_live_played(
        items, played_keys=played_keys, sets_root=sets_root
    )
    hidden = sum(1 for row in annotated if row.get("live_played"))
    if not hide:
        return annotated, hidden
    visible = [row for row in annotated if not row.get("live_played")]
    return visible, hidden
