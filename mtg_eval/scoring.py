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

import re

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

# The ten guilds in a stable display order, with a canonical 2-letter code.
GUILD_PAIRS = [
    ("WU", "Azorius"),
    ("WB", "Orzhov"),
    ("WR", "Boros"),
    ("WG", "Selesnya"),
    ("UB", "Dimir"),
    ("UR", "Izzet"),
    ("UG", "Simic"),
    ("BR", "Rakdos"),
    ("BG", "Golgari"),
    ("RG", "Gruul"),
]

# Chart-friendly RGB per MTG color (pure white would vanish on a white chart, so
# white is a warm cream). Used to color the Best Color / Color Pairs bars.
MTG_RGB = {
    "W": (0.95, 0.90, 0.66),
    "U": (0.10, 0.45, 0.75),
    "B": (0.28, 0.24, 0.28),
    "R": (0.82, 0.22, 0.18),
    "G": (0.20, 0.60, 0.32),
}
_GOLD_RGB = (0.83, 0.69, 0.22)  # multicolor / colorless fallback


def color_rgb(code: str) -> dict:
    """{red,green,blue} for a color code ('W', 'WU', ...). Pairs blend their colors."""
    letters = [c for c in str(code) if c in MTG_RGB]
    if not letters:
        r, g, b = _GOLD_RGB
    else:
        rs = [MTG_RGB[c] for c in letters]
        r = sum(x[0] for x in rs) / len(rs)
        g = sum(x[1] for x in rs) / len(rs)
        b = sum(x[2] for x in rs) / len(rs)
    return {"red": round(r, 4), "green": round(g, 4), "blue": round(b, 4)}


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


_COLOR_ORDER = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4}


def _color_rank(colors: str) -> int:
    colors = str(colors or "")
    if colors == "":
        return 100
    if len(colors) > 1:
        return 50
    return _COLOR_ORDER.get(colors, 60)


def _trick_type(type_line: str, oracle_text: str) -> str:
    tl = str(type_line or "").lower()
    inst = "instant" in tl
    fl = bool(re.search(r"\bflash\b", str(oracle_text or "").lower()))
    if inst and fl:
        return "Instant + Flash"
    if inst:
        return "Instant"
    if fl:
        return "Flash"
    # Fall back to the card's primary type (e.g. a tagged Sorcery / Creature).
    head = tl.split("//")[0].split("—")[0].split("-")[0].strip()
    return head.title() if head else "Other"


def combat_tricks(df: pd.DataFrame, trick_names: set | None = None) -> pd.DataFrame:
    """Combat tricks to play around, ordered by color then mana cost then score.

    If ``trick_names`` (normalized card names from Scryfall's combat-trick oracle
    tag) is given, the list is exactly those curated cards. Otherwise it falls
    back to a heuristic: all Instants plus Flash cards.
    """
    cols = ["name", "cmc", "colors", "rarity", "trick_type", "score",
            "gih_wr", "scryfall_uri", "image_url"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    work = df.copy()
    tl = work.get("type_line", pd.Series([""] * len(work))).astype(str)
    ot = work.get("oracle_text", pd.Series([""] * len(work))).astype(str)

    if trick_names is not None:
        def fits(name: str) -> bool:
            n = str(name or "").strip().lower()
            front = n.split(" // ", 1)[0]
            return n in trick_names or front in trick_names
        work = work[work["name"].map(fits)].copy()
    else:
        is_instant = tl.str.lower().str.contains("instant", na=False)
        has_flash = ot.str.lower().str.contains(r"\bflash\b", na=False, regex=True)
        work = work[is_instant | has_flash].copy()

    if work.empty:
        return pd.DataFrame(columns=cols)

    work["trick_type"] = [
        _trick_type(t, o) for t, o in zip(work["type_line"], work["oracle_text"])
    ]
    work["_c"] = work["colors"].map(_color_rank)
    work["_cmc"] = _numeric(work["cmc"]).fillna(0.0)
    work["_s"] = _numeric(work.get("score")).fillna(-1.0)
    work = work.sort_values(["_c", "_cmc", "_s"], ascending=[True, True, False])
    for c in cols:
        if c not in work.columns:
            work[c] = pd.NA
    return work[cols].reset_index(drop=True)


# Minimum games behind a card's in-archetype win rate to trust it.
ARCH_MIN_GAMES = 100


def guild_top_cards(
    cards_df: pd.DataFrame,
    pair: str,
    rarity: str,
    arch_stats: dict | None = None,
    n: int = 5,
) -> pd.DataFrame:
    """Top ``n`` cards of a rarity for a guild, by in-archetype win rate.

    If ``arch_stats`` ({normalized_name: (gih_wr, games)} from 17Lands filtered
    to this guild's deck colors) is given, rank by how the card performs *in that
    archetype* (with a games floor). Otherwise fall back to overall GIH WR among
    cards whose color identity fits the guild.

    Returns columns name, wr (the win rate used), scryfall_uri, image_url.
    """
    cols = ["name", "wr", "scryfall_uri", "image_url"]
    if cards_df is None or cards_df.empty:
        return pd.DataFrame(columns=cols)
    df = cards_df[cards_df["rarity"].astype(str) == rarity].copy()

    if arch_stats:
        def arch_wr(name: str):
            n_ = str(name or "").strip().lower()
            stat = arch_stats.get(n_) or arch_stats.get(n_.split(" // ", 1)[0])
            if stat and stat[1] >= ARCH_MIN_GAMES:
                return stat[0]
            return None

        df["wr"] = df["name"].map(arch_wr)
        df = df.dropna(subset=["wr"])
    else:
        allowed = set(pair)

        def fits(colors) -> bool:
            chars = {c for c in str(colors or "") if c in MTG_RGB}
            return chars.issubset(allowed)

        df["wr"] = _numeric(df.get("gih_wr"))
        df = df.dropna(subset=["wr"])
        df = df[df["colors"].map(fits)]

    df = df.sort_values("wr", ascending=False).head(n)
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols].reset_index(drop=True)
