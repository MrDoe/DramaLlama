#!/usr/bin/env python3
"""M0b smoke test: one Breeze stem + Whisper alignment on real audio."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from drama_studio.aligner import WhisperAligner
from drama_studio.engines.breeze import BreezeEngine
from drama_studio.models import StemRenderRequest
from drama_studio.parser import parse_script

REF_TEXT = {
    "MARA": (
        "You're late. Four days after the funeral. To the house you swore "
        "you'd never set foot in again."
    ),
    "ELIAS": (
        "I came, didn't I? What more do you want from me? You think this "
        "is easy for me?"
    ),
    "MOTHER": (
        "Eli, sweetheart. It's Mom. I know you're busy. I just wanted to "
        "hear your voice, even if it's only the machine."
    ),
}

INSTRUCTIONS = {
    ("MARA", 1): (
        "A woman in her late thirties with a hard, low, cutting voice. Cold fury; "
        "she is confronting someone and will not let go. Every line is an accusation."
    ),
    ("ELIAS", 1): (
        "A man in his early forties with a deep, rough voice. Angry, proud and "
        "argumentative. He fights back, but he is tired underneath."
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--script", default="/home/christoph/tts_test/audio_drama/script.md"
    )
    parser.add_argument(
        "--refs", default="/home/christoph/tts_test/output/audio_drama_breeze/refs"
    )
    parser.add_argument("--scene", type=int, default=1)
    parser.add_argument("--speaker", default="MARA")
    parser.add_argument("--out", default="/tmp/opencode/drama-breeze-smoke")
    parser.add_argument("--candidates", type=int, default=1)
    parser.add_argument("--no-align", action="store_true")
    args = parser.parse_args()

    doc = parse_script(Path(args.script).read_text(encoding="utf-8"), "en")
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
    request = StemRenderRequest(
        project_id="smoke",
        stem_id=f"s{args.scene}-{args.speaker.lower()}",
        speaker=args.speaker,
        text=" ... ".join(line.text for line in lines),
        instruction=INSTRUCTIONS.get(
            (args.speaker, args.scene), "A character in an audio drama, speaking naturally."
        ),
        output_dir=str(out),
        ref_audio_path=str(Path(args.refs) / f"{args.speaker.lower()}.wav"),
        ref_text=REF_TEXT[args.speaker],
        cfg_scale=2.5,
        seed=42 + args.scene * 10,
        candidates=args.candidates,
    )

    engine = BreezeEngine()
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
        aligner = WhisperAligner(language="en")
        alignment = aligner.align(
            stem_path, lines, out, stem_id=request.stem_id, language="en"
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
            "mfcc_sim": selected.mfcc_sim,
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
