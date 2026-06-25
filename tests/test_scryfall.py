"""Tests for Scryfall normalization (no network: uses fixture-shaped dicts)."""

from __future__ import annotations

from mtg_eval import scryfall


def test_card_to_row_simple():
    card = {
        "set": "dsk",
        "collector_number": "92",
        "name": "Acrobatic Cheerleader",
        "mana_cost": "{1}{W}",
        "cmc": 2.0,
        "type_line": "Creature - Human Survivor",
        "oracle_text": "When this enters,\ndraw a card.",
        "colors": ["W"],
        "rarity": "common",
        "scryfall_uri": "https://scryfall.com/x",
        "layout": "normal",
        "booster": True,
    }
    row = scryfall._card_to_row(card)
    assert row["name"] == "Acrobatic Cheerleader"
    assert row["colors"] == "W"
    assert "\n" not in row["oracle_text"]  # newlines flattened for CSV


def test_card_to_row_dfc_combines_faces():
    card = {
        "set": "mid",
        "collector_number": "50",
        "layout": "transform",
        "type_line": "Creature // Creature",
        "colors": None,
        "rarity": "rare",
        "booster": True,
        "card_faces": [
            {"name": "Front", "mana_cost": "{1}{R}", "oracle_text": "A", "colors": ["R"]},
            {"name": "Back", "mana_cost": "", "oracle_text": "B", "colors": ["G"]},
        ],
    }
    row = scryfall._card_to_row(card)
    assert row["name"] == "Front // Back"
    assert row["oracle_text"] == "A // B"
    assert set(row["colors"]) == set("RG")


def test_is_basic_land():
    assert scryfall._is_basic_land({"type_line": "Basic Land - Forest"})
    assert not scryfall._is_basic_land({"type_line": "Land"})
    assert not scryfall._is_basic_land({"type_line": "Creature - Elf"})


def test_fetch_set_filters_non_booster_and_basics(monkeypatch, tmp_path):
    raw = [
        {"set": "tst", "collector_number": "1", "name": "Real Card",
         "type_line": "Creature", "rarity": "common", "colors": ["G"],
         "booster": True, "layout": "normal", "cmc": 2.0},
        {"set": "tst", "collector_number": "2", "name": "Token",
         "type_line": "Token Creature", "rarity": "common", "colors": ["G"],
         "booster": False, "layout": "token", "cmc": 0.0},
        {"set": "tst", "collector_number": "3", "name": "Forest",
         "type_line": "Basic Land - Forest", "rarity": "common", "colors": [],
         "booster": True, "layout": "normal", "cmc": 0.0},
    ]
    monkeypatch.setattr(scryfall, "fetch_raw_cards", lambda *a, **k: raw)

    df = scryfall.fetch_set("tst", tmp_path)
    assert list(df["name"]) == ["Real Card"]

    df_basics = scryfall.fetch_set("tst", tmp_path, include_basics=True)
    assert set(df_basics["name"]) == {"Real Card", "Forest"}
