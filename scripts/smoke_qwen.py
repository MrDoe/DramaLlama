#!/usr/bin/env python3
"""Smoke test for the German Qwen3 path: one stem + German alignment."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from drama_studio.aligner import WhisperAligner
from drama_studio.engines.qwen3 import Qwen3Engine
from drama_studio.models import StemRenderRequest
from drama_studio.parser import parse_script


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--script", default="/home/christoph/tts_test/audio_drama_de/script.md"
    )
    parser.add_argument("--scene", type=int, default=1)
    parser.add_argument("--speaker", default="ANNA BERG")
    parser.add_argument("--preset", default="Vivian")
    parser.add_argument("--out", default="/tmp/opencode/drama-qwen-smoke")
    parser.add_argument("--candidates", type=int, default=1)
    parser.add_argument("--no-align", action="store_true")
    args = parser.parse_args()

    doc = parse_script(Path(args.script).read_text(encoding="utf-8"), "de")
    lines = [
        line
        for scene in doc.script.scenes
        if scene.id == args.scene
        for line in scene.lines
        if line.speaker == args.speaker
    ]
    if not lines:
        raise SystemExit(f"no lines for {args.speaker} in scene {args.scene}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    character = doc.script.characters[args.speaker]
    request = StemRenderRequest(
        project_id="smoke-de",
        stem_id=f"s{args.scene}-{args.speaker.lower().replace(' ', '-')}",
        speaker=args.speaker,
        text=" ... ".join(line.text for line in lines),
        instruction=(
            f"{character.description} "
            "Ruhig, präzise, unerbittlich; die Stimme bleibt tief und kontrolliert."
        ),
        output_dir=str(out),
        voice_preset=args.preset,
        language="de",
        seed=42 + args.scene * 10,
        candidates=args.candidates,
    )

    engine = Qwen3Engine()
    t0 = time.time()
    engine.load()
    load_s = time.time() - t0

    t1 = time.time()
    result = engine.render_stem(request, progress=lambda p, m: print(f"  {p:5.1%} {m}"))
    render_s = time.time() - t1
    selected = next(take for take in result.takes if take.selected)
    stem_path = out / selected.file

    alignment = None
    align_s = 0.0
    if not args.no_align:
        t2 = time.time()
        aligner = WhisperAligner(language="de")
        alignment = aligner.align(
            stem_path, lines, out, stem_id=request.stem_id, language="de"
        )
        align_s = time.time() - t2

    import torch

    report = {
        "load_s": round(load_s, 1),
        "render_s": round(render_s, 1),
        "align_s": round(align_s, 1),
        "stem": {
            "file": str(stem_path),
            "duration_s": selected.duration_s,
            "f0_hz": selected.f0_hz,
            "candidates": len(result.takes),
        },
        "lines": [
            {
                "id": item.line_id,
                "start_s": item.start_s,
                "end_s": item.end_s,
                "confidence": item.confidence,
            }
            for item in (alignment.lines if alignment else [])
        ],
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
    }
    (out / "smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    engine.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
