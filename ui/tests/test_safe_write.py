"""Verified single-writer saves: an edit round-trips into database.xml (temp copy only)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V
from sorter import cue_edit, safe_write


def make_db(audio: Path) -> bytes:
    pad = "".join(f'  <Poi Pos="{i}.0" Type="automix" Point="x{i}" />\r\n' for i in range(20))
    return (
        "<VirtualDJ_Database>\r\n"
        f'<Song FilePath="{audio}" Flag="1">\r\n'
        '  <Tags Author="A" Title="T" Key="Am" />\r\n'
        '  <Scan Bpm="0.5" />\r\n'
        + pad
        + '  <Poi Pos="0.1" Type="beatgrid" />\r\n'
        '  <Poi Name="Build" Pos="94.426121" Num="2" Color="4278255360" Type="cue" />\r\n'
        '  <Poi Name="Drop" Pos="173.114601" Num="3" Color="4278255360" Type="cue" />\r\n'
        '  <Poi Name="Melody Loop" Pos="260.626958" Num="-1" Color="4278255360" Type="loop" Size="8.0" Slot="2" />\r\n'
        "</Song>\r\n"
        '<Song FilePath="/other/untouched.flac" Flag="1">\r\n'
        '  <Poi Name="Intro" Pos="1.0" Num="1" Color="4278255360" Type="cue" />\r\n'
        "</Song>\r\n"
        "</VirtualDJ_Database>\r\n"
    ).encode("utf-8")


class SafeWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        safe_write.reset_session_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.audio = self.root / "track.flac"
        self.audio.write_bytes(b"audio")
        self.db = self.root / "database.xml"
        self.db.write_bytes(make_db(self.audio.resolve()))
        self.backups = self.root / "bk"
        self._env = patch.dict(
            os.environ,
            {
                "MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.backups),
                "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "0",  # immediate-refusal tests; retry has its own tests
            },
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def _color(self, color="blue"):
        return cue_edit.set_poi_color(
            self.audio, kind="cue", pos=94.426121, color=color, database_path=self.db
        )

    def test_edit_round_trips_into_database_xml_with_saved_true(self):
        before = self.db.read_bytes()
        res = self._color("blue")
        self.assertTrue(res["ok"])
        raw = self.db.read_bytes()
        self.assertIn(b'Name="Build" Pos="94.426121" Num="2" Color="4278190335"', raw)  # green -> blue
        self.assertIn(b"\r\n", raw)  # CRLF preserved
        # only the target Song changed
        self.assertIn(b'<Song FilePath="/other/untouched.flac" Flag="1">', raw)
        self.assertEqual(raw.split(b"</Song>")[1], before.split(b"</Song>")[1])
        last = safe_write.last_save()
        self.assertTrue(last["saved"])
        self.assertEqual(last["reason"], "verified in database.xml")

    def test_one_session_backup_before_first_write_only(self):
        self._color("blue")
        self.assertEqual(len(list(self.backups.iterdir())), 1)
        backup = next(self.backups.iterdir())
        self.assertIn(b'Color="4278255360" Type="cue"', backup.read_bytes())  # pre-write content
        self._color("purple")
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_delete_loop_round_trip(self):
        cue_edit.delete_cue_point(
            self.audio, kind="loop", pos=260.626958, slot="2", name="Melody Loop",
            database_path=self.db,
        )
        raw = self.db.read_bytes()
        self.assertNotIn(b"Melody Loop", raw)
        self.assertIn(b'Name="Drop"', raw)

    def test_stale_song_is_refused_and_nothing_written(self):
        content = V.read_vdj_database_text(self.db)
        s, e = V._find_song_span(content, V.normalize_database_path(str(self.audio.resolve())))
        base = content[s:e]
        # another writer changes the song after our read
        other = base.replace("Drop", "Chorus")
        self.db.write_bytes((content[:s] + other + content[e:]).encode())
        new_song = base.replace('Color="4278255360" Type="cue"', 'Color="4278190335" Type="cue"', 1)
        with self.assertRaises(safe_write.SaveFailed) as ctx:
            safe_write.safe_rewrite_song(
                self.db, V.normalize_database_path(str(self.audio.resolve())), new_song,
                base_song=base,
            )
        self.assertEqual(ctx.exception.code, "stale")
        self.assertIn(b"Chorus", self.db.read_bytes())
        self.assertFalse(safe_write.last_save()["saved"])

    def test_foreign_writer_within_window_is_refused(self):
        with patch.dict(os.environ, {"MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S": "20"}):
            with self.assertRaises(RuntimeError) as ctx:
                self._color("blue")
            self.assertIn("another writer", str(ctx.exception))
        self.assertNotIn(b"4278190335", self.db.read_bytes())
        self.assertFalse(self.backups.exists() and list(self.backups.iterdir()))

    def test_foreign_writer_that_clears_is_retried_until_the_edit_lands(self):
        # database.xml was just "written by VDJ Dev"; its 2 s window clears while we wait.
        env = {
            "MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S": "2",
            "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "10",
            "MUSIC_SORTER_FOREIGN_RETRY_POLL_S": "0.2",
        }
        with patch.dict(os.environ, env):
            t0 = time.monotonic()
            res = self._color("purple")
            waited = time.monotonic() - t0
        self.assertTrue(safe_write.last_save()["saved"])
        self.assertGreater(waited, 0.5)  # it really queued
        self.assertLess(waited, 6)
        self.assertIn(b"4288020735", self.db.read_bytes())
        self.assertEqual(safe_write.last_save()["saved"], True)

    def test_vdjdev_backup_that_ages_out_is_retried(self):
        bk = self.root / "database.xml.backup.20261004_070000.autocue-cue001.vdjdev"
        bk.write_bytes(b"x")
        env = {
            "MUSIC_SORTER_VDJDEV_BACKUP_WINDOW_S": "2",
            "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "10",
            "MUSIC_SORTER_FOREIGN_RETRY_POLL_S": "0.2",
        }
        with patch.dict(os.environ, env):
            self._color("purple")
        self.assertTrue(safe_write.last_save()["saved"])

    def test_foreign_writer_that_never_clears_fails_after_budget_and_keeps_payload(self):
        env = {
            "MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S": "3600",
            "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "1",
            "MUSIC_SORTER_FOREIGN_RETRY_POLL_S": "0.2",
        }

        class Body:
            def model_dump(self):
                return {"path": "x.flac", "color": "purple"}

        @safe_write.with_save_status("/api/test-edit")
        def endpoint(body):
            return self._color("purple")

        before = self.db.read_bytes()
        with patch.dict(os.environ, env):
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError) as ctx:
                endpoint(Body())
            waited = time.monotonic() - t0
        self.assertIn("another writer", str(ctx.exception))
        self.assertIn("Nothing was written", str(ctx.exception))
        self.assertGreaterEqual(waited, 0.8)
        self.assertLess(waited, 4)
        self.assertEqual(self.db.read_bytes(), before)
        last = safe_write.session_failures()[-1]
        self.assertEqual(last["endpoint"], "/api/test-edit")
        self.assertEqual(last["payload"], {"path": "x.flac", "color": "purple"})

    def test_vdj_running_and_stale_are_never_retried(self):
        env = {"MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "30", "MUSIC_SORTER_FOREIGN_RETRY_POLL_S": "5"}
        with patch.dict(os.environ, {**env, "VDJ_ALLOW_RUNNING_WRITES": "0"}), patch.object(
            V, "is_virtualdj_running", return_value=True
        ):
            t0 = time.monotonic()
            with self.assertRaises(RuntimeError):
                self._color("blue")
            self.assertLess(time.monotonic() - t0, 3)

    def test_own_write_is_not_treated_as_foreign(self):
        self._color("blue")
        with patch.dict(os.environ, {"MUSIC_SORTER_FOREIGN_WRITE_WINDOW_S": "20"}):
            self._color("purple")  # our own previous write must not block us
        self.assertIn(b"4288020735", self.db.read_bytes())

    def test_recent_vdjdev_backup_blocks_writes(self):
        bk = self.root / "database.xml.backup.20261004_070000.vdjdev-loops047"
        bk.write_bytes(b"x")
        with patch.dict(os.environ, {"MUSIC_SORTER_VDJDEV_BACKUP_WINDOW_S": "60"}):
            with self.assertRaises(RuntimeError) as ctx:
                self._color("blue")
            self.assertIn("VirtualDJ Dev", str(ctx.exception))
        old = time.time() - 600
        os.utime(bk, (old, old))
        with patch.dict(os.environ, {"MUSIC_SORTER_VDJDEV_BACKUP_WINDOW_S": "60"}):
            self._color("blue")  # old vdjdev backup no longer blocks

    def test_vdj_running_is_refused(self):
        with patch.dict(os.environ, {"VDJ_ALLOW_RUNNING_WRITES": "0"}), patch.object(
            V, "is_virtualdj_running", return_value=True
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self._color("blue")
            self.assertIn("VirtualDJ is running", str(ctx.exception))

    def test_readback_mismatch_is_reported_as_not_saved(self):
        real = V.read_vdj_database_text
        calls = {"n": 0}

        def flaky(path):
            calls["n"] += 1
            text = real(path)
            return text.replace("4278190335", "1") if calls["n"] >= 3 else text

        with patch.object(V, "read_vdj_database_text", flaky):
            with self.assertRaises(safe_write.SaveFailed) as ctx:
                self._color("blue")
        self.assertEqual(ctx.exception.code, "readback_mismatch")
        self.assertFalse(safe_write.last_save()["saved"])

    def test_endpoint_decorator_adds_saved_fields_and_records_failures(self):
        @safe_write.with_save_status("/api/test")
        def ok_endpoint():
            self._color("blue")
            return {"ok": True, "result": {"dry_run": False}}

        out = ok_endpoint()
        self.assertTrue(out["saved"])
        self.assertEqual(out["save_reason"], "verified in database.xml")

        @safe_write.with_save_status("/api/test")
        def failing():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            failing()
        self.assertEqual(safe_write.session_failures()[-1]["reason"], "boom")

    def test_stale_refusal_is_retried_with_a_fresh_read_and_then_lands(self):
        calls = []

        @safe_write.with_save_status("/api/test")
        def edit():
            calls.append(1)
            if len(calls) == 1:
                # first try: the song moved on between our read and our write
                raise safe_write._fail("The song changed on disk while this edit was being saved.", "stale")
            self._color("blue")
            return {"ok": True, "result": {"dry_run": False}}

        with patch.object(safe_write.time, "sleep"):
            out = edit()
        self.assertEqual(len(calls), 2)
        self.assertTrue(out["saved"])
        self.assertEqual(out["save_reason"], "verified in database.xml")
        self.assertEqual(safe_write.session_failures(), [])  # a retried-and-landed edit is not a failure

    def test_stale_refusal_that_never_clears_gives_up_with_the_plain_message(self):
        calls = []

        @safe_write.with_save_status("/api/test")
        def edit():
            calls.append(1)
            raise safe_write._fail("The song changed on disk while this edit was being saved.", "stale")

        with patch.object(safe_write.time, "sleep"):
            with self.assertRaises(safe_write.SaveFailed):
                edit()
        self.assertEqual(len(calls), safe_write.STALE_RETRIES + 1)
        self.assertEqual(len(safe_write.session_failures()), 1)


if __name__ == "__main__":
    unittest.main()
