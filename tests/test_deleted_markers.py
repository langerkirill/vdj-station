"""AutoCue must not recreate cues/loops recorded in deleted_markers.json (R-09)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vdj_cuer.cue_writer import PreparedPoi
from vdj_cuer import deleted_markers as DM


def _poi(kind, pos, name="x", beats=None):
    return PreparedPoi(
        kind=kind,
        name=name,
        position=pos,
        color_name="green",
        color_value="0",
        elements=[],
        length_beats=beats,
    )


def _write_store(path: Path, markers: list) -> Path:
    path.write_text(
        json.dumps({"version": 1, "markers": markers}, indent=2),
        encoding="utf-8",
    )
    return path


class ResolveStorePathTests(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "custom.json"
            f.write_text('{"version":1,"markers":[]}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"AUTOCUE_DELETED_MARKERS_PATH": str(f)}):
                self.assertEqual(DM.resolve_store_path(), f)

    def test_house_8788_preferred_over_flat(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            house = root / "House-8788" / "deleted_markers.json"
            flat = root / "deleted_markers.json"
            house.parent.mkdir()
            house.write_text('{"version":1,"markers":[]}', encoding="utf-8")
            flat.write_text('{"version":1,"markers":[]}', encoding="utf-8")
            env = {
                "DJ_NOTES_ROOT": str(root),
                "AUTOCUE_DELETED_MARKERS_PATH": "",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                # Clear override key if empty string still set — resolve treats
                # empty as unset via strip().
                self.assertEqual(DM.resolve_store_path(), house)


class FilterDeletedTests(unittest.TestCase):
    SONG = "/Music/DJ/Music/Cues/Add Cues/Sauna Fest House/010. Dexter Crowe - Bliss.flac"
    OTHER = "/Music/DJ/Music/Cues/Add Cues/Sauna Fest House/129. Other.flac"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.store = Path(self.td.name) / "deleted_markers.json"
        _write_store(
            self.store,
            [
                {
                    "path": self.SONG,
                    "kind": "loop",
                    "name": "Hook Loop",
                    "pos": 220.330556,
                    "size": "64.0",
                    "deleted_at": "2026-10-04T14:46:00-0600",
                },
                {
                    "path": self.SONG,
                    "kind": "cue",
                    "name": "Outro",
                    "pos": 168.126,
                    "size": None,
                    "deleted_at": "2026-10-04T15:00:00-0600",
                },
            ],
        )
        self.env = mock.patch.dict(
            os.environ, {"AUTOCUE_DELETED_MARKERS_PATH": str(self.store)}
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def test_deleted_loop_at_same_pos_filtered(self):
        cues, loops, msgs = DM.filter_deleted(
            [],
            [_poi("loop", 220.330556, "Hook Loop", beats=64)],
            self.SONG,
        )
        self.assertEqual(loops, [])
        self.assertEqual(len(msgs), 1)
        self.assertIn("loop", msgs[0])
        self.assertIn("220.331", msgs[0])

    def test_nearby_beyond_eps_kept(self):
        # 0.30 s away > POS_EPS 0.25
        kept_loop = _poi("loop", 220.330556 + 0.30, "Hook Loop", beats=64)
        cues, loops, msgs = DM.filter_deleted([], [kept_loop], self.SONG)
        self.assertEqual(loops, [kept_loop])
        self.assertEqual(msgs, [])

    def test_within_eps_filtered(self):
        near = _poi("loop", 220.330556 + 0.20, "Anything", beats=32)
        _, loops, msgs = DM.filter_deleted([], [near], self.SONG)
        self.assertEqual(loops, [])
        self.assertEqual(len(msgs), 1)

    def test_different_path_kept(self):
        loop = _poi("loop", 220.330556, "Hook Loop", beats=64)
        _, loops, msgs = DM.filter_deleted([], [loop], self.OTHER)
        self.assertEqual(loops, [loop])
        self.assertEqual(msgs, [])

    def test_cue_vs_loop_kind_distinction(self):
        # A cue at the deleted-loop position must survive; a loop at the
        # deleted-cue position must survive.
        cue_at_loop_pos = _poi("cue", 220.330556, "Hook")
        loop_at_cue_pos = _poi("loop", 168.126, "Outro Loop", beats=16)
        cues, loops, msgs = DM.filter_deleted(
            [cue_at_loop_pos], [loop_at_cue_pos], self.SONG
        )
        self.assertEqual(cues, [cue_at_loop_pos])
        self.assertEqual(loops, [loop_at_cue_pos])
        self.assertEqual(msgs, [])

    def test_deleted_cue_filtered(self):
        cues, loops, msgs = DM.filter_deleted(
            [_poi("cue", 168.126, "Outro")], [], self.SONG
        )
        self.assertEqual(cues, [])
        self.assertIn("cue", msgs[0])

    def test_list_for_path_returns_records(self):
        recs = DM.list_for_path(self.SONG)
        self.assertEqual(len(recs), 2)
        self.assertEqual(DM.list_for_path(self.OTHER), [])

    def test_missing_store_is_noop(self):
        missing = Path(self.td.name) / "nope.json"
        with mock.patch.dict(
            os.environ, {"AUTOCUE_DELETED_MARKERS_PATH": str(missing)}
        ):
            loop = _poi("loop", 220.3, "Hook Loop", beats=64)
            _, loops, msgs = DM.filter_deleted([], [loop], self.SONG)
            self.assertEqual(loops, [loop])
            self.assertEqual(msgs, [])


class PrepareSongCuesIntegrationTests(unittest.TestCase):
    """filter_deleted is the last hygiene step; exercise it with PreparedPoi."""

    def test_filter_drops_automatic_first_one_style_cue(self):
        with tempfile.TemporaryDirectory() as td:
            song = "/tmp/fake/song.flac"
            store = Path(td) / "deleted_markers.json"
            _write_store(
                store,
                [{"path": song, "kind": "cue", "name": "Beat Entry", "pos": 0.0}],
            )
            with mock.patch.dict(
                os.environ, {"AUTOCUE_DELETED_MARKERS_PATH": str(store)}
            ):
                first = _poi("cue", 0.05, "Beat Entry")  # within 0.25 of 0.0
                other = _poi("cue", 40.0, "Chorus")
                cues, loops, msgs = DM.filter_deleted([first, other], [], song)
                self.assertEqual([c.name for c in cues], ["Chorus"])
                self.assertEqual(len(msgs), 1)


if __name__ == "__main__":
    unittest.main()
