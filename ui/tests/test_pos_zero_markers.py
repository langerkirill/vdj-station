"""VDJ drops Pos when it is 0: a cue/loop at 0:00 has NO Pos attribute. Every edit must still find it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sorter import cue_edit as C
from sorter import poi_rename as R

SONG = (
    '<Song FilePath="/m/a.flac">\r\n'
    '  <Scan Bpm="0.5" />\r\n'
    '  <Poi Pos="0.265714" Type="beatgrid" />\r\n'
    '  <Poi Name="Beat Entry" Num="1" Color="4278255360" Type="cue" />\r\n'
    '  <Poi Name="Drop" Pos="32.000000" Num="2" Color="4278255360" Type="cue" />\r\n'
    '  <Poi Name="Intro Loop" Num="-1" Color="4278190335" Type="loop" Size="8.0" Slot="1" />\r\n'
    '  <Poi Name="Later Loop" Pos="48.000000" Num="-1" Color="4278190335" Type="loop" Size="8.0" Slot="2" />\r\n'
    "</Song>\r\n"
)


class PosZeroTests(unittest.TestCase):
    def test_delete_cue_at_zero(self):
        out, removed = C.remove_manual_poi_from_song_xml(SONG, kind="cue", pos=0.0, num="1", name="Beat Entry")
        self.assertNotIn("Beat Entry", out)
        self.assertIn("Drop", out)
        self.assertEqual(removed["name"], "Beat Entry")
        self.assertEqual(float(removed["pos"]), 0.0)

    def test_delete_loop_at_zero(self):
        out, removed = C.remove_manual_poi_from_song_xml(SONG, kind="loop", pos=0.0, num="-1", name="Intro Loop", slot="1")
        self.assertNotIn("Intro Loop", out)
        self.assertIn("Later Loop", out)

    def test_move_cue_off_zero_adds_the_pos_attribute(self):
        out, ch = C.set_poi_position_in_song_xml(SONG, kind="cue", pos=0.0, new_pos=12.5, num="1", name="Beat Entry")
        line = [l for l in out.split("\r\n") if "Beat Entry" in l][0]
        self.assertIn('Pos="12.500000"', line)
        self.assertEqual(ch["pos_before"], 0.0)
        self.assertEqual(ch["pos_after"], 12.5)

    def test_move_loop_off_zero_adds_the_pos_attribute(self):
        out, _ = C.set_poi_position_in_song_xml(SONG, kind="loop", pos=0.0, new_pos=7.5, num="-1", name="Intro Loop", slot="1")
        line = [l for l in out.split("\r\n") if "Intro Loop" in l][0]
        self.assertLess(line.index("Name="), line.index("Pos="))  # VDJ order: Name, Pos, Num ...
        self.assertLess(line.index("Pos="), line.index("Num="))

    def test_move_back_to_zero_drops_pos_like_vdj(self):
        moved, _ = C.set_poi_position_in_song_xml(SONG, kind="cue", pos=0.0, new_pos=12.5, num="1", name="Beat Entry")
        back, ch = C.set_poi_position_in_song_xml(moved, kind="cue", pos=12.5, new_pos=0.0, num="1", name="Beat Entry")
        line = [l for l in back.split("\r\n") if "Beat Entry" in l][0]
        self.assertNotIn("Pos=", line)
        self.assertEqual(ch["pos_after"], 0.0)

    def test_rename_cue_and_loop_at_zero(self):
        out, ch = R.set_poi_name_in_song_xml(SONG, kind="cue", pos=0.0, new_name="Start", num="1", name="Beat Entry")
        self.assertIn('Name="Start"', out)
        self.assertNotIn("Beat Entry", out)
        out2, _ = R.set_poi_name_in_song_xml(SONG, kind="loop", pos=0.0, new_name="Zero Loop", num="-1", name="Intro Loop", slot="1")
        self.assertIn('Name="Zero Loop"', out2)

    def test_recolor_cue_and_loop_at_zero(self):
        out, ch = C.set_poi_color_in_song_xml(SONG, kind="cue", pos=0.0, color="yellow", num="1", name="Beat Entry")
        line = [l for l in out.split("\r\n") if "Beat Entry" in l][0]
        self.assertIn('Color="4294967040"', line)
        out2, _ = C.set_poi_color_in_song_xml(SONG, kind="loop", pos=0.0, color="orange", num="-1", name="Intro Loop", slot="1")
        self.assertIn('Color="4294934272"', [l for l in out2.split("\r\n") if "Intro Loop" in l][0])


class PosZeroDatabaseTests(unittest.TestCase):
    """Whole-write path against a temp database.xml: write, then re-read and compare."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.audio = self.root / "track.flac"
        self.audio.write_bytes(b"x")
        self.db = self.root / "database.xml"
        song = SONG.replace("/m/a.flac", str(self.audio.resolve()))
        self.db.write_bytes(
            ("<VirtualDJ_Database>\r\n" + song + "</VirtualDJ_Database>\r\n").encode("utf-8")
        )
        self._patches = [
            patch.object(C, "CUES_ROOT", self.root),
            patch.object(C, "LIBRARIES", {}),
            patch.object(C, "VDJ_DATABASE", self.db),
            patch.object(C, "is_virtualdj_running", return_value=False),
            patch.object(R, "CUES_ROOT", self.root),
            patch.object(R, "LIBRARIES", {}),
            patch.object(R, "VDJ_DATABASE", self.db),
            patch.object(R, "is_virtualdj_running", return_value=False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _text(self):
        return self.db.read_bytes().decode("utf-8")

    def test_delete_pos_less_cue_removes_it_from_the_file(self):
        r = C.delete_cue_point(self.audio, kind="cue", pos=0.0, num="1", name="Beat Entry", database_path=self.db)
        self.assertTrue(r["ok"])
        self.assertNotIn("Beat Entry", self._text())
        self.assertIn('Name="Drop"', self._text())

    def test_move_pos_less_cue_updates_the_file_and_summary(self):
        r = C.set_poi_position(self.audio, kind="cue", pos=0.0, new_pos=12.5, num="1", name="Beat Entry", database_path=self.db)
        self.assertTrue(r["ok"])
        self.assertIn('Name="Beat Entry" Pos="12.500000" Num="1"', self._text())
        pts = {(p["name"], round(p["pos"], 3)) for p in r["cues"]["points"]} if "points" in r["cues"] else None
        if pts is not None:
            self.assertIn(("Beat Entry", 12.5), pts)

    def test_rename_pos_less_cue_updates_the_file(self):
        r = R.set_poi_name(self.audio, kind="cue", pos=0.0, new_name="Start", num="1", database_path=self.db)
        self.assertTrue(r["ok"])
        self.assertIn('Name="Start"', self._text())
        self.assertNotIn("Beat Entry", self._text())

    def test_recolor_pos_less_cue_updates_the_file(self):
        r = C.set_poi_color(self.audio, kind="cue", pos=0.0, color="orange", num="1", database_path=self.db)
        self.assertTrue(r["ok"])
        self.assertIn('Name="Beat Entry" Num="1" Color="4294934272"', self._text())

    def test_unconfirmed_write_is_reported_as_failure_not_success(self):
        """If the file does not show the change after saving, the call must raise (HTTP 409), never say ok."""
        real = C.safe_rewrite_song

        def noop(db, path_in_db, new_song, base_song=None, validate=False, **kw):
            return None  # pretends to write, writes nothing

        with patch.object(C, "safe_rewrite_song", noop):
            with self.assertRaises(RuntimeError) as cm:
                C.delete_cue_point(self.audio, kind="cue", pos=0.0, num="1", name="Beat Entry", database_path=self.db)
        self.assertIn("NOT confirmed", str(cm.exception))
        with patch.object(C, "safe_rewrite_song", noop):
            with self.assertRaises(RuntimeError):
                C.set_poi_position(self.audio, kind="cue", pos=0.0, new_pos=9.0, num="1", database_path=self.db)
            with self.assertRaises(RuntimeError):
                C.set_poi_color(self.audio, kind="cue", pos=0.0, color="purple", num="1", database_path=self.db)
        self.assertIn("Beat Entry", self._text())


if __name__ == "__main__":
    unittest.main()
