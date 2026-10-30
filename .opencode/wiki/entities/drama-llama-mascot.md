---
title: Drama Llama mascot and rename
tags: [branding, mascot, png, ui, rename]
sourceRefs:
  - web/llogo.png
  - web/public/llogo.png
  - web/public/llogo-64.png
  - web/src/components/Shell.tsx
  - web/src/pages/ProjectsPage.tsx
  - web/src/styles.css
  - web/index.html
lastReviewed: 2026-10-30
---

# Drama Llama mascot and rename

The product, UI and CLI were renamed **Drama Studio → Drama Llama**. The avatar
is the supplied cartoon llama with a headset (`web/llogo.png`, 861×873 RGBA),
drawn on an orange rounded-square tile with transparent corners.

| Asset | Purpose |
|---|---|
| `web/llogo.png` | source master, kept outside `public/` (not bundled) |
| `web/public/llogo.png` | 512×512 optimized; header mark, studio-panel logo, apple-touch icon |
| `web/public/llogo-64.png` | 64×64 favicon |

Regenerate the served copies from the master (plain resize — the tile already
carries its own rounded corners and transparency, so never frame it inside
another orange/glowy card):

```bash
convert web/llogo.png -filter Lanczos -resize 512x512 -background none \
  -gravity center -extent 512x512 -strip -define png:compression-level=9 \
  web/public/llogo.png
convert web/public/llogo.png -filter Lanczos -resize 64x64 -strip \
  -define png:compression-level=9 web/public/llogo-64.png
```

`#151617` fills the square padding so it blends into the dark UI. The palette
is the logo's own orange (`#ED6A0F`), cream fleece and ink, which already
matches the app accent (`#f97316`).

## Usage

- `web/index.html` sets `<title>Drama Llama</title>`, the PNG favicon and
  `apple-touch-icon`.
- `Shell.tsx` renders `llogo.png` in `.brand-mark` (40 px, neutral border,
  soft shadow — no orange glow).
- `ProjectsPage.tsx` shows it at 64 px in `.studio-head`, the header of the
  quiet `.studio-panel` card whose engine rows (LLM, Breeze, Chatterbox,
  Whisper, Suno) render as borderless dividers with per-row `tone-*` icon
  accents. The retired `.hero-mascot` sticker card double-framed the logo's
  own orange tile and read as cheap.
- Hero eyebrow is "The audio drama lab”; the “Scripts in. Drama out.”
  headline carries the left column.
- `start-ui.sh` watches `web/public` when deciding whether to rebuild.

Earlier hand-authored SVG drafts were retired once the supplied PNG arrived;
the PNG is the single source of truth.

## Rename boundaries

Deliberately unchanged: the Python import package `drama_studio`, the checkout
directory `~/code/drama-studio`, and the legacy `drama-studio` CLI alias. The
`DRAMA_STUDIO_*` environment variables still work as fallbacks behind the new
`DRAMA_LLAMA_*` names (see `config.py`).

## Related

- [[entities/drama-llama-app]]
- [[entities/start-ui-launcher]]
