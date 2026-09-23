from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.environ.get("HUMAN_QUEUE_HOME", Path.home() / ".human-queue"))
CONFIG_PATH = STATE_DIR / "config.json"
DB_PATH = STATE_DIR / "human-queue.db"


def generate_token() -> str:
    return "hq_" + secrets.token_urlsafe(24)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return config


def ensure_config(*, host: str = "127.0.0.1", port: int = 7482, force: bool = False) -> dict[str, Any]:
    existing = load_config()
    if existing and not force:
        return existing
    config = {
        "version": 1,
        "host": host,
        "port": port,
        "token": generate_token(),
        "db": str(DB_PATH),
    }
    return save_config(config)


def gateway_token() -> str | None:
    return os.environ.get("HUMAN_QUEUE_TOKEN") or load_config().get("token")


def gateway_url() -> str:
    env = os.environ.get("HUMAN_QUEUE_URL")
    if env:
        return env.rstrip("/")
    cfg = load_config()
    host = cfg.get("host", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{int(cfg.get('port', 7482))}"


def db_path() -> str:
    return os.environ.get("HUMAN_QUEUE_DB") or load_config().get("db") or str(DB_PATH)
