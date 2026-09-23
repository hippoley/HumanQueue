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
