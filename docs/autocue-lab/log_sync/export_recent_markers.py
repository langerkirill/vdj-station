#!/usr/bin/env python3
"""Read-only: current cues/loops in database.xml for songs edited in the sorter recently.

    export_recent_markers.py OUT.json ACTION_LOG [ACTION_LOG ...] [--database DB] [--days 14]

Song paths come from the action logs (source_path, dest_path, library/set copies). The
database is only read (a copy of its bytes), never written. Self-contained: no imports
from the repo, so it runs with any python3.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SONG_RE = re.compile(r'<Song\b[^>]*?\bFilePath="([^"]*)"[^>]*>(.*?)</Song>', re.S)
NFC = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731


def paths_in(event: dict) -> set[str]:
    found = set()
    for key in ("source_path", "dest_path"):
        if event.get(key):
            found.add(event[key])
    d = event.get("details") or {}
    for key in ("path", "sets_paths", "set_copies", "cues_sorted_path"):
        v = d.get(key)
        for p in (v if isinstance(v, list) else [v]):
            if isinstance(p, str) and p:
                found.add(p)
    for dest in d.get("library_dests") or []:
        if isinstance(dest, dict) and dest.get("path"):
            found.add(dest["path"])
    pre = d.get("preflight") or {}
    if isinstance(pre, dict) and pre.get("path"):
        found.add(pre["path"])
    return {NFC(p) for p in found}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--database", default=str(Path.home() / "Library/Application Support/VirtualDJ/database.xml"))
    ap.add_argument("--days", type=float, default=14)
    a = ap.parse_args(argv)

    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    wanted: dict[str, str] = {}  # path -> last edit ts
    for log in a.logs:
        p = Path(log).expanduser()
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
                ts = datetime.fromisoformat(ev["ts"])
            except Exception:
                continue
            if ts < since:
                continue
            for path in paths_in(ev):
                wanted[path] = max(wanted.get(path, ""), ev["ts"])

    db = Path(a.database).expanduser()
    text = db.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")
    songs = {}
    for m in SONG_RE.finditer(text):
        path = NFC(html.unescape(m.group(1)))
        if path not in wanted:
            continue
        try:
            el = ET.fromstring(f"<Song>{m.group(2)}</Song>")
        except ET.ParseError:
            continue
        scan = el.find("Scan")
        markers = []
        for poi in el.findall("Poi"):
            if poi.get("Type") not in ("cue", "loop"):
                continue
            markers.append({k: poi.get(k) for k in ("Name", "Pos", "Num", "Color", "Type", "Size") if poi.get(k) is not None})
        comment = el.find("Comment")
        songs[path] = {
            "last_edit": wanted[path],
            "bpm_scan": scan.get("Bpm") if scan is not None else None,
            "phase": scan.get("Phase") if scan is not None else None,
            "comment": comment.text if comment is not None else None,
            "markers": sorted(markers, key=lambda x: float(x.get("Pos") or 0)),
        }
    # No export time / file size here: the file only changes when a song's markers do,
    # so the 15-minute sync does not commit when nothing happened.
    out = {"days": a.days, "songs": dict(sorted(songs.items()))}
    tmp = Path(a.out + ".tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(a.out)
    print(f"exported {len(songs)} of {len(wanted)} recently edited songs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
