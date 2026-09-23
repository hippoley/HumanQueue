from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NativeHandle:
    provider: str
    session_id: str
    turn_id: str | None = None
    tool_use_id: str | None = None
    transcript_locator: str | None = None
    resume_kind: str = "native_hook_return"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextCapsule:
    provider: str
    session_id: str
    cwd: str | None = None
    model: str | None = None
    turn_id: str | None = None
    event_name: str | None = None
    latest_user_intent: str | None = None
    latest_assistant_message: str | None = None
    tool_name: str | None = None
    tool_input: Any = None
    transcript_locator: str | None = None
    native_handle: NativeHandle | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.native_handle:
            data["native_handle"] = self.native_handle.to_dict()
        return data
