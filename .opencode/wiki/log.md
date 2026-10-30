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
