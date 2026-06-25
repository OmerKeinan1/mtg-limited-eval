"""Merge Scryfall + 17Lands data and persist Omer's manual eval.

The killer invariant: rerunning the tool must never clobber ``my_eval`` /
``my_notes``. Those are preserved by merging the existing output CSV back in on
``(set, collector_number)`` -- a key that is stable across reprints, name
changes, and split-card weirdness.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import seventeen_lands

# Final column order, left to right.
OUTPUT_COLUMNS = [
    "set",
    "collector_number",
    "name",
    "mana_cost",
    "cmc",
    "type_line",
    "rarity",
    "colors",
    "gih_wr",
    "oh_wr",
    "iwd",
    "ata",
    "alsa",
    "my_eval",
    "my_notes",
    "oracle_text",
    "scryfall_uri",
]

EVAL_COLUMNS = ["my_eval", "my_notes"]
MERGE_KEY = ["set", "collector_number"]

# Sort orders.
_RARITY_RANK = {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3}
# Single-color WUBRG, then multicolor, then colorless.
_COLOR_RANK = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4}


class MergeError(RuntimeError):
    """Raised when the existing eval data cannot be preserved (hard stop)."""


def _front_name(full_name: str) -> str:
    return (full_name or "").split(" // ", 1)[0]


def _attach_17lands(scryfall_df: pd.DataFrame, sl_df: pd.DataFrame) -> pd.DataFrame:
    """Join 17Lands stats onto Scryfall rows by name (full, then front-face)."""
    base = scryfall_df.copy()
    for col in seventeen_lands.STAT_COLUMNS:
        base[col] = pd.NA

    if sl_df is None or sl_df.empty:
        return base

    lookup: dict[str, dict] = {}
    for _, row in sl_df.iterrows():
        key = row.get("join_name")
        if isinstance(key, str) and key:
            lookup[key] = {c: row.get(c) for c in seventeen_lands.STAT_COLUMNS}

    for idx, row in base.iterrows():
        name = row.get("name", "")
        for candidate in (
            seventeen_lands.normalize_name(name),
            seventeen_lands.normalize_name(_front_name(name)),
        ):
            stats = lookup.get(candidate)
            if stats is not None:
                for c, v in stats.items():
                    base.at[idx, c] = v
                break
    return base


def merge(
    scryfall_df: pd.DataFrame,
    seventeen_lands_df: pd.DataFrame,
    existing_eval_csv_path: Path,
) -> pd.DataFrame:
    """Combine sources and preserve my_eval / my_notes.

    Eval preservation key: (set, collector_number).
    Raises MergeError if an existing eval file exists but cannot be read or is
    missing the key columns -- we refuse to silently drop hand-entered data.
    """
    base = _attach_17lands(scryfall_df, seventeen_lands_df)

    if existing_eval_csv_path is not None and Path(existing_eval_csv_path).exists():
        try:
            existing = pd.read_csv(existing_eval_csv_path, dtype=str)
        except Exception as exc:  # noqa: BLE001 - surface as a hard stop
            raise MergeError(
                f"Could not read existing eval file {existing_eval_csv_path}: {exc}"
            ) from exc

        missing = [c for c in MERGE_KEY if c not in existing.columns]
        if missing:
            raise MergeError(
                f"Existing eval file {existing_eval_csv_path} is missing key "
                f"columns {missing}; refusing to overwrite and lose my_eval."
            )

        for col in EVAL_COLUMNS:
            if col not in existing.columns:
                existing[col] = ""

        eval_cols = existing[MERGE_KEY + EVAL_COLUMNS].copy()
        # Normalize key dtypes to string on both sides so the join lines up.
        for col in MERGE_KEY:
            base[col] = base[col].astype(str)
            eval_cols[col] = eval_cols[col].astype(str)

        # Drop fully-empty eval rows so they do not introduce noise.
        base = base.merge(eval_cols, on=MERGE_KEY, how="left")
    else:
        for col in EVAL_COLUMNS:
            base[col] = ""

    for col in EVAL_COLUMNS:
        base[col] = base[col].fillna("")

    return _finalize(base)


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Add any missing columns, order, and sort."""
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col in EVAL_COLUMNS else pd.NA
    df = df[OUTPUT_COLUMNS].copy()
    return sort_cards(df)


def _color_sort_key(colors: str) -> int:
    colors = colors or ""
    if colors == "":
        return 100  # colorless last
    if len(colors) > 1:
        return 50  # multicolor after monocolor
    return _COLOR_RANK.get(colors, 60)


def sort_cards(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by rarity (M>R>U>C), color identity (WUBRG, multi, colorless), cmc, name."""
    if df.empty:
        return df
    work = df.copy()
    work["_rarity"] = work["rarity"].map(lambda r: _RARITY_RANK.get(str(r), 9))
    work["_color"] = work["colors"].map(_color_sort_key)
    work["_cmc"] = pd.to_numeric(work["cmc"], errors="coerce").fillna(0.0)
    work["_name"] = work["name"].astype(str).str.lower()
    work = work.sort_values(
        ["_rarity", "_color", "_cmc", "_name"], kind="stable"
    ).drop(columns=["_rarity", "_color", "_cmc", "_name"])
    return work.reset_index(drop=True)


def write_output(df: pd.DataFrame, path: Path, *, fmt: str = "csv") -> None:
    """Write the combined frame as CSV or a Markdown table."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        path.write_text(df.to_markdown(index=False), encoding="utf-8")
    else:
        df.to_csv(path, index=False)
