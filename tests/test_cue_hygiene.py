"""Kirill 2026-10-04 (round 2): half-entered vocals, end-of-song cues, loop anchoring,
duplicate cues, final-Chorus naming, drums-stop breaks."""
import unittest

from vdj_cuer.cue_hygiene import (
    anchor_late_loop,
    clean_break_before,
    dedupe_cues,
    drop_late_cues,
    fit_loop_beats,
    late_by_phrases,
    rename_final_chorus,
    resolve_half_entered,
    vocal_is_mid_word,
)
from vdj_cuer.cue_writer import PreparedPoi
from vdj_cuer.stem_evidence import StemProfile
from vdj_cuer.stem_naming import name_from_stems

FS = 0.25


def P(kind, pos, color, name="x", beats=None):
    return PreparedPoi(kind=kind, name=name, position=pos, color_name=color,
                       color_value="0", elements=[], length_beats=beats)


def vocal_profile(sung, seconds=200.0):
    """sung = [(start, end)] seconds where the vocal stem is loud."""
    n = int(seconds / FS)
    frames = [0.0] * n
    for a, b in sung:
        for i in range(int(a / FS), min(n, int(b / FS))):
            frames[i] = 0.8
    return StemProfile.from_frames(frames, FS)


class HalfEnteredVocalTests(unittest.TestCase):
    BEAT = 0.5  # 120 BPM -> 16-beat phrase = 8 s

    def test_vocal_already_sounding_at_the_cue_is_mid_word(self):
        v = vocal_profile([(30.0, 60.0)])
        self.assertTrue(vocal_is_mid_word(v, 40.0))

    def test_clean_onset_after_a_gap_is_not_mid_word(self):
        v = vocal_profile([(10.0, 20.0), (40.0, 60.0)])
        self.assertFalse(vocal_is_mid_word(v, 40.0))

    def test_cue_right_after_a_breath_inside_a_phrase_is_mid_word(self):
        v = vocal_profile([(30.0, 39.2), (40.0, 60.0)])  # 0.8 s breath, same phrase
        self.assertTrue(vocal_is_mid_word(v, 40.0))

    def test_mid_word_cue_moves_to_quiet_bar_within_two_bars(self):
        # Quiet until 46.0; cue at 48.0 (mid-word). 1 bar earlier = 46.0 (BEAT=0.5 -> 2 s).
        v = vocal_profile([(46.0, 80.0)])
        new_t, why = resolve_half_entered(v, 48.0, self.BEAT)
        self.assertEqual(new_t, 44.0)  # 2 bars earlier; 1 bar (46) still touches the onset
        self.assertIn("moved", why)

    def test_mid_word_cue_with_no_nearby_break_is_skipped(self):
        # Continuous vocal: no quiet bar within 2 bars -> SKIP new proposal
        # (TL 2026-10-05 03:46). Hand-kept cues are protected by not rewriting
        # those songs + deleted_markers, not by keep-on-fail.
        v = vocal_profile([(0.0, 100.0)])
        new_t, why = resolve_half_entered(v, 48.0, self.BEAT)
        self.assertIsNone(new_t)
        self.assertIn("skipped", why)
        self.assertIsNone(clean_break_before(v, 48.0, self.BEAT))

    def test_mid_word_break_farther_than_two_bars_is_skipped_not_yanked(self):
        # Quiet only far earlier; 2-bar cap must not yank — skip the bad proposal.
        v = vocal_profile([(30.0, 60.0)])
        new_t, why = resolve_half_entered(v, 48.0, self.BEAT)
        self.assertIsNone(new_t)
        self.assertIn("skipped", why)

    def test_clean_cue_is_untouched(self):
        v = vocal_profile([(40.0, 60.0)])
        self.assertEqual(resolve_half_entered(v, 40.0, self.BEAT), (40.0, ""))


class EndOfSongTests(unittest.TestCase):
    def test_cue_in_the_last_20s_is_dropped_but_a_real_blue_outro_stays(self):
        cues = [P("cue", 30.0, "green"), P("cue", 170.0, "green"), P("cue", 181.0, "green")]
        kept, dropped = drop_late_cues(cues, 200.0, 0.5)
        self.assertEqual([c.position for c in kept], [30.0, 170.0])
        self.assertEqual([c.position for c in dropped], [181.0])
        outro = [P("cue", 30.0, "green"), P("cue", 183.0, "blue", "Outro")]
        kept, _ = drop_late_cues(outro, 200.0, 0.5)  # 17 s of music left >= 15 s
        self.assertEqual(len(kept), 2)
        tiny = [P("cue", 30.0, "green"), P("cue", 190.0, "blue", "Outro")]
        kept, dropped = drop_late_cues(tiny, 200.0, 0.5)  # 10 s left: nothing to jump to
        self.assertEqual(len(dropped), 1)

    def test_last_8_bars_count_on_slow_tracks(self):
        beat = 60.0 / 80.0  # 8 bars = 32 beats = 24 s > 20 s
        kept, dropped = drop_late_cues([P("cue", 177.0, "green")], 200.0, beat)
        self.assertEqual(len(dropped), 1)


class DuplicateCueTests(unittest.TestCase):
    def test_two_cues_at_one_spot_keep_the_better_color(self):
        cues = [P("cue", 38.908, "yellow", "Beat Entry"), P("cue", 38.908, "green", "Drop"),
                P("cue", 83.9, "orange", "Wake")]
        kept, dropped = dedupe_cues(cues)
        self.assertEqual([(c.position, c.color_name) for c in kept], [(38.908, "yellow"), (83.9, "orange")])
        self.assertEqual(len(dropped), 1)


class LoopAnchorTests(unittest.TestCase):
    BEAT = 60.0 / 126  # 0.47619 s

    def test_gorgon_city_loops_two_phrases_late_move_onto_their_cues(self):
        cues = [0.0, 45.714, 76.190, 150.0]
        for late, cue in ((60.952, 45.714), (91.428, 76.190)):
            found = late_by_phrases(cues, late, self.BEAT)
            self.assertEqual(found[0], cue)
            start, beats, _k = anchor_late_loop(late, 32, self.BEAT, cues, 230.0)
            self.assertAlmostEqual(start, cue, places=3)
            self.assertGreaterEqual(beats, 16)

    def test_one_phrase_late_loop_131_style(self):
        cues = [16.126, 32.126, 120.126, 168.126]
        start, beats, k = anchor_late_loop(128.126, 64, 0.5, cues, 200.0)
        self.assertEqual((round(start, 3), k), (120.126, 1))
        self.assertEqual(beats, 64)  # 120.1 + 32 s still ends before the Outro cue at 168.1
        _, beats2, _ = anchor_late_loop(128.126, 64, 0.5, [16.126, 120.126, 140.0], 200.0)
        self.assertEqual(beats2, 32)  # 64 beats would run into the next cue: fitted down

    def test_loop_on_its_cue_or_not_a_whole_phrase_after_is_left_alone(self):
        cues = [0.0, 60.0]
        self.assertIsNone(late_by_phrases(cues, 60.1, 0.5))
        self.assertIsNone(late_by_phrases(cues, 63.0, 0.5))  # 6 beats after: mid-phrase, not "late"
        self.assertIsNone(late_by_phrases(cues, 60.0 + 3 * 8.0, 0.5))  # 3 phrases: another section

    def test_seam_failure_at_the_cue_keeps_the_loop_where_it_passed(self):
        cues = [0.0, 60.0]
        self.assertIsNone(anchor_late_loop(68.0, 32, 0.5, cues, 300.0, seam_ok=lambda s, b: False))

    def test_loop_lengths_are_16_32_64_never_8(self):
        self.assertEqual(fit_loop_beats(100.0, 64, 0.5, [100.0, 120.0], 300.0), 32)
        self.assertEqual(fit_loop_beats(100.0, 64, 0.5, [100.0, 110.0], 300.0), 16)
        self.assertIsNone(fit_loop_beats(100.0, 64, 0.5, [100.0, 103.0], 300.0))


class NamingTests(unittest.TestCase):
    def test_chorus_at_the_very_end_becomes_ending_or_outro(self):
        cues = [P("cue", 30.0, "green", "Groove"), P("cue", 170.0, "yellow", "Chorus")]
        out = rename_final_chorus(cues, 200.0, 0.5)
        self.assertEqual(out[-1].name, "Ending")
        cues[-1] = P("cue", 170.0, "blue", "Chorus")
        self.assertEqual(rename_final_chorus(cues, 200.0, 0.5)[-1].name, "Outro")
        mid = [P("cue", 30.0, "green", "Groove"), P("cue", 100.0, "yellow", "Chorus")]
        self.assertEqual(rename_final_chorus(mid, 400.0, 0.5)[-1].name, "Chorus")

    def test_green_drop_only_on_a_high_energy_step_up(self):
        cues = [P("cue", 0.0, "purple"), P("cue", 30.0, "green"), P("cue", 60.0, "green")]
        c, _ = name_from_stems(cues, [], color_values={}, first_one=0.0, drop_positions={60.0})
        self.assertEqual(c[2].name, "Drop")
        self.assertNotEqual(c[1].name, "Drop")

    def test_drums_stop_after_the_start_is_a_break_not_a_drop(self):
        from vdj_cuer.cue_writer import CueWriterMixin

        class Stub:
            pass

        stub = Stub()
        beat = 0.5
        n = 400
        kick = [0.0] * n  # 1 s frames
        for i in range(0, 100):
            kick[i] = 0.9
        # drums on for the first bar of the section at 100 s, then stop for 4 s
        for i in range(100, 102):
            kick[i] = 0.9
        stub._final_stem_profiles = {
            "kick": StemProfile.from_frames(kick, 1.0),
            "hihat": StemProfile.from_frames([0.0] * n, 1.0),
            "instruments": StemProfile.from_frames([0.5] * n, 1.0),
        }
        stub._final_stem_audio = "a.flac"
        self.assertTrue(CueWriterMixin._drums_stop_after_start(stub, "a.flac", 100.0, beat))
        self.assertFalse(CueWriterMixin._drums_stop_after_start(stub, "a.flac", 50.0, beat))


class CalibrationTests(unittest.TestCase):
    def test_clean_onset_after_real_gap_not_flagged_but_breath_is(self):
        self.assertFalse(vocal_is_mid_word(vocal_profile([(20.0, 36.0), (40.0, 80.0)]), 40.0))
        self.assertTrue(vocal_is_mid_word(vocal_profile([(20.0, 39.0), (40.0, 80.0)]), 40.0))

    def test_continuous_singing_flagged(self):
        v = vocal_profile([(20.0, 80.0)])
        self.assertTrue(vocal_is_mid_word(v, 40.0))

    def test_loop_may_end_exactly_on_next_cue(self):
        beat = 30.0 / 64  # 64 beats = 30 s
        self.assertEqual(fit_loop_beats(165.0, 64, beat, [195.0], 225.0), 64)


if __name__ == "__main__":
    unittest.main()
