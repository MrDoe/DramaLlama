#!/usr/bin/env python3
"""Render an audio drama as one stem per character per scene, then cut it.

Why: per-line generation re-samples the voice every time, so timbre and pitch
drift. One continuous performance per character/scene keeps the voice stable;
Whisper word timestamps are used to cut the stem back into script lines.

Run:
  python ~/tts_test/breeze_stems.py --scenes 1 --out /tmp/opencode/stem_test
  python ~/tts_test/breeze_stems.py --out ~/tts_test/output/audio_drama_stems
"""
import argparse
import difflib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import soundfile as sf
import torch  # noqa: F401  (loads libcublas for ctranslate2)
import librosa
from faster_whisper import WhisperModel

from breeze_drama import REFS, convert_tags, slug, synth  # noqa: E402
from breeze_infer.runtime import (  # noqa: E402
    load_runtime,
    resolve_device,
    update_generation_config_for_breeze,
)
from models.fast_streaming import (  # noqa: E402
    FastBreezeStreamingRuntime,
    FastStreamingConfig,
)
from pathlib import Path  # noqa: E402

MODEL = Path(os.path.expanduser("~/models/Breeze-TTS-2"))
ASR_SNAP = os.path.expanduser(
    "~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/snapshots/*")

# Scene-level direction: the emotional arc of the whole stem.
SCENE_INSTRUCTIONS = {
    ("MARA", 1): (
        "A woman in her late thirties with a hard, low, cutting voice. Cold fury; "
        "she is confronting someone and will not let go. Every line is an accusation."
    ),
    ("ELIAS", 1): (
        "A man in his early forties with a deep, rough voice. Angry, proud and "
        "argumentative. He fights back, but he is tired underneath."
    ),
    ("MARA", 2): (
        "A woman in her late thirties with a hard, low voice. Quiet and merciless, "
        "but grief is leaking through. She is delivering facts that hurt."
    ),
    ("ELIAS", 2): (
        "A man in his early forties with a deep, rough voice. The fight is draining "
        "out of him. Guilt and shock."
    ),
    ("MOTHER", 2): (
        "An elderly woman, 68, with a low, weathered, frail voice. Warm and tired, "
        "leaving a voicemail."
    ),
    ("MARA", 3): (
        "A woman in her late thirties, hard voice softened by exhaustion. Guarded, "
        "but briefly tender."
    ),
    ("ELIAS", 3): (
        "A man in his early forties, deep, worn voice. Raw and quiet, no longer "
        "fighting."
    ),
}


def norm_words(text, fold_umlauts=False):
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.lower()
    if fold_umlauts:
        for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            text = text.replace(src, dst)
    text = re.sub(r"[^\w' ]+", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    return text.split()


def find_asr():
    import glob
    paths = glob.glob(ASR_SNAP)
    if not paths:
        sys.exit("error: faster-whisper large-v3 not found in the HF cache")
    return paths[0]


def align_cuts(stem, sr, lines, asr, language="en"):
    """Return ([(start_s, end_s), ...], [confidence, ...]) for each line."""
    fold = language.lower().startswith("de")
    y16 = librosa.resample(stem, orig_sr=sr, target_sr=16000)
    segments, _ = asr.transcribe(
        y16.astype(np.float32), language=language, beam_size=5,
        word_timestamps=True, condition_on_previous_text=False,
    )
    hyp = [(w.word, w.start, w.end) for s in segments for w in s.words]
    hyp_words = [norm_words(w, fold)[0] if norm_words(w, fold) else ""
                 for w, _, _ in hyp]

    script_words, ranges = [], []
    for line in lines:
        ws = norm_words(convert_tags(line["text"]), fold)
        ranges.append((len(script_words), len(script_words) + len(ws)))
        script_words.extend(ws)

    matcher = difflib.SequenceMatcher(None, script_words, hyp_words, autojunk=False)
    mapping = {}
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k

    cuts = []
    confs = []
    for a, b in ranges:
        idxs = [mapping[i] for i in range(a, b) if i in mapping]
        confs.append(len(idxs) / max(1, b - a))
        if idxs:
            start = max(0.0, hyp[min(idxs)][1] - 0.08)
            end = min(len(stem) / sr, hyp[max(idxs)][2] + 0.12)
        else:
            start = end = None
        cuts.append([start, end])

    # fill unmatched ranges by interpolation between neighbours
    for i, (start, end) in enumerate(cuts):
        if start is not None:
            continue
        prev_end = cuts[i - 1][1] if i > 0 and cuts[i - 1][1] is not None else 0.0
        nxt_start = next((c[0] for c in cuts[i + 1:] if c[0] is not None),
                         len(stem) / sr)
        cuts[i] = [prev_end + 0.05, max(prev_end + 0.15, nxt_start - 0.05)]

    # enforce monotonic, non-overlapping cuts
    for i in range(1, len(cuts)):
        if cuts[i][0] < cuts[i - 1][1]:
            mid = (cuts[i - 1][1] + cuts[i][0]) / 2
            cuts[i - 1][1] = mid
            cuts[i][0] = mid
    return [(float(a), float(b)) for a, b in cuts], confs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lines", default=os.path.join(HERE, "audio_drama/lines.json"))
    p.add_argument("--refs", default=os.path.expanduser(
        "~/tts_test/output/audio_drama_breeze/refs"))
    p.add_argument("--out", default=os.path.expanduser(
        "~/tts_test/output/audio_drama_stems"))
    p.add_argument("--scenes", default="1,2,3",
                   help="Comma-separated scene numbers to render")
    p.add_argument("--cfg-scale", type=float, default=2.5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--pause-ms", type=int, default=500)
    p.add_argument("--asr-device", default="cpu",
                   help="cpu keeps Whisper out of Breeze's VRAM")
    p.add_argument("--asr-compute", default="int8")
    p.add_argument("--language", default="en",
                   help="ASR language for alignment (e.g. en, de)")
    args = p.parse_args()

    doc = json.load(open(args.lines, encoding="utf-8"))
    scenes = {int(s) for s in args.scenes.split(",")}
    lines = [l for l in doc["lines"] if l["scene"] in scenes]
    os.makedirs(args.out, exist_ok=True)
    stems_dir = os.path.join(args.out, "stems")
    cut_dir = os.path.join(args.out, "cut")
    os.makedirs(stems_dir, exist_ok=True)
    os.makedirs(cut_dir, exist_ok=True)

    device = resolve_device()
    print(f"loading Breeze on {device}", flush=True)
    tokenizer, model, audio_tokenizer = load_runtime(
        MODEL, device=device, attn_implementation="eager")
    update_generation_config_for_breeze(model)
    runtime = FastBreezeStreamingRuntime(
        model, audio_tokenizer,
        FastStreamingConfig(max_new_tokens=2000, max_seq_len=2048,
                            fast_all=False, repetition_penalty=1.1),
        tokenizer=tokenizer,
    )
    asr = WhisperModel(find_asr(), device=args.asr_device,
                       compute_type=args.asr_compute)

    # group by (scene, speaker), preserving script order
    groups = {}
    for line in lines:
        groups.setdefault((line["scene"], line["speaker"]), []).append(line)

    entries = []
    for (scene, character), group in groups.items():
        ref = os.path.join(args.refs, f"{character.lower()}.wav")
        if not os.path.exists(ref):
            sys.exit(f"error: missing reference {ref}")
        stem_text = " ... ".join(convert_tags(l["text"]) for l in group)
        instruct = SCENE_INSTRUCTIONS.get(
            (character, scene),
            "A character in an audio drama, speaking naturally.")
        req = {
            "id": f"stem-{scene}-{character}",
            "text": stem_text,
            "instruction": instruct,
            "ref_audio_path": ref,
            "ref_text": REFS[character]["text"],
        }
        print(f"\nstem scene {scene} / {character}: {len(group)} lines", flush=True)
        t0 = time.time()
        stem, sr = synth(runtime, tokenizer, model, audio_tokenizer, req,
                         args.cfg_scale, args.seed + scene * 10)
        stem_path = os.path.join(stems_dir, f"s{scene}_{character.lower()}.wav")
        sf.write(stem_path, stem, sr)
        print(f"  stem {len(stem) / sr:.2f}s in {time.time() - t0:.1f}s", flush=True)

        cuts, confs = align_cuts(stem, sr, group, asr, args.language)
        for line, (a, b), conf in zip(group, cuts, confs):
            seg = stem[int(a * sr):int(b * sr)]
            fname = f"{line['id']}_{character.lower()}_{slug(convert_tags(line['text']))}.wav"
            path = os.path.join(cut_dir, fname)
            sf.write(path, seg, sr)
            f0, _, _ = librosa.pyin(seg, fmin=60, fmax=300, sr=sr, frame_length=2048)
            f0 = f0[~np.isnan(f0)]
            entries.append({
                "id": line["id"],
                "scene": scene,
                "speaker": character,
                "text": convert_tags(line["text"]),
                "file": path,
                "stem": stem_path,
                "start_s": round(a, 3),
                "end_s": round(b, 3),
                "duration_s": round((b - a), 3),
                "align_conf": round(float(conf), 3),
                "f0_hz": round(float(np.median(f0)), 1) if len(f0) else 0.0,
            })
            print(f"    {line['id']} {b - a:5.2f}s F0 {entries[-1]['f0_hz']:5.1f} "
                  f"conf {conf:.2f}  {convert_tags(line['text'])[:45]}", flush=True)

    # reassemble in script order
    order = {l["id"]: i for i, l in enumerate(doc["lines"])}
    entries.sort(key=lambda e: order[e["id"]])
    gap = np.zeros(int(sr * args.pause_ms / 1000), dtype=np.float32)
    parts = []
    for e in entries:
        seg, _ = sf.read(e["file"], dtype="float32")
        if parts:
            parts.append(gap)
        parts.append(seg)
    combined = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    stem_name = slug(doc.get("title", "drama"), 60)
    combined_path = os.path.join(args.out, f"{stem_name}_stems.wav")
    sf.write(combined_path, combined, sr)

    manifest = {
        "input": os.path.abspath(args.lines),
        "engine": "breeze-tts-2 stems",
        "mode": "one stem per character per scene, Whisper-aligned cuts",
        "cfg_scale": args.cfg_scale,
        "pause_ms": args.pause_ms,
        "entries": entries,
        "combined": {"file": combined_path,
                     "duration_s": round(len(combined) / sr, 3)},
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(f"\ncombined -> {combined_path} ({len(combined) / sr:.2f}s)")
    for c in sorted({e['speaker'] for e in entries}):
        vals = [e["f0_hz"] for e in entries if e["speaker"] == c and e["f0_hz"]]
        print(f"{c:6s} F0 median {np.median(vals):5.1f} Hz  "
              f"spread {min(vals):5.1f}-{max(vals):5.1f}  lines {len(vals)}")


if __name__ == "__main__":
    main()
