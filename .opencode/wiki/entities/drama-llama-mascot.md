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
is the supplied cartoon llama with a headset (`web/llogo.png`, 1057×992), drawn
on an orange rounded-square tile.

| Asset | Purpose |
|---|---|
| `web/llogo.png` | source master, kept outside `public/` (not bundled) |
| `web/public/llogo.png` | 512×512 optimized; header mark, hero mascot, apple-touch icon |
| `web/public/llogo-64.png` | 64×64 favicon |

The master has uneven dark margins around the orange tile, so it is cropped
before scaling:

```bash
convert web/llogo.png -crop 870x890+105+32 +repage \
  -filter Lanczos -resize 512x512 \
  -background '#151617' -gravity center -extent 512x512 \
  -strip -define png:compression-level=9 web/public/llogo.png
convert web/public/llogo.png -filter Lanczos -resize 64x64 -strip \
  -define png:compression-level=9 web/public/llogo-64.png
```

`#151617` fills the square padding so it blends into the dark UI. The palette
is the logo's own orange (`#ED6A0F`), cream fleece and ink, which already
matches the app accent (`#f97316`).

## Usage

- `web/index.html` sets `<title>Drama Llama</title>`, the PNG favicon and
  `apple-touch-icon`.
- `Shell.tsx` renders `llogo.png` in `.brand-mark` (38 px, transparent
  background, clipped corners).
- `ProjectsPage.tsx` shows it in `.hero-mascot` above the engine cards, with
  the eyebrow “Drama Llama · local audio drama lab”.
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
