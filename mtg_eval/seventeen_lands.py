"""17Lands fetch.

17Lands exposes per-set Limited stats via its card_ratings data API, which
returns JSON keyed by card *name* (it does not provide set / collector number).
We normalize the stats we care about and let merge.py join them onto the
Scryfall rows by name.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import requests

DATA_URL = "https://www.17lands.com/card_ratings/data"
COLOR_DATA_URL = "https://www.17lands.com/color_ratings/data"
USER_AGENT = "mtg-limited-eval/0.1 (https://github.com/OmerKeinan1/mtg-limited-eval)"
# Wide enough to cover every set's full Limited run.
DEFAULT_START_DATE = "2019-01-01"

# 17Lands raw field -> our column name.
STAT_FIELDS = {
    "ever_drawn_win_rate": "gih_wr",
    "opening_hand_win_rate": "oh_wr",
    "drawn_win_rate": "gd_wr",
    "drawn_improvement_win_rate": "iwd",
    "avg_pick": "ata",
    "avg_seen": "alsa",
    # Sample size behind GIH WR; used to flag low-confidence cards.
    "ever_drawn_game_count": "gih_games",
}
STAT_COLUMNS = list(STAT_FIELDS.values())


class SeventeenLandsError(RuntimeError):
    """Raised when 17Lands data cannot be fetched (network / anti-bot)."""


def normalize_name(name: str) -> str:
    """Lowercased, whitespace-trimmed name for joining across sources."""
    return (name or "").strip().lower()


def _cache_path(cache_dir: Path, set_code: str, fmt: str) -> Path:
    return cache_dir / f"17lands-{set_code.lower()}-{fmt.lower()}.csv"


def _fetch_raw(set_code: str, fmt: str, end_date: str) -> list[dict]:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    params = {
        "expansion": set_code.upper(),
        "format": fmt,
        "start_date": DEFAULT_START_DATE,
        "end_date": end_date,
    }
    resp = session.get(DATA_URL, params=params, timeout=30)
    if resp.status_code == 429:
        raise SeventeenLandsError(
            "17Lands returned 429 (rate limited / anti-bot). Stopping; do not loop."
        )
    if resp.status_code != 200:
        raise SeventeenLandsError(
            f"17Lands request failed ({resp.status_code}) for set '{set_code}'."
        )
    ctype = resp.headers.get("Content-Type", "")
    if "application/json" not in ctype and not resp.text.lstrip().startswith("["):
        raise SeventeenLandsError(
            f"17Lands returned non-JSON (Content-Type: {ctype}); likely anti-bot. "
            "Drop a manual CSV in evaluations/.cache/ and rerun."
        )
    return resp.json()


def fetch_set(
    set_code: str,
    cache_dir: Path,
    *,
    fmt: str = "PremierDraft",
    refresh: bool = False,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Fetch 17Lands stats for a set as a DataFrame keyed by normalized name.

    Returns an empty (schema-preserving) DataFrame if the set has no data.
    A manually downloaded CSV at evaluations/.cache/17lands-<set>-<fmt>.csv is
    used as a fallback / cache.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, set_code, fmt)

    records: list[dict] | None = None
    if path.exists() and not refresh:
        cached = pd.read_csv(path)
        return _normalize_frame(cached)

    if end_date is None:
        end_date = dt.date.today().isoformat()
    records = _fetch_raw(set_code, fmt, end_date)

    raw_df = pd.DataFrame(records)
    out = _normalize_frame(raw_df)
    # Cache the normalized frame so reruns and manual inspection are cheap.
    out.to_csv(path, index=False)
    return out


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw or cached 17Lands rows to our stat columns + join key."""
    cols = ["join_name", *STAT_COLUMNS]
    if df.empty:
        return pd.DataFrame(columns=cols)

    # A cached normalized frame already has our column names.
    if "join_name" in df.columns:
        for c in cols:
            if c not in df.columns:
                df[c] = pd.NA
        return df[cols]

    out = pd.DataFrame()
    out["join_name"] = df.get("name", pd.Series(dtype=str)).map(normalize_name)
    for raw_field, col in STAT_FIELDS.items():
        out[col] = df.get(raw_field, pd.NA)
    return out[cols]


def empty_frame() -> pd.DataFrame:
    """Schema-preserving empty 17Lands frame for the no-data path."""
    return pd.DataFrame(columns=["join_name", *STAT_COLUMNS])


# --- color / archetype ratings ------------------------------------------------

COLOR_COLUMNS = ["color_name", "short_name", "wins", "games", "win_rate", "is_summary"]


def _colors_cache_path(cache_dir: Path, set_code: str, fmt: str) -> Path:
    return cache_dir / f"17lands-colors-{set_code.lower()}-{fmt.lower()}.csv"


def _normalize_colors(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=COLOR_COLUMNS)
    df = pd.DataFrame(records)
    out = pd.DataFrame()
    out["color_name"] = df.get("color_name", "")
    out["short_name"] = df.get("short_name", "").astype(str)
    out["wins"] = pd.to_numeric(df.get("wins"), errors="coerce")
    out["games"] = pd.to_numeric(df.get("games"), errors="coerce")
    out["win_rate"] = (out["wins"] / out["games"]).where(out["games"] > 0)
    out["is_summary"] = df.get("is_summary", False)
    return out[COLOR_COLUMNS]


def fetch_colors(
    set_code: str,
    cache_dir: Path,
    *,
    fmt: str = "PremierDraft",
    refresh: bool = False,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Fetch 17Lands color/archetype win rates (mono colors and guild pairs).

    Returns a frame with win_rate per color_name; empty if the set has no data.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _colors_cache_path(cache_dir, set_code, fmt)

    if path.exists() and not refresh:
        return _normalize_colors(pd.read_csv(path).to_dict("records"))

    if end_date is None:
        end_date = dt.date.today().isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    params = {
        "expansion": set_code.upper(),
        "event_type": fmt,
        "start_date": DEFAULT_START_DATE,
        "end_date": end_date,
        "combine_splash": "false",
    }
    resp = session.get(COLOR_DATA_URL, params=params, timeout=30)
    if resp.status_code == 429:
        raise SeventeenLandsError(
            "17Lands returned 429 on color ratings (rate limited). Stopping."
        )
    if resp.status_code != 200:
        raise SeventeenLandsError(
            f"17Lands color ratings failed ({resp.status_code}) for '{set_code}'."
        )
    out = _normalize_colors(resp.json())
    out.to_csv(path, index=False)
    return out


def empty_colors() -> pd.DataFrame:
    """Schema-preserving empty color frame."""
    return pd.DataFrame(columns=COLOR_COLUMNS)
