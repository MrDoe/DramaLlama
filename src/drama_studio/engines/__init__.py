from __future__ import annotations

from .base import EngineInfo, TTSEngine
from .manager import ENGINES


def create_engine(name: str) -> TTSEngine:
    try:
        factory = ENGINES[name]
    except KeyError as exc:
        raise ValueError(f"unknown engine {name!r}; choose from {sorted(ENGINES)}") from exc
    engine = factory()
    engine.load()
    return engine


def engine_info(name: str) -> EngineInfo:
    factory = ENGINES.get(name)
    if factory is None:
        raise ValueError(f"unknown engine {name!r}; choose from {sorted(ENGINES)}")
    return factory.info


def all_engine_info() -> list[EngineInfo]:
    return [factory.info for factory in ENGINES.values()]
