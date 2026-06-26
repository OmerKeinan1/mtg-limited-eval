"""Scryfall fetch.

Pulls every card in a set via the search endpoint, with on-disk caching of the
raw paginated response so reruns do not hit the API unnecessarily.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

SEARCH_URL = "https://api.scryfall.com/cards/search"
USER_AGENT = "mtg-limited-eval/0.1 (https://github.com/OmerKeinan1/mtg-limited-eval)"
# Scryfall asks for ~50-100ms between requests.
PAGE_SLEEP_SECONDS = 0.1

# Fields we combine across faces for multi-faced layouts.
_FACE_FIELDS = ("name", "mana_cost", "oracle_text", "type_line")
# Layouts whose meaningful text lives in card_faces[].
_MULTIFACE_LAYOUTS = {"transform", "modal_dfc", "meld", "split", "adventure", "flip"}
# Non-gameplay layouts to drop defensively (the default search excludes most of
# these already, but some sets surface them).
_EXTRA_LAYOUTS = {
    "token",
    "double_faced_token",
    "emblem",
    "art_series",
    "vanguard",
    "scheme",
    "planar",
}


class ScryfallError(RuntimeError):
    """Raised when Scryfall data cannot be fetched."""


def _cache_path(cache_dir: Path, set_code: str) -> Path:
    return cache_dir / f"scryfall-{set_code.lower()}.json"


def _fetch_raw(set_code: str) -> list[dict]:
    """Fetch all card objects for a set, following pagination."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    cards: list[dict] = []
    # unique=cards: one row per gameplay-distinct card, not per art variant.
    params = {"q": f"set:{set_code}", "order": "name", "unique": "cards"}
    url = SEARCH_URL
    first = True
    while url:
        resp = session.get(url, params=params if first else None, timeout=30)
        first = False
        if resp.status_code == 404:
            # Scryfall returns 404 with an error body for an empty/unknown set.
            raise ScryfallError(
                f"Scryfall returned no cards for set '{set_code}' (404). "
                "Check the set code."
            )
        if resp.status_code != 200:
            raise ScryfallError(
                f"Scryfall request failed ({resp.status_code}) for set '{set_code}'."
            )
        payload = resp.json()
        cards.extend(payload.get("data", []))
        if payload.get("has_more"):
            url = payload.get("next_page")
            time.sleep(PAGE_SLEEP_SECONDS)
        else:
            url = None
    return cards


def fetch_raw_cards(
    set_code: str, cache_dir: Path, *, refresh: bool = False
) -> list[dict]:
    """Return raw Scryfall card objects, using the on-disk cache when present."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, set_code)
    if path.exists() and not refresh:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    cards = _fetch_raw(set_code)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(cards, fh, ensure_ascii=False, indent=0)
    return cards


def _combine_faces(card: dict, field: str) -> str:
    """Join a field across card_faces with the MTG '//' convention."""
    faces = card.get("card_faces")
    if faces:
        parts = [str(f.get(field, "") or "") for f in faces]
        # Drop trailing empties so single-text fields stay clean.
        if any(parts):
            return " // ".join(parts)
    return str(card.get(field, "") or "")


def _card_to_row(card: dict) -> dict:
    multiface = card.get("layout") in _MULTIFACE_LAYOUTS and card.get("card_faces")

    if multiface:
        name = _combine_faces(card, "name")
        mana_cost = _combine_faces(card, "mana_cost")
        oracle_text = _combine_faces(card, "oracle_text")
        # type_line is usually present at top level even for DFCs; fall back to faces.
        type_line = card.get("type_line") or _combine_faces(card, "type_line")
    else:
        name = card.get("name", "")
        mana_cost = card.get("mana_cost", "")
        oracle_text = card.get("oracle_text", "")
        type_line = card.get("type_line", "")

    colors = card.get("colors")
    if colors is None:
        # DFCs expose colors per-face; union them.
        seen: list[str] = []
        for face in card.get("card_faces", []) or []:
            for c in face.get("colors", []) or []:
                if c not in seen:
                    seen.append(c)
        colors = seen

    return {
        "set": card.get("set", ""),
        "collector_number": str(card.get("collector_number", "")),
        "name": name,
        "mana_cost": mana_cost,
        "cmc": card.get("cmc", ""),
        "type_line": type_line,
        "rarity": card.get("rarity", ""),
        "colors": "".join(colors) if colors else "",
        "oracle_text": (oracle_text or "").replace("\n", " "),
        "scryfall_uri": card.get("scryfall_uri", ""),
        "image_url": _image_url(card),
        "layout": card.get("layout", ""),
        "booster": bool(card.get("booster", False)),
    }


def _image_url(card: dict) -> str:
    """Small front-face image URL for inline previews."""
    imgs = card.get("image_uris")
    if not imgs:
        faces = card.get("card_faces") or []
        if faces:
            imgs = faces[0].get("image_uris")
    if not imgs:
        return ""
    return imgs.get("small") or imgs.get("normal") or ""


def _is_basic_land(card: dict) -> bool:
    type_line = (card.get("type_line") or "").lower()
    return "basic" in type_line and "land" in type_line


def fetch_set(
    set_code: str,
    cache_dir: Path,
    *,
    refresh: bool = False,
    include_basics: bool = False,
) -> pd.DataFrame:
    """Fetch a set as a normalized DataFrame keyed by (set, collector_number).

    Filters to booster cards by default (drops tokens/emblems/promos) and drops
    basic lands unless ``include_basics`` is set.
    """
    raw = fetch_raw_cards(set_code, cache_dir, refresh=refresh)

    # Drop basics (unless requested) and non-gameplay extras up front.
    candidates = [
        c
        for c in raw
        if (include_basics or not _is_basic_land(c))
        and c.get("layout") not in _EXTRA_LAYOUTS
    ]

    # The booster flag cleanly separates draftable cards from promos/extras for
    # most sets, but some sets (e.g. Marvel crossovers) leave it false on every
    # card. So only apply the booster filter when the set actually uses it.
    booster_count = sum(1 for c in candidates if c.get("booster"))
    use_booster_filter = booster_count > 0.5 * len(candidates) if candidates else False

    rows: list[dict] = []
    for card in candidates:
        if use_booster_filter and not card.get("booster", False):
            continue
        rows.append(_card_to_row(card))

    df = pd.DataFrame(rows)
    if df.empty:
        # Preserve the schema so downstream merge/sort do not crash.
        df = pd.DataFrame(
            columns=[
                "set",
                "collector_number",
                "name",
                "mana_cost",
                "cmc",
                "type_line",
                "rarity",
                "colors",
                "oracle_text",
                "scryfall_uri",
                "image_url",
                "layout",
                "booster",
            ]
        )
    return df
