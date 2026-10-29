from __future__ import annotations

import argparse
import json
import sys
import threading

from .config import DATA_DIR, DB_PATH, HOST, PORT, PROJECTS_DIR, ensure_dirs
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

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


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
    parser = argparse.ArgumentParser(prog="drama-studio")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create data directories and the job database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("serve", help="run the FastAPI server")
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--log-level", default="info")
    p.add_argument("--worker", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--stale-after", type=float, default=300)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("worker", help="run the job worker")
    p.add_argument("--once", action="store_true")
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--stale-after", type=float, default=300)
    p.set_defaults(func=cmd_worker)

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
