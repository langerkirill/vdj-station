# Music Sorter: House fork (port 8788)

A **read-only** copy of the Music Sorter UI tuned for organic / melodic / deep house.
It lives in `~/src/vdj-station-house` and never touches the live Zouk build on 8787
(`~/src/vdj-station`).

**Current live build:** `20261005-house-sauna-fest11` (fest11) on port 8788.
Bump `UI_BUILD` in `ui/app.py` and this line on every live ship.
Write mode: `ui/start_8788_write.sh`. Live Mac path: `~/src/vdj-station-house` — do not `git checkout` this tree while :8788 is serving.

## Run

```bash
~/src/vdj-station-house/ui/start_8788.sh      # foreground, 127.0.0.1:8788, no reload
```

The script `cd`s into this fork's `ui/`, sets `PYTHONPATH` to the fork root (so
`vdj_database_safety.py` and `vdj_cuer/` come from the fork, never from 8787) and prints
the `__file__` of those modules at startup (`[house-fork] ...` lines). To run it detached,
start it from a Python `subprocess.Popen(..., start_new_session=True)` (plain `nohup &`
dies with the calling shell).

## Environment

| var | value |
|---|---|
| `MUSIC_SORTER_PROFILE` | `house` |
| `MUSIC_SORTER_READONLY` | `1` (never write VDJ or the library) |
| `MUSIC_SORTER_GEMINI_MODEL` | `gemini-3.8-flash` (fallbacks 3.7, 3.6, 3.5 flash) |
| `MUSIC_SORTER_NOTES_DIR` | `~/Music/DJ/Notes/House-8788` |
| `MUSIC_SORTER_FS_ALLOW` | optional extra writable roots (os.pathsep-separated) |

## Read-only guarantees (defence in depth)

1. `vdj_database_safety`: `assert_safe_to_write_vdj_database`, `vdj_database_exclusive_lock`,
   snapshot / recover / atomic copy all raise `ReadOnlyModeError` ("Read-only (VDJ open)").
   `VDJ_ALLOW_RUNNING_WRITES` cannot bypass it.
2. Sort / relocate / promote / delete / cue-edit entry points are wrapped to force `dry_run=True`.
3. HTTP middleware returns 403 for mutating endpoints (cue/grid/BPM/notes/folders/undo/delete...).
4. `sorter/fs_guard.py` patches `open`, `shutil`, `os.*` write calls: writes outside the Notes dir,
   temp dirs and OS caches raise. This is the backstop if a code path forgot the flag.
5. Startup hooks `restore_jobs`, the sideview-recs watcher and `seed_historical_sorts` are skipped, and
   `write_sideview_recs` (VDJ MyLists / Cues folders) is a no-op.
6. The UI shows a **Read-only (VDJ open)** badge; Sort becomes a *Preview sort* (dry-run).

## House profile

- One library: `House` = `~/Music/DJ/Music/House` (the existing House folder). Sort destinations are its EXISTING subfolders (Amped, Bassy, Chill/…, Energy/…, nested ok) plus an explicit "New folder in House" option. Cues Sorted and House are both treated as cued destinations. No Zouk, no lanes/colors, no "Both".
- **Sort COPIES, it never moves**: sha1-verified true copy (+ `.vdjstems`) into the House folder, then ONE locked, backup-first, read-back-verified `database.xml` Song clone (cues, loops, beatgrid, color; `User2` from the destination folder). The original stays in Add Cues. Existing destination files/entries are skipped, never overwritten.
- New folders: validated (no separators/`..`, not an existing name, only directly under House or under an existing House subfolder), created on the first copy into them only, and capped at 3 created without asking Kirill (count persisted in `<notes>/new_house_folders.json`; at the cap the server refuses and says to ask Kirill).
- Every database write goes through `sorter/safe_write.py`: re-read inside the cross-process lock, refuse if VirtualDJ is running / another writer touched `database.xml` in the last 20 s / a `*vdjdev*` backup is newer than 60 s / the Song changed since it was read; one timestamped backup per server session in `~/Music/DJ/Notes/backups-8788-write/`; read-back verification returns `saved` true/false + reason. The UI shows a Saved / FAILED badge and a persistent list of failed edits.
- Default Add Cues folder: `Sauna Fest House` (selector in the list toolbar; "All folders" available).
- Recs mode: fixed target 120 BPM, candidates limited to 115-125 and Camelot-compatible with the source.
- Grid: no Halve-BPM suggestion; never halves 100-135 BPM; no 138-168 "always halve" band.
- Add Cues list: BPM min/max (+ `115-125` chip, Clear), sort by BPM or Camelot key (1A, 1B, 2A ... 12B),
  BPM and key shown per row; missing values show an em dash and sort last.
- Notes, caches, AutoCue jobs, action log (`music-sorter-actions.jsonl`), transitions.db etc. live in
  `~/Music/DJ/Notes/House-8788/`. Transition note files are read (never written) from the real Notes dir.

## Tests

```bash
cd ui && PYTHONPATH=.. ../venv/bin/python -m pytest tests -q -p no:cacheprovider
```

Three tests fail identically on the unmodified live working tree (see the build report):
`test_autocue_retry::test_run_job_spawns_subprocess_and_marks_ok`,
`test_grid_batch::LivePajamathonFixtureTests`, `test_placements_regression::LazyListContractTests`.
