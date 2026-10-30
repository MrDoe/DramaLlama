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
- SFX index over `~/Sounds`, cue sheet, mixer with ducking/EQ/loudnorm.
- React UI: projects, script/parse, stems, render, sound, mix.
- 60 backend tests, web build green.
- English Breeze path validated on one real scene; German Chatterbox path
  validated end-to-end (20.7 s ANNA stem, 7/7 cuts). Suno music generation and
  WAV download validated through the BetterSuno bridge.
- Per-stem engine selection (Breeze / Chatterbox / Fake) with engine-labelled
  takes for A/B comparison.

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
- **Chatterbox needs a running server** at `CHATTERBOX_URL` (default
  `http://127.0.0.1:4123`). It clones an uploaded voice sample; without one it
  falls back to the server's default voice. It has no voice-design mode.
- **Suno needs the BetterSuno extension** and a live `suno.com` tab. The
  `suno-bridge/` sidecar owns `ws://127.0.0.1:9423`; only one process can own
  it. Download music as WAV (the M4A path is not reliably decodable).

## Layout conventions

- Python package under `src/drama_studio/` (import name kept for compatibility);
  tests under `tests/`.
- App-specific engine code in `engines/`; `vendor/` and `suno-bridge/node_modules/`
  stay read-only.
- `suno-bridge/` is a Node HTTP sidecar that imports the installed
  `bettersuno-mcp` modules. It must be started separately from `serve`.
- Runtime data under `data/` (gitignored). Projects are file-based:
  `data/projects/<slug>/project.json`, `refs/`, `audio/takes/`, `lines/`, `mix/`.
- SQLite only for the job queue, never for project content.
- GPU work runs in `handlers.py`; the API process must not import torch.

## Commands

```bash
~/.venvs/qwen3tts/bin/python -m pytest
cd web && npm run build

~/.venvs/qwen3tts/bin/drama-llama init
./start-ui.sh                                         # build if stale + serve UI
~/.venvs/qwen3tts/bin/drama-llama serve              # API + in-process worker
~/.venvs/qwen3tts/bin/drama-llama serve --no-worker  # separate worker
~/.venvs/qwen3tts/bin/drama-llama worker --once
~/.venvs/qwen3tts/bin/drama-llama suno-bridge
~/.venvs/qwen3tts/bin/drama-llama breeze-de           # German Breeze sidecar (port 8137)
```

The Suno sidecar needs `bettersuno-mcp` installed under
`suno-bridge/node_modules` and the BetterSuno browser extension connected.

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
- Chatterbox is an external GPU service; `EngineManager` does not unload it.
  Its server owns the GPU memory, not the Drama Llama worker process.

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
