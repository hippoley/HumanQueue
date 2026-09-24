import asyncio

import httpx
from pathlib import Path

from fastapi.testclient import TestClient

from app.models import AttentionRequestCreate, AttentionSignals, BudgetPolicy, RequestKind, RoutePolicy
from app.scoring import priority_score
from app.store import Store


def req(**overrides):
    base = dict(source="x", source_ref="1", title="t", summary="s", kind=RequestKind.approval)
    base.update(overrides)
    return AttentionRequestCreate(**base)


def test_priority_rewards_unblocking_and_low_attention_cost():
    low = req(signals=AttentionSignals(urgency=.3, unblock_value=.2, downstream_blocked=0, human_effort_seconds=300))
    high = req(source_ref="2", signals=AttentionSignals(urgency=.8, unblock_value=.9, downstream_blocked=10, human_effort_seconds=10))
    assert priority_score(high) > priority_score(low)


def test_adapter_import_and_resolution(tmp_path: Path):
    from app import main
    main.store = Store(str(tmp_path / "test.db"))
    client = TestClient(main.app)
    resp = client.post("/v1/import", json={"adapter":"a2a","payload":{"task":{"id":"t1","status":{"state":"input-required","message":"Pick region"}}}})
    assert resp.status_code == 201
    item = resp.json()
    resolved = client.post(f"/v1/requests/{item['id']}/resolve", json={"actor":"alice","action":"continue","values":{"region":"EU"}})
    assert resolved.status_code == 200
    assert resolved.json()["request"]["status"] == "resolved"


def test_idempotency(tmp_path: Path):
    s = Store(str(tmp_path / "idem.db"))
    a = s.create(req(idempotency_key="same")); b = s.create(req(idempotency_key="same"))
    assert a.id == b.id


def test_supersession_replaces_stale_interrupt(tmp_path: Path):
    s = Store(str(tmp_path / "super.db"))
    old = s.create(req(source_ref="old", supersession_key="deploy-prod"))
    new = s.create(req(source_ref="new", supersession_key="deploy-prod"))
    old2 = s.get(old.id)
    assert old2.status.value == "superseded"
    assert old2.superseded_by == new.id


def test_quorum_requires_matching_votes(tmp_path: Path):
    s = Store(str(tmp_path / "quorum.db"))
    item = s.create(req(route=RoutePolicy(mode="quorum", actors=["a","b","c"], quorum=2)))
    one, final1 = s.resolve(item.id, "a", {"action":"approve","values":{}})
    assert not final1 and one.status.value == "claimed"
    two, final2 = s.resolve(item.id, "b", {"action":"approve","values":{}})
    assert final2 and two.status.value == "resolved"
    assert two.quorum_progress["leading_votes"] == 2


def test_attention_budget_batches_low_value_repeated_work(tmp_path: Path):
    s = Store(str(tmp_path / "budget.db"))
    s.set_budget(BudgetPolicy(group="ops", min_interrupt_priority=90, min_queue_priority=40, batch_below_priority=80))
    item = s.create(req(attention_group="ops", batch_key="refunds", signals=AttentionSignals(urgency=.2, unblock_value=.3, risk_if_wrong=.2, human_effort_seconds=15)))
    assert item.surface_mode.value == "batch"
    assert s.batches()[0]["batch_key"] == "refunds"


def test_delegation_frontier_is_suggestion_only(tmp_path: Path):
    s = Store(str(tmp_path / "frontier.db"))
    for i in range(5):
        item = s.create(req(source_ref=str(i), policy_key="refund-under-20", signals=AttentionSignals(risk_if_wrong=.2, human_effort_seconds=12)))
        s.resolve(item.id, f"actor{i}", {"action":"approve","values":{}})
    out = s.frontier()
    assert out[0]["policy_key"] == "refund-under-20"
    assert out[0]["recommendation"] == "candidate_for_human_authored_policy"


def test_human_protocol_maps_uri_to_canonical_request(tmp_path: Path):
    from app import main
    main.store = Store(str(tmp_path / "human.db"))
    client = TestClient(main.app)
    response = client.post("/v1/human", json={
        "uri":"human://approve", "source":"codex", "ref":"s1",
        "title":"Run destructive command?", "risk":.98, "seconds":8,
    })
    assert response.status_code == 201
    body = response.json()
    assert body["human_uri"] == "human://approve"
    assert body["request"]["kind"] == "approval"
    assert [o["id"] for o in body["request"]["options"]] == ["approve", "reject"]


def test_human_protocol_rejects_unknown_uri(tmp_path: Path):
    from app import main
    main.store = Store(str(tmp_path / "bad-human.db"))
    client = TestClient(main.app)
    response = client.post("/v1/human", json={
        "uri":"robot://approve", "source":"x", "ref":"1", "title":"bad"
    })
    assert response.status_code == 422


def test_policy_sandbox_is_shadow_only(tmp_path: Path):
    s = Store(str(tmp_path / "sandbox.db"))
    for i in range(6):
        item = s.create(req(source_ref=str(i), policy_key="small-refund", signals=AttentionSignals(risk_if_wrong=.15, human_effort_seconds=10)))
        s.resolve(item.id, f"actor-{i}", {"action":"approve","values":{}})
    replay = s.policy_sandbox("small-refund")
    assert replay["agreement"] == 1.0
    assert replay["conflicts"] == 0
    assert replay["mode"] == "shadow_only"
    assert replay["enabled"] is False


def test_demo_seed_creates_cross_platform_queue_and_batches(tmp_path: Path):
    from app.demo import seed_wow
    s = Store(str(tmp_path / "demo.db"))
    ids = seed_wow(s, reset=True)
    visible = s.queue()
    assert len(ids) == 5
    assert {x.source for x in visible} >= {"codex", "github", "support-agent", "mcp", "n8n"}
    assert any(b["batch_key"] == "refunds-under-20" for b in s.batches())
    assert s.frontier(min_samples=5)[0]["policy_key"] == "refund-under-20-known-customer"


def test_home_is_product_surface(tmp_path: Path):
    from app import main
    main.store = Store(str(tmp_path / "home.db"))
    client = TestClient(main.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "YOU ARE BLOCKING" in response.text
    assert "human://" in response.text


def test_gateway_token_protects_v1_routes(tmp_path: Path, monkeypatch):
    from app import main
    import app.auth as auth

    main.store = Store(str(tmp_path / "auth.db"))
    monkeypatch.setattr(auth, "gateway_token", lambda: "hq_test_secret")
    client = TestClient(main.app)

    assert client.get("/health").status_code == 200
    denied = client.post("/v1/human", json={
        "uri":"human://approve","source":"agent","ref":"1","title":"Continue?"
    })
    assert denied.status_code == 401

    allowed = client.post(
        "/v1/human",
        headers={"Authorization":"Bearer hq_test_secret"},
        json={"uri":"human://approve","source":"agent","ref":"1","title":"Continue?"},
    )
    assert allowed.status_code == 201


def test_gateway_token_shape():
    from humanqueue.config import generate_token
    token = generate_token()
    assert token.startswith("hq_")
    assert len(token) > 20


def test_standard_resolve_rejects_duplicate_terminal_decision(tmp_path: Path):
    from app import main
    import app.auth as auth

    main.store = Store(str(tmp_path / "duplicate-resolve.db"))
    client = TestClient(main.app)
    item = main.store.create(req())

    first = client.post(
        f"/v1/requests/{item.id}/resolve",
        json={"actor": "alice", "action": "approve"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/v1/requests/{item.id}/resolve",
        json={"actor": "alice", "action": "approve"},
    )
    assert second.status_code == 409


def test_channel_delivery_failure_is_audited_without_consuming_request(tmp_path: Path, monkeypatch):
    from app import main

    main.store = Store(str(tmp_path / "delivery-audit.db"))
    monkeypatch.setattr(
        main,
        "publish_request",
        lambda item: [
            {
                "channel": "ops",
                "delivered": False,
                "error": "simulated unreachable human surface",
            }
        ],
    )

    client = TestClient(main.app)
    created = client.post(
        "/v1/human",
        json={
            "uri": "human://approve",
            "source": "agent",
            "ref": "run-1",
            "title": "Continue?",
        },
    )
    assert created.status_code == 201
    rid = created.json()["request"]["id"]

    detail = client.get(f"/v1/requests/{rid}")
    assert detail.status_code == 200
    events = detail.json()["events"]

    failed = [event for event in events if event["type"] == "channel_undeliverable"]
    assert len(failed) == 1
    assert failed[0]["actor"] == "channel:ops"
    assert failed[0]["data"]["delivered"] is False
    assert "unreachable" in failed[0]["data"]["error"]

    # Delivery evidence is not yet a lifecycle decision. The obligation remains pending.
    assert main.store.get(rid).status.value == "pending"


def test_resume_transport_error_returns_evidence_instead_of_raising(tmp_path: Path, monkeypatch):
    import app.resume as resume_module

    item = Store(str(tmp_path / "resume-transport.db")).create(
        req(
            resume={
                "mode": "webhook",
                "url": "https://example.invalid/human-resume",
                "secret": "test-secret",
            }
        )
    )

    class BrokenAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise RuntimeError("simulated network failure")

    monkeypatch.setattr(resume_module.httpx, "AsyncClient", BrokenAsyncClient)

    result = asyncio.run(resume_module.resume(item, {"action": "approve"}))
    assert result["delivered"] is False
    assert result["confirmed"] is False
    assert result["reason"] == "resume_transport_error"
    assert "network failure" in result["error"]


def test_resume_receipt_must_bind_to_exact_request_id():
    from app.resume import _receipt_confirmation

    good = httpx.Response(
        200,
        json={"request_id": "attn_exact", "resumed": True},
    )
    confirmed, receipt = _receipt_confirmation("attn_exact", good)
    assert confirmed is True
    assert receipt == {"request_id": "attn_exact", "resumed": True}

    stale = httpx.Response(
        200,
        json={"request_id": "attn_stale", "resumed": True},
    )
    confirmed, receipt = _receipt_confirmation("attn_exact", stale)
    assert confirmed is False
    assert receipt["request_id"] == "attn_stale"


def test_native_wait_path_is_not_reported_as_resume_undeliverable(tmp_path: Path):
    from app import main

    main.store = Store(str(tmp_path / "native-wait-resume.db"))
    client = TestClient(main.app)
    item = main.store.create(req())

    resolved = client.post(
        f"/v1/requests/{item.id}/resolve",
        json={"actor": "alice", "action": "approve"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["request"]["status"] == "resolved"
    assert resolved.json()["resume"]["reason"] == "no_resume_target"

    events = main.store.events(item.id)
    assert any(event["type"] == "resume_not_applicable" for event in events)
    assert not any(event["type"] == "resume_undeliverable" for event in events)


def test_mcp_server_reports_package_version():
    from humanqueue import __version__
    from humanqueue.mcp_server import handle

    response = handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })
    assert response["result"]["serverInfo"]["version"] == __version__


def test_public_version_surfaces_match_package_version(tmp_path: Path):
    from app import main
    from humanqueue import __version__
    from humanqueue.mcp_server import handle

    main.store = Store(str(tmp_path / "version-surfaces.db"))
    client = TestClient(main.app)

    assert client.get("/health").json()["version"] == __version__
    assert client.get("/gateway").json()["version"] == __version__

    response = handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })
    assert response["result"]["serverInfo"]["version"] == __version__


def test_metrics_expose_integrity_event_counts(tmp_path: Path):
    store = Store(str(tmp_path / "integrity-metrics.db"))
    item = store.create(req())

    store.record_event(item.id, "channel_delivered", actor="channel:slack", data={"delivered": True})
    store.record_event(item.id, "channel_undeliverable", actor="channel:telegram", data={"delivered": False})
    store.record_event(item.id, "resume_delivered_unconfirmed", actor="resume", data={"delivered": True, "confirmed": False})
    store.record_event(item.id, "resume_confirmed", actor="resume", data={"delivered": True, "confirmed": True})
    store.record_event(item.id, "resume_not_applicable", actor="resume", data={"reason": "no_resume_target"})

    integrity = store.metrics()["integrity_last_24h"]
    assert integrity["channel_delivered"] == 1
    assert integrity["channel_undeliverable"] == 1
    assert integrity["resume_delivered_unconfirmed"] == 1
    assert integrity["resume_confirmed"] == 1
    assert integrity["resume_not_applicable"] == 1
    assert integrity.get("resume_undeliverable", 0) == 0


def test_home_surfaces_boundary_integrity_view(tmp_path: Path):
    from app import main

    main.store = Store(str(tmp_path / "integrity-home.db"))
    client = TestClient(main.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Boundary integrity" in response.text
    assert "resume confirmed" in response.text
    assert "channel undeliverable" in response.text


def test_missing_native_identity_never_creates_shared_supersession_key(tmp_path: Path):
    from app.adapters import from_a2a, from_openai

    store = Store(str(tmp_path / "identity-supersession.db"))

    a = from_a2a({"task": {"status": {"state": "input-required", "message": "first"}}})
    b = from_a2a({"task": {"status": {"state": "input-required", "message": "second"}}})
    assert a.supersession_key is None
    assert b.supersession_key is None

    first = store.create(a)
    second = store.create(b)
    assert first.id != second.id
    assert store.get(first.id).status.value == "pending"
    assert store.get(second.id).status.value == "pending"

    openai_unidentified = from_openai({"tool_name": "shell"})
    assert openai_unidentified.source_ref == "unidentified"
    assert openai_unidentified.supersession_key is None

    openai_identified = from_openai({"run_id": "run-123", "tool_name": "shell"})
    assert openai_identified.source_ref == "run-123"
    assert openai_identified.supersession_key == "openai:run-123"
