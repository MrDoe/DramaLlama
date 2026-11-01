# Installing Drama Llama

Drama Llama ships as a native, per-user installer. Nothing is installed
system-wide unless you explicitly ask the wizard to install Docker (for
Chatterbox) or the Vulkan loader (for Breeze German). Administrator rights are
never required for the app itself.

The installer is a small PySide6 wizard with a headless CLI underneath. Both
use the same installer core, so a silent CI install and a click-through install
produce identical layouts.

| Platform | Download | What it is |
|---|---|---|
| Windows 10/11 x64 | `DramaLlama-Setup-<version>.exe` | Inno Setup wrapper around the PyInstaller wizard; registers Start Menu and *Apps & features* entries |
| Linux x86_64 | `DramaLlama-<version>-x86_64.AppImage` | AppImage wizard; no root, no FUSE needed if you use `--appimage-extract-and-run` |
| Linux fallback | `DramaLlama-<version>-linux-x86_64.tar.gz` or `...AppDir.tar.gz` | Plain directory build if AppImage is unavailable |

Every release asset has a matching `.sha256` file.

## Before you start

- **Disk space:** at least 8 GiB free for a core CPU install, 16 GiB for a core
  GPU install, and up to 30 GiB if you select every engine.
- **GPU (optional):** an NVIDIA card with a current driver. The installer picks
  a CUDA build when a driver is visible and a CPU build otherwise.
- **Docker (optional):** only for Chatterbox. The wizard can install Docker for
  you with an administrator prompt, but a reboot or re-login is usually needed
  afterwards before Chatterbox can be added.
- **Network:** the installer downloads Python wheels, Whisper large-v3, and any
  engine you select. All downloads are resumable and checksum-verified.

## Unsigned builds

Releases are not code-signed yet. Windows SmartScreen will show *Windows
protected your PC*; choose **More info → Run anyway** only after verifying the
checksum. On Linux, make the AppImage executable and verify the checksum:

```bash
chmod +x DramaLlama-0.1.0-x86_64.AppImage
sha256sum -c DramaLlama-0.1.0-x86_64.AppImage.sha256
```

On Windows PowerShell:

```powershell
Get-FileHash .\DramaLlama-Setup-0.1.0.exe -Algorithm SHA256
# compare with the .sha256 file
```

If your desktop cannot run AppImages (missing FUSE), use:

```bash
./DramaLlama-0.1.0-x86_64.AppImage --appimage-extract-and-run
```

or install `libfuse2` / `fuse2` from your distribution.

## Running the wizard

Double-click the installer, or start it from a terminal. The wizard:

1. Detects the OS, CPU/RAM, free disk, NVIDIA GPU + driver, Vulkan loader,
   Docker daemon, ffmpeg and any existing install.
2. Asks which *optional* engines to add: Breeze English, Breeze German,
   Chatterbox. The core app is always installed.
3. Asks where to install and where to keep projects.
4. Shows the Breeze research/non-commercial licence when a Breeze component
   is selected.
5. Downloads, installs, verifies, and offers to launch the app.

If an existing install is found, the welcome page also offers **Repair** and
**Uninstall**. Repair re-checks every file and reuses completed steps, so it is
the right choice after an interrupted download or a moved model directory.

### Default locations

| | Linux | Windows |
|---|---|---|
| App | `~/.local/share/drama-llama/app` | `%LOCALAPPDATA%\DramaLlama\app` |
| Projects | `~/.local/share/drama-llama/data` | `%LOCALAPPDATA%\DramaLlama\data` |
| Runtimes | `<prefix>/runtime` | `<prefix>\runtime` |
| Virtualenv | `<prefix>/venv` | `<prefix>\venv` |
| Launchers | `<prefix>/bin` | `<prefix>\drama-llama.cmd` + Start Menu |
| Logs | `<prefix>/logs` | `<prefix>\logs` |
| State | `<prefix>/install-state.json` | `<prefix>\install-state.json` |

The projects directory is independent of the app directory. Uninstalling keeps
it by default.

### Shared engine locations

Drama Llama's vendored Breeze modules and the app's engine adapters expect
these paths, so the installer writes models and the Breeze checkout there
rather than inside the prefix:

| Component | Path |
|---|---|
| Breeze repo code | `~/code/breeze-tts` |
| Breeze English weights | `~/models/Breeze-TTS-2` |
| Breeze German sidecar | `~/code/breeze-tts-cpp/breeze-tts-2-v0.1.6-<platform>/breeze-server` |
| Breeze German GGUF | `~/models/Articulatio-DE-Stream-TTS-v1.5-Q8_0.gguf` |
| Whisper large-v3 (CTranslate2) | `~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/snapshots/<rev>` |

The Whisper cache path is deliberately not derived from `HF_HOME`: the
vendored alignment code globs this exact directory. Do not move it.

If you already have a Breeze checkout at `~/code/breeze-tts` with the expected
files, the installer reuses it instead of downloading the pinned commit.

## Components and disk space

Download sizes and on-disk sizes are approximate (GiB). "Core" includes the
Python/Node runtimes, PyTorch, Whisper large-v3, ffmpeg, the Breeze repo code,
the app payload and the Suno bridge.

| Selection | CPU download | CPU on disk | GPU download | GPU on disk |
|---|---:|---:|---:|---:|
| Core | 4.2 | 6.2 | 7.5 | 9.5 |
| Core + Breeze English | 11.3 | 13.3 | 14.7 | 16.7 |
| Core + Breeze German | 7.6 | 9.6 | 10.9 | 12.9 |
| Core + Chatterbox | 5.4 | 7.4 | 15.2 | 17.2 |
| Everything | 15.9 | 17.9 | 25.8 | 27.7 |

- **Breeze English** is English (and Chinese) only. It needs CUDA for practical
  throughput; CPU works but is very slow (minutes per line).
- **Breeze German** uses the Breeze-TTS-2.cpp sidecar and the Articulatio-DE
  Q8_0 GGUF. It prefers Vulkan and falls back to CPU automatically.
- **Chatterbox** runs as a Docker container, not in the Python virtualenv. It
  clones an uploaded voice sample and has no voice-design mode.

### GPU mode

The installer maps the NVIDIA driver to a pinned PyTorch wheel channel:

| Driver | Wheel channel |
|---|---|
| 580+ | cu132 |
| 525–579 | cu128 |
| older / no GPU | CPU wheels |

The wizard offers *Automatic*, *Force GPU* and *CPU only*. `--gpu` refuses to
continue when no NVIDIA GPU is visible; `--cpu` always wins. A CPU install can
render with Breeze (slowly), align with Whisper on CPU, and run Chatterbox with
its CPU Docker image.

### Docker and Chatterbox

Chatterbox needs a running Docker daemon. If Docker is missing and you allow
it, the wizard runs the platform installer with elevation:

- Linux: `pkexec`/`sudo` plus the distribution package manager, then
  `systemctl enable --now docker` and adds you to the `docker` group.
- Windows: `winget install Docker.DockerDesktop`, enabling WSL2 first when
  needed.

After that the wizard usually needs a reboot or re-login before it can create
the container. Re-run the wizard afterwards; it resumes at the Chatterbox step.
Existing images and the model volume are reused.

Pinned images (`installer/pins.json`):

| Variant | Image |
|---|---|
| GPU | `travisvn/chatterbox-tts-api@sha256:8c0b3791…` |
| CPU | `travisvn/chatterbox-tts-api@sha256:900e5ae1…` |

The installer starts the container once to trigger the first model download,
waits for `/health` to report `model_loaded`, then stops it. Drama Llama starts
and stops the container around each render.

## Licences

- The app and most dependencies are open source.
- **Breeze TTS 2 weights** are research/non-commercial. The wizard requires an
  explicit acceptance before downloading them, and the licence page is skipped
  only when no Breeze component is selected.
- **Breeze-TTS-2.cpp** and the **Articulatio-DE GGUF** are research releases;
  check upstream terms before commercial use.
- **Chatterbox** and its Docker image carry their own licences.
- The bundled ffmpeg comes from `imageio-ffmpeg`: the static binary is GPL,
  fetched by pip at install time and not redistributed by the installer.

A consolidated `NOTICE.md` is written to the install prefix.

## First run

After the wizard finishes:

- **Linux:** launch *Drama Llama* from the application menu, run
  `~/.local/share/drama-llama/bin/drama-llama ui`, or use the
  `~/.local/bin/drama-llama` symlink.
- **Windows:** use the Start Menu entry or `<prefix>\drama-llama.cmd`.

The app opens on `http://127.0.0.1:8770`. Projects are written to
`<prefix>/data/projects`. The Suno bridge starts with the app unless you pass
`--no-suno-bridge`; Suno still needs the BetterSuno browser extension and a
logged-in `suno.com` tab.

The local LLM is not installed by the wizard. Deterministic parsing works out
of the box; configure an Ollama-compatible endpoint in Settings if you want the
LLM-assisted parser.

## Uninstall

From the app-wizard welcome page, or:

```bash
# Linux
~/.local/share/drama-llama/bin/drama-llama --help   # lists app commands
./DramaLlama-0.1.0-x86_64.AppImage --headless --uninstall --keep-data

# Windows
DramaLlama-Setup.exe --headless --uninstall --keep-data
```

The Windows *Apps & features* entry runs the same command and keeps projects.
Add `--no-keep-data` to delete projects too, and `--purge` to remove downloaded
models, the Breeze checkouts and the Chatterbox image/volume. With `--purge`, only
files the installer downloaded are deleted; a Breeze checkout, checkpoint or
Whisper cache that already existed is left alone, because the app and sibling
checkouts may share it.

## Headless CLI

All wizard features are available without Qt. This is what CI and the Windows
uninstaller use.

```bash
DramaLlama-Setup --headless --dry-run                 # resolve the plan, no changes
DramaLlama-Setup --headless --cpu                     # core only, CPU wheels
DramaLlama-Setup --headless --gpu \
  --components core,breeze-en,breeze-de \
  --accept-breeze-license
DramaLlama-Setup --headless --components all --yes
DramaLlama-Setup --headless --verify                  # re-run checks on an install
DramaLlama-Setup --headless --state                   # print install-state.json
DramaLlama-Setup --headless --uninstall --keep-data
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--prefix PATH` | install root (default: per-user data dir) |
| `--data-dir PATH` | projects directory (default: `<prefix>/data`) |
| `--device auto\|gpu\|cpu` | device mode (`--cpu` / `--gpu` shortcuts) |
| `--components LIST` | `core`, `breeze-en`, `breeze-de`, `chatterbox`, `all` |
| `--accept-breeze-license` | required for any Breeze component |
| `--allow-docker-install`, `--yes` | allow elevated Docker installation |
| `--json` | machine-readable result |
| `--repair` | re-check and repair an existing install |

Exit codes: `0` success, `1` install/verify failure, `2` invalid invocation,
`130` cancelled.

Install state is written after every step, so an interrupted install resumes
instead of restarting. `--repair` is the explicit way to do that from the GUI.

## Building the installers

Build a target on its own OS; PyInstaller cannot cross-compile.

```bash
python -m pip install pyinstaller PySide6
cd web && npm ci && npm run build && cd ..

# Stage the app payload only (also used by tests)
python scripts/build_installer.py --target payload

# Linux: AppImage + tarball + checksums
python scripts/build_installer.py --target linux

# Windows: PyInstaller onedir + Inno Setup (needs Inno Setup 6 / ISCC.exe)
python scripts/build_installer.py --target windows
```

Artifacts land in `dist/`. The script rebuilds `web/dist` when sources are
newer; pass `--skip-web` to reuse the existing build.

Linux needs `appimagetool` on `PATH` or in `APPIMAGETOOL`. Without it the script
still produces an `AppDir.tar.gz` fallback. `APPIMAGE_EXTRACT_AND_RUN=1` is set
automatically so containers do not need FUSE.

The release workflow (`.github/workflows/installer.yml`) runs the tests and
builds both platforms, uploading the artifacts with their `.sha256` files.

Packaging files:

| File | Purpose |
|---|---|
| `installer/packaging/pyinstaller.spec` | Bundles the wizard + staged payload |
| `installer/packaging/inno/DramaLlama.iss` | Per-user Inno installer, Start Menu and ARP entry |
| `installer/packaging/appimage/build-appimage.sh` | AppDir assembly and AppImage build |
| `installer/pins.json` | Versions, URLs, hashes, HF revisions, Docker digests, size estimates |
| `scripts/build_installer.py` | Orchestrates web build → payload → native artifact |

## Troubleshooting

**The installer stops on a checksum.** Delete the partial file under
`<prefix>/cache/downloads` and run it again; downloads resume, but a corrupted
file is discarded.

**Breeze is missing from the engine list.** Check `~/code/breeze-tts` and
`~/models/Breeze-TTS-2`, then run the installer with `--verify`. The engine
reports unavailable when the vendored modules cannot import.

**Chatterbox needs Docker after a reboot.** Re-run the wizard; the Docker step
detects the daemon and continues. If the daemon is not running, start Docker
Desktop or `sudo systemctl start docker`.

**GPU is not used.** Check `nvidia-smi`, the driver channel table above, and
run `--verify`; the import smoke test prints the torch version, CUDA version and
`cuda_available`.

**Port conflicts.** Defaults are app `8770`, Chatterbox `4123`, Breeze German
`8137`, Suno bridge `9430`/websocket `9423`. Set `DRAMA_LLAMA_PORT`,
`CHATTERBOX_URL`, `BREEZE_DE_URL`, or `SUNO_BRIDGE_PORT` before launching.

**AppImage will not start.** Run with `--appimage-extract-and-run` or install
FUSE. Check `~/.local/share/drama-llama/logs` for the app's own logs.
