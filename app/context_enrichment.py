from __future__ import annotations

from .connector_registry import ConnectorRegistry
from .models import AttentionRequestCreate


def enrich_with_session_context(
    req: AttentionRequestCreate,
    registry: ConnectorRegistry,
) -> AttentionRequestCreate:
    """Merge the latest bounded editor-session state into a connector request.

    Native permission events often contain the exact tool call but not the
    preceding user/assistant messages. The session registry already observed
    those earlier lifecycle events, so the Gateway can safely join them by
    provider + session_id without parsing unstable full-transcript formats.
    """
    context = dict(req.context or {})
    handle = context.get("native_handle")
    if not isinstance(handle, dict):
        return req

    provider = handle.get("provider")
    session_id = handle.get("session_id")
    if not provider or not session_id:
        return req

    session = registry.session(str(provider), str(session_id), event_limit=0)
    if not session:
        return req

    bounded = {
        "provider": session.get("provider"),
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "cwd": session.get("cwd"),
        "model": session.get("model"),
        "turn_id": session.get("last_turn_id"),
        "latest_user_prompt": session.get("last_user_prompt"),
        "latest_assistant_message": session.get("last_assistant_message"),
        "transcript_locator": session.get("transcript_path"),
        "updated_at": session.get("updated_at"),
    }
    context["session_context"] = {
        key: value for key, value in bounded.items() if value is not None
    }
    return req.model_copy(update={"context": context})
