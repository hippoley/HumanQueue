from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ChannelCard:
    request_id: str
    title: str
    source: str
    uri: str
    summary: str = ""
    risk_label: str | None = None
    actions: list[str] = field(default_factory=list)
    context_preview: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(Protocol):
    """Projection of the Human Gateway into another human-facing channel.

    A channel never owns request state. It renders a request from the Gateway
    and sends user actions back to the Gateway's resolve endpoint.
    """

    name: str

    def publish(self, card: ChannelCard) -> str:
        """Render one request and return a channel-native message id."""
        ...

    def update(self, message_id: str, card: ChannelCard) -> None:
        """Refresh a projection after the source-of-truth request changes."""
        ...

    def resolve(self, request_id: str, action: str, actor: str, values: dict[str, Any] | None = None) -> None:
        """Send a human decision back to the Human Gateway."""
        ...
