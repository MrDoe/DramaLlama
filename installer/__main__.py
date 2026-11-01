"""``python -m installer`` — GUI wizard when possible, CLI otherwise."""

from __future__ import annotations

from .entry import main

if __name__ == "__main__":
    raise SystemExit(main())
