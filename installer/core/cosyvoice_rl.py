"""Build the reinforcement-tuned CosyVoice 3 model, when its sources are here.

Nobody publishes the RL language model converted to GGUF. When the official
snapshot (about 4.4 GB) is already in the Hugging Face cache, it is converted
locally; otherwise the published base model is used. Nothing large is ever
downloaded for this, and a failure only means the base model is used instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from .download import download
from .paths import InstallPaths, legacy_cosyvoice_models, whisper_cache_root
from .progress import Progress
from .runtimes import uv_pip_install
from .util import Cancelled, InstallerError, run, verify_file

PYPI = "https://pypi.org/simple"


def find_sources(meta: dict, home: Path | None, cancel=None) -> Path | None:
    """The upstream snapshot directory, if every pinned source file verifies."""
    upstream = meta["upstream"]
    cache = "models--" + str(upstream["repo"]).replace("/", "--")
    snapshot = whisper_cache_root(home) / cache / "snapshots" / str(upstream["revision"])
    for item in meta["rl"]["sources"]:
        if not verify_file(
            snapshot / item["path"],
            size=int(item["size"]),
            sha256=str(item["sha256"]),
            cancel=cancel,
        ):
            return None
    return snapshot


def convert(
    python: Path,
    meta: dict,
    snapshot: Path,
    output: Path,
    work: Path,
    *,
    packages_dir: Path | None = None,
) -> bool:
    """Run the pinned converter. Returns whether the result matches the pinned hash."""
    rl = meta["rl"]
    script = work / "convert_model_to_gguf.py"
    pinned = rl["converter"]
    download(
        str(pinned["url"]), script, sha256=str(pinned["sha256"]), size=int(pinned["size"])
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""  # conversion must not take the GPU
    if packages_dir is not None:
        env["PYTHONPATH"] = str(packages_dir)
    try:
        result = run(
            [
                python, script,
                "--yaml_config", snapshot / "cosyvoice3.yaml",
                "--llm_model", snapshot / "llm.rl.pt",
                "--blank_llm", snapshot / "CosyVoice-BlankEN",
                "--flow_model", snapshot / "flow.pt",
                "--hift_model", snapshot / "hift.pt",
                "--gguf_model", partial,
                "--ftype", "q8_0",
                "--tag", "2512",
            ],
            cwd=work,
            env=env,
            timeout=3600,
        )
        if result.returncode != 0 or not partial.is_file():
            detail = (result.stderr or result.stdout or "").strip()[-600:]
            raise InstallerError(f"the converter failed: {detail}")
        matches = verify_file(partial, size=int(rl["size"]), sha256=str(rl["sha256"]))
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    return matches


def build_if_possible(
    paths: InstallPaths,
    pins: dict,
    python: Path,
    progress: Progress,
    home: Path | None,
    created: list[Path],
) -> None:
    meta = pins["cosyvoice"]
    rl = meta.get("rl")
    if not rl:
        return
    output = legacy_cosyvoice_models(home) / str(rl["file"])
    if verify_file(output, size=int(rl["size"]), sha256=str(rl["sha256"])):
        return
    snapshot = find_sources(meta, home, progress.cancelled)
    if snapshot is None:
        progress.log(
            "the official CosyVoice sources are not on this machine; using the "
            "published model"
        )
        return
    progress.update("cosyvoice", 0.08, "Converting the better CosyVoice weights…")
    work = paths.cache / "cosyvoice-rl"
    packages = work / "packages"
    try:
        uv_pip_install(
            paths,
            pins,
            ["--target", str(packages), *rl["packages"]],
            progress=progress,
            index_url=PYPI,
        )
        if convert(python, meta, snapshot, output, work, packages_dir=packages):
            created.append(output)
            progress.log(f"built {output}")
            return
        output.unlink(missing_ok=True)
        progress.log("warning: the converted model does not match the pinned hash; removed")
    except Cancelled:
        raise
    except (InstallerError, OSError) as exc:
        progress.log(f"warning: could not convert the RL model: {exc}")
    progress.log("using the published model instead")
