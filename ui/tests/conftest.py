"""House-fork test harness.

VirtualDJ is usually open while these run, and the production guard refuses
database.xml writes then. The tests only ever write temp databases, so we set
the documented emergency override for the TEST PROCESS ONLY — and install a
deny-list so that if any test ever tried to write into the real VirtualDJ
folder or the real ~/Music library, it fails loudly instead of writing.
"""

from __future__ import annotations

import builtins
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "ui")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("VDJ_ALLOW_RUNNING_WRITES", "1")
# Legacy fixtures are LF-only. The CRLF hard gate is exercised explicitly in
# test_crlf_hard_gate.py (which clears this flag); production never sets it.
os.environ.setdefault("MUSIC_SORTER_ALLOW_LF_DB", "1")
# safe_write guards are exercised explicitly in test_safe_write.py; disable by default
os.environ.setdefault("MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S", "0")
os.environ.setdefault("MUSIC_SORTER_VDJDEV_BACKUP_WINDOW_S", "0")
os.environ.setdefault("MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S", "0")  # retry has explicit tests
os.environ.pop("MUSIC_SORTER_READONLY", None)  # readonly has its own tests
# Tests must never touch the real (or the running fork's) Notes dir / action log.
import tempfile as _tempfile  # noqa: E402

os.environ["MUSIC_SORTER_NOTES_DIR"] = _tempfile.mkdtemp(prefix="ms-house-test-notes-")
os.environ["MUSIC_SORTER_WRITE_BACKUP_DIR"] = _tempfile.mkdtemp(prefix="ms-house-test-backups-")

_PROTECTED = [
    os.path.realpath(os.path.expanduser("~/Library/Application Support/VirtualDJ")),
    os.path.realpath(os.path.expanduser("~/Music")),
]


def _protected(path) -> bool:
    try:
        real = os.path.realpath(os.fspath(path))
    except Exception:
        return False
    return any(real == r or real.startswith(r + os.sep) for r in _PROTECTED)


def _deny(op, path):
    raise AssertionError(f"TEST SAFETY: {op} would touch the real library/VDJ: {path}")


_orig_open = builtins.open


def _open(file, mode="r", *a, **k):
    if isinstance(file, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+"):
        if _protected(file):
            _deny("open(write)", file)
    return _orig_open(file, mode, *a, **k)


builtins.open = _open
import io  # noqa: E402

io.open = _open

for _mod, _name, _idx in (
    (os, "replace", 1),
    (os, "rename", 1),
    (os, "remove", 0),
    (os, "unlink", 0),
    (shutil, "move", 1),
    (shutil, "copy2", 1),
    (shutil, "copy", 1),
    (shutil, "copyfile", 1),
    (shutil, "rmtree", 0),
):
    _orig = getattr(_mod, _name)

    def _wrap(orig=_orig, name=_name, idx=_idx):
        def inner(*args, **kwargs):
            if len(args) > idx and isinstance(args[idx], (str, bytes, os.PathLike)):
                if _protected(args[idx]):
                    _deny(name, args[idx])
            return orig(*args, **kwargs)

        return inner

    setattr(_mod, _name, _wrap())
