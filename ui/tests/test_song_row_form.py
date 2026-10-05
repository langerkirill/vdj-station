"""Tool-written Song rows must match VDJ's own byte form (see the known-good file)."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V
from sorter import cue_edit, safe_write

HDR = b'<?xml version="1.0" encoding="UTF-8"?>\r\n<VirtualDJ_Database Version="2026">\r\n'
KNOWN_GOOD = Path.home() / "ms_stems_probe" / "known-good-opens-in-vdj-1218.xml"


def _vdj_rows(limit_with_comment=True):
    raw = KNOWN_GOOD.read_bytes()
    rows = re.findall(rb"\r\n( <Song .*?\r\n </Song>)", raw, re.S)
    with_c = [r for r in rows if b"\r\n  <Comment>" in r and b"Type=\"loop\"" in r and len(r) < 6000]
    return raw, rows, with_c


@unittest.skipUnless(KNOWN_GOOD.is_file(), "known-good VDJ file not present")
class SongRowFormTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.root / "bk"),
                                            "MUSIC_SORTER_FOREIGN_RETRY_BUDGET_S": "0"})
        self._env.start()
        os.environ.pop("MUSIC_SORTER_ALLOW_LF_DB", None)
        safe_write.reset_session_for_tests()
        self._nvdj = patch.object(cue_edit, "is_virtualdj_running", return_value=False)
        self._nvdj.start()

    def tearDown(self):
        self._nvdj.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_gate_does_not_false_flag_vdjs_own_file(self):
        raw = KNOWN_GOOD.read_bytes()
        self.assertEqual(V.song_form_problems(raw), [])
        V.assert_crlf_bytes(raw)

    def test_clone_row_matches_a_real_vdj_row_byte_for_byte(self):
        _raw, _rows, with_c = _vdj_rows()
        row = with_c[0]
        src_path = re.search(rb'FilePath="([^"]*)"', row).group(1).decode()
        db = self.root / "database.xml"
        db.write_bytes(HDR
                       + row + b"\r\n</VirtualDJ_Database>\r\n")
        new_path = "/Users/x/Music/DJ/Music/Zouk/Blvck/Clone Of It.mp3"
        safe_write.safe_clone_song(db, V.normalize_database_path(src_path.replace("&amp;", "&")), new_path)
        out = db.read_bytes()
        self.assertEqual(V.song_form_problems(out), [])
        V.assert_crlf_bytes(out)
        rows = re.findall(rb"\r\n( <Song .*?\r\n </Song>)", out, re.S)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], row)  # source row untouched, byte for byte
        a, b = rows[0].split(b"\r\n"), rows[1].split(b"\r\n")
        self.assertEqual(len(a), len(b))
        self.assertTrue(b[0].startswith(b' <Song FilePath="' + new_path.encode()))
        for i, (x, y) in enumerate(zip(a, b)):
            if i in (0, 1):  # open tag (FilePath) and Tags (User2 label) differ by design
                continue
            self.assertEqual(x, y, f"line {i}")
        self.assertEqual(b[-1], b" </Song>")
        kids = [re.match(rb"  <(\w+)", ln).group(1) for ln in b[1:-1]]
        self.assertEqual(kids[:3], [b"Tags", b"Infos", b"Comment"])

    def test_late_comment_and_stray_indent_are_healed_not_written(self):
        _raw, _rows, with_c = _vdj_rows()
        row = with_c[0]
        text = row.decode()
        lines = text.split("\r\n")
        comment = [ln for ln in lines if ln.startswith("  <Comment")][0]
        lines.remove(comment)
        lines.insert(len(lines) - 1, comment)  # late Comment (what AutoCue used to emit)
        bad = "\r\n".join(lines).replace("\r\n </Song>", "\r\n</Song>").replace(" <Song ", "  <Song ", 1)
        db = self.root / "database.xml"
        db.write_bytes(HDR + bad.encode() + b"\r\n</VirtualDJ_Database>\r\n")
        self.assertTrue(V.song_form_problems(db.read_bytes()))
        # any write heals the seams; the written file is exactly VDJ's form again
        V.atomic_replace_database(db, V.read_vdj_database_text(db))
        out = db.read_bytes()
        self.assertEqual(V.song_form_problems(out), [])
        self.assertIn(row, out)

    def test_candidate_that_cannot_be_healed_is_refused(self):
        db = self.root / "database.xml"
        good = HDR + b" <Song FilePath=\"/a.mp3\">\r\n  <Tags />\r\n </Song>\r\n</VirtualDJ_Database>\r\n"
        db.write_bytes(good)
        with self.assertRaises(V.LineEndingError):
            V.assert_vdj_song_form(HDR + b"<Song FilePath=\"/a.mp3\"></Song>\r\n</VirtualDJ_Database>\r\n")

    def test_removing_a_song_leaves_no_double_indent(self):
        _raw, rows, _ = _vdj_rows()
        a, b, c = rows[0], rows[1], rows[2]
        db = self.root / "database.xml"
        db.write_bytes(HDR + b"\r\n".join([a, b, c]) + b"\r\n</VirtualDJ_Database>\r\n")
        pb = re.search(rb'FilePath="([^"]*)"', b).group(1).decode().replace("&amp;", "&")
        safe_write.safe_remove_songs(db, [pb])
        out = db.read_bytes()
        self.assertEqual(V.song_form_problems(out), [])
        self.assertEqual(out.count(b"<Song "), 2)
        self.assertIn(a, out)
        self.assertIn(c, out)


SONG = (
    b' <Song FilePath="/a.mp3" FileSize="1">\r\n'
    b'  <Tags Author="A" />\r\n'
    b'  <Infos SongLength="1.0" LastModified="1" FirstSeen="1" Bitrate="320" UserColor="4278190335" Cover="1" />\r\n'
    b'  <Comment>line one\r\n\r\nline two after a blank (VDJ writes these)</Comment>\r\n'
    b'  <Scan Bpm="0.5" />\r\n'
    b'  <Poi Name="Intro" Pos="1.000000" Num="1" Color="4278255360" Type="cue" />\r\n'
    b'  <Poi Name="L" Pos="2.000000" Num="-1" Color="4278255360" Type="loop" Size="8.0" Slot="1" />\r\n'
    b' </Song>\r\n'
)
FOOT = b"</VirtualDJ_Database>\r\n"


class AcceptanceRuleTests(unittest.TestCase):
    """Rules proven by relaunching VirtualDJ."""

    def setUp(self):
        old = os.environ.pop("MUSIC_SORTER_ALLOW_LF_DB", None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, "MUSIC_SORTER_ALLOW_LF_DB", old)

    def problems(self, raw):
        return V.song_form_problems(raw)

    def test_good_row_with_blank_line_inside_a_comment_text_passes(self):
        self.assertEqual(self.problems(HDR + SONG + FOOT), [])

    def test_blank_or_whitespace_only_line_between_rows_is_rejected(self):
        for gap in (b"\r\n", b"  \r\n", b"\r\n\r\n"):
            raw = HDR + SONG + gap + SONG.replace(b"/a.mp3", b"/b.mp3") + FOOT
            self.assertTrue(any("empty" in p for p in self.problems(raw)), gap)
            with self.assertRaises(V.LineEndingError):
                V.assert_vdj_song_form(raw)

    def test_blank_line_inside_a_row_between_children_is_rejected(self):
        raw = HDR + SONG.replace(b'  <Scan', b"\r\n  <Scan") + FOOT
        self.assertTrue(any("empty" in p for p in self.problems(raw)))

    def test_writes_heal_a_stray_blank_line_between_rows(self):
        raw = HDR + SONG + b"\r\n" + SONG.replace(b"/a.mp3", b"/b.mp3") + FOOT
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.xml"
            db.write_bytes(HDR + SONG + FOOT)
            V.atomic_replace_database(db, raw.decode("utf-8"))
            out = db.read_bytes()
        self.assertEqual(self.problems(out), [])
        self.assertIn(b"Cover=\"1\" />\r\n  <Comment>line one\r\n\r\nline two", out)  # comment text untouched

    def test_header_and_footer_are_required(self):
        self.assertTrue(self.problems(SONG + FOOT))  # no header
        self.assertTrue(self.problems(HDR.replace(b"UTF-8", b"utf8") + SONG + FOOT))
        self.assertTrue(self.problems(HDR + SONG + FOOT + b"\r\n"))  # extra line after the footer
        self.assertTrue(self.problems(HDR + SONG + b"</VirtualDJ_Database>"))  # no final CRLF

    def test_child_order_is_enforced(self):
        bad = SONG.replace(b"  <Scan Bpm=\"0.5\" />\r\n", b"") .replace(b" </Song>", b'  <Scan Bpm="0.5" />\r\n </Song>')
        self.assertTrue(any("out of order" in p for p in self.problems(HDR + bad + FOOT)))

    def test_infos_and_poi_attribute_order_are_enforced(self):
        bad_infos = SONG.replace(b'Bitrate="320" UserColor="4278190335" Cover="1"', b'UserColor="4278190335" Bitrate="320" Cover="1"')
        self.assertTrue(any("Infos" in p for p in self.problems(HDR + bad_infos + FOOT)))
        bad_poi = SONG.replace(b'Pos="1.000000" Num="1"', b'Num="1" Pos="1.000000"')
        self.assertTrue(any("Poi" in p for p in self.problems(HDR + bad_poi + FOOT)))

    def test_poi_emitter_matches_vdj_form(self):
        line = V.format_vdj_poi_line(pos=12.5, poi_type="loop", num="-1", color="4278255360",
                                     name="L", size="8", slot="2", newline="\r\n")
        self.assertEqual(line, '  <Poi Name="L" Pos="12.500000" Num="-1" Color="4278255360" Type="loop" Size="8.0" Slot="2" />\r\n')
        zero = V.format_vdj_poi_line(pos=0.0, poi_type="cue", num="1", color="1", name="C", newline="\r\n")
        self.assertEqual(zero, '  <Poi Name="C" Num="1" Color="1" Type="cue" />\r\n')  # Pos dropped when 0

    @unittest.skipUnless(KNOWN_GOOD.is_file(), "known-good VDJ file not present")
    def test_every_vdj_known_good_file_passes(self):
        for f in sorted(KNOWN_GOOD.parent.glob("known-good-opens-in-vdj-*.xml")):
            self.assertEqual(V.song_form_problems(f.read_bytes()), [], f.name)


if __name__ == "__main__":
    unittest.main()
