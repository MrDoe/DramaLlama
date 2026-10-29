#!/usr/bin/env python3
"""Render an audio drama with Breeze TTS 2.

Workflow per character:
  1. voice design  -> a short reference clip from a text description
  2. voice direction -> every line uses that clip as timbre reference while
     the per-line instruction steers emotion and delivery

Reads the drama `lines.json` (text / qwen_instruct / speaker), writes one WAV
per line plus a combined track and a manifest.

Run:
  python ~/tts_test/breeze_drama.py \
    --lines ~/tts_test/audio_drama/lines.json \
    --out ~/tts_test/output/audio_drama_breeze
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = os.path.expanduser("~/code/breeze-tts")
MODEL = os.path.expanduser("~/models/Breeze-TTS-2")
sys.path.insert(0, REPO)

import numpy as np
import soundfile as sf
import torch
import librosa

from breeze_infer.runtime import (
    load_runtime,
    resolve_device,
    set_all_seeds,
    update_generation_config_for_breeze,
)
from breeze_infer.templates import get_template, prepare_inputs, select_template_name
from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig

# Breeze supports (sigh), (laugh), (cough), (clears throat); map or drop the rest.
TAG_MAP = {
    "sigh": "(sigh)",
    "laugh": "(laugh)",
    "chuckle": "(laugh)",
    "cough": "(cough)",
    "sniffle": "(sigh)",
}

# One designed reference per character. The ref text is spoken verbatim.
# Do NOT send a "speaker" field when using a reference: Breeze prefixes the
# text with "[S0]", which overrides the reference timbre (140 Hz clone became
# 195 Hz with the prefix). Omitting it lets the reference carry the identity.
REFS = {
    "MARA": {
        "text": ("You're late. Four days after the funeral. To the house you swore "
                 "you'd never set foot in again."),
        "instruction": (
            "A woman in her late thirties with a hard, low, cutting voice. Cold "
            "fury held on a tight leash; she is confronting someone and will not "
            "let go."
        ),
    },
    "ELIAS": {
        "text": ("I came, didn't I? What more do you want from me? You think this "
                 "is easy for me? I drove four hours in this weather. Four hours."),
        "instruction": (
            "A man in his early forties with a deep, rough, low-pitched voice. "
            "Quick to anger, proud and argumentative, tired underneath."
        ),
    },
    "MOTHER": {
        "text": ("Eli, sweetheart. It's Mom. I know you're busy. I just wanted to "
                 "hear your voice, even if it's only the machine."),
        "instruction": (
            "An elderly woman, 68 years old, with a low, weathered, frail voice. "
            "Warm and tired, speaking slowly."
        ),
    },
}


def convert_tags(text):
    def repl(match):
        return TAG_MAP.get(match.group(1).lower(), "")
    text = re.sub(r"<([a-zA-Z_]+)>", repl, text)
    return re.sub(r"\s{2,}", " ", text).strip()


def slug(text, limit=40):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower())[:limit].strip("_")
    return s or "line"


def synth(runtime, tokenizer, model, audio_tokenizer, request, cfg, seed):
    set_all_seeds(seed)
    template = get_template(select_template_name(request))
    inputs = prepare_inputs(
        tokenizer, audio_tokenizer, model, [request], template,
        guidance_scale=cfg, guidance_scale_ref=None, guidance_scale_ins=None,
    )
    chunks = []
    for chunk in runtime.iter_audio_chunks(
        inputs, request_id=request["id"], seed=seed
    ):
        chunks.append(np.asarray(chunk.audio, dtype=np.float32))
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    return audio, runtime.sample_rate


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lines", default=os.path.expanduser(
        "~/tts_test/audio_drama/lines.json"))
    p.add_argument("--out", default=os.path.expanduser(
        "~/tts_test/output/audio_drama_breeze"))
    p.add_argument("--cfg-scale", type=float, default=2.5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--pause-ms", type=int, default=500)
    p.add_argument("--force-refs", action="store_true")
    p.add_argument("--candidates", type=int, default=3,
                   help="Generate N takes per line and keep the one closest to the "
                        "character's reference pitch (after a timbre check)")
    p.add_argument("--limit", type=int, default=0, help="Render only N lines (debug)")
    args = p.parse_args()

    doc = json.load(open(args.lines, encoding="utf-8"))
    os.makedirs(args.out, exist_ok=True)
    refs_dir = os.path.join(args.out, "refs")
    os.makedirs(refs_dir, exist_ok=True)

    device = resolve_device()
    print(f"loading Breeze from {MODEL} on {device}", flush=True)
    t0 = time.time()
    tokenizer, model, audio_tokenizer = load_runtime(
        Path(MODEL), device=device, attn_implementation="eager"
    )
    update_generation_config_for_breeze(model)
    config = FastStreamingConfig(
        max_new_tokens=1500,
        max_seq_len=2048,
        fast_all=False,
        repetition_penalty=1.1,
    )
    runtime = FastBreezeStreamingRuntime(
        model, audio_tokenizer, config, tokenizer=tokenizer
    )
    print(f"loaded in {time.time() - t0:.1f}s", flush=True)

    # --- reference voices -------------------------------------------------
    ref_paths = {}
    for character, spec in REFS.items():
        path = os.path.join(refs_dir, f"{character.lower()}.wav")
        ref_paths[character] = path
        if os.path.exists(path) and not args.force_refs:
            print(f"ref {character}: cached", flush=True)
            continue
        print(f"designing ref for {character} ...", flush=True)
        req = {
            "id": f"ref-{character}",
            "text": spec["text"],
            "instruction": spec["instruction"],
        }
        audio, sr = synth(runtime, tokenizer, model, audio_tokenizer, req,
                          args.cfg_scale, args.seed)
        sf.write(path, audio, sr)
        print(f"  -> {path} ({len(audio) / sr:.2f}s)", flush=True)

    ref_f0 = {}
    ref_vec = {}
    for character, path in ref_paths.items():
        y, sr = sf.read(path)
        f0, _, _ = librosa.pyin(y, fmin=60, fmax=300, sr=sr, frame_length=2048)
        f0 = f0[~np.isnan(f0)]
        ref_f0[character] = float(np.median(f0))
        ref_vec[character] = librosa.feature.mfcc(
            y=y, sr=sr, n_mfcc=20).mean(axis=1)
        print(f"ref {character}: F0 {ref_f0[character]:.1f} Hz", flush=True)

    # --- lines ------------------------------------------------------------
    entries = []
    combined = []
    combined_sr = None
    gap = None
    t_start = time.time()
    lines = doc["lines"]
    if args.limit:
        lines = lines[: args.limit]

    for i, line in enumerate(lines, 1):
        character = line["speaker"]
        text = convert_tags(line["text"])
        instruct = line.get("qwen_instruct") or line.get("instruct") or ""
        req = {
            "id": f"line-{i}",
            "text": text,
            "instruction": instruct,
            "ref_audio_path": ref_paths[character],
            "ref_text": REFS[character]["text"],
        }
        t0 = time.time()
        takes = []
        for c in range(max(1, args.candidates)):
            audio, sr = synth(runtime, tokenizer, model, audio_tokenizer, req,
                              args.cfg_scale, args.seed + i * 10 + c)
            f0, _, _ = librosa.pyin(audio, fmin=60, fmax=300, sr=sr,
                                    frame_length=2048)
            f0 = f0[~np.isnan(f0)]
            med = float(np.median(f0)) if len(f0) else 0.0
            v = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20).mean(axis=1)
            sim = float(np.dot(v, ref_vec[character])
                        / (np.linalg.norm(v) * np.linalg.norm(ref_vec[character])))
            takes.append((sim, med, audio))
        takes.sort(key=lambda item: (item[0] < 0.98,
                                     abs(item[1] - ref_f0[character])))
        sim, med, audio = takes[0]
        gen_s = time.time() - t0
        fname = f"{i:03d}_{character.lower()}_{slug(text)}.wav"
        path = os.path.join(args.out, fname)
        sf.write(path, audio, sr)
        dur = len(audio) / sr
        entries.append({
            "index": i,
            "id": line.get("id"),
            "speaker": character,
            "text": text,
            "instruct": instruct,
            "file": path,
            "duration_s": round(dur, 3),
            "f0_hz": round(med, 1),
            "mfcc_sim": round(sim, 4),
            "gen_s": round(gen_s, 3),
        })
        print(f"[{i}/{len(lines)}] {character:6s} {dur:5.2f}s in {gen_s:4.1f}s -> {fname}",
              flush=True)

        if combined_sr is None:
            combined_sr = sr
            gap = np.zeros(int(sr * args.pause_ms / 1000), dtype=np.float32)
        if combined:
            combined.append(gap)
        combined.append(audio)

    all_audio = np.concatenate(combined) if combined else np.zeros(0, np.float32)
    stem = slug(doc.get("title", "drama"), 60)
    all_path = os.path.join(args.out, f"{stem}_breeze.wav")
    sf.write(all_path, all_audio, combined_sr)

    manifest = {
        "input": os.path.abspath(args.lines),
        "model": MODEL,
        "engine": "breeze-tts-2",
        "mode": "voice-design -> voice-direction",
        "cfg_scale": args.cfg_scale,
        "pause_ms": args.pause_ms,
        "refs": {c: {"file": ref_paths[c], "text": REFS[c]["text"],
                     "instruction": REFS[c]["instruction"]} for c in REFS},
        "entries": entries,
        "combined": {"file": all_path,
                     "duration_s": round(len(all_audio) / combined_sr, 3)},
        "total_gen_s": round(time.time() - t_start, 2),
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(f"\ncombined -> {all_path} ({len(all_audio) / combined_sr:.2f}s)")
    print(f"manifest -> {os.path.join(args.out, 'manifest.json')}")
    print(f"peak VRAM {torch.cuda.max_memory_allocated() / 1e9:.2f} GB")


if __name__ == "__main__":
    main()
