"""Google Sheets output.

Builds (or updates) a per-set spreadsheet named ``<SET>_Scores`` with:

  * a Cards tab: inline card-image previews via ``=IMAGE()``, ordered by color,
    with the score and 17Lands stats, plus editable my_eval / my_notes columns.
  * a Commons tab and an Uncommons tab: each a bar chart of GIH WR per card with
    a line marking the rarity's set average ("winning cards vs the field").
  * a Best Color tab: a chart ranking the five colors by a blend of average
    GIH WR and Omer's manual ratings.

Auth is OAuth (installed-app flow) against Omer's own Google account, so files
land in his Drive and reruns can read his hand-entered eval back out.

The spreadsheet id per set is remembered in evaluations/.cache/sheets-state.json
so reruns update the same workbook in place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import scoring

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CONFIG_DIR = Path.home() / ".config" / "mtg-eval"
DEFAULT_CRED_PATH = CONFIG_DIR / "credentials.json"
TOKEN_PATH = CONFIG_DIR / "token.json"
STATE_PATH = Path("evaluations/.cache/sheets-state.json")

CARDS_TAB = "Cards"
COMMONS_TAB = "Commons"
UNCOMMONS_TAB = "Uncommons"
COLOR_TAB = "Best Color"
PAIRS_TAB = "Color Pairs"
ARCHETYPES_TAB = "Archetypes"
TRICKS_TAB = "Combat Tricks"
NOTES_TAB = "Notes"
ALL_TABS = [
    CARDS_TAB,
    COMMONS_TAB,
    UNCOMMONS_TAB,
    COLOR_TAB,
    PAIRS_TAB,
    ARCHETYPES_TAB,
    TRICKS_TAB,
    NOTES_TAB,
]

# Section headers seeded into a fresh Notes tab (free-form below each).
NOTES_SECTIONS = [
    "Overperformers (beating their grade / data)",
    "Underperformers (below expectations)",
    "Successful decks / archetypes",
    "Notable cards, interactions, combos",
    "General format notes",
]

# Cards tab columns, in display order. (set / collector_number kept at the end so
# the sheet is self-sufficient for the preserve-my-eval merge key.)
CARD_HEADERS = [
    "preview",
    "name",
    "cmc",
    "color",
    "rarity",
    "score",
    "expert",
    "my_eval",
    "my_notes",
    "gih_wr",
    "iwd",
    "oh_wr",
    "gd_wr",
    "ata",
    "alsa",
    "gih_games",
    "oracle_text",
    "link",
    "set",
    "collector_number",
]


class SheetsError(RuntimeError):
    """Raised when Google Sheets output cannot proceed."""


# --- auth ---------------------------------------------------------------------


def get_service(credentials_path: Path | None = None):
    """Return an authorized Sheets API service, running the OAuth flow if needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SheetsError(
            "Google API libraries are not installed. Run `uv sync`."
        ) from exc

    cred_path = Path(credentials_path or DEFAULT_CRED_PATH)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not cred_path.exists():
                raise SheetsError(
                    f"No OAuth client file at {cred_path}. Create one (Google Cloud "
                    "Console > APIs & Services > Credentials > OAuth client ID > "
                    "Desktop app), download the JSON there, then rerun. See README."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return build("sheets", "v4", credentials=creds, cache_discovery=False)


# --- spreadsheet lifecycle ----------------------------------------------------


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_spreadsheet(service, set_code: str) -> tuple[str, str]:
    """Return (spreadsheet_id, url), reusing the stored workbook or creating one."""
    state = _load_state()
    title = f"{set_code.upper()}_Scores"
    ssid = state.get(set_code.upper())

    if ssid:
        try:
            meta = (
                service.spreadsheets()
                .get(spreadsheetId=ssid, fields="spreadsheetId,spreadsheetUrl")
                .execute()
            )
            return meta["spreadsheetId"], meta["spreadsheetUrl"]
        except Exception:  # noqa: BLE001 - stale id, recreate below
            pass

    body = {
        "properties": {"title": title},
        "sheets": [{"properties": {"title": t}} for t in ALL_TABS],
    }
    created = (
        service.spreadsheets()
        .create(body=body, fields="spreadsheetId,spreadsheetUrl")
        .execute()
    )
    ssid = created["spreadsheetId"]
    state[set_code.upper()] = ssid
    _save_state(state)
    return ssid, created["spreadsheetUrl"]


def _sheet_meta(service, ssid: str) -> dict:
    """Map tab title -> (sheetId, [chartId...])."""
    data = (
        service.spreadsheets()
        .get(spreadsheetId=ssid, fields="sheets(properties(sheetId,title),charts(chartId))")
        .execute()
    )
    out = {}
    for sheet in data.get("sheets", []):
        props = sheet["properties"]
        charts = [c["chartId"] for c in sheet.get("charts", [])]
        out[props["title"]] = {"sheetId": props["sheetId"], "charts": charts}
    return out


# --- value building -----------------------------------------------------------


def _cell(value):
    """Normalize a dataframe value for the Sheets API (NA -> empty)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, str):
        return value
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return value


def read_existing_eval(service, ssid: str) -> pd.DataFrame | None:
    """Read back [set, collector_number, my_eval, my_notes] from the Cards tab."""
    try:
        resp = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=ssid, range=f"{CARDS_TAB}!A1:Z5000")
            .execute()
        )
    except Exception:  # noqa: BLE001 - tab may not exist yet
        return None

    values = resp.get("values", [])
    if not values:
        return None
    header = values[0]
    idx = {h: i for i, h in enumerate(header)}
    need = ["set", "collector_number", "my_eval", "my_notes"]
    if not all(k in idx for k in ("set", "collector_number")):
        return None

    records = []
    for row in values[1:]:
        rec = {}
        for k in need:
            i = idx.get(k)
            rec[k] = row[i] if i is not None and i < len(row) else ""
        records.append(rec)
    df = pd.DataFrame(records)
    if df.empty:
        return None
    return df


def _build_card_values(df: pd.DataFrame) -> list[list]:
    """Header + one row per card, with IMAGE/HYPERLINK formulas for preview/link."""
    rows: list[list] = [CARD_HEADERS]
    for _, r in df.iterrows():
        img = _cell(r.get("image_url"))
        uri = _cell(r.get("scryfall_uri"))
        preview = f'=IMAGE("{img}",4,98,70)' if img else ""
        link = f'=HYPERLINK("{uri}","Scryfall")' if uri else ""
        rows.append(
            [
                preview,
                _cell(r.get("name")),
                _cell(r.get("cmc")),
                _cell(r.get("colors")),
                _cell(r.get("rarity")),
                _cell(r.get("score")),
                _cell(r.get("expert_grade")),
                _cell(r.get("my_eval")),
                _cell(r.get("my_notes")),
                _cell(r.get("gih_wr")),
                _cell(r.get("iwd")),
                _cell(r.get("oh_wr")),
                _cell(r.get("gd_wr")),
                _cell(r.get("ata")),
                _cell(r.get("alsa")),
                _cell(r.get("gih_games")),
                _cell(r.get("oracle_text")),
                link,
                _cell(r.get("set")),
                _cell(r.get("collector_number")),
            ]
        )
    return rows


def _rarity_chart_values(df: pd.DataFrame, rarity: str) -> list[list]:
    """Table [Preview, Card, GIH WR, Set Avg] for one rarity, sorted by GIH WR desc.

    Preview is an inline card image; Card is a HYPERLINK to Scryfall and doubles
    as the chart's x-axis label; Set Avg is a constant column so it draws as a
    flat line through the card dots.
    """
    sub = df[df["rarity"].astype(str) == rarity].copy()
    sub["_gih"] = pd.to_numeric(sub["gih_wr"], errors="coerce")
    sub = sub.dropna(subset=["_gih"])
    header = ["Preview", "Card", "GIH WR", "Set Avg"]
    if sub.empty:
        return [header]
    avg = round(float(sub["_gih"].mean()), 4)
    # Order by card name so the chart is a true scatter (no GIH-WR trend on the
    # x-axis); the flat Set Avg line is then the single linear reference.
    sub = sub.sort_values("name", key=lambda s: s.astype(str).str.lower())
    rows = [header]
    for _, r in sub.iterrows():
        name = _cell(r["name"])
        uri = _cell(r.get("scryfall_uri"))
        img = _cell(r.get("image_url"))
        preview = f'=IMAGE("{img}",4,98,70)' if img else ""
        card = f'=HYPERLINK("{uri}","{name}")' if uri else name
        rows.append([preview, card, round(float(r["_gih"]), 4), avg])
    return rows


def _tricks_values(df: pd.DataFrame, trick_names: set | None = None) -> list[list]:
    """Combat-tricks table: image, card link, mana, color, rarity, type, score, GIH WR."""
    table = scoring.combat_tricks(df, trick_names)
    header = ["Preview", "Card", "CMC", "Color", "Rarity", "Type", "Score", "GIH WR"]
    rows = [header]
    for _, r in table.iterrows():
        img = _cell(r.get("image_url"))
        uri = _cell(r.get("scryfall_uri"))
        gih = pd.to_numeric(pd.Series([r.get("gih_wr")]), errors="coerce").iloc[0]
        rows.append([
            f'=IMAGE("{img}",4,98,70)' if img else "",
            f'=HYPERLINK("{uri}","{_cell(r.get("name"))}")' if uri else _cell(r.get("name")),
            _cell(r.get("cmc")),
            _cell(r.get("colors")),
            _cell(r.get("rarity")),
            _cell(r.get("trick_type")),
            _cell(r.get("score")),
            round(float(gih), 4) if pd.notna(gih) else "",
        ])
    return rows


def _color_table_values(table: pd.DataFrame) -> list[list]:
    rows = [["Color", "Win Rate", "Games"]]
    for _, r in table.iterrows():
        wr = r["win_rate"]
        rows.append([_cell(r["color"]), round(float(wr), 4) if pd.notna(wr) else "",
                     _cell(r["games"])])
    return rows


def _combo_table_values(table: pd.DataFrame) -> list[list]:
    rows = [["Archetype", "Win Rate", "Games"]]
    for _, r in table.iterrows():
        wr = r["win_rate"]
        rows.append([_cell(r["archetype"]), round(float(wr), 4) if pd.notna(wr) else "",
                     _cell(r["games"])])
    return rows


def _arch_cell(row) -> str:
    """A guild card cell: the card's image preview."""
    img = _cell(row.get("image_url"))
    return f'=IMAGE("{img}",4,98,70)' if img else _cell(row.get("name"))


def _archetype_values(
    df: pd.DataFrame, arch_data: dict | None = None
) -> tuple[list[list], list[str], list[int]]:
    """Wide grid: one column per guild, rows are the top picks (by in-archetype WR).

    Returns (rows, guild_pairs, section_row_indexes). Layout:
        (blank) | Azorius (WU) | Orzhov (WB) | ... (10 guild columns)
        Top Commons
        1..5    | card 62% per guild ...
        Top Uncommons
        1..5    | ...
    """
    guilds = scoring.GUILD_PAIRS
    arch_data = arch_data or {}
    header = [""] + [f"{name} ({pair})" for pair, name in guilds]
    rows: list[list] = [header]
    section_rows: list[int] = []

    for title, rarity in (("Top Commons", "common"), ("Top Uncommons", "uncommon")):
        lists = {
            pair: scoring.guild_top_cards(df, pair, rarity, arch_data.get(pair), 5)
            for pair, _ in guilds
        }
        section_rows.append(len(rows))
        rows.append([title] + [""] * len(guilds))
        for i in range(5):
            row = [str(i + 1)]
            for pair, _ in guilds:
                lst = lists[pair]
                row.append(_arch_cell(lst.iloc[i]) if i < len(lst) else "")
            rows.append(row)

    return rows, [pair for pair, _ in guilds], section_rows


# --- chart + format requests --------------------------------------------------


def _src(sheet_id: int, n_rows: int, c: int) -> dict:
    return {
        "sourceRange": {
            "sources": [
                {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": n_rows + 1,
                    "startColumnIndex": c,
                    "endColumnIndex": c + 1,
                }
            ]
        }
    }


def _dots_line_chart_request(sheet_id: int, title: str, n_rows: int) -> dict:
    """One dot per card (x = card name, y = GIH WR) with the average as a line.

    Table layout is [Preview, Card, GIH WR, Set Avg]. A COMBO chart with the card
    series drawn as points only (invisible connecting line) and the average drawn
    as a solid line through them, so above/below-average cards read at a glance.
    """
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": "COMBO",
                        "legendPosition": "BOTTOM_LEGEND",
                        "headerCount": 1,
                        "domains": [{"domain": _src(sheet_id, n_rows, 1)}],
                        "series": [
                            {
                                "series": _src(sheet_id, n_rows, 2),
                                "type": "LINE",
                                "targetAxis": "LEFT_AXIS",
                                "lineStyle": {"type": "INVISIBLE"},
                                "pointStyle": {"size": 5, "shape": "CIRCLE"},
                            },
                            {
                                "series": _src(sheet_id, n_rows, 3),
                                "type": "LINE",
                                "targetAxis": "LEFT_AXIS",
                                "lineStyle": {"type": "SOLID", "width": 2},
                            },
                        ],
                        "axis": [
                            {"position": "BOTTOM_AXIS", "title": "Card"},
                            {"position": "LEFT_AXIS", "title": "GIH WR"},
                        ],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": 1,
                            "columnIndex": 5,
                        },
                        "widthPixels": 900,
                        "heightPixels": 420,
                    }
                },
            }
        }
    }


def _column_chart_request(
    sheet_id: int, title: str, n_rows: int, point_colors: list[dict] | None = None
) -> dict:
    """Single-series COLUMN chart of win rate (col 1) by label (col 0).

    ``point_colors`` (one rgb dict per row, in table order) colors each bar to
    match its MTG color.
    """
    series = {"series": _src(sheet_id, n_rows, 1), "targetAxis": "LEFT_AXIS"}
    if point_colors:
        series["styleOverrides"] = [
            {"index": i, "colorStyle": {"rgbColor": rgb}}
            for i, rgb in enumerate(point_colors)
        ]
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": "COLUMN",
                        "legendPosition": "NO_LEGEND",
                        "headerCount": 1,
                        "domains": [{"domain": _src(sheet_id, n_rows, 0)}],
                        "series": [series],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": 1,
                            "columnIndex": 4,
                        },
                        "widthPixels": 640,
                        "heightPixels": 380,
                    }
                },
            }
        }
    }


def _format_cards_requests(sheet_id: int, n_rows: int) -> list[dict]:
    """Freeze + bold header, size the preview column/rows, tint editable columns."""
    reqs: list[dict] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        # Preview column width.
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 1,
                },
                "properties": {"pixelSize": 80},
                "fields": "pixelSize",
            }
        },
        # Row heights to fit the card thumbnails.
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": n_rows + 1,
                },
                "properties": {"pixelSize": 104},
                "fields": "pixelSize",
            }
        },
        # Tint the editable my_eval / my_notes columns (H,I -> indices 7,8).
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": n_rows + 1,
                    "startColumnIndex": 7,
                    "endColumnIndex": 9,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1.0, "green": 0.98, "blue": 0.8}
                    }
                },
                "fields": "userEnteredFormat.backgroundColor",
            }
        },
    ]
    return reqs


def _freeze_and_bold_header(sheet_id: int) -> list[dict]:
    return [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat",
            }
        },
    ]


def _col_width(sheet_id: int, col: int, px: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }


def _row_heights(sheet_id: int, start: int, end: int, px: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": start, "endIndex": end},
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }


def _format_rarity_table_requests(sheet_id: int, n_rows: int) -> list[dict]:
    """Commons/Uncommons: freeze header, size the preview column and image rows."""
    return [
        *_freeze_and_bold_header(sheet_id),
        _col_width(sheet_id, 0, 80),
        _row_heights(sheet_id, 1, n_rows + 1, 104),
    ]


def _format_archetype_grid_requests(
    sheet_id: int, guild_pairs: list[str], section_rows: list[int], n_rows: int
) -> list[dict]:
    """Wide archetype grid: freeze header row + label column, color guild headers."""
    reqs: list[dict] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
                },
                "fields": "gridProperties(frozenRowCount,frozenColumnCount)",
            }
        },
        _col_width(sheet_id, 0, 80),
    ]
    # One colored, bold header cell per guild.
    for i, pair in enumerate(guild_pairs):
        rgb = scoring.color_rgb(pair)
        dark = (rgb["red"] * 0.299 + rgb["green"] * 0.587 + rgb["blue"] * 0.114) < 0.55
        text_fmt = {"bold": True}
        if dark:
            text_fmt["foregroundColor"] = {"red": 1, "green": 1, "blue": 1}
        reqs.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                        "startColumnIndex": i + 1, "endColumnIndex": i + 2,
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": rgb, "textFormat": text_fmt}},
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            }
        )
        reqs.append(_col_width(sheet_id, i + 1, 84))
    # Bold + tint the section label rows, and size the 5 image rows beneath each.
    for r in section_rows:
        reqs.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.92, "green": 0.92, "blue": 0.92},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            }
        )
        reqs.append(_row_heights(sheet_id, r + 1, r + 6, 104))
    return reqs


# --- top-level write ----------------------------------------------------------


def _notes_template(set_code: str) -> tuple[list[list], list[int]]:
    """Rows + header row indexes for a fresh Notes tab."""
    rows: list[list] = [
        [f"{set_code} notes  (free-form; this tab is preserved across weekly refreshes)"],
        [""],
    ]
    headers: list[int] = []
    for section in NOTES_SECTIONS:
        headers.append(len(rows))
        rows.append([section])
        rows.extend([[""], [""], [""], [""]])  # room to write
    return rows, headers


def _seed_notes_if_empty(service, ssid: str, sheet_id: int, set_code: str) -> None:
    """Seed the Notes tab with a template only if it has no content yet.

    This is what preserves your notes: once anything is written there, reruns
    never touch it.
    """
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=ssid, range=f"{NOTES_TAB}!A1:A50")
        .execute()
    )
    has_content = any(
        any(str(c).strip() for c in row) for row in resp.get("values", [])
    )
    if has_content:
        return

    rows, header_idx = _notes_template(set_code)
    _write_values(service, ssid, NOTES_TAB, rows)
    reqs: list[dict] = [
        _col_width(sheet_id, 0, 720),
        # Title row.
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 13}}},
                "fields": "userEnteredFormat.textFormat",
            }
        },
        # Wrap long notes in column A.
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        },
    ]
    for h in header_idx:
        reqs.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": h, "endRowIndex": h + 1},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 11},
                            "backgroundColor": {"red": 0.92, "green": 0.92, "blue": 0.92},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            }
        )
    service.spreadsheets().batchUpdate(spreadsheetId=ssid, body={"requests": reqs}).execute()


def _write_values(service, ssid: str, tab: str, values: list[list]) -> None:
    service.spreadsheets().values().update(
        spreadsheetId=ssid,
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def write_sheets(
    service, ssid: str, df: pd.DataFrame, set_code: str,
    colors_df: pd.DataFrame | None = None, trick_names: set | None = None,
    arch_data: dict | None = None,
) -> None:
    """Populate all tabs and (re)build the charts for one set.

    ``colors_df`` is 17Lands color-ratings data (see seventeen_lands.fetch_colors);
    it drives the Best Color and Color Pairs tabs. If None/empty those tabs are
    left empty.
    """
    meta = _sheet_meta(service, ssid)

    # Create any tabs added since this workbook was first made.
    missing = [t for t in ALL_TABS if t not in meta]
    if missing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=ssid,
            body={"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]},
        ).execute()
        meta = _sheet_meta(service, ssid)

    # Clear prior content + charts so reruns do not stack duplicates. The Notes
    # tab is never cleared -- it holds your hand-written notes.
    clear_reqs: list[dict] = []
    for tab, info in meta.items():
        if tab == NOTES_TAB:
            continue
        clear_reqs.append(
            {"updateCells": {"range": {"sheetId": info["sheetId"]}, "fields": "*"}}
        )
        for chart_id in info["charts"]:
            clear_reqs.append({"deleteEmbeddedObject": {"objectId": chart_id}})
    if clear_reqs:
        service.spreadsheets().batchUpdate(
            spreadsheetId=ssid, body={"requests": clear_reqs}
        ).execute()

    # Cards tab.
    _write_values(service, ssid, CARDS_TAB, _build_card_values(df))

    # Chart tables.
    commons_vals = _rarity_chart_values(df, "common")
    uncommons_vals = _rarity_chart_values(df, "uncommon")
    color_tbl = scoring.color_table(colors_df)
    combo_tbl = scoring.combo_table(colors_df)
    color_vals = _color_table_values(color_tbl)
    pairs_vals = _combo_table_values(combo_tbl)
    arch_vals, arch_guilds, arch_sections = _archetype_values(df, arch_data)
    tricks_vals = _tricks_values(df, trick_names)
    _write_values(service, ssid, COMMONS_TAB, commons_vals)
    _write_values(service, ssid, UNCOMMONS_TAB, uncommons_vals)
    _write_values(service, ssid, COLOR_TAB, color_vals)
    _write_values(service, ssid, PAIRS_TAB, pairs_vals)
    _write_values(service, ssid, ARCHETYPES_TAB, arch_vals)
    _write_values(service, ssid, TRICKS_TAB, tricks_vals)

    # Bar colors keyed to MTG color, in table (win-rate) order.
    color_bar_colors = [scoring.color_rgb(c) for c in color_tbl["color"]]
    pair_bar_colors = [scoring.color_rgb(p) for p in combo_tbl["pair"]]

    # Formatting + charts in one batch.
    reqs: list[dict] = _format_cards_requests(meta[CARDS_TAB]["sheetId"], len(df))
    reqs += _format_rarity_table_requests(meta[COMMONS_TAB]["sheetId"], len(commons_vals) - 1)
    reqs += _format_rarity_table_requests(meta[UNCOMMONS_TAB]["sheetId"], len(uncommons_vals) - 1)
    reqs += _format_archetype_grid_requests(
        meta[ARCHETYPES_TAB]["sheetId"], arch_guilds, arch_sections, len(arch_vals)
    )
    reqs += _format_rarity_table_requests(meta[TRICKS_TAB]["sheetId"], len(tricks_vals) - 1)
    if len(commons_vals) > 1:
        reqs.append(
            _dots_line_chart_request(
                meta[COMMONS_TAB]["sheetId"],
                "Commons GIH WR vs common average",
                len(commons_vals) - 1,
            )
        )
    if len(uncommons_vals) > 1:
        reqs.append(
            _dots_line_chart_request(
                meta[UNCOMMONS_TAB]["sheetId"],
                "Uncommons GIH WR vs uncommon average",
                len(uncommons_vals) - 1,
            )
        )
    if len(color_vals) > 1:
        reqs.append(
            _column_chart_request(
                meta[COLOR_TAB]["sheetId"], "Best color by win rate (17Lands)",
                len(color_vals) - 1, color_bar_colors,
            )
        )
    if len(pairs_vals) > 1:
        reqs.append(
            _column_chart_request(
                meta[PAIRS_TAB]["sheetId"], "Best two-color pair by win rate (17Lands)",
                len(pairs_vals) - 1, pair_bar_colors,
            )
        )

    service.spreadsheets().batchUpdate(
        spreadsheetId=ssid, body={"requests": reqs}
    ).execute()

    # Seed the Notes tab once; never overwrite existing notes.
    _seed_notes_if_empty(service, ssid, meta[NOTES_TAB]["sheetId"], set_code)
