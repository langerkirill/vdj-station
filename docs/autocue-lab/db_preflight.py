#!/usr/bin/env python3
"""Read-only: would the canonical write gates accept the live database.xml?

    python3 docs/autocue-lab/db_preflight.py [path/to/database.xml]

Every save re-checks the WHOLE file (CRLF + VirtualDJ's Song-row form, after the automatic
healing). One old row in a shape the gates refuse (written by an older tool) would make
every save fail. This prints OK, or the songs whose rows are the problem. It only reads.
Exit code: 0 = OK, 1 = problems found, 2 = could not read.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import vdj_database_safety as V  # noqa: E402

DEFAULT = Path.home() / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
ROW_RE = re.compile(rb"\r\n( <Song\b.*?\r\n[ \t]*</Song>)", re.S)


def main(argv: list[str]) -> int:
    db = Path(argv[1]).expanduser() if len(argv) > 1 else DEFAULT
    try:
        raw = V.sanitize_vdj_database_bytes(db.read_bytes())
    except OSError as exc:
        print(f"Could not read {db}: {exc}")
        return 2
    print(f"{db} ({len(raw) / 1e6:.1f} MB, {raw.count(b'<Song')} songs)")
    try:
        V.assert_crlf_bytes(raw, db.name)
    except V.LineEndingError as exc:
        print(f"PROBLEM: {exc}")
        return 1
    healed = V.heal_song_form(raw)
    problems = V.song_form_problems(healed)
    if not problems:
        note = " (the automatic healing fixes a few seams on the next save)" if healed != raw else ""
        print(f"OK: every save will pass the Song-form check{note}.")
        return 0
    print("PROBLEM: saves would be refused until these are fixed:")
    for p in problems:
        print(f"  - {p}")
    m = V._HEADER_RE.match(healed)
    header = healed[: m.end()] if m else b'<?xml version="1.0" encoding="UTF-8"?>\r\n<VirtualDJ_Database Version="2026">\r\n'
    bad = []
    for row in ROW_RE.finditer(healed):
        if V.song_form_problems(header + row.group(1) + b"\r\n</VirtualDJ_Database>\r\n"):
            fp = re.search(rb'FilePath="([^"]*)"', row.group(1))
            bad.append(fp.group(1).decode("utf-8", "replace") if fp else "(no FilePath)")
    if bad:
        print(f"Songs with rows in a shape VirtualDJ's form check refuses ({len(bad)}):")
        for fp in bad[:50]:
            print(f"  {fp}")
        if len(bad) > 50:
            print(f"  ... and {len(bad) - 50} more")
    else:
        print("(The problem is outside the Song rows: header, footer or blank lines.)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
