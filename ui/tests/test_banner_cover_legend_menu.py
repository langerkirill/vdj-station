"""Recordings 4-6 UI items: one color legend, place-loop name+color, waveform delete menu,
transform-only drag overlay, album cover in the banner (read-only)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sorter import cover_art

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
TRANSPORT = (STATIC / "transport.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")


class ColorSchemeTests(unittest.TestCase):
    def test_scheme_is_the_one_true_scheme_and_each_color_appears_once(self):
        block = TRANSPORT[TRANSPORT.index("const CUE_COLOR_SCHEME = ["):]
        block = block[: block.index("];")]
        rows = re.findall(r'id: "(\w+)", name: "(\w+)", meaning: "([^"]+)"', block)
        self.assertEqual(
            rows,
            [
                ("blue", "Blue", "Melodic, no drums"),
                ("green", "Green", "Melodic + drums"),
                ("purple", "Purple", "Drums only"),
                ("yellow", "Yellow", "Drums + vocals"),
                ("orange", "Orange", "Voice, no drums"),
            ],
        )
        self.assertEqual(len({r[0] for r in rows}), 5)

    def test_legend_is_built_from_one_source_not_hand_written_html(self):
        m = re.search(r'<div class="cue-color-legend" id="cueColorLegend"[^>]*>(.*?)</div>', HTML, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "", "legend markup must be rendered from the scheme only")
        self.assertEqual(HTML.count('id="cueColorLegend"'), 1)
        self.assertIn("function renderCueColorLegend()", JS)
        self.assertIn("MusicSorterTransport.CUE_COLOR_SCHEME", JS)
        self.assertNotIn("Vocals with no drums", JS + HTML + TRANSPORT)
        self.assertNotIn("Melodic — no drums or vocals", JS + HTML + TRANSPORT)

    def test_legend_render_replaces_children_so_it_cannot_duplicate(self):
        i = JS.index("function renderCueColorLegend()")
        self.assertIn("host.replaceChildren()", JS[i : i + 400])


class PlaceLoopPopoverTests(unittest.TestCase):
    def test_bar_has_name_field_and_swatches(self):
        self.assertIn('id="placeLoopName"', HTML)
        self.assertIn('id="placeLoopSwatches"', HTML)

    def test_placement_posts_name_and_chosen_color(self):
        i = JS.index("async function placeLoopAtTime")
        body = JS[i : i + 4500]
        self.assertIn("name: wantName || null", body)
        self.assertIn("color: wantColor", body)
        self.assertNotIn('color: "green"', body)
        self.assertIn("function renderPlaceLoopSwatches()", JS)

    def test_typing_a_name_does_not_fire_hotkeys(self):
        i = JS.index("function bindPlaceLoopFields()")
        self.assertIn("e.stopPropagation()", JS[i : i + 700])


class WaveformDeleteMenuTests(unittest.TestCase):
    def test_context_menu_reuses_optimistic_delete_with_undo(self):
        self.assertIn('addEventListener("contextmenu", onWaveformContextMenu)', JS)
        i = JS.index("function onWaveformContextMenu")
        body = JS[i : i + 2600]
        self.assertIn("deleteCuePoint(point)", body)
        self.assertIn("hitTestCueAtClientX", body)
        j = JS.index("async function deleteCuePoint")
        self.assertIn("showUndoToast", JS[j : j + 2500])


class DragOverlayTests(unittest.TestCase):
    def test_overlay_is_transform_only_and_canvas_is_not_redrawn_per_frame(self):
        i = JS.index("function applyLoopDragFrame(drag)")
        j = JS.index("async function onLoopDragPointerUp")
        frame = JS[i:j]
        self.assertNotIn("drawWaveform()", frame)
        self.assertEqual(frame.count("updateDragOverlay(drag)"), 2)
        k = JS.index("function updateDragOverlay(drag)")
        upd = JS[k : JS.index("/* Right-click a cue or loop", k)]
        self.assertIn("translate3d", upd)
        for forbidden in ("getBoundingClientRect", "offsetWidth", "clientWidth", ".style.left", ".style.width", "fetch(", "api("):
            self.assertNotIn(forbidden, upd)

    def test_commit_on_mouseup_only(self):
        j = JS.index("async function onLoopDragPointerUp")
        up = JS[j : j + 3500]
        self.assertIn("removeDragOverlay()", up)
        self.assertIn("commitLoopMove", up)
        self.assertIn("commitCueMove", up)
        self.assertIn("commitLoopResize", up)

    def test_css_overlay_uses_will_change_transform(self):
        self.assertIn(".drag-overlay .do-el", CSS)
        self.assertIn("will-change: transform", CSS)


class CoverTests(unittest.TestCase):
    def test_banner_has_cover_img_with_placeholder_fallback(self):
        self.assertIn('id="trackArtImg"', HTML)
        self.assertIn("function renderTrackCover(track)", JS)
        self.assertIn("img.onerror", JS)
        self.assertIn("/api/cover?", JS)

    def test_endpoint_is_get_only_and_never_touches_the_database(self):
        app = (STATIC.parent / "app.py").read_text(encoding="utf-8")
        i = app.index('@app.get("/api/cover")')
        fn = app[i : app.index('@app.get("/api/audio")')]
        self.assertNotIn("VDJ_DATABASE", fn)
        self.assertNotIn("safe_rewrite", fn)
        self.assertNotIn("vdj_db_write", fn)

    def test_vdj_cache_match_strips_source_prefix_and_ignores_case_accents(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "IT-Nutia - Em Pessoa.jpg").write_bytes(b"jpg")
            (d / "IT-Other - Song.jpg").write_bytes(b"jpg")
            hit = cover_art.find_vdj_cached_cover("nutia", "Em Pessoa", d)
            self.assertEqual(hit.name, "IT-Nutia - Em Pessoa.jpg")
            self.assertIsNone(cover_art.find_vdj_cached_cover("Nobody", "Nothing", d))

    def test_filename_fallback_parses_artist_and_title(self):
        a, t = cover_art.artist_title_from_filename(Path("/m/018. Fulltone - Papercut (Original Mix).flac"))
        self.assertEqual((a, t), ("Fulltone", "Papercut (Original Mix)"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_embedded_art_is_extracted_to_cache_dir_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "lib"
            lib.mkdir()
            cache = root / "cache"
            png = root / "c.png"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(png)], check=True)
            flac = lib / "01. Art - Song.flac"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-i", str(png), "-map", "0", "-map", "1",
                 "-c:a", "flac", "-c:v", "mjpeg", "-disposition:v", "attached_pic", str(flac)],
                check=True,
            )
            before = sorted(p.name for p in lib.iterdir())
            hit = cover_art.find_cover(flac, covers_dir=root / "nocovers", cache_dir=cache)
            self.assertIsNotNone(hit)
            self.assertEqual(hit.parent, cache)
            self.assertGreater(hit.stat().st_size, 100)
            self.assertEqual(sorted(p.name for p in lib.iterdir()), before, "library folder must stay untouched")
            # a file with no art leaves a marker and returns None
            plain = lib / "02. No - Art.flac"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "flac", str(plain)], check=True)
            self.assertIsNone(cover_art.find_cover(plain, covers_dir=root / "nocovers", cache_dir=cache))
            self.assertTrue(list(cache.glob("*.none")))


if __name__ == "__main__":
    unittest.main()
