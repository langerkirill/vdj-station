#!/bin/bash
# HOUSE FORK of Music Sorter — port 8788, WRITE MODE, isolated notes dir.
# Runs from THIS fork's ui/ with PYTHONPATH at THIS fork's repo root so it imports
# the fork's vdj_database_safety.py / vdj_cuer (never the live vdj-station repo).
FORK_ROOT="/Users/kirilllanger/src/vdj-station-house"
cd "$FORK_ROOT/ui"
export MUSIC_SORTER_PROFILE=house
export MUSIC_SORTER_READONLY=0
export MUSIC_SORTER_GEMINI_MODEL=gemini-3.8-flash
export MUSIC_SORTER_NOTES_DIR="$HOME/Music/DJ/Notes/House-8788"
export PYTHONPATH="$FORK_ROOT${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$MUSIC_SORTER_NOTES_DIR"   # the ONLY directory this fork creates up front
# Gemini key: loaded by sorter/vdj_cuer from the fork's own .env (never printed).
set -a; [ -f "$FORK_ROOT/.env" ] && . "$FORK_ROOT/.env"; set +a
# Keep the fork's model pin even if .env sets GEMINI_MODEL.
export MUSIC_SORTER_GEMINI_MODEL=gemini-3.8-flash
# One AutoCue at a time; stem/sklearn OpenMP otherwise saturates the machine.
export MUSIC_SORTER_AUTOCUE_CONCURRENCY="${MUSIC_SORTER_AUTOCUE_CONCURRENCY:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
export PYTHONUNBUFFERED=1
echo "[start_8788] fork=$FORK_ROOT profile=$MUSIC_SORTER_PROFILE readonly=$MUSIC_SORTER_READONLY notes=$MUSIC_SORTER_NOTES_DIR model=$MUSIC_SORTER_GEMINI_MODEL"
"$FORK_ROOT/venv/bin/python" - <<'PY'
import vdj_database_safety, sys
print("[start_8788] vdj_database_safety =", vdj_database_safety.__file__)
PY
exec "$FORK_ROOT/venv/bin/python" -m uvicorn app:app --host 127.0.0.1 --port 8788
