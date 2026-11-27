---
title: Release and version bump flow
tags: [release, versioning, installer, ci, tags]
sourceRefs:
  - pyproject.toml
  - installer/__init__.py
  - installer/packaging/inno/DramaLlama.iss
  - src/drama_studio/__init__.py
  - web/package.json
  - web/package-lock.json
  - .github/workflows/installer.yml
lastReviewed: 2026-11-21
---

# Release and version bump flow

A patch release changes the version in **seven files**, then the tag does the
rest. Missing one file leaves the wizard, the API, the CLI and the web UI
reporting different versions, which is how a "released" build can still look
like the previous one.

## The seven files

1. `INSTALL.md` — download URLs and checksum filenames.
2. `installer/__init__.py` — `INSTALLER_VERSION`.
3. `installer/packaging/inno/DramaLlama.iss` — the `iscc` comment/version.
4. `pyproject.toml` — package version.
5. `src/drama_studio/__init__.py` — `__version__` reported by `/api/health`.
6. `web/package.json` — root `version`.
7. `web/package-lock.json` — the root and `packages[""]` version entries only.

Do **not** touch the `@jridgewell` (or other dependency) pins in
`package-lock.json` while bumping; those are upstream resolutions, not the app
version.

## Commit and tag order

- Land the feature/fix commit first.
- Make a separate `chore: release X.Y.Z` commit with the seven edits.
- Create an **annotated** tag `vX.Y.Z` on that chore commit.
- Push the branch **and the tag**. The tag push triggers the Installers CI,
  which builds the AppImage and Windows Setup and uploads them to the GitHub
  release.
- The wiki `log.md` entry goes in a later `docs(wiki)` commit, after tagging,
  so the tag points at a clean release commit.

Re-tag only to rebuild a release — move the annotated tag, force-push it, and
the workflow replaces the assets. That is how the 0.3.0 Whisper and Components
fixes were published.

## Pinned spatial asset

Since 0.3.9 the release also carries the spatial renderer tarball and its
`.sha256`. CI builds it from `spatial.saf_commit`, never from a moving tag;
`ensure_spatial()` downloads it only when `installer/pins.json` has both a URL
and a hash for the platform. An empty `assets` map is a warning, not a failed
install — a dry mix without staging is supported. See
[[entities/installer]] and [[concepts/spatial-stage-rendering]].

## Related

- [[entities/installer]]
- [[concepts/gpu-optional-install]]
