"""Tests for derived scoring (LSV grade + 17Lands color/combo tables)."""

from __future__ import annotations

import pandas as pd

from mtg_eval import scoring


def _df(rows):
    return pd.DataFrame(rows)


def test_gih_to_lsv_bands():
    assert scoring.gih_to_lsv(0.70) == 5.0
    assert scoring.gih_to_lsv(0.62) == 5.0
    assert scoring.gih_to_lsv(0.58) == 3.5
    assert scoring.gih_to_lsv(0.545) == 2.5
    assert scoring.gih_to_lsv(0.50) == 1.0
    assert scoring.gih_to_lsv(0.40) == 0.0


def test_add_score_is_lsv_grade():
    df = _df(
        [
            {"name": "Bomb", "gih_wr": 0.65, "gih_games": 5000},
            {"name": "Good", "gih_wr": 0.575, "gih_games": 5000},
            {"name": "Filler", "gih_wr": 0.53, "gih_games": 5000},
        ]
    )
    out = scoring.add_score(df)
    scores = dict(zip(out["name"], out["score"]))
    assert scores["Bomb"] == 5.0
    assert scores["Good"] == 3.5
    assert scores["Filler"] == 2.0


def test_low_sample_cards_get_blank_score():
    df = _df(
        [
            {"name": "BigSample", "gih_wr": 0.60, "gih_games": 5000},
            {"name": "TinySample", "gih_wr": 0.99, "gih_games": 10},
            {"name": "NoData", "gih_wr": None, "gih_games": None},
        ]
    )
    out = scoring.add_score(df)
    scores = dict(zip(out["name"], out["score"]))
    assert pd.isna(scores["TinySample"])
    assert pd.isna(scores["NoData"])
    assert scores["BigSample"] == 4.0


def _colors_df():
    return pd.DataFrame(
        [
            {"color_name": "All Decks", "short_name": "All", "wins": 0, "games": 0,
             "win_rate": None, "is_summary": True},
            {"color_name": "Mono-White", "short_name": "W", "wins": 60, "games": 100,
             "win_rate": 0.60, "is_summary": False},
            {"color_name": "Mono-Blue", "short_name": "U", "wins": 50, "games": 100,
             "win_rate": 0.50, "is_summary": False},
            {"color_name": "Azorius (WU)", "short_name": "WU", "wins": 560, "games": 1000,
             "win_rate": 0.56, "is_summary": False},
            {"color_name": "Boros (RW)", "short_name": "WR", "wins": 580, "games": 1000,
             "win_rate": 0.58, "is_summary": False},
            {"color_name": "Azorius + Splash", "short_name": "WU+", "wins": 5, "games": 10,
             "win_rate": 0.50, "is_summary": False},
        ]
    )


def test_color_table_ranks_mono_colors():
    out = scoring.color_table(_colors_df())
    assert list(out["color"]) == ["W", "U"]  # best first, splash/summary excluded
    assert out.iloc[0]["win_rate"] == 0.60


def test_combo_table_names_guilds_and_excludes_splash():
    out = scoring.combo_table(_colors_df())
    # Best pair first; splash (WU+) excluded.
    assert out.iloc[0]["archetype"] == "Boros (RW)"
    assert "Azorius (WU)" in set(out["archetype"])
    assert not out["archetype"].str.contains(r"\+").any()


def test_combat_tricks_filters_instants_and_flash():
    df = pd.DataFrame([
        {"name": "Pump Spell", "type_line": "Instant", "oracle_text": "Target creature gets +3/+3.",
         "colors": "G", "cmc": 1, "rarity": "common", "score": 3.0, "gih_wr": 0.55,
         "scryfall_uri": "u", "image_url": "i"},
        {"name": "Flash Beast", "type_line": "Creature - Beast", "oracle_text": "Flash. Trample.",
         "colors": "G", "cmc": 4, "rarity": "uncommon", "score": 3.5, "gih_wr": 0.57,
         "scryfall_uri": "u", "image_url": "i"},
        {"name": "Sorcery Draw", "type_line": "Sorcery", "oracle_text": "Draw two cards.",
         "colors": "U", "cmc": 3, "rarity": "common", "score": 2.5, "gih_wr": 0.52,
         "scryfall_uri": "u", "image_url": "i"},
        {"name": "Flashback Guy", "type_line": "Sorcery", "oracle_text": "Flashback {3}{R}.",
         "colors": "R", "cmc": 2, "rarity": "common", "score": 2.0, "gih_wr": 0.5,
         "scryfall_uri": "u", "image_url": "i"},
    ])
    out = scoring.combat_tricks(df)
    names = list(out["name"])
    assert "Pump Spell" in names and "Flash Beast" in names
    assert "Sorcery Draw" not in names      # not instant, no flash
    assert "Flashback Guy" not in names     # flashback != flash keyword
    tt = dict(zip(out["name"], out["trick_type"]))
    assert tt["Pump Spell"] == "Instant"
    assert tt["Flash Beast"] == "Flash"


def test_color_tables_empty_input():
    assert scoring.color_table(pd.DataFrame()).empty
    assert scoring.combo_table(pd.DataFrame()).empty


def test_color_rgb_pair_blends():
    w = scoring.color_rgb("W")
    u = scoring.color_rgb("U")
    wu = scoring.color_rgb("WU")
    assert wu["red"] == round((w["red"] + u["red"]) / 2, 4)
    # Unknown / colorless falls back to gold, not an error.
    assert set(scoring.color_rgb("").keys()) == {"red", "green", "blue"}


def _cards():
    return pd.DataFrame(
        [
            {"name": "W common A", "colors": "W", "rarity": "common", "gih_wr": 0.58,
             "score": 3.5, "scryfall_uri": "u", "image_url": "i"},
            {"name": "U common B", "colors": "U", "rarity": "common", "gih_wr": 0.60,
             "score": 4.0, "scryfall_uri": "u", "image_url": "i"},
            {"name": "WU gold", "colors": "WU", "rarity": "common", "gih_wr": 0.62,
             "score": 5.0, "scryfall_uri": "u", "image_url": "i"},
            {"name": "Colorless", "colors": "", "rarity": "common", "gih_wr": 0.55,
             "score": 3.0, "scryfall_uri": "u", "image_url": "i"},
            {"name": "B common", "colors": "B", "rarity": "common", "gih_wr": 0.70,
             "score": 5.0, "scryfall_uri": "u", "image_url": "i"},
            {"name": "WU uncommon", "colors": "WU", "rarity": "uncommon", "gih_wr": 0.63,
             "score": 5.0, "scryfall_uri": "u", "image_url": "i"},
        ]
    )


def test_guild_top_cards_filters_by_color_identity():
    out = scoring.guild_top_cards(_cards(), "WU", "common", 5)
    names = list(out["name"])
    # WU, W, U and colorless qualify; off-color black does not.
    assert "B common" not in names
    assert "WU gold" in names and "Colorless" in names
    # Ranked by GIH WR desc -> WU gold (0.62) first.
    assert names[0] == "WU gold"


def test_guild_top_cards_respects_rarity_and_limit():
    out = scoring.guild_top_cards(_cards(), "WU", "uncommon", 5)
    assert list(out["name"]) == ["WU uncommon"]
    capped = scoring.guild_top_cards(_cards(), "WU", "common", 2)
    assert len(capped) == 2
