from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import DATA_DIR, SFX_LIBRARY_ROOTS

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aiff", ".aif"}
DEFAULT_EXCLUDES = {"juli", "miller", "projects", "snippets"}


class SfxItem(BaseModel):
    id: str
    name: str
    path: str
    folder: str
    duration_s: float
    sample_rate: int
    channels: int
    size_bytes: int
    mtime: float
    tags: list[str]


class SfxLibrary:
    """Index a local folder of sound effects without copying the files."""

    def __init__(
        self,
        roots: list[Path] | None = None,
        cache_path: Path | None = None,
        excludes: set[str] | None = None,
        upload_root: Path | None = None,
    ) -> None:
        self.roots = roots or SFX_LIBRARY_ROOTS
        self.cache_path = cache_path or (DATA_DIR / "sfx_index.json")
        self.excludes = {item.lower() for item in (excludes or DEFAULT_EXCLUDES)}
        self.upload_root = upload_root or (DATA_DIR / "sfx")
        self._lock = threading.RLock()
        self._items: dict[str, SfxItem] = {}
        self._loaded = False

    def scan(self, force: bool = False) -> int:
        with self._lock:
            cached = {} if force else self._load_cache()
            items: dict[str, SfxItem] = {}
            for root in self.roots:
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    if any(part.lower() in self.excludes for part in path.parts):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    sfx_id = _item_id(path)
                    old = cached.get(sfx_id)
                    if (
                        old
                        and abs(float(old.get("mtime", 0)) - stat.st_mtime) < 1
                        and int(old.get("size_bytes", -1)) == stat.st_size
                    ):
                        items[sfx_id] = SfxItem.model_validate(old)
                        continue
                    meta = _probe(path)
                    name = path.stem
                    folder = path.parent.name
                    items[sfx_id] = SfxItem(
                        id=sfx_id,
                        name=name,
                        path=str(path),
                        folder=folder,
                        duration_s=meta.get("duration_s", 0.0),
                        sample_rate=meta.get("sample_rate", 0),
                        channels=meta.get("channels", 0),
                        size_bytes=stat.st_size,
                        mtime=stat.st_mtime,
                        tags=_tags(name, folder),
                    )
            self._items = items
            self._loaded = True
            self._save_cache()
            return len(items)

    def search(self, query: str = "", limit: int = 50) -> list[SfxItem]:
        if not self._loaded:
            self.scan()
        items = list(self._items.values())
        tokens = [token for token in re.split(r"[^a-z0-9]+", query.lower()) if token]
        if tokens:
            scored: list[tuple[int, SfxItem]] = []
            for item in items:
                name_tokens = set(re.split(r"[^a-z0-9]+", item.name.lower()))
                folder_tokens = set(re.split(r"[^a-z0-9]+", item.folder.lower()))
                tag_tokens = set(item.tags)
                score = 0
                for token in tokens:
                    if token in name_tokens:
                        score += 6
                    elif token in folder_tokens:
                        score += 3
                    elif token in tag_tokens:
                        score += 2
                    else:
                        score -= 4
                if score > 0:
                    scored.append((score, item))
            scored.sort(key=lambda pair: (-pair[0], pair[1].name.lower()))
            items = [item for _, item in scored]
        else:
            items.sort(key=lambda item: (item.folder.lower(), item.name.lower()))
        return items[: max(1, min(200, limit))]

    def get(self, sfx_id: str) -> SfxItem | None:
        if not self._loaded:
            self.scan()
        return self._items.get(sfx_id)

    def find_by_path(self, path: str | Path) -> SfxItem | None:
        if not self._loaded:
            self.scan()
        target = Path(path).resolve()
        return next(
            (item for item in self._items.values() if Path(item.path).resolve() == target),
            None,
        )

    def audio_path(self, sfx_id: str) -> Path | None:
        item = self.get(sfx_id)
        if item is None:
            return None
        path = Path(item.path).resolve()
        for root in self.roots:
            try:
                path.relative_to(root.resolve())
                return path
            except ValueError:
                continue
        return None

    def add_upload(self, source: str | Path, filename: str | None = None) -> SfxItem:
        destination_root = self.upload_root
        destination_root.mkdir(parents=True, exist_ok=True)
        name = Path(filename or Path(source).name).name
        destination = destination_root / name
        if destination.exists():
            destination = destination_root / f"{destination.stem}-{_item_id(destination)}{destination.suffix}"
        shutil.copy2(source, destination)
        self.scan()
        item = self.get(_item_id(destination))
        if item is None:
            raise RuntimeError("uploaded SFX could not be indexed")
        return item

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data.get("items", {})
        except Exception:
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": {key: item.model_dump() for key, item in self._items.items()}}
        self.cache_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )


def _item_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:14]


def _tags(name: str, folder: str) -> list[str]:
    text = f"{name} {folder}".lower()
    return sorted(
        {
            token
            for token in re.split(r"[^a-z0-9]+", text)
            if len(token) > 1 and token not in {"the", "and", "with", "sound", "sfx"}
        }
    )


def _probe(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".wav":
        try:
            import soundfile as sf

            info = sf.info(str(path))
            return {
                "duration_s": round(info.frames / info.samplerate, 3),
                "sample_rate": int(info.samplerate),
                "channels": int(info.channels),
            }
        except Exception:
            pass

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {
            "duration_s": float((data.get("format") or {}).get("duration", 0.0)),
            "sample_rate": int(stream.get("sample_rate", 0) or 0),
            "channels": int(stream.get("channels", 0) or 0),
        }
    except Exception:
        return {"duration_s": 0.0, "sample_rate": 0, "channels": 0}
