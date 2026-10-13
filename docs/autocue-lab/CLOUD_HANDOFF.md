# AutoCue lab: cloud handoff (2026-10-08)

Goal (Kirill): the AutoCue code lives in the cloud (GitHub). It is developed, measured and tested there.
His Mac runs it locally on the VirtualDJ station by pulling one canonical checkout. The House
station (8788) and the main station (8787) should both import AutoCue from that one checkout
instead of keeping vendored copies.

## What is in this repo

| Path | What it is |
|---|---|
| repo root (branch `autocue-lab`) | `~/src/vdj-station` working tree as of 2026-10-08 18:00, on top of local `main` = `d73e430`. Excludes venv, .env, node_modules, screenshots and root scratch scripts. |
| `upstream/main` (remote `upstream` = public `langerkirill/vdj-station`) | `834ba38` (2026-10-05) "promote live fest11 to main", 111 files. **Not** in the local station checkout. |
| `house-fork/` | Snapshot of `~/src/vdj-station-house` (local-only repo; the 8788 House UI plus its vendored `vdj_cuer` and `vdj_database_safety.py`). `GIT_HISTORY.txt` lists its commits; baseline `dc2206f` = copy of the station tree on 2026-10-03 19:19. `AUTOCUE_CHANGES_SINCE_BASELINE.diff` shows House's own AutoCue edits. |
| `house-fork/merge-dryrun/` | 3-way merge inputs/outputs per file (`.base` = house dc2206f, `.station`, `.house`, `.merged`). **`cue_bar_clf.joblib.merged` is 0 bytes. Never install it.** |
| `docs/autocue-lab/merge_review_2026-10-08.json` | Six read-only reviewer reports (summarized below). |
| `bench/` | Audio-free benchmark bundle and tools (see `bench/README.md`). |
| `bench/improve-partial/` | Unfinished prototypes from an interrupted improvement sweep (cue-position, cue-deletions, loops, naming, colors-remaining incl. a mix-based vocal detector `mixvox.py`, pipeline-bugs). Caches were dropped; scripts use local Mac paths. |

## Done on 2026-10-08 (in the station tree, all tests pass: 456 passed + 4 failures that predate this work)

Measured against Kirill's own edits (`bench/labels/music-sorter-actions.jsonl`):

1. **Voice-band vocals** (`stem_evidence.py`). Vocal decisions use a 250-4000 Hz envelope of the vocal and instruments stems, not the 800 Hz peak envelope. On 101 hand-recolored markers / 40 songs: vocal recall 0.62 -> 0.91, leave-one-song-out accuracy 0.81 -> 0.95, precision 0.97 -> 0.98. Test: `tests/test_voice_band_vocals.py`.
2. **Drums/melody for colors** (`stem_evidence.section_band_flags`, `stem_color`, `cue_writer`). Drums = first-bar full-band kick >= 15% of the kick stem's p95 (hats alone are not drums). Melody = 8-beat instruments voice band >= 22% (bass is not melody). Exact color on 91 markers: 0.66 -> 0.86 in-sample, ~0.82 leave-one-song-out. Test: `tests/test_band_drum_melody.py`.
3. **Vocal pickup rule** (`cue_hygiene.vocal_line_starts_before`, 20 ms vocal envelope). A sung line starting within the last beat before a cue makes the cue "half-entered": move it to a quiet bar within 2 bars, else skip it. On Kirill's deletes/moves vs kept cues it flagged 7/34 rejected vs 2/25 kept. Test: `tests/test_vocal_pickup.py`.
4. **Seam re-test where a loop is written** (`cue_hygiene.final_loop_seam_ok`, `cue_writer`). Grid snap and fit-before-song-end used to move or shorten loops after the seam gate passed. Test: `tests/test_final_loop_seam.py`.

House UI only (`house-fork/ui`): loops place/drag on any beat, and resize keeps whole 16-beat phrases from the loop's own start (`ui/tests/test_loop_any_beat.py`). A separate session is adding a "Kirill approved" button on Practice transitions.

## Merge review findings (must be handled before canonicalizing)

- **CRITICAL semantic conflict in `vdj_database_safety.py`.** The station's post-write read-back in `rewrite_song_xml_in_database` (`if _n2(song_xml) not in _n2(back): raise 'Write read-back mismatch ... NOT saved'`) fights House's whole-file `heal_song_form` in `_replace_database_parts_locked`. AutoCue writes would fail. Fix: canonicalize both sides (`canonical_song_block` / `heal_song_form`) before comparing when not in `legacy_fixture_mode()`. Add a regression test with a VDJ-form CRLF DB.
- **Model.** Keep the House `cue_bar_clf.joblib` + `.json` (saved 2026-10-08, 708 tracks / 73,416 rows, includes Kirill's corrections). The station's is from 2026-08-30. The model is not in this repo's bench; take it from `house-fork/vdj_cuer/ml/artifacts/`.
- **Station tests need House's `tests/conftest.py`** (`MUSIC_SORTER_ALLOW_LF_DB=1`). Without it, 15 station tests fail against the hardened module. Also port House `ui/tests/test_crlf_hard_gate.py` and `ui/tests/test_song_row_form.py`.
- **Write latency.** The merged module heals the whole file on every save: ~0.45 s -> ~12.7 s on a synthetic 21 MB DB. Needs optimization (heal only the touched Song block, or detect-then-heal).
- **Hard-coded second-opinion path.** `_AUTOCUE_SAFETY_PATH = /Users/kirilllanger/src/vdj-automatic-cuer/vdj_database_safety.py` (a stale d73e430 checkout). Make it default to self and only use an external module via env var.
- `vdj_cue_patch.py` also has House CRLF/VDJ-detection changes that were not in the dry-run set.
- `core.py` / `file_processor.py`: clean (station never changed them; take House).
- `zouk` branch commit `3bc4a77` (in `~/src/_git-ops/vdj-station-soT`): nothing is lost; the station tree is a superset.
- **Switch order for 8788.** (1) Land House fixes in canonical. (2) Change `house-fork/ui/sorter/autocue_path.py` (it inserts the fork root at `sys.path[0]` and shadows everything) to use `VDJ_AUTOCUE_ROOT`, plus `grid_preflight.py:331`, the start scripts and the test conftests that pin the fork root. (3) On the Mac, with 8788 stopped and no AutoCue/ingest/grid jobs running, move the vendored copies to a dated folder (never delete them) and restart.

## Next steps for the cloud session

1. Integrate into one canonical tree: station WIP (this branch) + `upstream/main` 834ba38 + House AutoCue fixes. Apply the critical fixes above test-first, and run both test suites (`tests/`, `ui/tests/`, `house-fork/ui/tests/`).
2. Rebuild the benchmark harness on `bench/` (no audio) and re-run the improvement sweep from `bench/improve-partial/`: cue position (cues 4/8/16 bars off), deletions, loop length/seam, naming, remaining colors (hat-only purple vs blue; Dreams 029 has no vocal in its stem; try the mix-based detector), pipeline bugs. Use leave-one-song-out, and get two adversarial verifiers per proposal.
3. Do not push personal data to the public `langerkirill/vdj-station`. Only canonical code goes there, via PR, after Kirill approves.
4. Mac-only steps (ask Kirill first): switch 8788 to the canonical checkout, restart, then do one AutoCue dry run before any real write.

## Running AutoCue: now and later

- **Now (no API):** the Mac keeps one checkout of the canonical repo, `git pull`s improvements and runs AutoCue locally as today. The VDJ database is written locally through `vdj_database_safety`.
- **AutoCue is the pip package `vdj-autocue`; a release is the GitHub Release `v<version>`**: pushing a version bump (only via `release.py`) makes `.github/workflows/release.yml` build the wheel, a test bundle and SHA256SUMS and create the tag and the Release (this session cannot push tags) (docs/autocue-lab/RELEASING.md). Kirill's Mac downloads and installs the newest version its rule allows (default `vdj-autocue>=<installed>`, no upper bound) after AutoCue's and the House sorter's tests pass there against the installed package; 8788 restarts on it once idle. **Plain pushes to `autocue-lab-alludd` install nothing**: cut a release with `python3 docs/autocue-lab/release.py <version> --push`, then check the Release workflow and the release assets with the GitHub MCP tools (bump `vdj_cuer/_version.py`, add the CHANGELOG.md section first). PATCH = fixes with no intended cue change, MINOR = changes to the cues written or features, MAJOR = breaking (DB rules, model features, the job interface). Never move or delete a pushed tag. The tests are the only gate, so a behaviour change worth protecting needs a test. Updater script changes ship inside releases (the test bundle) and reach the Mac when Kirill runs `--refresh-updater`. The House sorter needs `house-busy-endpoint.patch` for automatic restarts.
- **Later (cloud compute, small API):** the Mac uploads a song's `.vdjstems` (or features), the cloud returns the cue/loop plan as JSON (the `PreparedSongCues` boundary in `cue_writer.prepare_song_cues`), and the Mac applies it locally with a dry-run preview. `database.xml` writes must always stay on the Mac (the file lives there and VirtualDJ must be closed).

Open questions for Kirill:
- He keeps 7/25 cues mid-singing; does he still want the existing mid-word rule?
- The Gemini seam check passes loops when Gemini is unavailable.
- Should approved transitions feed "Best for set"?
