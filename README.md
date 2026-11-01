# Drama Llama

[![Drama Llama mascot](web/public/llogo.png)](web/public/llogo.png)

Drama Llama is a local-first web app that turns a pasted script into a finished audio drama. You paste in your script, and the app splits it into scenes and dialogue lines, voices each character with a text-to-speech engine of your choice, and aligns the generated speech to the text. You then fine-tune the timing on a multitrack timeline and let the app mix dialogue, sound effects and music into a loudness-normalised WAV or MP3 master. Apart from the optional hosted LLM and Suno music generation, everything runs on your own machine.

## How it works

When you paste a script, Drama Llama parses it into scenes and dialogue lines. The parser is deterministic by default, but a local LLM can optionally normalise scripts with messy formatting first. The lines are then grouped by scene and speaker into stem scripts, and each stem is rendered with the project's engine. Breeze or the Breeze-DE sidecar produce designed-clone voices, Chatterbox clones voices from samples, Suno handles sung dialogue, and a Fake engine exists for testing. Every render produces one or more takes per character.

Once a stem has been rendered, Whisper aligns each take on the CPU and cuts it into per-line clips. You refine these clips on the shared Cut board, where you can trim windows, slip audio inside a region and choose which take each clip uses. Sound effects, one-shot sounds and music cues are anchored to individual lines, and music can be generated live with Suno through the BetterSuno browser extension. Finally, the app assembles everything, ducks the beds under speech, applies EQ and normalises the loudness to produce the master.

## A tour of the interface

The **Projects** page lists every drama on the machine and shows the engine stack at the top, running from the LLM through the voices and alignment to the music.

[![Projects](docs/screenshots/01-projects.png)](docs/screenshots/01-projects.png)

On the **Script** page you paste your script, choose whether to parse it deterministically or with the LLM, and inspect the detected cast.

[![Script](docs/screenshots/02-script.png)](docs/screenshots/02-script.png)

The **Stems & Render** page renders per-scene, per-character stems with the project's default engine. Takes accumulate over time, so you can compare them against each other.

[![Render](docs/screenshots/03-render.png)](docs/screenshots/03-render.png)

The **Cut** page is a shared multitrack timeline that shows every line at its script position. You choose the take for each clip, drag edges to trim, and a single Save-all rebuilds the mix once.

[![Cut board](docs/screenshots/04-cutboard.png)](docs/screenshots/04-cutboard.png)

The **Mix** page assembles the dialogue with ducked sound effects and offers the loudness-normalised master as a WAV or MP3 download.

[![Mix](docs/screenshots/05-mix.png)](docs/screenshots/05-mix.png)

These screenshots were taken live against the running app using the `fake` engine demo project, so no GPU was needed.

## Current status

The English path works end to end. You can paste Markdown, screenplay or plain text, and the deterministic parser detects cast tables, speaker aliases and stage directions while covering 100% of the dialogue. If you enable LLM parsing, the model rewrites any script style, such as `[NAME] "text"`, `Name: "text"` or `NAME (direction) text`, into the canonical `NAME (direction): text` format, which the deterministic parser then reads. The result is validated strictly against the original wording, and the app falls back to the deterministic parser if validation fails.

Rendering follows the pipeline described above: a Breeze reference voice is designed, the scene stem is rendered, Whisper aligns it on the CPU, and line clips are cut. On one real scene this took 10.1 seconds to load and 103.5 seconds to render 28.0 seconds of audio. Alignment took another 60.8 seconds, all six lines were aligned at confidence 1.0, and peak VRAM was 8.47 GB. The sound library indexes `~/Sounds` plus any uploads, and a cue sheet anchors effects to lines. The mixer assembles dialogue, ducks beds, applies a telephone filter and normalises to either −16 or −23 LUFS. A demo mix of 27 lines, 94.3 seconds long and with one cue, completed without warnings.

The German path uses Chatterbox Multilingual 0.5B through its local HTTP server, with per-character voice samples and exaggeration and cfg settings per take. It has been verified end to end with a 20.7-second stem, seven of seven line cuts and a 46.8-second test mix.

Music generation works through the `bettersuno-mcp` modules. In the Sound stage you can generate a theme, bed or song, preview the returned track, add it as a music cue and mix it. This has been validated with a real BetterSuno extension connection and a WAV download.

Native installers wrap all of this in a PySide6 wizard over a stdlib-only installer core that is shared with a headless CLI. The same core produces an AppImage on Linux and an Inno Setup installer on Windows, with a per-user layout, GPU auto-detection with `--cpu`/`--gpu` overrides, and opt-in Breeze English, Breeze German and Chatterbox using pinned wheels, models and Docker image digests. See [`INSTALL.md`](INSTALL.md).

Engines are chosen per project, so there is no per-stem switching, and every cut records the take it was measured on. Suno re-renders append new generations instead of replacing earlier ones, and manual trims survive re-renders. The Cut board edits all stems on one shared timeline with per-clip take choice and atomic batch saves, and it rebuilds the mix only once. A queued realignment never overwrites newer manual trims.

## Installation

### Install a release

Drama Llama ships a native, per-user installer that needs no administrator
rights and no Python, Node or ffmpeg on the machine. Download the asset for
your platform from the releases page, verify its `.sha256`, and run it:

| Platform | Asset |
|---|---|
| Windows 10/11 x64 | `DramaLlama-Setup-<version>.exe` |
| Linux x86_64 | `DramaLlama-<version>-x86_64.AppImage` (plus a `.tar.gz` fallback) |

The wizard detects the hardware, lets you pick optional engines (Breeze
English, Breeze German, Chatterbox), and downloads only what you select. A
core CPU install takes about 4.2 GiB of downloads and needs no GPU. Breeze
English is opt-in because it is English-only and impractically slow on CPU.
The local LLM is not installed; deterministic parsing works out of the box.

Releases are unsigned for now: Windows SmartScreen asks for *More info → Run
anyway*, and Linux may need `--appimage-extract-and-run` without FUSE. If the
AppImage fails to start with a missing `libxcb-cursor0`, install it with
`sudo apt install libxcb-cursor0`. See [`INSTALL.md`](INSTALL.md) for disk-space
tables, licence terms, the headless CLI, uninstall, and how to build the
installers.
### Run from a source checkout

A clean setup needs Python 3.12 or newer, an `ffmpeg` binary on your `PATH` and Node.js 20 or newer. The mixer calls `ffmpeg` for loudness normalisation and MP3 encoding, and uploads are converted with it. The web frontend is built with Node.js. A local NVIDIA GPU is optional but recommended. Without one, Breeze rendering and Whisper alignment fall back to slow CPU execution, while the Fake engine and the full test suite run anywhere.

Start by cloning the repository and pulling in the BetterSuno submodule, which the music sidecar depends on:

```
git clone git@github.com:MrDoe/DramaLlama.git
cd DramaLlama
git submodule update --init
```

Next, create a fresh virtual environment and install the package into it. The base install declares only the light web dependencies (FastAPI, uvicorn and pydantic), so it is enough for the UI, the job queue and the test suite:

```
python3.12 -m venv ~/.venvs/drama-llama
~/.venvs/drama-llama/bin/pip install -U pip
~/.venvs/drama-llama/bin/pip install -e . --no-deps
~/.venvs/drama-llama/bin/pip install -e ".[dev]"   # tests + lint
```

Rendering and alignment need the audio stack on top of that. Install it into the same environment with `pip install numpy soundfile faster-whisper demucs`, together with a matching `torch` build: the CUDA build from pytorch.org on GPU machines and the default CPU build otherwise. Demucs is invoked as `python -m demucs`, so it must resolve inside the virtual environment. Everything else is imported directly. If a model is missing, the app degrades gracefully and render jobs fail with a plain-language error instead of crashing the worker.

Build the frontend once with npm. Afterwards, `./start-ui.sh` rebuilds it only when the sources are newer than `web/dist`:

```
cd web && npm install && npm run build && cd ..
```

Then start the app. The `serve` command runs the FastAPI backend together with an in-process worker on `http://127.0.0.1:8770`:

```
~/.venvs/drama-llama/bin/drama-llama serve
```

The legacy `drama-studio` command is still installed as an alias. If you prefer to debug model loading separately, pass `--no-worker` and run `~/.venvs/drama-llama/bin/drama-llama worker --once` in a second terminal. You can inspect the queue with `~/.venvs/drama-llama/bin/drama-llama jobs`.

The `./start-ui.sh` launcher wraps all of this. It builds the frontend when it is stale and then serves the app. Pass `--dev` for Vite hot reload on port 5173, `--open` to launch the browser once `/api/health` passes, and `--build` or `--no-build` to control the rebuild.

### Suno music bridge

Suno generation needs the BetterSuno browser extension and an open `suno.com` tab. The submodule was already fetched during installation, but its own dependencies must be installed once. Its postinstall script is local-only, so a plain install skips it upstream, and you should run `npm ci --ignore-scripts` inside `third_party/bettersuno-mcp`. Then start the HTTP sidecar in another terminal with `~/.venvs/drama-llama/bin/drama-llama suno-bridge`. The `serve` command starts the bridge automatically unless you pass `--no-suno-bridge`.

The bridge listens on `http://127.0.0.1:9430` and owns the extension WebSocket on `ws://127.0.0.1:9423`. Only one process can own port 9423 at a time, so stop any other `bettersuno-mcp` instance first. The Sound stage shows the connection status and the Suno generation form. Downloads use the WAV conversion flow, so the mixer always receives a decodable file.

## Workflow

### 1. Script

Paste your script and choose English or German. `Save & parse` parses deterministically, while `LLM parse` first has the model normalise messy attribution into the canonical format. If the model is offline, the app falls back safely to the deterministic parser.

### 2. Stems & Render

Group the lines by scene and speaker, then render them with the project's default engine, which you pick in the project header. The `auto` setting means Breeze for English and Chatterbox for German. Breeze works from a designed reference voice of 6鈥�11 seconds, while Chatterbox uses an uploaded voice sample or its default voice. The worker renders the stem, aligns it with Whisper on the CPU, cuts the line clips and stores a confidence value for each. Takes accumulate and stay labelled by engine so you can compare them, and switching a cut's take scales its window by the ratio of the two durations.

### 3. Cut

Trim every line on one shared timeline that follows script order and mixer spacing. Drag the edges of a region to trim it, drag its body to slip the audio inside it, and switch a clip's take from the Track dropdown. When you press Save-all, all stems are validated atomically and the mix is rebuilt once. A `mix stale` badge appears whenever cuts change after the last mix.

### 4. Sound

Search `~/Sounds` (plus `data/sfx` and your uploads), preview the results and attach beds, one-shots or music to specific lines. Alternatively, generate music with Suno and add the returned track as a cue.

### 5. Mix

The mixer assembles the line clips, ducks the beds under speech, filters telephone lines and normalises the loudness to either podcast level (−16 LUFS) or broadcast level (−23 LUFS). You can then download the result as WAV or MP3.

## Engines

For English, Drama Llama uses Breeze TTS 2. You design a 6鈥�11 second reference voice for each character, and the engine clones it for every scene stem. For German there is an optional Breeze-TTS-2.cpp sidecar that runs a community finetune called Articulatio-DE and supports both voice design and cloning. Its weights and outputs are for research and non-commercial use only.

Chatterbox Multilingual 0.5B covers both English and German. It clones a voice from an uploaded sample and falls back to its default voice when no sample is provided. Drama Llama expects the Chatterbox model server at `http://127.0.0.1:4123`. You can start it independently, and it keeps the model warm between requests. Drama Llama only sends text and an optional voice sample over HTTP. Music comes from Suno through BetterSuno, where a prompt produces a track that you add as a music cue.

### German Breeze sidecar (optional)

The German finetune runs on the `Breeze-TTS-2.cpp` engine. Download the prebuilt binary and the Q8_0 GGUF model, then start the sidecar:

```
~/.venvs/qwen3tts/bin/drama-llama breeze-de
# http://127.0.0.1:8137  (override with --port / BREEZE_DE_URL)
```

The sidecar owns the GPU, and Drama Llama serialises GPU jobs, so stop the sidecar (or the LLM) before loading another model. Breeze weights and outputs are for research and non-commercial use only. The vendored stem renderer and aligner are Apache 2.0 code, and `vendor/breeze_stems/VENDORED_FROM.md` documents where they come from.

## Project layout

The backend lives in `src/drama_studio/`, which contains the FastAPI app, the job queue, the worker, the parser, the engines and the mixer. The engine adapters for Breeze, Breeze-DE, Chatterbox, Suno and Fake are in its `engines/` subfolder, and the shared take-aware cut logic (freezing, remapping and validating cuts) is in `cuts.py`. The pinned Breeze stem renderer and Whisper aligner are vendored under `vendor/breeze_stems/`.

The Suno bridge in `suno-bridge/` is a small Node HTTP sidecar built on the installed `bettersuno-mcp` modules. The frontend in `web/` is written in React, Vite and TypeScript, and the Drama Llama mascot sits in `web/public/`. The tour screenshots are in `docs/screenshots/`. At runtime, each project's JSON, references, takes, line clips and mix are stored in `data/projects/<id>/`, and the job queue is an SQLite database in WAL mode at `data/jobs.sqlite3`. The `installer/` folder at the repository root holds the PySide6 wizard and the stdlib-only installer core, with its PyInstaller spec, Inno Setup script and AppImage builder under `installer/packaging/`; `scripts/build_installer.py` drives the build. The `tests/` folder holds 236 tests, none of which require a GPU.

The Python package keeps its `drama_studio` import name. Only the product, the CLI and the UI were renamed.

## Configuration

Drama Llama is configured through environment variables. The old `DRAMA_STUDIO_*` names still work as fallbacks.

| Variable                    | Default                                                |
|-----------------------------|--------------------------------------------------------|
| `DRAMA_LLAMA_DATA`          | `<repo>/data`                                          |
| `DRAMA_LLAMA_HOST` / `PORT` | `127.0.0.1` / `8770`                                   |
| `DRAMA_LLAMA_LLM`           | `gemma4:12b-16k`                                       |
| `DRAMA_LLAMA_LLM_API`       | `ollama` 鈥� or `openai` for OpenAI-compatible endpoints |
| `DRAMA_LLAMA_LLM_URL`       | `http://127.0.0.1:11434` (falls back to `OLLAMA_URL`)  |
| `DRAMA_LLAMA_LLM_API_KEY`   | *(empty)* 鈥� bearer token for hosted/cloud endpoints    |
| `CHATTERBOX_URL`            | `http://127.0.0.1:4123`                                |
| `BREEZE_DE_URL`             | `http://127.0.0.1:8080`                                |
| `SUNO_BRIDGE_URL`           | `http://127.0.0.1:9430`                                |
| `DRAMA_LLAMA_SFX_ROOTS`     | `~/Sounds:<repo>/data/sfx`                             |

By default, the script-parsing LLM is a local Ollama instance. You can change this on the **Settings** page in the top bar, where you select the protocol (Ollama or OpenAI-compatible), the endpoint URL, the model and an optional API key for hosted endpoints. The model picker is filled from the endpoint's own model list, and a Test connection button lets you check the setup before saving. Your choices are stored in `data/settings.json`, which is gitignored. The API never returns the key itself. Changes apply immediately, including to the engine stack on the front page. For each setting, the settings file takes precedence over the corresponding `DRAMA_LLAMA_LLM_URL`, `DRAMA_LLAMA_LLM` and `DRAMA_LLAMA_LLM_API_KEY` environment variables, and settings you have not saved still follow the environment. `GET /api/config` reports where each value comes from.

Personal subfolders named `juli`, `miller`, `Projects` and `Snippets` are excluded from the sound-effects index by default.

### Using OpenCode Go as the LLM (optional)

With an [OpenCode Go](https://opencode.ai/v2/docs/console/go/) subscription, Drama Llama can parse scripts with a hosted model instead of a local one. This is handy on machines with little VRAM, because the local LLM occupies about 7.6 GB:

```
export DRAMA_LLAMA_LLM_API=openai
export DRAMA_LLAMA_LLM_URL=https://opencode.ai/inference/go/openai/v1
export DRAMA_LLAMA_LLM_API_KEY=<your Go API key from the OpenCode Console>
export DRAMA_LLAMA_LLM=kimi-k3   # or glm-5.3-flash, muse-spark-1.3-contributor, 鈥�
```

Every Go text model works through this OpenAI-compatible endpoint, because the Console translates between API families. As Go requires of clients other than OpenCode, Drama Llama identifies itself as `drama-llama/<ver>` and sends a per-parse `x-opencode-session` header.

Two caveats apply. First, Go traffic counts against your subscription limits, and script parses are long, so a full drama can take several thousand tokens. Second, some Go models train on request data, for example the Muse Spark contributors. The [Go privacy table](https://opencode.ai/v2/docs/console/go/#privacy) has the details.

The Settings page tests the endpoint before you save. It lists the usable models, hides embedding-only models while counting them, and reports failures precisely. An HTTP 401 means the API key is wrong, a 403 means the plan or model does not cover API use, and anything else points to a reachability problem.

## Testing

Run the test suite and the frontend build with:

```
~/.venvs/qwen3tts/bin/python -m pytest
cd web && npm run build
```

Smoke tests against the real models are opt-in:

```
~/.venvs/qwen3tts/bin/python scripts/smoke_breeze.py --scene 1 --speaker MARA
```

## Known gaps

Chatterbox voice samples are uploaded per character, but the app has no recorder or trimmer yet, so characters without a sample get the server's default voice. Suno generation needs the BetterSuno extension and a live `suno.com` tab, and because the bridge owns port 9423, any other `bettersuno-mcp` instance must be stopped first. Cue timing is anchored to lines, so sound effects cannot yet be dragged on the Cut timeline. Finally, LLM parsing requires Ollama and is deliberately serialised against Breeze on GPU.
