from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from pathlib import Path

import httpx
import uvicorn

from .config import CONFIG_PATH, channel_configs, db_path, ensure_config, gateway_token, gateway_url, generate_channel_secret, load_config, remove_channel, save_channel, save_config, generate_token


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
        print("Open Codex and run /hooks once to trust the human:// hook definition.")
        return
    if args.provider == "cursor":
        from .connectors.cursor import install_cursor_hooks
        result = install_cursor_hooks()
        print("human:// connected to Cursor")
        print(f" hooks      {result['hooks_path']}")
        print(f" detected   {'yes' if result['cursor_detected'] else 'not on PATH'}")
        print(" observe    session + prompt + final response")
        print(" gate       high-risk beforeShellExecution")
        print(" roundtrip  native permission allow/deny/ask")
        return
    if args.provider == "claude":
        from .connectors.claude import install_claude_hooks
        result = install_claude_hooks()
        print("human:// connected to Claude Code")
        print(f" settings   {result['settings_path']}")
        print(f" detected   {'yes' if result['claude_detected'] else 'not on PATH'}")
        print(" events     PermissionRequest + session/prompt/stop observers")
        print(" roundtrip  native allow/deny")
        return
    if args.provider == "opencode":
        from .connectors.opencode import install_opencode_plugin
        result = install_opencode_plugin()
        print("human:// connected to OpenCode V2")
        print(f" plugin     {result['plugin_path']}")
        print(" observe    prompt admission + permission boundary")
        print(" roundtrip  ctx.permission.hook evaluate → allow/deny")
        print("\nRestart OpenCode so the global plugin is loaded.")
        return
    raise SystemExit(f"unsupported connector: {args.provider}")


def verify(args: argparse.Namespace) -> None:
    if args.provider != "codex":
        raise SystemExit(f"unsupported verification provider: {args.provider}")

    from .connectors.codex import codex_readiness

    status = codex_readiness()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return

    print("human:// Codex verification")
    print(f" binary       {'yes' if status['codex_detected'] else 'missing'}")
    if status.get("codex_version"):
        print(f" version      {status['codex_version']}")
    print(f" gateway      {'online' if status['gateway_online'] else 'offline'}  {status['gateway_url']}")
    print(f" hook file    {'present' if status['hooks_file_present'] else 'missing'}  {status['hooks_path']}")
    print(f" permission   {'installed' if status['permission_hook_present'] else 'missing'}")
    print(
        " observers    "
        + (", ".join(status["observer_events_present"]) if status["observer_events_present"] else "missing")
    )
    print(f" runtime      {'matches' if status['hook_command_matches_current_runtime'] else 'mismatch/unknown'}")
    print(f" discovered   {'yes' if status['native_hook_discovered'] else 'no'} by Codex app-server")
    print(f" hook trust   {status['trust_status']}")
    if status.get("native_current_hash"):
        print(f" hook hash    {status['native_current_hash']}")
    print(" host E2E     NOT YET PROVEN")

    if status["problems"]:
        print("\nProblems:")
        for problem in status["problems"]:
            print(" - " + problem)

    if status["ready_for_real_host_probe"]:
        print("\nReady for the final native proof:")
        print("  1. Open Codex and run /hooks; trust the human:// hook if it is new or changed.")
        print("  2. Trigger one action that produces a native PermissionRequest.")
        print("  3. Resolve that boundary in human://.")
        print("  4. Confirm the exact Codex tool call continues or is denied.")
    else:
        print("\nNot ready for a real-host proof yet.")

def disconnect(args: argparse.Namespace) -> None:
    if args.provider == "codex":
        from .connectors.codex import uninstall_codex_hooks
        result = uninstall_codex_hooks()
        print(json.dumps(result, indent=2))
        return
    if args.provider == "cursor":
        from .connectors.cursor import uninstall_cursor_hooks
        result = uninstall_cursor_hooks()
        print(json.dumps(result, indent=2))
        return
    if args.provider == "claude":
        from .connectors.claude import uninstall_claude_hooks
        result = uninstall_claude_hooks()
        print(json.dumps(result, indent=2))
        return
    if args.provider == "opencode":
        from .connectors.opencode import uninstall_opencode_plugin
        result = uninstall_opencode_plugin()
        print(json.dumps(result, indent=2))
        return
    raise SystemExit(f"unsupported connector: {args.provider}")


def connector_hook(args: argparse.Namespace) -> None:
    if args.mode.startswith("codex-"):
        from .connectors.codex import hook_main
        raise SystemExit(hook_main(args.mode))
    if args.mode.startswith("cursor-"):
        from .connectors.cursor import hook_main
        raise SystemExit(hook_main(args.mode))
    if args.mode.startswith("claude-"):
        from .connectors.claude import hook_main
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



def channel_add(args: argparse.Namespace) -> None:
    ensure_config()

    if args.type == "webhook":
        url = args.target
        if not url:
            raise SystemExit("webhook channel requires a target URL")
        secret = args.secret or generate_channel_secret()
        save_channel(args.name, {
            "type": "webhook",
            "url": url,
            "secret": secret,
            "enabled": True,
        })
        print(f"channel {args.name} added")
        print(" type    webhook")
        print(f" url     {url}")
        print(f" secret  {secret}")
        print("\nThe receiver should verify X-Human-Channel-Signature.")
        print("Send signed button actions back to:")
        print(f"  {gateway_url()}/channels/{args.name}/resolve/<request_id>")
        return

    if args.type == "telegram":
        bot_token = args.bot_token or os.environ.get("HUMAN_QUEUE_TELEGRAM_BOT_TOKEN")
        chat_id = args.chat_id or os.environ.get("HUMAN_QUEUE_TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            raise SystemExit(
                "telegram channel requires --bot-token and --chat-id "
                "(or HUMAN_QUEUE_TELEGRAM_BOT_TOKEN / HUMAN_QUEUE_TELEGRAM_CHAT_ID)"
            )
        save_channel(args.name, {
            "type": "telegram",
            "bot_token": bot_token,
            "chat_id": str(chat_id),
            "enabled": True,
        })
        print(f"channel {args.name} added")
        print(" type    telegram")
        print(f" chat    {chat_id}")
        print(" token   stored privately in ~/.human-queue/config.json")
        print("\nStart the outbound-only long-poll worker:")
        print(f"  humanq channel run {args.name}")
        return

    if args.type == "slack":
        app_token = args.app_token or os.environ.get("HUMAN_QUEUE_SLACK_APP_TOKEN")
        bot_token = args.bot_token or os.environ.get("HUMAN_QUEUE_SLACK_BOT_TOKEN")
        channel_id = args.channel_id or os.environ.get("HUMAN_QUEUE_SLACK_CHANNEL_ID")
        if not app_token or not bot_token or not channel_id:
            raise SystemExit(
                "slack channel requires --app-token, --bot-token and --channel-id "
                "(or HUMAN_QUEUE_SLACK_APP_TOKEN / HUMAN_QUEUE_SLACK_BOT_TOKEN / "
                "HUMAN_QUEUE_SLACK_CHANNEL_ID)"
            )
        save_channel(args.name, {
            "type": "slack",
            "app_token": app_token,
            "bot_token": bot_token,
            "channel_id": str(channel_id),
            "enabled": True,
        })
        print(f"channel {args.name} added")
        print(" type    slack")
        print(f" channel {channel_id}")
        print(" tokens  stored privately in ~/.human-queue/config.json")
        print("\nStart the outbound-only Socket Mode worker:")
        print(f"  humanq channel run {args.name}")
        return

    raise SystemExit(f"unsupported channel type: {args.type}")


def channel_list(args: argparse.Namespace) -> None:
    configs = channel_configs()
    if args.json:
        safe = {}
        for name, cfg in configs.items():
            item = dict(cfg)
            if item.get("secret"):
                item["secret"] = "***"
            if item.get("bot_token"):
                item["bot_token"] = "***"
            if item.get("app_token"):
                item["app_token"] = "***"
            safe[name] = item
        print(json.dumps(safe, indent=2))
        return
    if not configs:
        print("No third-party channels configured.")
        return
    for name, cfg in configs.items():
        state = "enabled" if cfg.get("enabled", True) else "disabled"
        destination = cfg.get("url") or (
            f"chat:{cfg.get('chat_id')}" if cfg.get("type") == "telegram"
            else f"channel:{cfg.get('channel_id')}" if cfg.get("type") == "slack"
            else "-"
        )
        print(f"{name:<18} {cfg.get('type','?'):<10} {destination}  {state}")


def channel_run(args: argparse.Namespace) -> None:
    cfg = channel_configs().get(args.name)
    if not cfg:
        raise SystemExit(f"channel not found: {args.name}")
    if cfg.get("type") == "telegram":
        from .channels.telegram import run_long_poll
        raise SystemExit(run_long_poll(args.name))
    if cfg.get("type") == "slack":
        from .channels.slack import run_socket_mode
        raise SystemExit(run_socket_mode(args.name))
    raise SystemExit(f"channel type {cfg.get('type')} does not need a local worker")


def channel_remove(args: argparse.Namespace) -> None:
    removed = remove_channel(args.name)
    print("removed" if removed else "not found")



def source_add(args: argparse.Namespace) -> None:
    cfg = load_config() or ensure_config()
    source_id = args.id or f"{args.provider}:{args.account}"
    payload = {
        "provider": args.provider,
        "account": args.account,
        "mode": args.mode or (
            "gateway-ws" if args.provider == "openclaw"
            else "muse-msp" if args.provider == "muse"
            else "native-connector"
        ),
        "endpoint": args.endpoint,
        "enabled": True,
        "config": {
            "credential_env": args.credential_env,
            "host": args.host,
        },
    }
    try:
        r = httpx.post(
            gateway_url() + f"/v1/presence/sources/{source_id}",
            headers=_auth_headers(),
            json=payload,
            timeout=3,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"Cannot register source with {gateway_url()}: {exc}")
        raise SystemExit(1)
    print(f"source {source_id} registered")
    print(f" provider  {args.provider}")
    print(f" account   {args.account}")
    print(f" mode      {payload['mode']}")
    if args.endpoint:
        print(f" endpoint  {args.endpoint}")
    if args.credential_env:
        print(f" secret    env:{args.credential_env}")


def source_list(args: argparse.Namespace) -> None:
    try:
        r = httpx.get(
            gateway_url() + "/v1/presence/sources",
            headers=_auth_headers(),
            timeout=3,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"Cannot read sources from {gateway_url()}: {exc}")
        raise SystemExit(1)
    sources = r.json().get("sources", [])
    if args.json:
        print(json.dumps(sources, indent=2, ensure_ascii=False))
        return
    if not sources:
        print("No presence sources registered.")
        return
    for row in sources:
        print(
            f"{row['id']:<28} {row['provider']:<12} {row['account']:<18} "
            f"{row['mode']:<18} {row.get('endpoint') or '-'}"
        )


def presence_list(args: argparse.Namespace) -> None:
    params = {"limit": args.limit}
    if args.state:
        params["state"] = args.state
    try:
        r = httpx.get(
            gateway_url() + "/v1/presence/sessions",
            headers=_auth_headers(),
            params=params,
            timeout=3,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"Cannot read presence from {gateway_url()}: {exc}")
        raise SystemExit(1)
    rows = r.json().get("sessions", [])
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("No agent sessions observed.")
        return
    for row in rows:
        print(
            f"{row['state']:<16} {row['provider']:<12} {row['account']:<14} "
            f"{row['session_id']:<28} {(row.get('current_action') or row.get('title') or '-')[:60]}"
        )


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

    p_dashboard = sub.add_parser("dashboard", help="open the local human:// UI")
    p_dashboard.add_argument("--no-open", action="store_true")
    p_dashboard.set_defaults(func=dashboard)

    p_connect = sub.add_parser("connect", help="install an editor/agent connector")
    p_connect.add_argument("provider", choices=["codex", "cursor", "claude", "opencode"])
    p_connect.set_defaults(func=connect)

    p_verify = sub.add_parser("verify", help="check readiness for a real native connector proof")
    p_verify.add_argument("provider", choices=["codex"])
    p_verify.add_argument("--json", action="store_true")
    p_verify.set_defaults(func=verify)

    p_disconnect = sub.add_parser("disconnect", help="remove an editor/agent connector")
    p_disconnect.add_argument("provider", choices=["codex", "cursor", "claude", "opencode"])
    p_disconnect.set_defaults(func=disconnect)

    p_sessions = sub.add_parser("sessions", help="show sessions observed from connected agents")
    p_sessions.add_argument("--limit", type=int, default=30)
    p_sessions.add_argument("--json", action="store_true")
    p_sessions.set_defaults(func=sessions)

    p_source = sub.add_parser("source", help="register multi-account agent sources")
    ss = p_source.add_subparsers(dest="source_command")
    p_source_add = ss.add_parser("add", help="add one account/gateway source")
    p_source_add.add_argument("provider", choices=["openclaw", "muse", "codex", "claude", "cursor", "opencode"])
    p_source_add.add_argument("account")
    p_source_add.add_argument("--id")
    p_source_add.add_argument("--mode")
    p_source_add.add_argument("--endpoint")
    p_source_add.add_argument("--host")
    p_source_add.add_argument("--credential-env")
    p_source_add.set_defaults(func=source_add)
    p_source_list = ss.add_parser("list", help="list registered sources")
    p_source_list.add_argument("--json", action="store_true")
    p_source_list.set_defaults(func=source_list)

    p_presence = sub.add_parser("presence", help="show normalized session state across all sources")
    p_presence.add_argument("--state", choices=["idle","running","waiting_human","waiting_external","completed","failed","offline","unknown"])
    p_presence.add_argument("--limit", type=int, default=100)
    p_presence.add_argument("--json", action="store_true")
    p_presence.set_defaults(func=presence_list)

    p_channel = sub.add_parser("channel", help="project human:// into a third-party channel")
    chs = p_channel.add_subparsers(dest="channel_command")
    p_channel_add = chs.add_parser("add", help="add a third-party channel")
    p_channel_add.add_argument("type", choices=["webhook", "telegram", "slack"])
    p_channel_add.add_argument("name")
    p_channel_add.add_argument("target", nargs="?", help="webhook URL")
    p_channel_add.add_argument("--secret")
    p_channel_add.add_argument("--bot-token")
    p_channel_add.add_argument("--chat-id")
    p_channel_add.add_argument("--app-token")
    p_channel_add.add_argument("--channel-id")
    p_channel_add.set_defaults(func=channel_add)
    p_channel_list = chs.add_parser("list", help="list configured channels")
    p_channel_list.add_argument("--json", action="store_true")
    p_channel_list.set_defaults(func=channel_list)
    p_channel_run = chs.add_parser("run", help="run a local channel worker")
    p_channel_run.add_argument("name")
    p_channel_run.set_defaults(func=channel_run)
    p_channel_remove = chs.add_parser("remove", help="remove a configured channel")
    p_channel_remove.add_argument("name")
    p_channel_remove.set_defaults(func=channel_remove)

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
