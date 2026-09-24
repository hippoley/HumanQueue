from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def _config_root() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "opencode"
    return Path.home() / ".config" / "opencode"


def plugin_source() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "opencode-human-queue.ts"


def install_opencode_plugin(config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or _config_root()
    plugins = root / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    target = plugins / "human-queue.ts"

    if target.exists():
        backup = target.with_suffix(".ts.humanq.bak")
        if not backup.exists():
            shutil.copy2(target, backup)

    source = plugin_source()
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "provider": "opencode",
        "plugin_path": str(target),
        "config_root": str(root),
        "installed": True,
    }


def uninstall_opencode_plugin(config_root: Path | None = None) -> dict[str, Any]:
    root = config_root or _config_root()
    target = root / "plugins" / "human-queue.ts"
    if not target.exists():
        return {"provider": "opencode", "removed": False, "plugin_path": str(target)}
    target.unlink()
    return {"provider": "opencode", "removed": True, "plugin_path": str(target)}
