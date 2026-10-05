"""Detect digital-silent vocal tiles in VirtualDJ .vdjstems files.

Zen Eyer failure: ~10s windows where the vocal stream is exact zeros while
the original mix is still loud. That is not bleed — the singer was dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import subprocess

VDJ_STEM_NAMES = ("vocal", "hihat", "bass", "instruments", "kick")

# Vocal RMS at or below this is treated as digital mute (decoded zeros).
VOCAL_SILENT_DB = -80.0
# Original mix must be at least this loud for a mute to count as a hole.
ORIG_LOUD_DB = -18.0
# Ignore sub-tile blips; VDJ windows are ~10s.
MIN_HOLE_SECONDS = 6.0
# Broken if any hole is this long, or total hole time reaches this.
BROKEN_LONGEST_SECONDS = 8.0
BROKEN_TOTAL_SECONDS = 12.0

AUDIT_SAMPLE_RATE = 11025
AUDIT_HOP_SECONDS = 0.5


@dataclass(frozen=True)
class VocalHole:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class StemVocalAudit:
    audio_path: str
    stems_path: str
    holes: list[VocalHole] = field(default_factory=list)
    error: str = ""
    duration: float = 0.0

    @property
    def broken(self) -> bool:
        if self.error:
            return False
        return audit_is_broken(self.holes)

    @property
    def hole_seconds(self) -> float:
        return sum(h.duration for h in self.holes)

    def to_dict(self) -> dict:
        return {
            "audio_path": self.audio_path,
            "stems_path": self.stems_path,
            "broken": self.broken,
            "error": self.error,
            "duration": self.duration,
            "hole_seconds": round(self.hole_seconds, 2),
            "holes": [
                {"start": round(h.start, 2), "end": round(h.end, 2)}
                for h in self.holes
            ],
        }


def merge_flagged_hops(
    starts: list[float],
    *,
    hop: float,
) -> list[VocalHole]:
    """Merge consecutive hop starts into half-open [start, end) holes."""
    if not starts or hop <= 0:
        return []
    ordered = sorted(starts)
    holes: list[VocalHole] = []
    run_start = ordered[0]
    prev = ordered[0]
    for t in ordered[1:]:
        if t - prev <= hop + 1e-6:
            prev = t
            continue
        holes.append(VocalHole(start=run_start, end=prev + hop))
        run_start = prev = t
    holes.append(VocalHole(start=run_start, end=prev + hop))
    return holes


def vocal_holes_from_rms(
    vocal_rms_db: list[float],
    orig_rms_db: list[float],
    *,
    hop: float,
    vocal_silent_db: float = VOCAL_SILENT_DB,
    orig_loud_db: float = ORIG_LOUD_DB,
    min_hole_seconds: float = 0.5,
) -> list[VocalHole]:
    """Flag hops where vocal is digital-silent and the mix is still loud."""
    n = min(len(vocal_rms_db), len(orig_rms_db))
    flagged: list[float] = []
    for i in range(n):
        if vocal_rms_db[i] <= vocal_silent_db and orig_rms_db[i] >= orig_loud_db:
            flagged.append(i * hop)
    holes = merge_flagged_hops(flagged, hop=hop)
    return [h for h in holes if h.duration + 1e-9 >= min_hole_seconds]


def audit_is_broken(
    holes: list[VocalHole],
    *,
    min_longest: float = BROKEN_LONGEST_SECONDS,
    min_total: float = BROKEN_TOTAL_SECONDS,
) -> bool:
    if not holes:
        return False
    longest = max(h.duration for h in holes)
    total = sum(h.duration for h in holes)
    return longest >= min_longest or total >= min_total


def _rms_db_frames(samples, *, hop_samples: int) -> list[float]:
    import math

    if hop_samples <= 0 or not samples:
        return []
    out: list[float] = []
    n = len(samples)
    i = 0
    while i + hop_samples <= n:
        acc = 0.0
        end = i + hop_samples
        for j in range(i, end):
            x = samples[j]
            acc += x * x
        rms = math.sqrt(acc / hop_samples)
        out.append(20.0 * math.log10(rms + 1e-12))
        i += hop_samples
    return out


def _decode_mono_f32(
    path: Path,
    *,
    stream: Optional[int],
    sample_rate: int,
):
    import array

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
    ]
    if stream is not None:
        cmd += ["-map", f"0:a:{stream}"]
    cmd += [
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise RuntimeError(err or f"ffmpeg failed on {path}")
    samples = array.array("f")
    samples.frombytes(result.stdout[: len(result.stdout) // 4 * 4])
    return samples


def _vocal_stream_index(stems_path: Path) -> Optional[int]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",
            str(stems_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    data = json.loads(probe.stdout or "{}")
    for stream in data.get("streams") or []:
        title = str((stream.get("tags") or {}).get("title") or "").lower()
        if title == "vocal":
            return int(stream["index"])
    return 0


def audit_stem_file(
    audio_path: str | Path,
    *,
    hop_seconds: float = AUDIT_HOP_SECONDS,
    sample_rate: int = AUDIT_SAMPLE_RATE,
) -> StemVocalAudit:
    """Compare original mix vs vocal stem. Read-only."""
    audio = Path(audio_path)
    stems = Path(f"{audio}.vdjstems")
    report = StemVocalAudit(audio_path=str(audio), stems_path=str(stems))
    if not audio.is_file():
        report.error = "audio missing"
        return report
    if not stems.is_file():
        report.error = "no vdjstems"
        return report
    try:
        vocal_index = _vocal_stream_index(stems)
        if vocal_index is None:
            report.error = "no vocal stream"
            return report
        vocal = _decode_mono_f32(stems, stream=vocal_index, sample_rate=sample_rate)
        orig = _decode_mono_f32(audio, stream=None, sample_rate=sample_rate)
        hop_samples = max(1, int(round(hop_seconds * sample_rate)))
        report.duration = min(len(vocal), len(orig)) / float(sample_rate)
        report.holes = vocal_holes_from_rms(
            _rms_db_frames(vocal, hop_samples=hop_samples),
            _rms_db_frames(orig, hop_samples=hop_samples),
            hop=hop_seconds,
        )
    except Exception as exc:
        report.error = str(exc)[:300]
    return report


def cued_audio_paths_from_database(database_xml: Path) -> list[str]:
    """FilePath values for Song entries that have at least one cue POI."""
    import re
    from xml.sax.saxutils import unescape

    text = database_xml.read_text(encoding="utf-8", errors="replace")
    song_re = re.compile(r"<Song\b([^>]*)>([\s\S]*?)</Song>", re.I)
    paths: list[str] = []
    seen: set[str] = set()
    for match in song_re.finditer(text):
        body = match.group(2)
        if 'Type="cue"' not in body and "Type='cue'" not in body:
            continue
        fp = re.search(r'FilePath="([^"]+)"', match.group(1))
        if not fp:
            continue
        path = unescape(fp.group(1))
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def list_stem_audio_paths(
    root: Path,
    *,
    since_unix: float | None = None,
) -> list[Path]:
    """Audio files under ``root`` that have an adjacent .vdjstems sidecar."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for stems in root.rglob("*.vdjstems"):
        try:
            mtime = stems.stat().st_mtime
        except OSError:
            continue
        if since_unix is not None and mtime < since_unix:
            continue
        name = stems.name
        if not name.endswith(".vdjstems"):
            continue
        audio = stems.with_name(name[: -len(".vdjstems")])
        if audio.is_file():
            found.append(audio)
    found.sort(key=lambda p: str(p).lower())
    return found


def scan_stems_under(
    root: Path,
    *,
    since_unix: float | None = None,
    limit: int | None = None,
    workers: int = 1,
) -> list[StemVocalAudit]:
    """Audit every adjacent .vdjstems file under a music root."""
    paths = list_stem_audio_paths(root, since_unix=since_unix)
    if limit is not None:
        paths = paths[:limit]
    if workers <= 1:
        return [audit_stem_file(p) for p in paths]
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(audit_stem_file, paths))


def scan_cued_with_stems(
    database_xml: Path,
    *,
    limit: int | None = None,
) -> list[StemVocalAudit]:
    """Audit cued songs that have an adjacent .vdjstems sidecar."""
    reports: list[StemVocalAudit] = []
    checked = 0
    for raw in cued_audio_paths_from_database(database_xml):
        audio = Path(raw)
        if not audio.is_file():
            continue
        if not Path(f"{audio}.vdjstems").is_file():
            continue
        reports.append(audit_stem_file(audio))
        checked += 1
        if limit is not None and checked >= limit:
            break
    return reports


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(
        description="Audit cued VirtualDJ tracks for silent vocal-stem tiles."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home()
        / "Library"
        / "Application Support"
        / "VirtualDJ"
        / "database.xml",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "Music" / "DJ" / "Notes" / "stem_vocal_audit.json",
    )
    parser.add_argument(
        "--path",
        type=Path,
        action="append",
        default=None,
        help="Audit this audio file (repeatable). Skips the full cued scan.",
    )
    parser.add_argument(
        "--all-stems",
        action="store_true",
        help="Scan every .vdjstems under --root (not only cued database songs).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.home() / "Music" / "DJ" / "Music",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Only stems modified on/after this date (YYYY-MM-DD).",
    )
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    since_unix = None
    if args.since:
        since_unix = datetime.strptime(args.since, "%Y-%m-%d").timestamp()

    if args.path:
        reports = [audit_stem_file(p) for p in args.path]
    elif args.all_stems:
        reports = scan_stems_under(
            args.root,
            since_unix=since_unix,
            limit=args.limit,
            workers=max(1, args.workers),
        )
    else:
        reports = scan_cued_with_stems(args.database, limit=args.limit)

    broken = [r for r in reports if r.broken]
    errors = [r for r in reports if r.error]
    named = bool(args.path)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(reports),
        "broken": len(broken),
        "errors": len(errors),
        "tracks": [
            r.to_dict()
            for r in reports
            if named or r.broken or r.error
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"checked={len(reports)} broken={len(broken)} errors={len(errors)} "
        f"out={args.out}"
    )
    for r in reports:
        name = Path(r.audio_path).name
        if r.broken:
            print(f"BROKEN {r.hole_seconds:.0f}s holes  {name}")
        elif r.error:
            print(f"ERROR  {r.error}  {name}")
        elif named:
            print(f"ok     {name}")
    return 0 if not broken else 2


if __name__ == "__main__":
    raise SystemExit(main())
