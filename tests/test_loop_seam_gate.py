"""validate_loop_seam is the mandatory gate: bad seams are refused."""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

from vdj_cuer.loop_seam_dsp import validate_loop_seam, SEAM_CLIP_DIR

SR = 22050
BEAT = 0.5


def _kick_track(seconds, level=0.6):
    t = np.arange(int(seconds * SR)) / SR
    x = 0.05 * np.sin(2 * np.pi * 440 * t)
    for b in np.arange(0, seconds, BEAT):
        i = int(b * SR)
        n = int(0.15 * SR)
        k = np.arange(n) / SR
        x[i:i + n] += level * np.sin(2 * np.pi * (60 * np.exp(-k * 12)) * k * 3) * np.exp(-k * 18)
    return x[: int(seconds * SR)].astype(np.float32)


def _write(x):
    path = os.path.join(tempfile.mkdtemp(), "t.wav")
    wavfile.write(path, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))
    return path


class SeamGateTests(unittest.TestCase):
    def test_clean_periodic_loop_passes(self):
        path = _write(_kick_track(40))
        r = validate_loop_seam(path, 8.0, 16 * BEAT, beat_seconds=BEAT, window=2.0, save_name="unit_good")
        self.assertTrue(r.passed, r.summary())
        self.assertTrue(r.wav_path and os.path.exists(r.wav_path))

    def test_loop_with_level_jump_and_click_is_refused(self):
        x = _kick_track(40)
        x[int(16 * SR):] *= 0.1          # energy drop at the loop end region
        x[int(15.9 * SR):int(16 * SR)] += 0.5  # DC step into the join
        path = _write(x)
        r = validate_loop_seam(path, 8.0, 16 * BEAT, beat_seconds=BEAT, window=2.0, save_name="unit_bad")
        self.assertFalse(r.passed)
        self.assertTrue({"loudness", "click", "spectrum"} & set(r.failures), r.summary())

    def test_off_grid_kick_is_refused(self):
        x = _kick_track(40)
        # shift the tail half a beat late so the kick lands off the grid across the join
        a, b = int(14 * SR), int(16 * SR)
        x[a:b] = np.roll(x[a:b], int(0.2 * SR))
        path = _write(x)
        r = validate_loop_seam(path, 8.0, 16 * BEAT, beat_seconds=BEAT, window=2.0)
        self.assertIn("kick_grid", r.failures, r.summary())


if __name__ == "__main__":
    unittest.main()
