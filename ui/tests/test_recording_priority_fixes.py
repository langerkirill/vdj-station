"""Recording bugs (regression rows 30-31): edits after a rename, the color picker, resize to the
song end, and the wrong song name flashing in the header. Static wiring checks; the live click-throughs
are the Playwright scripts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")


def body_of(name: str) -> str:
    m = re.search(rf"(?:async )?function {name}\(", JS)
    assert m, name
    start = m.start()
    nxt = re.search(r"\n(?:async )?function \w+\(", JS[m.end():])
    return JS[start : m.end() + (nxt.start() if nxt else len(JS))]


class EditsAfterRenameTests(unittest.TestCase):
    def test_every_function_is_defined_once(self):
        names = re.findall(r"^(?:async )?function (\w+)\(", JS, re.M)
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], "a later duplicate silently replaces the earlier function")

    def test_row_actions_resolve_the_live_marker_by_stable_identity(self):
        for fn in ("renamePoiPoint", "setCueColor", "deleteCuePoint", "commitLoopResize"):
            self.assertIn("resolveLivePoint(", body_of(fn), fn)
        self.assertIn("modelPoint(path, key)", body_of("resolveLivePoint") + JS)

    def test_queued_tasks_re_read_identity_when_they_run(self):
        self.assertIn("function refreshBodyIdentity(", JS)
        self.assertGreaterEqual(JS.count("refreshBodyIdentity("), 3)

    def test_loop_scale_buttons_go_through_the_per_track_queue_and_are_optimistic(self):
        scale = body_of("scaleLoopPoint")
        self.assertNotIn('api("/api/scale-loop"', scale)
        self.assertIn("commitLoopResize(", scale)
        resize = body_of("commitLoopResize")
        self.assertIn("enqueueTrackEdit(path", resize)
        self.assertLess(resize.index("setSize(newBeats)"), resize.index("enqueueTrackEdit(path"))

    def test_server_summary_does_not_overwrite_newer_queued_edits(self):
        self.assertIn("editPending.get(path)", body_of("applyCueSummaryToTrack"))

    def test_stale_refusals_are_retried_on_the_server(self):
        src = (Path(__file__).resolve().parents[1] / "sorter" / "safe_write.py").read_text()
        self.assertIn("STALE_RETRIES", src)


class ColorPickerTests(unittest.TestCase):
    def test_native_select_is_gone_and_options_apply_by_key(self):
        self.assertNotIn("cue-color-select", JS)
        menu = body_of("openCueColorMenu")
        self.assertIn("pointerdown", menu)
        self.assertIn("preventDefault", menu)
        self.assertIn("setCueColor(", menu)
        self.assertIn("modelPoint(", menu)  # the clicked marker is found by key, not by list index
        self.assertIn(".cue-color-menu", CSS)

    def test_list_does_not_re_render_under_an_open_menu(self):
        self.assertIn("renderCuesDeferred", JS)


class ClampTests(unittest.TestCase):
    def test_client_clamps_to_song_end(self):
        self.assertIn("function clampLoopBeatsToSongEnd(", JS)
        self.assertIn("already reaches the end of the song", JS)


class HeaderStaleResponseTests(unittest.TestCase):
    def test_list_reload_does_not_bring_back_a_previous_selection(self):
        load = body_of("loadTracks")
        self.assertIn("selectGenAtStart", load)
        self.assertIn("state.trackGen === selectGenAtStart", load)

    def test_header_exposes_the_path_it_shows(self):
        self.assertIn("root.dataset.path = track.path", JS)


class OneMarkerModelTests(unittest.TestCase):
    def test_counts_come_from_the_marker_list_everywhere(self):
        self.assertIn("function markerCountsOf(", JS)
        changed = body_of("markersChanged")
        for needle in ("t.cues.cue_count = n.cues", "t.cues.loop_count = n.loops", "renderReviewPanel()", "updatePlayerMetaOnly(", "renderTrackList()"):
            self.assertIn(needle, changed)
        for fn in ("applyCueSummaryToTrack", "patchPointName", "patchPointColor", "commitLoopResize", "commitCueMove"):
            self.assertIn("markersChanged(", body_of(fn) + (JS if fn == "commitLoopResize" else ""), fn)
        self.assertIn("markerCountsOf(track)", body_of("renderReviewPanel"))

    def test_row_buttons_find_their_marker_by_key_not_by_list_index(self):
        self.assertIn("function pointForRowEl(", JS)
        self.assertNotIn("const point = points[idx];", body_of("renderCues"))

    def test_delete_toast_names_the_real_marker_and_unnamed_get_a_position(self):
        d = body_of("deleteCuePoint")
        self.assertIn("at ${fmtTime(point.pos)}", d)

    def test_loading_the_list_keeps_songs_with_queued_edits(self):
        self.assertIn("(editPending.get(t.path) || 0) > 0", body_of("loadTracks"))


class SaveFeedbackTests(unittest.TestCase):
    def test_every_edit_shows_saving_at_once_and_all_saved_when_the_queue_drains(self):
        q = body_of("enqueueTrackEdit")
        self.assertIn("renderSaveBadges()", q)
        self.assertIn("lastAllSavedAt", q)
        badges = body_of("renderSaveBadges")
        self.assertIn("Saving ${pendingN} edit", badges)
        self.assertIn("All changes saved", badges)

    def test_failed_edits_are_kept_retryable_and_persisted(self):
        self.assertIn("async function retryFailedEdit(", JS)
        self.assertIn("ms.failedEdits.v1", JS)
        self.assertIn("apiPath: info.apiPath", JS)
        self.assertIn("kept here until you retry or dismiss", JS)


class PajamathonBannerTests(unittest.TestCase):
    def test_house_never_shows_not_in_pajamathon_or_the_skip_button(self):
        self.assertIn('isHouseProfile() && model.state === "missing"', JS)
        self.assertIn('body[data-profile="house"] #skipBtn { display: none; }', CSS)


if __name__ == "__main__":
    unittest.main()
