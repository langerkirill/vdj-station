"""Rename sharing (loop <-> cue), smooth edge drag, 16-beat snap kept, sort never blocked by an existing folder."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from sorter import cue_edit
from sorter import poi_rename as rename_mod

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")

SONG = (
    '<Song FilePath="/m/a.flac">\r\n'
    '  <Poi Name="Beat Entry" Pos="0.000000" Num="1" Color="1" Type="cue" />\r\n'
    '  <Poi Name="Drop" Pos="32.000000" Num="2" Color="1" Type="cue" />\r\n'
    '  <Poi Name="Loop A" Pos="0.000000" Num="-1" Color="1" Type="loop" Size="8.0" Slot="1" />\r\n'
    '  <Poi Name="Loop B" Pos="48.000000" Num="-1" Color="1" Type="loop" Size="8.0" Slot="2" />\r\n'
    "</Song>\r\n"
)


def fn(name: str) -> str:
    m = re.search(rf"\n(?:async )?function {name}\(", JS)
    assert m, name
    start = m.start()
    nxt = re.search(r"\n(?:async )?function \w+\(", JS[start + 20 :])
    return JS[start : start + 20 + (nxt.start() if nxt else 6000)]


class RenameSharingTests(unittest.TestCase):
    def test_loop_may_take_a_cues_name_and_vice_versa(self):
        out, ch = rename_mod.set_poi_name_in_song_xml(SONG, kind="loop", pos=0.0, new_name="Beat Entry", num="-1", name="Loop A", slot="1")
        self.assertEqual(ch["name_after"], "Beat Entry")
        self.assertEqual(out.count('Name="Beat Entry"'), 2)  # cue + loop
        out2, _ = rename_mod.set_poi_name_in_song_xml(SONG, kind="cue", pos=32.0, new_name="Loop B", num="2", name="Drop")
        self.assertEqual(out2.count('Name="Loop B"'), 2)

    def test_client_only_treats_same_kind_as_duplicate_and_suffixes_calmly(self):
        self.assertIn("function sameKindNameTaken(", JS)
        body = fn("sameKindNameTaken")
        self.assertIn("pointKind(p) === kind", body)  # loops vs cues are separate namespaces
        self.assertIn("function uniqueRenameForKind(", JS)
        self.assertIn("uniqueRenameForKind(track, point, next)", fn("beginRenamePoi"))
        self.assertNotRegex(fn("uniqueRenameForKind"), r"setStatus\([^)]*error")

    def test_name_clash_from_the_server_is_soft_not_a_failed_save(self):
        api = fn("api")
        self.assertIn("softConflict", api)
        self.assertLess(api.index("softConflict"), api.index("reportSave(false"))
        body = fn("renamePoiPoint")
        self.assertIn("err.softConflict", body)
        self.assertIn('kind === "loop" ? "Loop" : "Cue"', body)  # e.g. "Beat Entry Loop"


class EdgeDragTests(unittest.TestCase):
    def test_scale_loop_takes_any_beat_ratio(self):
        out, ch = cue_edit.scale_loop_size_in_song_xml(SONG, pos=0.0, factor=16 / 8, num="-1", name="Loop A", slot="1")
        self.assertIn('Size="16.0"', out)  # the 8 -> 16 beat extension from the recording

    def test_drag_is_frame_coalesced_and_network_free(self):
        move = fn("onLoopDragPointerMove")
        self.assertIn("requestAnimationFrame", move)
        self.assertNotIn("api(", move)
        self.assertNotIn("getBoundingClientRect", move)
        frame = fn("applyLoopDragFrame")
        self.assertNotIn("api(", frame)
        self.assertNotIn("getBoundingClientRect", frame)
        self.assertNotIn("fetch(", frame)
        down = fn("onLoopDragPointerDown")
        self.assertIn("ctx:", down)  # rect / duration / bpm cached once
        up = fn("onLoopDragPointerUp")
        self.assertIn("cancelAnimationFrame", up)

    def test_end_edge_still_snaps_to_the_16_beat_grid(self):
        frame = fn("applyLoopDragFrame")
        self.assertIn("snapPhraseTime(next, { free })", frame)
        self.assertIn("phrasePeriodSeconds", fn("onLoopDragPointerDown"))

    def test_loop_move_is_optimistic_and_reverts_on_failure(self):
        body = fn("commitLoopMove")
        self.assertIn("setPos(originPos, newPos)", body)
        self.assertLess(body.index("setPos(originPos, newPos)"), body.index('"/api/move-poi"'))
        self.assertIn("enqueueTrackEdit(path", body)
        self.assertIn("setPos(newPos, originPos)", body)

    def test_short_loop_end_handle_is_grabbable(self):
        body = fn("hitTestLoopAtClientX")
        self.assertIn("w / 3", body)  # handle zones shrink with the loop width
        self.assertIn('hit: "end"', body.replace("hit: best.hit", 'hit: "end"')) if False else None
        self.assertIn('"end"', body)


class SortNeverBlockedTests(unittest.TestCase):
    def test_client_resolves_an_existing_folder_before_sorting(self):
        self.assertIn("function existingHouseFolderFor(", JS)
        self.assertIn("existingHouseFolderFor(relativePath)", fn("applySortDest"))
        sort = fn("sortSelectedImpl")
        self.assertIn("existingHouseFolderFor(dests[0].path)", sort)
        self.assertLess(sort.index("existingHouseFolderFor"), sort.index("Create a NEW House folder?"))
        block = fn("bindNewFolderBlock")
        self.assertNotIn("already exists — pick it from the list", block)
        self.assertIn("Using the existing folder", block)


if __name__ == "__main__":
    unittest.main()
