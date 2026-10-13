#!/usr/bin/env python3
"""Put one song's cues and loops back from a database.xml backup.

    # 1. Which backups still have the markers? (newest first, read-only)
    python3 restore_song_markers.py --song "/path/to/song.flac" --find
    # 2. Preview: current markers vs the backup's (read-only)
    python3 restore_song_markers.py --song "/path/to/song.flac" --backup "<backup file>"
    # 3. Restore (VirtualDJ closed): only this song's cue/loop rows and its Comment change
    python3 restore_song_markers.py --song "/path/to/song.flac" --backup "<backup file>" --apply

The rest of the song (Tags, Infos, Scan, beatgrid, automix) and every other song stay as
they are now. The write goes through vdj_database_safety (CRLF / Song-form gates, lock,
read-back, rollback) like any other edit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import vdj_database_safety as V

DEFAULT_DB = Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"


def song_block(db_text: str, song: str) -> str | None:
    span = V._find_song_span(db_text, V.normalize_database_path(song))
    return None if span is None else db_text[span[0]:span[1]]


def marker_rows(block: str) -> list[str]:
    return [m.group(0) for m in V._POI_LINE_RE.finditer(block) if V._is_manual_cue_or_loop_poi(m.group(0))]


def comment_row(block: str) -> str:
    m = V._COMMENT_RE.search(block)
    return m.group(0) if m else ""


def describe(block: str | None) -> list[str]:
    if block is None:
        return ["(song not in this database)"]
    found = V.extract_manual_pois_from_song_xml(block)
    out = []
    for kind in ("cues", "loops"):
        for p in found[kind]:
            size = f" {p['length_beats']:g} beats" if p["kind"] == "loop" else ""
            out.append(f"{p['position']:9.3f}s {p['kind']:4} {p['name']:<24} color={p['color']}{size}")
    return sorted(out) or ["(no cues or loops)"]


def restored_block(current: str, backup: str) -> str:
    """``current`` with its cue/loop rows and Comment replaced by the backup's."""
    newline = V._detect_newline(current)
    cleaned = V.strip_manual_cues_from_song_xml(current)
    close = cleaned.rfind("</Song>")
    if close < 0:
        raise ValueError("Song XML is missing </Song>")
    body = cleaned[:close].rstrip(" \t")
    if not body.endswith("\n"):
        body += newline
    rows = [r if r.endswith(("\n", "\r")) else r + newline for r in marker_rows(backup)]
    comment = comment_row(backup)
    if comment and not comment.endswith(("\n", "\r")):
        comment += newline
    return body + "".join(rows) + comment + cleaned[close:]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--song", required=True, help="audio file path as it is in VirtualDJ")
    ap.add_argument("--database", default=str(DEFAULT_DB))
    ap.add_argument("--backup", help="backup database.xml to take the markers from")
    ap.add_argument("--find", action="store_true", help="list backups next to the database that have this song's markers")
    ap.add_argument("--apply", action="store_true", help="write (VirtualDJ must be closed)")
    args = ap.parse_args(argv)
    db = Path(args.database).expanduser()

    current = song_block(V.read_vdj_database_text(db), args.song)
    print(f"Now in {db.name}:")
    for line in describe(current):
        print(f"  {line}")

    if args.find:
        backups = sorted(
            (p for p in db.parent.glob(f"{db.name}*") if p != db and p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        print(f"\nBackups next to {db.name}, newest first (cues/loops for this song):")
        for p in backups[:40]:
            try:
                blk = song_block(V.read_vdj_database_text(p), args.song)
            except Exception as exc:  # unreadable / partial files
                print(f"  {p.name}: unreadable ({exc.__class__.__name__})")
                continue
            if blk is None:
                print(f"  {p.name}: song not present")
                continue
            found = V.extract_manual_pois_from_song_xml(blk)
            print(f"  {p.name}: {len(found['cues'])} cues, {len(found['loops'])} loops")
        return 0

    if not args.backup:
        print("\nPass --backup <file> (use --find to pick one).")
        return 2
    if current is None:
        print("The song is not in the current database; nothing to restore into.")
        return 1
    backup = song_block(V.read_vdj_database_text(Path(args.backup).expanduser()), args.song)
    print(f"\nIn backup {Path(args.backup).name}:")
    for line in describe(backup):
        print(f"  {line}")
    if backup is None or not marker_rows(backup):
        print("The backup has no cues or loops for this song; nothing to restore.")
        return 1
    new_block = restored_block(current, backup)
    if new_block == current:
        print("\nAlready identical; nothing to do.")
        return 0
    if not args.apply:
        print("\nPreview only. Re-run with --apply (VirtualDJ closed) to restore these markers.")
        return 0
    V.rewrite_song_xml_in_database(db, V.normalize_database_path(args.song), new_block, base_song_xml=current)
    print("\nRestored. Now in database.xml:")
    for line in describe(song_block(V.read_vdj_database_text(db), args.song)):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
