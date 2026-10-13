"""Training labels survive when a song's cues cannot be read.

An AutoCue package installed by the Mac updater has no Music Sorter UI of its own; the sorter
names its UI with VDJ_SORTER_UI_DIR. If that is missing (or the database cannot be read), ingest
used to read "no cues" and drop the song's labels and retrain the model. Unreadable is not empty:
the labels and the model must stay exactly as they are.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from vdj_cuer.ml.dataset import load_jsonl, write_jsonl
from vdj_cuer.ml.features import FEATURE_NAMES
from vdj_cuer.ml.ingest import ingest_cued_track

TRACK = "/music/Cues/Cues Sorted/Pop/song.flac"


def _row(track: str, t: float, is_cue: int) -> dict:
    row = {name: 0.0 for name in FEATURE_NAMES}
    row.update(timestamp=t, track_id=track, is_cue=is_cue, is_loop_start=0)
    return row


class KeepLabelsTests(unittest.TestCase):
    def _labels(self, tmp: str) -> Path:
        dest = Path(tmp) / "bars.jsonl"
        write_jsonl(dest, [_row(TRACK, 0.0, 1), _row(TRACK, 8.0, 0), _row("/music/Cues/Cues Sorted/Pop/other.flac", 0.0, 1)])
        return dest

    def test_no_sorter_ui_keeps_labels_and_model(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as empty_ui:
            dest = self._labels(tmp)
            before = dest.read_bytes()
            # "sorter" not importable, whatever other tests put on sys.path before (None in
            # sys.modules makes the import fail)
            with patch.dict(os.environ, {"VDJ_SORTER_UI_DIR": empty_ui}), \
                    patch.dict(sys.modules, {"sorter": None, "sorter.relocate": None}), \
                    patch("vdj_cuer.ml.ingest.train_from_labels_file") as train:
                result = ingest_cued_track(TRACK, labels_path=dest, model_path=Path(tmp) / "m.joblib")
            self.assertTrue(result["reason"].startswith("summary_unavailable"), result)
            self.assertEqual(dest.read_bytes(), before)
            train.assert_not_called()

    def test_unreadable_cues_keep_labels_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = self._labels(tmp)
            fake = types.ModuleType("sorter.relocate")

            def summarize_cues(path, database_path=None):
                raise OSError("database.xml is being written")

            fake.summarize_cues = summarize_cues
            pkg = types.ModuleType("sorter")
            pkg.relocate = fake
            with patch.dict(sys.modules, {"sorter": pkg, "sorter.relocate": fake}), \
                    patch("vdj_cuer.ml.dataset.ensure_ui_on_path"), \
                    patch("vdj_cuer.ml.ingest.train_from_labels_file") as train:
                result = ingest_cued_track(TRACK, labels_path=dest, model_path=Path(tmp) / "m.joblib")
            self.assertTrue(result["reason"].startswith("summary_unavailable"), result)
            self.assertEqual(len([r for r in load_jsonl(dest) if r["track_id"] == TRACK]), 2)
            train.assert_not_called()


if __name__ == "__main__":
    unittest.main()
