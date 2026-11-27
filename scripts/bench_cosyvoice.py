"""Measure CosyVoice 3 reliability and delivery for model/flag/instruction variants.

Renders the same German lines for every variant with ``cosyvoice-cli``, scores
each clip with ``drama_studio.speech_check`` and reports how often a variant
says the line, how often it is silent, and how far it moves level, pitch and
tempo away from the first variant.

    python scripts/bench_cosyvoice.py spec.json out/

The spec is JSON::

    {"cli": "...", "ggml_dir": "...", "lib_dirs": ["..."], "backend": "Vulkan1",
     "seeds": [42, 43, 44], "lines": ["...", "..."],
     "voices": {"anna": "prompt_speech.gguf"},
     "variants": [{"name": "base", "model": "...gguf", "voices": {...},
                   "instruction": null, "args": []}]}

Needs a free GPU: Whisper runs on it too (``--asr-device cpu`` to avoid that).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drama_studio.audio import read_mono, rms
from drama_studio.speech_check import PASS_SCORE, is_silent, match_score
from drama_studio.transcriber import WhisperTranscriber


def measure(path: Path) -> dict:
    import librosa

    audio, rate = read_mono(path)
    if is_silent(audio, rate):
        return {"silent": True, "duration_s": len(audio) / rate, "level_db": -90.0, "f0": 0.0}
    f0 = librosa.yin(audio, fmin=60, fmax=500, sr=rate)
    energy = librosa.feature.rms(y=audio)[0][: len(f0)]
    voiced = f0[: len(energy)][energy > np.percentile(energy, 40)]
    return {
        "silent": False,
        "duration_s": len(audio) / rate,
        "level_db": float(20 * np.log10(rms(audio) + 1e-9)),
        "f0": float(np.median(voiced)),
    }


def synthesize(spec: dict, variant: dict, voice: Path, line: str, seed: int, out: Path) -> None:
    library_path = os.pathsep.join([*spec["lib_dirs"], spec["ggml_dir"]])
    command = [
        spec["cli"], "--backend-path", spec["ggml_dir"], "--backend", spec["backend"],
        "--model", variant["model"], "--prompt-speech", str(voice),
        "--text", line, "--seed", str(seed), "--output", str(out),
        *variant.get("args", []),
    ]
    if variant.get("instruction"):
        command += ["--instruction", variant["instruction"]]
    done = subprocess.run(
        command, capture_output=True, text=True, timeout=300,
        env={**os.environ, "LD_LIBRARY_PATH": library_path},
    )
    if done.returncode != 0 or not out.is_file():
        raise RuntimeError(f"{variant['name']}: {(done.stdout + done.stderr)[-1500:]}")


def summarize(rows: list[dict], baseline: dict[tuple, dict]) -> dict:
    paired = [(r, baseline[r["key"]]) for r in rows if r["key"] in baseline]
    paired = [(r, b) for r, b in paired if not r["silent"] and not b["silent"]]
    summary = {
        "clips": len(rows),
        "pass_rate": round(sum(r["score"] >= PASS_SCORE for r in rows) / len(rows), 3),
        "mean_score": round(float(np.mean([r["score"] for r in rows])), 3),
        "silent": sum(r["silent"] for r in rows),
        "synth_s": round(float(np.mean([r["synth_s"] for r in rows])), 2),
    }
    if paired:
        summary |= {
            "level_db": round(float(np.median([r["level_db"] - b["level_db"] for r, b in paired])), 1),
            "pitch_st": round(float(np.median([12 * np.log2(r["f0"] / b["f0"]) for r, b in paired])), 1),
            "tempo_x": round(float(np.median([r["duration_s"] / b["duration_s"] for r, b in paired])), 2),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--asr-device", default="cuda", choices=["cuda", "cpu"])
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if args.asr_device == "cuda":
        import torch  # noqa: F401  (loads the CUDA libraries ctranslate2 needs)
    asr = WhisperTranscriber(
        language="de",
        device=args.asr_device,
        compute_type="float16" if args.asr_device == "cuda" else "int8",
    )
    args.out.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    baseline: dict[tuple, dict] = {}
    for position, variant in enumerate(spec["variants"]):
        name = variant["name"]
        folder = args.out / name
        folder.mkdir(exist_ok=True)
        voices = variant.get("voices") or spec["voices"]
        rows = []
        for voice_name, voice in voices.items():
            for index, line in enumerate(spec["lines"]):
                for seed in spec["seeds"]:
                    path = folder / f"{voice_name}_{index:02d}_{seed}.wav"
                    start = time.perf_counter()
                    synthesize(spec, variant, Path(voice), line, seed, path)
                    elapsed = time.perf_counter() - start
                    audio, rate = read_mono(path)
                    heard = asr.transcribe(audio, rate, word_timestamps=False).text
                    row = {
                        "key": (voice_name, index, seed), "file": path.name, "line": line,
                        "heard": heard, "score": match_score(line, heard),
                        "synth_s": elapsed, **measure(path),
                    }
                    rows.append(row)
        if position == 0:
            baseline = {r["key"]: r for r in rows}
        results[name] = {"summary": summarize(rows, baseline), "rows": rows}
        print(name, json.dumps(results[name]["summary"]), flush=True)
        (args.out / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
