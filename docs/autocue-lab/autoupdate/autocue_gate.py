#!/usr/bin/env python3
"""Test gate for the Mac auto-updater: may version CAND replace the installed version BASE?

    autocue_gate.py --python PY --base DIR --cand DIR --work DIR [--house DIR] [--timeout S]

Runs, back to back and in the same environment, AutoCue's tests/ for BASE and CAND, and (with
--house) the House sorter's ui/tests against each (VDJ_AUTOCUE_ROOT), which is what 8788 runs.
Without --base (no installed package version to compare with, e.g. a release of the first
updater), CAND may fail only the test ids in its own known_test_failures.txt.
Results come from pytest's JUnit XML (failures, errors and failing subtests all count), never
from scraping text. CAND is accepted only if:
  - every run finished (pytest exit 0 or 1, XML present, at least one test ran, no timeout);
  - CAND fails no test that BASE passes (on a new failure the whole suite runs once more; it
    must fail again, which rules out a flake but keeps order-dependent failures);
  - with --house, the House suite exists and runs;
  - CAND passes at least 90% as many tests as BASE.
Prints one JSON object: {"accept": bool, "reason": str, ...}. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _env(work: Path, root: Path, extra_pythonpath=(), autocue_root=None) -> dict:
    """HOME and TMPDIR under ``work``: keep ``work`` out of hidden folders (the House library scan
    skips any path with a .dot folder) and out of /tmp (the House read-only guard allows it)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VDJ_", "MUSIC_SORTER_", "PYTEST_", "AUTOCUE_GITHUB_"))
           and k not in ("GH_TOKEN", "GITHUB_TOKEN")}  # tests never see a GitHub token
    home = work / "home"
    tmp = work / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env.update(
        HOME=str(home),
        TMPDIR=str(tmp),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join([str(root), *map(str, extra_pythonpath)]),
        VDJ_ALLOW_RUNNING_WRITES="1",           # tests use temp databases; VDJ may be open
        VDJ_DB_VERIFIED_CACHE="off",
        VDJ_AUTOCUE_MODEL_DIR="",
    )
    if autocue_root is not None:
        env["VDJ_AUTOCUE_ROOT"] = str(autocue_root)
    return env


def run_suite(py: str, cwd: Path, target: str, env: dict, xml: Path, timeout: float) -> dict:
    xml.unlink(missing_ok=True)
    # --rootdir: test ids must not depend on whether a version has a pyproject.toml (a version from
    # before the package vs one after would otherwise name the same test differently)
    cmd = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", f"--rootdir={cwd}",
           f"--junitxml={xml}"]
    cmd.append(target)
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": f"timed out after {int(timeout)} s"}
    out = {"exit": proc.returncode, "tail": (proc.stdout + proc.stderr)[-1500:]}
    if proc.returncode not in (0, 1):
        return {**out, "ok": False, "why": f"pytest exit {proc.returncode} (collection error, crash or no tests)"}
    try:
        root = ET.parse(xml).getroot()
    except (OSError, ET.ParseError) as exc:
        return {**out, "ok": False, "why": f"no readable test report ({exc.__class__.__name__})"}
    failed, total, skipped = set(), 0, 0
    for case in root.iter("testcase"):
        total += 1
        cid = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(cid)
        elif case.find("skipped") is not None:
            skipped += 1
    passed = total - len(failed) - skipped
    if total == 0 or passed <= 0:
        return {**out, "ok": False, "why": "no test ran"}
    return {**out, "ok": True, "passed": passed, "failed": sorted(failed), "total": total}


def compare(py, base_dir, cand_dir, target, work, timeout, env_for, known=None) -> dict:
    """CAND against BASE; with no BASE (``base_dir`` None) against the ``known`` failure ids that
    CAND ships (docs/autocue-lab/known_test_failures.txt), with no pass-count comparison."""
    if base_dir is None:
        b = {"ok": True, "failed": sorted(known or ()), "passed": 0}
    else:
        b = run_suite(py, base_dir, target, env_for(base_dir, "base"), work / "base.xml", timeout)
    if not b["ok"]:
        return {"accept": False, "reason": f"installed version's {target} did not run: {b['why']}", "base": b}
    c = run_suite(py, cand_dir, target, env_for(cand_dir, "cand"), work / "cand.xml", timeout)
    if not c["ok"]:
        return {"accept": False, "reason": f"new version's {target} did not run: {c['why']}", "cand": c}
    new_fail = sorted(set(c["failed"]) - set(b["failed"]))
    if new_fail:  # a flake must fail twice; the whole suite again, so order-dependent failures count
        r = run_suite(py, cand_dir, target, env_for(cand_dir, "cand"), work / "rerun.xml", timeout)
        if r.get("ok"):
            new_fail = [x for x in new_fail if x in set(r["failed"])]
    if new_fail:
        what = "that pass now" if base_dir is not None else "beyond its known failures"
        return {"accept": False, "reason": f"new version fails {len(new_fail)} test(s) in {target} {what}",
                "new_failures": new_fail[:30]}
    if c["passed"] < 0.9 * b["passed"]:
        return {"accept": False, "reason": f"new version passes only {c['passed']} tests in {target} (installed: {b['passed']})"}
    return {"accept": True, "reason": f"{target}: {c['passed']} passed, no new failures", "base_passed": b["passed"],
            "cand_passed": c["passed"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    ap.add_argument("--base", default=None, help="installed version's tree; none: compare with --known")
    ap.add_argument("--known", default=None, help="known failure ids (used without --base)")
    ap.add_argument("--cand", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--house", default=None)
    ap.add_argument("--timeout", type=float, default=1200)
    a = ap.parse_args(argv)
    work = Path(a.work)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    cand = Path(a.cand).resolve()
    base = Path(a.base).resolve() if a.base else None
    known = set()
    if base is None:
        kfile = Path(a.known) if a.known else cand / "docs" / "autocue-lab" / "known_test_failures.txt"
        if kfile.is_file():
            known = {l.strip() for l in kfile.read_text().splitlines() if l.strip() and not l.startswith("#")}
    probe = subprocess.run([a.python, "-m", "pytest", "--version"], capture_output=True, text=True)
    if probe.returncode != 0:
        print(json.dumps({"accept": False, "reason": "pytest is not installed in " + a.python, "no_pytest": True}))
        return 0
    results = []
    res = compare(a.python, base, cand, "tests", work, a.timeout,
                  lambda root, tag: _env(work / tag, root), known)
    results.append(res)
    house = Path(a.house).expanduser() if a.house else None
    if res["accept"] and a.house is not None and not a.house.strip():
        res = {"accept": False, "reason": "--house was given empty (no House checkout recorded)"}
        results.append(res)
    elif res["accept"] and house and not (house / "ui" / "tests").is_dir():
        res = {"accept": False, "reason": f"the House sorter's tests are missing ({house / 'ui' / 'tests'})"}
        results.append(res)
    elif res["accept"] and house:
        # as start_8788_write.sh: PYTHONPATH=$VDJ_AUTOCUE_ROOT:$FORK_ROOT, cwd = the House checkout
        def house_env(_root, tag):
            autocue = base if tag == "base" else cand
            return _env(work / ("h" + tag), autocue, [house, house / "ui"], autocue_root=autocue)

        res = compare(a.python, house if base is not None else None, house, "ui/tests", work, a.timeout,
                      house_env, known)
        results.append(res)
    final = results[-1] if not results[-1]["accept"] else {
        "accept": True, "reason": "; ".join(r["reason"] for r in results)}
    final["checks"] = len(results)
    print(json.dumps(final))
    return 0


if __name__ == "__main__":
    sys.exit(main())
