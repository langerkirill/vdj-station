"""Process-wide filesystem write guard for MUSIC_SORTER_READONLY=1 (House fork).

Belt-and-braces on top of the explicit read-only checks: when the process is
read-only, any attempt to create/modify/delete/move a file outside a tiny
allow-list (the isolated Notes dir, temp dirs, OS caches) raises
``ReadOnlyError`` — even if some code path forgot to check the flag.

Install once, early (``install()`` is idempotent and a no-op when not read-only).
"""

from __future__ import annotations

import builtins
import io
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import profile as _profile

_INSTALLED = False
_ALLOWED: list[str] = []
_ORIG: dict[str, Any] = {}
BLOCKED_LOG: list[str] = []  # last few blocked attempts (diagnostics)


def _real(p: Any) -> str:
    try:
        return os.path.realpath(os.fspath(p))
    except Exception:
        return str(p)


def allowed_roots() -> list[str]:
    roots = {
        "/tmp",
        "/private/tmp",
        "/var/folders",
        "/private/var/folders",
        "/dev",
        os.path.realpath(tempfile.gettempdir()),
        os.path.realpath(os.path.expanduser("~/Library/Caches")),
        os.path.realpath(os.path.expanduser("~/.cache")),
    }
    notes = os.environ.get("MUSIC_SORTER_NOTES_DIR", "").strip()
    if notes:
        roots.add(os.path.realpath(os.path.expanduser(notes)))
    for extra in os.environ.get("MUSIC_SORTER_FS_ALLOW", "").split(os.pathsep):
        if extra.strip():
            roots.add(os.path.realpath(os.path.expanduser(extra.strip())))
    return sorted(roots)


def is_allowed(path: Any) -> bool:
    real = _real(path)
    for root in _ALLOWED or allowed_roots():
        if real == root or real.startswith(root.rstrip("/") + "/"):
            return True
    return False


def _deny(op: str, *paths: Any) -> None:
    shown = ", ".join(str(p) for p in paths)
    msg = f"{op}: {shown}"
    BLOCKED_LOG.append(msg)
    del BLOCKED_LOG[:-50]
    raise _profile.ReadOnlyError(f"{_profile.READONLY_MESSAGE} Blocked {msg}")


def _check(op: str, *paths: Any) -> None:
    if not _profile.readonly():
        return
    for p in paths:
        if isinstance(p, int):  # fd
            continue
        if not is_allowed(p):
            _deny(op, p)


def _wrap_path_fn(mod: Any, name: str, n_paths: int = 1, op: str | None = None) -> None:
    orig = getattr(mod, name, None)
    if orig is None:
        return
    _ORIG[f"{mod.__name__}.{name}"] = orig

    def guarded(*args: Any, **kwargs: Any):
        _check(op or f"{mod.__name__}.{name}", *args[:n_paths])
        return orig(*args, **kwargs)

    guarded.__name__ = name
    setattr(mod, name, guarded)


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _guarded_open(orig):
    def _open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and _profile.readonly():
            m = mode if isinstance(mode, str) else "r"
            if any(c in m for c in "wax+"):
                _check("open(%s)" % m, file)
        return orig(file, mode, *args, **kwargs)

    return _open


def _guarded_os_open(orig):
    def _os_open(path, flags, *args, **kwargs):
        if _profile.readonly() and (flags & _WRITE_FLAGS) and isinstance(path, (str, bytes, os.PathLike)):
            _check("os.open(write)", path)
        return orig(path, flags, *args, **kwargs)

    return _os_open


def install() -> bool:
    """Patch the stdlib. Returns True when the guard is active."""
    global _INSTALLED, _ALLOWED
    if _INSTALLED:
        return True
    if not _profile.readonly():
        return False
    _ALLOWED = allowed_roots()

    def dst_only(mod: Any, name: str) -> None:
        orig = getattr(mod, name)
        _ORIG[f"{mod.__name__}.{name}"] = orig

        def guarded(src, dst, *a, **k):
            _check(f"{mod.__name__}.{name}", dst)
            return orig(src, dst, *a, **k)

        guarded.__name__ = name
        setattr(mod, name, guarded)

    for name in ("copy2", "copy", "copyfile", "copytree", "copymode", "copystat"):
        dst_only(shutil, name)
    dst_only(os, "link")
    dst_only(os, "symlink")

    orig_move = shutil.move
    _ORIG["shutil.move"] = orig_move

    def guarded_move(src, dst, *a, **k):
        _check("shutil.move(src)", src)
        _check("shutil.move(dst)", dst)
        return orig_move(src, dst, *a, **k)

    shutil.move = guarded_move
    _wrap_path_fn(shutil, "rmtree", 1)
    for name in ("rename", "replace"):
        _wrap_path_fn(os, name, 2)
    for name in ("remove", "unlink", "rmdir", "truncate", "chmod", "utime", "removedirs"):
        _wrap_path_fn(os, name, 1)

    orig_mkdir, orig_makedirs = os.mkdir, os.makedirs
    _ORIG["os.mkdir"], _ORIG["os.makedirs"] = orig_mkdir, orig_makedirs

    def guarded_mkdir(path, *a, **k):
        if not (isinstance(path, (str, bytes, os.PathLike)) and os.path.exists(path)):
            _check("os.mkdir", path)
        return orig_mkdir(path, *a, **k)

    def guarded_makedirs(name, mode=0o777, exist_ok=False):
        if exist_ok and os.path.isdir(name):
            return None  # no-op
        _check("os.makedirs", name)
        return orig_makedirs(name, mode, exist_ok)

    os.mkdir, os.makedirs = guarded_mkdir, guarded_makedirs

    _ORIG["builtins.open"] = builtins.open
    builtins.open = _guarded_open(builtins.open)
    io.open = builtins.open
    _ORIG["os.open"] = os.open
    os.open = _guarded_os_open(os.open)
    _INSTALLED = True
    return True


def status() -> dict[str, Any]:
    return {
        "installed": _INSTALLED,
        "readonly": _profile.readonly(),
        "allowed_roots": list(_ALLOWED),
        "blocked_recent": list(BLOCKED_LOG[-10:]),
    }
