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


def test_color_tables_empty_input():
    assert scoring.color_table(pd.DataFrame()).empty
    assert scoring.combo_table(pd.DataFrame()).empty
