# Wiki log

## [2026-10-29] seed | Drama Studio knowledge base

Seeded the wiki from the M0–M5 implementation and the sibling Breeze
stem/alignment session. Pages cover the app, the stem pipeline, GPU
serialization, the engine adapters, the job worker, SFX/mix, and the vendored
aligner contract.

## [2026-10-29] update | Suno, Chatterbox and engine selection

Added the Suno HTTP bridge page, replaced the Qwen3 engine page with Chatterbox
Multilingual, and documented per-stem engine selection with engine-labelled
takes.

## [2026-10-29] ingest | UI launcher script

Added `start-ui.sh` (auto-build + serve, `--dev`, `--open`, health preflight)
and documented it in the README and agent notes; added
[[entities/start-ui-launcher]].

## [2026-10-30] update | combined Stems & Render UI steps

Merged the Stems and Render production steps into one "Stems & Render" tab in
`ProjectPage.tsx`; updated README workflow and app wiki entity.
## [2026-10-29] ingest | suno-engine

## [2026-10-29] query | German Breeze feasibility

## [2026-10-30] update | Suno decoupled from Breeze

Suno was only reachable through language/Breeze assumptions. Added
`Project.default_engine` (create + `PUT`, `"auto"` to clear), resolved it in the
render endpoint, and made the project header, New Project engine picker, Cast
panel and Render queue label the real engine. Legacy `Character.engine` values
that merely mirrored the language default now inherit the project default;
changing the project engine rewrites only stems on the old default. Only
Breeze/Breeze-DE require reference files.

## [2026-10-29] ingest | German Breeze finetune (Articulatio-DE)

## [2026-10-30] implement | breeze-de engine (Articulatio-DE sidecar)

## [2026-10-30] rename | Drama Studio → Drama Llama

Rebranded the product, UI title, FastAPI title, CLI (`drama-llama`, legacy
`drama-studio` alias), `DRAMA_LLAMA_*` env vars (legacy names still fall back),
launcher, README and agent notes. Kept the `drama_studio` import package and
the checkout directory unchanged. Added a hand-authored cartoon llama mascot
(`web/public/drama-llama.svg`) and app icon (`drama-llama-mark.svg`); the first
draft read as a rabbit, so the final uses profile anatomy with a long neck and
outward-curved ears. Renamed the app page to [[entities/drama-llama-app]] and
added [[entities/drama-llama-mascot]].

## [2026-10-30] update | Avatar switched to supplied llogo.png

Replaced the hand-authored SVG mascot with the provided `web/llogo.png`
(cartoon llama with headset). Kept the 1057×992 master at `web/llogo.png`,
generated a cropped 512×512 `web/public/llogo.png` and a 64×64 favicon, and
removed the SVGs.

## [2026-10-30] update | Hero logo replaces headline

Moved the large `llogo.png` into the left hero slot (`.hero-logo`), replacing
the old "Scripts in. Drama out." `<h1>`; the engine cards remain in the right
column. Header mark bumped to 46 px for more presence.

## [2026-10-30] update | Professional hero: headline + studio panel

Restored the "Scripts in. Drama out." `<h1>` (tighter 42–68 px scale) and
rebuilt the right column as one quiet `.studio-panel`: 64 px logo header with
status dot, then the three engine rows as borderless spec-sheet dividers
instead of nested glow cards. Retired the orange-bordered `.hero-mascot`
sticker card (it double-framed the logo's own orange tile), slimmed the header
mark to a 40 px neutral-bordered tile, and scoped `.hero-copy p` off the
eyebrow.

## [2026-10-30] update | Suno joins the hero engine stack

Added the missing fourth `EngineCard` (Suno · "Music · prompt → score via
bridge" · `WEB` badge, `MusicNotes` icon, teal tile) after Whisper, and
reworded the panel subtitle to "Engine stack · voices, music, alignment"
since Suno runs through the BetterSuno bridge, not on this machine.

## [2026-10-30] update | Configurable LLM (local/cloud) + hero row

The script-parsing LLM is now configurable beyond local Ollama:
`DRAMA_LLAMA_LLM_URL` (falls back to legacy `OLLAMA_URL`) points at any
Ollama-compatible endpoint, `DRAMA_LLAMA_LLM_API_KEY` adds a Bearer token for
hosted/cloud offerings, `OllamaClient.provider` reports `local` vs `cloud`,
and `GET /api/config` exposes model/provider/key-configured (never the key).
The hero stack leads with the live LLM row (model · parsing & casting,
GPU/WEB badge) in pipeline order LLM → Breeze → Chatterbox → Whisper → Suno;
`EngineCard` takes a `tone` prop instead of fragile `nth-child` selectors.
Tests: 67 passed. README config table updated.

## [2026-10-30] update | OpenCode Go models as LLM via OpenAI-compatible client

Yes — Go exposes `POST https://opencode.ai/inference/go/openai/v1/chat/completions`
(Bearer Go key; every text model works through it, Console translates families).
Added `OpenAIChatClient` (`src/drama_studio/llm.py`) with the same duck type as
`OllamaClient`, selected by `DRAMA_LLAMA_LLM_API=openai` via `make_llm_client()`.
It sends `response_format: json_object`, parses SSE streams, identifies as
`drama-llama/<ver>` with a per-parse `x-opencode-session` header (Go requirement),
and no-ops `unload`. `/api/config` gains an `api` field; README documents the
Go setup, per-token limit cost caveat, and training-data privacy note. Tests:
85 passed (5 new). Lesson: batch multi-line api.py touches atomically — a
reloader importing between the import-block and annotation edits NameErrors.

## [2026-10-30] update | LLM configurable via Settings GUI

New Settings page (`/settings`, topbar nav): protocol segmented control
(Ollama / OpenAI-compatible), endpoint URL, model input with a datalist fed by
the endpoint's own model list, API-key password field with remove affordance,
Save + Test connection (auto-tests on load). Backend: `settings.py` layers
file > env > default per key with source tracking; `PUT /api/config` validates
and rebuilds `app.state.llm`; `POST /api/config/test` checks reachability
without persisting; client constructors resolve defaults live via
`resolve_llm()` so worker jobs pick up GUI saves. Key never leaves the server.
Verified live: save roundtrip, real 13-model Ollama listing, hero stack
unchanged. Tests: 111 passed. README rewritten around the GUI.

## [2026-10-30] update | Model dropdown on Settings page

Replaced the model datalist with a real `<select>` fed by the endpoint's model
list (plus a "(current)" entry when the saved model isn't listed and an
"Other (custom value)…" escape hatch to a text field, with a back-to-list affordance). Form edits clear the connection status so a stale "Connected"
can't mislead. Verified live: 13 Ollama options + custom entry render.

## [2026-10-30] update | Go fix verified live via firefox-devtools

User's Go setup failed with "no response": two stacked causes. (1) My
reachability check hit `{base}/models`, which 404s on Go (catalog lives at
`/go/v1/models`), so Test cried unreachable on a healthy endpoint — fixed with
a catalog fallback + a `check()` that distinguishes unreachable / 401 / 403 /
404. (2) The saved model `gemma-4-12b-uncensored` is not a Go model id at all
(local Ollama name carried over) → chat 400. Tested the GUI in real Firefox
(MCP): auto-test listed 28 usable Go models, picked `kimi-k3` from the
dropdown, saved, "Connected · cloud · kimi-k3", and a minimal chat returned
valid JSON. Local Ollama was down during the session, so its capability
probing (hides `*embed*` via /api/show) is unit-covered only.

## [2026-10-30] update | Null-tolerant LLM schema (beam-free fix)

c07cef parsed to 0 lines: beam-free emits `"title": null` etc., and strict
pydantic models rejected the whole document. LLM output models now extend
`_LenientModel`, which drops explicit nulls for fields with defaults (required
fields like speaker/text/name still fail loudly). Verbatim + coverage gates
unchanged. Also: never `pkill -f` with a pattern present in the invoker's own
command line — it kills your shell and leaves :8770 down; verify with pgrep
and a health check instead.

## [2026-10-30] update | LLM normalizes, parser structures (no more JSON)

Per request, the LLM no longer emits JSON: `normalize_llm_text` rewrites any
attribution style (`[NAME] "x"`, `Name: "x"`, `NAME (dir) text`, bold headers)
into canonical `NAME (direction): text`, and `parse_script` structures it
(`parse_normalized` = deterministic parse + verbatim/coverage gate, shared by
blocking and streaming endpoints). Removed `LLMScript*` models, `_convert`,
`_json_schema`, `parse_llm_content`; both clients gained `chat_text`.
Verified live with beam-free on the exact messy formats (3/3 lines,
coverage 1.0). FailingLLM fakes gained `chat_text`.

## [2026-10-30] update | Render shows per-stem Suno voice/title/tags

Render cards with the Suno engine now show a Suno section: voice persona
picker (reused, stored per character), per-stem song title + style tags
inputs (empty clears to engine defaults), saved via PATCH. New
`Stem.suno_title/suno_tags` flow through `StemRenderRequest` into the bridge
payload (was hardcoded `dialogue <id>` / `audio drama radio play`). Per user
request, persona selection lives ONLY in Render now — removed from the Script
Cast panel (badge + hint remain). Verified live in Firefox incl. save
roundtrip; scratch project deleted.

## [2026-10-30] investigate | Suno Turnstile blocks every render

Renders fail with "could not be solved automatically". Proven: Suno requires
Turnstile (v2), extension is connected, installed 2.5.0 matches source 2.5.4 on
the captcha path, and the observed branch is the empty-`executeScript`-result
one (no widget ever rendered). Ruled out frameIds and async-func support in
Firefox 154 with a throwaway probe extension. Filed
[[concepts/suno-turnstile-captcha]] and
[[concepts/batch-render-and-captcha-recovery]]; added `POST /captcha` to the
bridge to force a solve on demand.

## [2026-10-30] implement | Batch render, captcha recovery, error dialog, extension hardening

Landed all four phases. Backend: `POST /api/suno/captcha` (+ bridge
`POST /captcha`), `CaptchaRequiredError` with `CAPTCHA_REQUIRED:` marker and
`is_captcha_error()`; render handler raises `PermanentJobError` so captcha
failures are not retried. Frontend: `waitForJob()` + sequential `renderAll`
(skips stems with takes), **Render all** with batch progress, actionable error
line + Details modal + **Re-check verification** that resumes a paused batch.
Extension: wakes/reloads a discarded tab, injection retry ladder, content.js
`renderCaptchaWidget` fallback, diagnostics in the error; rebuilt
dist/firefox 2.5.4. Verified: 150 tests, web build, fake-engine batch 1/3→3/3,
dialog + recheck roundtrip live. Remaining: user must reload the extension
for the fallback path, then confirm the real Andreas render.

## [2026-10-30] fix | Suno free-plan render works end to end

Andreas on v6-mini (`chirp-goose`), Free plan: job succeeded — 2 Suno takes
(119.5 s and 144.0 s), WAV conversion, Whisper alignment over 8 lines.
Three bugs fixed along the way: (1) the engine sent `style_weight: 20` by
default and Suno rejects it on free accounts with "This feature isn't
available on your current plan" — paid knobs (style/weirdness/audio,
personas) are now gated on the new Suno plan setting (Free default, Pro
opt-in, env `DRAMA_LLAMA_SUNO_PLAN`); (2) `SunoBridge.make_stems` had been
nested inside `_clip_ready`, so stem extraction did not exist and renders
crashed after generation — restored as a method plus a regression test and a
non-fatal guard; (3) the extension fallback that renders its own Turnstile
widget (2.5.5) — Suno's page no longer exposes `window.turnstile`, so token
minting now injects Cloudflare's api.js into the page and reads the token
back through a DOM attribute. 157 tests green.

## [2026-10-30] fix | Suno alignment no longer trusts a full mix

Alignments were nonsense (conf 0.08-0.52) because Suno vocal separation is
also paid: `/stems` returns 403, so Whisper ran on the full song. Takes now
carry `vocal_isolated`; when a Suno take has no isolated vocals both the
render and the realign path skip Whisper and produce a proportional
word-count estimate (`aligner="estimate"`, confidence 0.05, fallback=True)
instead of confidently wrong cuts. 159 tests green; realign verified live.

## [2026-10-30] build | Stop running instances before launching

New `scripts/stop_running.py` (SIGTERM every `serve`/`worker`, SIGKILL after
10s, never matches itself or its ancestors, leaves the Suno bridge alone) is
wired as the VS Code task `drama-llama: stop running`. All three `serve` launch
configs now set `preLaunchTask`, and the `dev: api` task has `dependsOn`.
Tests: `tests/test_stop_running.py` asserts self-exclusion and that the bridge
and test processes are never targeted. 163 tests green.
## [2026-10-30] fix | Suno account-switch download failure

## [2026-10-30] build | Multitrack Cut board, script-order clips

Replaced the per-stem Cut cards with one shared timeline
(`MultiTrackEditor.tsx`) where every line region sits at its source-script
slot: `computeTimeline()` mirrors `mixer.py` (script order, `pause_ms`,
`scene_gap_ms`, cut length as clip duration, 2 s tail). The earlier
median-offset approach was dropped — it could push a take's regions far out of
script order when a take's internal pauses differed from the cut-length
timeline. Trimming an edge now shifts all later lines like the mix; body drag
is a slip edit inside the region. `SyncEngine` schedules one `BufferSource`
per line segment on a shared `AudioContext` clock instead of one source per
stem. Waveform rows grow to the track card's measured content height. Verified
live on `hinterkaifeck-2`: total 294.3 s, region order s1-01…s1-16, trim
294.3→291.3 s, slip preserved 21.85 s length, Space/Enter transport and region
audition advanced and paused correctly. Added
[[concepts/cut-board-multitrack]].

## [2026-10-31] build | Take-aware cuts

Suno re-renders now append tracks instead of replacing them, and each cut
names the take it was measured on (`AlignedLine.take_id`, `None` = the
stem's selected take). `render_stem` freezes legacy cuts before selection
changes, dedupes Suno by `(engine, clip_id, vocal_isolated)`, preserves
non-empty cut sheets, and ratio-remaps cuts whose take disappeared.
`PATCH .../alignment` slices each line from its own take (cached decode,
per-take range check); `select_take` only queues a destructive realign when
safe. The Cut board decodes needed takes lazily, labels regions `T#`, and adds
a per-region Track dropdown that scales the window by the duration ratio.
Verified live on a scratch project (3.0-5.0 -> 3.5-5.83 on a 12 s -> 14 s
switch, saved clip 660 Hz / 2.33 s, reload preserved). Added
[[concepts/take-aware-cuts]]; updated [[concepts/cut-board-multitrack]],
[[entities/suno-engine]] and [[concepts/script-to-stem-pipeline]]. 175 tests
green, web build green.

## [2026-10-31] refactor | Project-level engines + cuts domain hardening

Removed per-stem engine overrides end to end (`Stem.engine`,
`Character.engine`, `RenderBody.engine`/`StemUpdate.engine` now 400 with
"set Project.default_engine instead"). Old project.json files with legacy
engine keys still load (pydantic extra ignore + `store.get` normalizes legacy
`take_id=None` via `cuts.normalize_alignment`). Added shared
`src/drama_studio/cuts.py` (freeze/remap/scale/validate/pick-selected) and
made handlers + API use it; `render_stem` honors
`StemRenderResult.selected_take_id`, fails clearly on missing replacement
audio, and sets `mix_stale`. `realign_stem` no longer clobbers manual sheets
(queued-realign race). `PATCH .../alignment` validates ownership/duplicates,
writes batches atomically, and sets/clears `mix_stale`; new batch endpoint
`PATCH .../alignments` rebuilds the mix once for Save-all. Frontend drops the
per-stem Engine dropdown (badge shows project default), uses batch Save-all,
shows `mix stale`, surfaces missing takes instead of falling back, guards
unload when dirty, and LRU-caps/prunes decoded takes in `SyncEngine`. Tests:
186 passed (11 new: `test_cuts.py`, `test_cut_board.py`); web build green.
Updated [[concepts/engine-selection]], [[concepts/take-aware-cuts]], added
[[entities/cuts-module]].

## [2026-11-01] ingest | Native installers

Documented the installer package: stdlib-only core shared by the PySide6
wizard and the headless CLI, per-user layout with legacy engine paths, pinned
runtimes/wheels/models/Docker digests, AppImage and Inno Setup packaging, the
GitHub Actions build, and the GPU-optional component matrix. Added
[[entities/installer]] and [[concepts/gpu-optional-install]]; refreshed the
README and AGENTS notes.

## [2026-11-01] release | v0.3.0 published

Published https://github.com/MrDoe/DramaLlama/releases/tag/v0.3.0 with the
Linux AppImage, Linux tarball, Windows installer, and SHA-256 checksums.
Fixed the AppImage build (desktop file + icon at AppDir root) and re-ran the
workflow after moving the tag.

## [2026-11-02] fix | Installer 0.3.0 crash at the Whisper step

Diagnosed the reported `AttributeError: 'list' object has no attribute
'items'` from a CPU install at step [4/8]: `installer/core/whisper.py`
converted the pinned file map to a list for the download loop, then iterated
it as a dict in the final completeness check — after the 3 GB snapshot had
already downloaded and verified. Fixed both loops to iterate the same
`(name, item)` pairs; added offline regression tests for the complete-cache
and download paths. Confirmed against the real cache (`downloaded=False`,
3.2 s). Added the gotcha + resume notes to [[entities/installer]].

## [2026-11-02] release | v0.3.0 re-published with the installer fix

Moved the annotated `v0.3.0` tag to `04e50be` and force-pushed it so the
Installers workflow rebuilt both targets from the fix commit. All four jobs
passed and the `release` job clobbered the six release assets (AppImage,
Linux tarball, Windows setup plus checksums). The tag now matches
`master`; [[entities/installer]] carries the underlying gotcha.

## [2026-11-02] fix | Components page checkbox/description overlap

The Components page needed ~650 px inside a ~505 px wizard page, so Qt
overflowed the QGroupBox layouts and drew each "Install this component"
checkbox over the description label (the labels' fixed
`setMinimumHeight(60/72)` made it worse). Removed those fixed heights and
wrapped the page in a frameless, transparent QScrollArea; verified offscreen
at 760×600, 900×700 and with a +4 pt font (scrollbar appears, no collisions).
Recorded in [[entities/installer]].

## [2026-11-02] release | v0.3.0 assets updated with the Components fix

Moved `v0.3.0` to `f769d14` (Components page scroll area) and force-pushed;
run 41410088383 rebuilt all targets with all four jobs green. Verified the
published Linux tarball: checksum OK, `_internal/payload/installer/gui/app.py`
contains the QScrollArea wiring and no `setMinimumHeight` calls.

## [2026-11-02] test | Released AppImage installed CPU-only

Downloaded `DramaLlama-0.3.0-x86_64.AppImage` from the release, verified its
SHA-256, and ran a headless CPU install into `/tmp/opencode/dl-test`: all
8 steps passed, `Verification passed`, `completed: true`, `device: cpu`,
torch `2.15.0+cpu` with `cuda False`, 4.0 GB on disk (Whisper reused the
shared `~/.cache/huggingface` snapshot). Drove the packaged wizard under
Xvfb to the Components page: the scroll area is present, the four option
boxes render, and the checkbox no longer collides with the description.
The scratch install was left in place for inspection.

## [2026-11-02] fix | Installer default folder no longer follows a temp pointer

`resolve_prefix()` used to trust `<default>/location.txt` unconditionally. A
scratch CPU install at `/tmp/opencode/dl-test` (left from the released-AppImage
test) had written that pointer, so `--dry-run` and the wizard pre-filled
`/tmp/opencode/dl-test` instead of `~/.local/share/drama-llama`. The resolver
now ignores pointers whose target is missing/not an install or lives under the
OS temp root; an explicit `--prefix` still wins. Removed the stale pointer, added
three regression tests, and confirmed `--dry-run --cpu` resolves the per-user
prefix. Recorded in [[entities/installer]].

## [2026-11-03] build | Cut board cue lanes + unchanged-SFX PATCH rule

Finished the in-progress cue lanes: Music/SFX rows render on the shared
timeline (`computeCueSpans` mirrors the mixer), drag stages `offset_s`,
inspector edits anchor/offset/gain/kind/loop/duck with audition and two-click
delete, and Save all persists cue edits with cuts (toolbar counts both, mix
stale badge shown). `ProjectPage` wires `saveCues`/`deleteCue`. Backend:
`CueUpdate.kind/filter` are Literals, and `update_cue` only re-validates
`sfx_id` when it changed — full-body saves no longer 404 when the file is not
indexed here (regression test `test_cue_patch_keeps_missing_sfx_id`). Verified
live in Firefox on `was-wir-begraben-haben-7a3f9c` (lanes, inspector, save
roundtrip, delete-arm, audition no-crash; project values restored). 244 tests
green, web build green. See [[concepts/cut-cues-lanes]].

## [2026-11-03] ingest | render-error-dialog concept

Created the missing [[concepts/render-error-dialog]] page (referenced by
[[concepts/suno-turnstile-captcha]] but never created). Documents the
ErrorDialog modal, actionable last-error-line extraction, captcha hint,
Re-check verification resuming a paused batch, and the Details modal.
Added to index.md.

## [2026-11-03] ingest | cut-region-effects concept

Created [[concepts/cut-region-effects]] documenting the in-progress per-region
DSP (gain/fade/speed/pitch/reverb/dereverb): field table, apply_region_effects
order, preview-vs-mix, persistence. Marked in-progress for preview/inspector.
Added to index.md.

## [2026-11-04] update | Cut editor region effects wired

Per-region fade/speed/pitch/reverb/dereverb + volume now render in the mixer via `audio.apply_region_effects` (speed shortens slot duration). Fixed 5-tuple unpacking in `build_mix` loops and deduped `CutUpdate` to a single effects block. Updated [[concepts/cut-cues-lanes]] and [[concepts/cut-region-effects]]. 247 pytest green, web build green; Firefox verify pending.

## [2026-11-04] update | Cut-board wiki sanitized

Updated [[concepts/cut-region-effects]] to reflect completion (backend DSP + model +
API + mixer + SyncEngine preview + inspector; 247 pytest + web build green;
Firefox verified). Trimmed the duplicated effects table in
[[concepts/cut-cues-lanes]] to a summary + link. Refreshed
[[concepts/cut-board-multitrack]] cross-refs and lastReviewed. No shutdown.

## [2026-11-04] verify | Cut effects + drag/align live, save race fixed

Firefox run on scratch project `wiki-cut-fx-test-1a2b3c`:
- Align-to-script re-sorted all three stems into strictly increasing global
  positions (0, 25.93, 39.65, …), removing the legacy per-stem overlap at 0.
- LMB body drag moved `s1-02` 25.93 → 47.1 → 68.27 s; Save-all roundtrip
  ~5.75 s ended with `mix_stale: false`.
- Inspector set +6 dB, fades 0.3/0.4 s, speed 150 %, pitch −300 ¢, reverb 40 %,
  dereverb on; all persisted and the mix rebuilt (~6.3 s). Inspector Play
  toggled to Stop with no error.
- Fixed a real save race: `ProjectPage` wrapped save handlers in `void`, so
  dirty state cleared before the PATCH finished. Handlers now return promises,
  rethrow after `setError`, and `MultiTrackEditor` keeps stems dirty on
  failure. Documented in [[concepts/cut-region-effects]] and
  [[concepts/cut-cues-lanes]].
- Local gemma4 (`gemma-4-12b-uncensored` on llama.cpp :8080) correctly heard
  added reverb and the quieter/faded clip in A/B excerpts; its speed A/B
  answer was wrong, but duration 8.0 s → 4.44 s verifies numerically.
Scratch project and temp audio removed; pytest + web build re-run before commit.

## [2026-11-04] fix | Pitch was a no-op; now a phase-vocoder shift

The "down-resample then up-resample" pitch pair inverted itself and left the
frequency unchanged (verified: 220 Hz stayed 220 Hz at ±700 ¢). Replaced with
`librosa.effects.pitch_shift` (duration-preserving), lazily imported and
warning-suppressed; minimal installs without the `tts` extra leave pitch
unchanged instead of failing the mix. Added a frequency regression test:
220 Hz +700 ¢ → 329.5 Hz with unchanged length. Updated
[[concepts/cut-region-effects]].

## [2026-11-04] release | v0.3.2 published

Tag `v0.3.2` (release commit bcf9a84) shipped the Cut board work: movable
sections, align-to-script ordering, per-region effects, save-race fix and
the pitch-shift fix. CI run 41921637900 green (installer tests, AppImage,
Windows Setup, release upload); the GitHub release has all six assets and
the AppImage checksum re-verified locally. v0.3.1 is the previous release.

## [2026-11-05] release | v0.3.3 published

Tag `v0.3.3` (release commit 0653cc5) ships cutting marked sections out of
clips on the Cut board: Shift-drag inside a clip marks a section,
Backspace/Delete (or the Cut-out button) removes it and splits the clip
into adjacent head/tail segments, closing the gap like the next mix rebuild.
Timeline, schedule, drawing, selection and inspector are per-segment now
(slots keyed by `cut.id ?? line_id`, `lineStarts` for cue anchors, matching
the mixer stagger), and batch Save-all accepts several cuts per line.
Verified: web build green, full backend suite 256 passed (2 new split tests).

## [2026-11-05] fix | installer TLS trust store (v0.3.4)

A CachyOS user hit `CERTIFICATE_VERIFY_FAILED` at the uv step of 0.3.1: the
frozen AppImage carries Ubuntu's compiled-in OpenSSL paths (`/usr/lib/ssl`),
which do not exist on Arch. New `installer/core/tls.py` probes the host store,
validates candidates by CA count, exports `SSL_CERT_FILE` for child processes,
and falls back to an unverified context when nothing is usable; `download.py`
turns certificate failures into a non-retried `TlsError` instead of retrying a
deterministic error three times. Added `--check-tls` / `--insecure-tls`, a
troubleshooting entry, and an Arch-container check in the installer workflow.
See [[entities/installer]].

## [2026-11-05] release | v0.3.4 published

Tag `v0.3.4` (release commit b116c74) publishes the installer TLS trust-store
fix. CI run 42094672349 green: installer tests, Linux AppImage, Windows Setup,
and the new Arch-container TLS check (system store and no-store fallback both
verified), release upload complete. Assets: AppImage, linux tarball, Windows
Setup and their `.sha256` files.

## [2026-11-13] ingest | Spatial stage rendering, Spatial coordinate convention

New Stage production step (M0–M3): SPARTA AmbiRoomSim's room acoustics now
render Drama Llama mixes. Integrated at the DSP layer, not the plug-in layer —
`ambi_roomsim` is ISC and reusable, the GPLv3 JUCE wrapper is neither linked nor
copied, and the plug-in is realtime-only with no file export anyway.

- M0 spike: SAF 1.3.5 built standalone, ctypes shim written, verified against
  physics (3.43 m → 10.00 ms onset, front source centred, ±x panning, listener
  rotation recentres). Benchmarked: order 3, 6 moving sources ≈ 0.22 s CPU per
  audio second — staging needs no GPU and does not join [[concepts/gpu-serialization]].
- M1 model: `StageRoom`/`StageListener`/`StageActor`/`StagePath`/`SceneStage`
  under `Project.stage`, validated (16 sources, positions inside the room,
  sorted keyframes, unique speakers). `stage is None` keeps the old mix exactly.
- M2 renderer: `spatial.py` + `spatial/spatial_shim.c`, wired into `build_mix`
  via a per-(scene, speaker) track split, one summed ambisonic timeline decoded
  once (SAD + max-rE, ±30° virtual pair). Optional ACN/SN3D order-3 master.
  Degrades to dry on missing library or render failure — never loses a line.
- M3 UI: Stage step with a top-down SVG stage, drag/keyboard actor placement,
  8 room presets, listener panel, per-actor dry blend, "Spread the cast" and
  per-scene audition through the real render path.

Two traps cost real time and are now written down: `ambi_roomsim_process` wants
per-channel pointer arrays (a flat buffer segfaults), and `LOUDSPEAKER_DECODER_ALLRAD`
returns a silent all-zero matrix for nLS=2 — SAD is the correct stereo decoder.
`maxTime_s = -0.05f` is an order-mode sentinel, not a reverb cap; the true bound
is `IMS_CIRC_BUFFER_LENGTH`, 0.68 s at 48 kHz.

334 tests green (44 new), web build clean, ruff delta zero on new files. Live
browser verification of the Stage step is still outstanding — no browser MCP is
configured in this workspace, so it was checked through the running API instead.
Packaging is the open end: no CI-built binaries or `installer/pins.json` entry
yet, so staging is currently build-it-yourself.

## [2026-11-13] lint | Full UI workflow verified in Firefox; three real bugs found

Drove the whole pipeline in the automation browser with the `fake`/`chatterbox`
engines (two `llama-server` processes held 8.6 GB of 16, so Breeze could not
fit): parse → Voices design → Stems per-stem voice → render → Cut → Stage →
Audition → Mix. AGENTS.md's "build + pytest is not enough" rule paid for
itself immediately — three defects were invisible to every test I had written.

1. **Stage rail button was inert.** I had added `stage` to `STEPS`, the union
   type and the `available` gate, but not to the rail's `onClick` dispatcher, so
   the button rendered enabled and did nothing. Only a click caught it.
2. **"Spread the cast" could not stage anything.** `patchScene` bailed when the
   scene had no entry yet, which is precisely the not-yet-staged case. Now it
   starts from a blank scene.
3. **Applying a room preset broke saving.** The listener is placed against the
   old depth; shrinking to the Kitchen preset (5 m → 3.5 m) left it outside and
   the PUT 422'd — surfaced as "[object Object]" because `api.ts` passed
   FastAPI's `{loc,msg}` array straight through. Fixed by `fitToRoom()` clamping
   on every scene patch, plus `describeDetail()` flattening the message.

Also verified: per-scene rooms hold (scene 1 Kitchen 4×3.5×2.6 while scene 2
keeps 6×5×3), keyboard nudging moves an actor 25 cm per press with the readout
following, audition produced a 48 kHz stereo file, and the mix reported
`spatial: true` with L/R correlation **0.3631** — a duplicated mono master would
be exactly 1.0 — true peak −1.54 dBFS, and ACN channel 0 (W omni) strongest.
Found and fixed a fourth issue: the Ambisonic master was 4.96 s against the
stereo 6.36 s; it is now padded to match (305280 frames both).

Practical notes for the next browser session: firefox-devtools is configured but
mcp-lazy keeps it disconnected (`tools.mcp.load` first), it has no console tools,
`saveTo` is sandboxed, `evaluate_script` has no `setTimeout`, and UIDs go stale
after any re-render. Scratch project deleted afterwards; 8 projects remain.

## [2026-11-13] ingest | Shipping the native room renderer

Closed the packaging gap that kept staging build-it-yourself.

`src/drama_studio/spatial/` cannot become a package: a directory named `spatial`
would shadow the `drama_studio.spatial` module next to it, so setuptools never
saw it and the shim sources reached neither the sdist nor the wheel — verified by
building both and inspecting them. `MANIFEST.in` fixes the sdist; the installer
payload already copies `src/` verbatim, and wheel users get a binary instead.

`installer.yml` gained a `spatial` job: it checks out SAF at the commit pinned as
`spatial.saf_commit`, builds, then gates the artifact on `spike_spatial.py` plus
`tests/test_spatial.py` so a binary that loads but renders wrong physics cannot
reach a release. It publishes a 476 KB tar with the ISC licence beside it, which
the existing release job uploads unchanged. Linux x86-64 only: an MSVC + OpenBLAS
build is real work, and a workflow that fails on every tag is worse than none.

`ensure_spatial()` fetches that asset into `<prefix>/lib` and `dlopen()`s it,
because a library that will not load is worse than no library — the mixer falls
back silently and the user hears a dry mix they asked not to get. Empty
`pins.json` assets mean "warn and skip", never a failed install. Discovery had a
latent bug fixed on the way: the app searched only the Linux default prefix, so an
installed renderer on Windows, macOS or a custom `DRAMA_LLAMA_PREFIX` would have
been invisible; `_install_prefixes()` now mirrors every installer prefix rule.

Deliberately not done: the SHA-256 stays empty until CI publishes the first real
asset. Any hash written now would be invented.

## [2026-11-13] ingest | Shipping the renderer, and four bugs a browser found

Packaging first: the native room renderer is now built by CI (`spatial` job in
`installer.yml`, SAF commit pinned in `pins.json`, gated on `spike_spatial.py`
and `tests/test_spatial.py` before an artefact is published) and fetched by the
installer into `<prefix>/lib`. The shim sources reached neither sdist nor wheel —
`src/drama_studio/spatial/` has no `__init__.py` on purpose, because a package of
that name would shadow the module beside it — so `MANIFEST.in` grafts them in.
`pins.json` ships an empty `assets` map: the SHA-256 has to come from a release
that exists, and inventing one is worse than none.

Then a Lem-style five-scene, four-character piece in the browser, Breeze with
emotional direction on every line. Four real defects fell out.

**The Ambisonic master was the wrong length and the wrong shape.** The writer
re-stitched the per-scene beds, appending them rather than placing them at their
offsets, so a tail that reached into the next scene pushed everything after it
late — and the frame counts still matched, which is why the first regression test
I wrote passed over it. Export now takes the summed ACN array the stereo decode
already uses and streams it in blocks; length and alignment are true by
construction, and the test correlates the two envelopes to keep it that way.

**Long jobs reverted the user's edits.** A voice direction typed while a design
ran was silently rolled back, because the job saved the project it had loaded
minutes earlier — project.json, one file, last writer wins. Three of four
directions disappeared in a single session. Jobs now replay only what they own.
The same class of bug was in `SfxLibrary`: two processes, one index, so the API
answered "sfx not found" about a Suno track the worker had just downloaded, and
the panel's own preview 404ed with it.

**A queue wedged permanently.** Stale-lease recovery ran once, at worker startup,
so a job orphaned by a restart sat `running` with an idle GPU and everything
behind it starved. Recovery is now periodic, which is only safe because the
worker refreshes the lease while a handler runs — a single Breeze take is longer
than the timeout, and without the keep-alive recovery would steal healthy renders.

**The auto-cut was 0.4–1.0 s short on nearly every line.** `align_cuts()` reports
where words are; the consonant decay sits past that, and the clip ended at the
boundary. Padding lives in our wrapper (the vendored module is another session's):
the cut follows a 20 ms envelope outward and stops when the signal stops, so
decay is kept and silence is not, and the allowance can never shorten a cut.
A relative floor alone was wrong — on a silent take the peak is zero, so digital
silence passes any percentage-of-peak test; there is an absolute term now.

Also: "Design from description" had no description. The field was only reachable
by PATCH, so a deterministic parse gave every character the same generic
narrator; the Voices panel now has the field, and designing persists it first and
uses the text on screen rather than stale React state.

New work, from a request to give staged sources sensible paths: layout now reads
the parentheticals the parser already keeps. Hatch, closer now, as he leaves,
pacing become enter/approach/leave/pace moves timed across the window in which
that speaker actually speaks — taken from alignments, so an unaligned project
gets positions and no paths. `render_scene` already interpolated motion on a
100 ms grid; only the producer was missing.

Known gap I did not close: `StageBoard` does not draw those paths yet, so motion
is audible and invisible. Nothing marks a mover on the board.

Numbers: 365 tests, up from 334 at the start of the branch. Linux-only renderer
artefact; Windows and macOS still build by hand. SFX and beds remain
non-spatial.

## [2026-11-13] ingest | A mix that played the whole play at once

Measured rather than assumed, this time. The five-scene test project mixed to
35.8 s; it is a 176 s piece. Every scene sat on top of every other scene from
t = 0.

The aligner wrote a cursor into `AlignedLine.position_s`. Inside a stem that
cursor is meaningful — it is where the next line of *this speaker's* performance
falls. But `position_s` is a slot on the project timeline, and every stem starts
its own clock at zero, so every scene's first line landed at the top of the mix.
`cuts.py` already documents the contract (`None` means derive script order) and
already carries the function that exists because "a per-stem cursor would stack
every stem at 0 and overlap". The aligner was simply the last producer still
doing it. `build_mix` only rescued `None`; a `0.0` looks like a placement someone
chose.

Fixed at both ends: the producers leave the field alone, and
`cuts.repair_per_stem_positions` clears numbers that cannot be placements (two
*different scenes* both opening at 0.0 — scenes follow one another) in
`ProjectStore.get`, beside the other legacy repairs, so the mix, the Cut board and
stage timing share one corrected view. After the fix: 175.9 s, silence at the four
scene boundaries, Ambisonic still frame-for-frame with stereo, envelope lag 0.00 s.

Staging looked healthy throughout, which is how this hid: speaker windows already
came from script order, so the motion timings were right inside a mix that was
wrong. A bug in the timeline is invisible to everything that measures relative
time.

Also in this session, on real takes: the auto-cut now stops at the *last* audible
hop inside its allowance rather than at the first dip — cutting on the dip left
0.1-0.2 s stranded, a smaller version of the clip the allowance exists to fix.
A/B on the same 23 s Breeze take: voiced coverage 97.8 % → 99.8 %, worst line
0.22 s → 0.04 s. And motion was confirmed in the *exported* Ambisonic file by
rendering the project twice, once with a scene's path removed: the difference
appears only inside that scene's walk window, 76.6-80.8 s.

## [2026-11-13] ingest | Second pass on a fresh project, measured absolutely

The repaired build was run over the whole workflow again as a new project, because
a read-time migration can make an old project sound right while the producer is
still wrong. Five scenes, four characters, Breeze with emotional direction on all
31 lines, one take per stem.

Parse: 31 lines, 31 stage directions carried, only the prose preamble unparsed.
Voices: four designed from descriptions. Stems: 17/17 rendered and aligned. The
stored file has 31 cuts and **none of them carries an explicit `position_s`** —
the producer now defers to script order, which is the contract.

Stage: five scenes, each in its own room, including the 1.4 m car that used to
return 500 from the layout endpoint. Motion came from the stage directions and
landed in the right places — MERIAN's entrance at 8.2 s rather than at the top of
the scene, VOGEL's exit at 40.6 s inside the car.

Mix: 179.2 s, zero warnings, stereo and Ambisonic frame-identical at 8,603,040
frames, envelope cross-correlation between the W channel and the stereo sum at
+0.00 s, all 16 spherical-harmonic channels active. With the Suno bed attached the
music measures 0.156 RMS before the first line and 0.037 under it — a 12.5 dB duck,
which is the cue system working rather than the bed merely being present.

Also found this pass, unrelated to staging: the Sound step's Suno picker never
opened. The bridge forwarded `limit=500`, the Suno API allows at most 100 and said
so, and the bridge turned that into a 502 that the UI showed as an empty panel. One
line on each side of the seam; verified live, 100 clips returned.

The pass-1 scratch project and an old probe project were deleted afterwards; the
second-pass project stays so the master can be listened to.
