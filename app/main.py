from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from .adapters import ADAPTERS
from .humanize import to_attention_request
from .models import (
    AttentionRequestCreate,
    BatchResolve,
    BudgetPolicy,
    ClaimIn,
    HumanAsk,
    ImportEnvelope,
    ResolveIn,
)
from .resume import deliver
from .store import Store

DB_PATH = os.environ.get("HUMAN_QUEUE_DB", "./data/human-queue.db")
store = Store(DB_PATH)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="human://", version="0.3.0", description="One queue for everything that needs a human.", lifespan=lifespan)


@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "web" / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "service": "human://", "version": "0.3.0"}


@app.post("/v1/human", status_code=201)
def human(ask: HumanAsk):
    item = store.create(to_attention_request(ask))
    return {"human_uri": ask.uri, "request": item}


@app.post("/v1/requests", status_code=201)
def create(req: AttentionRequestCreate):
    return store.create(req)


@app.post("/v1/import", status_code=201)
def import_request(env: ImportEnvelope):
    adapter = ADAPTERS.get(env.adapter)
    if not adapter:
        raise HTTPException(400, f"unknown adapter: {env.adapter}")
    return store.create(adapter(env.payload))


@app.get("/v1/queue")
def queue(status: str | None = Query(None)):
    return {"items": store.queue(status=status)}


@app.get("/v1/batches")
def batches():
    return {"batches": store.batches()}


@app.get("/v1/requests/{request_id}")
def get_request(request_id: str):
    item = store.get(request_id)
    if not item:
        raise HTTPException(404, "not found")
    return {"request": item, "events": store.events(request_id)}


@app.post("/v1/requests/{request_id}/claim")
def claim(request_id: str, data: ClaimIn):
    item = store.claim(request_id, data.actor)
    if not item:
        raise HTTPException(404, "not found")
    return item


@app.post("/v1/requests/{request_id}/resolve")
async def resolve(request_id: str, data: ResolveIn):
    item, finalized = store.resolve(request_id, data.actor, data.model_dump(exclude={"actor"}))
    if not item:
        raise HTTPException(404, "not found")
    delivery = {"delivered": False, "reason": "awaiting_quorum"}
    if finalized:
        delivery = await deliver(item, item.resolution or data.model_dump(exclude={"actor"}))
    return {"request": item, "finalized": finalized, "resume": delivery}


@app.post("/v1/batches/{batch_key}/resolve")
async def resolve_batch(batch_key: str, data: BatchResolve):
    items = store.batch_items(batch_key)
    if not items:
        raise HTTPException(404, "batch not found")
    results = []
    resolution = {"action": data.action, "values": data.values, "comment": data.comment}
    for candidate in items:
        item, finalized = store.resolve(candidate.id, data.actor, resolution)
        delivery = {"delivered": False, "reason": "awaiting_quorum"}
        if finalized:
            delivery = await deliver(item, item.resolution or resolution)
        results.append({"request": item, "finalized": finalized, "resume": delivery})
    return {"batch_key": batch_key, "processed": len(results), "results": results}


@app.get("/v1/budgets")
def budgets():
    return {"budgets": store.budgets()}


@app.put("/v1/budgets/{group}")
def set_budget(group: str, policy: BudgetPolicy):
    if group != policy.group:
        policy = policy.model_copy(update={"group": group})
    store.set_budget(policy)
    return policy


@app.get("/v1/metrics")
def metrics():
    return store.metrics()


@app.get("/v1/delegation-frontier")
def frontier(min_samples: int = 4):
    return {"candidates": store.frontier(min_samples=min_samples)}


@app.get("/v1/policy-sandbox/{policy_key}")
def policy_sandbox(policy_key: str, action: str | None = None):
    result = store.policy_sandbox(policy_key, proposed_action=action)
    if not result:
        raise HTTPException(404, "no human decision history for policy key")
    return result


@app.get("/v1/events/stream")
async def events_stream():
    async def gen():
        last = ""
        while True:
            snapshot = json.dumps(store.stream_snapshot(), sort_keys=True, default=str)
            if snapshot != last:
                yield f"data: {snapshot}\n\n"
                last = snapshot
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
