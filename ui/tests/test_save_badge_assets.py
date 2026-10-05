"""Frontend contract: per-edit Saved/FAILED badges and House copy wording (static asset checks)."""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


class SaveBadgeAssets(unittest.TestCase):
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_saved_and_failed_badges_and_failure_list(self):
        self.assertIn("Saved ✓", self.js)
        self.assertIn("FAILED: ", self.js)
        self.assertIn("NOT saved", self.js)
        self.assertIn("saved === false", self.js)
        # every non-2xx (403/409/5xx), network error and saved:false goes through one path
        self.assertIn("if (!res.ok)", self.js)
        self.assertIn("saveFailureReason(err)", self.js)
        self.assertIn("reportSave(false, info, reason)", self.js)
        self.assertIn("Dismiss all", self.js)

    def test_house_copy_wording_and_no_oh_folders(self):
        self.assertIn("COPY TO House", self.js)
        self.assertIn("New folder in House", self.js)
        for text in (self.js, self.html):
            self.assertIsNone(re.search(r"\bOH [A-Z][a-z]+", text))
        self.assertIn("original", self.html.lower())

    def test_build_strings_match_app_py(self):
        app = (STATIC.parent / "app.py").read_text(encoding="utf-8")
        build = re.search(r'UI_BUILD = "([^"]+)"', app).group(1)
        self.assertIn(build, self.js)
        self.assertIn(build, self.html)


if __name__ == "__main__":
    unittest.main()
