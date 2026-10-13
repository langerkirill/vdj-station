#!/usr/bin/env python3
"""Cut an AutoCue release: the GitHub Release v<version> that the Mac's auto-updater installs.

    python3 docs/autocue-lab/release.py 1.0.1            # check, build, test; push only with --push
    python3 docs/autocue-lab/release.py 1.0.1 --push

Refuses unless:
  - the branch is autocue-lab-alludd with nothing uncommitted (the release is that commit);
  - vdj_cuer/_version.py says <version> and CHANGELOG.md has a "## <version>" section;
  - <version> is newer than every v* tag, and v<version> exists neither here nor on GitHub
    (a version never changes: fix forward with a new one);
  - the wheel builds, imports from a clean folder with its model, and AutoCue's tests plus the
    House sorter's tests pass against the INSTALLED package (as the Mac's gate runs them),
    except the failures listed in docs/autocue-lab/known_test_failures.txt.
Then pushes the branch. The push carries the version bump, so GitHub Actions
(.github/workflows/release.yml) builds the same assets from that commit, checks them, and creates
the tag v<version> together with the GitHub Release the Mac installs from (this session may push
only its branch, never tags; a failed workflow creates no tag, so the version is not used up).
Semantic versioning: docs/autocue-lab/RELEASING.md.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BRANCH = "autocue-lab-alludd"
KNOWN = REPO / "docs" / "autocue-lab" / "known_test_failures.txt"
sys.path.insert(0, str(REPO / "docs" / "autocue-lab" / "autoupdate"))
from autocue_gate import _env, run_suite  # noqa: E402  (same JUnit runner as the Mac gate)

try:
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.version import Version


def git(*args, check=True) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=check, capture_output=True, text=True).stdout.strip()


def die(msg: str) -> None:
    print(f"release: {msg}", file=sys.stderr)
    sys.exit(1)


def changelog_section(ver: str) -> str:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(ver)}\b.*?(?=^## |\Z)", text, re.M | re.S)
    return m.group(0).strip() if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--push", action="store_true", help="tag and push (otherwise only check)")
    a = ap.parse_args()
    ver = a.version.lstrip("v")
    if str(Version(ver)) != ver:
        die(f"{ver} is not a normalized version (e.g. 1.0.1)")

    if git("rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        die(f"not on {BRANCH}")
    if git("status", "--porcelain", "--untracked-files=no"):
        die("uncommitted changes: commit first (the tag is the commit)")
    file_ver = re.search(r'__version__ = "([^"]+)"', (REPO / "vdj_cuer" / "_version.py").read_text()).group(1)
    if file_ver != ver:
        die(f"vdj_cuer/_version.py says {file_ver}; set it to {ver} and commit")
    notes = changelog_section(ver)
    if not notes:
        die(f"CHANGELOG.md has no '## {ver}' section")
    git("fetch", "-q", "--tags", "origin", check=False)
    tags = [t[1:] for t in git("tag", "-l", "v*").split() if t]
    remote = git("ls-remote", "--tags", "origin", f"refs/tags/v{ver}", check=False)
    if ver in tags or remote:
        die(f"v{ver} exists already; a version never changes: use a new one")
    valid = []
    for t in tags:
        try:
            valid.append(Version(t))
        except Exception:
            pass
    if valid and Version(ver) <= max(valid):
        die(f"{ver} is not newer than v{max(valid)}")

    head = git("rev-parse", "HEAD")
    known = {l.strip() for l in KNOWN.read_text().splitlines() if l.strip() and not l.startswith("#")} if KNOWN.is_file() else set()
    # not under /tmp (the House read-only guard allows it) and not in a .dot folder (the House
    # library scan skips those): either would quietly hide tests
    scratch = Path("/var/tmp") if os.access("/var/tmp", os.W_OK) else REPO.parent
    work = Path(tempfile.mkdtemp(prefix="autocue-release-", dir=scratch))
    home_root = work / "homes"
    try:
        # the release assets exactly as CI builds them (build_release_assets.sh), and the tests
        # exactly as the Mac's gate runs them: the test bundle around the pip-installed wheel
        dist, tree, pure = work / "dist", work / "tree", work / "pure"
        built = subprocess.run(["bash", str(REPO / "docs" / "autocue-lab" / "autoupdate" / "build_release_assets.sh"),
                                str(dist)], cwd=REPO, capture_output=True, text=True,
                               env={**os.environ, "PYTHON": sys.executable})
        if built.returncode != 0 or built.stdout.strip().splitlines()[-1:] != [ver]:
            die(f"the release assets do not build:\n{built.stderr[-1500:]}")
        wheel = dist / f"vdj_autocue-{ver}-py3-none-any.whl"
        tree.mkdir()
        subprocess.run(["tar", "-xzf", str(dist / f"vdj-autocue-{ver}-tests.tar.gz"), "-C", str(tree)], check=True)
        for target in (tree, pure):
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-index",
                            "--target", str(target), str(wheel)], check=True)
        smoke = subprocess.run(
            [sys.executable, "-c",
             "import sys, importlib.metadata as m, vdj_database_safety, vdj_cue_patch, vdj_cuer.cue_writer, "
             "vdj_cuer.autocue_job, vdj_cuer.ml.model as M; "
             "assert m.version('vdj-autocue') == sys.argv[1]; assert M.SHIPPED_ARTIFACT.stat().st_size > 0", ver],
            cwd="/", env={**os.environ, "PYTHONPATH": str(pure)}, capture_output=True, text=True)
        if smoke.returncode != 0:
            die(f"the installed package does not load:\n{smoke.stderr[-1500:]}")
        sizes = ", ".join(f"{f.name} {f.stat().st_size // 1024} KB" for f in sorted(dist.iterdir()))
        print(f"release assets build, install and load: {sizes}")

        failures = []
        runs = [("tests", tree, "tests", _env(home_root / "a", tree)),
                ("House ui/tests", REPO / "house-fork", "ui/tests",
                 _env(home_root / "h", pure, [REPO / "house-fork", REPO / "house-fork" / "ui"], autocue_root=pure))]
        for name, cwd, target, env in runs:
            r = run_suite(sys.executable, cwd, target, env, work / f"{name.split()[0]}.xml", 1800)
            if not r.get("ok"):
                die(f"{name} did not run: {r.get('why')}\n{r.get('tail', '')}")
            new = sorted(set(r["failed"]) - known)
            print(f"{name}: {r['passed']} passed, {len(r['failed'])} failed ({len(r['failed']) - len(new)} known)")
            failures += new
        if failures:
            die("new test failures:\n  " + "\n  ".join(failures))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(home_root, ignore_errors=True)

    if not a.push:
        print(f"ready: run again with --push to tag v{ver} on {head[:7]} and push it")
        return 0
    # the Release workflow runs on pushes that change vdj_cuer/_version.py or the workflow itself
    git("fetch", "-q", "origin", BRANCH, check=False)
    changed = git("diff", "--name-only", f"origin/{BRANCH}", head, check=False).split()
    if not {"vdj_cuer/_version.py", ".github/workflows/release.yml"} & set(changed):
        die(f"nothing in this push starts the Release workflow (vdj_cuer/_version.py is already {ver} on "
            "GitHub): the version bump must be in the push")
    for attempt in range(4):
        if subprocess.run(["git", "-C", str(REPO), "push", "-u", "origin", BRANCH]).returncode == 0:
            break
        if attempt == 3:
            die(f"could not push {BRANCH}")
        import time
        time.sleep(2 ** (attempt + 1))
    print(f"pushed {head[:7]} with version {ver}: GitHub Actions (.github/workflows/release.yml) now builds "
          f"and checks it and creates the tag v{ver} and its Release; the Mac installs it after its own "
          "tests, within ~15 minutes of that")
    return 0


if __name__ == "__main__":
    sys.exit(main())
