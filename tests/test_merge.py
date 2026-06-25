"""Tests for the merge layer.

The single most important test in this repo is
``test_preserves_my_eval_on_rerun``. If it fails, the tool is broken: that is
the whole product.
"""

from __future__ import annotations

import pandas as pd
import pytest

from mtg_eval import merge as merge_mod
from mtg_eval import seventeen_lands


def _scryfall_row(name, cn, set_code="tst", rarity="common", colors="R", cmc=1.0):
    return {
        "set": set_code,
        "collector_number": str(cn),
        "name": name,
        "mana_cost": "{R}",
        "cmc": cmc,
        "type_line": "Instant",
        "rarity": rarity,
        "colors": colors,
        "oracle_text": "Deal 3 damage to any target.",
        "scryfall_uri": "https://scryfall.com/x",
        "layout": "normal",
        "booster": True,
    }


def _scryfall_df():
    return pd.DataFrame(
        [
            _scryfall_row("Lightning Bolt", 1),
            _scryfall_row("Giant Growth", 2, colors="G"),
            _scryfall_row("Counterspell", 3, colors="U"),
        ]
    )


def _empty_17lands():
    return seventeen_lands.empty_frame()


# --- The critical invariant ---------------------------------------------------


def test_preserves_my_eval_on_rerun(tmp_path):
    out = tmp_path / "TST.csv"

    # First run: no existing eval, write the file.
    first = merge_mod.merge(_scryfall_df(), _empty_17lands(), out)
    merge_mod.write_output(first, out)

    # Omer hand-edits: Lightning Bolt gets my_eval 8.5 with a note.
    edited = pd.read_csv(out, dtype=str)
    mask = edited["name"] == "Lightning Bolt"
    edited.loc[mask, "my_eval"] = "8.5"
    edited.loc[mask, "my_notes"] = "premium removal"
    edited.to_csv(out, index=False)

    # Second run: Scryfall/17Lands data is identical, but the rerun must NOT
    # clobber the hand-entered eval.
    second = merge_mod.merge(_scryfall_df(), _empty_17lands(), out)

    bolt = second[second["name"] == "Lightning Bolt"].iloc[0]
    assert str(bolt["my_eval"]) == "8.5"
    assert str(bolt["my_notes"]) == "premium removal"


def test_preserves_eval_when_card_data_changes(tmp_path):
    """Eval survives even when Scryfall/17Lands values change between runs."""
    out = tmp_path / "TST.csv"
    first = merge_mod.merge(_scryfall_df(), _empty_17lands(), out)
    merge_mod.write_output(first, out)

    edited = pd.read_csv(out, dtype=str)
    edited.loc[edited["name"] == "Counterspell", "my_eval"] = "7"
    edited.to_csv(out, index=False)

    # Rerun with changed oracle text + real 17Lands stats.
    changed = _scryfall_df()
    changed.loc[changed["name"] == "Counterspell", "oracle_text"] = "CHANGED"
    sl = pd.DataFrame(
        [{"join_name": "counterspell", "gih_wr": 0.61, "oh_wr": 0.58,
          "iwd": 0.04, "ata": 3.1, "alsa": 4.2}]
    )

    second = merge_mod.merge(changed, sl, out)
    cs = second[second["name"] == "Counterspell"].iloc[0]
    assert str(cs["my_eval"]) == "7"
    assert cs["oracle_text"] == "CHANGED"
    assert float(cs["gih_wr"]) == pytest.approx(0.61)


def test_preserves_eval_by_collector_number_not_name(tmp_path):
    """If a card is renamed but keeps its collector number, eval is preserved."""
    out = tmp_path / "TST.csv"
    first = merge_mod.merge(_scryfall_df(), _empty_17lands(), out)
    merge_mod.write_output(first, out)

    edited = pd.read_csv(out, dtype=str)
    edited.loc[edited["collector_number"] == "1", "my_eval"] = "9"
    edited.to_csv(out, index=False)

    renamed = _scryfall_df()
    renamed.loc[renamed["collector_number"] == "1", "name"] = "Lightning Bolt (errata)"

    second = merge_mod.merge(renamed, _empty_17lands(), out)
    row = second[second["collector_number"] == "1"].iloc[0]
    assert str(row["my_eval"]) == "9"


# --- Merge failure is a hard stop --------------------------------------------


def test_merge_raises_when_existing_file_missing_key_columns(tmp_path):
    out = tmp_path / "TST.csv"
    # A malformed existing file with no collector_number column.
    pd.DataFrame([{"name": "Lightning Bolt", "my_eval": "8.5"}]).to_csv(out, index=False)

    with pytest.raises(merge_mod.MergeError):
        merge_mod.merge(_scryfall_df(), _empty_17lands(), out)


# --- 17Lands join -------------------------------------------------------------


def test_17lands_joins_by_name(tmp_path):
    sl = pd.DataFrame(
        [{"join_name": "lightning bolt", "gih_wr": 0.6, "oh_wr": 0.55,
          "iwd": 0.05, "ata": 2.0, "alsa": 3.0}]
    )
    combined = merge_mod.merge(_scryfall_df(), sl, tmp_path / "none.csv")
    bolt = combined[combined["name"] == "Lightning Bolt"].iloc[0]
    assert float(bolt["gih_wr"]) == pytest.approx(0.6)
    # A card with no 17Lands data has blank stats.
    gg = combined[combined["name"] == "Giant Growth"].iloc[0]
    assert pd.isna(gg["gih_wr"]) or gg["gih_wr"] == ""


def test_17lands_joins_dfc_by_front_name(tmp_path):
    scry = pd.DataFrame([_scryfall_row("Front Face // Back Face", 5)])
    sl = pd.DataFrame(
        [{"join_name": "front face", "gih_wr": 0.52, "oh_wr": 0.5,
          "iwd": 0.01, "ata": 5.0, "alsa": 6.0}]
    )
    combined = merge_mod.merge(scry, sl, tmp_path / "none.csv")
    row = combined.iloc[0]
    assert float(row["gih_wr"]) == pytest.approx(0.52)


# --- Output shape -------------------------------------------------------------


def test_output_column_order(tmp_path):
    combined = merge_mod.merge(_scryfall_df(), _empty_17lands(), tmp_path / "none.csv")
    assert list(combined.columns) == merge_mod.OUTPUT_COLUMNS


def test_sort_order_rarity_then_color(tmp_path):
    df = pd.DataFrame(
        [
            _scryfall_row("Common Red", 1, rarity="common", colors="R", cmc=2),
            _scryfall_row("Mythic White", 2, rarity="mythic", colors="W", cmc=5),
            _scryfall_row("Rare Blue", 3, rarity="rare", colors="U", cmc=1),
        ]
    )
    out = merge_mod.merge(df, _empty_17lands(), tmp_path / "none.csv")
    assert list(out["name"]) == ["Mythic White", "Rare Blue", "Common Red"]
