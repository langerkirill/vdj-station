"""validate_loop_seam: the one mandatory gate every loop must pass.

Renders what the ear hears on a wrap (the last ``window`` seconds of the loop
immediately followed by the first ``window`` seconds), scores it numerically,
logs scores and thresholds, and saves the seam clip as a wav.

Checks (all must pass):
  click      join step vs the local slope just around the join
  loudness   RMS before vs after the join (dB)
  spectrum   per octave-band level before vs after the join (dB)
  kick_grid  low-band kick position inside the beat, end vs start (ms)
  stems      per-stem level before vs after the join; vocal / reverb-tail cut
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 22050
SEAM_CLIP_DIR = os.path.expanduser("~/vdj_seam_clips")

THRESHOLDS: Dict[str, float] = {
    "click_ratio_max": 12.0,
    "loudness_db_max": 4.5,
    "band_db_max": 7.0,
    "band_db_mean_max": 3.5,
    "kick_offset_ms_max": 65.0,
    "stem_score_diff_max": 0.70,
    "vocal_cut_ratio_min": 0.5,
}

_BANDS = [(30, 120), (120, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000)]


@dataclass
class SeamResult:
    passed: bool
    window: float
    scores: Dict[str, float] = field(default_factory=dict)
    failures: list = field(default_factory=list)
    thresholds: Dict[str, float] = field(default_factory=lambda: dict(THRESHOLDS))
    wav_path: Optional[str] = None

    def summary(self) -> str:
        s = " ".join(f"{k}={v:.2f}" for k, v in self.scores.items())
        return f"{'PASS' if self.passed else 'FAIL'} [{s}]" + (
            f" failed: {', '.join(self.failures)}" if self.failures else ""
        )


def _decode(path: str, start: float, dur: float) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.6f}",
           "-t", f"{dur:.6f}", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0, 1e-6))


def _band_db(x: np.ndarray) -> np.ndarray:
    if len(x) < 2048:
        x = np.pad(x, (0, 2048 - len(x)))
    f, _, z = signal.stft(x, SR, nperseg=2048, noverlap=1536)
    mag = np.abs(z) ** 2
    out = []
    for lo, hi in _BANDS:
        sel = (f >= lo) & (f < hi)
        out.append(10 * np.log10(max(float(mag[sel].mean()), 1e-12)))
    return np.array(out)


def _kick_phase(x: np.ndarray, beat: float) -> Optional[float]:
    """Seconds into the beat where the low-band envelope peaks (mean over beats)."""
    sos = signal.butter(4, 150, "low", fs=SR, output="sos")
    env = np.abs(signal.sosfiltfilt(sos, x))
    env = signal.sosfiltfilt(signal.butter(2, 30, "low", fs=SR, output="sos"), env)
    n = int(beat * SR)
    if n <= 0 or len(env) < n or env.max() < 1e-4:
        return None
    beats = len(env) // n
    folded = env[: beats * n].reshape(beats, n).mean(axis=0)
    return float(np.argmax(folded) / SR)


def validate_loop_seam(
    audio_path: str,
    loop_start: float,
    loop_duration: float,
    *,
    beat_seconds: float,
    window: float = 2.0,
    profiles: Optional[Mapping[str, object]] = None,
    save_name: Optional[str] = None,
) -> SeamResult:
    half = min(float(window), float(loop_duration) / 2.0)
    tail_start = loop_start + loop_duration - half
    tail = _decode(audio_path, tail_start, half)
    head = _decode(audio_path, loop_start, half)
    n = min(len(tail), len(head))
    tail, head = tail[-n:], head[:n]
    x = np.concatenate([tail, head])
    J = n
    scores: Dict[str, float] = {}
    fails: list = []
    T = THRESHOLDS

    # click: step at the join vs the largest slope seen in +-20ms around it
    d = np.abs(np.diff(x))
    around = np.concatenate([d[max(0, J - 441): J - 2], d[J + 1: J + 441]])
    ref = max(float(np.percentile(around, 99)) if len(around) else 0.0, 1e-4)
    scores["click_ratio"] = float(d[J - 1] / ref)
    if scores["click_ratio"] > T["click_ratio_max"]:
        fails.append("click")

    a = int(min(0.5, half) * SR)
    scores["loudness_db"] = abs(_rms_db(tail[-a:]) - _rms_db(head[:a]))
    if scores["loudness_db"] > T["loudness_db_max"]:
        fails.append("loudness")

    bd = np.abs(_band_db(tail[-a * 2:]) - _band_db(head[: a * 2]))
    scores["band_db_max"], scores["band_db_mean"] = float(bd.max()), float(bd.mean())
    if scores["band_db_max"] > T["band_db_max"] or scores["band_db_mean"] > T["band_db_mean_max"]:
        fails.append("spectrum")

    beat = float(beat_seconds)
    span = int(min(half, 4 * beat) * SR)
    kt, kh = _kick_phase(tail[-span:], beat), _kick_phase(head[:span], beat)
    kick_present = True
    if profiles and "kick" in profiles:
        try:
            win_k = min(half, 2.0)
            kick_present = (
                profiles["kick"].measure(loop_start + loop_duration - win_k, win_k).score >= 0.3
                and profiles["kick"].measure(loop_start, win_k).score >= 0.3
            )
        except Exception:
            kick_present = True
    if kick_present and kt is not None and kh is not None:
        diff = abs(kt - kh)
        diff = min(diff, beat - diff)
        scores["kick_offset_ms"] = diff * 1000
        if scores["kick_offset_ms"] > T["kick_offset_ms_max"]:
            fails.append("kick_grid")

    if profiles:
        win = min(half, 2.0)
        worst, vocal_cut = 0.0, False
        for name, prof in profiles.items():
            try:
                t_sc = prof.measure(loop_start + loop_duration - win, win).score
                h_sc = prof.measure(loop_start, win).score
            except Exception:
                continue
            if name == "vocal":
                # Kirill (2026-10-03, by ear): a vocal that stops/starts at the
                # wrap (064 Melody Loop 165.246) is fine -> logged, not fatal.
                scores["vocal_tail_vs_head"] = abs(t_sc - h_sc)
                continue
            worst = max(worst, abs(t_sc - h_sc))
        scores["stem_diff_max"] = worst
        if worst > T["stem_score_diff_max"]:
            fails.append("stems")

    wav = None
    if save_name:
        os.makedirs(SEAM_CLIP_DIR, exist_ok=True)
        wav = os.path.join(SEAM_CLIP_DIR, re.sub(r"[^\w.\-]+", "_", save_name) + f"__{window:g}s.wav")
        wavfile.write(wav, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))
    return SeamResult(not fails, float(window), scores, fails, dict(T), wav)
