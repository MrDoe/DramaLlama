"""The PySide6 installer wizard.

Qt is imported only here (and in :mod:`installer.gui.worker`), which keeps the
installer core importable and testable on machines without PySide6.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QThread, QTimer
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..core.detect import SystemInfo, detect_system, summary_lines
from ..core.paths import InstallPaths, load_pins, payload_root
from ..core.plan import (
    BREEZE_DE,
    BREEZE_EN,
    CHATTERBOX,
    CORE,
    Selection,
    build_steps,
    download_estimate,
    effective_device,
    installed_estimate,
)
from ..core.state import installed_prefix, load_state
from ..core.util import InstallerError, human_bytes, is_windows
from .worker import CallableWorker, InstallWorker, UninstallWorker

PAGE_WELCOME = 1
PAGE_DETECT = 2
PAGE_COMPONENTS = 3
PAGE_LOCATION = 4
PAGE_LICENSE = 5
PAGE_PROGRESS = 6
PAGE_FINISH = 7
PAGE_UNINSTALL = 8

LICENSE_TEXT = """\
Drama Llama bundles and downloads several third-party components. The core app
and most dependencies are open source, but the Breeze voices are released for
research and non-commercial use only.

Breeze English — Breeze TTS 2 (BreezeBlue/Breeze-TTS-2)
  Model weights are released under a research/non-commercial licence. Read the
  full licence before use: https://huggingface.co/BreezeBlue/Breeze-TTS-2
  The inference code (breezeblue-ai/breeze-tts) is Apache-2.0.

Breeze German — Breeze-TTS-2.cpp + Articulatio-DE
  The sidecar binary and the Articulatio-DE Q8_0 GGUF are research releases.
  Check the upstream repositories before any commercial use.

Chatterbox
  Chatterbox Multilingual and the chatterbox-tts-api Docker image carry their
  own licences. The image is pulled from Docker Hub only if you select it.

Drama Llama itself is provided as-is, without warranty. Third-party notices
are written to NOTICE.md inside the install directory.
"""


def _payload_icon() -> QIcon | None:
    try:
        path = payload_root() / "web" / "public" / "llogo.png"
    except (OSError, FileNotFoundError):
        return None
    if path.is_file():
        return QIcon(str(path))
    return None


def _heading(text: str, subtitle: str = "") -> QVBoxLayout:
    layout = QVBoxLayout()
    title = QLabel(text)
    font = title.font()
    font.setPointSize(max(14, font.pointSize() + 4))
    font.setBold(True)
    title.setFont(font)
    title.setWordWrap(True)
    layout.addWidget(title)
    if subtitle:
        subtitle_label = QLabel(subtitle)
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)
    return layout


def _mono() -> QPlainTextEdit:
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return view


class WelcomePage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Welcome")
        layout = QVBoxLayout(self)
        layout.addLayout(
            _heading(
                "Install Drama Llama",
                "A local-first audio drama studio. This wizard installs a "
                "self-contained, per-user copy and can add the Breeze and "
                "Chatterbox voice engines.",
            )
        )
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.choice = QButtonGroup(self)
        self.install_radio = QRadioButton("Install or update Drama Llama")
        self.choice.addButton(self.install_radio, 1)
        layout.addWidget(self.install_radio)
        self.repair_radio = QRadioButton("Repair the existing installation")
        self.choice.addButton(self.repair_radio, 2)
        layout.addWidget(self.repair_radio)
        self.uninstall_radio = QRadioButton("Uninstall Drama Llama")
        self.choice.addButton(self.uninstall_radio, 3)
        layout.addWidget(self.uninstall_radio)
        self.install_radio.setChecked(True)

        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch(1)

        details = QLabel(
            "Nothing is installed system-wide. The app lives in your user data "
            "directory and projects are stored inside it. Administrator rights "
            "are only requested if you ask the wizard to install Docker for "
            "Chatterbox, or the Vulkan loader for Breeze German."
        )
        details.setWordWrap(True)
        layout.addWidget(details)

    def initializePage(self) -> None:
        state = self.wizard_ref.install_state()
        has_install = state is not None
        self.repair_radio.setVisible(has_install)
        self.uninstall_radio.setVisible(has_install)
        self.summary.setText(
            f"Existing install found at <code>{self.wizard_ref.installed_prefix()}</code>"
            if has_install
            else "No existing Drama Llama installation was found."
        )
        if has_install:
            self.install_radio.setText("Update or reinstall Drama Llama")
        self.note.setText(
            "Updating is safe: the wizard re-checks files, keeps your projects "
            "and only re-downloads what is missing."
        )
        self.choice.setExclusive(True)

    def mode(self) -> str:
        if self.uninstall_radio.isChecked():
            return "uninstall"
        if self.repair_radio.isChecked():
            return "repair"
        return "install"


class DetectPage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("This machine")
        layout = QVBoxLayout(self)
        layout.addLayout(
            _heading(
                "Check what the installer found",
                "The installer picks the right PyTorch build and warns about "
                "missing GPU or service prerequisites.",
            )
        )
        self.table = QFormLayout()
        self.labels: dict[str, QLabel] = {}
        for key, _value in summary_lines(_placeholder_system()):
            label = QLabel("detecting…")
            label.setWordWrap(True)
            self.labels[key] = label
            self.table.addRow(f"{key}:", label)
        layout.addLayout(self.table)

        device_box = QGroupBox("GPU acceleration")
        device_layout = QVBoxLayout(device_box)
        self.auto_radio = QRadioButton("Automatic (use the GPU when one is available)")
        self.gpu_radio = QRadioButton("Force GPU (CUDA wheels)")
        self.cpu_radio = QRadioButton("CPU only")
        self.devices = QButtonGroup(self)
        for index, radio in enumerate((self.auto_radio, self.gpu_radio, self.cpu_radio)):
            self.devices.addButton(radio, index)
            device_layout.addWidget(radio)
        self.auto_radio.setChecked(True)
        layout.addWidget(device_box)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)

        row = QHBoxLayout()
        self.refresh_button = QPushButton("Detect again")
        self.refresh_button.clicked.connect(self.detect)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self._thread: QThread | None = None
        self._worker: CallableWorker | None = None
        self._detected = False

    def initializePage(self) -> None:
        requested = self.wizard_ref.requested_device
        if requested == "gpu":
            self.gpu_radio.setChecked(True)
        elif requested == "cpu":
            self.cpu_radio.setChecked(True)
        if self._detected:
            self._apply_device_state()
        else:
            self.detect()

    def detect(self) -> None:
        if self._thread is not None:
            return
        self.refresh_button.setEnabled(False)
        self.warning.setText("Detecting hardware and services…")
        self._thread = QThread(self)
        self._worker = CallableWorker(lambda: detect_system(self.wizard_ref.args.prefix))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._detected_system)
        self._worker.failed.connect(self._detection_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_done)
        self._thread.start()

    def _thread_done(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self.refresh_button.setEnabled(True)

    def _detected_system(self, system: SystemInfo) -> None:
        self.wizard_ref.system = system
        for key, value in summary_lines(system):
            self.labels.get(key, QLabel()).setText(value)
        self._detected = True
        self._apply_device_state()
        self.completeChanged.emit()

    def _apply_device_state(self) -> None:
        system = self.wizard_ref.system
        if system is None:
            return
        self.gpu_radio.setEnabled(system.has_gpu)
        if not system.has_gpu:
            self.auto_radio.setText("No NVIDIA GPU detected — CPU only")
            if self.gpu_radio.isChecked():
                self.auto_radio.setChecked(True)
        else:
            self.auto_radio.setText("Automatic (use the GPU when one is available)")
        notes = []
        if not system.has_gpu:
            notes.append("No NVIDIA GPU: voices will run on CPU where supported.")
        if not system.vulkan:
            notes.append("Vulkan is not available; Breeze German will use its CPU backend.")
        if system.docker is None:
            notes.append("Docker is not installed; Chatterbox needs it.")
        elif not system.docker_daemon:
            notes.append("Docker is installed but not running; start it before using Chatterbox.")
        self.warning.setText(" ".join(notes))

    def _detection_failed(self, message: str) -> None:
        self.warning.setText(f"Detection failed: {message}")
        self.wizard_ref.system = _placeholder_system()
        self._detected = True
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._detected

    def selected_device(self) -> str:
        if self.gpu_radio.isChecked():
            return "gpu"
        if self.cpu_radio.isChecked():
            return "cpu"
        return "auto"


def _placeholder_system() -> SystemInfo:
    return SystemInfo(
        os_name="windows" if is_windows() else "linux",
        arch="x86_64",
        platform_key="",
        cpu="",
        ram_gb=0,
        disk_free_gb=0,
    )


class ComponentsPage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Components")
        layout = QVBoxLayout(self)
        layout.addLayout(
            _heading(
                "Choose the optional voice engines",
                "The core is always installed. Everything here can be added "
                "later by running the installer again.",
            )
        )
        self.core_box = QGroupBox("Drama Llama core (required)")
        core_layout = QVBoxLayout(self.core_box)
        core_label = QLabel(
            "App, web UI, Python and Node runtimes, PyTorch, Whisper large-v3 "
            "alignment, ffmpeg and the Suno bridge."
        )
        core_label.setWordWrap(True)
        core_layout.addWidget(core_label)
        layout.addWidget(self.core_box)

        self.breeze_en = self._option(
            layout,
            "Breeze English (Breeze TTS 2)",
            "Voice design and reference cloning for English. Downloads the "
            "7.2 GiB checkpoint. Needs CUDA for practical speeds; CPU works "
            "but is very slow.",
        )
        self.breeze_de = self._option(
            layout,
            "Breeze German (Articulatio-DE)",
            "German Breeze sidecar with the Articulatio-DE Q8_0 GGUF. Runs on "
            "Vulkan, or on CPU when no Vulkan loader is available.",
        )
        self.chatterbox = self._option(
            layout,
            "Chatterbox (Docker)",
            "Multilingual voice cloning in Docker. Uses the NVIDIA image when "
            "CUDA is available, otherwise the smaller CPU image.",
        )

        self.allow_docker = QCheckBox(
            "Allow the wizard to request administrator permission to install Docker"
        )
        self.allow_docker.setVisible(False)
        layout.addWidget(self.allow_docker)

        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        layout.addWidget(self.estimate)
        layout.addStretch(1)

        for box in (self.breeze_en, self.breeze_de, self.chatterbox):
            box.toggled.connect(self.update_estimate)
        self.allow_docker.toggled.connect(self.update_estimate)

    def _option(self, layout: QVBoxLayout, title: str, description: str) -> QCheckBox:
        box = QGroupBox(title)
        inner = QVBoxLayout(box)
        label = QLabel(description)
        label.setWordWrap(True)
        inner.addWidget(label)
        checkbox = QCheckBox("Install this component")
        inner.addWidget(checkbox)
        layout.addWidget(box)
        return checkbox

    def initializePage(self) -> None:
        state = self.wizard_ref.install_state() or {}
        installed = {
            name
            for name, meta in state.get("components", {}).items()
            if meta.get("status") in {"installed", "failed", "pending_reboot"}
        }
        requested = self.wizard_ref.requested_components
        self.breeze_en.setChecked(BREEZE_EN in (requested or installed))
        self.breeze_de.setChecked(BREEZE_DE in (requested or installed))
        self.chatterbox.setChecked(CHATTERBOX in (requested or installed))
        self.allow_docker.setChecked(bool(self.wizard_ref.args.allow_docker_install))
        self.update_estimate()

    def selected_components(self) -> list[str]:
        components = [CORE]
        for name, box in (
            (BREEZE_EN, self.breeze_en),
            (BREEZE_DE, self.breeze_de),
            (CHATTERBOX, self.chatterbox),
        ):
            if box.isChecked():
                components.append(name)
        return components

    def has_breeze(self) -> bool:
        return self.breeze_en.isChecked() or self.breeze_de.isChecked()

    def update_estimate(self) -> None:
        self.allow_docker.setVisible(self.chatterbox.isChecked() and not (
            self.wizard_ref.system and self.wizard_ref.system.docker_daemon
        ))
        selection = self.wizard_ref.selection()
        system = self.wizard_ref.system
        if system is None:
            return
        try:
            device = effective_device(selection, system)
            download = download_estimate(selection, self.wizard_ref.pins, system)
            installed = installed_estimate(selection, self.wizard_ref.pins, system)
        except InstallerError as exc:
            self.estimate.setText(f"<b>{exc}</b>")
            return
        parts = [
            f"<b>{device.upper()}</b> install",
            f"download about <b>{human_bytes(download)}</b>",
            f"on disk about <b>{human_bytes(installed)}</b>",
        ]
        if system.disk_free_gb:
            parts.append(f"{system.disk_free_gb:.1f} GiB free")
        if device == "cpu" and self.breeze_en.isChecked():
            parts.append(
                "<span style='color:#b45309'>Breeze English on CPU is very slow.</span>"
            )
        self.estimate.setText(" · ".join(parts))

    def isComplete(self) -> bool:
        return True


class LocationPage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Install location")
        layout = QVBoxLayout(self)
        layout.addLayout(
            _heading(
                "Where should Drama Llama live?",
                "Use a local disk with several gigabytes free. The default is a "
                "per-user directory that needs no administrator rights.",
            )
        )

        form = QFormLayout()
        prefix_row = QHBoxLayout()
        self.prefix_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_prefix)
        prefix_row.addWidget(self.prefix_edit, 1)
        prefix_row.addWidget(browse)
        form.addRow("Install folder:", prefix_row)

        data_row = QHBoxLayout()
        self.data_edit = QLineEdit()
        self.data_edit.setPlaceholderText("default: <install folder>/data")
        data_browse = QPushButton("Browse…")
        data_browse.clicked.connect(self._choose_data)
        data_row.addWidget(self.data_edit, 1)
        data_row.addWidget(data_browse)
        form.addRow("Projects folder:", data_row)
        layout.addLayout(form)

        self.disk = QLabel()
        self.disk.setWordWrap(True)
        layout.addWidget(self.disk)
        layout.addStretch(1)

        note = QLabel(
            "Projects live in the projects folder. The rest of the install can be "
            "removed without losing them."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.prefix_edit.textChanged.connect(self._update_disk)

    def initializePage(self) -> None:
        default = installed_prefix(self.wizard_ref.args.prefix)
        if not self.prefix_edit.text().strip():
            self.prefix_edit.setText(str(default.prefix))
        state = self.wizard_ref.install_state() or {}
        if state.get("data_dir") and not self.data_edit.text().strip():
            self.data_edit.setText(str(state["data_dir"]))
        self._update_disk()

    def _choose_prefix(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the install folder", self.prefix_edit.text() or str(Path.home())
        )
        if chosen:
            self.prefix_edit.setText(chosen)

    def _choose_data(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the projects folder", self.data_edit.text() or str(Path.home())
        )
        if chosen:
            self.data_edit.setText(chosen)

    def _update_disk(self) -> None:
        prefix = Path(self.prefix_edit.text().strip() or str(Path.home())).expanduser()
        candidate = prefix
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        try:
            free = shutil.disk_usage(candidate).free
            self.disk.setText(f"{human_bytes(free)} free at {candidate}")
        except OSError:
            self.disk.setText("Could not read free disk space for that folder.")

    def prefix(self) -> Path:
        return Path(self.prefix_edit.text().strip() or str(Path.home())).expanduser()

    def data_dir(self) -> Path | None:
        value = self.data_edit.text().strip()
        return Path(value).expanduser() if value else None

    def validatePage(self) -> bool:
        prefix = self.prefix()
        if not prefix.name:
            QMessageBox.warning(self, "Invalid folder", "Choose a folder for the install.")
            return False
        data = self.data_dir()
        if data is not None and data == prefix:
            QMessageBox.warning(
                self, "Invalid projects folder", "The projects folder must not be the install root."
            )
            return False
        existing = load_state(prefix)
        if existing is None and prefix.exists() and any(prefix.iterdir()):
            answer = QMessageBox.question(
                self,
                "Folder is not empty",
                f"{prefix} already contains files. Installing here will not delete "
                "them, but files with the same names may be replaced.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        selection = self.wizard_ref.selection()
        system = self.wizard_ref.system
        if system is not None:
            needed = installed_estimate(selection, self.wizard_ref.pins, system)
            try:
                free = shutil.disk_usage(
                    prefix if prefix.exists() else prefix.parent
                ).free
            except OSError:
                free = 0
            if free and needed > free:
                answer = QMessageBox.question(
                    self,
                    "Not enough disk space",
                    f"This selection needs about {human_bytes(needed)} but only "
                    f"{human_bytes(free)} is free.\n\nContinue anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
        return True


class LicensePage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Breeze licence")
        layout = QVBoxLayout(self)
        layout.addLayout(
            _heading(
                "The Breeze voices are research releases",
                "Read the terms before downloading the weights. This page only "
                "appears when you selected a Breeze component.",
            )
        )
        self.text = _mono()
        self.text.setPlainText(LICENSE_TEXT)
        layout.addWidget(self.text, 1)
        self.accept = QCheckBox(
            "I understand and accept the research/non-commercial terms for the "
            "Breeze components I selected."
        )
        layout.addWidget(self.accept)
        self.accept.toggled.connect(lambda _checked: self.completeChanged.emit())

    def initializePage(self) -> None:
        self.accept.setChecked(bool(self.wizard_ref.args.accept_breeze_license))

    def isComplete(self) -> bool:
        return self.accept.isChecked()


class ProgressPage(QWizardPage):
    def __init__(self, wizard: Wizard, finish_page: FinishPage) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.finish_ref = finish_page
        self.setTitle("Installing")
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall.setFormat("%p%")
        layout.addWidget(self.overall)
        self.step_label = QLabel("Preparing…")
        self.step_label.setWordWrap(True)
        layout.addWidget(self.step_label)
        self.log_view = _mono()
        self.log_view.setMaximumBlockCount(5000)
        layout.addWidget(self.log_view, 1)
        self._started = False
        self._finished = False
        self._fractions: dict[str, float] = {}
        self._thread: QThread | None = None
        self._worker: InstallWorker | UninstallWorker | None = None

    def initializePage(self) -> None:
        if self._started:
            return
        self._started = True
        self._fractions.clear()
        self.overall.setValue(0)
        self.log_view.clear()
        mode = self.wizard_ref.mode()
        self.heading.setText(
            "Removing Drama Llama…" if mode == "uninstall" else "Installing Drama Llama…"
        )
        self.step_label.setText("Starting…")
        wizard = self.wizard_ref
        self._thread = QThread(self)
        if mode == "uninstall":
            keep = wizard.uninstall_page.keep_data.isChecked()
            purge = wizard.uninstall_page.purge_models.isChecked()
            worker: InstallWorker | UninstallWorker = UninstallWorker(
                InstallPaths.for_prefix(wizard.target_prefix()),
                keep_data=keep,
                purge_models=purge,
            )
        else:
            worker = InstallWorker(
                wizard.selection(),
                wizard.pins,
                wizard.current_system(),
                repair=mode == "repair",
            )
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.update.connect(self.on_update)
        worker.log.connect(self.append_log)
        worker.finished.connect(self.on_finished)
        worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_done)
        self._thread.start()

    def step_weights(self) -> list[tuple[str, float]]:
        wizard = self.wizard_ref
        if wizard.mode() == "uninstall":
            return [("uninstall", 1.0)]
        try:
            system = wizard.current_system()
            steps = build_steps(wizard.selection(), wizard.pins, system)
            return [(step.id, step.weight) for step in steps]
        except InstallerError:
            return []

    def on_update(self, step: str, fraction: float, message: str) -> None:
        self._fractions[step] = max(0.0, min(1.0, fraction))
        weights = dict(self.step_weights())
        total = sum(weights.values()) or 1.0
        overall = sum(weights.get(key, 0.0) * value for key, value in self._fractions.items())
        self.overall.setValue(int(min(1.0, overall / total) * 1000))
        if message:
            self.step_label.setText(message)

    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def on_finished(self, outcome: object) -> None:
        self._finished = True
        self.overall.setValue(1000 if getattr(outcome, "ok", False) or (
            isinstance(outcome, dict) and outcome.get("ok")
        ) else self.overall.value())
        self.wizard_ref.operation_outcome = outcome
        if isinstance(outcome, dict) and not outcome.get("ok"):
            self.step_label.setText(f"Uninstall failed: {outcome.get('error')}")
        elif getattr(outcome, "cancelled", False):
            self.step_label.setText("Installation cancelled.")
        elif getattr(outcome, "ok", False):
            self.step_label.setText("Done.")
        else:
            self.step_label.setText(f"Installation failed: {getattr(outcome, 'error', '')}")
        self.completeChanged.emit()
        QTimer.singleShot(500, self.wizard_ref.advance_from_progress)

    def _thread_done(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()

    def request_cancel(self) -> bool:
        if self._finished or self._worker is None:
            return False
        self._worker.request_cancel()
        self.step_label.setText("Cancelling…")
        return True

    def isComplete(self) -> bool:
        return self._finished


class FinishPage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Finished")
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        font = self.heading.font()
        font.setPointSize(max(14, font.pointSize() + 4))
        font.setBold(True)
        self.heading.setFont(font)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.body)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.warnings)
        layout.addStretch(1)

        row = QHBoxLayout()
        self.launch = QCheckBox("Launch Drama Llama now")
        self.launch.setChecked(True)
        row.addWidget(self.launch)
        row.addStretch(1)
        self.open_folder = QPushButton("Open install folder")
        self.open_folder.clicked.connect(self._open_folder)
        row.addWidget(self.open_folder)
        layout.addLayout(row)

        self.log_note = QLabel()
        self.log_note.setWordWrap(True)
        self.log_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.log_note)

    def initializePage(self) -> None:
        outcome = self.wizard_ref.operation_outcome
        mode = self.wizard_ref.mode()
        prefix = self.wizard_ref.target_prefix()
        if mode == "uninstall":
            ok = isinstance(outcome, dict) and outcome.get("ok")
            removed = len(outcome.get("removed", [])) if isinstance(outcome, dict) else 0
            self.heading.setText(
                "Drama Llama was removed" if ok else "Uninstall did not finish"
            )
            keep = self.wizard_ref.uninstall_page.keep_data.isChecked()
            body = [f"Removed {removed} item(s) from <code>{prefix}</code>."]
            body.append(
                f"Projects and settings remain in <code>{self.wizard_ref.selection().data}</code>."
                if keep
                else "Projects and settings were removed too."
            )
            if isinstance(outcome, dict) and outcome.get("error"):
                body.append(f"Error: {outcome['error']}")
            self.body.setText("<br>".join(body))
            self.warnings.setText("")
            self.launch.setVisible(False)
            self.launch.setChecked(False)
            self.log_note.setText("")
            return

        ok = bool(getattr(outcome, "ok", False))
        cancelled = bool(getattr(outcome, "cancelled", False))
        if cancelled:
            self.heading.setText("Installation cancelled")
        elif ok:
            self.heading.setText("Drama Llama is ready")
        else:
            self.heading.setText("Installation did not finish")
        lines: list[str] = []
        if ok:
            lines.append(
                f"Installed at <code>{prefix}</code>. Launch it from the "
                "Start Menu or the desktop entry."
            )
            lines.append(
                f"Projects are stored in <code>{self.wizard_ref.selection().data}</code>."
            )
        elif getattr(outcome, "error", ""):
            lines.append(f"<b>{outcome.error}</b>")
            lines.append(
                "The install log below has the details. Fix the problem and run "
                "the installer again; finished steps are reused."
            )
        self.body.setText("<br>".join(lines))
        warn = list(getattr(outcome, "warnings", []) or [])
        self.warnings.setText(
            "<br>".join(f"• {item}" for item in warn) if warn else ""
        )
        state = getattr(outcome, "state", None) or {}
        log_path = state.get("log_file", "")
        self.log_note.setText(f"Install log: {log_path}" if log_path else "")
        self.launch.setVisible(ok)
        self.launch.setChecked(ok)

    def _open_folder(self) -> None:
        prefix = self.wizard_ref.target_prefix()
        QProcess.startDetached("xdg-open" if not is_windows() else "explorer", [str(prefix)])

    def validatePage(self) -> bool:
        if (
            self.wizard_ref.mode() != "uninstall"
            and self.launch.isVisible()
            and self.launch.isChecked()
        ):
            self.wizard_ref.launch_app()
        return True


class UninstallPage(QWizardPage):
    def __init__(self, wizard: Wizard) -> None:
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Uninstall")
        layout = QVBoxLayout(self)
        layout.addLayout(
            _heading(
                "Remove Drama Llama",
                "The app, runtimes and launchers are removed. Your projects can be kept.",
            )
        )
        self.prefix_label = QLabel()
        self.prefix_label.setWordWrap(True)
        layout.addWidget(self.prefix_label)
        self.keep_data = QCheckBox("Keep my projects and settings (recommended)")
        self.keep_data.setChecked(True)
        layout.addWidget(self.keep_data)
        self.purge_models = QCheckBox(
            "Also delete downloaded models, Breeze checkpoints and the Chatterbox Docker image"
        )
        layout.addWidget(self.purge_models)
        layout.addStretch(1)

    def initializePage(self) -> None:
        self.prefix_label.setText(
            f"Install folder: <code>{self.wizard_ref.target_prefix()}</code>"
        )

    def validatePage(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Remove Drama Llama?",
            f"Remove Drama Llama from {self.wizard_ref.target_prefix()}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes


class Wizard(QWizard):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.pins = load_pins()
        self.system: SystemInfo | None = None
        self.operation_outcome: object = None
        self.requested_device = args.device
        self.requested_components = _parse_components_arg(args.components)

        self.setWindowTitle("Drama Llama installer")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.setMinimumSize(760, 600)
        icon = _payload_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self.finish_page = FinishPage(self)
        self.progress_page = ProgressPage(self, self.finish_page)
        self.location_page = LocationPage(self)
        self.components_page = ComponentsPage(self)
        self.detect_page = DetectPage(self)
        self.welcome_page = WelcomePage(self)
        self.license_page = LicensePage(self)
        self.uninstall_page = UninstallPage(self)

        self.setPage(PAGE_WELCOME, self.welcome_page)
        self.setPage(PAGE_DETECT, self.detect_page)
        self.setPage(PAGE_COMPONENTS, self.components_page)
        self.setPage(PAGE_LOCATION, self.location_page)
        self.setPage(PAGE_LICENSE, self.license_page)
        self.setPage(PAGE_PROGRESS, self.progress_page)
        self.setPage(PAGE_FINISH, self.finish_page)
        self.setPage(PAGE_UNINSTALL, self.uninstall_page)

        self.currentIdChanged.connect(self._page_changed)
        self.setButtonText(QWizard.WizardButton.NextButton, "Continue")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Close")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")

    # -- state helpers ---------------------------------------------------
    def install_state(self) -> dict | None:
        return load_state(installed_prefix(self.args.prefix))

    def installed_prefix(self) -> Path:
        return installed_prefix(self.args.prefix).prefix

    def current_system(self) -> SystemInfo:
        return self.system or _placeholder_system()

    def mode(self) -> str:
        return self.welcome_page.mode()

    def target_prefix(self) -> Path:
        if self.mode() == "uninstall":
            return self.installed_prefix()
        text = self.location_page.prefix_edit.text().strip()
        return Path(text).expanduser() if text else self.installed_prefix()

    def selection(self) -> Selection:
        components = (
            [CORE]
            if self.mode() == "uninstall"
            else self.components_page.selected_components()
        )
        accept = self.license_page.accept.isChecked()
        return Selection(
            components=frozenset(components),
            device=self.detect_page.selected_device() if hasattr(self, "detect_page") else "auto",
            prefix=self.target_prefix(),
            data_dir=self.location_page.data_dir() if self.mode() != "uninstall" else None,
            accept_breeze_license=accept,
            allow_docker_install=(
                self.components_page.chatterbox.isChecked()
                and self.components_page.allow_docker.isChecked()
            ),
        )

    def launch_app(self) -> None:
        prefix = self.target_prefix()
        python = prefix / "venv" / ("Scripts/pythonw.exe" if is_windows() else "bin/python")
        launch = prefix / "launch.py"
        if not python.is_file() or not launch.is_file():
            return
        QProcess.startDetached(str(python), [str(launch), "ui"], str(prefix))

    def advance_from_progress(self) -> None:
        if self.currentId() == PAGE_PROGRESS:
            self.next()

    # -- navigation ------------------------------------------------------
    def nextId(self) -> int:
        current = self.currentId()
        mode = self.mode()
        if current == PAGE_WELCOME:
            return PAGE_UNINSTALL if mode == "uninstall" else PAGE_DETECT
        if current == PAGE_DETECT:
            return PAGE_COMPONENTS
        if current == PAGE_COMPONENTS:
            return PAGE_LOCATION
        if current == PAGE_LOCATION:
            return PAGE_LICENSE if self.components_page.has_breeze() else PAGE_PROGRESS
        if current == PAGE_LICENSE:
            return PAGE_PROGRESS
        if current == PAGE_UNINSTALL:
            return PAGE_PROGRESS
        if current == PAGE_PROGRESS:
            return PAGE_FINISH
        return -1

    def back(self) -> None:
        if self.currentId() == PAGE_PROGRESS:
            return
        super().back()

    def reject(self) -> None:
        if self.currentId() == PAGE_PROGRESS and self.progress_page.request_cancel():
            self.setButtonText(QWizard.WizardButton.CancelButton, "Cancelling…")
            self.button(QWizard.WizardButton.CancelButton).setEnabled(False)
            return
        super().reject()

    def _page_changed(self, page_id: int) -> None:
        on_progress = page_id == PAGE_PROGRESS
        back = self.button(QWizard.WizardButton.BackButton)
        next_button = self.button(QWizard.WizardButton.NextButton)
        cancel = self.button(QWizard.WizardButton.CancelButton)
        if back is not None:
            back.setVisible(not on_progress and page_id != PAGE_FINISH)
        if cancel is not None:
            cancel.setVisible(page_id not in (PAGE_FINISH,))
        if next_button is not None:
            next_button.setVisible(page_id not in (PAGE_PROGRESS, PAGE_FINISH))
        if on_progress:
            self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel installation")
            self.progress_page.initializePage()
        if page_id == PAGE_FINISH:
            self.finish_page.initializePage()
            self.setButtonText(QWizard.WizardButton.FinishButton, "Close")
        if page_id == PAGE_COMPONENTS:
            self.components_page.update_estimate()
        if page_id == PAGE_LICENSE:
            self.license_page.initializePage()
            self.license_page.completeChanged.emit()


def _parse_components_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    from ..core.plan import parse_components

    try:
        return parse_components(value)
    except InstallerError:
        return None


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prefix")
    parser.add_argument("--data-dir")
    parser.add_argument("--cpu", dest="device", action="store_const", const="cpu")
    parser.add_argument("--gpu", dest="device", action="store_const", const="gpu")
    parser.add_argument("--device", choices=("auto", "gpu", "cpu"), default="auto")
    parser.add_argument("--components")
    parser.add_argument("--accept-breeze-license", action="store_true")
    parser.add_argument("--allow-docker-install", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser


def run_wizard(argv: list[str] | None = None) -> int:
    args, _unknown = _argument_parser().parse_known_args(argv)
    if args.yes:
        args.allow_docker_install = True
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Drama Llama installer")
    app.setApplicationDisplayName("Drama Llama installer")
    wizard = Wizard(args)
    wizard.show()
    return int(app.exec())
