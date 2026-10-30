from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..models import (
    ReferenceRequest,
    ReferenceResult,
    StemRenderRequest,
    StemRenderResult,
    VoiceMode,
)

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class EngineError(RuntimeError):
    pass


class EngineNotAvailable(EngineError):
    pass


class UnsupportedOperation(EngineError):
    pass


@dataclass(frozen=True)
class EngineInfo:
    name: str
    languages: frozenset[str]
    sample_rate: int
    supports_reference_clone: bool
    voice_mode: VoiceMode
    description: str = ""
    # True for engines (e.g. Suno) that infer language from the text/style
    # instead of a project-level language hint.
    language_agnostic: bool = False


@runtime_checkable
class TTSEngine(Protocol):
    info: EngineInfo

    def load(self) -> None: ...

    def unload(self) -> None: ...

    def available(self) -> bool:
        """Cheap, non-loading reachability check for status displays."""
        return True

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult: ...

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult: ...


class BaseEngine:
    """Shared bookkeeping. Adapters only implement what their model supports."""

    info: EngineInfo

    def __init__(self) -> None:
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def available(self) -> bool:
        return True

    def design_reference(self, request: ReferenceRequest) -> ReferenceResult:
        raise UnsupportedOperation(
            f"{self.info.name} does not support reference design; use preset voices"
        )

    def render_stem(
        self,
        request: StemRenderRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> StemRenderResult:
        raise NotImplementedError
