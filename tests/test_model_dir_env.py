"""VDJ_AUTOCUE_MODEL_DIR: one shared cue model for every installed AutoCue release.

The Mac auto-updater installs each version into its own release folder and deletes old ones;
8788's retraining must keep writing the SAME model across releases (it holds Kirill's
corrections), so with the variable set every save goes to that folder. Until the first save
there, loading falls back to the model shipped with the code. Unset: the shipped model, as
before.
"""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vdj_cuer.ml.model as M


class ModelDirTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(M)

    def test_default_is_the_shipped_model(self):
        with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": ""}):
            importlib.reload(M)
            self.assertEqual(M.DEFAULT_ARTIFACT, M.SHIPPED_ARTIFACT)
            self.assertEqual(M.model_load_path(), M.SHIPPED_ARTIFACT)
            self.assertEqual(M.DEFAULT_ARTIFACT.parent.name, "artifacts")

    def test_shared_folder_with_a_model_is_loaded_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cue_bar_clf.joblib").write_bytes(b"x")
            with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": tmp}):
                importlib.reload(M)
                self.assertEqual(M.DEFAULT_ARTIFACT, Path(tmp) / "cue_bar_clf.joblib")
                self.assertEqual(M.model_load_path(), Path(tmp) / "cue_bar_clf.joblib")

    def test_empty_shared_folder_loads_shipped_but_saves_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": tmp}):
                importlib.reload(M)
                self.assertEqual(M.model_load_path(), M.SHIPPED_ARTIFACT)
                self.assertEqual(M.DEFAULT_ARTIFACT, Path(tmp) / "cue_bar_clf.joblib")
                # callers that pass the default path explicitly (ingest, CLI) get the same
                self.assertEqual(M.model_load_path(M.DEFAULT_ARTIFACT), M.SHIPPED_ARTIFACT)

    def test_an_explicit_other_path_is_never_redirected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": tmp}):
                importlib.reload(M)
                other = Path(tmp) / "elsewhere.joblib"
                self.assertEqual(M.model_load_path(other), other)

    def test_retrain_saves_into_the_shared_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": tmp}):
                importlib.reload(M)
                fake_joblib = type("J", (), {"dump": staticmethod(lambda obj, p: Path(p).write_bytes(b"model"))})
                model = M.CueBarModel(cue_clf=None, loop_clf=None, feature_names=M.MODEL_FEATURE_NAMES)
                with patch.dict("sys.modules", {"joblib": fake_joblib}):
                    dest = M.save_cue_bar_model(model)
                self.assertEqual(dest, Path(tmp) / "cue_bar_clf.joblib")
                self.assertEqual(dest.read_bytes(), b"model")
                self.assertTrue((Path(tmp) / "cue_bar_clf.json").is_file())
                self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                                 ["cue_bar_clf.joblib", "cue_bar_clf.json"])  # no temp files left
                self.assertNotIn(str(M.SHIPPED_ARTIFACT.parent), str(dest))

    def test_a_failed_save_keeps_the_previous_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cue_bar_clf.joblib").write_bytes(b"previous")
            with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": tmp}):
                importlib.reload(M)

                def dump(obj, p):
                    Path(p).write_bytes(b"cut-o")
                    raise OSError("disk full")

                fake_joblib = type("J", (), {"dump": staticmethod(dump)})
                model = M.CueBarModel(cue_clf=None, loop_clf=None, feature_names=M.MODEL_FEATURE_NAMES)
                with patch.dict("sys.modules", {"joblib": fake_joblib}):
                    with self.assertRaises(OSError):
                        M.save_cue_bar_model(model)
                self.assertEqual((Path(tmp) / "cue_bar_clf.joblib").read_bytes(), b"previous")
                self.assertEqual([p.name for p in Path(tmp).iterdir()], ["cue_bar_clf.joblib"])

    def test_an_unreadable_model_loads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "elsewhere.joblib"
            bad.write_bytes(b"x")

            def load(p):
                raise EOFError("truncated")

            with patch.dict("sys.modules", {"joblib": type("J", (), {"load": staticmethod(load)})}):
                with self.assertLogs("vdj_cuer.ml.model", level="WARNING"):
                    self.assertIsNone(M.load_cue_bar_model(bad))

    def test_an_unusable_shared_model_falls_back_to_the_shipped_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cue_bar_clf.joblib").write_bytes(b"x")
            with patch.dict(os.environ, {"VDJ_AUTOCUE_MODEL_DIR": tmp}):
                importlib.reload(M)
                shipped = {"schema": M.SCHEMA, "feature_names": list(M.MODEL_FEATURE_NAMES),
                           "cue_clf": "c", "loop_clf": "l"}
                other_features = {**shipped, "feature_names": ["old_feature"]}
                for shared_payload in (EOFError("truncated"), other_features):
                    def load(p, shared_payload=shared_payload):
                        if Path(p) == M.SHIPPED_ARTIFACT:
                            return shipped
                        if isinstance(shared_payload, Exception):
                            raise shared_payload
                        return shared_payload

                    with patch.dict("sys.modules", {"joblib": type("J", (), {"load": staticmethod(load)})}):
                        with self.assertLogs("vdj_cuer.ml.model", level="WARNING"):
                            model = M.load_cue_bar_model()
                    self.assertIsNotNone(model)
                    self.assertEqual(model.cue_clf, "c")


if __name__ == "__main__":
    unittest.main()
