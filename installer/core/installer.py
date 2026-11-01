"""Install orchestration: turn a validated selection into a working app."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .. import INSTALLER_VERSION
from .app_payload import install_payload, write_notices
from .detect import SystemInfo
from .engines import (
    DockerPendingReboot,
    install_breeze_de,
    install_breeze_en,
    install_breeze_repo,
    install_chatterbox,
)
from .launchers import write_launchers
from .plan import (
    BREEZE_DE,
    BREEZE_EN,
    CHATTERBOX,
    CORE,
    Selection,
    build_steps,
    effective_device,
    torch_channel_for,
    validate_selection,
)
from .progress import Progress
from .python_stack import install_stack
from .runtimes import ensure_ffmpeg, ensure_runtimes
from .state import (
    add_created_path,
    load_state,
    new_state,
    record_docker,
    save_state,
    set_component,
    write_location_pointer,
)
from .util import (
    Cancelled,
    InstallerError,
    app_version,
    human_bytes,
    utc_now,
)
from .verify import VerifyResult, verify_install
from .whisper import install_whisper


@dataclass
class InstallOutcome:
    ok: bool
    state: dict | None = None
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    cancelled: bool = False
    verify: VerifyResult | None = None


class _FileLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a", encoding="utf-8")  # noqa: SIM115 - closed in run_install

    def write(self, message: str) -> None:
        self.handle.write(message.rstrip() + "\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def _tee_progress(progress: Progress, log: _FileLog) -> None:
    original = progress.on_log

    def sink(message: str) -> None:
        log.write(message)
        if original is not None:
            original(message)

    progress.on_log = sink


def _step_component(step_id: str) -> str:
    if step_id in {BREEZE_EN, BREEZE_DE, CHATTERBOX}:
        return step_id
    return CORE


def run_install(
    selection: Selection,
    pins: dict,
    system: SystemInfo,
    progress: Progress,
    *,
    repair: bool = False,
) -> InstallOutcome:
    device = effective_device(selection, system)
    warnings = validate_selection(selection, pins, system)
    paths = selection.paths().ensure()
    write_location_pointer(paths.prefix)
    stamp = utc_now().replace(":", "").replace("-", "")
    log_path = paths.logs / f"install-{stamp}.log"
    log = _FileLog(log_path)
    _tee_progress(progress, log)
    progress.log(
        f"Drama Llama installer {app_version(paths.app)} | "
        f"{system.os_name}/{system.arch} | device={device} | prefix={paths.prefix}"
    )
    progress.log(f"install log: {log_path}")

    state = load_state(paths) or new_state(
        app_version=app_version(paths.app),
        installer_version=INSTALLER_VERSION,
        prefix=paths,
        platform=system.os_name,
        device=device,
        components=sorted(selection.components),
    )
    state["app_version"] = app_version(paths.app)
    state["platform"] = system.os_name
    state["device"] = device
    state["prefix"] = str(paths.prefix)
    state["data_dir"] = str(selection.data)
    state["log_file"] = str(log_path)
    state["completed"] = False
    state["cancelled"] = False
    for component in selection.components:
        set_component(state, component, "pending")
    state.setdefault("created_paths", [])
    save_state(paths, state)

    steps = build_steps(selection, pins, system)
    warnings_by_step: list[str] = list(warnings)
    breeze_de_cpu = bool(state.get("breeze_de_cpu", False))
    channel = torch_channel_for(selection, pins, system)
    verification: VerifyResult | None = None
    progress.log(f"torch channel: {channel}")
    try:
        for index, step in enumerate(steps):
            progress.check_cancelled()
            progress.update(step.id, 0.0, step.title)
            progress.log(f"[{index + 1}/{len(steps)}] {step.title}")
            try:
                if step.id == "payload":
                    install_payload(paths, progress)
                    write_notices(paths, pins)
                elif step.id == "runtimes":
                    ensure_runtimes(paths, pins, system, progress)
                elif step.id == "python":
                    install_stack(paths, pins, system, selection, progress)
                elif step.id == "whisper":
                    snapshot, downloaded = install_whisper(paths, pins, progress)
                    if downloaded:
                        add_created_path(state, snapshot)
                elif step.id == "ffmpeg":
                    ensure_ffmpeg(paths, system, progress)
                elif step.id == "breeze-repo":
                    repo, created = install_breeze_repo(paths, pins, progress)
                    if created:
                        add_created_path(state, repo)
                elif step.id == BREEZE_EN:
                    model, downloaded = install_breeze_en(paths, pins, progress)
                    if downloaded:
                        add_created_path(state, model)
                elif step.id == BREEZE_DE:
                    binary, model, breeze_de_cpu, created = install_breeze_de(
                        paths,
                        pins,
                        system,
                        progress,
                        device=device,
                        allow_elevated=selection.allow_docker_install,
                    )
                    state["breeze_de_cpu"] = breeze_de_cpu
                    if created:
                        add_created_path(state, binary.parent)
                        add_created_path(state, model)
                elif step.id == CHATTERBOX:
                    details = install_chatterbox(
                        paths,
                        pins,
                        system,
                        progress,
                        device=device,
                        allow_elevated=selection.allow_docker_install,
                    )
                    record_docker(state, **details)
                elif step.id == "launchers":
                    write_launchers(
                        paths,
                        pins,
                        selection,
                        system,
                        progress,
                        device=device,
                        breeze_de_cpu=breeze_de_cpu,
                        state=state,
                    )
                elif step.id == "verify":
                    verification = verify_install(
                        paths,
                        pins,
                        system,
                        selection,
                        progress,
                        breeze_de_cpu=breeze_de_cpu,
                    )
                    warnings_by_step.extend(verification.warnings)
                    result = verification
                    if not result.ok:
                        raise InstallerError(
                            "post-install verification failed: " + result.summary()
                        )
                else:  # pragma: no cover - defensive
                    raise InstallerError(f"unknown install step {step.id!r}")
                set_component(state, step.component, "installed")
                save_state(paths, state)
            except DockerPendingReboot as exc:
                set_component(state, step.component, "pending_reboot", message=str(exc))
                warnings_by_step.append(f"{step.title}: {exc}")
                save_state(paths, state)
                progress.log(f"pending: {exc}")
                progress.update(step.id, 1.0, "Needs a restart")
            except Cancelled:
                raise
            except InstallerError as exc:
                if step.optional:
                    set_component(state, step.component, "failed", message=str(exc))
                    warnings_by_step.append(f"{step.title}: {exc}")
                    save_state(paths, state)
                    progress.log(f"optional component failed: {exc}")
                    progress.update(step.id, 1.0, "Failed (optional)")
                    continue
                set_component(state, step.component, "failed", message=str(exc))
                save_state(paths, state)
                progress.log(f"error: {exc}")
                log.write(traceback.format_exc())
                return InstallOutcome(
                    ok=False,
                    state=state,
                    warnings=warnings_by_step,
                    error=str(exc),
                )
        state["completed"] = True
        state["app_version"] = app_version(paths.app)
        state["completed_at"] = utc_now()
        save_state(paths, state)
        progress.log("installation complete")
    except Cancelled:
        state["cancelled"] = True
        save_state(paths, state)
        progress.log("installation cancelled")
        return InstallOutcome(
            ok=False, state=state, warnings=warnings_by_step, cancelled=True
        )
    finally:
        log.close()

    return InstallOutcome(
        ok=True,
        state=state,
        warnings=warnings_by_step,
        verify=verification,
    )


def plan_text(selection: Selection, pins: dict, system: SystemInfo) -> str:
    from .plan import download_estimate, installed_estimate

    download = download_estimate(selection, pins, system)
    installed = installed_estimate(selection, pins, system)
    components = ", ".join(sorted(selection.components))
    return (
        f"components: {components}\n"
        f"device: {effective_device(selection, system)}\n"
        f"torch: {torch_channel_for(selection, pins, system)}\n"
        f"download: {human_bytes(download)}\n"
        f"on disk: {human_bytes(installed)}\n"
        f"prefix: {selection.prefix}\n"
        f"data: {selection.data}"
    )


