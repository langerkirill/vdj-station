#!/bin/bash
cd /Users/kirilllanger/src/vdj-station/ui
# One AutoCue at a time. Stem/sklearn OpenMP otherwise saturates the machine
# and holds the GIL so /api/tracks (Set Overview) appears frozen.
export MUSIC_SORTER_AUTOCUE_CONCURRENCY="${MUSIC_SORTER_AUTOCUE_CONCURRENCY:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
export PYTHONUNBUFFERED=1
exec /Users/kirilllanger/src/vdj-station/venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8787
