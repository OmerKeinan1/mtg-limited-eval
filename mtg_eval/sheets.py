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
ALL_TABS = [CARDS_TAB, COMMONS_TAB, UNCOMMONS_TAB, COLOR_TAB, PAIRS_TAB]

# Cards tab columns, in display order. (set / collector_number kept at the end so
# the sheet is self-sufficient for the preserve-my-eval merge key.)
CARD_HEADERS = [
    "preview",
    "name",
    "cmc",
    "color",
    "rarity",
    "score",
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
    """Table [Card, GIH WR, Set Avg] for one rarity, sorted by GIH WR desc.

    The Card cell is a HYPERLINK to Scryfall so you can preview it from the chart
    table too.
    """
    sub = df[df["rarity"].astype(str) == rarity].copy()
    sub["_gih"] = pd.to_numeric(sub["gih_wr"], errors="coerce")
    sub = sub.dropna(subset=["_gih"]).sort_values("_gih", ascending=False)
    if sub.empty:
        return [["Card", "GIH WR", "Set Avg"]]
    avg = round(float(sub["_gih"].mean()), 4)
    rows = [["Card", "GIH WR", "Set Avg"]]
    for _, r in sub.iterrows():
        name = _cell(r["name"])
        uri = _cell(r.get("scryfall_uri"))
        card = f'=HYPERLINK("{uri}","{name}")' if uri else name
        rows.append([card, round(float(r["_gih"]), 4), avg])
    return rows


def _color_table_values(colors_df: pd.DataFrame) -> list[list]:
    table = scoring.color_table(colors_df)
    rows = [["Color", "Win Rate", "Games"]]
    for _, r in table.iterrows():
        wr = r["win_rate"]
        rows.append([_cell(r["color"]), round(float(wr), 4) if pd.notna(wr) else "",
                     _cell(r["games"])])
    return rows


def _combo_table_values(colors_df: pd.DataFrame) -> list[list]:
    table = scoring.combo_table(colors_df)
    rows = [["Archetype", "Win Rate", "Games"]]
    for _, r in table.iterrows():
        wr = r["win_rate"]
        rows.append([_cell(r["archetype"]), round(float(wr), 4) if pd.notna(wr) else "",
                     _cell(r["games"])])
    return rows


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


def _line_chart_request(sheet_id: int, title: str, n_rows: int) -> dict:
    """LINE chart: GIH WR per card (sorted) plus a flat set-average line.

    Cards whose line sits above the average line are the above-average ones.
    """
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": "LINE",
                        "legendPosition": "BOTTOM_LEGEND",
                        "headerCount": 1,
                        "domains": [{"domain": _src(sheet_id, n_rows, 0)}],
                        "series": [
                            {"series": _src(sheet_id, n_rows, 1), "targetAxis": "LEFT_AXIS"},
                            {"series": _src(sheet_id, n_rows, 2), "targetAxis": "LEFT_AXIS"},
                        ],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": 1,
                            "columnIndex": 4,
                        },
                        "widthPixels": 900,
                        "heightPixels": 420,
                    }
                },
            }
        }
    }


def _column_chart_request(sheet_id: int, title: str, n_rows: int) -> dict:
    """Single-series COLUMN chart of win rate (col 1) by label (col 0)."""
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
                        "series": [{"series": _src(sheet_id, n_rows, 1), "targetAxis": "LEFT_AXIS"}],
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
        # Tint the editable my_eval / my_notes columns (G,H -> indices 6,7).
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": n_rows + 1,
                    "startColumnIndex": 6,
                    "endColumnIndex": 8,
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


# --- top-level write ----------------------------------------------------------


def _write_values(service, ssid: str, tab: str, values: list[list]) -> None:
    service.spreadsheets().values().update(
        spreadsheetId=ssid,
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def write_sheets(
    service, ssid: str, df: pd.DataFrame, set_code: str, colors_df: pd.DataFrame | None = None
) -> None:
    """Populate all tabs and (re)build the charts for one set.

    ``colors_df`` is 17Lands color-ratings data (see seventeen_lands.fetch_colors);
    it drives the Best Color and Color Pairs tabs. If None/empty those tabs are
    left empty.
    """
    meta = _sheet_meta(service, ssid)

    # Clear prior content + charts so reruns do not stack duplicates.
    clear_reqs: list[dict] = []
    for tab, info in meta.items():
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
    color_vals = _color_table_values(colors_df)
    pairs_vals = _combo_table_values(colors_df)
    _write_values(service, ssid, COMMONS_TAB, commons_vals)
    _write_values(service, ssid, UNCOMMONS_TAB, uncommons_vals)
    _write_values(service, ssid, COLOR_TAB, color_vals)
    _write_values(service, ssid, PAIRS_TAB, pairs_vals)

    # Formatting + charts in one batch.
    reqs: list[dict] = _format_cards_requests(meta[CARDS_TAB]["sheetId"], len(df))
    if len(commons_vals) > 1:
        reqs.append(
            _line_chart_request(
                meta[COMMONS_TAB]["sheetId"],
                "Commons GIH WR vs common average",
                len(commons_vals) - 1,
            )
        )
    if len(uncommons_vals) > 1:
        reqs.append(
            _line_chart_request(
                meta[UNCOMMONS_TAB]["sheetId"],
                "Uncommons GIH WR vs uncommon average",
                len(uncommons_vals) - 1,
            )
        )
    if len(color_vals) > 1:
        reqs.append(
            _column_chart_request(
                meta[COLOR_TAB]["sheetId"], "Best color by win rate (17Lands)",
                len(color_vals) - 1,
            )
        )
    if len(pairs_vals) > 1:
        reqs.append(
            _column_chart_request(
                meta[PAIRS_TAB]["sheetId"], "Best two-color pair by win rate (17Lands)",
                len(pairs_vals) - 1,
            )
        )

    service.spreadsheets().batchUpdate(
        spreadsheetId=ssid, body={"requests": reqs}
    ).execute()
