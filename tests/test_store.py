from __future__ import annotations

import pytest

from drama_studio.store import ProjectStore, slugify


def test_slugify():
    assert slugify("Kaltes Licht") == "kaltes-licht"
    assert slugify("The House on Bellweather Road") == "the-house-on-bellweather-road"
    assert slugify("  ...  ") == "untitled"


def test_create_get_list_delete(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("The Test House", "en", "# THE TEST HOUSE\n\nMARA: Hello.")
    assert project.id.startswith("the-test-house-")
    assert store.get(project.id).title == "The Test House"
    assert len(store.list()) == 1
    store.delete(project.id)
    assert store.list() == []
    with pytest.raises(KeyError):
        store.get(project.id)


def test_reparse_updates_script(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("Test", "en", "# Test\n\nMARA: Hello.")
    assert len(project.script.iter_lines()) == 1
    project.raw_script = "# Test\n\nMARA: Hello.\nELIAS: Hi."
    project = store.reparse(project)
    assert len(project.script.iter_lines()) == 2
    assert project.report is not None
    assert project.report.method == "deterministic"


def test_invalid_project_id(tmp_path):
    store = ProjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("../escape")


def test_parse_detects_title_when_blank(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("", "en", "# DETECTED TITLE\n\nMARA: Hello.")
    assert project.title == "DETECTED TITLE"
