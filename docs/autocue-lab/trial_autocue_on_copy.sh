#!/bin/bash
# Try the canonical AutoCue on a few songs WITHOUT touching VirtualDJ's database.
#
#   docs/autocue-lab/trial_autocue_on_copy.sh "/path/song1.mp3" "/path/song2.flac" ...
#
# - Copies database.xml to a fresh trial folder and writes ONLY to that copy
#   (full write path: CRLF / Song-form gates, heal, read-back).
# - Analysis cache off, so you see what the new code decides, not a cached plan.
# - Prints each song's cues/loops: current (real DB) vs new (trial copy).
# - Checks the real database.xml is byte-identical before and after.
# VirtualDJ may stay open: the real database is only read.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
REAL_DB="${VDJ_REAL_DB:-$HOME/Library/Application Support/VirtualDJ/database.xml}"
# The House venv saved the cue model (scikit-learn version must match), so prefer it.
PY="${AUTOCUE_PYTHON:-$HOME/src/vdj-station-house/venv/bin/python}"
[ -x "$PY" ] || PY="$HOME/src/vdj-station/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
[ "$#" -ge 1 ] || { echo "usage: $0 <audio file> [more audio files]"; exit 2; }
[ -f "$REAL_DB" ] || { echo "No database at $REAL_DB (set VDJ_REAL_DB)"; exit 2; }

TRIAL="$HOME/Music/DJ/AutoCue-trials/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$TRIAL"
cp -p "$REAL_DB" "$TRIAL/database.xml"
REAL_SHA="$(shasum -a 256 "$REAL_DB" | cut -d' ' -f1)"

# Gemini key etc. (never printed). Prefer the repo's own .env, else the station's.
set -a
for envf in "$REPO/.env" "$HOME/src/vdj-station-house/.env" "$HOME/src/vdj-station/.env"; do
  if [ -f "$envf" ]; then . "$envf"; break; fi
done
set +a
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export AUTOCUE_DISABLE_ANALYSIS_CACHE=1
export VDJ_ALLOW_RUNNING_WRITES=1          # this process writes the trial copy only
export MUSIC_SORTER_WRITE_BACKUP_DIR="$TRIAL/backups"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1

echo "repo:  $REPO ($(git -C "$REPO" rev-parse --short HEAD))"
echo "trial: $TRIAL"
"$PY" -c "import vdj_database_safety as v; print('safety module:', v.__file__)"

i=0
for audio in "$@"; do
  i=$((i + 1))
  echo; echo "=== [$i/$#] $(basename "$audio")"
  "$PY" -m vdj_cuer.autocue_job --audio "$audio" --database "$TRIAL/database.xml" \
    --result "$TRIAL/result_$i.json" 2>&1 | tee "$TRIAL/log_$i.txt" | tail -25 || true
done

echo; echo "=== Cues/loops: current (real DB) -> new (trial copy)"
"$PY" - "$REAL_DB" "$TRIAL/database.xml" "$@" <<'PY'
import sys
import xml.etree.ElementTree as ET
import vdj_database_safety as V

real, trial, songs = sys.argv[1], sys.argv[2], sys.argv[3:]

def pois(db_text, audio):
    span = V._find_song_span(db_text, V.normalize_database_path(audio))
    if span is None:
        return None
    song = ET.fromstring(db_text[span[0]:span[1]])
    rows = []
    for p in song.findall("Poi"):
        if p.get("Type") in ("cue", "loop") and p.get("Num", "0") != "0":
            rows.append((float(p.get("Pos", "0")), p.get("Type"), p.get("Name", ""),
                         p.get("Color", ""), p.get("Size", "")))
    return sorted(rows)

a, b = V.read_vdj_database_text(real), V.read_vdj_database_text(trial)
for s in songs:
    print(f"\n{s}")
    for label, rows in (("current", pois(a, s)), ("new", pois(b, s))):
        if rows is None:
            print(f"  {label}: (song not in database)")
            continue
        print(f"  {label}: {len(rows)} markers")
        for pos, kind, name, color, size in rows:
            extra = f" size={size}" if kind == "loop" else ""
            print(f"    {pos:8.2f}s {kind:4} {name:<22} color={color}{extra}")
PY

AFTER_SHA="$(shasum -a 256 "$REAL_DB" | cut -d' ' -f1)"
echo
if [ "$REAL_SHA" = "$AFTER_SHA" ]; then
  echo "Real database.xml unchanged (sha256 identical)."
else
  echo "NOTE: real database.xml changed during the trial. This script never writes it;"
  echo "      VirtualDJ itself saves it while open. Check the trial log if unsure."
fi
echo "Trial folder (copy DB, logs, JSON results): $TRIAL"
