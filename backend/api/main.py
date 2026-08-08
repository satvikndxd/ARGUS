"""ARGUS API — FastAPI service exposing the decision plane, graph plane,
agentic plane and simulation plane, and serving the Matrix console UI.

Run:  uvicorn backend.api.main:app --port 8000
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from argus import __version__  # noqa: E402
from argus.agents import AGENT_ROSTER, InvestigationOrchestrator  # noqa: E402
from argus.engine import POLICY_STACK, DecisionEngine  # noqa: E402
from argus.evaluate import evaluate as run_evaluation  # noqa: E402
from argus.graph import RiskGraph  # noqa: E402
from argus.simulation import SCENARIOS, run_scenario  # noqa: E402
from argus.synth import World  # noqa: E402

app = FastAPI(title="ARGUS", version=__version__,
              description="AI-Native Payment Risk Intelligence Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ----------------------------------------------------------------- boot state
BOOT = {"t0": time.time()}
world = World(seed=1337)
graph = RiskGraph(world)
engine = DecisionEngine(world, graph)
orchestrator = InvestigationOrchestrator(engine)

# warm the platform: stream the whole world through the decision engine once
_feed: list[dict] = []
for _txn in world.txns:
    _d = engine.evaluate({
        "id": _txn.id, "step": _txn.step, "consumer_id": _txn.consumer_id,
        "merchant_id": _txn.merchant_id, "device_id": _txn.device_id,
        "card_id": _txn.card_id, "amount": _txn.amount, "geo": _txn.geo,
    })
    _feed.append({
        "transaction_id": _txn.id, "step": _txn.step, "amount": _txn.amount,
        "consumer_id": _txn.consumer_id, "merchant_id": _txn.merchant_id,
        "geo": _txn.geo, "decision": _d["decision"], "risk_score": _d["risk_score"],
        "is_fraud": _txn.is_fraud, "archetype": _txn.archetype,
        "reasons": _d["action_reasons"][:3],
    })

# open investigations on the top high-risk fraud decisions
_ranked = sorted(engine.decisions, key=lambda d: -d["risk_score"])
_seen_entities: set[str] = set()
for _d in _ranked:
    _eid = _d["graph_evidence"]["entity_id"]
    if _d["risk_score"] >= 0.7 and _eid not in _seen_entities:
        _seen_entities.add(_eid)
        orchestrator.investigate(_d)
    if len(orchestrator.cases) >= 14:
        break

_metrics_cache: dict | None = None


def metrics() -> dict:
    global _metrics_cache
    if _metrics_cache is None:
        _metrics_cache = run_evaluation(seed=1337)
    return _metrics_cache


# ------------------------------------------------------------------- schemas
class TxnRequest(BaseModel):
    transaction_id: str | None = None
    amount: float
    currency: str = "USD"
    consumer_id: str
    merchant_id: str
    device_id: str
    card_id: str = "card_adhoc"
    geo: str = "US"
    step: int = 999999


# ----------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    return {"status": "OPERATIONAL", "version": __version__,
            "uptime_s": round(time.time() - BOOT["t0"], 1),
            "decisions_served": len(engine.decisions)}


@app.get("/api/overview")
def overview():
    m = metrics()
    stats = world.stats()
    dist = {}
    for d in _feed:
        dist[d["decision"]] = dist.get(d["decision"], 0) + 1
    day_len = max(1, (_feed[-1]["step"] if _feed else 1) // 14)
    series = []
    for day in range(14):
        rows = [f for f in _feed if day * day_len <= f["step"] < (day + 1) * day_len]
        fraud = [f for f in rows if f["is_fraud"]]
        caught = [f for f in fraud if f["decision"] in ("review", "decline", "step_up")]
        series.append({
            "day": day + 1, "txns": len(rows), "fraud": len(fraud), "caught": len(caught),
            "volume": round(sum(f["amount"] for f in rows), 2),
            "fraud_volume": round(sum(f["amount"] for f in fraud), 2),
            "prevented": round(sum(f["amount"] for f in caught), 2),
        })
    return {
        "world": stats, "scores": m["scores"], "ring_detection": m["ring_detection"],
        "latency_ms": m["latency_ms"], "action_mix": dist, "daily": series,
        "risk_histogram": m["risk_histogram"],
        "per_archetype": m["per_archetype_detection"],
        "cases_open": len(orchestrator.cases),
        "communities": graph.community_summary()[:8],
    }


@app.get("/api/feed")
def feed(limit: int = 40, min_risk: float = 0.0):
    rows = [f for f in _feed if f["risk_score"] >= min_risk]
    return {"decisions": rows[-limit:][::-1], "total": len(rows)}


@app.post("/v1/transactions:evaluate")
def evaluate_txn(req: TxnRequest):
    d = engine.evaluate({
        "id": req.transaction_id or f"txn_adhoc_{len(engine.decisions)}",
        "step": req.step, "consumer_id": req.consumer_id,
        "merchant_id": req.merchant_id, "device_id": req.device_id,
        "card_id": req.card_id, "amount": req.amount, "geo": req.geo,
    })
    return d


@app.get("/v1/transactions/{txn_id}/decision")
def get_decision(txn_id: str):
    for d in engine.decisions:
        if d["transaction_id"] == txn_id:
            return d
    raise HTTPException(404, "decision not found")


@app.get("/api/entities/sample")
def entity_sample():
    """Interesting entities for the console pickers."""
    ring_ids = [r.members[0] for r in world.rings.values()]
    legit = [c.id for c in world.consumers.values() if c.kind == "legit"][:5]
    return {
        "ring_members": ring_ids, "legit": legit,
        "merchants": list(world.merchants)[:8],
        "devices": [d.id for d in world.devices.values() if d.kind in ("device_farm", "emulator")][:6],
    }


@app.get("/v1/entities/{entity_id}/graph")
def entity_graph(entity_id: str, depth: int = 2, limit: int = 120):
    sub = graph.neighborhood(entity_id, depth=depth, limit=limit)
    if not sub["nodes"]:
        raise HTTPException(404, "entity not found")
    return sub


@app.get("/v1/graph/communities")
def communities():
    return {"communities": graph.community_summary()[:20]}


@app.post("/v1/graph/path")
def graph_path(body: dict):
    path = graph.shortest_path(body.get("from", ""), body.get("to", ""))
    return {"path": path, "hops": max(0, len(path) - 1)}


@app.get("/api/cases")
def cases():
    return {"cases": [{k: c[k] for k in
                       ("case_id", "transaction_id", "entity_id", "risk_score",
                        "verdict", "queue", "archetype", "agents_dispatched")}
                      for c in orchestrator.cases]}


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str):
    for c in orchestrator.cases:
        if c["case_id"] == case_id:
            return c
    raise HTTPException(404, "case not found")


@app.post("/v1/investigations")
def open_investigation(body: dict):
    txn_id = body.get("transaction_id")
    for d in engine.decisions:
        if d["transaction_id"] == txn_id:
            return orchestrator.investigate(d)
    raise HTTPException(404, "no decision for that transaction")


@app.get("/api/agents")
def agents():
    return {"roster": [{"id": a, "name": n, "mission": m} for a, n, m in AGENT_ROSTER]}


@app.get("/api/policies")
def policies():
    return {"policies": POLICY_STACK}


@app.get("/v1/simulations")
def list_scenarios():
    return {"scenarios": list(SCENARIOS.values())}


@app.post("/v1/simulations/{scenario_id}:run")
def simulate(scenario_id: str, seed: int = 1337):
    try:
        return run_scenario(scenario_id, seed=seed)
    except KeyError:
        raise HTTPException(404, "unknown scenario")


@app.get("/api/metrics")
def metrics_endpoint():
    return metrics()


# ------------------------------------------------------------------ frontend
FRONTEND = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND / "static")), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND / "index.html"))
