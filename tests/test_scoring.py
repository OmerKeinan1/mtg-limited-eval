"""Tests for derived scoring (set-relative score + best color)."""

from __future__ import annotations

import pandas as pd

from mtg_eval import scoring


def _df(rows):
    return pd.DataFrame(rows)


def test_score_is_set_relative_percentile():
    df = _df(
        [
            {"name": "A", "gih_wr": 0.50, "gih_games": 5000, "colors": "W", "my_eval": ""},
            {"name": "B", "gih_wr": 0.55, "gih_games": 5000, "colors": "U", "my_eval": ""},
            {"name": "C", "gih_wr": 0.60, "gih_games": 5000, "colors": "B", "my_eval": ""},
        ]
    )
    out = scoring.add_score(df)
    scores = dict(zip(out["name"], out["score"]))
    # Highest GIH WR gets the top percentile.
    assert scores["C"] > scores["B"] > scores["A"]
    assert float(scores["C"]) == 100.0


def test_low_sample_cards_get_blank_score():
    df = _df(
        [
            {"name": "BigSample", "gih_wr": 0.55, "gih_games": 5000, "colors": "R", "my_eval": ""},
            {"name": "TinySample", "gih_wr": 0.99, "gih_games": 10, "colors": "R", "my_eval": ""},
            {"name": "NoData", "gih_wr": None, "gih_games": None, "colors": "R", "my_eval": ""},
        ]
    )
    out = scoring.add_score(df)
    scores = dict(zip(out["name"], out["score"]))
    assert pd.isna(scores["TinySample"])
    assert pd.isna(scores["NoData"])
    assert not pd.isna(scores["BigSample"])


def test_best_color_ranks_by_gih_wr_when_no_manual():
    df = _df(
        [
            {"name": "w1", "gih_wr": 0.60, "colors": "W", "my_eval": ""},
            {"name": "u1", "gih_wr": 0.50, "colors": "U", "my_eval": ""},
            {"name": "b1", "gih_wr": 0.45, "colors": "B", "my_eval": ""},
            {"name": "r1", "gih_wr": 0.48, "colors": "R", "my_eval": ""},
            {"name": "g1", "gih_wr": 0.52, "colors": "G", "my_eval": ""},
        ]
    )
    out = scoring.best_color(df)
    assert out.iloc[0]["color"] == "W"  # highest avg GIH WR


def test_best_color_incorporates_manual_rating():
    # Green is weak on data but Omer rates it highly; manual should pull it up.
    df = _df(
        [
            {"name": "w1", "gih_wr": 0.60, "colors": "W", "my_eval": "2"},
            {"name": "u1", "gih_wr": 0.55, "colors": "U", "my_eval": "2"},
            {"name": "b1", "gih_wr": 0.50, "colors": "B", "my_eval": "2"},
            {"name": "r1", "gih_wr": 0.50, "colors": "R", "my_eval": "2"},
            {"name": "g1", "gih_wr": 0.45, "colors": "G", "my_eval": "10"},
        ]
    )
    out = scoring.best_color(df)
    rank = dict(zip(out["color"], out["rank_score"]))
    # With manual factored in, Green's rank beats its pure-data (last) position.
    assert rank["G"] > rank["B"]
    # The manual average is surfaced.
    g_row = out[out["color"] == "G"].iloc[0]
    assert float(g_row["avg_my_eval"]) == 10.0


def test_multicolor_card_counts_toward_each_color():
    df = _df([{"name": "wu", "gih_wr": 0.58, "colors": "WU", "my_eval": ""}])
    out = scoring.best_color(df)
    w = out[out["color"] == "W"].iloc[0]
    u = out[out["color"] == "U"].iloc[0]
    assert w["n_cards"] == 1 and u["n_cards"] == 1
