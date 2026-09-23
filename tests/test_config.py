"""Offline tests for Phase 7 user settings."""

from __future__ import annotations

import json

from readbox.config import (
    DEFAULT_ROOT_FOLDER,
    Settings,
    load_settings,
    save_settings,
)
from readbox.formatter import filename_for, folder_for


# -- load/save ---------------------------------------------------------------


def test_defaults_when_no_file(tmp_path):
    settings = load_settings(tmp_path / "nope.json")
    assert settings == Settings()
    assert settings.root_folder == DEFAULT_ROOT_FOLDER


def test_partial_override_merges_over_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"root_folder": "Meetings"}))
    settings = load_settings(path)
    assert settings.root_folder == "Meetings"
    # Untouched fields keep their defaults.
    assert settings.folder_template == Settings().folder_template


def test_unknown_and_nonstring_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"bogus": "x", "date_format": 5, "root_folder": "R"}))
    settings = load_settings(path)
    assert settings.root_folder == "R"
    assert settings.date_format == Settings().date_format  # non-string ignored
    assert not hasattr(settings, "bogus")


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json")
    assert load_settings(path) == Settings()


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(Settings(root_folder="Archive", date_format="%d-%m-%Y"), path)
    reloaded = load_settings(path)
    assert reloaded.root_folder == "Archive"
    assert reloaded.date_format == "%d-%m-%Y"


# -- formatter template rendering --------------------------------------------

_MEETING = {"start_time_ms": 1787328000000, "title": "Eng Sync"}  # 2026-08-21


def test_filename_default_template():
    assert filename_for(_MEETING) == "2026-08-21 Eng Sync"


def test_filename_custom_template_and_date_format():
    out = filename_for(_MEETING, template="{title} ({date})", date_format="%b %Y")
    assert out == "Eng Sync (Aug 2026)"


def test_filename_flattens_path_separators():
    # A slash in the title must not create a subfolder in the filename.
    out = filename_for({"start_time_ms": 1787328000000, "title": "A/B"})
    assert "/" not in out


def test_folder_default_and_custom_templates():
    assert folder_for(_MEETING) == "2026/08"
    assert folder_for(_MEETING, template="{year}") == "2026"
    assert folder_for(_MEETING, template="{year}/{month}/{day}") == "2026/08/21"


def test_unknown_token_renders_empty_not_error():
    # {foo} is unknown → dropped; {year} still resolves.
    assert folder_for(_MEETING, template="{year}/{foo}") == "2026"


def test_malformed_template_falls_back_to_default():
    # A stray brace can't render → fall back to the default template.
    assert folder_for(_MEETING, template="{year") == "2026/08"
