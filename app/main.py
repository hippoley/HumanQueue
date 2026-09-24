from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .adapters import ADAPTERS
from .auth import require_gateway_token
from .connector_registry import ConnectorEventIn, ConnectorRegistry
from .context_enrichment import enrich_with_session_context
from .demo import seed_wow
from .humanize import to_attention_request
from .models import (
    AttentionRequestCreate,
    BatchResolveRequest,
    BudgetPolicy,
    ClaimRequest,
    HumanAsk,
    ImportEnvelope,
    ResolveRequest,
)
from .protocol import uri_for_kind
from .presence_registry import PresenceRegistry, PresenceUpdate
from .resume import resume
from .store import Store
from humanqueue.channels.webhook import publish_request, verify_resolution
from humanqueue.config import db_path, gateway_token

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DB_PATH = db_path()
store = Store(DB_PATH)
connector_registry = ConnectorRegistry(DB_PATH)
presence_registry = PresenceRegistry(DB_PATH)

app = FastAPI(
    title="human://",
    version="0.6.0",
    description="One queue for everything that needs a human.",
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def gateway_auth(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        try:
            require_gateway_token(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or {},
            )
    return await call_next(request)


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "name": "human://", "version": "0.6.0"}


@app.get("/gateway")
def gateway_info():
    return {
        "name": "human://",
        "version": "0.6.0",
        "auth_required": bool(gateway_token()),
        "self_hosted": True,
    }




@app.post("/channels/{name}/resolve/{rid}")
async def channel_resolve(name: str, rid: str, request: Request):
    body = await request.body()
    signature = request.headers.get("X-Human-Channel-Signature")
    if not verify_resolution(name, body, signature):
        raise HTTPException(401, "invalid channel signature")

    try:
        payload = json.loads(body)
    except Exception as exc:
        raise HTTPException(400, "invalid JSON body") from exc

    action = str(payload.get("action") or "").strip()
    if not action:
        raise HTTPException(422, "action is required")

    existing = store.get(rid)
    if not existing:
        raise HTTPException(404, "request not found")
    if existing.status.value in {"resolved", "cancelled", "expired", "superseded"}:
        raise HTTPException(409, f"request is already {existing.status.value}")

    resolution = {
        "action": action,
        "values": payload.get("values") or {},
        "comment": payload.get("comment"),
    }
    actor = str(payload.get("actor") or f"channel:{name}")
    try:
        item, finalized = store.resolve(rid, actor, resolution)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    delivery = {"delivered": False, "reason": "waiting_for_quorum"}
    if finalized:
        delivery = await resume(item, item.resolution or resolution)
    return {"request": item, "finalized": finalized, "resume": delivery}

@app.post("/v1/human", status_code=201)
def human_interrupt(ask: HumanAsk, background_tasks: BackgroundTasks):
    try:
        req = enrich_with_session_context(to_attention_request(ask), connector_registry)
        item = store.create(req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    background_tasks.add_task(publish_request, item)
    return {"human_uri": uri_for_kind(item.kind), "request": item}


@app.post("/v1/requests", status_code=201)
def create_request(req: AttentionRequestCreate, background_tasks: BackgroundTasks):
    req = enrich_with_session_context(req, connector_registry)
    item = store.create(req)
    background_tasks.add_task(publish_request, item)
    return item


@app.post("/v1/import", status_code=201)
def import_request(env: ImportEnvelope, background_tasks: BackgroundTasks):
    item = store.create(ADAPTERS[env.adapter](env.payload))
    background_tasks.add_task(publish_request, item)
    return item


def _presence_state_for_event(event_name: str) -> str:
    name = event_name.lower()
    if name in {"permissionrequest", "beforeshellexecution", "notification"}:
        return "waiting_human"
    if name in {"userpromptsubmit", "preshelluse", "pretooluse", "tool.execute.before", "sessionstart"}:
        return "running" if name != "sessionstart" else "idle"
    if name in {"stop", "afteragentresponse", "posttooluse", "tool.execute.after"}:
        return "completed"
    if name in {"sessionend", "session_end"}:
        return "offline"
    return "unknown"


@app.post("/v1/connectors/events", status_code=202)
def connector_event(event: ConnectorEventIn):
    capsule = connector_registry.record(event)
    account = str(event.metadata.get("account") or "local")
    source_id = str(event.metadata.get("source_id") or f"{event.provider}:{account}")
    presence_registry.upsert_source(
        source_id=source_id,
        provider=event.provider,
        account=account,
        mode="native-connector",
        endpoint=None,
        config={},
    )
    presence_registry.update(PresenceUpdate(
        source_id=source_id,
        provider=event.provider,
        account=account,
        session_id=event.session_id,
        state=_presence_state_for_event(event.event_name),
        workspace=event.cwd,
        last_user=event.latest_user_prompt,
        last_agent=event.latest_assistant_message,
        current_action=event.tool_name,
        waiting_reason="human decision required" if _presence_state_for_event(event.event_name) == "waiting_human" else None,
        native={"turn_id": event.turn_id, "tool_use_id": event.tool_use_id},
    ))
    return {"accepted": True, "capsule": capsule}


@app.post("/v1/presence/sources/{source_id}")
def presence_source_upsert(source_id: str, payload: dict):
    provider = str(payload.get("provider") or "").strip()
    account = str(payload.get("account") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    if not provider or not account or not mode:
        raise HTTPException(422, "provider, account and mode are required")
    return presence_registry.upsert_source(
        source_id=source_id,
        provider=provider,
        account=account,
        mode=mode,
        endpoint=payload.get("endpoint"),
        config=payload.get("config") or {},
        enabled=bool(payload.get("enabled", True)),
    )


@app.get("/v1/presence/sources")
def presence_sources():
    return {"sources": presence_registry.list_sources()}


@app.post("/v1/presence/sessions", status_code=202)
def presence_update(update: PresenceUpdate):
    return presence_registry.update(update)


@app.get("/v1/presence/sessions")
def presence_sessions(state: str | None = None, limit: int = 200):
    return {"sessions": presence_registry.sessions(state=state, limit=limit)}


@app.get("/v1/connectors/sessions")
def connector_sessions(limit: int = 100):
    return {"sessions": connector_registry.sessions(limit=limit)}


@app.get("/v1/connectors/sessions/{provider}/{session_id}")
def connector_session(provider: str, session_id: str, event_limit: int = 20):
    session = connector_registry.session(provider, session_id, event_limit=event_limit)
    if not session:
        raise HTTPException(404, "connector session not found")
    return session


@app.get("/v1/queue")
def get_queue(status: str = "pending", limit: int = 100, include_deferred: bool = False):
    return {"items": store.queue(status=status, limit=limit, include_deferred=include_deferred)}


@app.get("/v1/batches")
def get_batches():
    return {"batches": store.batches()}


@app.post("/v1/batches/{batch_key}/resolve")
async def resolve_batch(batch_key: str, decision: BatchResolveRequest):
    try:
        items = store.resolve_batch(batch_key, decision.actor, decision.action, decision.comment)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    deliveries = []
    for item in items:
        if item.status.value == "resolved":
            deliveries.append({
                "id": item.id,
                "resume": await resume(item, item.resolution or {}),
            })
    return {"items": items, "deliveries": deliveries}


@app.get("/v1/budgets/{group}")
def get_budget(group: str):
    return store.get_budget(group)


@app.put("/v1/budgets/{group}")
def put_budget(group: str, policy: BudgetPolicy):
    policy.group = group
    return store.set_budget(policy)


@app.get("/v1/metrics")
def metrics():
    return store.metrics()


@app.get("/v1/delegation-frontier")
def delegation_frontier(min_samples: int = 5, min_agreement: float = 0.9):
    return {
        "suggestions": store.frontier(
            min_samples=min_samples,
            min_agreement=min_agreement,
        )
    }


@app.get("/v1/policy-sandbox/{policy_key}")
def policy_sandbox(policy_key: str, action: str | None = None):
    return store.policy_sandbox(policy_key, proposed_action=action)


@app.get("/v1/requests/{rid}")
def get_request(rid: str):
    req = store.get(rid)
    if not req:
        raise HTTPException(404, "request not found")
    return {
        "request": req,
        "human_uri": uri_for_kind(req.kind),
        "events": store.events(rid),
    }


@app.post("/v1/requests/{rid}/claim")
def claim_request(rid: str, claim: ClaimRequest):
    try:
        req = store.claim(rid, claim.actor)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not req:
        raise HTTPException(404, "request not found")
    return req


@app.post("/v1/requests/{rid}/resolve")
async def resolve_request(rid: str, decision: ResolveRequest):
    existing = store.get(rid)
    if not existing:
        raise HTTPException(404, "request not found")
    if existing.status.value in {"resolved", "cancelled", "expired", "superseded"}:
        raise HTTPException(409, f"request is already {existing.status.value}")

    resolution = decision.model_dump(mode="json")
    try:
        req, finalized = store.resolve(rid, decision.actor, resolution)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    delivery = {"delivered": False, "reason": "waiting_for_quorum"}
    if finalized:
        delivery = await resume(req, req.resolution or resolution)
    return {"request": req, "finalized": finalized, "resume": delivery}


@app.get("/v1/events/stream")
async def event_stream(request: Request):
    async def generate():
        last = store.latest_event_seq()
        yield f"event: ready\ndata: {json.dumps({'seq': last})}\n\n"
        ticks = 0
        while not await request.is_disconnected():
            seq = store.latest_event_seq()
            if seq != last:
                last = seq
                yield f"event: changed\ndata: {json.dumps({'seq': seq})}\n\n"
            ticks += 1
            if ticks % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/v1/demo/seed")
def demo_seed(reset: bool = False):
    ids = seed_wow(store, reset=reset)
    return {"seeded": len(ids), "ids": ids}
