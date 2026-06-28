"""Tests for sheet-building helpers that are pure (no network)."""

from __future__ import annotations

from mtg_eval import sheets


def test_notes_template_has_title_and_sections():
    rows, headers = sheets._notes_template("MSH")
    flat = [r[0] for r in rows]
    assert flat[0].startswith("MSH notes")
    # Every configured section appears, and its index is flagged as a header.
    for section in sheets.NOTES_SECTIONS:
        assert section in flat
    assert all(rows[i][0] in sheets.NOTES_SECTIONS for i in headers)
    # There is writing room (blank rows) after the last header.
    assert len(rows) > headers[-1] + 1


def test_notes_tab_is_in_all_tabs():
    assert sheets.NOTES_TAB in sheets.ALL_TABS
