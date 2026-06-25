# mtg-limited-eval

A Python CLI that builds a per-set Magic: The Gathering Limited evaluation file.
Given a set code, it pulls canonical card data from Scryfall, Limited
performance stats from 17Lands, and stitches in your own hand-entered grades,
producing one file per set at `evaluations/<SET>.csv`.

## The point of the tool

Each row is one draftable card with three layers of data:

1. **Scryfall** — name, mana cost, cmc, type, rarity, colors, oracle text, link.
2. **17Lands** — `gih_wr`, `oh_wr`, `iwd`, `ata`, `alsa` (PremierDraft by default).
3. **Yours** — `my_eval` (a numeric grade) and `my_notes` (freeform), which you
   fill in by hand.

The killer feature: **rerunning the tool never clobbers `my_eval` / `my_notes`.**
When Scryfall or 17Lands data changes, your evaluations are preserved by merging
the existing file back in on `(set, collector_number)`, a key that is stable
across reprints, name changes, and split-card weirdness.

## Install

```bash
uv sync
```

## Usage

```bash
uv run mtg-eval DSK
```

This writes `evaluations/DSK.csv`. Open it in any spreadsheet, fill in the
`my_eval` and `my_notes` columns, save, and rerun whenever you want fresh data;
your columns survive.

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--format csv\|md` | `csv` | Output format (Markdown table renders on GitHub). |
| `--include-basics` | off | Include basic lands. |
| `--17lands-format PremierDraft\|TradDraft\|Sealed` | `PremierDraft` | 17Lands format to pull. |
| `--refresh` | off | Bypass caches and refetch from both APIs. |
| `--output PATH` | `evaluations/<SET>.<ext>` | Override the output path. |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 2 | Scryfall failure (nothing written). |
| 3 | 17Lands failure (file still written, stat columns blank). |
| 4 | Merge failure: existing eval could not be preserved (hard stop). |

## How the data joins

- Scryfall is the spine, keyed by `(set, collector_number)`.
- 17Lands data has no collector numbers, so it joins onto Scryfall by card
  **name** (full name first, then the front-face name for double-faced and
  split cards).
- Your eval is preserved by `(set, collector_number)`.

Double-faced, split, and adventure cards are emitted as a single row with faces
combined using the MTG `//` convention. Tokens, emblems, and non-booster extras
are filtered out; basic lands are dropped unless `--include-basics` is passed.

## Caching

Raw API responses are cached under `evaluations/.cache/` (gitignored). Reruns
hit the cache; use `--refresh` to force a refetch. If 17Lands blocks automated
requests, you can drop a manually downloaded CSV at
`evaluations/.cache/17lands-<set>-<format>.csv` and it will be used as the source.

## Development

```bash
uv run pytest      # tests/test_merge.py guards the preserve-my-eval invariant
uv run ruff check  # lint
```
