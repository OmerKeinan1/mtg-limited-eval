# mtg-limited-eval

A small Python CLI that builds a **per-set Magic: The Gathering Limited
evaluation** and syncs it to a **Google Sheet** you own. For a given set code it
pulls card data from Scryfall, performance stats from 17Lands, and expert grades
from Card Game Base, computes a score, and lays it all out across tabs with
inline card images and charts. You add your own grades in the sheet, and reruns
never overwrite them.

It is built so **anyone can run it for themselves** in about 15 minutes
(most of which is a one-time Google sign-in setup). This guide walks you through
the whole thing.

## What you get

A spreadsheet named `<SET>_Scores` in your Google Drive with these tabs:

- **Cards** - every draftable card, ordered by color, with a thumbnail preview,
  an LSV-style 0-5 **score**, an **expert** letter grade, the 17Lands stats, and
  editable **my_eval** / **my_notes** columns for your own notes.
- **Commons** / **Uncommons** - a dot-per-card chart of GIH WR with the set
  average drawn as a line, so above/below-average cards are obvious.
- **Best Color** / **Color Pairs** - bar charts of win rate by color and by
  two-color archetype, colored to match the MTG colors, from 17Lands data.
- **Archetypes** - each of the ten guilds with its top 5 commons and top 5
  uncommons, with images and links.

A CSV copy is also written to `evaluations/<SET>.csv`.

## How it works (the short version)

The data sources publish their numbers; this tool just fetches, joins, scores,
and writes:

1. **Scryfall** (`api.scryfall.com`) - card facts and image URLs.
2. **17Lands** (`17lands.com/card_ratings/data` and `/color_ratings/data`) -
   win rates aggregated from real MTG Arena draft games.
3. **Card Game Base** - per-set draft tier-list grades (scraped from the page).
4. It joins these by card name, computes the score, then writes to your Google
   Sheet via the Google Sheets API.

### The score

The **score** is an LSV-style 0-5 Limited grade derived from **GIH WR** (Games In
Hand Win Rate), the strongest single 17Lands power signal. GIH WR is only fair
when read against a baseline, so it is mapped to the 0-5 scale with fixed
win-rate bands (5.0 = bomb, ~3.0-3.5 = good common, ~2.0 = filler). Cards with
too few games are left ungraded. The bands live in `mtg_eval/scoring.py`
(`LSV_BANDS`) and are easy to tune.

### The preserve-your-eval guarantee

Rerunning never clobbers `my_eval` / `my_notes`. They are preserved by merging
your prior values back in on `(set, collector_number)`, a key stable across
reprints and name changes. The live Google Sheet is the source of truth.

## Requirements

- **Python 3.13+** and **[uv](https://docs.astral.sh/uv/)** (`brew install uv`
  or see their site).
- A **Google account**. Use a **personal `@gmail.com` account**, not a Google
  Workspace (company) account - many Workspaces block the `IMAGE()` function and
  the card thumbnails will show `#REF!` (see Troubleshooting).

## Setup

### 1. Clone and install

```bash
git clone https://github.com/OmerKeinan1/mtg-limited-eval.git
cd mtg-limited-eval
uv sync
```

### 2. Create Google OAuth credentials (one time)

The tool writes to *your* Drive, so you provide a Google OAuth client:

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create
   (or pick) a project.
2. **APIs & Services > Library** - enable the **Google Sheets API**.
3. **APIs & Services > Google Auth Platform > Audience** - set **User type** to
   **External**, then add your Google address under **Test users**. (Internal
   only works for Workspace org members and will fail with `403 org_internal`.)
4. **Google Auth Platform > Clients > Create client > Application type: Desktop
   app**. Download the JSON.
5. Save it as `~/.config/mtg-eval/credentials.json`
   (or pass `--credentials PATH`).
6. **Publish the app** (Audience tab > **Publish app**). Optional for one-off
   runs, but required for the unattended weekly job, otherwise the token expires
   after 7 days. You can click through the "unverified app" notice; it is your
   own app.

### 3. Run it

```bash
uv run mtg-eval MSH
```

The first run opens a browser to authorize (sign in with the account you added
as a test user). It then builds `evaluations/MSH.csv` and the `MSH_Scores` Google
Sheet, and prints the sheet URL. The token is cached at
`~/.config/mtg-eval/token.json`, so later runs need no browser.

Use any set code (e.g. `DSK`, `TDM`, `MSH`). Use `--no-sheets` to write only the
CSV and skip Google entirely.

### Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--format csv\|md` | `csv` | Local file format. |
| `--include-basics` | off | Include basic lands. |
| `--17lands-format PremierDraft\|TradDraft\|Sealed` | `PremierDraft` | Which 17Lands format. |
| `--refresh` | off | Bypass caches and refetch. |
| `--output PATH` | `evaluations/<SET>.<ext>` | Override the local file path. |
| `--no-sheets` | off | Write the CSV only, skip Google Sheets. |
| `--credentials PATH` | `~/.config/mtg-eval/credentials.json` | OAuth client JSON. |

## Weekly auto-update (optional, macOS)

To keep a set's sheet fresh while its 17Lands data matures, install a launchd
agent (after step 3 above and after publishing your OAuth app):

```bash
scripts/install-weekly.sh MSH        # Mondays 09:00 by default
# scripts/install-weekly.sh MSH 8 30 1   # or: hour 8, minute 30, Monday
```

It refreshes every Monday and rebuilds the whole sheet. Logs go to
`~/.config/mtg-eval/weekly.log`. The script prints commands to test it, watch the
log, and uninstall.

**Linux**: there is no launchd; add a cron entry instead, e.g.
`0 9 * * 1 cd /path/to/mtg-limited-eval && uv run mtg-eval MSH --refresh`.

## Troubleshooting

- **Card previews show `#REF!`** - your Google account (usually a Workspace/
  company account) blocks the `IMAGE()` function from loading external images.
  Use a **personal `@gmail.com` account** instead; the sheet will be created
  there and images render. (A `#REF!` right after a run can also just be
  recalculation lag - reopen the tab.)
- **`403 org_internal` during sign-in** - your OAuth consent screen User type is
  **Internal**. Switch it to **External** and add yourself as a test user.
- **Weekly job stops working after about a week** - publish your OAuth app
  (Audience > Publish app). Testing-mode apps expire the refresh token after 7
  days.
- **A new set has blank stats** - 17Lands needs games to accumulate; brand-new
  sets are sparse for a few days. Expert grades fill the gap early.

## Data, limitations, and credits

- 17Lands data is **format-specific** (defaults to Premier Draft) and **grows
  over time** - early in a set it is noisy. The score firms up as games are
  logged, which is what the weekly refresh is for.
- The Card Game Base grades are an **HTML scrape**, not an API. If they change
  their page or have no tier list for a set, the `expert` column is simply blank;
  nothing else breaks.
- Card data and images: [Scryfall](https://scryfall.com). Win-rate data:
  [17Lands](https://www.17lands.com). Expert grades:
  [Card Game Base](https://cardgamebase.com). This project is not affiliated with
  any of them, or with Wizards of the Coast. Please respect each source's terms
  of use and rate limits (the tool caches responses under `evaluations/.cache/`).

## Development

```bash
uv run pytest      # tests; test_merge.py guards the preserve-my-eval invariant
uv run ruff check  # lint
```

## License

MIT - see [LICENSE](LICENSE).
