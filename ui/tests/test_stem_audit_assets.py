"""Structural + helper checks for the Music Sorter Stems tab."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

try:
    from tests.js_assets import SHIPPED_JS, UI_STATIC, read_shipped_js, read_static
except ImportError:
    from js_assets import SHIPPED_JS, UI_STATIC, read_shipped_js, read_static


def _node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node failed ({proc.returncode}): {proc.stderr or proc.stdout}"
        )
    return json.loads(proc.stdout.strip() or "{}")


class StemAuditAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (UI_STATIC / "index.html").read_text(encoding="utf-8")
        cls.css = (UI_STATIC / "styles.css").read_text(encoding="utf-8")
        cls.app = read_static("app.js")
        cls.state = read_static("state.js")
        cls.js = read_shipped_js()
        cls.app_py = (Path(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8"
        )

    def test_stems_tab_and_panel_exist(self) -> None:
        self.assertIn('data-mode="stems"', self.html)
        self.assertIn(">Stems<", self.html)
        self.assertIn('id="stemsPanel"', self.html)
        self.assertIn('id="stemsScanAllBtn"', self.html)
        self.assertIn('id="stemsScanCuedBtn"', self.html)
        self.assertIn("Scan new", self.html)
        self.assertIn("skip_scanned", self.app)
        self.assertIn("unscanned_count", self.app)
        self.assertIn('id="stemsCheckBtn"', self.html)
        self.assertIn('id="stemsCancelBtn"', self.html)
        self.assertIn('id="stemsDeleteBtn"', self.html)
        self.assertIn('id="stemsBrokenTable"', self.html)
        self.assertIn("vocal-layer holes", self.html.lower().replace("—", " "))

    def test_stems_js_is_a_shipped_classic_script(self) -> None:
        self.assertIn("stems.js", SHIPPED_JS)
        self.assertIn("/static/stems.js", self.html)
        self.assertLess(self.html.index("stems.js"), self.html.index("app.js"))
        self.assertIn("MusicSorterStems", self.app)
        self.assertIn("function isStemsMode", self.state)
        self.assertIn("function isStemsMode", self.app)

    def test_stems_apis_are_wired(self) -> None:
        for needle in (
            "/api/stems/inventory",
            "/api/stems/audit",
            "/api/stems/check",
            "/api/stems/delete",
        ):
            self.assertIn(needle, self.app)
            self.assertIn(needle, self.app_py)
        self.assertIn('@app.post("/api/stems/audit")', self.app_py)
        self.assertIn('@app.get("/api/stems/audit/{job_id}")', self.app_py)
        self.assertIn("start_stem_audit_job", self.app_py)

    def test_mode_ui_hides_sort_chrome(self) -> None:
        self.assertIn('document.body.classList.toggle("mode-stems"', self.app)
        self.assertIn("mode !== \"stems\"", self.app.replace("'", '"'))
        self.assertIn("body.mode-stems #stemsPanel", self.css)
        self.assertIn("body.mode-stems #playerPanel", self.css)
        self.assertIn("isStemsMode()", self.app)
        self.assertIn('requestedMode === "stems"', self.app)
        self.assertIn("Unknown stem", self.app)
        self.assertRegex(self.app, r"r\.broken\s*\|\|\s*r\.error")

    def test_ui_build_is_bumped_and_matches(self) -> None:
        js_build = re.search(r'const UI_BUILD = "([^"]+)"', self.app)
        py_build = re.search(r'^UI_BUILD = "([^"]+)"', self.app_py, re.M)
        self.assertTrue(js_build and py_build)
        self.assertEqual(js_build.group(1), py_build.group(1))
        self.assertIn(js_build.group(1), self.html)

    def test_format_helpers(self) -> None:
        stems_path = json.dumps(str(UI_STATIC / "stems.js"))
        out = _node(
            f"""
const S = require({stems_path});
const busy = S.stemsJobBusy({{ id: "1", status: "running" }});
const idle = S.stemsJobBusy({{ id: "1", status: "ok" }});
const rel = S.formatStemRel(
  "/Users/k/Music/DJ/Music/Zouk/Chill/song.flac",
  "/Users/k/Music/DJ/Music"
);
const holes = S.formatHoles([{{ start: 12, end: 24.5 }}, {{ start: 90, end: 101 }}]);
const clock = S.formatStemClock(125.4);
console.log(JSON.stringify({{ busy, idle, rel, holes, clock }}));
"""
        )
        self.assertTrue(out["busy"])
        self.assertFalse(out["idle"])
        self.assertEqual(out["rel"], "Zouk/Chill/song.flac")
        self.assertIn("0:12–0:24", out["holes"])
        self.assertEqual(out["clock"], "2:05")
