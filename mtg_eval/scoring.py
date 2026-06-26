"""Derived scoring.

Two derived views built on top of the merged data:

1. ``score`` -- a set-relative card score. GIH WR is the gold-standard 17Lands
   power signal but is only fair when compared within a set (it is inflated by
   strong colors and by being expensive). So the score is the percentile rank of
   a card's GIH WR among the cards in the same set that have a trustworthy sample.

2. ``best_color`` -- a per-color ranking that blends the data (average GIH WR of
   the color's cards) with Omer's manual ratings (average my_eval), so the
   "which color is best" call reflects both the metrics and his own read.
"""

from __future__ import annotations

import pandas as pd

# A GIH WR number needs enough games behind it to be meaningful. Commons /
# uncommons clear this easily; thin-sample rares/mythics get flagged instead.
MIN_GIH_GAMES = 200

# Single colors in WUBRG order. Multicolor cards count toward each of their
# colors; colorless is tracked separately.
COLORS = ["W", "U", "B", "R", "G"]
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def add_score(df: pd.DataFrame) -> pd.DataFrame:
    """Add a set-relative ``score`` column (0-100 percentile of GIH WR).

    Cards without GIH WR data, or with fewer than ``MIN_GIH_GAMES`` games, get a
    blank score (they are not dropped, just not ranked).
    """
    out = df.copy()
    gih = _numeric(out.get("gih_wr"))
    games = _numeric(out.get("gih_games"))

    eligible = gih.notna() & (games.fillna(0) >= MIN_GIH_GAMES)
    score = pd.Series([pd.NA] * len(out), index=out.index, dtype="object")
    if eligible.any():
        # Percentile rank within the eligible population, 0-100.
        pct = gih[eligible].rank(pct=True) * 100.0
        score.loc[eligible] = pct.round(1)
    out["score"] = score
    return out


def _zscore(series: pd.Series) -> pd.Series:
    vals = _numeric(series)
    std = vals.std(ddof=0)
    if not std or pd.isna(std):
        return pd.Series([0.0] * len(series), index=series.index)
    return (vals - vals.mean()) / std


def best_color(df: pd.DataFrame) -> pd.DataFrame:
    """Rank the five colors by a blend of average GIH WR and average my_eval.

    A card counts toward every color in its color identity (so multicolor cards
    feed both colors). Returns one row per color, best first.
    """
    gih = _numeric(df.get("gih_wr"))
    my_eval = _numeric(df.get("my_eval"))
    colors = df.get("colors", pd.Series([""] * len(df))).fillna("").astype(str)

    rows = []
    for c in COLORS:
        mask = colors.str.contains(c, regex=False)
        col_gih = gih[mask].dropna()
        col_eval = my_eval[mask].dropna()
        rows.append(
            {
                "color": c,
                "color_name": COLOR_NAMES[c],
                "n_cards": int(mask.sum()),
                "avg_gih_wr": round(col_gih.mean(), 4) if len(col_gih) else pd.NA,
                "n_rated": int(len(col_eval)),
                "avg_my_eval": round(col_eval.mean(), 2) if len(col_eval) else pd.NA,
            }
        )

    table = pd.DataFrame(rows)

    # Blend: z-score the data signal and (if any manual ratings exist) the manual
    # signal across the five colors, then average whichever signals are present.
    z_data = _zscore(table["avg_gih_wr"])
    if table["avg_my_eval"].notna().any():
        z_manual = _zscore(table["avg_my_eval"])
        # Average the two z-scores per color, ignoring a missing manual signal.
        stacked = pd.concat([z_data, z_manual], axis=1)
        table["rank_score"] = stacked.mean(axis=1).round(3)
    else:
        table["rank_score"] = z_data.round(3)

    return table.sort_values("rank_score", ascending=False).reset_index(drop=True)
