"""Post-write read-back must accept the Song block that heal_song_form put in VDJ form.

The station's read-back in rewrite_song_xml_in_database compared the raw new
Song XML to the file; the House hard gates heal stray indentation / a late
<Comment> on the whole file before writing. Both sides must be canonicalized,
or every AutoCue write that the healer touches is reported as NOT saved.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_database_safety as V

AUDIO = "/Users/x/Music/House/Track One.flac"


def vdj_db() -> bytes:
    pad = "".join(f'  <Poi Pos="{i}.0" Type="automix" Point="x{i}" />\n' for i in range(20))
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<VirtualDJ_Database Version="2026">\n'
        f' <Song FilePath="{AUDIO}" Flag="1">\n'
        '  <Tags Author="A" Title="T" Key="Am" />\n'
        '  <Infos SongLength="300.0" />\n'
        "  <Comment>keep me</Comment>\n"
        '  <Scan Bpm="0.5" />\n'
        + pad
        + '  <Poi Pos="0.1" Type="beatgrid" />\n'
        '  <Poi Name="Build" Pos="94.426121" Num="2" Color="4278255360" Type="cue" />\n'
        " </Song>\n"
        ' <Song FilePath="/other/untouched.flac" Flag="1">\n'
        '  <Poi Name="Intro" Pos="1.0" Num="1" Color="4278255360" Type="cue" />\n'
        " </Song>\n"
        "</VirtualDJ_Database>\n"
    )
    return text.replace("\n", "\r\n").encode("utf-8")


def song_block(raw: bytes) -> str:
    text = raw.decode("utf-8")
    start, end = V._find_song_span(text, AUDIO)
    return text[start:end]


class ReadbackVsHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "database.xml"
        self.db.write_bytes(vdj_db())
        self._env = patch.dict(os.environ, {"MUSIC_SORTER_WRITE_BACKUP_DIR": str(self.root / "bk")})
        self._env.start()
        os.environ.pop("MUSIC_SORTER_ALLOW_LF_DB", None)  # production mode: gates + healing on
        os.environ.pop("MUSIC_SORTER_READONLY", None)
        self._nvdj = patch.object(V, "is_virtualdj_running", return_value=False)
        self._nvdj.start()

    def tearDown(self) -> None:
        self._nvdj.stop()
        self._env.stop()
        os.environ["MUSIC_SORTER_ALLOW_LF_DB"] = "1"
        self._tmp.cleanup()

    def _assert_written_in_vdj_form(self) -> bytes:
        out = self.db.read_bytes()
        self.assertEqual(V.song_form_problems(out), [])
        V.assert_crlf_bytes(out)
        self.assertIn(b'Name="Drop" Pos="120.0"', out)
        self.assertIn(b"keep me", out)
        self.assertIn(b"/other/untouched.flac", out)
        return out

    def test_late_comment_is_healed_and_readback_accepts_it(self):
        base = song_block(self.db.read_bytes())
        lines = base.split("\r\n")
        comment = [ln for ln in lines if ln.strip().startswith("<Comment")][0]
        lines.remove(comment)
        lines.insert(-1, '  <Poi Name="Drop" Pos="120.0" Num="3" Color="4278255360" Type="cue" />')
        lines.insert(-1, comment)  # Comment after Poi: the healer moves it back after Infos
        V.rewrite_song_xml_in_database(self.db, AUDIO, "\r\n".join(lines), base_song_xml=base)
        out = self._assert_written_in_vdj_form()
        block = song_block(out)
        self.assertLess(block.index("<Comment>"), block.index("<Scan"))

    def test_stray_close_indent_is_healed_and_readback_accepts_it(self):
        base = song_block(self.db.read_bytes())
        lines = base.split("\r\n")
        lines.insert(-1, '  <Poi Name="Drop" Pos="120.0" Num="3" Color="4278255360" Type="cue" />')
        lines[-1] = "</Song>"  # column-0 close; spliced after the prefix it becomes "\r\n</Song>"
        new_xml = "\r\n".join(lines)
        V.rewrite_song_xml_in_database(self.db, AUDIO, new_xml, base_song_xml=base)
        self._assert_written_in_vdj_form()

    def test_readback_still_refuses_a_block_that_did_not_land(self):
        base = song_block(self.db.read_bytes())
        lines = base.split("\r\n")
        lines.insert(-1, '  <Poi Name="Drop" Pos="120.0" Num="3" Color="4278255360" Type="cue" />')
        real_replace = V._replace_database_parts_locked

        def drop_edit(path, parts, *a, **k):
            prefix, _song, suffix = parts
            return real_replace(path, (prefix, base, suffix), *a, **k)

        with patch.object(V, "_replace_database_parts_locked", side_effect=drop_edit):
            with self.assertRaisesRegex(RuntimeError, "read-back mismatch"):
                V.rewrite_song_xml_in_database(self.db, AUDIO, "\r\n".join(lines))


if __name__ == "__main__":
    unittest.main()
