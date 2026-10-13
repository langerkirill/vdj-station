#!/usr/bin/env python3
"""GitHub Releases for the AutoCue auto-updater (standard library only).

  autocue_github.py check                  exit 0 when the releases can be read (the login works)
  autocue_github.py list                   "<version> <release id>" for every published release
                                           v<version> that has all its assets
  autocue_github.py download VERSION DIR   the wheel, the test bundle and SHA256SUMS of v<VERSION>
                                           into DIR, checked against SHA256SUMS; prints the
                                           sha256 of SHA256SUMS (the whole release: the updater
                                           records it and ignores a version that ever changes)

Each release (built by .github/workflows/release.yml from the tag) carries:
  vdj_autocue-<version>-py3-none-any.whl   the package
  vdj-autocue-<version>-tests.tar.gz       its tests, test data and updater scripts (the Mac
                                           gate runs them against the installed package)
  SHA256SUMS

Login, first that works: AUTOCUE_GITHUB_TOKEN; `gh auth token`; the GitHub login git keeps
(`git credential fill`). The token goes only into the request header: never on a command line,
never into another process's environment, never to the download host GitHub redirects to.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = os.environ.get("AUTOCUE_GITHUB_REPO", "langerkirill/vdj-autocue-lab")
API = os.environ.get("AUTOCUE_GITHUB_API", "https://api.github.com").rstrip("/")
TIMEOUT = 60


def asset_names(version: str) -> list[str]:
    return [f"vdj_autocue-{version}-py3-none-any.whl", f"vdj-autocue-{version}-tests.tar.gz", "SHA256SUMS"]


def _token() -> str:
    tok = os.environ.get("AUTOCUE_GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    # never a prompt (this runs unattended, maybe during a gig): no terminal, no askpass helper,
    # no Git Credential Manager window
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GIT_ASKPASS": "",
           "SSH_ASKPASS": "", "GCM_INTERACTIVE": "never"}
    try:
        out = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"], capture_output=True,
                             text=True, timeout=20, env=env)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        out = subprocess.run(["git", "-c", "core.askPass=", "-c", "credential.interactive=never",
                              "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                             capture_output=True, text=True, timeout=20, env=env)
        for line in out.stdout.splitlines():
            if line.startswith("password=") and len(line) > 9:
                return line.split("=", 1)[1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


class _DropAuthOnOtherHost(urllib.request.HTTPRedirectHandler):
    """Asset downloads redirect to a storage host: never send the GitHub token there."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_DropAuthOnOtherHost)
_TOKEN: str | None = None


def _request(url: str, accept: str):
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _token()
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "vdj-autocue-updater",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if _TOKEN:
        req.add_header("Authorization", f"Bearer {_TOKEN}")
    return _OPENER.open(req, timeout=TIMEOUT)


def _json(path: str):
    with _request(f"{API}{path}", "application/vnd.github+json") as resp:
        return json.load(resp)


def _releases() -> list[dict]:
    out = []
    for page in (1, 2, 3):
        batch = _json(f"/repos/{REPO}/releases?per_page=100&page={page}")
        out += batch
        if len(batch) < 100:
            break
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_list() -> int:
    for rel in _releases():
        tag = str(rel.get("tag_name") or "")
        if rel.get("draft") or not tag.startswith("v"):
            continue
        version = tag[1:]
        have = {a.get("name") for a in rel.get("assets") or []}
        if all(n in have for n in asset_names(version)):
            print(version, rel.get("id"))
    return 0


def cmd_download(version: str, dest: Path) -> int:
    rel = _json(f"/repos/{REPO}/releases/tags/v{urllib.parse.quote(version)}")
    assets = {a.get("name"): a for a in rel.get("assets") or []}
    names = asset_names(version)
    missing = [n for n in names if n not in assets]
    if missing:
        print(f"release v{version} lacks {', '.join(missing)}", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        part = dest / f".{name}.part"
        with _request(assets[name]["url"], "application/octet-stream") as resp, open(part, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        size = assets[name].get("size")
        got = part.stat().st_size
        if isinstance(size, int) and got != size:
            part.unlink(missing_ok=True)
            print(f"{name}: download cut off ({got} of {size} bytes)", file=sys.stderr)
            return 1
        os.replace(part, dest / name)
    sums = {}
    for line in (dest / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    for name in names[:2]:
        if sums.get(name) != _sha256(dest / name):
            print(f"{name}: checksum does not match SHA256SUMS", file=sys.stderr)
            for n in names:
                (dest / n).unlink(missing_ok=True)
            return 1
    print(_sha256(dest / "SHA256SUMS"))
    return 0


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["check"]:
            _json(f"/repos/{REPO}/releases?per_page=1")
            return 0
        if argv[:1] == ["list"]:
            return cmd_list()
        if argv[:1] == ["download"] and len(argv) == 3:
            return cmd_download(argv[1], Path(argv[2]))
    except urllib.error.HTTPError as exc:
        hint = {401: " (not logged in to GitHub on this Mac: gh auth login)",
                403: " (refused: rate limit, or this login has no access to the repo)",
                404: " (not found: this login cannot see the private repo, or no such release)"}.get(exc.code, "")
        print(f"GitHub answered {exc.code}{hint}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        print(f"GitHub not reachable: {exc}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
