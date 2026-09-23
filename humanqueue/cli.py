from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

DEFAULT_DB = Path.home() / ".human-queue" / "human-queue.db"


def _db_path(value: str | None) -> str:
    return value or os.environ.get("HUMAN_QUEUE_DB") or str(DEFAULT_DB)


def _open_later(url: str) -> None:
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()


def demo(args: argparse.Namespace) -> None:
    db = _db_path(args.db)
    os.environ["HUMAN_QUEUE_DB"] = db
    from app.demo import seed_wow
    from app.store import Store

    store = Store(db)
    seed_wow(store, reset=True)
    url = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}"
    print("\n  human://  One queue for everything that needs a human.")
    print(f"  Demo seeded → {url}\n")
    if not args.no_open:
        _open_later(url)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)


def serve(args: argparse.Namespace) -> None:
    os.environ["HUMAN_QUEUE_DB"] = _db_path(args.db)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


def seed(args: argparse.Namespace) -> None:
    db = _db_path(args.db)
    os.environ["HUMAN_QUEUE_DB"] = db
    from app.demo import seed_wow
    from app.store import Store

    ids = seed_wow(Store(db), reset=args.reset)
    print(f"Seeded {len(ids)} visible machine→human interrupts into {db}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="humanq", description="One queue for everything that needs a human.")
    sub = parser.add_subparsers(dest="command")

    p_demo = sub.add_parser("demo", help="seed the wow demo and open the local inbox")
    p_demo.add_argument("--host", default="127.0.0.1")
    p_demo.add_argument("--port", type=int, default=7482)
    p_demo.add_argument("--db")
    p_demo.add_argument("--no-open", action="store_true")
    p_demo.set_defaults(func=demo)

    p_serve = sub.add_parser("serve", help="run the Human Queue server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7482)
    p_serve.add_argument("--db")
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=serve)

    p_seed = sub.add_parser("seed", help="seed demo interrupts")
    p_seed.add_argument("--db")
    p_seed.add_argument("--reset", action="store_true")
    p_seed.set_defaults(func=seed)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()
