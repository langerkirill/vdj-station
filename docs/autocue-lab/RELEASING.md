# Releasing AutoCue (the vdj-autocue package)

AutoCue is the pip package `vdj-autocue` (`pyproject.toml`). A **release is the GitHub Release
`v<version>`** of the private repo `langerkirill/vdj-autocue-lab`. Pushing a new version
(`vdj_cuer/_version.py`) to vdj-station `main` starts `.github/workflows/release.yml`, which
builds the release assets from that commit (`docs/autocue-lab/autoupdate/build_release_assets.sh`),
checks that the package installs and imports, and then creates the tag `v<version>` on that
commit together with the Release (a failed run creates no tag). The cloud session never pushes
tags (it may push only its branch). The Release carries:

| Asset | What |
|---|---|
| `vdj_autocue-<version>-py3-none-any.whl` | the package pip installs |
| `vdj-autocue-<version>-tests.tar.gz` | its tests, test data and updater scripts (`autoupdate/test_bundle.txt`) |
| `SHA256SUMS` | checksums of both |

Pushing commits without a new version installs nothing on Kirill's Mac; a published release
does (after the Mac's own tests).

## Versions (semantic versioning)

| Bump | When | Examples |
|---|---|---|
| **PATCH** 1.0.x | Fixes, safety, speed. No intended change to the cues written. | a crash, a write that was refused, faster saves |
| **MINOR** 1.x.0 | Changes to what AutoCue writes, or new features. | cue placement, vocal rules, loops, colors, a new option |
| **MAJOR** x.0.0 | Breaking changes. | database write rules, model features that need a retrain, the job interface (`python -m vdj_cuer.autocue_job` arguments/output) the sorters use, dropping a Python version |

The version lives only in `vdj_cuer/_version.py`. Every release has a `## <version> (date)`
section in `CHANGELOG.md`, written for Kirill: what changes in his cues, not code details.

**A version never changes.** Never move or delete a release tag or edit a published release's
assets; fix forward with a new version (the workflow never touches a version that has a release). (The Mac records each version's wheel checksum and
ignores a version whose release ever changes.)

## On the Mac

The auto-updater (`docs/autocue-lab/autoupdate/`) reads the GitHub Releases with the GitHub
login already on the Mac (`gh auth login`, or git's stored login) and installs the newest version
its rule allows:
`~/.vdj-autocue/state/requirement`, default `vdj-autocue>=<first installed>` with no upper
bound, so patches, minors and majors all install once they pass the tests there. Kirill can
change it: `autocue_update.sh --pin 'vdj-autocue>=1.4,<2'` (no automatic majors) or pin one
version (`==1.4.2`). For each new version the Mac downloads its assets (checked against
`SHA256SUMS`), checks the House Python has its dependencies (else it waits and says what to
install), runs the version's tests (from its test bundle) against its installed wheel next to the
installed version's, and installs the wheel with pip into `~/.vdj-autocue/releases/<version>`
only when nothing that passes now fails. Updater script changes ride on releases too: Kirill gets
one notification and installs them with `autocue_update.sh --refresh-updater`.

## Cutting a release (cloud session)

1. Finish the change on vdj-station `main` with tests; commit and push as usual.
2. Bump `vdj_cuer/_version.py`, add the `CHANGELOG.md` section, commit.
3. `python3 docs/autocue-lab/release.py <version>` checks everything, builds the assets exactly
   as CI will, and runs the tests the way the Mac does (the test bundle around the installed
   wheel, AutoCue + House tests, only the failures in `known_test_failures.txt` allowed). Then
   `python3 docs/autocue-lab/release.py <version> --push` pushes the branch; the push carries the
   version bump, which starts the Release workflow.
4. Check that the Release workflow ran and the release has its three assets (GitHub MCP:
   `actions_list` / `list_releases`). A failed workflow publishes nothing; fix forward with the
   next version (never re-tag).
5. Say which version it is and what changes in the cues. The Mac picks it up within about
   15 minutes of the release, unless VirtualDJ is open or a sorter job is running.

Work in progress belongs on the branch without a version bump (or on another branch); only a
published release reaches the Mac. Push a version bump only through `release.py`.
