"""Qt worker objects. The installer core never imports this module."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ..core.installer import InstallOutcome, run_install
from ..core.paths import InstallPaths
from ..core.plan import Selection
from ..core.progress import Progress
from ..core.uninstall import uninstall


class CallableWorker(QObject):
    """Run one callable on a worker thread and emit its result."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self._function = function

    @Slot()
    def run(self) -> None:
        try:
            self.done.emit(self._function())
        except Exception as exc:
            self.failed.emit(str(exc))


class InstallWorker(QObject):
    update = Signal(str, float, str)
    log = Signal(str)
    finished = Signal(object)

    def __init__(
        self,
        selection: Selection,
        pins: dict,
        system: Any,
        *,
        repair: bool = False,
    ) -> None:
        super().__init__()
        self._selection = selection
        self._pins = pins
        self._system = system
        self._repair = repair
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        # Called directly from the GUI thread; a queued slot would never run
        # because this worker has no event loop while run_install() blocks.
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        progress = Progress(
            on_update=lambda step, fraction, message: self.update.emit(
                step, fraction, message
            ),
            on_log=self.log.emit,
            cancel_event=self.cancel_event,
        )
        try:
            outcome = run_install(
                self._selection,
                self._pins,
                self._system,
                progress,
                repair=self._repair,
            )
        except Exception as exc:
            outcome = InstallOutcome(ok=False, error=str(exc))
        self.finished.emit(outcome)


class UninstallWorker(QObject):
    update = Signal(str, float, str)
    log = Signal(str)
    finished = Signal(object)

    def __init__(
        self,
        paths: InstallPaths,
        *,
        keep_data: bool,
        purge_models: bool,
    ) -> None:
        super().__init__()
        self._paths = paths
        self._keep_data = keep_data
        self._purge_models = purge_models
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        progress = Progress(
            on_update=lambda step, fraction, message: self.update.emit(
                step, fraction, message
            ),
            on_log=self.log.emit,
            cancel_event=self.cancel_event,
        )
        try:
            removed = uninstall(
                self._paths,
                keep_data=self._keep_data,
                purge_models=self._purge_models,
                progress=progress,
            )
            outcome = {"ok": True, "removed": removed}
        except Exception as exc:
            outcome = {"ok": False, "error": str(exc)}
        self.finished.emit(outcome)
