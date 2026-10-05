"""Line-ending hard gate: VirtualDJ's database.xml is CRLF on every line.

LF in -> refusal (nothing written). CRLF in -> CRLF out. A write that fails the
post-write checks is rolled back from the pre-write bytes. Temp copies only.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V
from sorter import cue_edit, deleted_markers, safe_write


def make_db(audio: Path, newline: str = "\r\n") -> bytes:
    pad = "".join(f'  <Poi Pos="{i}.0" Type="automix" Point="x{i}" />\n' for i in range(20))
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<VirtualDJ_Database Version="2026">\n'
        f' <Song FilePath="{audio}" Flag="1">\n'
        '  <Tags Author="A" Title="T" Key="Am" />\n'
        '  <Infos SongLength="300.0" />\n'
        '  <Scan Bpm="0.5" />\n'
        + pad
        + '  <Poi Pos="0.1" Type="beatgrid" />\n'
        '  <Poi Name="Build" Pos="94.426121" Num="2" Color="4278255360" Type="cue" />\n'
        '  <Poi Name="Melody Loop" Pos="260.626958" Num="-1" Color="4278255360" Type="loop" Size="8.0" Slot="2" />\n'
        " </Song>\n"
        ' <Song FilePath="/other/untouched.flac" Flag="1">\n'
        '  <Poi Name="Intro" Pos="1.0" Num="1" Color="4278255360" Type="cue" />\n'
        " </Song>\n"
        "</VirtualDJ_Database>\n"
    )
    return text.replace("\n", newline).encode("utf-8")


def pure_crlf(raw: bytes) -> bool:
    return raw.count(b"\r") == raw.count(b"\n") == raw.count(b"\r\n") > 0


class CrlfGateTests(unittest.TestCase):
    def setUp(self) -> None:
        safe_write.reset_session_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.audio = self.root / "track.flac"
        self.audio.write_bytes(b"audio")
        self.db = self.root / "database.xml"
        self._env = patch.dict(
            os.environ,
            {
                "MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.root / "bk"),
                "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "0",
                "MUSIC_SORTER_NOTES_DIR": str(self.root / "notes"),
            },
        )
        self._env.start()
        self._nvdj = patch.object(cue_edit, "is_virtualdj_running", return_value=False)
        self._nvdj.start()
        os.environ.pop("MUSIC_SORTER_ALLOW_LF_DB", None)  # gate ON for these tests
        self._notes_before = deleted_markers.config.DJ_NOTES_ROOT
        deleted_markers.config.DJ_NOTES_ROOT = self.root / "notes"

    def tearDown(self) -> None:
        deleted_markers.config.DJ_NOTES_ROOT = self._notes_before
        self._nvdj.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _recolor(self):
        return cue_edit.set_poi_color(
            self.audio, kind="cue", pos=94.426121, color="blue", database_path=self.db
        )

    def test_crlf_in_crlf_out(self):
        self.db.write_bytes(make_db(self.audio.resolve()))
        self._recolor()
        raw = self.db.read_bytes()
        self.assertTrue(pure_crlf(raw))
        self.assertIn(b'Name="Build" Pos="94.426121" Num="2" Color="4278190335"', raw)

    def test_lf_in_is_refused_and_nothing_is_written(self):
        original = make_db(self.audio.resolve(), "\n")
        self.db.write_bytes(original)
        with self.assertRaises(RuntimeError) as ctx:
            self._recolor()
        self.assertIn("CRLF", str(ctx.exception))
        self.assertEqual(self.db.read_bytes(), original)
        self.assertFalse(safe_write.last_save()["saved"])

    def test_candidate_with_bare_lf_is_refused_before_replacing(self):
        original = make_db(self.audio.resolve())
        self.db.write_bytes(original)
        text = original.decode("utf-8")
        bad = text.replace("</VirtualDJ_Database>", "\n</VirtualDJ_Database>")  # one bare LF
        with self.assertRaises(V.LineEndingError):
            V.atomic_replace_database(self.db, bad)
        self.assertEqual(self.db.read_bytes(), original)

    def test_spliced_new_song_is_crlf_inside_a_crlf_database(self):
        self.db.write_bytes(make_db(self.audio.resolve()))
        new_audio = str(self.root / "copy.flac")
        safe_write.safe_clone_song(self.db, str(self.audio.resolve()), new_audio)
        raw = self.db.read_bytes()
        self.assertTrue(pure_crlf(raw))
        self.assertIn(new_audio.encode(), raw)

    def test_failed_post_write_check_rolls_back_to_previous_bytes(self):
        original = make_db(self.audio.resolve())
        self.db.write_bytes(original)

        class Broken:
            @staticmethod
            def database_integrity_stats(_p):
                return {"size_bytes": 1, "song_count": 1, "cue_loop_count": 1}

            @staticmethod
            def validate_database_replacement(_p, _s, stats_fn=None):
                raise ValueError("Generated database failed integrity check: test")

        with patch.object(V, "autocue_safety_module", return_value=Broken):
            with self.assertRaises(RuntimeError) as ctx:
                self._recolor()
        self.assertIn("rolled back", str(ctx.exception))
        self.assertEqual(self.db.read_bytes(), original)

    def test_assert_crlf_bytes_and_to_crlf(self):
        V.assert_crlf_bytes(b"a\r\nb\r\n")
        with self.assertRaises(V.LineEndingError):
            V.assert_crlf_bytes(b"a\r\nb\n")
        with self.assertRaises(V.LineEndingError):
            V.assert_crlf_bytes(b"a\nb\n")
        self.assertEqual(V.to_crlf("a\nb\r\nc"), "a\r\nb\r\nc")

    def test_autocue_module_checks_are_used(self):
        mod = V.autocue_safety_module()
        self.assertTrue(callable(mod.database_integrity_stats))
        self.assertTrue(callable(mod.validate_database_replacement))

    def test_restore_marker_puts_back_exact_poi_in_crlf(self):
        self.db.write_bytes(make_db(self.audio.resolve()))
        res = cue_edit.delete_cue_point(
            self.audio, kind="loop", pos=260.626958, slot="2", name="Melody Loop",
            database_path=self.db,
        )
        removed = res["removed"]
        self.assertNotIn(b"Melody Loop", self.db.read_bytes())
        entry = deleted_markers.record_deleted(str(self.audio), removed)
        out = cue_edit.restore_poi_point(
            self.audio, raw=entry["raw"], database_path=self.db, allow_vdj_running=True
        )
        raw = self.db.read_bytes()
        self.assertTrue(pure_crlf(raw))
        self.assertIn(
            b'Name="Melody Loop" Pos="260.626958" Num="-1" Color="4278255360" Type="loop" Size="8.0" Slot="2"',
            raw,
        )
        self.assertEqual(out["restored"]["slot"], "2")
        # second undo can never duplicate the marker
        with self.assertRaises(ValueError):
            cue_edit.restore_poi_point(
                self.audio, raw=entry["raw"], database_path=self.db, allow_vdj_running=True
            )


class DeletedMarkersStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._notes_before = deleted_markers.config.DJ_NOTES_ROOT
        deleted_markers.config.DJ_NOTES_ROOT = Path(self._tmp.name)

    def tearDown(self) -> None:
        deleted_markers.config.DJ_NOTES_ROOT = self._notes_before
        self._tmp.cleanup()

    def test_record_list_remove_and_atomic_file(self):
        removed = {"kind": "loop", "name": "L", "pos": 12.5, "size": "8.0", "num": "-1",
                   "slot": "1", "color": "1", "raw": '<Poi Name="L" Pos="12.5" Type="loop" />'}
        e = deleted_markers.record_deleted("/a/b.flac", removed)
        f = deleted_markers.store_path()
        self.assertTrue(f.is_file())
        self.assertEqual([m["id"] for m in deleted_markers.list_for_path("/a/b.flac")], [e["id"]])
        self.assertEqual(deleted_markers.list_for_path("/other.flac"), [])
        self.assertEqual(e["key"], "/a/b.flac|loop|L|12.500|8.0")
        self.assertEqual([p.name for p in f.parent.iterdir()], ["deleted_markers.json"])  # no temp left
        self.assertTrue(deleted_markers.remove_record(e["id"]))
        self.assertEqual(deleted_markers.list_for_path("/a/b.flac"), [])
        self.assertFalse(deleted_markers.remove_record(e["id"]))


if __name__ == "__main__":
    unittest.main()
