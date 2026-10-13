#!/usr/bin/env python3
"""Version decisions for the AutoCue auto-updater (standard library + packaging / pip's copy).

  autocue_versions.py pick --spec SPEC --current VERSION [--exclude FILE ...] < tags
      tags: lines "<version> <commit>" (from the git tags v<version>). Prints
      "<version>\t<commit>" of the version to install, or nothing. With the current version
      allowed by SPEC: the newest allowed version if it is newer. With the current version
      not allowed (the rule changed, or the first packaged install): the newest allowed one.
      Pre-releases only when SPEC names one. Versions listed in an --exclude file are skipped.
  autocue_versions.py check-spec SPEC        exit 0 when SPEC is a valid rule (prints it normalized)
  autocue_versions.py allows SPEC VERSION    exit 0 when SPEC allows VERSION
  autocue_versions.py older A B              exit 0 when version A is older than version B
  autocue_versions.py check-deps WHEEL       exit 1 and print what this Python lacks (Requires-Dist,
                                             Requires-Python); exit 0 when everything is met
SPEC is a pip-style rule for vdj-autocue: "vdj-autocue>=1.4.0", ">=1.4,<2", "~=1.4" ...
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from email.parser import Parser

try:
    from packaging.requirements import Requirement
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:  # every venv has pip, which carries its own copy
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.specifiers import InvalidSpecifier, SpecifierSet
    from pip._vendor.packaging.version import InvalidVersion, Version

NAME = "vdj-autocue"


def parse_spec(text: str) -> SpecifierSet:
    text = text.strip()
    if text[:1].isalpha():  # a requirement: only vdj-autocue, no extras, URL or marker
        req = Requirement(text)
        if req.name.lower().replace("_", "-") != NAME or req.extras or req.url or req.marker:
            raise ValueError(f"the rule must be for {NAME} only, e.g. '{NAME}>=1.4'")
        return req.specifier
    return SpecifierSet(text)


def parse_version(text: str):
    try:
        return Version(text.strip().lstrip("v"))
    except InvalidVersion:
        return None


def names_prerelease(spec: SpecifierSet) -> bool:
    for s in spec:
        v = parse_version(s.version.rstrip(".*"))
        if v is not None and v.is_prerelease:
            return True
    return False


def pick(spec: SpecifierSet, current: str, lines, excluded: set[str]):
    cur = parse_version(current)
    pre = names_prerelease(spec)  # explicit: packaging's default changed between versions
    allowed = []
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        v = parse_version(parts[0])
        if v is None or str(v) in excluded or parts[0] in excluded:
            continue
        if v.is_prerelease and not pre:
            continue
        if spec.contains(v, prereleases=True):
            allowed.append((v, parts[1]))
    if not allowed:
        return None
    best = max(allowed, key=lambda x: x[0])
    if cur is not None and spec.contains(cur, prereleases=True):
        return best if best[0] > cur else None
    return best if cur is None or best[0] != cur else None


def unmet_requirements(wheel: str) -> list[str]:
    from importlib.metadata import PackageNotFoundError, version as installed

    with zipfile.ZipFile(wheel) as z:
        meta_name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        meta = Parser().parsestr(z.read(meta_name).decode("utf-8"))
    missing = []
    py = meta.get("Requires-Python")
    if py and not SpecifierSet(py).contains(".".join(map(str, sys.version_info[:3])), prereleases=True):
        missing.append(f"Python {py} (this is {sys.version.split()[0]})")
    for raw in meta.get_all("Requires-Dist") or []:
        req = Requirement(raw)
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        try:
            have = installed(req.name)
        except PackageNotFoundError:
            missing.append(f"{req} (not installed)")
            continue
        if req.specifier and not req.specifier.contains(have, prereleases=True):
            missing.append(f"{req} (have {have})")
    return missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--spec", required=True)
    p.add_argument("--current", default="")
    p.add_argument("--exclude", action="append", default=[])
    c = sub.add_parser("check-spec")
    c.add_argument("spec")
    a = sub.add_parser("allows")
    a.add_argument("spec")
    a.add_argument("version")
    d = sub.add_parser("check-deps")
    d.add_argument("wheel")
    o = sub.add_parser("older")
    o.add_argument("a")
    o.add_argument("b")
    args = ap.parse_args(argv)

    if args.cmd == "check-spec":
        try:
            spec = parse_spec(args.spec)
        except (InvalidSpecifier, ValueError, Exception) as exc:
            print(f"not a valid rule: {exc}", file=sys.stderr)
            return 1
        print(f"{NAME}{spec}" if str(spec) else NAME)
        return 0
    if args.cmd == "allows":
        v = parse_version(args.version)
        return 0 if v is not None and parse_spec(args.spec).contains(v, prereleases=True) else 1
    if args.cmd == "older":
        va, vb = parse_version(args.a), parse_version(args.b)
        return 0 if va is not None and vb is not None and va < vb else 1
    if args.cmd == "check-deps":
        missing = unmet_requirements(args.wheel)
        for m in missing:
            print(m)
        return 1 if missing else 0
    excluded: set[str] = set()
    for path in args.exclude:
        try:
            with open(path, encoding="utf-8") as fh:
                excluded.update(x.strip().lstrip("v") for x in fh if x.strip())
        except OSError:
            pass
    best = pick(parse_spec(args.spec), args.current, sys.stdin.read().splitlines(), excluded)
    if best:
        print(f"{best[0]}\t{best[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
