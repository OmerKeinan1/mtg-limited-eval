"""Derived scoring.

* ``score`` -- an LSV-style 0-5 Limited grade derived from GIH WR, the
  gold-standard 17Lands power signal. LSV (Luis Scott-Vargas) grades Limited
  cards 0.0-5.0 in half-point steps: 5.0 is a format-defining bomb, good rares
  sit at 4.0+, good commons around 3.0-3.5, filler near 2.0, and unplayables
  near 0. We translate GIH WR into that scale with fixed win-rate bands
  (calibrated to typical Premier Draft baselines). Cards with too few games are
  left ungraded.

* ``color_table`` / ``combo_table`` -- best color and best two-color archetype,
  taken straight from 17Lands' aggregate color-ratings data (objective win
  rates), not inferred from card averages.
"""

from __future__ import annotations

import pandas as pd

# A GIH WR number needs enough games to be meaningful.
MIN_GIH_GAMES = 200

# GIH WR -> LSV 0-5 grade. Each (threshold, grade): the first band whose
# threshold a card's GIH WR meets or exceeds wins. Tunable.
LSV_BANDS = [
    (0.620, 5.0),
    (0.605, 4.5),
    (0.590, 4.0),
    (0.575, 3.5),
    (0.560, 3.0),
    (0.545, 2.5),
    (0.530, 2.0),
    (0.515, 1.5),
    (0.500, 1.0),
    (0.480, 0.5),
]
LSV_FLOOR = 0.0

# Guild names for the ten two-color pairs (keyed by sorted color letters).
GUILD_NAMES = {
    "WU": "Azorius",
    "UB": "Dimir",
    "BR": "Rakdos",
    "RG": "Gruul",
    "GW": "Selesnya",
    "WB": "Orzhov",
    "UR": "Izzet",
    "BG": "Golgari",
    "RW": "Boros",
    "GU": "Simic",
}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def gih_to_lsv(gih_wr: float) -> float:
    for threshold, grade in LSV_BANDS:
        if gih_wr >= threshold:
            return grade
    return LSV_FLOOR


def add_score(df: pd.DataFrame) -> pd.DataFrame:
    """Add an LSV 0-5 ``score`` column derived from GIH WR.

    Cards without GIH WR data, or with fewer than ``MIN_GIH_GAMES`` games, are
    left blank (not graded) rather than dropped.
    """
    out = df.copy()
    gih = _numeric(out.get("gih_wr"))
    games = _numeric(out.get("gih_games"))

    eligible = gih.notna() & (games.fillna(0) >= MIN_GIH_GAMES)
    score = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")
    if eligible.any():
        score.loc[eligible] = gih[eligible].map(gih_to_lsv)
    out["score"] = score
    return out


# --- 17Lands color / archetype tables -----------------------------------------


def color_table(colors_df: pd.DataFrame) -> pd.DataFrame:
    """Mono-color win rates (W/U/B/R/G), best first. From 17Lands color ratings."""
    cols = ["color", "win_rate", "games"]
    if colors_df is None or colors_df.empty:
        return pd.DataFrame(columns=cols)
    df = colors_df.copy()
    df["short_name"] = df["short_name"].astype(str)
    mono = df[(df["is_summary"].astype(str).isin(["False", "false", "0"]))
              & (df["short_name"].isin(["W", "U", "B", "R", "G"]))].copy()
    if mono.empty:
        return pd.DataFrame(columns=cols)
    mono = mono.rename(columns={"short_name": "color"})
    mono = mono[["color", "win_rate", "games"]]
    return mono.sort_values("win_rate", ascending=False).reset_index(drop=True)


def combo_table(colors_df: pd.DataFrame) -> pd.DataFrame:
    """Two-color guild win rates (Azorius, Rakdos, ...), best first."""
    cols = ["archetype", "pair", "win_rate", "games"]
    if colors_df is None or colors_df.empty:
        return pd.DataFrame(columns=cols)
    df = colors_df.copy()
    df["short_name"] = df["short_name"].astype(str)
    # Exact two-letter color codes, no splash ('+'), not summary rows.
    two = df[
        (df["is_summary"].astype(str).isin(["False", "false", "0"]))
        & (df["short_name"].str.fullmatch(r"[WUBRG]{2}"))
    ].copy()
    if two.empty:
        return pd.DataFrame(columns=cols)
    two["pair"] = two["short_name"]
    # 17Lands already labels pairs nicely ("Boros (RW)"); use that, else build one.
    def _label(r):
        cn = str(r.get("color_name") or "").strip()
        if cn:
            return cn
        guild = GUILD_NAMES.get("".join(sorted(r["pair"])), "")
        return f"{guild} ({r['pair']})" if guild else r["pair"]

    two["archetype"] = two.apply(_label, axis=1)
    two = two[["archetype", "pair", "win_rate", "games"]]
    return two.sort_values("win_rate", ascending=False).reset_index(drop=True)
