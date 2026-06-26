# mtg-limited-eval

A Python CLI that builds a per-set Magic: The Gathering Limited evaluation.
Given a set code, it pulls canonical card data from Scryfall, Limited
performance stats from 17Lands, derives a set-relative score, stitches in your
own hand-entered grades, and **syncs it all to a Google Sheet** (`<SET>_Scores`)
with inline card-image previews and chart tabs. A local CSV mirror is also
written to `evaluations/<SET>.csv`.

## What you get

**Cards tab** (ordered by color): a thumbnail preview of each card, name, cmc,
color, rarity, the **score**, your **my_eval** / **my_notes** (editable, tinted),
and the 17Lands stats. Three more tabs:

- **Commons** and **Uncommons**: each a bar chart of GIH WR per card with a line
  at that rarity's set average, so you see the winners relative to the field.
- **Best Color**: a chart ranking the five colors by a blend of average GIH WR
  and your manual ratings.

### The score

Score is the **set-relative percentile of GIH WR** (Games In Hand Win Rate),
0-100. GIH WR is the strongest single 17Lands power signal, but it is only fair
compared within a set (it is inflated by strong colors and by being expensive),
so the score ranks each card against the rest of its own set. Cards with fewer
than 200 games behind their GIH WR are left unscored (low confidence). `iwd`
(Improvement When Drawn) is shown as a secondary signal; `ata` / `alsa` reflect
pick order and availability, not power.

### The preserve-my-eval invariant

**Rerunning the tool never clobbers `my_eval` / `my_notes`.** They are preserved
by merging the prior evaluation back in on `(set, collector_number)`, a key
stable across reprints, name changes, and split-card weirdness. The live Google
Sheet (where you actually edit) is the source of truth; the CSV is a mirror.

## Install

```bash
uv sync
```

## Google Sheets setup (one time)

The tool writes to *your* Google Drive via OAuth. You provide an OAuth client:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/), create
   (or pick) a project.
2. APIs & Services > Library > enable the **Google Sheets API**.
3. APIs & Services > OAuth consent screen > External > add yourself as a test
   user.
4. APIs & Services > Credentials > Create credentials > **OAuth client ID** >
   Application type **Desktop app**. Download the JSON.
5. Save it as `~/.config/mtg-eval/credentials.json` (or pass `--credentials PATH`).

The first run opens a browser to authorize; the token is cached at
`~/.config/mtg-eval/token.json`, so later runs are non-interactive. Use
`--no-sheets` to skip Sheets entirely and just write the CSV.

## Usage

```bash
uv run mtg-eval DSK
```

Builds `evaluations/DSK.csv` and syncs the `DSK_Scores` Google Sheet (printing
its URL). Open the sheet, fill in `my_eval` / `my_notes`, and rerun any time for
fresh data; your columns survive.

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--format csv\|md` | `csv` | Local file format (the Sheet is always richer). |
| `--include-basics` | off | Include basic lands. |
| `--17lands-format PremierDraft\|TradDraft\|Sealed` | `PremierDraft` | 17Lands format to pull. |
| `--refresh` | off | Bypass caches and refetch from both APIs. |
| `--output PATH` | `evaluations/<SET>.<ext>` | Override the local file path. |
| `--no-sheets` | off | Write the CSV only, skip the Google Sheets sync. |
| `--credentials PATH` | `~/.config/mtg-eval/credentials.json` | OAuth client JSON. |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 2 | Scryfall failure (nothing written). |
| 3 | 17Lands failure (file still written, stat columns blank). |
| 4 | Merge failure: existing eval could not be preserved (hard stop). |
| 5 | Google Sheets failure (CSV still written). |

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
