"""Expert grade scrape (Card Game Base draft tier lists).

Card Game Base publishes per-set Limited tier lists as server-rendered tables,
one per color, each row being ``<card name>, <grade>`` with grades A+ .. F. We
scrape those and expose a name -> grade mapping that merge.py joins onto the
cards (an ``expert_grade`` column). This is most useful in a set's first weeks,
before 17Lands play data has enough games to be reliable.

This is a scrape of an HTML page, not an API: if Card Game Base changes its
markup or has no page for a set, fetch_grades returns empty and the tool simply
leaves expert_grade blank.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

from .seventeen_lands import normalize_name

BASE_URL = "https://cardgamebase.com"
# A browser-like UA; the site 403s the default requests UA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_GRADE_RE = re.compile(r"^[A-F][+-]?$")
_TABLE_RE = re.compile(r'<table[^>]*\btierlist\b.*?</table>', re.S | re.I)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


class ExpertGradesError(RuntimeError):
    """Raised when the grade source cannot be fetched."""


def slug_for(set_name: str) -> str:
    """Card Game Base URL slug for a set name, e.g. 'Marvel Super Heroes' -> path."""
    s = set_name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return f"{s}-draft-tier-list"


def _clean(cell: str) -> str:
    import html as _html

    return _html.unescape(_TAG_RE.sub("", cell)).strip()


def parse_grades(html_text: str) -> dict[str, str]:
    """Parse name -> grade from Card Game Base tier-list tables."""
    out: dict[str, str] = {}
    for table in _TABLE_RE.findall(html_text):
        for row in _ROW_RE.findall(table):
            cells = [_clean(c) for c in _CELL_RE.findall(row)]
            if len(cells) < 2:
                continue
            name, grade = cells[0], cells[1]
            if not name or not _GRADE_RE.match(grade):
                continue  # skips header rows and non-grade cells
            key = normalize_name(name)
            if key:
                out[key] = grade
    return out


def _cache_path(cache_dir: Path, set_code: str) -> Path:
    return cache_dir / f"grades-{set_code.lower()}.csv"


def fetch_grades(
    set_code: str,
    set_name: str,
    cache_dir: Path,
    *,
    refresh: bool = False,
) -> dict[str, str]:
    """Return a normalized-name -> grade dict for a set (empty if unavailable)."""
    import csv

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, set_code)

    if path.exists() and not refresh:
        with path.open(encoding="utf-8") as fh:
            return {row["join_name"]: row["expert_grade"] for row in csv.DictReader(fh)}

    if not set_name:
        return {}
    url = f"{BASE_URL}/{slug_for(set_name)}/"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.RequestException as exc:
        raise ExpertGradesError(f"Could not reach {url}: {exc}") from exc
    if resp.status_code == 404:
        return {}  # no tier list for this set; not an error
    if resp.status_code != 200:
        raise ExpertGradesError(f"Card Game Base returned {resp.status_code} for {url}.")

    grades = parse_grades(resp.text)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["join_name", "expert_grade"])
        for k, v in grades.items():
            w.writerow([k, v])
    return grades
