---
title: Installer
tags: [installer, packaging, appimage, inno-setup, pyside6, windows, linux]
sourceRefs:
  - installer/entry.py
  - installer/core/
  - installer/gui/app.py
  - installer/packaging/pyinstaller.spec
  - installer/packaging/inno/DramaLlama.iss
  - installer/packaging/appimage/build-appimage.sh
  - scripts/build_installer.py
  - .github/workflows/installer.yml
  - INSTALL.md
lastReviewed: 2026-11-01
---

# Installer

Native per-user installer for Drama Llama, written as a layered Python
package at the repo root (`installer/`, not under `src/`). One code path is
shared by the graphical wizard, the command line and CI.

## Layers

| Layer | Location | Rule |
|---|---|---|
| Core | `installer/core/` | stdlib only, no Qt, no torch — importable and testable anywhere |
| Wizard | `installer/gui/` | PySide6; imported lazily by `entry.py` |
| Entry | `installer/entry.py` | picks GUI vs CLI |
| Packaging | `installer/packaging/` | PyInstaller spec, Inno script, AppImage script |
| Build driver | `scripts/build_installer.py` | web build → payload → native artifact |

`installer/core/__init__.py` intentionally stays empty of logic; the version
lives in `installer/__init__.py` as `INSTALLER_VERSION` (imported as
`from .. import INSTALLER_VERSION`). Getting that import level wrong is the
usual cause of `ImportError: cannot import name 'INSTALLER_VERSION'` when the
package is treated as a namespace.

### Core modules

- `paths.py` — `InstallPaths`, default prefixes, legacy engine paths, Whisper
  cache, `pins_path()`, payload resolution.
- `detect.py` — OS/CPU/RAM/disk, NVIDIA GPU + driver, Vulkan, Docker, WSL2,
  admin rights; `cuda_channel()` maps driver → wheel channel.
- `plan.py` — component catalogue, `Selection`, `build_steps()`, size
  estimates, licence/device validation.
- `installer.py` — orchestrates steps, records state after each, tolerates
  optional-component failure, writes the log.
- `python_stack.py`, `runtimes.py` — uv + CPython + Node, virtualenv, wheel
  installs (torch from the pinned channel).
- `whisper.py` — pinned faster-whisper large-v3 snapshot.
- `engines.py` — Breeze repo/EN/DE, Docker detection/elevation, Chatterbox
  image pull, container creation and health check.
- `app_payload.py` — stages/copies the app tree and writes `NOTICE.md`.
- `launchers.py` — Linux `.desktop` + `bin/` wrappers, Windows `.cmd`, env.
- `verify.py` — file checks plus an import smoke test and a server boot probe.
- `uninstall.py` — removal with `keep_data` / `purge_models`.
- `download.py`, `util.py`, `state.py`, `progress.py`, `cli.py`.

## Install layout

Per-user, no admin by default:

| | Linux | Windows |
|---|---|---|
| Prefix | `~/.local/share/drama-llama` | `%LOCALAPPDATA%\DramaLlama` |
| App | `<prefix>/app` | `<prefix>\app` |
| Projects | `<prefix>/data` | `<prefix>\data` |
| Runtimes | `<prefix>/runtime` (uv, Node, CPython) | same |
| Virtualenv | `<prefix>/venv` | `<prefix>\venv` |
| Launchers | `<prefix>/bin` + `~/.local/share/applications` | `<prefix>\drama-llama.cmd` + Start Menu |
| State | `<prefix>/install-state.json` | same |

Engine assets intentionally stay at the legacy paths the vendored code
hardcodes: `~/code/breeze-tts`, `~/models/Breeze-TTS-2`,
`~/code/breeze-tts-cpp/breeze-tts-2-v0.1.6-<platform>/breeze-server`,
`~/models/Articulatio-DE-Stream-TTS-v1.5-Q8_0.gguf`, and the Whisper snapshot
under `~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3`.
See [[concepts/gpu-optional-install]] for device selection and
[[entities/chatterbox-engine]] for the container lifecycle.

## Wizard

`installer/gui/app.py` is one `QWizard` with pages:

1. Welcome — install / repair / uninstall (the latter two appear only when an
   existing `install-state.json` is found).
2. Detect — runs `detect_system()` on a worker thread; device radio buttons
   (Auto / Force GPU / CPU only).
3. Components — core plus opt-in Breeze EN, Breeze DE, Chatterbox, live size
   estimate and the Docker-elevation checkbox.
4. Location — prefix and projects folder, disk-space guard.
5. Licence — Breeze research/non-commercial acceptance, skipped when no
   Breeze component is selected.
6. Progress — weighted per-step bar driven by `Step.weight`, capped log view.
7. Finish — launch/app folder, warnings, log path.
8. Uninstall — keep projects (default) or purge models.

`worker.py` owns the `QThread` workers. `InstallWorker.request_cancel()` is
called directly rather than through a queued slot: the worker has no event
loop while `run_install()` blocks.

## Entry point and exit codes

`installer/entry.py` decides GUI vs CLI:

- Console flags (`--headless`, `--dry-run`, `--verify`, `--state`,
  `--uninstall`, `--json`, `--repair`, `--help`, `--version`, …) always take
  the CLI path.
- `DRAMA_LLAMA_INSTALLER_HEADLESS=1` forces CLI.
- No `DISPLAY`/`WAYLAND_DISPLAY` on Linux, or a missing PySide6, means the
  wizard cannot start.
- A **GUI launch failure returns 2** and never falls through to a headless
  install — otherwise multi-GB downloads would start without consent.

CLI exit codes: `0` success, `1` install/verify failure, `2` invalid
invocation, `130` cancelled.

## Packaging

- `installer/packaging/pyinstaller.spec` builds a windowed `DramaLlama-Setup`
  onedir binary. The staged payload (`src/`, `web/dist`, `vendor/`, `installer/`,
  Suno bridge, `third_party/bettersuno-mcp`) is embedded as data and copied
  into the prefix at install time. `pins_path()` also checks
  `payload_root()/installer/pins.json` for the frozen case.
- `installer/packaging/inno/DramaLlama.iss` is a thin per-user wrapper around
  that binary: Start Menu entry, *Apps & features* (ARP) registration, and an
  uninstall hook that runs
  `DramaLlama-Setup.exe --headless --uninstall --yes --keep-data`.
  `PrivilegesRequired=lowest`; a custom prefix is found through
  `<default>/location.txt`.
- `installer/packaging/appimage/build-appimage.sh` assembles an AppDir and
  calls `appimagetool`; without it, it writes an `AppDir.tar.gz` fallback.
  `APPIMAGE_EXTRACT_AND_RUN=1` avoids a FUSE requirement in CI.
- `scripts/build_installer.py` is the only supported driver. It rebuilds
  `web/dist` when stale, stages the payload, runs PyInstaller, then Inno or
  the AppImage script, and writes `.sha256` files. PyInstaller cannot
  cross-compile, so targets are built on their own OS.

`.github/workflows/installer.yml` runs the installer tests and lints on every
PR touching the installer, builds both targets on their native runners, and
attaches artifacts plus checksums to a release on a `v*` tag.

## Tests

`tests/test_installer.py` and `tests/test_windows_paths.py` cover the core
without PySide6 or torch: path/layout resolution, archive traversal rejection,
download hashing/retry, plan validation and estimates, state round-trips,
launcher generation, uninstall semantics, Docker refusal, and simulated
Windows paths. `InstallPaths` accepts `system=` so Windows layouts are
exercised on Linux.

## Related

- [[concepts/gpu-optional-install]]
- [[entities/chatterbox-engine]]
- [[entities/breeze-engine]]
- [[entities/drama-llama-app]]
