"""mtg-eval CLI entrypoint.

Exit codes:
  0  success
  2  Scryfall failure (nothing written)
  3  17Lands failure (file still written with blank stat columns)
  4  merge failure (existing eval could not be preserved -- hard stop)
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from . import merge as merge_mod
from . import scryfall, seventeen_lands

CACHE_DIR = Path("evaluations/.cache")


def _default_output(set_code: str, fmt: str) -> Path:
    ext = "md" if fmt == "md" else "csv"
    return Path("evaluations") / f"{set_code.upper()}.{ext}"


@click.command()
@click.argument("set_code")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["csv", "md"]),
    default="csv",
    help="Output format (default csv).",
)
@click.option(
    "--include-basics", is_flag=True, help="Include basic lands (off by default)."
)
@click.option(
    "--17lands-format",
    "sl_format",
    type=click.Choice(["PremierDraft", "TradDraft", "Sealed"]),
    default="PremierDraft",
    help="17Lands format to pull stats from (default PremierDraft).",
)
@click.option("--refresh", is_flag=True, help="Bypass caches and refetch.")
@click.option(
    "--output",
    "output",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the default evaluations/<SET>.<ext> output path.",
)
def main(
    set_code: str,
    fmt: str,
    include_basics: bool,
    sl_format: str,
    refresh: bool,
    output: Path | None,
) -> None:
    """Build a per-set Limited evaluation file for SET_CODE (e.g. MH3, DSK, TDM)."""
    load_dotenv()
    set_code = set_code.upper()
    out_path = output or _default_output(set_code, fmt)

    # --- Scryfall (fatal on failure) ---
    try:
        scry_df = scryfall.fetch_set(
            set_code, CACHE_DIR, refresh=refresh, include_basics=include_basics
        )
    except scryfall.ScryfallError as exc:
        click.echo(f"Scryfall error: {exc}", err=True)
        sys.exit(2)

    if scry_df.empty:
        click.echo(f"Scryfall returned 0 cards for {set_code}.", err=True)
        sys.exit(2)
    click.echo(f"Fetching Scryfall for {set_code}... {len(scry_df)} cards")

    # --- 17Lands (non-fatal; write blanks if it fails) ---
    sl_failed = False
    try:
        sl_df = seventeen_lands.fetch_set(
            set_code, CACHE_DIR, fmt=sl_format, refresh=refresh
        )
        n_data = int(sl_df["gih_wr"].notna().sum()) if not sl_df.empty else 0
        click.echo(
            f"Fetching 17Lands {sl_format} for {set_code}... "
            f"{n_data} cards with data"
        )
    except seventeen_lands.SeventeenLandsError as exc:
        sl_failed = True
        sl_df = seventeen_lands.empty_frame()
        click.echo(f"17Lands error: {exc}", err=True)
        click.echo("Continuing with blank 17Lands columns.", err=True)

    # --- Merge + preserve eval (fatal on failure) ---
    try:
        combined = merge_mod.merge(scry_df, sl_df, out_path)
    except merge_mod.MergeError as exc:
        click.echo(f"Merge error (eval not preserved): {exc}", err=True)
        sys.exit(4)

    preserved = int(
        (combined["my_eval"].astype(str).str.strip() != "").sum()
    )
    click.echo(
        f"Merging with existing eval at {out_path}... "
        f"preserved {preserved} my_eval values"
    )

    merge_mod.write_output(combined, out_path, fmt=fmt)
    click.echo(
        f"Wrote {out_path} ({len(combined)} rows, {len(combined.columns)} columns)"
    )

    if sl_failed:
        sys.exit(3)


if __name__ == "__main__":
    main()
