"""Headless installer CLI (also used by the wizard's uninstall path)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import INSTALLER_VERSION
from .detect import detect_system
from .installer import InstallOutcome, plan_text, run_install
from .paths import InstallPaths, load_pins, resolve_prefix
from .plan import (
    ALL_COMPONENTS,
    CORE,
    DEVICE_CHOICES,
    Selection,
    download_estimate,
    effective_device,
    installed_estimate,
    parse_components,
    plan_payload,
    validate_selection,
)
from .progress import Progress
from .state import load_state, save_state
from .uninstall import uninstall, uninstall_summary
from .util import Cancelled, InstallerError, human_bytes
from .verify import verify_install


class ConsoleProgress(Progress):
    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet
        self._last: dict[str, int] = {}

        def update(step: str, fraction: float, message: str) -> None:
            if self.quiet:
                return
            decile = int(fraction * 10)
            if self._last.get(step) == decile and fraction < 1.0:
                return
            self._last[step] = decile
            text = message or step
            print(f"[{fraction * 100:3.0f}%] {text}", flush=True)

        def log(message: str) -> None:
            if not self.quiet:
                print(f"    {message}", flush=True)

        super().__init__(on_update=update, on_log=log)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Drama Llama installer",
        description="Install, repair, verify or remove a per-user Drama Llama.",
    )
    parser.add_argument("--headless", action="store_true", help="never start the GUI")
    parser.add_argument("--yes", action="store_true", help="assume yes for admin prompts")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved plan and exit without changing anything",
    )
    parser.add_argument("--cpu", dest="device", action="store_const", const="cpu")
    parser.add_argument("--gpu", dest="device", action="store_const", const="gpu")
    parser.add_argument("--device", choices=DEVICE_CHOICES, default="auto")
    parser.add_argument("--prefix", help="install root (default: per-user data dir)")
    parser.add_argument("--data-dir", help="project/data directory (default: <prefix>/data)")
    parser.add_argument(
        "--components",
        default=CORE,
        help=f"comma-separated components ({', '.join(ALL_COMPONENTS)}) or all",
    )
    parser.add_argument(
        "--accept-breeze-license",
        action="store_true",
        help="accept the Breeze research/non-commercial model licence",
    )
    parser.add_argument(
        "--allow-docker-install",
        action="store_true",
        help="allow an elevated Docker installation (also implied by --yes)",
    )
    parser.add_argument("--repair", action="store_true", help="re-check and repair an install")
    parser.add_argument("--verify", action="store_true", help="verify an existing install")
    parser.add_argument("--uninstall", action="store_true", help="remove Drama Llama")
    parser.add_argument(
        "--keep-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep projects and settings on uninstall (default: keep)",
    )
    parser.add_argument(
        "--purge",
        "--purge-models",
        dest="purge_models",
        action="store_true",
        help="also remove downloaded models and Docker images",
    )
    parser.add_argument("--state", action="store_true", help="print the install state as JSON")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--log-file", help="write installer output here as well")
    parser.add_argument(
        "--installer-version",
        action="version",
        version=f"Drama Llama installer {INSTALLER_VERSION}",
    )
    return parser


def selection_from_args(args: argparse.Namespace, pins: dict) -> Selection:
    prefix = resolve_prefix(args.prefix)
    components = parse_components(args.components)
    return Selection(
        components=frozenset(components),
        device=args.device,
        prefix=prefix,
        data_dir=Path(args.data_dir).expanduser() if args.data_dir else None,
        accept_breeze_license=bool(args.accept_breeze_license),
        allow_docker_install=bool(args.allow_docker_install or args.yes),
    )


def _selection_from_state(prefix: Path, pins: dict) -> Selection:
    state = load_state(prefix) or {}
    components = [
        name
        for name, meta in state.get("components", {}).items()
        if meta.get("status") in {"installed", "failed", "pending_reboot"}
    ] or [CORE]
    return Selection(
        components=frozenset(components),
        device=str(state.get("device", "auto")),
        prefix=prefix,
        data_dir=Path(state["data_dir"]) if state.get("data_dir") else None,
        accept_breeze_license=True,
        allow_docker_install=False,
    )


def _print_outcome(outcome: InstallOutcome, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "ok": outcome.ok,
                    "cancelled": outcome.cancelled,
                    "error": outcome.error,
                    "warnings": outcome.warnings,
                    "state": outcome.state,
                    "verify": (
                        {
                            "ok": outcome.verify.ok,
                            "checks": [
                                {
                                    "name": check.name,
                                    "ok": check.ok,
                                    "detail": check.detail,
                                }
                                for check in outcome.verify.checks
                            ],
                        }
                        if outcome.verify
                        else None
                    ),
                },
                indent=2,
            )
        )
        return
    for warning in outcome.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if outcome.cancelled:
        print("Installation cancelled.")
        return
    if outcome.ok:
        state = outcome.state or {}
        print(f"Drama Llama is installed at {state.get('prefix')}")
        print(f"Launch it from the Start Menu or with: {state.get('prefix')}/bin/drama-llama ui")
    else:
        print(f"Installation failed: {outcome.error}", file=sys.stderr)


def _handle_uninstall(args: argparse.Namespace, as_json: bool) -> int:
    prefix = resolve_prefix(args.prefix)
    paths = InstallPaths.for_prefix(prefix)
    progress = ConsoleProgress(quiet=as_json)
    removed = uninstall(
        paths,
        keep_data=bool(args.keep_data),
        purge_models=bool(args.purge_models),
        progress=progress,
    )
    if as_json:
        print(json.dumps({"removed": removed, "keep_data": args.keep_data}, indent=2))
    else:
        print(uninstall_summary(removed, keep_data=bool(args.keep_data), data=paths.data))
    return 0


def _handle_state(args: argparse.Namespace) -> int:
    prefix = resolve_prefix(args.prefix)
    state = load_state(prefix)
    if state is None:
        print(f"No Drama Llama install found at {prefix}", file=sys.stderr)
        return 1
    print(json.dumps(state, indent=2))
    return 0


def _handle_verify(args: argparse.Namespace, pins: dict, as_json: bool) -> int:
    prefix = resolve_prefix(args.prefix)
    system = detect_system(prefix)
    selection = _selection_from_state(prefix, pins)
    progress = ConsoleProgress(quiet=as_json)
    result = verify_install(
        InstallPaths.for_prefix(prefix),
        pins,
        system,
        selection,
        progress,
        breeze_de_cpu=bool((load_state(prefix) or {}).get("breeze_de_cpu", False)),
    )
    if as_json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "checks": [
                        {"name": check.name, "ok": check.ok, "detail": check.detail}
                        for check in result.checks
                    ],
                },
                indent=2,
            )
        )
    else:
        for check in result.checks:
            mark = "ok" if check.ok else "FAIL"
            print(f"  [{mark}] {check.name}: {check.detail}")
        print(result.summary())
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(args.json)
    try:
        if args.uninstall:
            return _handle_uninstall(args, as_json)
        if args.state:
            return _handle_state(args)
        pins = load_pins()
        prefix = resolve_prefix(args.prefix)
        system = detect_system(prefix)
        if args.verify:
            return _handle_verify(args, pins, as_json)
        selection = selection_from_args(args, pins)
        warnings = validate_selection(selection, pins, system)
        payload = plan_payload(selection, pins, system)
        if args.dry_run:
            if as_json:
                print(json.dumps(payload, indent=2))
            else:
                print(plan_text(selection, pins, system))
                print()
                print("steps:")
                for step in payload["steps"]:
                    optional = " (optional)" if step["optional"] else ""
                    print(
                        f"  - {step['title']}{optional}: "
                        f"{human_bytes(step['download_bytes'])}"
                    )
                print()
                for warning in warnings:
                    print(f"warning: {warning}")
                print()
                print(
                    f"download {human_bytes(payload['download_bytes'])}, "
                    f"on disk {human_bytes(payload['installed_bytes'])}"
                )
            return 0
        if as_json:
            progress = ConsoleProgress(quiet=True)
        else:
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            progress = ConsoleProgress()
        outcome = run_install(
            selection,
            pins,
            system,
            progress,
            repair=bool(args.repair),
        )
        _print_outcome(outcome, as_json=as_json)
        return 0 if outcome.ok else 1
    except Cancelled:
        print("cancelled", file=sys.stderr)
        return 130
    except InstallerError as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130


def estimate(selection: Selection, pins: dict, system) -> dict[str, int]:
    return {
        "download": download_estimate(selection, pins, system),
        "installed": installed_estimate(selection, pins, system),
        "device": effective_device(selection, system),
    }


def save_install_state(prefix: Path, state: dict) -> None:
    save_state(prefix, state)
