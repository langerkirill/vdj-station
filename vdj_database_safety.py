"""Safety checks and low-memory VirtualDJ database.xml mutation."""

from __future__ import annotations

import fcntl
import gc
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence


VDJ_DATABASE_ROOT = "VirtualDJ_Database"
MANUAL_CUE_TYPES = {"cue", "loop"}

# Set VDJ_ALLOW_RUNNING_WRITES=1 only for emergency recovery with VDJ force-quit.
_ALLOW_RUNNING_WRITES_ENV = "VDJ_ALLOW_RUNNING_WRITES"


class VirtualDJRunningError(RuntimeError):
    """Raised when a database.xml write is refused because VirtualDJ is open."""


class ReadOnlyModeError(RuntimeError):
    """Raised when MUSIC_SORTER_READONLY is on (House fork) and a VDJ write is attempted."""


def readonly_mode() -> bool:
    """HOUSE FORK: MUSIC_SORTER_READONLY=1 forbids every VDJ write, unconditionally."""
    return os.environ.get("MUSIC_SORTER_READONLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _refuse_if_readonly(what: str) -> None:
    if readonly_mode():
        raise ReadOnlyModeError(
            "Read-only (VDJ open): MUSIC_SORTER_READONLY=1 — refusing to "
            f"{what}. This build never writes VirtualDJ files."
        )


def allow_vdj_running_writes() -> bool:
    """True only when emergency override env is explicitly enabled."""
    return os.environ.get(_ALLOW_RUNNING_WRITES_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_virtualdj_executable(comm: str) -> bool:
    """True only for a process whose EXECUTABLE is VirtualDJ (never its argv).

    ``comm`` is the executable path/name from ``ps -o comm=``: exactly ``VirtualDJ`` or a
    path ending in ``/Contents/MacOS/VirtualDJ``; ``VirtualDJ.exe`` only as an exact match.
    A shell or agent whose command line merely MENTIONS VirtualDJ.app has comm zsh/bash/...
    and never matches.
    """
    c = (comm or "").strip()
    return (
        c == "VirtualDJ"
        or c.endswith("/Contents/MacOS/VirtualDJ")
        or c == "VirtualDJ.exe"
    )


def is_virtualdj_running() -> bool:
    """
    Return True when a process whose executable is VirtualDJ is running.

    Uses ``ps -axo pid=,comm=`` (comm = executable path, NOT argv), so shells,
    agents, grep/pgrep or editors whose command line only mentions VirtualDJ or
    VirtualDJ.app can never false-positive.
    """
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,comm="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    for raw in result.stdout.splitlines():
        parts = raw.strip().split(None, 1)
        if len(parts) == 2 and is_virtualdj_executable(parts[1]):
            return True
    return False


def assert_safe_to_write_vdj_database() -> None:
    """
    Refuse database.xml mutations while VirtualDJ is running.

    Writing while VDJ is open is the main cause of the “database corrupted”
    popup and lost Cues Sorted clones: VDJ overwrites our edits on save/exit.
    """
    _refuse_if_readonly("write database.xml")
    if not is_virtualdj_running():
        return
    if allow_vdj_running_writes():
        return
    raise VirtualDJRunningError(
        "VirtualDJ is running — refusing to write database.xml. "
        "Close VirtualDJ completely, then retry. "
        "Writing while it is open causes the corruption popup and drops "
        "recent library entries. Emergency only: "
        f"{_ALLOW_RUNNING_WRITES_ENV}=1"
    )


# --- Auto-heal: VDJ "fix" can wipe a full library down to ~160KB -------------
# Full libraries here are ~30–40MB. Wipes are ~100–200KB. Use size as the primary signal
# so small test databases are not treated as catastrophic.
WIPE_SIZE_BYTES = 2_000_000  # under this → catastrophic wipe candidate
AUTO_GOLDEN_PREFIX = "database.xml.golden.auto."
AUTO_GOLDEN_KEEP = 8
LAST_GOOD_META_NAME = "database.xml.last-good.json"


def _vdj_home(database_path: os.PathLike | str) -> Path:
    return Path(database_path).expanduser().resolve().parent


def quick_database_fingerprint(database_path: os.PathLike | str) -> Dict[str, Any]:
    """
    Cheap health signal without full XML parse.

    Uses size + Song tag count from a binary scan (works on multi‑MB libraries).
    """
    path = Path(database_path)
    if not path.is_file():
        return {
            "exists": False,
            "size_bytes": 0,
            "song_count": 0,
            "has_crlf": False,
            "has_root": False,
            "healthy": False,
            "reason": "missing",
        }
    raw = path.read_bytes()
    size = len(raw)
    songs = raw.count(b"<Song")
    # Prefer exact CRLF count match; also accept head check for huge files.
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    has_crlf = lf == 0 or crlf == lf
    has_root = b"<VirtualDJ_Database" in raw[:512]
    reason = "ok"
    healthy = True
    if not has_root:
        healthy = False
        reason = "missing_root"
    elif size < WIPE_SIZE_BYTES:
        # Only treat as wipe when we *know* a much larger good copy exists nearby,
        # or the file is the classic near-empty stub. Callers that only have a tiny
        # test fixture remain healthy unless a golden is present for recovery tests.
        healthy = False
        reason = "wipe_size"
    elif not has_crlf:
        # LF-only full libraries make VDJ reset the DB.
        healthy = False
        reason = "missing_crlf"
    return {
        "exists": True,
        "size_bytes": size,
        "song_count": songs,
        "has_crlf": has_crlf,
        "has_root": has_root,
        "healthy": healthy,
        "reason": reason,
    }


def _candidate_recovery_files(vdj_dir: Path) -> List[Path]:
    """Prefer auto goldens, then manual goldens, then VDJ 'broken' full copies, then sorter backups."""
    patterns = [
        f"{AUTO_GOLDEN_PREFIX}*",
        "database.xml.golden.*",
        "database.xml.backup.*",
    ]
    found: List[Path] = []
    for pattern in patterns:
        found.extend(vdj_dir.glob(pattern))
    backup_dir = vdj_dir / "Backup"
    if backup_dir.is_dir():
        found.extend(backup_dir.glob("*broken database.xml"))
        found.extend(backup_dir.glob("*Database Backup*"))
    # Newest first among healthy candidates later
    return found


def find_best_database_recovery_source(
    database_path: os.PathLike | str,
) -> Optional[Path]:
    """Return the largest healthy recovery candidate, preferring recent auto goldens."""
    vdj_dir = _vdj_home(database_path)
    ranked: List[tuple] = []
    for cand in _candidate_recovery_files(vdj_dir):
        if not cand.is_file():
            continue
        if cand.resolve() == Path(database_path).expanduser().resolve():
            continue
        try:
            fp = quick_database_fingerprint(cand)
        except Exception:
            continue
        if not fp["healthy"]:
            continue
        # Prefer auto goldens, then explicit goldens, then everything else; then recency + size
        name = cand.name
        tier = 0
        if name.startswith(AUTO_GOLDEN_PREFIX):
            tier = 3
        elif "golden" in name:
            tier = 2
        elif "broken" in name:
            tier = 1
        mtime = cand.stat().st_mtime
        ranked.append((tier, mtime, fp["size_bytes"], fp["song_count"], cand))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3]), reverse=True)
    return ranked[0][4]


_LAST_AUTO_GOLDEN_MONO: float = time.monotonic()
_AUTO_GOLDEN_MIN_INTERVAL_SEC = 600.0


def snapshot_last_good_database(database_path: os.PathLike | str) -> Optional[Path]:
    """
    After a successful write, keep a rolling golden so wipe recovery needs no user.

    Safe to call often; copies are throttled so color/delete clicks stay fast.
    """
    global _LAST_AUTO_GOLDEN_MONO
    if readonly_mode():
        return None  # never write golden copies / meta into the VDJ folder
    now = time.monotonic()
    if now - _LAST_AUTO_GOLDEN_MONO < _AUTO_GOLDEN_MIN_INTERVAL_SEC:
        return None
    path = Path(database_path)
    if not path.is_file():
        return None
    fp = quick_database_fingerprint(path)
    if not fp["healthy"]:
        return None
    # Never snapshot while VDJ is open — it may be mid-rewrite to a wipe.
    if is_virtualdj_running() and not allow_vdj_running_writes():
        return None

    vdj_dir = path.parent
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = vdj_dir / f"{AUTO_GOLDEN_PREFIX}{ts}"
    shutil.copy2(path, dest)

    # Persist last-good meta for health UI / tests
    meta = {
        "path": str(dest),
        "size_bytes": fp["size_bytes"],
        "song_count": fp["song_count"],
        "source": str(path),
        "ts": ts,
    }
    try:
        import json

        (vdj_dir / LAST_GOOD_META_NAME).write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        pass

    # Rotate
    autos = sorted(
        vdj_dir.glob(f"{AUTO_GOLDEN_PREFIX}*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in autos[AUTO_GOLDEN_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
    _LAST_AUTO_GOLDEN_MONO = now
    return dest


def _is_strictly_better_recovery(source: Path, current_fp: Dict[str, Any]) -> bool:
    """Recovery source must be a real full library, not another tiny stub."""
    src = quick_database_fingerprint(source)
    if not src.get("has_root") or not src.get("has_crlf"):
        return False
    if src["size_bytes"] < WIPE_SIZE_BYTES:
        return False
    # Must be substantially larger than the wiped file (or current missing).
    return src["size_bytes"] >= max(current_fp.get("size_bytes", 0) * 5, WIPE_SIZE_BYTES)


def recover_vdj_database_if_wiped(
    database_path: os.PathLike | str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """
    If database.xml looks wiped/corrupt-small, restore the best full golden.

    Refuses to restore while VirtualDJ is running (unless emergency env), so we
    do not fight a live VDJ rewrite. Returns a status dict always.
    """
    path = Path(database_path)
    fp = quick_database_fingerprint(path)
    result: Dict[str, Any] = {
        "recovered": False,
        "needed": not fp["healthy"],
        "fingerprint": fp,
        "source": None,
        "error": None,
    }
    if fp["healthy"] and not force:
        return result
    if readonly_mode():
        result["error"] = "read-only mode: auto-recovery disabled (no VDJ writes)"
        return result

    if is_virtualdj_running() and not allow_vdj_running_writes():
        result["error"] = "virtualdj_running"
        return result

    source = find_best_database_recovery_source(path)
    if source is None or not _is_strictly_better_recovery(source, fp):
        # Tiny DBs with no full golden nearby are test fixtures / fresh installs —
        # not catastrophic wipes. Leave them alone so writers still work.
        result["error"] = "no_recovery_source"
        result["needed"] = fp.get("reason") in {"wipe_size", "missing_crlf", "missing_root"}
        return result

    # Stash the bad file for forensics
    try:
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if path.is_file():
            wiped_copy = path.parent / f"database.xml.wiped.{ts}"
            shutil.copy2(path, wiped_copy)
            result["wiped_saved_as"] = str(wiped_copy)
    except Exception:
        pass

    try:
        # Copy without going through write_vdj_database_text (already asserted VDJ closed).
        tmp = path.parent / f".database.xml.recovering.{os.getpid()}"
        shutil.copy2(source, tmp)
        try:
            assert_crlf_bytes(tmp.read_bytes(), "recovery source")
        except LineEndingError as exc:
            tmp.unlink(missing_ok=True)
            result["error"] = f"source_not_crlf: {exc}"
            return result
        src_fp = quick_database_fingerprint(tmp)
        if src_fp["size_bytes"] < WIPE_SIZE_BYTES:
            tmp.unlink(missing_ok=True)
            result["error"] = "source_unhealthy"
            return result
        os.replace(tmp, path)
        result["recovered"] = True
        result["source"] = str(source)
        result["fingerprint"] = quick_database_fingerprint(path)
        # After restore the file is large + CRLF → mark healthy even if fingerprint
        # thresholds change later.
        snapshot_last_good_database(path)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def ensure_healthy_vdj_database(database_path: os.PathLike | str) -> Dict[str, Any]:
    """Health check + auto-heal entry point used by Music Sorter and writers."""
    path = Path(database_path)
    # Surgical writes already know the live file is large — skip a 37MB reread.
    try:
        size = path.stat().st_size if path.is_file() else 0
    except OSError:
        size = 0
    if size >= WIPE_SIZE_BYTES:
        return {
            "ok": True,
            "recovered": False,
            "fingerprint": {
                "exists": True,
                "size_bytes": size,
                "healthy": True,
                "reason": "ok_size",
            },
        }
    fp = quick_database_fingerprint(path)
    if fp["healthy"]:
        return {"ok": True, "recovered": False, "fingerprint": fp}

    recovery = recover_vdj_database_if_wiped(path)
    if recovery.get("recovered"):
        return {
            "ok": True,
            "recovered": True,
            "fingerprint": recovery.get("fingerprint") or fp,
            "recovery": recovery,
        }

    # No full golden available: allow small/test DBs to proceed; flag real wipes
    # only when a better source exists but restore failed.
    if recovery.get("error") == "no_recovery_source" and fp.get("reason") == "wipe_size":
        return {
            "ok": True,
            "recovered": False,
            "fingerprint": {**fp, "healthy": True, "reason": "ok_small_or_test"},
            "recovery": recovery,
        }

    return {
        "ok": False,
        "recovered": False,
        "fingerprint": recovery.get("fingerprint") or fp,
        "recovery": recovery,
    }


# Match Song open tags. FilePath may contain XML entities.
_SONG_OPEN_RE = re.compile(
    r"<Song\b(?P<attrs>[^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_FILEPATH_RE = re.compile(
    r'\bFilePath\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)


def _unescape_xml_attr(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def normalize_database_path(file_path: str) -> str:
    return unicodedata.normalize("NFC", file_path)


def sanitize_vdj_database_bytes(raw: bytes) -> bytes:
    """Drop NUL bytes so ElementTree can parse (trailing 0x00 breaks color edits)."""
    if not raw or b"\x00" not in raw:
        return raw
    return raw.replace(b"\x00", b"")


class LineEndingError(ValueError):
    """database.xml bytes contain bare-LF (or bare-CR) line endings.

    VirtualDJ's own database.xml is CRLF on every line. A file with even one
    bare-LF line is rejected by VirtualDJ ("broken database") on next start.
    """


def legacy_fixture_mode() -> bool:
    """MUSIC_SORTER_ALLOW_LF_DB=1 turns the CRLF / Song-form gates (and healing) off.

    Only for legacy unit-test fixtures (LF, column-0 <Song>). Never set in production.
    """
    return os.environ.get("MUSIC_SORTER_ALLOW_LF_DB", "").strip() in {"1", "true", "yes"}


def line_ending_counts(raw: bytes) -> Dict[str, int]:
    return {
        "cr": raw.count(b"\r"),
        "lf": raw.count(b"\n"),
        "crlf": raw.count(b"\r\n"),
    }


def assert_crlf_bytes(raw: bytes, what: str = "database.xml") -> Dict[str, int]:
    """Hard gate: every line ending must be CRLF (CR count == LF count == CRLF count).

    ``MUSIC_SORTER_ALLOW_LF_DB=1`` disables the gate; it exists only so legacy
    LF-only unit-test fixtures keep working and is never set in production.
    """
    counts = line_ending_counts(raw)
    if legacy_fixture_mode():
        return counts
    if counts["cr"] == counts["lf"] == counts["crlf"]:
        return counts
    bare_lf = counts["lf"] - counts["crlf"]
    bare_cr = counts["cr"] - counts["crlf"]
    raise LineEndingError(
        f"{what} line endings are not pure CRLF (CR={counts['cr']}, LF={counts['lf']}, "
        f"bare-LF lines={bare_lf}, bare-CR={bare_cr}). VirtualDJ rejects such a file, "
        "so nothing was written."
    )


# --- VirtualDJ's own Song-row form ------------------------------------------------
#   CRLF line breaks; " <Song ...>" (exactly ONE leading space); children indented
#   two spaces; " </Song>" (one space); <Comment> directly after <Infos>
#   (Tags, Infos, Comment, Scan, Poi...). Anything else makes VDJ reject the file.

_SONG_OPEN_ANY_RE = re.compile(rb"<Song\b")
_SONG_OPEN_BAD_LINE_RE = re.compile(rb"\r\n[ \t]*<Song\b")
_SONG_CLOSE_BAD_LINE_RE = re.compile(rb"\r\n[ \t]*</Song>")
_COMMENT_LINE_RE = re.compile(rb"\r\n  <Comment\b")


def canonical_song_block(song_xml: str) -> str:
    """Return one Song block (``<Song ...>`` .. ``</Song>``) in VDJ's exact form.

    Pure CRLF, children at two spaces, closing line ' </Song>', Comment moved
    right after Infos. Blocks with multi-line children are only CRLF-normalized.
    The caller supplies the single leading space before ``<Song`` (a block span
    from _find_song_span starts at ``<Song``).
    """
    text = to_crlf(song_xml.strip())
    lines = text.split("\r\n")
    if len(lines) < 2 or not lines[0].lstrip().startswith("<Song") or lines[-1].strip() != "</Song>":
        return text
    children = [ln.strip() for ln in lines[1:-1] if ln.strip()]
    if any(not ln.startswith("<") for ln in children):
        return text
    comments = [ln for ln in children if ln.startswith("<Comment")]
    rest = [ln for ln in children if not ln.startswith("<Comment")]
    if comments:
        at = 0
        for i, ln in enumerate(rest):
            if ln.startswith("<Infos"):
                at = i + 1
                break
            if ln.startswith("<Tags"):
                at = i + 1
        rest[at:at] = comments
    out = [lines[0].strip()] + ["  " + ln for ln in rest] + [" </Song>"]
    return "\r\n".join(out)


_XML_DECL = b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
_HEADER_RE = re.compile(rb'<\?xml version="1\.0" encoding="UTF-8"\?>\r\n<VirtualDJ_Database Version="\d+">\r\n')
# A blank / whitespace-only line BETWEEN elements makes VDJ report a corrupted database.
# (Blank lines inside a multi-line <Comment> text are VDJ's own and are allowed: the line
# before them does not end with a tag's ">".)
_BLANK_BETWEEN_RE = re.compile(rb">[ \t]*\r\n(?:[ \t]*\r\n)+")
_CHILD_RANK = {"Tags": 0, "Infos": 1, "Comment": 2, "Scan": 3, "Poi": 4, "Link": 5}
_INFOS_ORDER = ["SongLength", "LastModified", "FirstSeen", "FirstPlay", "LastPlay", "PlayCount",
                "Bitrate", "UserColor", "Cover"]
_POI_ORDER = ["Name", "Pos", "Num", "Color", "Type", "Size", "Slot"]
_ROW_TOKEN_RE = re.compile(rb"\r\n(?: <Song\b| </Song>|  <(\w+)([^\r\n]*))")
_ATTR_NAME_RE = re.compile(rb'\s(\w+)="')


def _attr_order_ok(attrs: bytes, order: List[str]) -> bool:
    last = -1
    for m in _ATTR_NAME_RE.finditer(attrs):
        name = m.group(1).decode("ascii", "ignore")
        if name not in order:
            continue
        i = order.index(name)
        if i < last:
            return False
        last = i
    return True


def song_form_problems(raw: bytes) -> List[str]:
    """Why ``raw`` is not in VDJ's Song-row form (empty list = fine)."""
    problems: List[str] = []
    if not _HEADER_RE.match(raw):
        problems.append(
            'header must be <?xml version="1.0" encoding="UTF-8"?> then <VirtualDJ_Database Version="2026"> on their own CRLF lines'
        )
    if not raw.endswith(b"</VirtualDJ_Database>\r\n"):
        problems.append("file must end with </VirtualDJ_Database> + CRLF (no extra lines)")
    blank = len(_BLANK_BETWEEN_RE.findall(raw))
    if blank:
        problems.append(f"{blank} empty / whitespace-only line(s) between elements")
    bad_order = bad_infos = bad_poi = 0
    last_rank = -1
    for m in _ROW_TOKEN_RE.finditer(raw):
        tag = m.group(1)
        if tag is None:
            last_rank = -1
            continue
        name = tag.decode("ascii", "ignore")
        rank = _CHILD_RANK.get(name)
        if rank is not None:
            if rank < last_rank:
                bad_order += 1
            last_rank = max(last_rank, rank)
        if name == "Infos":
            if not _attr_order_ok(m.group(2), _INFOS_ORDER):
                bad_infos += 1
        elif name == "Poi":
            attrs = m.group(2)
            if (b'Type="cue"' in attrs or b'Type="loop"' in attrs) and not _attr_order_ok(attrs, _POI_ORDER):
                bad_poi += 1
    if bad_order:
        problems.append(f"{bad_order} Song child(ren) out of order (Tags, Infos, Comment, Scan, Poi, Link)")
    if bad_infos:
        problems.append(f"{bad_infos} <Infos> row(s) with attributes out of VDJ's order")
    if bad_poi:
        problems.append(f"{bad_poi} cue/loop <Poi> row(s) with attributes out of VDJ's order")
    total_open = len(_SONG_OPEN_ANY_RE.findall(raw))
    good_open = raw.count(b"\r\n <Song") - len(re.findall(rb"\r\n  +<Song", raw))
    if good_open != total_open:
        problems.append(
            f"{total_open - good_open} <Song> row(s) are not indented by exactly one space"
        )
    total_close = raw.count(b"</Song>")
    good_close = raw.count(b"\r\n </Song>") - len(re.findall(rb"\r\n  +</Song>", raw))
    if good_close != total_close:
        problems.append(
            f"{total_close - good_close} </Song> line(s) are not indented by exactly one space"
        )
    late = _late_comment_spans(raw)
    if late:
        problems.append(f"{len(late)} <Comment> element(s) sit after Scan/Poi instead of right after Infos")
    return problems


def _late_comment_spans(raw: bytes) -> List[tuple]:
    """(song_start, song_end) for each Song whose Comment is after a Scan/Poi line."""
    spans: List[tuple] = []
    seen_songs = set()
    for m in _COMMENT_LINE_RE.finditer(raw):
        c = m.start()
        s0 = raw.rfind(b"<Song", 0, c)
        if s0 < 0 or s0 in seen_songs:
            continue
        scan = raw.find(b"\r\n  <Scan", s0, c)
        poi = raw.find(b"\r\n  <Poi", s0, c)
        if scan >= 0 or poi >= 0:
            seen_songs.add(s0)
            e0 = raw.find(b"</Song>", c)
            if e0 >= 0:
                spans.append((s0, e0 + len(b"</Song>")))
    return spans


def assert_vdj_song_form(raw: bytes, what: str = "database.xml") -> None:
    if legacy_fixture_mode():
        return
    problems = song_form_problems(raw)
    if problems:
        raise LineEndingError(
            f"{what} is not in VirtualDJ's own Song form: " + "; ".join(problems)
            + ". VirtualDJ rejects such a file, so nothing was written."
        )


def heal_song_form(raw: bytes) -> bytes:
    """Fix the seams a splice can leave (stray indentation, late Comment) before writing."""
    if legacy_fixture_mode():
        return raw
    if _BLANK_BETWEEN_RE.search(raw):
        raw = re.sub(rb"(>[ \t]*\r\n)(?:[ \t]*\r\n)+", rb"\1", raw)
    if _SONG_OPEN_BAD_LINE_RE.search(raw) or _SONG_CLOSE_BAD_LINE_RE.search(raw):
        raw = _SONG_OPEN_BAD_LINE_RE.sub(b"\r\n <Song", raw)
        raw = _SONG_CLOSE_BAD_LINE_RE.sub(b"\r\n </Song>", raw)
    late = _late_comment_spans(raw)
    for a, b in reversed(late):
        block = canonical_song_block(raw[a:b].decode("utf-8"))
        raw = raw[:a] + block.encode("utf-8") + raw[b:]
    return raw



def to_crlf(text: str) -> str:
    """Normalize any mix of LF / CRLF to pure CRLF (for text spliced into a CRLF file)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


_AUTOCUE_SAFETY_PATH = Path("/Users/kirilllanger/src/vdj-automatic-cuer/vdj_database_safety.py")
_autocue_safety_mod: Any = None


def autocue_safety_module() -> Any:
    """The AutoCue repo's vdj_database_safety, imported read-only under its own name.

    Its ``database_integrity_stats`` / ``validate_database_replacement`` run as an
    independent second opinion after every write. Falls back to this module's
    copies if the AutoCue checkout is not present.
    """
    global _autocue_safety_mod
    if _autocue_safety_mod is not None:
        return _autocue_safety_mod
    override = os.environ.get("MUSIC_SORTER_AUTOCUE_SAFETY_PATH", "").strip()
    path = Path(override) if override else _AUTOCUE_SAFETY_PATH
    mod: Any = None
    if path.is_file():
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("_autocue_vdj_database_safety", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception:
            mod = None
    _autocue_safety_mod = mod if mod is not None else sys.modules[__name__]
    return _autocue_safety_mod


def read_vdj_database_text(database_path: os.PathLike | str) -> str:
    """
    Read database.xml while preserving VirtualDJ's CRLF line endings.

    Path.read_text() / open(..., encoding=utf-8) use universal newlines and
    strip \\r. VirtualDJ then treats the file as corrupted and resets the library.
    """
    raw = sanitize_vdj_database_bytes(Path(database_path).read_bytes())
    return raw.decode("utf-8")


def vdj_database_lock_path(database_path: os.PathLike | str) -> Path:
    """Sidecar lock file next to database.xml (cross-process exclusive writes)."""
    path = Path(database_path).expanduser()
    return path.parent / f"{path.name}.write.lock"


@contextmanager
def vdj_database_exclusive_lock(database_path: os.PathLike | str) -> Iterator[None]:
    """
    Exclusive flock for database.xml writers (UI + CLI + other processes).

    Complements the in-process sorter.db_lock so two Music Sorter / AutoCue
    processes cannot last-write-wins the same library.
    """
    _refuse_if_readonly("take the database.xml write lock (creates a lock file in the VDJ folder)")
    lock_path = vdj_database_lock_path(database_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _fsync_directory(directory: Path) -> None:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def write_vdj_database_text(database_path: os.PathLike | str, content: str) -> None:
    """Write database.xml via the atomic gate (UTF-8 bytes, no newline translation)."""
    atomic_replace_database(database_path, content, original_stats=None)


def database_integrity_stats(database_path: os.PathLike | str) -> Dict[str, int]:
    """Stream structural stats without retaining the full XML tree."""
    path = Path(database_path)
    song_count = 0
    cue_loop_count = 0
    root_tag = None

    import io

    raw = sanitize_vdj_database_bytes(path.read_bytes())
    for event, element in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
        if event == "start" and root_tag is None:
            root_tag = element.tag
            if root_tag != VDJ_DATABASE_ROOT:
                raise ValueError(f"Unexpected VirtualDJ database root: {root_tag}")
            continue

        if event != "end" or element.tag != "Song":
            continue

        song_count += 1
        for poi in element.findall("Poi"):
            if poi.get("Type") in MANUAL_CUE_TYPES and poi.get("Num", "0") != "0":
                cue_loop_count += 1
        element.clear()

    if root_tag is None:
        raise ValueError("Empty or unreadable VirtualDJ database")

    return {
        "size_bytes": path.stat().st_size,
        "song_count": song_count,
        "cue_loop_count": cue_loop_count,
    }


def serialize_song_element(song: ET.Element) -> str:
    """Serialize one Song element. Prefer text-preserving rewrite over this."""
    return ET.tostring(song, encoding="unicode")


def _escape_xml_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_vdj_poi_line(
    *,
    pos: float,
    poi_type: str,
    num: str,
    color: str,
    name: Optional[str] = None,
    size: Optional[str] = None,
    slot: Optional[str] = None,
    indent: str = "  ",
    newline: str = "\r\n",
) -> str:
    """Emit one VirtualDJ POI line in the native spaced/self-closing style."""
    attrs = []
    if name:
        attrs.append(f'Name="{_escape_xml_attr(name)}"')
    if abs(float(pos)) >= 5e-7:  # VDJ drops Pos when it is 0
        attrs.append(f'Pos="{pos:.6f}"')
    attrs.append(f'Num="{num}"')
    attrs.append(f'Color="{color}"')
    attrs.append(f'Type="{poi_type}"')
    if size is not None:
        # Native VDJ loops use Size="16.0" / "32.0"
        try:
            size_num = float(size)
            size_text = f"{size_num:.1f}" if size_num.is_integer() else f"{size_num:g}"
        except (TypeError, ValueError):
            size_text = str(size)
        attrs.append(f'Size="{size_text}"')
    if slot is not None:
        attrs.append(f'Slot="{slot}"')
    return f"{indent}<Poi {' '.join(attrs)} />{newline}"


_POI_LINE_RE = re.compile(
    r"[ \t]*<Poi\b[^>]*/>[ \t]*(?:\r?\n)?",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(
    r"[ \t]*<Comment\b[^>]*>.*?</Comment>[ \t]*(?:\r?\n)?",
    re.IGNORECASE | re.DOTALL,
)


def _is_autocue_info_poi(poi_tag: str) -> bool:
    """True for AutoCue info markers (Type=cue Num=0, Info name)."""
    type_match = re.search(r'\bType\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    if type_match is None or type_match.group(1).lower() != "cue":
        return False
    num_match = re.search(r'\bNum\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    num = num_match.group(1) if num_match else "0"
    if num != "0":
        return False
    name_match = re.search(r'\bName\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    name = (name_match.group(1) if name_match else "").lower()
    return name.startswith("info")


def _is_manual_cue_or_loop_poi(poi_tag: str) -> bool:
    """True for user cue/loop markers (keep Num=0 hotcues and automix/beatgrid).

    AutoCue info POIs (Num=0, name starts with Info) are treated as rewriteable
    so they do not stack across AutoCue runs.
    """
    if _is_autocue_info_poi(poi_tag):
        return True
    type_match = re.search(r'\bType\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    if type_match is None:
        return False
    poi_type = type_match.group(1).lower()
    if poi_type == "loop":
        return True
    if poi_type != "cue":
        return False
    num_match = re.search(r'\bNum\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    num = num_match.group(1) if num_match else "0"
    return num != "0"


def _poi_attr(poi_tag: str, name: str) -> Optional[str]:
    match = re.search(rf'\b{name}\s*=\s*"([^"]*)"', poi_tag, re.IGNORECASE)
    return match.group(1) if match else None


def iter_manual_poi_tags(song_xml: str):
    """Yield raw POI tags for manual cue/loop markers in song order."""
    for match in _POI_LINE_RE.finditer(song_xml):
        tag = match.group(0)
        if _is_manual_cue_or_loop_poi(tag):
            yield tag


def parse_manual_poi_tag(poi_tag: str) -> Optional[Dict[str, Any]]:
    """Parse one manual cue/loop POI into a plain dict (or None if not manual)."""
    if not _is_manual_cue_or_loop_poi(poi_tag):
        return None
    poi_type = (_poi_attr(poi_tag, "Type") or "").lower()
    try:
        pos = float(_poi_attr(poi_tag, "Pos") or "0")
    except ValueError:
        pos = 0.0
    length_beats: Optional[float] = None
    if poi_type == "loop":
        size_raw = _poi_attr(poi_tag, "Size")
        if size_raw is not None:
            try:
                length_beats = float(size_raw)
            except ValueError:
                length_beats = 16.0
        else:
            length_beats = 16.0
    return {
        "kind": "loop" if poi_type == "loop" else "cue",
        "name": _poi_attr(poi_tag, "Name") or ("Loop" if poi_type == "loop" else "Cue"),
        "position": pos,
        "color": _poi_attr(poi_tag, "Color") or "",
        "num": _poi_attr(poi_tag, "Num") or ("-1" if poi_type == "loop" else "1"),
        "length_beats": length_beats,
    }


def extract_manual_pois_from_song_xml(song_xml: str) -> Dict[str, List[Dict[str, Any]]]:
    """Split existing manual markers into cues and loops (database order)."""
    cues: List[Dict[str, Any]] = []
    loops: List[Dict[str, Any]] = []
    for tag in iter_manual_poi_tags(song_xml):
        parsed = parse_manual_poi_tag(tag)
        if parsed is None:
            continue
        if parsed["kind"] == "loop":
            loops.append(parsed)
        else:
            cues.append(parsed)
    return {"cues": cues, "loops": loops}


def strip_manual_cues_from_song_xml(song_xml: str) -> str:
    """Remove cue/loop POIs and Comment nodes while keeping native VDJ markup."""
    cleaned = _POI_LINE_RE.sub(
        lambda match: "" if _is_manual_cue_or_loop_poi(match.group(0)) else match.group(0),
        song_xml,
    )
    cleaned = _COMMENT_RE.sub("", cleaned)
    return cleaned


def inject_pois_into_song_xml(
    song_xml: str,
    poi_lines: Sequence[str],
    comment: Optional[str] = None,
) -> str:
    """
    Insert formatted POI lines before </Song>, preserving original Song markup.

    This avoids ElementTree re-serialization, which VirtualDJ rejects and can
    trigger a library reset on open.
    """
    newline = _detect_newline(song_xml)
    cleaned = strip_manual_cues_from_song_xml(song_xml)
    close_idx = cleaned.rfind("</Song>")
    if close_idx < 0:
        raise ValueError("Song XML is missing </Song>")

    indent = "  "
    body = cleaned[:close_idx].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline

    insertion = "".join(poi_lines)
    if comment:
        insertion += (
            f"{indent}<Comment>{_escape_xml_attr(comment)}</Comment>{newline}"
        )

    return body + insertion + cleaned[close_idx:]


def serialize_vdj_database(root: ET.Element) -> str:
    """Serialize a full database tree (legacy path; prefer surgical rewrite)."""
    # ElementTree normalizes line breaks to LF — always rebuild pure CRLF.
    return to_crlf(ET.tostring(root, encoding="unicode"))


def validate_database_replacement(
    candidate_path: os.PathLike | str,
    original_stats: Dict[str, int],
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Reject parseable but structurally broken replacement databases."""
    counter = stats_fn or database_integrity_stats
    candidate_stats = counter(candidate_path)

    if candidate_stats["song_count"] < original_stats["song_count"]:
        raise ValueError(
            "Generated database failed integrity check: song count dropped "
            f"from {original_stats['song_count']} to "
            f"{candidate_stats['song_count']}"
        )

    original_cues = original_stats["cue_loop_count"]
    candidate_cues = candidate_stats["cue_loop_count"]
    if original_cues >= 20 and candidate_cues < int(original_cues * 0.75):
        raise ValueError(
            "Generated database failed integrity check: cue/loop count dropped "
            f"from {original_cues} to {candidate_cues}"
        )
    if 0 < original_cues < 20 and candidate_cues == 0:
        raise ValueError(
            "Generated database failed integrity check: cue/loop count dropped "
            f"from {original_cues} to 0"
        )

    original_size = original_stats["size_bytes"]
    candidate_size = candidate_stats["size_bytes"]
    if original_size >= 1_000_000 and candidate_size < int(original_size * 0.75):
        raise ValueError(
            "Generated database failed integrity check: file size dropped "
            f"from {original_size} to {candidate_size} bytes"
        )

    return candidate_stats


def _detect_newline(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _find_song_span(content: str, audio_file_path: str) -> Optional[tuple[int, int]]:
    """Return [start, end) byte/char offsets of the Song element for a path."""
    target = normalize_database_path(audio_file_path)
    for match in _SONG_OPEN_RE.finditer(content):
        attrs = match.group("attrs")
        path_match = _FILEPATH_RE.search(attrs)
        if path_match is None:
            continue
        song_path = normalize_database_path(_unescape_xml_attr(path_match.group(1)))
        if song_path != target:
            continue

        start = match.start()
        # Scan for the matching close tag from this Song open.
        depth = 1
        pos = match.end()
        while depth > 0:
            next_open = content.find("<Song", pos)
            next_close = content.find("</Song>", pos)
            if next_close < 0:
                return None
            if next_open >= 0 and next_open < next_close:
                depth += 1
                pos = next_open + 5
            else:
                depth -= 1
                if depth == 0:
                    return start, next_close + len("</Song>")
                pos = next_close + len("</Song>")
    return None


def load_song_element(database_path: os.PathLike | str, audio_file_path: str) -> ET.Element:
    """Parse only the Song element for one track (low memory)."""
    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, audio_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {audio_file_path}")
    start, end = span
    return ET.fromstring(content[start:end])


# Top-level library roots omitted from Directory Sort labels.
# Nested folders like "Add Cues" under Zouk are *not* roots — they are leaf labels.
_DIRECTORY_SORT_ROOT_NAMES = frozenset({"Zouk", "House", "Cues Sorted"})


def _is_directory_sort_root(part: str) -> bool:
    """True for Zouk/House/Cues Sorted and variants like 'Cues Sorted copy'."""
    if part in _DIRECTORY_SORT_ROOT_NAMES:
        return True
    # Backup / copy crates that mirror Cues Sorted
    if part.startswith("Cues Sorted"):
        return True
    return False


def directory_sort_label(file_path: str) -> str:
    """Folder label for VirtualDJ User2 / Directory Sort column.

    Strips library roots (Zouk, House, Cues Sorted, …). Then:
    - one folder under the root → that leaf only (``Energy``)
    - deeper nesting → bottom two folders (``Chill/Shaman``)

    Examples::

        .../Zouk/Chill/Shaman/t.flac     → Chill/Shaman
        .../Zouk/Energy/t.flac           → Energy
        .../Cues Sorted/Chill/t.flac     → Chill
        .../Cues Sorted/Chill/Mystical/t → Chill/Mystical
        .../House/Chill/t.flac           → Chill
    """
    path = Path(normalize_database_path(file_path))
    parent = path.parent
    if not parent.name or parent == parent.parent:
        return ""

    parts = [p for p in parent.parts if p and p != "/"]
    # Path relative to the *innermost* library root (Cues Sorted wins over Cues).
    rel: list[str] | None = None
    for index, part in enumerate(parts):
        if _is_directory_sort_root(part):
            rel = list(parts[index + 1 :])

    if rel is None:
        # Outside known roots: at most last two folder names.
        rel = parts[-2:] if len(parts) >= 2 else parts

    if not rel:
        return ""
    if len(rel) == 1:
        return rel[0]
    return f"{rel[-2]}/{rel[-1]}"


def normalize_user2_dest(label: str) -> str:
    """Genre dest only. Refuses Add Cues / Cues Sorted / Sets. Kizouk stays."""
    text = (label or "").replace("&amp;", "&").strip()
    if not text:
        return ""
    parts = [part for part in text.split("/") if part]
    if not parts:
        return ""
    if parts[-1] == "Kizouk" and (len(parts) == 1 or parts[0] == "Sets"):
        return "Kizouk"
    head = parts[0]
    if (
        head in {"Add Cues", "Cues Sorted", "Sets", "Cues", "Ready For Sort"}
        or head.startswith("Cues Sorted")
        or head.startswith("Pajamathon")
    ):
        return ""
    return text


def is_allowed_user2_dest(label: str) -> bool:
    return bool(normalize_user2_dest(label))


def song_xml_with_user2_label(song_xml: str, label: str) -> str:
    """Patch Tags User2 only. Empty or forbidden dest leaves the Song unchanged."""
    allowed = normalize_user2_dest(label)
    if not allowed:
        return song_xml
    escaped = _escape_xml_attr(allowed)
    # Prefer self-closing Tags; do not swallow the '/' into attrs.
    tags_m = re.search(r"(<Tags\b)(.*?)(\s*/>)", song_xml, flags=re.DOTALL)
    if tags_m is None:
        tags_m = re.search(r"(<Tags\b)([^>]*)(>)", song_xml)
    if tags_m is None:
        return song_xml
    attrs = tags_m.group(2)
    if re.search(r"\bUser2\s*=", attrs):
        new_attrs = re.sub(
            r'\bUser2\s*=\s*"[^"]*"',
            f'User2="{escaped}"',
            attrs,
            count=1,
        )
    elif re.search(r"\bFlag\s*=", attrs):
        new_attrs = re.sub(
            r"(\bFlag\s*=)",
            f'User2="{escaped}" \\1',
            attrs,
            count=1,
        )
    else:
        new_attrs = attrs.rstrip() + f' User2="{escaped}"'
    new_tags = f"{tags_m.group(1)}{new_attrs}{tags_m.group(3)}"
    return song_xml[: tags_m.start()] + new_tags + song_xml[tags_m.end() :]


def song_xml_with_directory_sort_user2(song_xml: str, file_path: str) -> str:
    """Set Tags User2 from the path label. Refuses Add Cues / Cues Sorted / Sets."""
    return song_xml_with_user2_label(song_xml, directory_sort_label(file_path))


def patch_song_infos_and_user2(
    song_xml: str,
    *,
    user_color: str | None = None,
    user2: str | None = None,
) -> str:
    """Infos UserColor + Tags User2 only. Never replaces the Song block (POIs stay)."""
    out = song_xml
    if user_color is not None:
        from song_lane_color import apply_user_color_to_infos

        out = apply_user_color_to_infos(out, user_color)
    if user2 is not None:
        out = song_xml_with_user2_label(out, user2)
    return out


def song_xml_with_new_filepath(
    song_xml: str,
    new_file_path: str,
    *,
    directory_sort_path: str | None = None,
) -> str:
    """
    Change only the FilePath attribute on the Song open tag.

    Also refreshes Tags User2 (Directory Sort). Default is the new path's
    folders; pass directory_sort_path to keep the origin crate (e.g. Zouk
    Chill/Mystical after copying into Sets/).
    """
    match = _SONG_OPEN_RE.search(song_xml)
    if match is None:
        raise ValueError("Song XML is missing a <Song> open tag")

    attrs = match.group("attrs")
    if _FILEPATH_RE.search(attrs) is None:
        raise ValueError("Song open tag is missing FilePath")

    escaped = _escape_xml_attr(normalize_database_path(new_file_path))
    new_attrs = _FILEPATH_RE.sub(f'FilePath="{escaped}"', attrs, count=1)
    new_open = f"<Song{new_attrs}>"
    updated = song_xml[: match.start()] + new_open + song_xml[match.end() :]
    label_from = directory_sort_path or new_file_path
    return song_xml_with_directory_sort_user2(updated, label_from)


def relocate_song_filepath_in_database(
    database_path: os.PathLike | str,
    old_file_path: str,
    new_file_path: str,
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """
    Surgically retarget one Song entry after the audio file was moved/renamed.

    Uses the same text-preserving rewrite path as cue injection: CRLF kept,
    native Song body untouched, atomic replace with integrity checks.
    """
    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, old_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {old_file_path}")
    start, end = span
    updated_song_xml = song_xml_with_new_filepath(content[start:end], new_file_path)
    del content
    gc.collect()
    return rewrite_song_xml_in_database(
        database_path,
        old_file_path,
        updated_song_xml,
        validate=validate,
    )


def insert_song_xml_in_database(
    database_path: os.PathLike | str,
    song_xml: str,
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """
    Append one Song block before </VirtualDJ_Database>, preserving CRLF.

    Used when a cued track is copied to a second path (e.g. Cues Sorted) and
    needs its own database entry without re-serializing the library.
    """
    path = Path(database_path)
    content = read_vdj_database_text(path)
    original_stats = _lightweight_rewrite_stats(content, path) if validate else None
    newline = _detect_newline(content)

    song_xml = song_xml.strip()
    if newline == "\r\n":
        song_xml = song_xml.replace("\r\n", "\n").replace("\n", "\r\n")
    if not song_xml.endswith(newline):
        song_xml = song_xml + newline

    close_tag = "</VirtualDJ_Database>"
    close_idx = content.rfind(close_tag)
    if close_idx < 0:
        raise ValueError("Database is missing </VirtualDJ_Database>")

    # Ensure separation from previous content.
    prefix = content[:close_idx].rstrip(" \t")
    if not prefix.endswith("\n"):
        prefix += newline
    suffix = content[close_idx:]
    del content
    gc.collect()

    return atomic_replace_database_parts(
        path,
        (prefix, song_xml, suffix),
        original_stats,
        stats_fn=_lightweight_content_stats if validate else None,
    )


def clone_song_entry_to_path(
    database_path: os.PathLike | str,
    source_file_path: str,
    new_file_path: str,
    *,
    validate: bool = True,
    skip_if_exists: bool = True,
) -> Dict[str, Any]:
    """
    Duplicate one Song entry under a new FilePath (cues/loops preserved).

    Returns stats plus flags describing what happened.
    """
    content = read_vdj_database_text(database_path)
    new_norm = normalize_database_path(new_file_path)
    existing = _find_song_span(content, new_norm)
    if existing is not None:
        if skip_if_exists:
            return {
                "cloned": False,
                "already_present": True,
                "song_count": content.count("<Song"),
            }
        raise FileExistsError(f"Song already exists in database: {new_file_path}")

    span = _find_song_span(content, source_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {source_file_path}")
    start, end = span
    cloned_xml = song_xml_with_new_filepath(content[start:end], new_norm)
    del content
    gc.collect()
    stats = insert_song_xml_in_database(
        database_path, cloned_xml, validate=validate
    )
    return {
        "cloned": True,
        "already_present": False,
        **stats,
    }


def _lightweight_rewrite_stats(content: str, path: Path) -> Dict[str, int]:
    """Fast structural counts for surgical rewrites (no full XML tree)."""
    return {
        "size_bytes": path.stat().st_size if path.exists() else len(content.encode("utf-8")),
        "song_count": content.count("<Song"),
        # Cheap upper bound used only for catastrophic-drop detection.
        "cue_loop_count": content.count('Type="cue"') + content.count("Type='cue'")
        + content.count('Type="loop"') + content.count("Type='loop'"),
    }


def rewrite_song_xml_in_database(
    database_path: os.PathLike | str,
    audio_file_path: str,
    new_song_xml: str,
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """Replace one Song block with pre-built XML that keeps native VDJ formatting.

    Re-read + splice happens inside ``vdj_database_exclusive_lock`` so a
    concurrent AutoCue child / UI edit of a *different* song cannot be
    last-write-wins reverted. The lock is not re-entrant — the replace
    path must not take a second flock.
    """
    path = Path(database_path)
    with vdj_database_exclusive_lock(path):
        content = read_vdj_database_text(path)
        original_stats = _lightweight_rewrite_stats(content, path) if validate else None
        span = _find_song_span(content, audio_file_path)
        if span is None:
            raise KeyError(f"Song not found in database: {audio_file_path}")

        start, end = span
        newline = _detect_newline(content)
        song_xml = new_song_xml
        if newline == "\r\n":
            # Force CRLF for any injected markup (Path.read_text would have stripped it).
            song_xml = song_xml.replace("\r\n", "\n").replace("\n", "\r\n")

        # Guard: never allow a rewritten Song that lost most of its original bulk
        # (Tags/Infos/Scan/automix markers), which indicates bad re-serialization.
        original_song = content[start:end]
        if len(original_song) >= 400 and len(song_xml) < int(len(original_song) * 0.5):
            raise ValueError(
                "Refusing song rewrite that shrinks the Song block by more than 50% "
                f"({len(original_song)} -> {len(song_xml)} chars)"
            )

        # Also refuse converting a CRLF database into LF-only (VDJ resets the library).
        if newline == "\r\n" and "\r\n" not in song_xml:
            raise ValueError("Refusing song rewrite that would drop CRLF line endings")

        prefix = content[:start]
        suffix = content[end:]
        return _replace_database_parts_locked(
            path,
            (prefix, song_xml, suffix),
            original_stats,
            stats_fn=_lightweight_content_stats if validate else None,
        )


def rewrite_song_in_database(
    database_path: os.PathLike | str,
    audio_file_path: str,
    mutator: Callable[[ET.Element], None],
    *,
    validate: bool = True,
) -> Dict[str, int]:
    """
    Legacy ElementTree mutator path.

    Prefer rewrite_song_xml_in_database / inject_pois_into_song_xml so VirtualDJ
    keeps the original Tags/Scan/automix markup intact.
    """
    content = read_vdj_database_text(database_path)
    span = _find_song_span(content, audio_file_path)
    if span is None:
        raise KeyError(f"Song not found in database: {audio_file_path}")
    start, end = span
    song = ET.fromstring(content[start:end])
    mutator(song)
    # Still go through the guarded XML rewriter so size checks apply.
    return rewrite_song_xml_in_database(
        database_path,
        audio_file_path,
        serialize_song_element(song),
        validate=validate,
    )


def _lightweight_content_stats(database_path: os.PathLike | str) -> Dict[str, int]:
    path = Path(database_path)
    content = read_vdj_database_text(path)
    return _lightweight_rewrite_stats(content, path)


def _replace_database_parts_locked(
    path: Path,
    parts: Sequence[str],
    original_stats: Optional[Dict[str, int]] = None,
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Tempfile + validate + os.replace. Caller must hold vdj_database_exclusive_lock.

    Bytes only (no newline translation). HARD GATES: the existing file and the
    candidate must be pure CRLF before anything is replaced; after the replace the
    file is read back, must still be pure CRLF and pass the AutoCue module's
    database_integrity_stats / validate_database_replacement, otherwise the
    previous bytes are restored and the write is refused with an error.
    """
    directory = path.parent
    assert_safe_to_write_vdj_database()
    ensure_healthy_vdj_database(path)

    original_raw: Optional[bytes] = None
    if path.exists():
        original_raw = path.read_bytes()
        if original_raw:
            assert_crlf_bytes(original_raw, f"existing {path.name}")

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(directory),
    )
    temp_path = Path(temp_name)
    rollback_path = directory / f".{path.name}.prewrite.{os.getpid()}"
    counter = stats_fn or database_integrity_stats
    try:
        candidate_raw = heal_song_form(
            b"".join(sanitize_vdj_database_bytes(part.encode("utf-8")) for part in parts)
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(candidate_raw)
            handle.flush()
            os.fsync(handle.fileno())

        candidate_raw = temp_path.read_bytes()
        assert_crlf_bytes(candidate_raw, f"new {path.name}")
        assert_vdj_song_form(candidate_raw, f"new {path.name}")
        if original_stats is not None:
            stats = validate_database_replacement(
                temp_path, original_stats, stats_fn=counter
            )
        else:
            stats = counter(temp_path)

        assert_safe_to_write_vdj_database()
        if original_raw is not None:
            rollback_path.write_bytes(original_raw)
        os.replace(temp_path, path)
        _fsync_directory(directory)
        try:
            _post_write_gate(path, original_raw, original_stats, candidate_raw)
        except Exception as exc:
            if original_raw is not None and rollback_path.exists():
                os.replace(rollback_path, path)
                _fsync_directory(directory)
                raise type(exc)(
                    f"{exc} — database.xml was rolled back to the bytes from before this write."
                ) from exc
            raise
        try:
            snapshot_last_good_database(path)
        except Exception:
            pass
        return stats
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        rollback_path.unlink(missing_ok=True)


_POI_TAG_BYTES_RE = re.compile(rb"<Poi\b[^>]*/>")


def _raw_integrity_stats(raw: bytes) -> Dict[str, int]:
    """database_integrity_stats-equivalent counts straight from bytes (no XML parse)."""
    cue_loop = 0
    for m in _POI_TAG_BYTES_RE.finditer(raw):
        tag = m.group(0)
        tm = re.search(rb'\bType\s*=\s*"([^"]*)"', tag)
        if tm is None or tm.group(1).decode("ascii", "ignore") not in MANUAL_CUE_TYPES:
            continue
        nm = re.search(rb'\bNum\s*=\s*"([^"]*)"', tag)
        if (nm.group(1) if nm else b"0") != b"0":
            cue_loop += 1
    return {
        "size_bytes": len(raw),
        "song_count": len(re.findall(rb"<Song\b", raw)),
        "cue_loop_count": cue_loop,
    }


def _post_write_gate(
    path: Path,
    original_raw: Optional[bytes],
    original_stats: Optional[Dict[str, int]],
    expected_raw: bytes,
) -> None:
    """Read the file back as bytes and re-verify it (see _replace_database_parts_locked)."""
    back = path.read_bytes()
    if back != expected_raw:
        raise ValueError("Read-back of database.xml does not match the bytes that were written")
    assert_crlf_bytes(back, f"written {path.name}")
    # `back` is byte-for-byte the candidate that already passed assert_vdj_song_form just before the replace
    # (checked above), so running the same ~3 s song-form scan on identical bytes again proves nothing new.
    if back is not expected_raw and back != expected_raw:  # pragma: no cover - raised above already
        assert_vdj_song_form(back, f"written {path.name}")
    ac = autocue_safety_module()
    stats_as_written = ac.database_integrity_stats(path)  # full XML parse of the file as written (once)
    if original_raw is not None:
        before = _raw_integrity_stats(original_raw)
        if original_stats is not None:
            # An intentional removal passes pre-reduced expectations; honor them.
            before = {k: min(before[k], int(original_stats.get(k, before[k]))) for k in before}
        # same file, same stats: reuse the parse above instead of parsing the 38 MB file a second time
        ac.validate_database_replacement(path, before, stats_fn=lambda _p: stats_as_written)


def atomic_replace_database_parts(
    database_path: os.PathLike | str,
    parts: Sequence[str],
    original_stats: Optional[Dict[str, int]] = None,
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Write candidate XML parts to a temp file, validate, then replace atomically."""
    path = Path(database_path)
    with vdj_database_exclusive_lock(path):
        return _replace_database_parts_locked(
            path, parts, original_stats, stats_fn=stats_fn
        )


def atomic_replace_database(
    database_path: os.PathLike | str,
    xml_content: str,
    original_stats: Optional[Dict[str, int]] = None,
    stats_fn: Optional[Callable[[os.PathLike | str], Dict[str, int]]] = None,
) -> Dict[str, int]:
    """Write candidate XML to a temp file, validate, then replace atomically."""
    return atomic_replace_database_parts(
        database_path, (xml_content,), original_stats, stats_fn=stats_fn
    )


def copy_database_atomically(source: os.PathLike | str, destination: os.PathLike | str) -> None:
    """Copy a validated database file into place."""
    _refuse_if_readonly("copy a database file into place")
    assert_crlf_bytes(Path(source).read_bytes(), "database copy source")
    shutil.copy2(source, destination)
