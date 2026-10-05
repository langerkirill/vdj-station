"""Stem vocal-hole audit jobs for the Music Sorter Stems tab."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter.autocue_path import ensure_autocue_on_path

ensure_autocue_on_path()

from vdj_cuer.stem_vocal_holes import StemVocalAudit, VocalHole

from sorter import stem_audit as sa


def _touch_pair(root: Path, rel: str) -> Path:
    audio = root / rel
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    Path(f"{audio}.vdjstems").write_bytes(b"stems")
    return audio


def _audit_for(path: Path) -> StemVocalAudit:
    name = path.name.lower()
    report = StemVocalAudit(
        audio_path=str(path),
        stems_path=f"{path}.vdjstems",
        duration=180.0,
    )
    if "broken" in name:
        report.holes = [VocalHole(start=12.0, end=24.5)]
    elif "err" in name:
        report.error = "ffmpeg boom"
    return report


class StemAuditJobTests(unittest.TestCase):
    def setUp(self) -> None:
        sa._jobs.clear()
        sa._cancel.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self._snap = Path(self._tmp.name) / "stem_vocal_audit.json"
        self._snap_patch = patch.object(sa, "SNAPSHOT_PATH", self._snap)
        self._snap_patch.start()

    def tearDown(self) -> None:
        self._snap_patch.stop()
        self._tmp.cleanup()
        sa._jobs.clear()
        sa._cancel.clear()

    def test_inventory_counts_sidecars_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch_pair(root, "Zouk/a.flac")
            _touch_pair(root, "House/b.mp3")
            (root / "no-stems.flac").write_bytes(b"x")
            inv = sa.stem_inventory(root)
        self.assertEqual(inv["sidecar_count"], 2)
        self.assertEqual(inv["unscanned_count"], 2)
        self.assertEqual(inv["root"], str(root.resolve()))

    def test_scan_job_records_broken_ok_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch_pair(root, "ok.flac")
            _touch_pair(root, "broken.flac")
            _touch_pair(root, "err.flac")
            notes = Path(tmp) / "notes.json"
            with (
                patch.object(sa, "DJ_NOTES_ROOT", Path(tmp)),
                patch.object(sa, "SNAPSHOT_PATH", notes),
                patch.object(sa, "audit_stem_file", side_effect=_audit_for),
            ):
                job = sa.start_stem_audit_job(scope="all", root=root, workers=1)
                finished = _wait_job(job["id"])
            self.assertEqual(finished["status"], "ok")
            self.assertEqual(finished["checked"], 3)
            self.assertEqual(finished["broken_count"], 1)
            self.assertEqual(finished["ok_count"], 1)
            self.assertEqual(finished["error_count"], 1)
            self.assertEqual(len(finished["broken"]), 1)
            self.assertIn("broken.flac", finished["broken"][0]["audio_path"])
            self.assertAlmostEqual(finished["broken"][0]["hole_seconds"], 12.5)
            self.assertTrue(notes.is_file())
            saved = json.loads(notes.read_text(encoding="utf-8"))
            self.assertEqual(saved["broken"], 1)
            self.assertEqual(saved["job"]["status"], "ok")
            self.assertNotEqual(saved["job"]["status"], "running")

    def test_second_scan_skips_unchanged_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch_pair(root, "ok.flac")
            _touch_pair(root, "broken.flac")
            notes = Path(tmp) / "notes.json"
            seen: list[str] = []

            def capture(path: Path) -> StemVocalAudit:
                seen.append(Path(path).name)
                return _audit_for(Path(path))

            with (
                patch.object(sa, "DJ_NOTES_ROOT", Path(tmp)),
                patch.object(sa, "SNAPSHOT_PATH", notes),
                patch.object(sa, "audit_stem_file", side_effect=capture),
            ):
                first = sa.start_stem_audit_job(scope="all", root=root)
                _wait_job(first["id"])
                seen.clear()
                _touch_pair(root, "fresh.flac")
                second = sa.start_stem_audit_job(scope="all", root=root)
                finished = _wait_job(second["id"])
        self.assertEqual(seen, ["fresh.flac"])
        self.assertEqual(finished["checked"], 1)
        self.assertEqual(finished["skipped"], 2)
        self.assertEqual(finished["broken_count"], 1)
        self.assertEqual(finished["ok_count"], 2)

    def test_changed_sidecar_is_scanned_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _touch_pair(root, "ok.flac")
            notes = Path(tmp) / "notes.json"
            seen: list[str] = []

            def capture(path: Path) -> StemVocalAudit:
                seen.append(Path(path).name)
                return _audit_for(Path(path))

            with (
                patch.object(sa, "DJ_NOTES_ROOT", Path(tmp)),
                patch.object(sa, "SNAPSHOT_PATH", notes),
                patch.object(sa, "audit_stem_file", side_effect=capture),
            ):
                first = sa.start_stem_audit_job(scope="all", root=root)
                _wait_job(first["id"])
                seen.clear()
                sidecar = Path(f"{audio}.vdjstems")
                sidecar.write_bytes(b"stems-rewritten")
                second = sa.start_stem_audit_job(scope="all", root=root)
                _wait_job(second["id"])
        self.assertEqual(seen, ["ok.flac"])

    def test_rescan_all_does_not_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch_pair(root, "ok.flac")
            notes = Path(tmp) / "notes.json"
            seen: list[str] = []

            def capture(path: Path) -> StemVocalAudit:
                seen.append(Path(path).name)
                return _audit_for(Path(path))

            with (
                patch.object(sa, "DJ_NOTES_ROOT", Path(tmp)),
                patch.object(sa, "SNAPSHOT_PATH", notes),
                patch.object(sa, "audit_stem_file", side_effect=capture),
            ):
                first = sa.start_stem_audit_job(scope="all", root=root)
                _wait_job(first["id"])
                seen.clear()
                second = sa.start_stem_audit_job(
                    scope="all", root=root, skip_scanned=False
                )
                _wait_job(second["id"])
        self.assertEqual(seen, ["ok.flac"])

    def test_second_start_returns_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch_pair(root, "slow.flac")

            def slow(path: Path) -> StemVocalAudit:
                time.sleep(0.4)
                return _audit_for(Path(path))

            with patch.object(sa, "audit_stem_file", side_effect=slow):
                first = sa.start_stem_audit_job(scope="all", root=root, workers=1)
                second = sa.start_stem_audit_job(scope="all", root=root, workers=1)
                self.assertEqual(first["id"], second["id"])
                sa.cancel_stem_audit_job(first["id"])
                _wait_job(first["id"], timeout=3.0)

    def test_cancel_stops_remaining_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(6):
                _touch_pair(root, f"t{i}.flac")
            started = {"n": 0}

            def slow(path: Path) -> StemVocalAudit:
                started["n"] += 1
                time.sleep(0.15)
                return _audit_for(Path(path))

            with patch.object(sa, "audit_stem_file", side_effect=slow):
                job = sa.start_stem_audit_job(scope="all", root=root, workers=1)
                time.sleep(0.2)
                sa.cancel_stem_audit_job(job["id"])
                finished = _wait_job(job["id"], timeout=3.0)
        self.assertEqual(finished["status"], "cancelled")
        self.assertLess(finished["checked"], 6)

    def test_cued_scope_uses_database_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cued = _touch_pair(root, "cued.flac")
            _touch_pair(root, "uncued.flac")
            db = Path(tmp) / "database.xml"
            db.write_text(
                "<VirtualDJ_Database>\n"
                f'<Song FilePath="{cued}">\n'
                '  <Poi Pos="1" Num="1" Type="cue" />\n'
                "</Song>\n"
                f'<Song FilePath="{root / "uncued.flac"}">\n'
                "</Song>\n"
                "</VirtualDJ_Database>\n",
                encoding="utf-8",
            )
            seen: list[str] = []

            def capture(path: Path) -> StemVocalAudit:
                seen.append(Path(path).name)
                return _audit_for(Path(path))

            with (
                patch.object(sa, "VDJ_DATABASE", db),
                patch.object(sa, "audit_stem_file", side_effect=capture),
            ):
                job = sa.start_stem_audit_job(scope="cued", root=root, workers=1)
                finished = _wait_job(job["id"])
        self.assertEqual(finished["status"], "ok")
        self.assertEqual(seen, ["cued.flac"])

    def test_check_one_returns_auditor_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = _touch_pair(Path(tmp), "broken.flac")
            with (
                patch.object(sa, "MUSIC_ROOT", Path(tmp)),
                patch.object(sa, "audit_stem_file", side_effect=_audit_for),
            ):
                payload = sa.check_one_stem(str(audio))
        self.assertTrue(payload["broken"])
        self.assertEqual(payload["hole_seconds"], 12.5)

    def test_delete_removes_sidecar_not_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _touch_pair(root, "House/broken.flac")
            sidecar = Path(f"{audio}.vdjstems")
            with patch.object(sa, "MUSIC_ROOT", root):
                result = sa.delete_stem_sidecars([str(audio)])
            self.assertEqual(result["deleted"], 1)
            self.assertFalse(sidecar.exists())
            self.assertTrue(audio.is_file())

    def test_delete_rejects_audio_without_sidecar_name_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "song.flac"
            audio.write_bytes(b"keep")
            with patch.object(sa, "MUSIC_ROOT", root):
                with self.assertRaises(ValueError):
                    sa.delete_stem_sidecars([str(audio)])
            self.assertTrue(audio.is_file())

    def test_delete_rejects_path_outside_music_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "music"
            root.mkdir()
            outsider = Path(tmp) / "secret.flac.vdjstems"
            outsider.write_bytes(b"x")
            with patch.object(sa, "MUSIC_ROOT", root):
                with self.assertRaises(ValueError):
                    sa.delete_stem_sidecars([str(outsider)])
            self.assertTrue(outsider.exists())

    def test_unknown_job_is_none(self) -> None:
        self.assertIsNone(sa.get_stem_audit_job("nope"))

    def test_orphaned_running_snapshot_is_not_busy(self) -> None:
        self._snap.write_text(
            json.dumps(
                {
                    "generated_at": "2026-08-30T00:00:00+00:00",
                    "checked": 3,
                    "broken": 1,
                    "errors": 0,
                    "job": {
                        "id": "deadjob",
                        "status": "running",
                        "checked": 3,
                        "broken_count": 1,
                        "ok_count": 2,
                        "error_count": 0,
                        "broken": [
                            {
                                "audio_path": "/music/broken.flac",
                                "stems_path": "/music/broken.flac.vdjstems",
                                "broken": True,
                            }
                        ],
                        "errors": [],
                        "message": "2/3 · broken.flac",
                    },
                }
            ),
            encoding="utf-8",
        )
        latest = sa.latest_stem_audit_job()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["status"], "ok")
        self.assertEqual(latest["broken_count"], 1)

    def test_delete_drops_sidecar_from_saved_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = _touch_pair(root, "House/broken.flac")
            sidecar = Path(f"{audio}.vdjstems")
            job = sa.StemAuditJob(
                id="live",
                status="ok",
                scope="all",
                root=str(root),
                checked=1,
                broken_count=1,
                broken=[
                    {
                        "audio_path": str(audio),
                        "stems_path": str(sidecar),
                        "broken": True,
                        "hole_seconds": 12.5,
                    }
                ],
            )
            sa._jobs[job.id] = job
            with patch.object(sa, "MUSIC_ROOT", root):
                sa.delete_stem_sidecars([str(audio)])
            latest = sa.latest_stem_audit_job()
            self.assertEqual(latest["broken_count"], 0)
            self.assertEqual(latest["broken"], [])
            saved = json.loads(self._snap.read_text(encoding="utf-8"))
            self.assertEqual(saved["broken"], 0)
            self.assertFalse(sidecar.exists())
            self.assertTrue(audio.is_file())

    def test_latest_reads_cli_snapshot_without_job_key(self) -> None:
        self._snap.write_text(
            json.dumps(
                {
                    "generated_at": "2026-08-30T00:00:00+00:00",
                    "checked": 10,
                    "broken": 1,
                    "errors": 0,
                    "tracks": [
                        {
                            "audio_path": "/music/broken.flac",
                            "stems_path": "/music/broken.flac.vdjstems",
                            "broken": True,
                            "hole_seconds": 12.5,
                            "holes": [{"start": 12, "end": 24.5}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        latest = sa.latest_stem_audit_job()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["broken_count"], 1)
        self.assertEqual(latest["checked"], 10)
        self.assertIn("broken.flac", latest["broken"][0]["audio_path"])


def _wait_job(job_id: str, timeout: float = 4.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = sa.get_stem_audit_job(job_id)
        if job and job["status"] in {"ok", "error", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {sa.get_stem_audit_job(job_id)}")
