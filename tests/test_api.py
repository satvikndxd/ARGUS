"""ARGUS API contract tests (FastAPI TestClient over the live app)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health").json()
    assert r["status"] == "OPERATIONAL"
    assert r["decisions_served"] >= 6000


def test_overview_contract():
    r = client.get("/api/overview").json()
    for key in ("world", "scores", "ring_detection", "latency_ms", "daily",
                "risk_histogram", "per_archetype", "communities"):
        assert key in r
    assert r["scores"]["auc_roc"] > 0.9
    assert len(r["daily"]) == 14


def test_evaluate_endpoint():
    sample = client.get("/api/entities/sample").json()
    body = {"consumer_id": sample["ring_members"][0],
            "merchant_id": sample["merchants"][0],
            "device_id": sample["devices"][0],
            "amount": 950.0, "geo": "NG"}
    d = client.post("/v1/transactions:evaluate", json=body).json()
    assert d["risk_score"] > 0.5
    assert d["review_required"] is True
    assert d["graph_evidence"]["entity_id"] == body["consumer_id"]
    assert d["explanation"]


def test_entity_graph_and_404():
    sample = client.get("/api/entities/sample").json()
    ok = client.get(f"/v1/entities/{sample['ring_members'][0]}/graph?depth=2")
    assert ok.status_code == 200 and ok.json()["nodes"]
    assert client.get("/v1/entities/usr_nope/graph").status_code == 404


def test_cases_and_trace():
    cases = client.get("/api/cases").json()["cases"]
    assert cases
    c = client.get(f"/api/cases/{cases[0]['case_id']}").json()
    assert c["trace"] and c["report"]
    assert all(t["tools"] for t in c["trace"])          # every step carries tool tags
    assert c["verdict"] in ("confirmed_fraud", "cleared", "inconclusive")


def test_simulation_endpoint():
    r = client.post("/v1/simulations/synthetic_identity_wave:run?seed=7").json()
    assert r["summary"]["transactions"] == 3000
    assert client.post("/v1/simulations/nope:run").status_code == 404


def test_policies_and_agents():
    assert len(client.get("/api/policies").json()["policies"]) == 5
    assert len(client.get("/api/agents").json()["roster"]) == 15


@pytest.mark.parametrize("path", ["/", "/static/style.css", "/static/app.js"])
def test_console_served(path):
    assert client.get(path).status_code == 200
