"""Tests for the Card Game Base expert-grade scraper (parser only, no network)."""

from __future__ import annotations

from mtg_eval import expert_grades as eg

_HTML = """
<table class="tablepress tierlist white-table">
  <thead><tr><th>White</th><th>Grade</th></tr></thead>
  <tbody>
    <tr><td>Captain Marvel, Earth&#039;s Protector</td><td>A+</td></tr>
    <tr><td>Monica Rambeau // Photon, Living Light</td><td>A</td></tr>
    <tr><td>Some Filler</td><td>D-</td></tr>
  </tbody>
</table>
<table class="tablepress tierlist blue-table">
  <tbody>
    <tr><th>Blue</th><th>Grade</th></tr>
    <tr><td>Leader, Super-Genius</td><td>A</td></tr>
    <tr><td>Not A Card</td><td>notagrade</td></tr>
  </tbody>
</table>
"""


def test_parse_grades_extracts_name_grade_pairs():
    g = eg.parse_grades(_HTML)
    assert g["captain marvel, earth's protector"] == "A+"
    assert g["monica rambeau // photon, living light"] == "A"
    assert g["leader, super-genius"] == "A"


def test_parse_grades_skips_headers_and_nongrades():
    g = eg.parse_grades(_HTML)
    assert "white" not in g and "blue" not in g  # header rows
    assert "not a card" not in g  # invalid grade text


def test_slug_for():
    assert eg.slug_for("Marvel Super Heroes") == "marvel-super-heroes-draft-tier-list"
    assert eg.slug_for("Duskmourn: House of Horror") == (
        "duskmourn-house-of-horror-draft-tier-list"
    )


def test_empty_html():
    assert eg.parse_grades("<html></html>") == {}
