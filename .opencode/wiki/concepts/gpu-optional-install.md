---
title: GPU-optional installation
tags: [installer, gpu, cuda, cpu, components, licences]
sourceRefs:
  - installer/core/detect.py
  - installer/core/plan.py
  - installer/core/engines.py
  - installer/pins.json
  - INSTALL.md
lastReviewed: 2026-11-01
---

# GPU-optional installation

Drama Llama installs and runs on a CPU-only machine. The GPU changes which
PyTorch wheels and which optional components make sense — it never gates the
core app.

## Device resolution

- **Auto** (default): GPU when an NVIDIA GPU with a usable driver is visible,
  CPU otherwise.
- **CPU only** (`--cpu`): always wins; sets `CUDA_VISIBLE_DEVICES=""` in the
  launcher, so a later GPU is not picked up accidentally.
- **Force GPU** (`--gpu`): fails with a clear error when no NVIDIA GPU is
  visible, rather than silently installing CPU wheels.

Torch channels come from `installer/pins.json` and are chosen by the driver's
major version (`cuda_channel()` in `detect.py`):

| Driver | Channel |
|---|---|
| 580+ | `cu132` |
| older / no GPU | `cpu` (auto mode warns when a GPU is present but the driver is too old) |

Upstream publishes no `cu128` builds for the pinned torch, so the `cu128`
channel was removed: pre-580 drivers fall back to CPU with a warning telling
the user to update the driver and re-run for GPU acceleration.

`--gpu` with a driver too old for every pinned channel raises instead of
downloading a CPU build. Wheel URLs are pinned, so an install is reproducible
months later.

## Component behaviour per device

| Component | GPU | CPU |
|---|---|---|
| Core (app, Whisper large-v3, ffmpeg, Suno bridge) | CUDA wheels; Whisper still aligns on CPU int8 | CPU wheels; everything works, slower |
| Breeze English | Practical (~3.7× real time on a 4090 laptop) | Works but minutes per line; the wizard warns |
| Breeze German | Vulkan sidecar | Automatic CPU fallback when no Vulkan loader |
| Chatterbox | NVIDIA Docker image (`DEVICE=cuda`) | CPU Docker image (`DEVICE=cpu`) |
| Fake engine / tests | always | always |

The GPU device is not required for alignment: `align_cuts` runs Whisper on CPU
int8 regardless, which avoids competing with Breeze for VRAM (see
[[concepts/gpu-serialization]]).

## Size envelope

Estimates (`download_estimate` / `installed_estimate` in `plan.py`) for Linux;
Windows is within ~0.02 GiB:

| Selection | CPU download | CPU disk | GPU download | GPU disk |
|---|---:|---:|---:|---:|
| Core | 4.2 | 6.2 | 7.5 | 9.5 |
| Core + Breeze EN | 11.3 | 13.3 | 14.7 | 16.7 |
| Core + Breeze DE | 7.6 | 9.6 | 10.9 | 12.9 |
| Core + Chatterbox | 5.4 | 7.4 | 15.2 | 17.2 |
| Everything | 15.9 | 17.9 | 25.8 | 27.7 |

The wizard and `--dry-run` both show the plan before anything is downloaded.
The location page refuses to continue on a full disk unless the user overrides
the warning.

## Optional components and failure isolation

`build_steps()` marks Breeze EN, Breeze DE and Chatterbox as **optional**. If
one fails (missing Docker daemon, licence not accepted, download error), the
core install still completes and the component is recorded as failed in
`install-state.json`. Re-running the wizard resumes at the failing step
because completed steps are checked on disk.

Licence rules:

- Breeze EN weights and the Breeze DE GGUF are research/non-commercial.
  Selection requires `--accept-breeze-license` (or the wizard checkbox).
- Chatterbox and its Docker image carry upstream licences.
- The bundled ffmpeg from `imageio-ffmpeg` is fetched by pip, not
  redistributed.

## Docker is the only elevated dependency

Chatterbox needs a Docker daemon. If it is missing and the user consents
(`--allow-docker-install` or the wizard checkbox), the installer runs the
distribution's package manager with elevation, enables the daemon, and adds
the user to the `docker` group. That normally requires a reboot/re-login; the
step is then recorded as `pending_reboot` and a re-run resumes it. Nothing
else in the installer needs administrator rights.

## Related

- [[entities/installer]]
- [[entities/chatterbox-engine]]
- [[entities/breeze-engine]]
- [[concepts/gpu-serialization]]
