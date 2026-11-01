"""Resumable, checksum-verified HTTP downloads using only the stdlib."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .progress import DownloadProgress
from .util import (
    Cancelled,
    DownloadError,
    InstallerError,
    human_bytes,
    sha256_file,
    verify_file,
)

USER_AGENT = "DramaLlama-Installer/0.2 (+https://github.com/MrDoe/DramaLlama)"
RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


def hf_url(repo: str, revision: str, filename: str) -> str:
    quoted = "/".join(urllib.parse.quote(part) for part in filename.split("/"))
    return f"https://huggingface.co/{repo}/resolve/{revision}/{quoted}?download=true"


def download(
    url: str,
    destination: str | Path,
    *,
    sha256: str | None = None,
    size: int | None = None,
    label: str | None = None,
    progress: Callable[[DownloadProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    retries: int = 3,
    timeout: float = 60.0,
) -> Path:
    """Download ``url`` to ``destination``, resuming a previous ``.part`` file.

    A completed file with the pinned size and hash is left untouched, which
    makes every installer step idempotent. Hash mismatches restart the file;
    transient network errors keep the partial file so the next attempt resumes.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    title = label or target.name

    if verify_file(target, size=size, sha256=sha256, cancel=cancel):
        if progress is not None:
            total = int(size or target.stat().st_size)
            progress(DownloadProgress(title, total, total))
        return target

    attempts = max(1, int(retries))
    last_error: Exception | None = None
    for attempt in range(attempts):
        if cancel is not None and cancel():
            raise Cancelled("cancelled")
        try:
            _download_once(
                url,
                target,
                part,
                sha256=sha256,
                size=size,
                label=title,
                progress=progress,
                cancel=cancel,
                timeout=timeout,
            )
            return target
        except Cancelled:
            raise
        except DownloadError as exc:
            # Integrity failures delete the .part file, so a retry starts fresh.
            last_error = exc
            if attempt + 1 < attempts:
                _sleep(1.5 * (attempt + 1), cancel)
        except InstallerError:
            raise
        except RETRYABLE as exc:
            last_error = exc
            if attempt + 1 < attempts:
                _sleep(1.5 * (attempt + 1), cancel)
    detail = f": {last_error}" if last_error else ""
    raise DownloadError(f"could not download {title} after {attempts} attempts{detail}")


def _sleep(seconds: float, cancel: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel is not None and cancel():
            raise Cancelled("cancelled")
        time.sleep(0.1)


def _download_once(
    url: str,
    target: Path,
    part: Path,
    *,
    sha256: str | None,
    size: int | None,
    label: str,
    progress: Callable[[DownloadProgress], None] | None,
    cancel: Callable[[], bool] | None,
    timeout: float,
) -> None:
    resume = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if resume:
        headers["Range"] = f"bytes={resume}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and part.is_file():
            if verify_file(part, size=size, sha256=sha256, cancel=cancel):
                part.replace(target)
                return
            part.unlink(missing_ok=True)
        raise

    with response:
        status = getattr(response, "status", 200)
        if resume and status != 206:
            # Server ignored Range: start over instead of duplicating the prefix.
            resume = 0
        mode = "ab" if resume else "wb"
        length = int(response.headers.get("Content-Length") or 0)
        total = int(size or (resume + length))
        done = resume
        started = time.monotonic()
        last_report = 0.0
        with open(part, mode) as handle:
            while True:
                if cancel is not None and cancel():
                    raise Cancelled("cancelled")
                block = response.read(1024 * 1024)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                now = time.monotonic()
                if progress is not None and (now - last_report >= 0.25 or done == total):
                    elapsed = max(0.001, now - started)
                    speed = (done - resume) / elapsed
                    progress(DownloadProgress(label, done, total, speed))
                    last_report = now
        if progress is not None:
            progress(DownloadProgress(label, done, total or done))

    actual = part.stat().st_size
    if size is not None and actual != int(size):
        part.unlink(missing_ok=True)
        raise DownloadError(
            f"{label}: expected {human_bytes(size)}, got {human_bytes(actual)}"
        )
    if sha256:
        digest = sha256_file(part, cancel=cancel)
        if digest.lower() != sha256.lower():
            part.unlink(missing_ok=True)
            raise DownloadError(f"{label}: checksum mismatch (got {digest[:12]}…)")
    os.replace(part, target)


def download_text(url: str, *, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")
