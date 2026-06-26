"""Tests for 17Lands normalization (no network)."""

from __future__ import annotations

import pandas as pd

from mtg_eval import seventeen_lands as sl


def test_normalize_frame_maps_fields():
    raw = pd.DataFrame(
        [
            {
                "name": "Lightning Bolt",
                "ever_drawn_win_rate": 0.6,
                "opening_hand_win_rate": 0.58,
                "drawn_win_rate": 0.59,
                "drawn_improvement_win_rate": 0.03,
                "avg_pick": 2.1,
                "avg_seen": 3.4,
                "ever_drawn_game_count": 12345,
                "extra_field": "ignored",
            }
        ]
    )
    out = sl._normalize_frame(raw)
    assert list(out.columns) == [
        "join_name", "gih_wr", "oh_wr", "gd_wr", "iwd", "ata", "alsa", "gih_games",
    ]
    row = out.iloc[0]
    assert row["join_name"] == "lightning bolt"
    assert row["gih_wr"] == 0.6
    assert row["ata"] == 2.1
    assert row["gih_games"] == 12345


def test_normalize_frame_empty():
    out = sl._normalize_frame(pd.DataFrame())
    assert out.empty
    assert "join_name" in out.columns


def test_normalize_frame_passthrough_cached():
    cached = pd.DataFrame(
        [{"join_name": "x", "gih_wr": 0.5, "oh_wr": 0.5, "iwd": 0.0,
          "ata": 1.0, "alsa": 1.0}]
    )
    out = sl._normalize_frame(cached)
    assert out.iloc[0]["join_name"] == "x"


def test_normalize_name():
    assert sl.normalize_name("  Lightning Bolt ") == "lightning bolt"
    assert sl.normalize_name(None) == ""
