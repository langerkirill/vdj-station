"""Scratch fixture + scratch server for the song-switch "marker bleed" Playwright tests.

SAFETY MODEL (nothing here can reach the real Library / real database.xml):
  * Everything lives under SCRATCH_ROOT (default ~/ms_bleed_scratch, never ~/Music or ~/Library).
  * The server subprocess runs with HOME=<scratch>/home, so every ``Path.home()`` based default
    (Music/DJ/..., Library/Application Support/VirtualDJ/...) resolves INSIDE the scratch dir, and
    MUSIC_SORTER_VDJ_DATABASE / NOTES_DIR / HOUSE_ROOT / backup dir are pinned explicitly as well.
  * ``verify_config()`` imports the app's own config in that environment and refuses to continue if
    any resolved path is outside the scratch dir, or inside the real home's Music / VirtualDJ dirs.
  * Port defaults to 8796; 8787 / 8788 are refused outright.

Run a server by hand:  python ui/tests/pw_bleed/scratch_env.py serve   (Ctrl-C to stop)
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
UI = REPO / "ui"
REAL_HOME = Path(os.path.realpath(os.path.expanduser("~")))
SCRATCH_ROOT = Path(os.environ.get("MS_BLEED_SCRATCH", str(REAL_HOME / "ms_bleed_scratch"))).resolve()
HOME = SCRATCH_ROOT / "home"
DB = HOME / "Library" / "Application Support" / "VirtualDJ" / "database.xml"
CRATE = "Sauna Fest House"
ADD_CUES = HOME / "Music" / "DJ" / "Music" / "Cues" / "Add Cues" / CRATE
NOTES = SCRATCH_ROOT / "notes"
BACKUPS = SCRATCH_ROOT / "backups"
HOUSE_ROOT = HOME / "Music" / "DJ" / "Music" / "House"
SAUNA = SCRATCH_ROOT / "sauna"
PORT = int(os.environ.get("MS_BLEED_PORT", "8796"))
FORBIDDEN_PORTS = {8787, 8788}
BASE_URL = f"http://127.0.0.1:{PORT}"

# name -> (file stem, bpm, duration s, [(cue name, pos, color int)], [(loop name, pos, beats, color int)])
BLUE, GREEN, PURPLE, YELLOW, ORANGE = "4278190335", "4278255360", "4288020735", "4294967040", "4294934272"
SONGS = {
    "weiss": ("Weiss (UK) - Feel My Needs", 127.0, 300.0,
              [("Beat Entry", 0.0, BLUE), ("Feel my needs", 23.605, GREEN), ("Groove", 149.509, PURPLE), ("Drop", 55.081, YELLOW)],
              [("FMNL", 23.605, 8, GREEN)]),
    "roosevelt": ("Roosevelt - Fixture Sea", 124.0, 320.0,
                  [("Roos Intro", 8.0, BLUE), ("Roos Verse", 48.2, GREEN), ("Roos Break", 101.7, ORANGE)],
                  [("Roos Loop", 80.0, 16, PURPLE)]),
    "secretly": ("Secretly Hells - Fixture Night", 130.0, 330.0,
                 [("Hells Start", 16.0, YELLOW), ("Hells Peak", 72.5, PURPLE), ("Hells Out", 250.0, BLUE), ("Hells Extra", 180.0, GREEN)],
                 [("Hells Loop", 130.0, 8, ORANGE)]),
    "kaiser": ("045 Kaiserkraft", 133.0, 345.0,
               [("Kaiser In", 4.0, BLUE), ("Kaiser Lift", 345.008 - 120.0, GREEN), ("Kaiser Out", 300.0, PURPLE)],
               [("Kaiser Loop", 100.0, 8, YELLOW)]),
    "turm": ("Turmstrasse - Fixture Fourth", 141.0, 280.0,
             [("Turm A", 10.0, GREEN), ("Turm B", 90.0, BLUE)],
             [("Turm Loop", 150.0, 8, GREEN)]),
}


def path_of(key: str) -> str:
    return str(ADD_CUES / f"{SONGS[key][0]}.wav")


def _assert_scratch(p: Path) -> None:
    rp = Path(os.path.realpath(p))
    if not str(rp).startswith(str(SCRATCH_ROOT) + os.sep) and rp != SCRATCH_ROOT:
        raise AssertionError(f"refusing: {rp} is outside scratch root {SCRATCH_ROOT}")
    if SCRATCH_ROOT.name != "ms_bleed_scratch" and "MS_BLEED_SCRATCH" not in os.environ:
        raise AssertionError("unexpected scratch root")


def _write_wav(path: Path, seconds: float, bpm: float) -> None:
    """Tiny mono 4 kHz 8-bit click track (~1.2 MB per 5 min) so waveform/probe code has real audio."""
    rate = 4000
    n = int(seconds * rate)
    beat = int(rate * 60.0 / bpm)
    frames = bytearray()
    for i in range(n):
        v = 128
        if i % beat < 60:
            v = 128 + (90 if (i % beat) % 8 < 4 else -90)
        frames.append(v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _song_xml(key: str) -> str:
    stem, bpm, dur, cues, loops = SONGS[key]
    p = path_of(key)
    size = os.path.getsize(p) if os.path.exists(p) else 0
    lines = [
        f'<Song FilePath="{p}" FileSize="{size}">',
        f'  <Tags Author="{stem.split(" - ")[0]}" Title="{stem.split(" - ")[-1]}" Bpm="{60.0 / bpm:.6f}" />',
        f'  <Infos SongLength="{dur:.6f}" FirstSeen="1790000000" />',
        f'  <Scan Version="801" Bpm="{60.0 / bpm:.6f}" AltBpm="{30.0 / bpm:.6f}" Volume="0.9" Key="Am" Flag="32" />',
        '  <Poi Pos="0.100000" Type="beatgrid" />',
    ]
    for i, (name, pos, col) in enumerate(cues, start=1):
        pos_attr = "" if pos == 0.0 else f' Pos="{pos:.6f}"'
        lines.append(f'  <Poi Name="{name}"{pos_attr} Num="{i}" Color="{col}" Type="cue" />')
    for j, (name, pos, beats, col) in enumerate(loops, start=1):
        lines.append(f'  <Poi Name="{name}" Pos="{pos:.6f}" Num="-1" Color="{col}" Type="loop" Size="{float(beats)}" Slot="{j}" />')
    lines.append("</Song>")
    return "\r\n".join(lines)


def build_fixture(keys=None) -> None:
    """(Re)create the scratch library: dummy audio + a synthesized VirtualDJ database.xml."""
    _assert_scratch(SCRATCH_ROOT)
    if SCRATCH_ROOT.exists():
        shutil.rmtree(SCRATCH_ROOT)
    for d in (ADD_CUES, DB.parent, NOTES, BACKUPS, HOUSE_ROOT, SAUNA):
        d.mkdir(parents=True, exist_ok=True)
    keys = list(keys or SONGS)
    for k in keys:
        stem, bpm, dur, _c, _l = SONGS[k]
        _write_wav(ADD_CUES / f"{stem}.wav", dur, bpm)
    body = "\r\n".join(_song_xml(k) for k in keys)
    DB.write_bytes(
        ('<?xml version="1.0" encoding="UTF-8"?>\r\n<VirtualDJ_Database Version="2025">\r\n' + body + "\r\n</VirtualDJ_Database>\r\n").encode("utf-8")
    )
    (SCRATCH_ROOT / "README.txt").write_text("Scratch fixture for ms_bleed tests. Safe to delete.\n")


def server_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MUSIC_SORTER_", "VDJ_"))}
    env.update(
        HOME=str(HOME),
        PYTHONPATH=str(REPO),
        PYTHONUNBUFFERED="1",
        MUSIC_SORTER_PROFILE="house",
        MUSIC_SORTER_VDJ_DATABASE=str(DB),
        MUSIC_SORTER_NOTES_DIR=str(NOTES),
        MUSIC_SORTER_WRITE_BACKUP_DIR=str(BACKUPS),
        MUSIC_SORTER_HOUSE_ROOT=str(HOUSE_ROOT),
        MUSIC_SORTER_SAUNA_FEST_DIR=str(SAUNA),
        # a real VirtualDJ may be open on the Mac; these only affect the SCRATCH database
        VDJ_ALLOW_RUNNING_WRITES="1",
        MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S="0",
        MUSIC_SORTER_VDJDEV_BACKUP_WINDOW_S="0",
        MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S="0",
        MUSIC_SORTER_AUTOCUE_CONCURRENCY="1",
        OMP_NUM_THREADS="1",
        GEMINI_API_KEY="",  # scratch server never calls Gemini
        OPENAI_API_KEY="",
    )
    return env


CONFIG_PROBE = r"""
import json, sys
sys.path.insert(0, %r); sys.path.insert(0, %r)
from sorter import config as c
out = {k: str(getattr(c, k)) for k in ("VDJ_DATABASE","MUSIC_ROOT","DJ_ROOT","CUES_ROOT","ADD_CUES","READY_FOR_SORT","HOUSE_ROOT","DJ_NOTES_ROOT","VDJ_HISTORY_DIR","VDJ_CACHE_DB","MIXES_ROOT","SETS_ROOT","SAUNA_FEST_SET_DIR","TRANSITIONS_DB_PATH")}
print(json.dumps(out))
"""


def verify_config() -> dict[str, str]:
    """Import the app's config under the scratch env; fail unless EVERY path is inside scratch."""
    res = subprocess.run([sys.executable, "-c", CONFIG_PROBE % (str(UI), str(REPO))], env=server_env(), capture_output=True, text=True, cwd=str(UI))
    if res.returncode != 0:
        raise RuntimeError(f"config probe failed: {res.stderr[-800:]}")
    import json

    cfg = json.loads(res.stdout.strip().splitlines()[-1])
    real_bad = [str(REAL_HOME / "Music"), str(REAL_HOME / "Library")]
    for k, v in cfg.items():
        rv = os.path.realpath(v)
        if not (rv == str(SCRATCH_ROOT) or rv.startswith(str(SCRATCH_ROOT) + os.sep)):
            raise AssertionError(f"config {k}={v} is OUTSIDE the scratch root {SCRATCH_ROOT}")
        if any(rv == b or rv.startswith(b + os.sep) for b in real_bad):
            raise AssertionError(f"config {k}={v} resolves into the real Library")
    return cfg


def port_busy(port: int = PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server(log_path: Path | None = None) -> subprocess.Popen:
    if PORT in FORBIDDEN_PORTS:
        raise AssertionError("refusing to use port 8787/8788")
    if port_busy():
        raise RuntimeError(f"port {PORT} already in use (not by us) - set MS_BLEED_PORT to another scratch port")
    verify_config()
    log = open(log_path or (SCRATCH_ROOT / "server.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(UI), env=server_env(), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("scratch server exited early; see server.log")
        try:
            with urllib.request.urlopen(BASE_URL + "/api/health", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.4)
    else:
        stop_server(proc)
        raise RuntimeError("scratch server did not come up")
    import json

    health = json.loads(urllib.request.urlopen(BASE_URL + "/api/health", timeout=5).read())
    if os.path.realpath(health["vdj_database"]) != os.path.realpath(DB):
        stop_server(proc)
        raise AssertionError(f"server reports database {health['vdj_database']}, expected scratch {DB}")
    return proc


def stop_server(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=8)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


# ---- read-back helpers (parse the scratch database.xml directly) ----------------------------
def db_markers() -> dict[str, dict]:
    """FilePath -> {'cues': [(name,pos,color)], 'loops': [(name,pos,size,color)]} straight from database.xml."""
    root = ET.fromstring(DB.read_bytes())
    out: dict[str, dict] = {}
    for song in root.findall("Song"):
        cues, loops = [], []
        for poi in song.findall("Poi"):
            t = (poi.get("Type") or "").lower()
            if t == "cue" and poi.get("Num", "0") != "0":
                cues.append((poi.get("Name") or "", round(float(poi.get("Pos") or 0.0), 3), poi.get("Color") or ""))
            elif t == "loop":
                loops.append((poi.get("Name") or "", round(float(poi.get("Pos") or 0.0), 3), float(poi.get("Size") or 0), poi.get("Color") or ""))
        out[song.get("FilePath")] = {"cues": sorted(cues, key=lambda x: x[1]), "loops": sorted(loops, key=lambda x: x[1])}
    return out


def db_signature() -> bytes:
    return DB.read_bytes()


def reset_db() -> None:
    """Rewrite the scratch database.xml to the pristine fixture (server re-reads it on every request)."""
    _assert_scratch(DB)
    body = "\r\n".join(_song_xml(k) for k in SONGS)
    DB.write_bytes(
        ('<?xml version="1.0" encoding="UTF-8"?>\r\n<VirtualDJ_Database Version="2025">\r\n' + body + "\r\n</VirtualDJ_Database>\r\n").encode("utf-8")
    )
    for f in DB.parent.glob("database.xml.backup.*"):
        f.unlink()
    for d in (BACKUPS,):
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "verify":
        build_fixture()
        print(__import__("json").dumps(verify_config(), indent=2))
    elif cmd == "serve":
        build_fixture()
        p = start_server()
        print(f"scratch server pid={p.pid} {BASE_URL}  db={DB}", flush=True)
        try:
            p.wait()
        except KeyboardInterrupt:
            pass
        finally:
            stop_server(p)
    else:
        raise SystemExit("usage: scratch_env.py [verify|serve]")
