from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from pathlib import Path

import httpx
import uvicorn

from .config import CONFIG_PATH, db_path, ensure_config, gateway_token, gateway_url, load_config, save_config, generate_token


def _open_later(url: str) -> None:
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()


def _auth_headers() -> dict[str, str]:
    token = gateway_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def onboard(args: argparse.Namespace) -> None:
    cfg = ensure_config(host=args.host, port=args.port, force=args.force)
    if args.demo:
        from app.demo import seed_wow
        from app.store import Store
        seed_wow(Store(cfg["db"]), reset=True)
    print("\n  human://  local gateway initialized")
    print(f"  State      {CONFIG_PATH.parent}")
    print(f"  Dashboard  {gateway_url()}")
    print(f"  Token      {cfg['token']}")
    print("\n  Next:")
    print("    humanq gateway run")
    print("    humanq dashboard")
    print("    humanq connect codex")
    print("\n  Agent endpoint:")
    print(f"    {gateway_url()}/v1/human\n")


def _run_gateway(host: str | None, port: int | None, reload: bool = False) -> None:
    cfg = load_config() or ensure_config()
    bind = host or cfg.get("host", "127.0.0.1")
    listen = int(port or cfg.get("port", 7482))
    os.environ["HUMAN_QUEUE_DB"] = db_path()
    uvicorn.run("app.main:app", host=bind, port=listen, reload=reload)


def serve(args: argparse.Namespace) -> None:
    _run_gateway(args.host, args.port, args.reload)


def gateway_run(args: argparse.Namespace) -> None:
    _run_gateway(args.host, args.port, args.reload)


def gateway_status(_: argparse.Namespace) -> None:
    url = gateway_url()
    try:
        r = httpx.get(url + "/health", timeout=2)
        r.raise_for_status()
        print(f"human:// gateway online  {url}")
    except Exception as exc:
        print(f"human:// gateway offline {url}")
        print(f"reason: {exc}")
        raise SystemExit(1)


def dashboard(args: argparse.Namespace) -> None:
    cfg = load_config() or ensure_config()
    url = gateway_url()
    print(f"Dashboard  {url}")
    print(f"Token      {cfg['token']}")
    print("The browser receives the token in the URL fragment once, then keeps it in session storage.")
    if not args.no_open:
        _open_later(url + "/#token=" + cfg["token"])


def doctor(_: argparse.Namespace) -> None:
    cfg = load_config()
    issues = []
    if not cfg:
        issues.append("not onboarded: run humanq onboard")
    else:
        p = Path(cfg.get("db", db_path()))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            probe = p.parent / ".write-test"
            probe.write_text("ok")
            probe.unlink()
        except Exception as exc:
            issues.append(f"state directory is not writable: {exc}")
        if not str(cfg.get("token", "")).startswith("hq_"):
            issues.append("gateway token is missing or malformed")
    if issues:
        print("human:// doctor found problems:")
        for x in issues:
            print(" - " + x)
        raise SystemExit(1)
    print("human:// doctor: OK")
    print(f" config  {CONFIG_PATH}")
    print(f" db      {cfg['db']}")
    print(f" bind    {cfg['host']}:{cfg['port']}")


def rotate_token(_: argparse.Namespace) -> None:
    cfg = load_config() or ensure_config()
    cfg["token"] = generate_token()
    save_config(cfg)
    print("New gateway token:")
    print(cfg["token"])
    print("Restart the gateway so all clients use the new token.")


def demo(args: argparse.Namespace) -> None:
    cfg = load_config() or ensure_config()
    from app.demo import seed_wow
    from app.store import Store
    seed_wow(Store(cfg["db"]), reset=True)
    url = gateway_url()
    print(f"Demo seeded → {url}")
    if not args.no_open:
        _open_later(url + "/#token=" + cfg["token"])
    _run_gateway(args.host, args.port, False)


def seed(args: argparse.Namespace) -> None:
    cfg = load_config() or ensure_config()
    from app.demo import seed_wow
    from app.store import Store
    ids = seed_wow(Store(cfg["db"]), reset=args.reset)
    print(f"Seeded {len(ids)} visible machine→human interrupts into {cfg['db']}")


def connect(args: argparse.Namespace) -> None:
    ensure_config()
    if args.provider == "codex":
        from .connectors.codex import install_codex_hooks
        result = install_codex_hooks()
        print("human:// connected to Codex")
        print(f" hooks      {result['hooks_path']}")
        print(f" detected   {'yes' if result['codex_detected'] else 'not on PATH'}")
        print(" events     PermissionRequest + session/prompt/stop observers")
        print(" roundtrip  native allow/deny")
        print("\nCodex requires review of new non-managed hooks.")
        print("Open Codex and run /hooks once to trust the Human Queue hook definition.")
        return
    raise SystemExit(f"unsupported connector: {args.provider}")


def disconnect(args: argparse.Namespace) -> None:
    if args.provider == "codex":
        from .connectors.codex import uninstall_codex_hooks
        result = uninstall_codex_hooks()
        print(json.dumps(result, indent=2))
        return
    raise SystemExit(f"unsupported connector: {args.provider}")


def connector_hook(args: argparse.Namespace) -> None:
    if args.mode.startswith("codex-"):
        from .connectors.codex import hook_main
        raise SystemExit(hook_main(args.mode))
    raise SystemExit(f"unknown hook mode: {args.mode}")


def sessions(args: argparse.Namespace) -> None:
    try:
        r = httpx.get(
            gateway_url() + "/v1/connectors/sessions",
            params={"limit": args.limit},
            headers=_auth_headers(),
            timeout=3,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"Cannot read connector sessions from {gateway_url()}: {exc}")
        raise SystemExit(1)
    rows = r.json().get("sessions", [])
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("No connected agent sessions observed yet.")
        return
    for row in rows:
        print(
            f"{row['provider']:<10} {row['status']:<7} "
            f"{row['session_id']:<28} {row.get('last_turn_id') or '-':<18} "
            f"{row.get('cwd') or '-'}"
        )


def mcp_serve(_: argparse.Namespace) -> None:
    from .mcp_server import run_stdio
    raise SystemExit(run_stdio())


def main() -> None:
    parser = argparse.ArgumentParser(prog="humanq", description="Self-hosted human:// gateway.")
    sub = parser.add_subparsers(dest="command")

    p_onboard = sub.add_parser("onboard", help="create local state and a gateway token")
    p_onboard.add_argument("--host", default="127.0.0.1")
    p_onboard.add_argument("--port", type=int, default=7482)
    p_onboard.add_argument("--force", action="store_true")
    p_onboard.add_argument("--demo", action="store_true")
    p_onboard.set_defaults(func=onboard)

    p_gateway = sub.add_parser("gateway", help="run or inspect the local gateway")
    gs = p_gateway.add_subparsers(dest="gateway_command")
    p_run = gs.add_parser("run", help="run the gateway")
    p_run.add_argument("--host")
    p_run.add_argument("--port", type=int)
    p_run.add_argument("--reload", action="store_true")
    p_run.set_defaults(func=gateway_run)
    p_status = gs.add_parser("status", help="check gateway health")
    p_status.set_defaults(func=gateway_status)

    p_dashboard = sub.add_parser("dashboard", help="open the local Human Queue UI")
    p_dashboard.add_argument("--no-open", action="store_true")
    p_dashboard.set_defaults(func=dashboard)

    p_connect = sub.add_parser("connect", help="install an editor/agent connector")
    p_connect.add_argument("provider", choices=["codex"])
    p_connect.set_defaults(func=connect)

    p_disconnect = sub.add_parser("disconnect", help="remove an editor/agent connector")
    p_disconnect.add_argument("provider", choices=["codex"])
    p_disconnect.set_defaults(func=disconnect)

    p_sessions = sub.add_parser("sessions", help="show sessions observed from connected agents")
    p_sessions.add_argument("--limit", type=int, default=30)
    p_sessions.add_argument("--json", action="store_true")
    p_sessions.set_defaults(func=sessions)

    p_connector = sub.add_parser("connector", help=argparse.SUPPRESS)
    cs = p_connector.add_subparsers(dest="connector_command")
    p_hook = cs.add_parser("hook", help=argparse.SUPPRESS)
    p_hook.add_argument("mode")
    p_hook.set_defaults(func=connector_hook)

    p_mcp = sub.add_parser("mcp", help="run the human.ask MCP bridge")
    ms = p_mcp.add_subparsers(dest="mcp_command")
    p_mcp_serve = ms.add_parser("serve", help="run MCP server over stdio")
    p_mcp_serve.set_defaults(func=mcp_serve)

    p_doctor = sub.add_parser("doctor", help="validate local configuration")
    p_doctor.set_defaults(func=doctor)

    p_token = sub.add_parser("token", help="manage the gateway token")
    ts = p_token.add_subparsers(dest="token_command")
    p_rotate = ts.add_parser("rotate", help="rotate the gateway token")
    p_rotate.set_defaults(func=rotate_token)

    p_demo = sub.add_parser("demo", help="seed demo work and run the gateway")
    p_demo.add_argument("--host")
    p_demo.add_argument("--port", type=int)
    p_demo.add_argument("--no-open", action="store_true")
    p_demo.set_defaults(func=demo)

    p_serve = sub.add_parser("serve", help="compatibility alias for gateway run")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=serve)

    p_seed = sub.add_parser("seed", help="seed demo interrupts")
    p_seed.add_argument("--reset", action="store_true")
    p_seed.set_defaults(func=seed)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()
