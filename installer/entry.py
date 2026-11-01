"""Single entry point for the wizard executable and ``python -m installer``.

The frozen ``DramaLlama-Setup`` binary and the dev checkout both call
:func:`main`. It decides between the PySide6 wizard and the headless CLI
without importing Qt unless the wizard is actually going to run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller runs the entry script as a top-level module, and a developer may
# call ``python installer/entry.py`` directly. Put the repository root on the
# path so the absolute ``installer.*`` imports below resolve in both cases.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Console actions are unambiguous: the Inno Setup uninstaller and CI call these
# with no display available, so never try to start Qt for them.
CONSOLE_FLAGS = {
    "--headless",
    "--dry-run",
    "--verify",
    "--state",
    "--uninstall",
    "--json",
    "--repair",
    "--help",
    "-h",
    "--version",
    "--installer-version",
}


def _has_display(system: str | None = None) -> bool:
    if (system or os.name) in {"windows", "nt", "win32"}:
        return True
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def wants_gui(argv: list[str]) -> bool:
    if any(flag in argv for flag in CONSOLE_FLAGS):
        return False
    if os.environ.get("DRAMA_LLAMA_INSTALLER_HEADLESS"):
        return False
    return _has_display()


def ensure_console_streams() -> None:
    """Give a windowed frozen build somewhere to write.

    PyInstaller's ``console=False`` executable has ``sys.stdout``/``stderr``
    set to ``None`` when it is launched without a console (for example from
    Inno Setup's uninstall hook). ``print()`` would then raise
    ``AttributeError`` and turn a clean uninstall into a crash.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ensure_console_streams()
    args = list(sys.argv[1:] if argv is None else argv)
    if wants_gui(args):
        if not _qt_available():
            print(
                "PySide6 is not installed, so the graphical installer is "
                "unavailable.\nRun with --headless to use the command-line "
                "installer.",
                file=sys.stderr,
            )
            return 2
        try:
            from installer.gui.app import run_wizard

            return run_wizard(args)
        except Exception as exc:
            # Never fall through to a headless *install* here: a GUI launch
            # failure must not start downloading GBs without consent.
            print(f"Drama Llama installer GUI failed: {exc}", file=sys.stderr)
            print("Run with --headless to use the command-line installer.", file=sys.stderr)
            return 2
    from installer.core.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":  # pragma: no cover - manual run
    raise SystemExit(main())
