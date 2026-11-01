"""Populate the exact faster-whisper cache layout the vendored aligner expects."""

from __future__ import annotations

from pathlib import Path

from .download import download, hf_url
from .paths import InstallPaths, whisper_cache_root
from .progress import Progress
from .util import InstallerError, human_bytes, verify_file, write_text


def whisper_snapshot(pins: dict, home: Path | None = None) -> Path:
    meta = pins["whisper"]
    return (
        whisper_cache_root(home)
        / str(meta["cache_name"])
        / "snapshots"
        / str(meta["revision"])
    )


def whisper_model_dir(pins: dict, home: Path | None = None) -> Path:
    return whisper_snapshot(pins, home)


def install_whisper(
    paths: InstallPaths,
    pins: dict,
    progress: Progress,
    *,
    home: Path | None = None,
) -> tuple[Path, bool]:
    """Populate the cache and report whether the installer downloaded anything."""
    meta = pins["whisper"]
    snapshot = whisper_snapshot(pins, home)
    snapshot.mkdir(parents=True, exist_ok=True)
    total = int(meta.get("size", 0)) or sum(
        int(item.get("size", 0)) for item in meta.get("files", {}).values()
    )
    files = list(meta.get("files", {}).items())
    done_before = 0
    downloaded = False
    progress.update("whisper", 0.01, f"Checking Whisper large-v3 ({human_bytes(total)})…")
    for name, item in files:
        size = int(item.get("size", 0)) or None
        sha = item.get("sha256")
        target = snapshot / name
        if verify_file(target, size=size, sha256=sha, cancel=progress.cancelled):
            done_before += size or 0
            continue

        def _report(update, *, base=done_before, item_size=size or 0, name=name) -> None:
            done = base + (min(update.done, item_size) if item_size else update.done)
            fraction = (done / total) if total else 0.0
            progress.update("whisper", fraction, f"Whisper: {name}")

        downloaded = True
        download(
            hf_url(str(meta["repo"]), str(meta["revision"]), name),
            target,
            sha256=str(sha) if sha else None,
            size=size,
            label=f"Whisper {name}",
            progress=_report,
            cancel=progress.cancelled,
            retries=4,
        )
        done_before += size or target.stat().st_size

    missing = [
        name
        for name, item in files.items()
        if not verify_file(
            snapshot / name,
            size=int(item.get("size", 0)) or None,
            sha256=item.get("sha256"),
            cancel=progress.cancelled,
        )
    ]
    if missing:
        raise InstallerError(f"Whisper cache is incomplete: {', '.join(missing)}")
    refs = whisper_cache_root(home) / str(meta["cache_name"]) / "refs" / "main"
    write_text(refs, str(meta["revision"]) + "\n")
    progress.update("whisper", 1.0, f"Whisper large-v3 ready at {snapshot}")
    return snapshot, downloaded


def whisper_summary(pins: dict, home: Path | None = None) -> str:
    snapshot = whisper_snapshot(pins, home)
    if not snapshot.is_dir():
        return "not installed"
    present = sum(1 for path in snapshot.rglob("*") if path.is_file())
    return f"{present} file(s) at {snapshot}"
