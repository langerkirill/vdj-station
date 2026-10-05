import unittest

from vdj_cuer.stem_evidence import StemProfile, measure_stem_evidence


def _profiles(vocal, instruments=None, n=None):
    n = n or len(vocal)
    instruments = instruments or [0.5] * n
    return {
        "vocal": StemProfile.from_frames(vocal, 1.0),
        "instruments": StemProfile.from_frames(instruments, 1.0),
        "kick": StemProfile.from_frames([0.8] * n, 1.0),
    }


class RelativeVocalRuleTests(unittest.TestCase):
    def test_short_phrase_counts_relative_to_the_songs_own_vocal_level(self):
        # Quiet-ish vocal stem (peak 0.2 vs instruments 0.5) with a 12 s sung phrase.
        vocal = [0.0] * 100
        for i in range(40, 52):
            vocal[i] = 0.2
        profiles = _profiles(vocal)
        yes = measure_stem_evidence(profiles, 38.0, 16.0, [], strict_drums=False)
        no = measure_stem_evidence(profiles, 5.0, 16.0, [], strict_drums=False)
        self.assertIn("vocals", yes.elements)
        self.assertNotIn("vocals", no.elements)

    def test_cue_lookahead_finds_a_phrase_that_starts_after_the_onset_window(self):
        vocal = [0.0] * 100
        for i in range(60, 80):
            vocal[i] = 0.3
        profiles = _profiles(vocal)
        onset_only = measure_stem_evidence(profiles, 40.0, 8.0, [], strict_drums=False)
        section = measure_stem_evidence(
            profiles, 40.0, 8.0, [], strict_drums=False, vocal_lookahead=4.0
        )
        self.assertNotIn("vocals", onset_only.elements)
        self.assertIn("vocals", section.elements)

    def test_melody_bleed_vocal_stem_stays_off(self):
        # Memories-like: vocal stem peak ~0.21 of the instruments' peak.
        vocal = [0.105] * 100
        profiles = _profiles(vocal, instruments=[0.5] * 100)
        ev = measure_stem_evidence(profiles, 20.0, 16.0, [], strict_drums=False)
        self.assertNotIn("vocals", ev.elements)

    def test_near_silent_vocal_stem_is_no_vocal_at_all(self):
        # Quiet bleed: p95 0.05 in absolute terms, even with a loud-looking relative burst.
        vocal = [0.0] * 100
        for i in range(30, 70):
            vocal[i] = 0.05
        profiles = _profiles(vocal, instruments=[0.2] * 100)  # ratio 0.25, abs 0.05
        ev = measure_stem_evidence(profiles, 40.0, 16.0, [], strict_drums=False, vocal_lookahead=4.0)
        self.assertNotIn("vocals", ev.elements)

    def test_vocal_stem_far_below_instruments_is_no_vocal_at_all(self):
        vocal = [0.0] * 100
        for i in range(30, 70):
            vocal[i] = 0.12  # absolute ok (>= 0.08) but only 12% of the instruments
        profiles = _profiles(vocal, instruments=[1.0] * 100)
        ev = measure_stem_evidence(profiles, 40.0, 16.0, [], strict_drums=False)
        self.assertNotIn("vocals", ev.elements)

    def test_bliss_like_steady_stem_is_no_vocal_but_modulated_voice_is(self):
        # Bliss/Kaiserkraft/Nyctophobia: loud vocal stem (abs/ratio pass) but unmodulated
        # (index ~0.7); Cheyenne (1.08), Control (1.25), Ed Marquis (1.6) are real voices.
        import dataclasses

        def run(modulation, ratio=0.317):
            vocal = [0.0] * 100
            for i in range(30, 70):
                vocal[i] = 0.092
            profiles = _profiles(vocal, instruments=[0.092 / ratio] * 100)
            profiles["vocal"] = dataclasses.replace(
                profiles["vocal"], voice_modulation=modulation
            )
            return measure_stem_evidence(profiles, 40.0, 16.0, [], strict_drums=False)

        self.assertNotIn("vocals", run(0.72).elements)
        self.assertIn("vocals", run(1.08).elements)
        self.assertIn("vocals", run(None).elements)  # not measured never blocks
        self.assertNotIn("vocals", run(1.2, ratio=0.26).elements)  # ratio floor 0.28

    def test_track_level_instrumental_flag_silences_the_vocal_stem(self):
        from vdj_cuer.vocal_gate import apply_track_vocal_gate, track_is_instrumental

        vocal = [0.3] * 100
        profiles = _profiles(vocal)
        self.assertTrue(track_is_instrumental({"has_vocals": False}, "/x/010. Bliss.flac"))
        self.assertTrue(track_is_instrumental({"song_structure": {"instrumental": True}}))
        self.assertFalse(track_is_instrumental({"has_vocals": True}))
        gated = apply_track_vocal_gate(profiles, {"has_vocals": False}, "/x/010. Bliss.flac")
        ev = measure_stem_evidence(gated, 40.0, 16.0, [], strict_drums=False, vocal_lookahead=4.0)
        self.assertNotIn("vocals", ev.elements)
        self.assertIn("vocals", measure_stem_evidence(profiles, 40.0, 16.0, [], strict_drums=False).elements)

    def test_vocal_and_chorus_names_only_on_vocal_colored_markers(self):
        from dataclasses import dataclass

        from vdj_cuer.stem_naming import enforce_vocal_names

        @dataclass(frozen=True)
        class P:
            name: str
            color_name: str
            position: float = 0.0

        cues, loops = enforce_vocal_names(
            [P("Chorus", "green"), P("Vocal Drop", "yellow"), P("Hook", "purple")],
            [P("Chorus Loop", "green"), P("Vocal Loop", "orange")],
        )
        self.assertEqual([c.name for c in cues][1], "Vocal Drop")
        self.assertFalse(any("chorus" in c.name.lower() or "hook" in c.name.lower() for c in (cues[0], cues[2])))
        self.assertEqual(loops[1].name, "Vocal Loop")
        self.assertNotIn("Chorus", loops[0].name)

    def test_inst_in_file_name_is_a_hard_no_vocals_hint(self):
        import os
        from unittest import mock

        from vdj_cuer.vocal_gate import track_is_instrumental

        with mock.patch.dict(os.environ, {"AUTOCUE_VOCAL_OVERRIDES": "/nonexistent/x.json"}):
            for name in (
                "Bust (Inst) - 98 BM for AutoCue.wav",
                "012. Artist - Title Instrumental.flac",
                "Artist - Title INST.mp3",
                "Artist - Title (instrumental mix).flac",
            ):
                self.assertTrue(track_is_instrumental({}, "/m/" + name), name)
            for name in ("Artist - Instinct.flac", "Artist - Instant Love.flac", "Artist - Insta.flac"):
                self.assertFalse(track_is_instrumental({}, "/m/" + name), name)


if __name__ == "__main__":
    unittest.main()
