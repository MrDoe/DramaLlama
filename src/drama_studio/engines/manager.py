from __future__ import annotations

from collections.abc import Callable

from .base import BaseEngine, EngineNotAvailable, TTSEngine
from .breeze import BreezeEngine
from .breeze_de import BreezeDeEngine
from .chatterbox import ChatterboxEngine
from .suno import SunoEngine
from .fake import FakeEngine

ENGINES: dict[str, Callable[[], TTSEngine]] = {
    "fake": FakeEngine,
    "breeze": BreezeEngine,
    "breeze-de": BreezeDeEngine,
    "chatterbox": ChatterboxEngine,
    "suno": SunoEngine,
}


class EngineManager:
    """One live engine per worker process. Breeze is unloaded before Chatterbox."""

    def __init__(self) -> None:
        self._engines: dict[str, BaseEngine] = {}

    def get(self, name: str) -> BaseEngine:
        engine = self._engines.get(name)
        if engine is None:
            # Breeze and the local models must never be resident together on a 16 GB card.
            for other in list(self._engines):
                if other != name:
                    self.unload(other)
            factory = ENGINES.get(name)
            if factory is None:
                raise EngineNotAvailable(f"unknown engine {name!r}")
            engine = factory()
            engine.load()
            self._engines[name] = engine
        return engine

    def unload(self, name: str) -> None:
        engine = self._engines.pop(name, None)
        if engine is not None:
            engine.unload()

    def unload_all(self) -> None:
        for name in list(self._engines):
            self.unload(name)
