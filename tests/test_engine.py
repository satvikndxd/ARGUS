"""ARGUS engine test suite — determinism, policy, graph, and scoring invariants."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest

from argus.engine import ACTIONS, DecisionEngine
from argus.graph import RiskGraph
from argus.simulation import run_scenario
from argus.synth import World


@pytest.fixture(scope="module")
def world():
    return World(seed=1337)


@pytest.fixture(scope="module")
def graph(world):
    return RiskGraph(world)


@pytest.fixture(scope="module")
def engine(world, graph):
    return DecisionEngine(world, graph)


def _txn(world, i=0):
    t = world.txns[i]
    return {"id": t.id, "step": t.step, "consumer_id": t.consumer_id,
            "merchant_id": t.merchant_id, "device_id": t.device_id,
            "card_id": t.card_id, "amount": t.amount, "geo": t.geo}


# ------------------------------------------------------------- determinism
def test_world_is_deterministic():
    a, b = World(seed=42), World(seed=42)
    assert a.stats() == b.stats()
    assert [t.id for t in a.txns[:50]] == [t.id for t in b.txns[:50]]
    assert a.txns[7].amount == b.txns[7].amount


def test_different_seeds_differ():
    assert World(seed=1).stats() != World(seed=2).stats()


def test_decision_replay_is_exact(world, graph):
    e1 = DecisionEngine(world, graph)
    e2 = DecisionEngine(world, graph)
    d1 = e1.evaluate(_txn(world))
    d2 = e2.evaluate(_txn(world))
    d1.pop("latency_ms"); d2.pop("latency_ms")
    assert d1 == d2


# ------------------------------------------------------------- decisioning
def test_decision_shape(engine, world):
    d = engine.evaluate(_txn(world, 1))
    assert d["decision"] in ACTIONS
    assert 0.0 <= d["risk_score"] <= 1.0
    assert 0.5 <= d["confidence"] <= 0.99
    assert d["action_reasons"]
    assert set(d["model_scores"]) == {"tabular", "graph", "behavioral"}
    assert d["explanation"]


def test_ring_member_scores_higher_than_legit(engine, world):
    ring = next(c for c in world.consumers.values() if c.ring_id)
    legit = next(c for c in world.consumers.values()
                 if c.kind == "legit" and c.identity_conf > 0.9)
    base = dict(amount=800.0, card_id="card_x", geo="US", step=10)
    d_ring = engine.evaluate({**base, "id": "t1", "consumer_id": ring.id,
                              "merchant_id": "mer_000", "device_id": ring.devices[0]})
    d_legit = engine.evaluate({**base, "id": "t2", "consumer_id": legit.id,
                               "merchant_id": "mer_000", "device_id": legit.devices[0]})
    assert d_ring["risk_score"] > d_legit["risk_score"]


def test_card_testing_policy_fires(engine, world):
    legit = next(c for c in world.consumers.values() if c.kind == "legit")
    d = None
    for i in range(6):
        d = engine.evaluate({"id": f"ct{i}", "step": 5000, "consumer_id": legit.id,
                             "merchant_id": "mer_001", "device_id": legit.devices[0],
                             "card_id": "card_ct", "amount": 1.5, "geo": legit.geo})
    assert any(p["id"] == "POL-001" for p in d["policy_hits"])
    assert d["decision"] == "decline"


def test_counterfactual_reduces_risk(engine, world):
    ring = next(c for c in world.consumers.values() if c.ring_id)
    d = engine.evaluate({"id": "cf1", "step": 20, "consumer_id": ring.id,
                         "merchant_id": "mer_002", "device_id": ring.devices[0],
                         "card_id": ring.cards[0], "amount": 900.0, "geo": "NG"})
    assert d["counterfactuals"]
    assert all(cf["risk_delta"] <= 0 for cf in d["counterfactuals"])


# ------------------------------------------------------------------- graph
def test_graph_neighborhood_bounded(graph, world):
    ring = next(c for c in world.consumers.values() if c.ring_id)
    sub = graph.neighborhood(ring.id, depth=3, limit=50)
    assert 0 < len(sub["nodes"]) <= 50
    ids = {n["id"] for n in sub["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in sub["edges"])


def test_ring_members_share_community(graph, world):
    ring = next(iter(world.rings.values()))
    comms = {graph.communities[m] for m in ring.members}
    assert len(comms) == 1


def test_no_label_leakage_in_risk(graph, world):
    """Risk propagation must not read ring_id — verify a groomed ring member
    with strong identity is not trivially assigned max risk."""
    scores = [graph.node_risk[c.id] for c in world.consumers.values()
              if c.ring_id and c.identity_conf > 0.6]
    assert scores and min(scores) < 0.95


# -------------------------------------------------------------- simulation
def test_simulation_replay_exact():
    r1 = run_scenario("card_testing_storm", seed=99)
    r2 = run_scenario("card_testing_storm", seed=99)
    assert r1 == r2


def test_simulation_summary_sane():
    r = run_scenario("mule_cashout_campaign", seed=1337)
    s = r["summary"]
    assert 0.5 < s["detection_rate"] <= 1.0
    assert s["loss_prevented_usd"] > 0
    assert s["fraud_caught"] <= s["fraud_attempts"]
    assert len(r["timeline"]) == r["scenario"]["duration_days"]
