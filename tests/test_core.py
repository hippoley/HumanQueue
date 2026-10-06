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


def test_blank_identity_is_rejected_at_presence_and_connector_boundaries(tmp_path: Path):
    from app import main

    main.presence_registry = __import__("app.presence_registry", fromlist=["PresenceRegistry"]).PresenceRegistry(
        str(tmp_path / "blank-presence.db")
    )
    main.connector_registry = __import__("app.connector_registry", fromlist=["ConnectorRegistry"]).ConnectorRegistry(
        str(tmp_path / "blank-connectors.db")
    )
    client = TestClient(main.app)

    presence = client.post(
        "/v1/presence/sessions",
        json={
            "source_id": "   ",
            "provider": "openclaw",
            "account": "work",
            "session_id": "session-1",
            "state": "running",
        },
    )
    assert presence.status_code == 422

    connector = client.post(
        "/v1/connectors/events",
        json={
            "provider": "codex",
            "event_name": "SessionStart",
            "session_id": "   ",
        },
    )
    assert connector.status_code == 422


def test_batch_authorization_fails_before_any_item_is_resolved(tmp_path: Path):
    store = Store(str(tmp_path / "batch-auth-preflight.db"))

    first = store.create(req(
        source_ref="batch-a",
        batch_key="mixed-auth",
        route=RoutePolicy(mode="single", actors=["alice"]),
        signals=AttentionSignals(
            urgency=.1,
            unblock_value=.1,
            risk_if_wrong=.1,
            human_effort_seconds=10,
        ),
    ))
    second = store.create(req(
        source_ref="batch-b",
        batch_key="mixed-auth",
        route=RoutePolicy(mode="single", actors=["bob"]),
        signals=AttentionSignals(
            urgency=.1,
            unblock_value=.1,
            risk_if_wrong=.1,
            human_effort_seconds=10,
        ),
    ))

    import pytest
    with pytest.raises(PermissionError):
        store.resolve_batch("mixed-auth", "alice", "approve")

    assert store.get(first.id).status.value == "pending"
    assert store.get(second.id).status.value == "pending"
    assert not any(e["type"] == "resolved" for e in store.events(first.id))
    assert not any(e["type"] == "resolved" for e in store.events(second.id))

def test_human_boundary_is_public_alias():
    import humanqueue
    assert humanqueue.HumanBoundary is humanqueue.HumanQueue


def test_python_sdk_default_constructor_uses_gateway_config(monkeypatch):
    import humanqueue.client as client_module
    from humanqueue import HumanBoundary

    monkeypatch.setattr(client_module, "gateway_url", lambda: "http://127.0.0.1:9999")
    monkeypatch.setattr(client_module, "gateway_token", lambda: "hq_test_client")

    human = HumanBoundary()

    assert human.base_url == "http://127.0.0.1:9999"
    assert human._headers() == {"Authorization": "Bearer hq_test_client"}


def test_machine_provenance_cannot_resolve_human_boundary(tmp_path: Path):
    from app import main

    main.store = Store(str(tmp_path / "machine-cannot-resolve-human.db"))
    client = TestClient(main.app)
    item = main.store.create(req())

    response = client.post(
        f"/v1/requests/{item.id}/resolve",
        json={
            "actor": "scheduler",
            "actor_kind": "system",
            "action": "approve",
        },
    )

    assert response.status_code == 403
    current = main.store.get(item.id)
    assert current is not None
    assert current.status.value == "pending"
    assert current.resolution is None
    assert not any(e["type"] == "resolved" for e in main.store.events(item.id))


def test_machine_outcome_expires_without_fabricating_human_resolution(tmp_path: Path):
    from app import main

    main.store = Store(str(tmp_path / "machine-expiry.db"))
    client = TestClient(main.app)
    item = main.store.create(req())

    response = client.post(
        f"/v1/requests/{item.id}/outcome",
        json={
            "actor": "timeout-worker",
            "actor_kind": "system",
            "outcome": "expired",
            "reason": "deadline elapsed",
            "metadata": {"timer": "60s"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["human_resolution"] is False
    assert body["request"]["status"] == "expired"
    assert body["request"]["resolution"] is None
    assert body["outcome"]["actor_kind"] == "system"

    events = main.store.events(item.id)
    expired = [e for e in events if e["type"] == "machine_expired"]
    assert len(expired) == 1
    assert expired[0]["actor"] == "timeout-worker"
    assert expired[0]["data"]["actor_kind"] == "system"
    assert expired[0]["data"]["reason"] == "deadline elapsed"


def test_human_resolution_records_typed_provenance(tmp_path: Path):
    store = Store(str(tmp_path / "human-provenance.db"))
    item = store.create(req())

    resolved, finalized = store.resolve(
        item.id,
        "alice",
        {"action": "approve", "values": {}},
        actor_kind="human",
    )

    assert finalized is True
    assert resolved is not None
    assert resolved.resolution["provenance"] == {
        "actor": "alice",
        "actor_kind": "human",
    }


def test_explicit_any_authority_can_accept_policy_resolution(tmp_path: Path):
    store = Store(str(tmp_path / "policy-authority.db"))
    item = store.create(req(route=RoutePolicy(required_actor_kind="any")))

    resolved, finalized = store.resolve(
        item.id,
        "low-risk-policy",
        {"action": "approve", "values": {}},
        actor_kind="policy",
    )

    assert finalized is True
    assert resolved is not None
    assert resolved.status.value == "resolved"
    assert resolved.resolution["provenance"]["actor_kind"] == "policy"


def test_source_turn_completion_does_not_clear_pending_human_boundary(tmp_path: Path):
    from app import main
    from app.connector_registry import ConnectorRegistry
    from app.presence_registry import PresenceRegistry

    db = str(tmp_path / "turn-complete-boundary.db")
    main.store = Store(db)
    main.connector_registry = ConnectorRegistry(db)
    main.presence_registry = PresenceRegistry(db)
    client = TestClient(main.app)

    created = client.post(
        "/v1/human",
        json={
            "uri": "human://clarify",
            "source": "codex",
            "ref": "session-1:turn-1",
            "title": "Which deployment target?",
            "context": {
                "session_id": "session-1",
                "turn_id": "turn-1",
            },
        },
    )
    assert created.status_code == 201
    rid = created.json()["request"]["id"]

    # The source runtime can finish/stop the turn that created the question.
    # That lifecycle observation must not imply that the human obligation was answered.
    stopped = client.post(
        "/v1/connectors/events",
        json={
            "provider": "codex",
            "event_name": "Stop",
            "session_id": "session-1",
            "turn_id": "turn-1",
        },
    )
    assert stopped.status_code == 202

    ended = client.post(
        "/v1/connectors/events",
        json={
            "provider": "codex",
            "event_name": "SessionEnd",
            "session_id": "session-1",
            "turn_id": "turn-1",
        },
    )
    assert ended.status_code == 202

    boundary = main.store.get(rid)
    assert boundary is not None
    assert boundary.status.value == "pending"
    assert boundary.resolution is None
    assert not any(
        event["type"] in {"resolved", "machine_expired", "machine_cancelled"}
        for event in main.store.events(rid)
    )


def test_concurrent_independent_store_instances_resolve_once(tmp_path: Path):
    """Simulate two Gateway workers racing on one shared SQLite boundary."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "concurrent-resolve.db")
    creator = Store(db)
    item = creator.create(req())

    left = Store(db)
    right = Store(db)
    start = threading.Barrier(2)

    def resolve(store: Store, actor: str):
        start.wait(timeout=5)
        try:
            resolved, finalized = store.resolve(
                item.id,
                actor,
                {"action": "approve", "values": {}},
            )
            return {
                "error": None,
                "status": resolved.status.value if resolved else None,
                "finalized": finalized,
            }
        except Exception as exc:
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "status": None,
                "finalized": False,
            }

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(resolve, left, "alice")
        b = pool.submit(resolve, right, "bob")
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert all(result["error"] is None for result in results), results

    final = creator.get(item.id)
    assert final is not None
    assert final.status.value == "resolved"

    with creator._conn() as c:
        resolved_events = c.execute(
            "SELECT actor,data FROM events WHERE request_id=? AND type='resolved'",
            (item.id,),
        ).fetchall()
        votes = c.execute(
            "SELECT actor FROM votes WHERE request_id=? ORDER BY actor",
            (item.id,),
        ).fetchall()

    assert len(resolved_events) == 1, [
        {"actor": row["actor"], "data": row["data"]}
        for row in resolved_events
    ]
    # Once one single-resolver decision wins, a racing second worker must not
    # create a second vote that could be mistaken for another human decision.
    assert len(votes) == 1, [row["actor"] for row in votes]


def test_concurrent_api_resolve_resumes_machine_once(tmp_path: Path, monkeypatch):
    """Two HTTP workers racing the same boundary must produce one resume."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app import main

    main.store = Store(str(tmp_path / "concurrent-api-resolve.db"))
    item = main.store.create(req())

    resume_calls = []
    resume_lock = threading.Lock()

    async def fake_resume(resolved, resolution):
        with resume_lock:
            resume_calls.append((resolved.id, resolution.get("action")))
        return {"delivered": True, "semantic_confirmed": True}

    monkeypatch.setattr(main, "_resume_and_record", fake_resume)

    start = threading.Barrier(2)

    def post(actor: str):
        client = TestClient(main.app)
        start.wait(timeout=5)
        response = client.post(
            f"/v1/requests/{item.id}/resolve",
            json={"actor": actor, "action": "approve"},
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(post, "alice")
        b = pool.submit(post, "bob")
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert sorted(status for status, _ in results) == [200, 409], results
    assert resume_calls == [(item.id, "approve")], resume_calls

    detail = TestClient(main.app).get(f"/v1/requests/{item.id}").json()
    resolved_events = [
        event for event in detail["events"]
        if event["type"] == "resolved"
    ]
    assert len(resolved_events) == 1, resolved_events


def test_concurrent_quorum_votes_finalize_once(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "concurrent-quorum.db")
    creator = Store(db)
    item = creator.create(
        req(route=RoutePolicy(mode="quorum", actors=["alice", "bob"], quorum=2))
    )
    stores = [Store(db), Store(db)]
    start = threading.Barrier(2)

    def vote(store: Store, actor: str):
        start.wait(timeout=5)
        resolved, finalized = store.resolve(
            item.id,
            actor,
            {"action": "approve", "values": {}},
        )
        return finalized, resolved.status.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(vote, stores[0], "alice"),
            pool.submit(vote, stores[1], "bob"),
        ]
        results = [future.result(timeout=10) for future in results]

    assert sorted(finalized for finalized, _ in results) == [False, True], results

    with creator._conn() as c:
        votes = c.execute(
            "SELECT actor FROM votes WHERE request_id=? ORDER BY actor",
            (item.id,),
        ).fetchall()
        resolved_events = c.execute(
            "SELECT actor FROM events WHERE request_id=? AND type='resolved'",
            (item.id,),
        ).fetchall()

    assert [row["actor"] for row in votes] == ["alice", "bob"]
    assert len(resolved_events) == 1
    assert creator.get(item.id).status.value == "resolved"


def test_concurrent_conflicting_quorum_votes_never_finalize(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "concurrent-conflict.db")
    creator = Store(db)
    item = creator.create(
        req(route=RoutePolicy(mode="quorum", actors=["alice", "bob"], quorum=2))
    )
    stores = [Store(db), Store(db)]
    start = threading.Barrier(2)

    def vote(store: Store, actor: str, action: str):
        start.wait(timeout=5)
        resolved, finalized = store.resolve(
            item.id,
            actor,
            {"action": action, "values": {}},
        )
        return finalized, resolved.status.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(vote, stores[0], "alice", "approve")
        b = pool.submit(vote, stores[1], "bob", "reject")
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert all(finalized is False for finalized, _ in results), results

    final = creator.get(item.id)
    assert final.status.value == "claimed"
    assert final.quorum_progress["votes"] == 2
    assert final.quorum_progress["conflicted"] is True

    with creator._conn() as c:
        resolved_events = c.execute(
            "SELECT actor FROM events WHERE request_id=? AND type='resolved'",
            (item.id,),
        ).fetchall()
    assert resolved_events == []


def test_human_resolution_racing_machine_expiry_has_one_terminal_outcome(tmp_path: Path):
    """Human approval and system expiry racing across workers must not both commit."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "human-vs-expiry.db")
    creator = Store(db)
    item = creator.create(req())

    human_store = Store(db)
    machine_store = Store(db)
    start = threading.Barrier(2)

    def human():
        start.wait(timeout=5)
        resolved, finalized = human_store.resolve(
            item.id,
            "alice",
            {"action": "approve", "values": {}},
            actor_kind="human",
        )
        return {
            "kind": "human",
            "status": resolved.status.value if resolved else None,
            "finalized": finalized,
        }

    def machine():
        start.wait(timeout=5)
        resolved = machine_store.apply_machine_outcome(
            item.id,
            actor="expiry-worker",
            actor_kind="system",
            outcome="expired",
            reason="deadline elapsed",
        )
        return {
            "kind": "machine",
            "status": resolved.status.value if resolved else None,
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(human),
            pool.submit(machine),
        ]
        results = [future.result(timeout=10) for future in results]

    final = creator.get(item.id)
    assert final is not None
    assert final.status.value in {"resolved", "expired"}

    with creator._conn() as c:
        terminal_events = c.execute(
            """
            SELECT type,actor,data
            FROM events
            WHERE request_id=?
              AND type IN ('resolved','machine_expired')
            ORDER BY seq
            """,
            (item.id,),
        ).fetchall()

    assert len(terminal_events) == 1, [
        {"type": row["type"], "actor": row["actor"], "data": row["data"]}
        for row in terminal_events
    ]

    if final.status.value == "resolved":
        assert terminal_events[0]["type"] == "resolved"
    else:
        assert terminal_events[0]["type"] == "machine_expired"


def test_human_resolution_racing_machine_cancel_has_one_terminal_outcome(tmp_path: Path):
    """Cancellation has the same exactly-one terminal invariant as expiry."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "human-vs-cancel.db")
    creator = Store(db)
    item = creator.create(req())

    human_store = Store(db)
    machine_store = Store(db)
    start = threading.Barrier(2)

    def human():
        start.wait(timeout=5)
        return human_store.resolve(
            item.id,
            "alice",
            {"action": "approve", "values": {}},
            actor_kind="human",
        )

    def machine():
        start.wait(timeout=5)
        return machine_store.apply_machine_outcome(
            item.id,
            actor="scheduler",
            actor_kind="service",
            outcome="cancelled",
            reason="workflow aborted",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(human)
        b = pool.submit(machine)
        a.result(timeout=10)
        b.result(timeout=10)

    final = creator.get(item.id)
    assert final is not None
    assert final.status.value in {"resolved", "cancelled"}

    with creator._conn() as c:
        terminal_events = c.execute(
            """
            SELECT type
            FROM events
            WHERE request_id=?
              AND type IN ('resolved','machine_cancelled')
            ORDER BY seq
            """,
            (item.id,),
        ).fetchall()

    assert len(terminal_events) == 1, [row["type"] for row in terminal_events]


def test_http_human_resolution_racing_machine_expiry_has_one_winner(tmp_path: Path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app import main

    main.store = Store(str(tmp_path / "http-human-vs-expiry.db"))
    item = main.store.create(req())

    resume_calls = []
    resume_lock = threading.Lock()

    async def fake_resume(resolved, resolution):
        with resume_lock:
            resume_calls.append(resolved.id)
        return {"delivered": True, "semantic_confirmed": True}

    monkeypatch.setattr(main, "_resume_and_record", fake_resume)

    start = threading.Barrier(2)

    def human():
        client = TestClient(main.app)
        start.wait(timeout=5)
        response = client.post(
            f"/v1/requests/{item.id}/resolve",
            json={"actor": "alice", "action": "approve"},
        )
        return response.status_code, response.json()

    def machine():
        client = TestClient(main.app)
        start.wait(timeout=5)
        response = client.post(
            f"/v1/requests/{item.id}/outcome",
            json={
                "actor": "expiry-worker",
                "actor_kind": "system",
                "outcome": "expired",
                "reason": "deadline elapsed",
            },
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(human)
        b = pool.submit(machine)
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert sorted(status for status, _ in results) == [200, 409], results

    detail = TestClient(main.app).get(f"/v1/requests/{item.id}").json()
    terminal_events = [
        event for event in detail["events"]
        if event["type"] in {"resolved", "machine_expired"}
    ]
    assert len(terminal_events) == 1, terminal_events

    final_status = detail["request"]["status"]
    if final_status == "resolved":
        assert resume_calls == [item.id]
        assert terminal_events[0]["type"] == "resolved"
    else:
        assert final_status == "expired"
        assert resume_calls == []
        assert terminal_events[0]["type"] == "machine_expired"


def test_concurrent_claim_has_one_owner(tmp_path: Path):
    """Two workers racing to claim one boundary must not both take ownership."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "claim-race.db")
    creator = Store(db)
    item = creator.create(req())

    left = Store(db)
    right = Store(db)
    start = threading.Barrier(2)

    def claim(store: Store, actor: str):
        start.wait(timeout=5)
        try:
            claimed = store.claim(item.id, actor)
            return {
                "error": None,
                "claimed_by": claimed.claimed_by if claimed else None,
                "status": claimed.status.value if claimed else None,
            }
        except Exception as exc:
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "claimed_by": None,
                "status": None,
            }

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(claim, left, "alice")
        b = pool.submit(claim, right, "bob")
        results = [a.result(timeout=10), b.result(timeout=10)]

    final = creator.get(item.id)
    assert final is not None
    assert final.status.value == "claimed"
    assert final.claimed_by in {"alice", "bob"}

    with creator._conn() as c:
        claim_events = c.execute(
            "SELECT actor FROM events WHERE request_id=? AND type='claimed' ORDER BY seq",
            (item.id,),
        ).fetchall()

    assert len(claim_events) == 1, [row["actor"] for row in claim_events]
    winner = claim_events[0]["actor"]
    assert final.claimed_by == winner

    # Store.claim is a state-returning primitive: the racing loser is
    # allowed to observe the winner's current ownership. Acquisition itself is
    # proven by the single claimed event; HTTP converts a different-owner
    # observation into 409.
    assert all(row["error"] is None for row in results), results
    assert {row["claimed_by"] for row in results} == {winner}, results


def test_same_actor_reclaim_is_idempotent(tmp_path: Path):
    s = Store(str(tmp_path / "claim-idempotent.db"))
    item = s.create(req())

    first = s.claim(item.id, "alice")
    second = s.claim(item.id, "alice")

    assert first.claimed_by == "alice"
    assert second.claimed_by == "alice"

    with s._conn() as c:
        claim_events = c.execute(
            "SELECT actor FROM events WHERE request_id=? AND type='claimed'",
            (item.id,),
        ).fetchall()
    assert len(claim_events) == 1


def test_concurrent_http_claim_has_one_winner(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app import main

    main.store = Store(str(tmp_path / "http-claim-race.db"))
    item = main.store.create(req())
    start = threading.Barrier(2)

    def post(actor: str):
        client = TestClient(main.app)
        start.wait(timeout=5)
        response = client.post(
            f"/v1/requests/{item.id}/claim",
            json={"actor": actor},
        )
        return actor, response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(post, "alice")
        b = pool.submit(post, "bob")
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert sorted(status for _, status, _ in results) == [200, 409], results

    winner = next(actor for actor, status, _ in results if status == 200)
    loser = next(actor for actor, status, _ in results if status == 409)
    assert winner != loser

    final = main.store.get(item.id)
    assert final is not None
    assert final.status.value == "claimed"
    assert final.claimed_by == winner

    with main.store._conn() as c:
        claim_events = c.execute(
            "SELECT actor FROM events WHERE request_id=? AND type='claimed'",
            (item.id,),
        ).fetchall()

    assert [row["actor"] for row in claim_events] == [winner]


def test_same_actor_http_reclaim_is_idempotent(tmp_path: Path):
    from app import main

    main.store = Store(str(tmp_path / "http-claim-idempotent.db"))
    item = main.store.create(req())
    client = TestClient(main.app)

    first = client.post(
        f"/v1/requests/{item.id}/claim",
        json={"actor": "alice"},
    )
    second = client.post(
        f"/v1/requests/{item.id}/claim",
        json={"actor": "alice"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["claimed_by"] == "alice"

    with main.store._conn() as c:
        claim_events = c.execute(
            "SELECT actor FROM events WHERE request_id=? AND type='claimed'",
            (item.id,),
        ).fetchall()
    assert len(claim_events) == 1


def test_wait_retries_transport_error_after_request_exists(monkeypatch):
    import httpx
    import humanqueue.client as client_module
    from humanqueue.client import HumanQueue

    calls = []

    def fake_get(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            raise httpx.ConnectError("gateway restarting")
        return httpx.Response(
            200,
            json={
                "request": {
                    "status": "resolved",
                    "resolution": {
                        "action": "approve",
                        "values": {"recovered": True},
                    },
                }
            },
        )

    monkeypatch.setattr(client_module.httpx, "get", fake_get)
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    decision = HumanQueue("http://127.0.0.1:9999").wait(
        "attn_restart",
        timeout=5,
        poll_interval=0.01,
    )

    assert len(calls) == 2
    assert decision == {
        "action": "approve",
        "values": {"recovered": True},
    }


def test_wait_does_not_hide_http_protocol_errors(monkeypatch):
    import httpx
    import pytest
    import humanqueue.client as client_module
    from humanqueue.client import HumanQueue, HumanQueueError

    calls = []

    def fake_get(*args, **kwargs):
        calls.append(args[0])
        return httpx.Response(
            401,
            json={"detail": "bad token"},
        )

    monkeypatch.setattr(client_module.httpx, "get", fake_get)

    with pytest.raises(HumanQueueError, match="HTTP 401"):
        HumanQueue("http://127.0.0.1:9999").wait(
            "attn_auth",
            timeout=5,
            poll_interval=0.01,
        )

    assert len(calls) == 1


def test_store_connection_context_releases_sqlite_handle(tmp_path: Path):
    import sqlite3
    import pytest

    store = Store(str(tmp_path / "close-handle.db"))
    with store._conn() as conn:
        conn.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_duplicate_idempotent_human_post_does_not_republish_channel(tmp_path: Path, monkeypatch):
    from app import main

    main.store = Store(str(tmp_path / "idempotent-publish.db"))
    publishes = []

    monkeypatch.setattr(
        main,
        "publish_request",
        lambda item: publishes.append(item.id) or [{"channel": "test", "delivered": True}],
    )

    client = TestClient(main.app)
    payload = {
        "uri": "human://approve",
        "source": "agent",
        "ref": "retryable-operation",
        "title": "Continue?",
        "idempotency_key": "agent:retryable-operation:approval",
    }

    first = client.post("/v1/human", json=payload)
    second = client.post("/v1/human", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    first_id = first.json()["request"]["id"]
    second_id = second.json()["request"]["id"]
    assert first_id == second_id

    # A transport retry must not create another human-facing notification.
    assert publishes == [first_id]

    events = main.store.events(first_id)
    assert len([event for event in events if event["type"] == "created"]) == 1
    assert len([event for event in events if event["type"] == "channel_delivered"]) == 1


def test_concurrent_independent_store_instances_create_one_idempotent_request(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "concurrent-idempotent-create.db")
    left = Store(db)
    right = Store(db)
    start = threading.Barrier(2)

    def create(store: Store):
        start.wait(timeout=5)
        try:
            item, created = store.create_with_status(
                req(
                    source="agent",
                    source_ref="same-operation",
                    idempotency_key="same-idempotency-key",
                )
            )
            return {
                "error": None,
                "id": item.id,
                "created": created,
            }
        except Exception as exc:
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "id": None,
                "created": None,
            }

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(create, left)
        b = pool.submit(create, right)
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert all(result["error"] is None for result in results), results
    ids = {result["id"] for result in results}
    assert len(ids) == 1, results
    assert sorted(result["created"] for result in results) == [False, True]

    item_id = next(iter(ids))
    with left._conn() as c:
        rows = c.execute(
            "SELECT id FROM requests WHERE source=? AND idempotency_key=?",
            ("agent", "same-idempotency-key"),
        ).fetchall()
        events = c.execute(
            "SELECT type FROM events WHERE request_id=? ORDER BY seq",
            (item_id,),
        ).fetchall()

    assert len(rows) == 1
    assert [row["type"] for row in events].count("created") == 1


def test_request_commit_persists_durable_channel_publish_intent(tmp_path: Path):
    db = str(tmp_path / "durable-channel-outbox.db")
    store = Store(db)

    item, created = store.create_with_status(
        req(
            source="agent",
            source_ref="crash-window",
            idempotency_key="crash-window:1",
        )
    )
    assert created is True

    outbox = store.channel_outbox(item.id)
    assert outbox is not None
    assert outbox["status"] == "pending"
    assert outbox["attempts"] == 0

    # The publish intent is durable even before any Gateway background task
    # gets a chance to run.
    events = store.events(item.id)
    assert [event["type"] for event in events] == ["created"]


def test_expired_channel_publish_lease_is_reclaimable_by_another_worker(tmp_path: Path):
    from datetime import timedelta

    db = str(tmp_path / "reclaim-outbox-lease.db")
    left = Store(db)
    right = Store(db)

    item, created = left.create_with_status(
        req(
            source="agent",
            source_ref="lease-crash",
            idempotency_key="lease-crash:1",
        )
    )
    assert created is True

    claimed = left.claim_channel_outbox(
        request_id=item.id,
        limit=1,
        lease_seconds=30,
    )
    assert claimed == [item.id]

    with left._conn() as c:
        expired = (left._now() - timedelta(seconds=1)).isoformat()
        c.execute(
            "UPDATE channel_outbox SET lease_until=? WHERE request_id=?",
            (expired, item.id),
        )

    reclaimed = right.claim_channel_outbox(
        request_id=item.id,
        limit=1,
        lease_seconds=30,
    )
    assert reclaimed == [item.id]

    outbox = right.channel_outbox(item.id)
    assert outbox is not None
    assert outbox["status"] == "processing"
    assert outbox["attempts"] == 2


def test_all_create_apis_publish_through_outbox_once(tmp_path: Path, monkeypatch):
    """Every canonical create surface must have exactly one human-facing publish path."""
    from app import main
    from app.connector_registry import ConnectorRegistry

    db = str(tmp_path / "all-create-paths-outbox.db")
    main.store = Store(db)
    main.connector_registry = ConnectorRegistry(db)

    deliveries = []

    def fake_publish(item):
        deliveries.append(item.id)
        return [{
            "channel": "probe",
            "delivered": True,
            "request_id": item.id,
        }]

    monkeypatch.setattr(main, "publish_request", fake_publish)
    client = TestClient(main.app)

    human = client.post(
        "/v1/human",
        json={
            "uri": "human://approve",
            "source": "human-api",
            "ref": "human-1",
            "title": "Human API boundary",
        },
    )
    assert human.status_code == 201
    human_id = human.json()["request"]["id"]

    canonical_payload = req(
        source="requests-api",
        source_ref="requests-1",
        title="Canonical request boundary",
    ).model_dump(mode="json")
    canonical = client.post("/v1/requests", json=canonical_payload)
    assert canonical.status_code == 201
    canonical_id = canonical.json()["id"]

    imported = client.post(
        "/v1/import",
        json={
            "adapter": "a2a",
            "payload": {
                "task": {
                    "id": "import-1",
                    "status": {
                        "state": "input-required",
                        "message": "Imported boundary",
                    },
                }
            },
        },
    )
    assert imported.status_code == 201
    import_id = imported.json()["id"]

    ids = [human_id, canonical_id, import_id]
    assert sorted(deliveries) == sorted(ids), deliveries

    for rid in ids:
        outbox = main.store.channel_outbox(rid)
        assert outbox is not None
        assert outbox["status"] == "done"
        assert outbox["attempts"] == 1

        events = main.store.events(rid)
        delivered = [event for event in events if event["type"] == "channel_delivered"]
        claimed = [event for event in events if event["type"] == "channel_publish_claimed"]
        completed = [event for event in events if event["type"] == "channel_publish_completed"]
        assert len(delivered) == 1, (rid, events)
        assert len(claimed) == 1, (rid, events)
        assert len(completed) == 1, (rid, events)


def test_canonical_and_import_create_paths_never_call_legacy_direct_publisher(tmp_path: Path, monkeypatch):
    """The durable outbox is the only supported API publish path."""
    from app import main
    from app.connector_registry import ConnectorRegistry

    db = str(tmp_path / "no-legacy-direct-publish.db")
    main.store = Store(db)
    main.connector_registry = ConnectorRegistry(db)

    async def fake_drain(rid):
        # Leave the row pending so this test proves endpoint routing only.
        assert main.store.channel_outbox(rid)["status"] == "pending"

    def forbidden_direct_publish(item):
        raise AssertionError(
            f"API bypassed durable outbox for {item.id}"
        )

    monkeypatch.setattr(main, "_drain_channel_outbox_request", fake_drain)
    monkeypatch.setattr(main, "_publish_and_record", forbidden_direct_publish)
    client = TestClient(main.app)

    canonical = client.post(
        "/v1/requests",
        json=req(source="canonical", source_ref="c-1").model_dump(mode="json"),
    )
    assert canonical.status_code == 201

    imported = client.post(
        "/v1/import",
        json={
            "adapter": "a2a",
            "payload": {
                "task": {
                    "id": "i-1",
                    "status": {
                        "state": "input-required",
                        "message": "Need input",
                    },
                }
            },
        },
    )
    assert imported.status_code == 201


def test_sdk_create_retry_reuses_generated_idempotency_key(monkeypatch):
    import httpx
    import humanqueue.client as client_module
    from humanqueue.client import HumanQueue

    payloads = []
    responses = [
        httpx.ReadTimeout("lost create acknowledgement"),
        httpx.Response(
            201,
            json={
                "request": {
                    "id": "attn_recovered",
                    "status": "pending",
                }
            },
        ),
    ]

    def fake_post(*args, **kwargs):
        payloads.append(dict(kwargs["json"]))
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    item = HumanQueue("http://127.0.0.1:9999", timeout=0.1).ask(
        "human://approve",
        source="sdk",
        ref="run-1",
        title="Recover create ack?",
        wait=False,
    )

    assert item["id"] == "attn_recovered"
    assert len(payloads) == 2
    first_key = payloads[0]["idempotency_key"]
    assert first_key.startswith("humanq-client:")
    assert payloads[1]["idempotency_key"] == first_key


def test_sdk_create_retry_preserves_explicit_idempotency_key(monkeypatch):
    import httpx
    import humanqueue.client as client_module
    from humanqueue.client import HumanQueue

    payloads = []

    def fake_post(*args, **kwargs):
        payloads.append(dict(kwargs["json"]))
        if len(payloads) == 1:
            raise httpx.ConnectError("connection reset after commit")
        return httpx.Response(
            201,
            json={
                "request": {
                    "id": "attn_explicit",
                    "status": "pending",
                }
            },
        )

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    HumanQueue("http://127.0.0.1:9999").ask(
        "human://approve",
        source="sdk",
        ref="run-2",
        title="Explicit idempotency",
        idempotency_key="operator-supplied-key",
    )

    assert [p["idempotency_key"] for p in payloads] == [
        "operator-supplied-key",
        "operator-supplied-key",
    ]


def test_sdk_create_retry_does_not_retry_client_errors(monkeypatch):
    import humanqueue.client as client_module
    import pytest
    from humanqueue.client import HumanQueue, HumanQueueError

    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(422, json={"detail": "bad boundary"})

    monkeypatch.setattr(client_module.httpx, "post", fake_post)

    with pytest.raises(HumanQueueError, match="HTTP 422"):
        HumanQueue("http://127.0.0.1:9999").ask(
            "human://approve",
            source="sdk",
            ref="run-3",
            title="Bad request",
        )

    assert len(calls) == 1


def test_sdk_create_retry_retries_ambiguous_server_error(monkeypatch):
    import humanqueue.client as client_module
    from humanqueue.client import HumanQueue

    payloads = []

    def fake_post(*args, **kwargs):
        payloads.append(dict(kwargs["json"]))
        if len(payloads) == 1:
            return httpx.Response(500, json={"detail": "late failure"})
        return httpx.Response(
            201,
            json={
                "request": {
                    "id": "attn_after_500",
                    "status": "pending",
                }
            },
        )

    monkeypatch.setattr(client_module.httpx, "post", fake_post)
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    item = HumanQueue("http://127.0.0.1:9999").ask(
        "human://approve",
        source="sdk",
        ref="run-4",
        title="Recover late server failure",
    )

    assert item["id"] == "attn_after_500"
    assert len(payloads) == 2
    assert payloads[0]["idempotency_key"] == payloads[1]["idempotency_key"]


def test_human_resolution_and_machine_timeout_race_has_one_terminal_outcome(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "human-vs-timeout-race.db")
    seed = Store(db)
    human_store = Store(db)
    system_store = Store(db)
    item = seed.create(req())
    start = threading.Barrier(2)

    def human():
        start.wait(timeout=5)
        resolved, finalized = human_store.resolve(
            item.id, "alice",
            {"action": "approve", "values": {"safe": True}},
            actor_kind="human",
        )
        return finalized, resolved.status.value if resolved else None

    def timeout():
        start.wait(timeout=5)
        result = system_store.apply_machine_outcome(
            item.id,
            actor="deadline-worker",
            actor_kind="system",
            outcome="expired",
            reason="approval deadline elapsed",
        )
        return result.status.value if result else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(human)
        b = pool.submit(timeout)
        a.result(timeout=10)
        b.result(timeout=10)

    canonical = seed.get(item.id)
    assert canonical is not None
    assert canonical.status.value in {"resolved", "expired"}

    events = seed.events(item.id)
    resolved_events = [e for e in events if e["type"] == "resolved"]
    expired_events = [e for e in events if e["type"] == "machine_expired"]
    assert len(resolved_events) + len(expired_events) == 1, events

    if canonical.status.value == "resolved":
        assert len(resolved_events) == 1
        assert canonical.resolution is not None
        assert canonical.resolution["provenance"]["actor_kind"] == "human"
    else:
        assert len(expired_events) == 1
        assert canonical.resolution is None


def test_concurrent_duplicate_same_actor_resolution_is_idempotent(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "concurrent-same-actor-resolution.db")
    seed = Store(db)
    left = Store(db)
    right = Store(db)
    item = seed.create(req())
    start = threading.Barrier(2)

    def submit(store: Store):
        start.wait(timeout=5)
        return store.resolve(
            item.id,
            "alice",
            {"action": "approve", "values": {"ticket": "same"}},
            actor_kind="human",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(submit, left)
        b = pool.submit(submit, right)
        results = [a.result(timeout=10), b.result(timeout=10)]

    assert sum(1 for _, finalized in results if finalized) == 1

    canonical = seed.get(item.id)
    assert canonical is not None
    assert canonical.status.value == "resolved"
    assert canonical.resolution is not None
    assert all(resolved.resolution == canonical.resolution for resolved, _ in results)

    events = seed.events(item.id)
    assert len([e for e in events if e["type"] == "vote"]) == 1, events
    assert len([e for e in events if e["type"] == "resolved"]) == 1, events


def test_terminal_compare_and_set_survives_repeated_human_timeout_races(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "terminal-cas-stress.db")
    seed = Store(db)
    human_store = Store(db)
    system_store = Store(db)

    for index in range(20):
        item = seed.create(
            req(source="stress", source_ref=f"race-{index}", title=f"Race {index}")
        )
        start = threading.Barrier(2)

        def human():
            start.wait(timeout=5)
            return human_store.resolve(
                item.id,
                f"human-{index}",
                {"action": "approve", "values": {"iteration": index}},
                actor_kind="human",
            )

        def timeout():
            start.wait(timeout=5)
            return system_store.apply_machine_outcome(
                item.id,
                actor="deadline-worker",
                actor_kind="system",
                outcome="expired",
                reason=f"deadline {index}",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            human_future = pool.submit(human)
            timeout_future = pool.submit(timeout)
            human_future.result(timeout=10)
            timeout_future.result(timeout=10)

        canonical = seed.get(item.id)
        assert canonical is not None
        assert canonical.status.value in {"resolved", "expired"}

        terminal = [
            e for e in seed.events(item.id)
            if e["type"] in {"resolved", "machine_expired"}
        ]
        assert len(terminal) == 1, (index, canonical, terminal)


def test_human_resolution_and_supersession_race_has_one_terminal_meaning(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "resolve-vs-supersede.db")
    seed = Store(db)
    resolver = Store(db)
    creator = Store(db)

    original = seed.create(
        req(
            source="agent",
            source_ref="original",
            supersession_key="deployment-choice",
            title="Deploy original target?",
        )
    )
    start = threading.Barrier(2)

    def resolve_old():
        start.wait(timeout=5)
        resolved, finalized = resolver.resolve(
            original.id,
            "alice",
            {"action": "approve", "values": {"target": "original"}},
            actor_kind="human",
        )
        return {
            "finalized": finalized,
            "status": resolved.status.value if resolved else None,
        }

    def create_replacement():
        start.wait(timeout=5)
        item = creator.create(
            req(
                source="agent",
                source_ref="replacement",
                supersession_key="deployment-choice",
                title="Deploy replacement target?",
            )
        )
        return item.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        resolve_future = pool.submit(resolve_old)
        replace_future = pool.submit(create_replacement)
        resolve_result = resolve_future.result(timeout=10)
        replacement_id = replace_future.result(timeout=10)

    old = seed.get(original.id)
    replacement = seed.get(replacement_id)
    assert old is not None
    assert replacement is not None
    assert replacement.status.value == "pending"

    events = seed.events(original.id)
    terminal = [
        event for event in events
        if event["type"] in {"resolved", "superseded"}
    ]
    assert len(terminal) == 1, (resolve_result, events)

    if old.status.value == "resolved":
        assert terminal[0]["type"] == "resolved"
        assert old.superseded_by is None
        assert old.resolution is not None
        assert old.resolution["action"] == "approve"
        assert resolve_result["finalized"] is True
    else:
        assert old.status.value == "superseded"
        assert terminal[0]["type"] == "superseded"
        assert old.superseded_by == replacement_id
        assert old.resolution is None
        assert resolve_result["finalized"] is False


def test_two_concurrent_replacements_leave_one_active_tip(tmp_path: Path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db = str(tmp_path / "concurrent-supersession-chain.db")
    seed = Store(db)
    left = Store(db)
    right = Store(db)
    original = seed.create(
        req(
            source="agent",
            source_ref="v0",
            supersession_key="same-intent",
            title="Version 0",
        )
    )
    start = threading.Barrier(2)

    def replace(store: Store, ref: str):
        start.wait(timeout=5)
        return store.create(
            req(
                source="agent",
                source_ref=ref,
                supersession_key="same-intent",
                title=ref,
            )
        ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(replace, left, "v1")
        b = pool.submit(replace, right, "v2")
        replacement_ids = {a.result(timeout=10), b.result(timeout=10)}

    rows = [seed.get(original.id)] + [seed.get(rid) for rid in replacement_ids]
    assert all(row is not None for row in rows)

    active = [row for row in rows if row.status.value == "pending"]
    superseded = [row for row in rows if row.status.value == "superseded"]

    # Serializing replacement creation creates a deterministic chain: exactly
    # one newest request remains active, every older version is superseded.
    assert len(active) == 1, rows
    assert len(superseded) == 2, rows

    superseded_ids = {row.id for row in superseded}
    assert original.id in superseded_ids
    assert active[0].id in replacement_ids

    terminal_events = {
        row.id: [e for e in seed.events(row.id) if e["type"] == "superseded"]
        for row in superseded
    }
    assert all(len(events) == 1 for events in terminal_events.values())


def test_resume_payload_and_signature_are_stable_for_same_canonical_decision(tmp_path: Path):
    import json
    from app.resume import signed_payload

    item = Store(str(tmp_path / "stable-resume-payload.db")).create(
        req(
            source="agent",
            source_ref="run-stable",
            resume={
                "mode": "webhook",
                "url": "https://example.invalid/resume",
                "secret": "stable-secret",
            },
        )
    )

    first_resolution = {
        "action": "approve",
        "values": {"target": "prod", "count": 2},
        "provenance": {"actor": "alice", "actor_kind": "human"},
    }
    # Same semantic object with deliberately different Python dict key order.
    second_resolution = {
        "provenance": {"actor_kind": "human", "actor": "alice"},
        "values": {"count": 2, "target": "prod"},
        "action": "approve",
    }

    body1, sig1 = signed_payload(item, first_resolution)
    body2, sig2 = signed_payload(item, second_resolution)

    assert body1 == body2
    assert sig1 == sig2
    assert sig1 is not None and sig1.startswith("sha256=")

    decoded = json.loads(body1)
    assert decoded["event"] == "attention.resolved"
    assert decoded["request_id"] == item.id
    assert decoded["source"] == "agent"
    assert decoded["source_ref"] == "run-stable"
    assert decoded["resolution"]["action"] == "approve"


def test_resume_repeated_attempt_keeps_same_transport_identity(tmp_path: Path, monkeypatch):
    import app.resume as resume_module

    item = Store(str(tmp_path / "stable-resume-attempt.db")).create(
        req(
            source="agent",
            source_ref="run-transport",
            resume={
                "mode": "webhook",
                "url": "https://example.invalid/resume",
                "secret": "transport-secret",
            },
        )
    )
    resolution = {
        "action": "approve",
        "values": {"ticket": "42"},
        "provenance": {"actor": "alice", "actor_kind": "human"},
    }
    attempts = []

    class RecordingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, content, headers):
            attempts.append({
                "url": str(url),
                "content": bytes(content),
                "headers": dict(headers),
            })
            return httpx.Response(
                200,
                json={"request_id": item.id, "resumed": True},
            )

    monkeypatch.setattr(resume_module.httpx, "AsyncClient", RecordingAsyncClient)

    first = asyncio.run(resume_module.resume(item, resolution))
    second = asyncio.run(resume_module.resume(item, resolution))

    assert first["confirmed"] is True
    assert second["confirmed"] is True
    assert len(attempts) == 2

    assert attempts[0]["content"] == attempts[1]["content"]
    assert attempts[0]["headers"]["x-attention-request-id"] == item.id
    assert attempts[1]["headers"]["x-attention-request-id"] == item.id
    assert (
        attempts[0]["headers"]["x-attention-signature"]
        == attempts[1]["headers"]["x-attention-signature"]
    )


def test_resume_changed_decision_changes_signed_transport_identity(tmp_path: Path):
    from app.resume import signed_payload

    item = Store(str(tmp_path / "changed-resume-payload.db")).create(
        req(
            resume={
                "mode": "webhook",
                "url": "https://example.invalid/resume",
                "secret": "change-secret",
            }
        )
    )

    approve_body, approve_sig = signed_payload(
        item,
        {"action": "approve", "values": {}},
    )
    reject_body, reject_sig = signed_payload(
        item,
        {"action": "reject", "values": {}},
    )

    assert approve_body != reject_body
    assert approve_sig != reject_sig


def test_resolution_atomically_enqueues_webhook_resume_intent(tmp_path: Path):
    from app.models import ResumeTarget

    store = Store(str(tmp_path / "resume-intent.db"))
    item = store.create(
        req(
            resume=ResumeTarget(
                mode="webhook",
                url="http://127.0.0.1:9999/resume",
                secret="test-secret",
            )
        )
    )

    resolved, finalized = store.resolve(
        item.id,
        "alice",
        {"action": "approve", "values": {"ok": True}},
    )

    assert finalized is True
    assert resolved is not None
    assert resolved.status.value == "resolved"

    outbox = store.resume_outbox(item.id)
    assert outbox is not None
    assert outbox["status"] == "pending"
    assert int(outbox["attempts"]) == 0

    events = store.events(item.id)
    event_types = [event["type"] for event in events]
    assert "resolved" in event_types
    assert "resume_enqueued" in event_types
    assert event_types.index("resolved") < event_types.index("resume_enqueued")


def test_abandoned_resume_attempt_becomes_uncertain_and_is_not_replayed(tmp_path: Path):
    from app.models import ResumeTarget

    db = str(tmp_path / "resume-uncertain.db")
    first = Store(db)
    item = first.create(
        req(
            resume=ResumeTarget(
                mode="webhook",
                url="http://127.0.0.1:9999/resume",
            )
        )
    )
    _, finalized = first.resolve(
        item.id,
        "alice",
        {"action": "approve", "values": {}},
    )
    assert finalized is True

    claimed = first.claim_resume_outbox(
        request_id=item.id,
        limit=1,
        lease_seconds=0,
    )
    assert claimed == [item.id]

    # A different worker observes the expired processing lease. It must not
    # automatically replay a callback whose remote side effect is unknown.
    second = Store(db)
    replay = second.claim_resume_outbox(
        request_id=item.id,
        limit=1,
        lease_seconds=30,
    )
    assert replay == []

    outbox = second.resume_outbox(item.id)
    assert outbox is not None
    assert outbox["status"] == "uncertain"
    assert int(outbox["attempts"]) == 1

    # Repeated reconciliation remains fail-closed and does not increment attempts.
    assert second.claim_resume_outbox(
        request_id=item.id,
        limit=1,
        lease_seconds=30,
    ) == []
    outbox_again = second.resume_outbox(item.id)
    assert outbox_again is not None
    assert outbox_again["status"] == "uncertain"
    assert int(outbox_again["attempts"]) == 1

    uncertain = [
        event
        for event in second.events(item.id)
        if event["type"] == "resume_delivery_uncertain"
    ]
    assert len(uncertain) == 1
    assert uncertain[0]["data"]["automatic_retry"] is False

    metrics = second.metrics()
    assert metrics["resume_outbox"]["uncertain"] == 1
    assert metrics["integrity_last_24h"]["resume_delivery_uncertain"] == 1


def test_quorum_canonical_provenance_is_terminal_finalizer(tmp_path: Path):
    import json

    store = Store(str(tmp_path / "quorum-finalizer-provenance.db"))
    item = store.create(
        req(
            route=RoutePolicy(
                mode="quorum",
                actors=["alice", "bob"],
                quorum=2,
            )
        )
    )

    after_alice, finalized = store.resolve(
        item.id,
        "alice",
        {"action": "approve", "values": {"target": "prod"}},
        actor_kind="human",
    )
    assert finalized is False
    assert after_alice is not None
    assert after_alice.status.value == "claimed"

    after_bob, finalized = store.resolve(
        item.id,
        "bob",
        {"action": "approve", "values": {"target": "prod"}},
        actor_kind="human",
    )
    assert finalized is True
    assert after_bob is not None
    assert after_bob.status.value == "resolved"
    assert after_bob.resolved_by == "bob"
    assert after_bob.resolution["provenance"] == {
        "actor": "bob",
        "actor_kind": "human",
    }
    assert after_bob.resolution["quorum"] == {
        "required": 2,
        "actors": ["alice", "bob"],
        "votes": 2,
    }

    # Vote-level evidence remains independently auditable; changing canonical
    # provenance must not rewrite either participant's vote.
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT actor,resolution FROM votes WHERE request_id=? ORDER BY created_at",
            (item.id,),
        ).fetchall()
    assert [row["actor"] for row in rows] == ["alice", "bob"]
    assert json.loads(rows[0]["resolution"])["provenance"]["actor"] == "alice"
    assert json.loads(rows[1]["resolution"])["provenance"]["actor"] == "bob"
