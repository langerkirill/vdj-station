"""Hide Best-for-set rows that use a song already played in the live set."""

from __future__ import annotations

from pathlib import Path

from sorter.live_set_played import (
    annotate_best_items_live_played,
    filter_best_items_hide_live_played,
    iter_pajamathon_played_audio,
    keys_for_history_play,
    live_set_played_block_keys,
    practice_label_block_keys,
    transition_uses_live_played,
)


def _touch_audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_played_folder_scan_skips_stems_and_non_pajamathon(tmp_path: Path):
    sets = tmp_path / "Sets"
    paj = sets / "Pajamathon 2026" / "Played"
    other = sets / "Moon 2026" / "Played"
    _touch_audio(paj / "149. Reina - Burn.flac")
    (paj / "149. Reina - Burn.flac.vdjstems").write_bytes(b"stems")
    _touch_audio(paj / "Changes Trimmed.wav")
    _touch_audio(other / "Should Ignore.flac")
    _touch_audio(sets / "Pajamathon 2026" / "001. still in crate.m4a")

    found = {p.name for p in iter_pajamathon_played_audio(sets_root=sets)}
    assert found == {"149. Reina - Burn.flac", "Changes Trimmed.wav"}


def test_practice_label_matches_numbered_played_filename():
    played = live_set_played_block_keys(
        played_paths=[
            Path("/Sets/Pajamathon 2026/Played/149. Reina - Burn.flac"),
            Path("/Sets/Pajamathon 2026/Played/030. Changes Trimmed.wav"),
            Path("/Sets/Pajamathon 2026/Played/029. Naked Trimmed.wav"),
        ],
        history_keys=set(),
    )
    assert practice_label_block_keys("Reina - Burn") & played
    assert practice_label_block_keys("Changes Trimmed") & played
    assert practice_label_block_keys("Naked Trimmed") & played
    assert not (practice_label_block_keys("Sade - Kiss of Life") & played)


def test_same_title_different_artist_is_not_live_played():
    played = live_set_played_block_keys(
        played_paths=[
            Path(
                "/Sets/Pajamathon 2026/Played/436. Le Yora - EVERYTHING IN ITS RIGHT PLACE.flac"
            )
        ],
        history_keys=set(),
    )
    assert practice_label_block_keys("Le Yora - EVERYTHING IN ITS RIGHT PLACE") & played
    assert not (
        practice_label_block_keys("JEWELS - EVERYTHING IN ITS RIGHT PLACE") & played
    )


def test_unicode_title_matches_played_filename():
    played = live_set_played_block_keys(
        played_paths=[
            Path(
                "/Sets/Pajamathon 2026/Played/129. Ecstasy (Meridiyün Zoukified).wav"
            )
        ],
        history_keys=set(),
    )
    assert practice_label_block_keys("Ecstasy (Meridiyun Zoukified)") & played
    assert practice_label_block_keys("Ecstasy (Meridiyün Zoukified)") & played


def test_duplicated_artist_in_played_filename_matches_short_label():
    played = live_set_played_block_keys(
        played_paths=[
            Path(
                "/Sets/Pajamathon 2026/Played/247. Blvck Skyle - Blvck Skyle - LUV'N ATTN.wav"
            )
        ],
        history_keys=set(),
    )
    assert practice_label_block_keys("Blvck Skyle - LUV'N ATTN") & played
    assert not (practice_label_block_keys("Kaysha - LUV'N ATTN") & played)


def test_history_tags_keep_artist_with_title():
    keys = keys_for_history_play(
        "/Played/091. Little Dragon - High.flac",
        "Little Dragon",
        "High",
    )
    assert practice_label_block_keys("Little Dragon - High") & keys
    assert not (practice_label_block_keys("Of The Trees - High") & keys)


def test_history_tag_artist_does_not_override_different_filename_artist():
    keys = keys_for_history_play(
        "/Played/436. Le Yora - EVERYTHING IN ITS RIGHT PLACE.flac",
        "JEWELS",
        "EVERYTHING IN ITS RIGHT PLACE",
    )
    assert practice_label_block_keys("Le Yora - EVERYTHING IN ITS RIGHT PLACE") & keys
    assert not (
        practice_label_block_keys("JEWELS - EVERYTHING IN ITS RIGHT PLACE") & keys
    )


def test_history_featured_credit_still_matches():
    keys = keys_for_history_play(
        "/Played/425. Saqi - Falling Under Water.flac",
        "Saqi, MARYA STARK",
        "Falling Under Water",
    )
    assert practice_label_block_keys("Saqi, MARYA STARK - Falling Under Water") & keys
    assert practice_label_block_keys("Saqi - Falling Under Water") & keys


def test_history_artist_title_matches_practice_label():
    history = practice_label_block_keys("Sasha Keable - heal something")
    played = live_set_played_block_keys(
        played_paths=[],
        history_keys=history,
    )
    assert transition_uses_live_played(
        "Sasha Keable - heal something",
        "Ari Lennox, J. Cole - Shea Butter Baby",
        played,
    )
    assert not transition_uses_live_played(
        "Podval Capella - Risk",
        "Blvck Skyle - Kizombeat",
        played,
    )


def test_hide_drops_row_if_either_side_was_played():
    items = [
        {
            "id": 1,
            "from_track": "Reina - Burn",
            "to_track": "Break 1",
        },
        {
            "id": 2,
            "from_track": "Naked Trimmed",
            "to_track": "BRANKO, Mr. Carmack, Nonku Phiri - Let Me Go",
        },
        {
            "id": 3,
            "from_track": "Podval Capella - Risk",
            "to_track": "Blvck Skyle - Kizombeat",
        },
    ]
    keys = live_set_played_block_keys(
        played_paths=[
            Path("/Played/149. Reina - Burn.flac"),
            Path("/Played/029. Naked Trimmed.wav"),
        ],
        history_keys=set(),
    )
    visible, hidden = filter_best_items_hide_live_played(
        items, hide=True, played_keys=keys
    )
    assert [row["id"] for row in visible] == [3]
    assert hidden == 2
    assert visible[0]["live_played"] is False

    shown, still_hidden = filter_best_items_hide_live_played(
        items, hide=False, played_keys=keys
    )
    assert [row["id"] for row in shown] == [1, 2, 3]
    assert still_hidden == 2
    assert shown[0]["live_played"] is True
    assert shown[1]["from_live_played"] is True
    assert shown[1]["to_live_played"] is True or shown[1]["from_live_played"] is True


def test_annotate_does_not_mutate_input():
    items = [{"id": 1, "from_track": "Reina - Burn", "to_track": "X"}]
    keys = live_set_played_block_keys(
        played_paths=[Path("/Played/149. Reina - Burn.flac")],
        history_keys=set(),
    )
    out = annotate_best_items_live_played(items, played_keys=keys)
    assert out[0]["live_played"] is True
    assert "live_played" not in items[0]
