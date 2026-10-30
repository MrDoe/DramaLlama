from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import (
    BREEZE_DE_URL,
    DATA_DIR,
    DB_PATH,
    HOST,
    PORT,
    PROJECTS_DIR,
    SUNO_BRIDGE_URL,
    ensure_dirs,
)
from .handlers import default_handlers
from .jobs import JobQueue
from .worker import Worker


def _queue() -> JobQueue:
    ensure_dirs()
    return JobQueue(DB_PATH)


def cmd_init(_: argparse.Namespace) -> int:
    ensure_dirs()
    queue = _queue()
    print(f"data:     {DATA_DIR}")
    print(f"projects: {PROJECTS_DIR}")
    print(f"queue:    {DB_PATH}")
    print(f"jobs:     {queue.stats()}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    ensure_dirs()
    bridge_proc = _ensure_suno_bridge() if args.suno_bridge else None

    if args.worker:
        queue = _queue()
        worker = Worker(
            queue,
            default_handlers(),
            poll_interval=args.poll,
            recover_stale_after_s=args.stale_after,
        )
        thread = threading.Thread(target=worker.run_forever, daemon=True)
        thread.start()
        print(f"worker started in-process (poll {args.poll}s)")

    try:
        uvicorn.run(
            create_app(),
            host=args.host,
            port=args.port,
            log_level=args.log_level,
        )
    finally:
        if bridge_proc is not None and bridge_proc.poll() is None:
            bridge_proc.terminate()
    return 0


def _suno_bridge_port() -> int:
    """HTTP port of the Suno sidecar (env override wins, else SUNO_BRIDGE_URL)."""
    if os.environ.get("SUNO_BRIDGE_PORT"):
        return int(os.environ["SUNO_BRIDGE_PORT"])
    try:
        return urlparse(SUNO_BRIDGE_URL).port or 9430
    except ValueError:
        return 9430


def _suno_bridge_healthy(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as response:
            return response.status == 200
    except Exception:
        return False


def _tcp_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _ensure_suno_bridge() -> subprocess.Popen | None:
    """Start the BetterSuno sidecar if needed. Best-effort: never fail `serve`.

    Returns the child process when this command owns it (caller terminates it
    on shutdown), else None when the bridge was already up or could not start.
    """
    port = _suno_bridge_port()
    if _suno_bridge_healthy(port):
        print(f"Suno bridge already running on http://127.0.0.1:{port}")
        return None
    root = Path(__file__).resolve().parents[2]
    script = root / "suno-bridge" / "server.mjs"
    if not script.exists():
        print(f"warning: {script} not found; Suno engine will be unavailable",
              file=sys.stderr)
        return None
    ws_port = int(os.environ.get("MCP_WS_PORT", "9423"))
    if _tcp_in_use(ws_port):
        print(
            f"warning: ws://127.0.0.1:{ws_port} is already owned (likely the opencode "
            "bettersuno-mcp server); skipping Suno bridge auto-start. "
            "Stop the other owner or run `drama-llama suno-bridge` manually.",
            file=sys.stderr,
        )
        return None
    env = dict(os.environ)
    env.setdefault("BETTERSUNO_MCP_DIR", str(Path.home() / "code" / "bettersuno-mcp"))
    env.setdefault("SUNO_BRIDGE_PORT", str(port))
    log_path = DATA_DIR / "suno-bridge.log"
    try:
        log_file = open(log_path, "ab")
    except OSError as exc:
        print(f"warning: cannot open {log_path}: {exc}; Suno bridge not started",
              file=sys.stderr)
        return None
    try:
        proc = subprocess.Popen(
            ["node", str(script)], env=env, stdout=log_file, stderr=subprocess.STDOUT
        )
        log_file.close()  # child keeps its own copy; parent must not hold it open
    except FileNotFoundError:
        print("warning: node not found; Suno engine will be unavailable",
              file=sys.stderr)
        log_file.close()
        return None
    for _ in range(50):
        if _suno_bridge_healthy(port):
            print(f"Suno bridge on http://127.0.0.1:{port} (log {log_path})")
            return proc
        if proc.poll() is not None:
            print(
                f"warning: Suno bridge exited during startup; see {log_path}. "
                "Suno engine will be unavailable.",
                file=sys.stderr,
            )
            return None
        time.sleep(0.2)
    print(
        f"warning: Suno bridge did not answer on :{port} within 10s; "
        f"see {log_path}. Continuing without it.",
        file=sys.stderr,
    )
    return proc


def cmd_worker(args: argparse.Namespace) -> int:
    queue = _queue()
    worker = Worker(
        queue,
        default_handlers(),
        poll_interval=args.poll,
        recover_stale_after_s=args.stale_after,
    )
    if args.once:
        worked = worker.run_once()
        print("ran one job" if worked else "queue empty")
        return 0
    print(f"worker {worker.worker_id} watching {DB_PATH}")
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
        print("stopped")
    return 0


def cmd_suno_bridge(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    script = root / "suno-bridge" / "server.mjs"
    if not script.exists():
        print(f"error: {script} not found", file=sys.stderr)
        return 1
    env = dict(os.environ)
    env.setdefault("BETTERSUNO_MCP_DIR", str(Path.home() / "code" / "bettersuno-mcp"))
    env.setdefault("SUNO_BRIDGE_PORT", str(args.port))
    print(f"Suno bridge on http://127.0.0.1:{args.port} (BetterSuno {env['BETTERSUNO_MCP_DIR']})")
    try:
        return subprocess.call(["node", str(script)], env=env)
    except KeyboardInterrupt:
        return 0


def cmd_breeze_de(args: argparse.Namespace) -> int:
    import os

    binary = Path(args.binary).expanduser()
    if not binary.exists():
        # Fall back to a sibling extracted directory or PATH lookup.
        candidates = [
            Path.home() / "code" / "breeze-tts-cpp" / "breeze-tts-2-v0.1.3-linux-x64-vulkan" / "breeze-server",
            Path("breeze-server"),
        ]
        for cand in candidates:
            if cand.exists():
                binary = cand
                break
    if not binary.exists():
        print(f"error: breeze-server binary not found at {binary}", file=sys.stderr)
        print("download from https://github.com/HoppouAI/Breeze-TTS-2.cpp/releases", file=sys.stderr)
        return 1
    model = Path(args.model).expanduser()
    if not model.exists():
        print(f"error: model not found at {model}", file=sys.stderr)
        return 1
    port = args.port
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    if "BREEZE_DE_VK_DEVICE" in env:
        env["GGML_VK_DEVICE"] = env["BREEZE_DE_VK_DEVICE"]
    elif "GGML_VK_DEVICE" not in env:
        env["GGML_VK_DEVICE"] = "1"
    print(f"Breeze-DE sidecar on {url} (model {model})")
    print(f"set BREEZE_DE_URL={url} for Drama Llama")
    try:
        return subprocess.call(
            [str(binary), str(model), "--port", str(port), "--host", "127.0.0.1"],
            env=env,
        )
    except KeyboardInterrupt:
        return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    queue = _queue()
    for job in queue.list_jobs(args.status, args.limit):
        print(
            f"{job.id}  {job.status:9s} {job.type:20s} "
            f"{job.progress:5.1%}  {job.message[:60]}"
        )
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    queue = _queue()
    payload = json.loads(args.payload) if args.payload else {}
    job = queue.enqueue(args.type, payload, priority=args.priority)
    print(job.id)
    return 0


def cmd_demo_render(args: argparse.Namespace) -> int:
    from .models import StemRenderRequest

    ensure_dirs()
    project_dir = PROJECTS_DIR / "demo"
    project_dir.mkdir(parents=True, exist_ok=True)
    request = StemRenderRequest(
        project_id="demo",
        stem_id="s1-mara",
        speaker="MARA",
        text=args.text,
        instruction="Cold, controlled, barely containing irritation.",
        output_dir=str(project_dir),
        candidates=args.candidates,
        seed=args.seed,
    )
    queue = _queue()
    job = queue.enqueue("stem.render.fake", request.model_dump(mode="json"))
    print(f"enqueued {job.id} -> {project_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drama-llama")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create data directories and the job database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("serve", help="run the FastAPI server")
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--log-level", default="info")
    p.add_argument("--worker", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument(
        "--suno-bridge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="auto-start the BetterSuno sidecar (:9430) with serve",
    )
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--stale-after", type=float, default=300)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("worker", help="run the job worker")
    p.add_argument("--once", action="store_true")
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--stale-after", type=float, default=300)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("suno-bridge", help="run the BetterSuno HTTP sidecar")
    p.add_argument("--port", type=int, default=9430)
    p.set_defaults(func=cmd_suno_bridge)

    p = sub.add_parser("breeze-de", help="run the German Breeze (Breeze-TTS-2.cpp) sidecar")
    p.add_argument("--port", type=int, default=8137)
    p.add_argument(
        "--binary",
        default=str(Path.home() / "code" / "breeze-tts-cpp" / "breeze-tts-2-v0.1.3-linux-x64-vulkan" / "breeze-server"),
    )
    p.add_argument(
        "--model",
        default=str(Path.home() / "models" / "Articulatio-DE-Stream-TTS-v1.5-Q8_0.gguf"),
    )
    p.set_defaults(func=cmd_breeze_de)

    p = sub.add_parser("jobs", help="list jobs")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("enqueue", help="enqueue a raw job")
    p.add_argument("type")
    p.add_argument("payload", nargs="?", default="{}")
    p.add_argument("--priority", type=int, default=0)
    p.set_defaults(func=cmd_enqueue)

    p = sub.add_parser("demo-render", help="enqueue a fake stem render")
    p.add_argument("--text", default="You're late. Four days after the funeral.")
    p.add_argument("--candidates", type=int, default=2)
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_demo_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
