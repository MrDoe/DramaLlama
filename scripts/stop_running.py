"""Stop every running Drama Llama instance (serve / worker).

Used as the VS Code pre-launch step so a fresh `serve` can bind its port and
so no stale in-process worker keeps claiming jobs.

Safety notes:
- Matches on the *process cmdline*, never on a shell pattern that could match
  this script's own invocation (self-kill footgun).
- Skips this process and its ancestors.
- Leaves the Suno bridge (`node suno-bridge/server.mjs`) alone; it is a
  separate sidecar and killing it would drop the extension connection.
- Graceful first: SIGTERM, wait, then SIGKILL for stragglers.
"""

from __future__ import annotations

import os
import signal
import sys
import time

GRACE_SECONDS = 10.0
# Substrings that identify a Drama Llama backend process.
MATCHES = ("drama_studio.cli", "drama-llama")
# Commands to stop. `suno-bridge` is deliberately excluded.
MODES = (" serve", " worker")
SKIP = ("stop_running", "suno-bridge", "pytest", "ruff")


def _ancestors() -> set[int]:
    skip: set[int] = set()
    pid = os.getpid()
    while pid > 1:
        skip.add(pid)
        try:
            with open(f"/proc/{pid}/stat") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
            pid = int(fields[1])
        except Exception:
            break
    return skip


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode(errors="replace").strip()


def find_instances() -> list[tuple[int, str]]:
    skip = _ancestors()
    found: list[tuple[int, str]] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in skip:
            continue
        cmd = _cmdline(pid)
        if not cmd or any(token in cmd for token in SKIP):
            continue
        if not any(token in cmd for token in MATCHES):
            continue
        if not any(mode in f" {cmd}" for mode in MODES):
            continue
        found.append((pid, cmd))
    return found


def stop(instances: list[tuple[int, str]]) -> None:
    for pid, cmd in instances:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"stopping {pid}: {cmd[:110]}")
        except ProcessLookupError:
            continue
        except PermissionError:
            print(f"no permission for {pid}")

    deadline = time.monotonic() + GRACE_SECONDS
    remaining = [pid for pid, _ in instances]
    while remaining and time.monotonic() < deadline:
        time.sleep(0.2)
        remaining = [pid for pid in remaining if os.path.exists(f"/proc/{pid}")]

    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"killed {pid} (did not exit in {GRACE_SECONDS:.0f}s)")
        except ProcessLookupError:
            pass


def main() -> int:
    instances = find_instances()
    if not instances:
        print("no running Drama Llama instances")
        return 0
    stop(instances)
    left = find_instances()
    if left:
        print(f"warning: {len(left)} instance(s) still running")
        return 1
    print(f"stopped {len(instances)} instance(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
