"""Merge Scryfall + 17Lands data, derive scores, and persist Omer's manual eval.

The killer invariant: rerunning the tool must never clobber ``my_eval`` /
``my_notes``. Those are preserved by merging the prior evaluation back in on
``(set, collector_number)`` -- a key stable across reprints, name changes, and
split-card weirdness. The prior evaluation can come from the existing CSV or
from the live Google Sheet (whichever the caller passes).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import scoring, seventeen_lands

# Full data model, left to right (CSV order). The Sheet writer reorders/selects.
OUTPUT_COLUMNS = [
    "set",
    "collector_number",
    "name",
    "mana_cost",
    "cmc",
    "type_line",
    "rarity",
    "colors",
    "score",
    "gih_wr",
    "iwd",
    "oh_wr",
    "gd_wr",
    "ata",
    "alsa",
    "gih_games",
    "my_eval",
    "my_notes",
    "oracle_text",
    "scryfall_uri",
    "image_url",
]

EVAL_COLUMNS = ["my_eval", "my_notes"]
MERGE_KEY = ["set", "collector_number"]

# Sort orders.
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


def load_existing_eval(source) -> pd.DataFrame | None:
    """Return a [set, collector_number, my_eval, my_notes] frame from a prior run.

    ``source`` may be a Path to a CSV, an already-loaded DataFrame (e.g. read
    back from the Google Sheet), or None. Raises MergeError if a source exists
    but lacks the merge-key columns, so we never silently drop hand-entered data.
    """
    if source is None:
        return None

    if isinstance(source, pd.DataFrame):
        existing = source.copy()
        label = "the live sheet"
    else:
        path = Path(source)
        if not path.exists():
            return None
        try:
            existing = pd.read_csv(path, dtype=str)
        except Exception as exc:  # noqa: BLE001 - surface as a hard stop
            raise MergeError(f"Could not read existing eval file {path}: {exc}") from exc
        label = str(path)

    missing = [c for c in MERGE_KEY if c not in existing.columns]
    if missing:
        raise MergeError(
            f"Existing eval source ({label}) is missing key columns {missing}; "
            "refusing to overwrite and lose my_eval."
        )
    for col in EVAL_COLUMNS:
        if col not in existing.columns:
            existing[col] = ""
    out = existing[MERGE_KEY + EVAL_COLUMNS].copy()
    for col in MERGE_KEY:
        out[col] = out[col].astype(str)
    return out


def merge(
    scryfall_df: pd.DataFrame,
    seventeen_lands_df: pd.DataFrame,
    existing_eval_source=None,
) -> pd.DataFrame:
    """Combine sources, derive score, and preserve my_eval / my_notes.

    Eval preservation key: (set, collector_number). ``existing_eval_source`` is a
    CSV Path, a DataFrame (from the Sheet), or None.
    """
    base = _attach_17lands(scryfall_df, seventeen_lands_df)

    eval_cols = load_existing_eval(existing_eval_source)
    if eval_cols is not None:
        for col in MERGE_KEY:
            base[col] = base[col].astype(str)
        base = base.merge(eval_cols, on=MERGE_KEY, how="left")
    else:
        for col in EVAL_COLUMNS:
            base[col] = ""

    for col in EVAL_COLUMNS:
        base[col] = base[col].fillna("")

    return _finalize(base)


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Derive score, add any missing columns, order, and sort."""
    df = scoring.add_score(df)
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
    """Sort by color identity (WUBRG, multi, colorless), then score desc, then name."""
    if df.empty:
        return df
    work = df.copy()
    work["_color"] = work["colors"].map(_color_sort_key)
    # Higher score is better; rank descending with blanks last.
    work["_score"] = pd.to_numeric(work["score"], errors="coerce").fillna(-1.0)
    work["_name"] = work["name"].astype(str).str.lower()
    work = work.sort_values(
        ["_color", "_score", "_name"],
        ascending=[True, False, True],
        kind="stable",
    ).drop(columns=["_color", "_score", "_name"])
    return work.reset_index(drop=True)


def write_output(df: pd.DataFrame, path: Path, *, fmt: str = "csv") -> None:
    """Write the combined frame as CSV or a Markdown table."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        path.write_text(df.to_markdown(index=False), encoding="utf-8")
    else:
        df.to_csv(path, index=False)
