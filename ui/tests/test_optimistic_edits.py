"""Quick edits (rename / color / delete) are optimistic: patch the DOM + model, save in
the background, serialize per track, never redraw the cue list on success."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")


def fn_body(name: str) -> str:
    start = JS.index(f"async function {name}(")
    m = re.search(r"\n(?:async )?function \w+\(", JS[start + 20 :])
    return JS[start : start + 20 + (m.start() if m else 4000)]


class OptimisticEditTests(unittest.TestCase):
    def test_saves_are_serialized_per_track(self):
        self.assertIn("function enqueueTrackEdit(path, task)", JS)
        self.assertIn("editChains", JS)
        for name in ("renamePoiPoint", "setCueColor"):
            self.assertIn("enqueueTrackEdit(path", fn_body(name), name)
        self.assertIn('enqueueTrackEdit(track.path, () => api("/api/delete-cue"', JS)

    def test_rename_patches_ui_before_any_await_and_never_redraws_list(self):
        body = fn_body("renamePoiPoint")
        self.assertLess(body.index("patchPointName("), body.index("enqueueTrackEdit("))
        self.assertLess(body.index('setRowSaveState(path, key, "saving")'), body.index("enqueueTrackEdit("))
        self.assertNotIn("renderCues()", body)
        self.assertNotIn("loadTracks(", body)
        # the name sent to the server is the one it currently has (earlier queued renames land first)
        self.assertIn("const sentName = point.name || null", body)
        self.assertIn("revert()", body)  # rollback on failure / refusal

    def test_color_patches_ui_first_and_rolls_back(self):
        body = fn_body("setCueColor")
        self.assertLess(body.index("patchPointColor("), body.index("enqueueTrackEdit("))
        self.assertNotIn("renderCues()", body)
        self.assertNotIn("selectEl.disabled = true", body)  # no blocking the control
        self.assertIn("revert()", body)

    def test_rename_commit_does_not_await_the_save_or_redraw(self):
        s = JS.index("function beginRenamePoi(")
        e = JS.index("async function renamePoiPoint(")
        block = JS[s:e]
        self.assertIn("putBack(unique);", block)
        self.assertNotIn("await renamePoiPoint", block)
        self.assertNotIn("renderCues()", block)  # cancel/save put the span back; no list rebuild

    def test_health_check_does_not_block_edits_every_time(self):
        body = fn_body("isVdjRunningFresh")
        self.assertIn("state.healthAt", body)
        self.assertIn("> 15000", body)

    def test_row_save_state_css_and_delete_pending(self):
        for phase in ("saving", "saved", "failed"):
            self.assertIn(f'.cue-row[data-save="{phase}"]::after', CSS)
        self.assertIn(".cue-row.pending-delete", CSS)
        self.assertIn('classList.add("pending-delete")', JS)
        self.assertIn('classList.remove("pending-delete")', JS)

    def test_failure_path_keeps_toast_and_failed_list(self):
        self.assertIn("state.failedEdits.push(", JS)  # reportSave(false, ...) unchanged
        self.assertIn("NOT saved", JS)  # red box keeps failed edits visible, with Retry

    def test_write_endpoints_are_sync_so_lock_waits_never_block_the_event_loop(self):
        app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        for fn in ("post_rename_poi", "post_set_cue_color", "post_delete_cue", "post_add_cue"):
            self.assertRegex(app, rf"\ndef {fn}\(")
            self.assertNotRegex(app, rf"async def {fn}\(")


if __name__ == "__main__":
    unittest.main()
