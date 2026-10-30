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
