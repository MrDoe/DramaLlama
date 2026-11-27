"""Convert CosyVoice 3's reinforcement-tuned weights to GGUF.

    pip install gguf==0.17.1 transformers==4.57.3 PyYAML==6.0.3   # plus torch
    python scripts/convert_cosyvoice_rl.py

The installer does this by itself when the sources are on the machine; this is
the same step for a source checkout. The published GGUF files carry the base
language model; nobody ships the RL checkpoint (``llm.rl.pt``, Apache-2.0, in
the official repository) converted. In measurements with the full-precision
speech tokenizer it said lines correctly about as often as the base model, but
never produced a silent clip. The sources are the official snapshot in the
Hugging Face cache (about 4.4 GB; ``hf download`` it at the revision in
installer/pins.json). The app prefers the result over any other CosyVoice model.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from installer.core import cosyvoice_rl
from installer.core.paths import legacy_cosyvoice_models, load_pins
from installer.core.util import InstallerError


def main(argv: list[str] | None = None) -> int:
    meta = load_pins()["cosyvoice"]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, default=legacy_cosyvoice_models() / meta["rl"]["file"]
    )
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "cosyvoice-rl")
    args = parser.parse_args(argv)

    missing = [
        name
        for name in ("torch", "transformers", "gguf", "yaml")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise SystemExit(f"this Python lacks {', '.join(missing)}; install them first")
    for pin in meta["rl"]["packages"]:
        name, _, wanted = pin.partition("==")
        found = importlib.metadata.version(name)
        if found != wanted:
            print(
                f"warning: {name} {found} is installed, not {wanted}; the result may "
                "not match the pinned hash",
                file=sys.stderr,
            )

    snapshot = cosyvoice_rl.find_sources(meta, None)
    if snapshot is None:
        upstream = meta["upstream"]
        raise SystemExit(
            "the pinned sources are missing or differ; fetch them with: "
            f"hf download {upstream['repo']} --revision {upstream['revision']}"
        )
    try:
        matches = cosyvoice_rl.convert(
            Path(sys.executable), meta, snapshot, args.output.resolve(), args.work.resolve()
        )
    except InstallerError as exc:
        raise SystemExit(str(exc)) from exc
    note = "" if matches else " (differs from the pinned hash)"
    print(f"wrote {args.output}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
