"""Progress, logging and cancellation plumbing shared by the CLI and the wizard."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .util import Cancelled


@dataclass
class DownloadProgress:
    label: str
    done: int
    total: int
    speed_bps: float = 0.0

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.done / self.total))


@dataclass
class Progress:
    """Callback bundle passed down through every installer step.

    The callbacks are intentionally plain functions so the core never imports
    Qt. The wizard adapts them to signals; the CLI prints them.
    """

    on_update: Callable[[str, float, str], None] | None = None
    on_log: Callable[[str], None] | None = None
    on_download: Callable[[str, DownloadProgress], None] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def update(self, step: str, fraction: float, message: str = "") -> None:
        self.check_cancelled()
        if self.on_update is not None:
            self.on_update(step, max(0.0, min(1.0, fraction)), message)

    def log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)

    def download(self, step: str, item: DownloadProgress) -> None:
        self.check_cancelled()
        if self.on_download is not None:
            self.on_download(step, item)

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled("installation cancelled")

    def cancel(self) -> None:
        self.cancel_event.set()


class NullProgress(Progress):
    """Progress that only enforces cancellation (used by tests)."""


def combine_steps(
    steps: list[tuple[str, float]],
    *,
    on_overall: Callable[[float], None] | None = None,
) -> Progress:
    """Build callbacks that map per-step progress onto one overall fraction."""
    weights = {step: max(0.0, weight) for step, weight in steps}
    total = sum(weights.values()) or 1.0
    current: dict[str, float] = {step: 0.0 for step, _ in steps}

    def update(step: str, fraction: float, message: str) -> None:
        current[step] = max(0.0, min(1.0, fraction))
        if on_overall is not None:
            overall = sum(weights.get(key, 0.0) * value for key, value in current.items())
            on_overall(overall / total)

    return Progress(on_update=update)
