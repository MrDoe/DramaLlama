# Drama Llama — agent notes

## What this is

Local single-user web app: paste a script → parse scenes/lines and attribute
speakers → group into per-character, per-scene stems → render with Breeze
(English) or Chatterbox (German) → align/cut line clips with Whisper → search/place
SFX → mix and export.

The product was renamed from Drama Studio to Drama Llama. The Python import
package (`drama_studio`), the checkout directory and the legacy `drama-studio`
CLI alias are intentionally unchanged.

## Current state (M0–M5)

- Deterministic parser + optional Ollama LLM parse with verbatim validation.
- Stem builder, reference design, render + align worker, take/alignment storage.
- Reference voices from uploaded samples: CPU-only decode/window/trim/Whisper
  pipeline, editable transcript, per-character voice list in a dedicated
  Voices workflow step.
- SFX index over `~/Sounds`, cue sheet, mixer with ducking/EQ/loudnorm.
- React UI: projects, script/parse, stems, render, sound, mix.
- 367 backend tests (spatial ones skip without the native renderer), web build green.
- English Breeze path validated on one real scene; German Chatterbox path
  validated end-to-end (20.7 s ANNA stem, 7/7 cuts). Suno music generation and
  WAV download validated through the BetterSuno bridge.
- Project-level engine (`Project.default_engine`: Breeze / Breeze-DE /
  Chatterbox / Suno / Fake) with engine-labelled takes for A/B comparison and
  take-aware cuts.
- Native installers: PySide6 wizard over a stdlib-only core, shared with a
  headless CLI; AppImage on Linux, Inno Setup around PyInstaller on Windows.
  Optional Breeze EN, Breeze DE and Chatterbox, GPU auto-detect with
  `--cpu`/`--gpu`, per-user prefix, pinned versions in `installer/pins.json`.

## Hard constraints

- **Breeze is English + Chinese only** in the local open-weight checkpoint.
  German can optionally use the community Articulatio-DE finetune via the
  `breeze-de` sidecar (Breeze-TTS-2.cpp, research/non-commercial); it is an
  opt-in engine, not the default. German default remains Chatterbox.
  Qwen3-TTS was removed from Drama Llama; do not reintroduce it.
- **One GPU job at a time.** Breeze eager peaks ~8.5 GB; the local LLM ~7.6 GB;
  Whisper + Breeze exceeds 16 GB. Align on CPU int8. The app serializes jobs,
  but other sessions can still hold VRAM — check `nvidia-smi` before a load.
- **Do not fork `align_cuts`.** It is owned by the sibling `~/tts_test`
  session (Kepler). Vendor the stable module under `vendor/breeze_stems/`,
  pin hashes in `VENDORED_FROM.md`, and send fixes upstream.
- **LLM parse must preserve dialogue verbatim.** The validator rejects
  paraphrase (<85% substring coverage) and the API falls back to deterministic.
- **Breeze reference clips:** never send a `speaker` field with a reference;
  it prepends `[S0]` and overrides the reference timbre.
- **Chatterbox runs in a docker container** (`DRAMA_LLAMA_CHATTERBOX_CONTAINER`,
  default `chatterbox-tts-api`, empty disables) serving `CHATTERBOX_URL`
  (default `http://127.0.0.1:4123`). Drama Llama owns its lifecycle:
  `ChatterboxEngine.load()` runs `docker start` and waits for `/health`,
  `unload()` and the end of `render_stem()` run `docker stop`, so its ~4.4 GB
  of VRAM is only held while rendering. It clones an uploaded voice sample;
  without one it falls back to the server's default voice. It has no
  voice-design mode.
- **The installer core must stay stdlib-only.** `installer/core/` is shared by
  the CLI, the wizard and CI; only `installer/gui/` may import PySide6, and
  always lazily. Tests must pass without Qt installed.
- **The installer never installs an LLM.** Deterministic parsing works out of
  the box; Ollama is configured later in Settings. Docker/Vulkan are the only
  system packages it may add, and only with explicit consent.
- **Installer prefixes are per-user** (`~/.local/share/drama-llama`,
  `%LOCALAPPDATA%\DramaLlama`) but engine paths stay at the legacy locations
  (`~/code/breeze-tts`, `~/models/Breeze-TTS-2`,
  `~/code/breeze-tts-cpp/...`, `~/models/Articulatio-DE-...gguf`). The Whisper
  cache path is hardcoded to the Hugging Face hub layout; do not derive it from
  `HF_HOME`.
- **Pin everything in `installer/pins.json`** — uv/Node versions, wheel
  channels, HF revisions, Breeze repo commit and Docker image digests. Never
  resolve `latest` at install time.
- **The spatial renderer is published, never fetched from a moving tag.**
  `.github/workflows/installer.yml`'s `spatial` job builds it from
  `spatial.saf_commit` and attaches `dramallama-spatial-linux-x86_64.tar.gz`
  (+ `.sha256`) to the release. `ensure_spatial` downloads an asset only when
  `pins.json` has both `url` and `sha256` for the platform; an empty `assets`
  map means "skip with a warning", never a failed install, because a mix
  without staging is supported. Fill in the hash from `dist/*.sha256` after the
  first release that carries the asset.
- **A GUI launch failure returns exit code 2**; never fall through to a
  headless install, which would start multi-GB downloads without consent.
- **Suno needs the BetterSuno extension** and a live `suno.com` tab. The
  `suno-bridge/` sidecar owns `ws://127.0.0.1:9423`; only one process can own
  it. Download music as WAV (the M4A path is not reliably decodable).
- **Spatial staging links ISC code, never the GPLv3 plug-in.** The room DSP is
  SAF's `ambi_roomsim` example, wrapped by `src/drama_studio/spatial/`. SPARTA's
  `audio_plugins/_SPARTA_ambiRoomSim_/src/**` (including `ui/SceneModel.*` and
  `StudioShell.*`) is GPLv3 JUCE and must not be copied or adapted into this
  repo; only the shared *coordinate convention* and a future JSON schema are
  exchanged. Do not add a SPARTA submodule to `pyproject.toml`.
- **Staging must never break a mix.** `spatial.available()` False means the mix
  falls back to the dry mono path with a warning; a render failure folds the
  staged tracks back into the centre. Never let the spatial path raise out of
  `build_mix`, and never change the `stage is None` path — it must stay
  byte-identical for every pre-staging project.
- **Room tail is bounded by the DSP, not a setting.** SAF buffers
  `IMS_CIRC_BUFFER_LENGTH` = 32768 samples of source history: **0.68 s at
  48 kHz**, 1.37 s at 24 kHz. Long-reverberation "cathedral" presets will not
  ring. `ambi_roomsim`'s `maxTime_s = -0.05f` is an order-mode sentinel, not a
  50 ms cap.

## Layout conventions

- Python package under `src/drama_studio/` (import name kept for compatibility);
  tests under `tests/`.
- App-specific engine code in `engines/`; `vendor/` and `suno-bridge/node_modules/`
  stay read-only.
- `suno-bridge/` is a Node HTTP sidecar that imports `bettersuno-mcp` from the
  `third_party/bettersuno-mcp` git subproject (pinned commit; `npm ci
  --ignore-scripts` inside it after clone — the postinstall script is local-only
  and gitignored upstream). `serve` auto-starts it (`--no-suno-bridge` opts out).
- Runtime data under `data/` (gitignored). Projects are file-based:
  `data/projects/<slug>/project.json`, `refs/`, `audio/takes/`, `lines/`, `mix/`.
- SQLite only for the job queue, never for project content.
- GPU work runs in `handlers.py`; the API process must not import torch.
- `installer/` sits at the repo root (not under `src/`) and is importable as a
  top-level package; `installer/packaging/` holds the PyInstaller spec, the Inno
  script and the AppImage builder. Build output goes to `build/` and `dist/`
  (gitignored). `scripts/build_installer.py` stages the same payload the tests
  exercise.
- Spatial code splits in three places: `src/drama_studio/spatial.py` (ctypes
  wrapper, geometry, presets, scene assembly — Python, testable without a
  compiler), `src/drama_studio/spatial/` (the C shim + its CMakeLists — the only
  native code in the app), and `web/src/components/StageBoard.tsx` (the Stage
  step). `mixer.py` is the only consumer of the renderer.

## Commands

```bash
~/.venvs/qwen3tts/bin/python -m pytest
cd web && npm run build

# spatial renderer (optional; staging stays disabled without it)
~/.venvs/qwen3tts/bin/python scripts/build_spatial.py          # needs cmake + SAF
~/.venvs/qwen3tts/bin/python scripts/spike_spatial.py          # physics self-test
~/.venvs/qwen3tts/bin/python scripts/bench_spatial.py          # render cost, s per s

~/.venvs/qwen3tts/bin/drama-llama init
./start-ui.sh                                         # build if stale + serve UI
~/.venvs/qwen3tts/bin/drama-llama serve              # API + in-process worker
~/.venvs/qwen3tts/bin/drama-llama serve --no-worker  # separate worker
~/.venvs/qwen3tts/bin/drama-llama worker --once
~/.venvs/qwen3tts/bin/drama-llama suno-bridge
~/.venvs/qwen3tts/bin/drama-llama breeze-de           # German Breeze sidecar (port 8137)

~/.venvs/qwen3tts/bin/python -m installer --dry-run --cpu   # wizard, or headless CLI
~/.venvs/qwen3tts/bin/python scripts/build_installer.py --target payload
~/.venvs/qwen3tts/bin/python scripts/build_installer.py --target linux   # AppImage
~/.venvs/qwen3tts/bin/python scripts/build_installer.py --target windows # Inno, on Windows
```

The installer wizard needs `PySide6`; the core and its tests do not. PyInstaller
cannot cross-compile, so build each target on its own OS (or use
`.github/workflows/installer.yml`).

The Suno sidecar resolves `bettersuno-mcp` via `suno-bridge/node_modules`
(symlink into `third_party/`) and needs the BetterSuno browser extension
connected. After a fresh clone run `git submodule update --init` plus
`npm ci --ignore-scripts` in `third_party/bettersuno-mcp`.

## UI verification (mandatory)

Every UI change must be verified in a live browser via firefox-devtools
against the running app (`./start-ui.sh` or `drama-llama serve` on :8770) —
`npm run build` + pytest alone are not enough. Navigate to the affected page,
operate the real controls (selects, buttons, audio playback), and assert
observable behavior, not just DOM presence.

- Drive the automation browser (fresh profile), never the user's session
  browser. Create scratch projects for tests and delete them afterwards.
- `take_snapshot` truncates deep subtrees — use `evaluate_script` (takes
  `function`, not `script`) for targeted reads, `click_by_uid` for trusted
  clicks (synthetic `el.click()` carries no user gesture: media `play()`
  then rejects `NotAllowedError`, which proves wiring but not audibility).
- `navigate_page` acts on the selected tab — confirm with `list_pages`
  afterwards; a wrong-tab navigation kills page state.
- Firefox cannot demux Suno `m4a-opus` preview streams (`NotSupportedError`
  in `<audio>`); preview via the clip's mp4 `video_url`, which decodes.
- Never kill processes by cmdline-substring patterns that also match your own
  shell (classic self-kill footgun) — kill exact PIDs, or `fuser -k` the port.

## Gotchas

- First SFX scan of `~/Sounds` (322 files, 14 GB) takes ~26 s; later scans use
  `data/sfx_index.json` and are fast. Personal folders are excluded by default.
- Breeze stem render is ~3.7× real time on the 4090 laptop; a 28 s stem took
  103.5 s. Whisper large-v3 CPU alignment took 60.8 s.
- The 16 GB GPU often has a persistent ~4.3 GB `python main.py` process; Breeze
  still fits but the local LLM may not run concurrently.
- `align_cuts` returns `(cuts, confidences)` and takes `language="en"|"de"`.
- Job payloads/result JSON use relative project paths; resolve against
  `ProjectStore.project_dir(project_id)`.
- Engine takes are merged across providers. A re-render replaces only takes
  from the same engine and leaves the others visible for A/B comparison.
- Suno `download_song` can fail on M4A even when a progressive media URL is
  listed; request `format: "wav"` and let Suno convert. The bridge's `/download`
  endpoint defaults to the caller's format, so Drama Llama always asks for WAV.
- Chatterbox is an external GPU service in docker; its server owns the GPU
  memory, not the Drama Llama worker process. The engine stops the container
  on unload and after each render — never leave it running idle; the next
  render restarts it automatically.
- Reference generation (`reference.generate`) is CPU-only: ffmpeg decode,
  faster-whisper large-v3 int8, librosa trim/F0. It must never call
  `_require_gpu`, `EngineManager`, or `BreezeEngine.load()`.
- Per-stem reference selection must pass that `RefVoice.ref_text` into
  `StemRenderRequest`; falling back to `character.ref_text` silently pairs the
  wrong transcript with the clone. `_resolve_reference` owns this.
- Generated reference WAVs are 24 kHz mono PCM-16; `audio.write_mono` defaults
  to PCM-24, so `refvoice` passes `subtype="PCM_16"` explicitly.
- Uploaded generation inputs live in `refs/.uploads/` and are deleted by the
  job; generated voices use unique ids and never overwrite an existing ref.
  Inputs longer than 6 s are accepted and automatically cut to the best 6–11 s
  window; the hard guard is 10 minutes.
- `.page` has a fill-mode animation with a transform, which makes `position:
  fixed` children behave like they belong to the page. Render true full-screen
  dialogs through a React portal; Voices is a workflow step, not a modal.
- `librosa.pyin` can reject every frame on noisy dialogue; `refvoice.measure_f0`
  uses `yin` first and falls back to `pyin`. Keep F0 advisory.
- When shelling out to ffmpeg, put `-i <input>` before output options; a bare
  input path plus an early `-map` makes ffmpeg treat the input as an output.
- `ambi_roomsim_process()` takes **arrays of per-channel pointers** for both
  inputs and outputs (`outputs[i]` is channel *i*), not one flat buffer — a
  single pointer writes out of bounds and segfaults. It also only renders when
  `nSamples == 128` exactly and silently emits silence otherwise; the shim does
  the buffering so callers pass any block size.
- For a 2-channel decode use `LOUDSPEAKER_DECODER_SAD` with max-rE.
  `LOUDSPEAKER_DECODER_ALLRAD` returns an **all-zero matrix** for `nLS = 2`
  (its VBAP table needs ≥3 speakers to form triangles), so the master is pure
  silence with no error anywhere. MMD is ill-conditioned for `nLS < nSH`.
- Building SAF's example standalone needs `set(example_prefix "saf_example_")`
  before `add_subdirectory(examples)`; that variable normally comes from SAF's
  top-level CMakeLists, which this build bypasses. Also set
  `SAF_PERFORMANCE_LIB` or configure aborts.
- Stage azimuth 0 faces the front wall (`y = 0`), `+90` faces `+x`, which is the
  listener's **left**. SAF's decoder convention is `+x` = front, so the shim
  rotates the field by `90 - facing`. `ims_shoebox` also flips y internally
  (`room_dims[1] - y`) — never pre-flip a position.
- Moving a source re-runs `ims_shoebox_computeEchograms` for that source
  (crossfaded), so per-sample motion is wasted CPU. `MOTION_GRID_S` (100 ms)
  bounds it; measured cost is ~0.12–0.24 s of wall time per audio second at
  order 3 with 6 sources, so staging is comfortably faster than real time and
  needs no GPU.
- Stage positions must be inside the room; the Pydantic validators compare
  against the *scene's* room, and `scene`/`room` field order matters for the
  `info.data` lookup in `SceneStage`. Ear and mouth height come from the room
  (`layout_scene`), never literal 1.6/1.5 m: a 1.4 m preset with a 1.6 m listener
  fails that validator and the layout endpoint answers 500.
- Motion keyframe times are **scene-relative**, and `render_scene` subtracts the
  scene's `start_s` before looking them up. Stem `position_s` is not comparable
  across speakers either — every stem starts at 0 — so speaker windows for
  auto-staging come from `layout_script_order`, relative to the scene's first
  sound. Read the keys at project time and every scene after the first one stops
  moving; `tests/test_spatial.py` pins both halves of that.
- "Spread the cast" must not change the room: a staged scene keeps its own room,
  and `StageBoard` sends the room currently on screen because a preset picked a
  second ago is unsaved and the server cannot see it.
- Adding a production step means **five** edits in `ProjectPage.tsx`: `STEPS`,
  the `activeStep` union, the rail's `available` gate, the rail button's
  `onClick` dispatcher, and the render ternary. Forgetting the dispatcher is
  silent — the button looks enabled and does nothing. Only a live click
  catches it.
- The Stage UI must re-clamp the listener and actors whenever the room changes
  (`fitToRoom` in `StageBoard.tsx`). Applying a smaller room preset otherwise
  leaves the listener outside the new walls and the save 422s.
- The Ambisonic master carries staged dialogue only; unstaged scenes and the
  SFX bed have no SH representation and read as silence there. It is padded to
  the stereo master's length so the two line up for picture muxing.
- FastAPI body-validation failures return `detail` as an **array** of
  `{loc,msg}`. `api.ts` flattens it via `describeDetail()`; assigning
  `body.detail` straight to a message renders as `[object Object]`.

<!-- BEGIN opencode-rag -->
## Code Navigation

ALWAYS use OpenCodeRAG tools before reading or editing:
- **Search first** — `search_semantic(query)` instead of grep/glob
- **Skeleton before read** — `get_file_skeleton(filePath)` then read specific lines
- **Usages before edit** — `find_usages(symbolName)` before modifying any symbol
- **Images via describe** — `describe_image(filePath, systemPrompt?)` — never read raw bytes
- **Recall quirks** — `recall_quirks(query)` when you hit a known pitfall
- **Add quirks** — `add_quirk(content)` when you discover a non-obvious fact
- **Fix quirks** — `update_quirk(id, ...)` / `delete_quirk(id)` when a stored quirk is outdated or wrong

If no results, run `opencode-rag index`.

### Decision tree — ALWAYS follow this order
1. User mentions code behavior/architecture → `search_semantic(query)`
2. User mentions a file path → `get_file_skeleton(filePath)` THEN `read` on specific lines
3. User mentions a function/class/variable to edit → `find_usages(symbolName)` THEN `search_semantic` THEN `edit`
4. User asks a code question → `search_semantic` to gather context before answering
5. User asks about an image or visual asset → `describe_image(filePath)` (optionally pass `systemPrompt` to focus on specific features) to retrieve its generated description, then optionally `search_semantic` for related code
6. You encounter an error or need to recall a known pitfall → `recall_quirks(query)`
7. You discover a non-obvious fact or workaround → `add_quirk(content)` to persist it for future sessions
8. A recalled quirk is outdated or wrong → `update_quirk(id, ...)` to fix it, or `delete_quirk(id)` if it no longer applies

### Proactive triggers — you MUST call these tools when
- User asks about code behavior, architecture, or implementation details
- User asks to edit, refactor, or fix code — call `find_usages` first
- User references files or functions you haven't read yet
- User says "find", "search", "look up", "where is", "how does"
- User refers to an image, screenshot, diagram, or visual asset
- Before answering ANY code-related question, retrieve context first
- Before reading ANY file, call `get_file_skeleton` to orient first

### Anti-patterns — NEVER do these
- Reading full files without calling `get_file_skeleton` first (wastes tokens)
- Editing a function without calling `find_usages` first (breaks call sites)
- Answering code questions without calling `search_semantic` first (you guess at behavior)
- Using `grep`/`glob` when `search_semantic` would find the answer faster
- Treating image files as text — use `describe_image` instead of reading raw bytes
- Using `npx opencode-rag quirk` shell commands instead of the built-in quirk tools (`add_quirk` / `recall_quirks` / `update_quirk` / `delete_quirk`) (the tools are faster, already loaded in-process, and go through the trust monitor)

### MANDATORY quirk capture rules — you MUST call `add_quirk` when
- A build, test, or type-check command fails and you resolve it
- You discover an undocumented library constraint, peer dep, or workaround
- You learn an environment-specific requirement (OS, tool version, etc.)
- You make a design decision that future sessions should remember
- You resolve a gotcha that cost more than one attempt

### MANDATORY quirk hygiene — you MUST call `update_quirk` or `delete_quirk` when
- A stored quirk is outdated, wrong, or has been fixed — update it or delete it instead of adding a contradicting duplicate
- NEVER finish a coding session without adding quirks for resolved errors.

### Long-running commands
Long-running commands:
- Any `shell` call expected to take more than 30s MUST set `background: true` (returns immediately; the session is notified on completion) or set an explicit `timeout` (ms).
- Default foreground timeout: 120000ms. Never run builds/tests/indexes in the foreground beyond 30s; if a call is already running, press Ctrl+B to background it (OpenCode: `session.background`).
- OpenCodeRAG measures foreground shell duration and records a quirk when a call exceeds the threshold, so repeated foreground runs become visible.
<!-- END opencode-rag -->
