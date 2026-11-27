from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _tests_do_not_depend_on_an_installed_cosyvoice(monkeypatch):
    """New German projects pick CosyVoice when it is installed on the machine.

    Without this the same test would behave differently on a developer's
    machine and in CI. Tests of that behaviour override it themselves.
    """
    try:
        from drama_studio import api
    except ImportError:  # installer-only environments
        return
    monkeypatch.setattr(api, "_cosyvoice_installed", lambda: False)
