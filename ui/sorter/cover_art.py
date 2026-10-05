"""Album cover lookup for the song header banner. READ-ONLY with respect to VirtualDJ.

Order:
  1. VirtualDJ's own cover cache (``~/Library/Application Support/VirtualDJ/Cache/Covers``), matched
     by "Artist - Title" (VDJ prefixes some files with a two-letter source code such as ``IT-``).
  2. Art embedded in the audio file, extracted with ffmpeg into OUR cache dir (never into the
     music library, never into VirtualDJ's folders) and scaled down to a small JPEG.
  3. Nothing (the UI keeps its placeholder).

``database.xml`` is never opened for writing here; ``Infos Cover`` is only a flag in VDJ's file.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Optional

from .config import DJ_NOTES_ROOT

VDJ_COVERS_DIR = Path.home() / "Library" / "Application Support" / "VirtualDJ" / "Cache" / "Covers"
COVER_CACHE_DIR = DJ_NOTES_ROOT / "cover-cache"
COVER_PX = 320
FFMPEG_TIMEOUT_S = 20
AUDIO_SUFFIXES = {".flac", ".mp3", ".m4a", ".aac", ".wav", ".aiff", ".aif", ".ogg", ".opus", ".wma", ".mp4"}

_PREFIX_RE = re.compile(r"^[A-Za-z]{2}-(?=\S)")
_TRACKNO_RE = re.compile(r"^\s*\d{1,3}[.\-\s]+\s*")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def artist_title_from_filename(path: Path) -> tuple[str, str]:
    stem = _TRACKNO_RE.sub("", path.stem)
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return "", stem.strip()


def find_vdj_cached_cover(artist: str, title: str, covers_dir: Optional[Path] = None) -> Optional[Path]:
    d = covers_dir or VDJ_COVERS_DIR
    if not title or not d.is_dir():
        return None
    want = _norm(f"{artist} {title}") if artist else _norm(title)
    want_title = _norm(title)
    best: Optional[Path] = None
    try:
        entries = list(d.iterdir())
    except OSError:
        return None
    for f in entries:
        if f.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        stem = _PREFIX_RE.sub("", f.stem)
        n = _norm(stem)
        if n == want:
            return f
        if artist is None or not artist:
            if n.endswith(want_title) and best is None:
                best = f
    return best


def _cache_key(audio: Path) -> str:
    st = audio.stat()
    raw = f"{audio}|{st.st_size}|{int(st.st_mtime)}|{COVER_PX}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def extract_embedded_cover(audio: Path, cache_dir: Optional[Path] = None) -> Optional[Path]:
    """Extract embedded art to the cache dir (once). Returns the cached JPEG, or None if there is none."""
    cdir = cache_dir or COVER_CACHE_DIR
    if audio.suffix.lower() not in AUDIO_SUFFIXES:
        return None
    key = _cache_key(audio)
    out = cdir / f"{key}.jpg"
    none_marker = cdir / f"{key}.none"
    if out.is_file() and out.stat().st_size > 0:
        return out
    if none_marker.exists():
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cdir.mkdir(parents=True, exist_ok=True)
    tmp = cdir / f"{key}.tmp.jpg"
    try:
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(audio),
                "-an", "-map", "0:v:0?", "-frames:v", "1",
                "-vf", f"scale='min({COVER_PX},iw)':-2", "-q:v", "3", str(tmp),
            ],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        tmp.unlink(missing_ok=True)
        return None
    if tmp.is_file() and tmp.stat().st_size > 0:
        tmp.replace(out)
        return out
    tmp.unlink(missing_ok=True)
    none_marker.write_text("no embedded art\n")
    return None


def find_cover(
    audio_path: str | Path,
    *,
    artist: str = "",
    title: str = "",
    covers_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> Optional[Path]:
    audio = Path(audio_path).expanduser()
    if not audio.is_file():
        return None
    if not (artist or title):
        artist, title = artist_title_from_filename(audio)
    hit = find_vdj_cached_cover(artist, title, covers_dir)
    if hit is not None:
        return hit
    return extract_embedded_cover(audio.resolve(), cache_dir)
