"""ARGUS decision engine — the real-time brain.

Ensemble of three scorers (tabular, graph, behavioral-velocity) fused with a
calibrated blend, evaluated against a declarative policy stack, emitting an
evidence-linked, counterfactual-capable explanation bundle for every decision.

Design goals mirror the platform spec:
  * hot path only touches precomputed features (no unbounded traversals)
  * every decision carries reason codes, model attributions, policy hits
  * deterministic replay: same world seed + same txn => same decision
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque

from .graph import RiskGraph
from .synth import HIGH_RISK_MCC, World

ACTIONS = ["approve", "monitor", "step_up", "review", "decline"]

POLICY_STACK = [
    {"id": "POL-001", "name": "card_testing_velocity", "version": 3,
     "when": "velocity_1h >= 5 and amount < 5", "action": "decline",
     "desc": "Rapid low-value enumeration pattern"},
    {"id": "POL-002", "name": "device_farm_block", "version": 2,
     "when": "device_sharing_max >= 5", "action": "review",
     "desc": "Device shared by 5+ accounts"},
    {"id": "POL-003", "name": "geo_impossible", "version": 1,
     "when": "geo_mismatch and amount > 500", "action": "step_up",
     "desc": "Cross-geo high-value transaction"},
    {"id": "POL-004", "name": "young_identity_high_value", "version": 4,
     "when": "identity_conf < 0.5 and amount > 250", "action": "review",
     "desc": "Low-confidence identity moving high value"},
    {"id": "POL-005", "name": "collusion_merchant_watch", "version": 1,
     "when": "merchant_colluding", "action": "review",
     "desc": "Merchant on collusion watchlist"},
]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class VelocityTracker:
    """Sliding-window velocity counters per consumer / device / card."""

    def __init__(self, window_steps: int = 60):
        self.window = window_steps
        self.events: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str, step: int) -> int:
        q = self.events[key]
        q.append(step)
        while q and q[0] < step - self.window:
            q.popleft()
        return len(q)

    def count(self, key: str, step: int) -> int:
        q = self.events[key]
        while q and q[0] < step - self.window:
            q.popleft()
        return len(q)


class DecisionEngine:
    def __init__(self, world: World, graph: RiskGraph):
        self.world = world
        self.graph = graph
        self.velocity = VelocityTracker()
        self.decisions: list[dict] = []
        self.model_versions = {
            "tabular": "argus-gbm-v2.4.1",
            "graph": "argus-gnn-sage-v1.7.0",
            "behavioral": "argus-seq-txf-v0.9.3",
            "blend": "argus-blend-v3.1.0",
        }

    # ----------------------------------------------------------- featureize
    def features(self, txn: dict) -> dict:
        w = self.world
        cid = txn["consumer_id"]
        consumer = w.consumers.get(cid)
        merchant = w.merchants.get(txn["merchant_id"])
        device = w.devices.get(txn["device_id"])
        step = txn.get("step", 0)

        gfeat = self.graph.graph_features(cid)
        v_user = self.velocity.hit(f"u:{cid}", step)
        v_dev = self.velocity.hit(f"d:{txn['device_id']}", step)
        v_card = self.velocity.hit(f"c:{txn['card_id']}", step)

        return {
            "amount": float(txn["amount"]),
            "log_amount": math.log1p(float(txn["amount"])),
            "identity_conf": consumer.identity_conf if consumer else 0.3,
            "account_age_days": consumer.age_days if consumer else 0,
            "geo_mismatch": bool(consumer and txn.get("geo") and txn["geo"] != consumer.geo),
            "merchant_cb_rate": merchant.chargeback_rate if merchant else 0.01,
            "merchant_colluding": bool(merchant and merchant.colluding),
            "merchant_high_risk_mcc": bool(merchant and merchant.mcc in HIGH_RISK_MCC),
            "device_entropy": device.fingerprint_entropy if device else 0.5,
            "device_kind": device.kind if device else "unknown",
            "velocity_1h": v_user,
            "device_velocity_1h": v_dev,
            "card_velocity_1h": v_card,
            **gfeat,
        }

    # ------------------------------------------------------------- scorers
    def score_tabular(self, f: dict) -> tuple[float, list[tuple[str, float]]]:
        contrib = [
            ("identity_conf", (0.75 - f["identity_conf"]) * 3.2),
            ("account_age", (60 - min(f["account_age_days"], 60)) / 60 * 1.6),
            ("merchant_cb_rate", f["merchant_cb_rate"] * 24),
            ("high_risk_mcc", 0.55 if f["merchant_high_risk_mcc"] else 0.0),
            ("geo_mismatch", 0.8 if f["geo_mismatch"] else 0.0),
            ("amount_shape", max(0.0, (f["log_amount"] - 5.6)) * 0.7),
            ("micro_amount", 0.9 if f["amount"] < 5 else 0.0),
        ]
        z = sum(v for _, v in contrib) - 1.9
        return _sigmoid(z), contrib

    def score_graph(self, f: dict) -> tuple[float, list[tuple[str, float]]]:
        contrib = [
            ("entity_risk", f["entity_risk"] * 2.6),
            ("device_sharing", min(f["device_sharing_max"], 10) * 0.34),
            ("linked_high_risk", min(f["linked_high_risk"], 12) * 0.22),
            ("community_risk", f["community_risk"] * 1.4),
            ("device_entropy", (0.6 - min(f["device_entropy"], 0.6)) * 2.0),
        ]
        z = sum(v for _, v in contrib) - 2.4
        return _sigmoid(z), contrib

    def score_behavioral(self, f: dict) -> tuple[float, list[tuple[str, float]]]:
        contrib = [
            ("velocity_1h", min(f["velocity_1h"], 20) * 0.30),
            ("device_velocity", min(f["device_velocity_1h"], 20) * 0.22),
            ("card_velocity", min(f["card_velocity_1h"], 20) * 0.18),
            ("burst_micro", 1.1 if (f["velocity_1h"] >= 4 and f["amount"] < 5) else 0.0),
        ]
        z = sum(v for _, v in contrib) - 2.1
        return _sigmoid(z), contrib

    # -------------------------------------------------------------- policy
    def eval_policies(self, f: dict) -> list[dict]:
        hits = []
        ctx = {
            "velocity_1h": f["velocity_1h"], "amount": f["amount"],
            "device_sharing_max": f["device_sharing_max"],
            "geo_mismatch": f["geo_mismatch"], "identity_conf": f["identity_conf"],
            "merchant_colluding": f["merchant_colluding"],
        }
        checks = {
            "POL-001": ctx["velocity_1h"] >= 5 and ctx["amount"] < 5,
            "POL-002": ctx["device_sharing_max"] >= 5,
            "POL-003": ctx["geo_mismatch"] and ctx["amount"] > 500,
            "POL-004": ctx["identity_conf"] < 0.5 and ctx["amount"] > 250,
            "POL-005": ctx["merchant_colluding"],
        }
        for pol in POLICY_STACK:
            if checks[pol["id"]]:
                hits.append({k: pol[k] for k in ("id", "name", "version", "action", "desc")})
        return hits

    # ------------------------------------------------------------ decision
    def evaluate(self, txn: dict) -> dict:
        t0 = time.perf_counter()
        f = self.features(txn)

        s_tab, c_tab = self.score_tabular(f)
        s_gph, c_gph = self.score_graph(f)
        s_beh, c_beh = self.score_behavioral(f)

        weights = {"tabular": 0.38, "graph": 0.40, "behavioral": 0.22}
        risk = weights["tabular"] * s_tab + weights["graph"] * s_gph + weights["behavioral"] * s_beh
        # agreement-based confidence: models that agree => high confidence
        spread = max(s_tab, s_gph, s_beh) - min(s_tab, s_gph, s_beh)
        confidence = round(min(0.99, max(0.5, 1.0 - spread * 0.9 - (0.08 if not f["known_entity"] else 0.0))), 3)

        policy_hits = self.eval_policies(f)
        action = self._resolve_action(risk, policy_hits)

        reasons = self._reason_codes(f, c_tab + c_gph + c_beh, policy_hits)
        counterfactuals = self._counterfactuals(txn, f, risk)

        latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        decision = {
            "transaction_id": txn.get("id") or txn.get("transaction_id", "txn_adhoc"),
            "decision": action,
            "risk_score": round(risk, 4),
            "confidence": confidence,
            "action_reasons": reasons,
            "model_scores": {
                "tabular": {"score": round(s_tab, 4), "version": self.model_versions["tabular"], "weight": weights["tabular"]},
                "graph": {"score": round(s_gph, 4), "version": self.model_versions["graph"], "weight": weights["graph"]},
                "behavioral": {"score": round(s_beh, 4), "version": self.model_versions["behavioral"], "weight": weights["behavioral"]},
            },
            "attributions": {
                "tabular": [{"feature": k, "weight": round(v, 3)} for k, v in sorted(c_tab, key=lambda x: -abs(x[1]))[:4]],
                "graph": [{"feature": k, "weight": round(v, 3)} for k, v in sorted(c_gph, key=lambda x: -abs(x[1]))[:4]],
                "behavioral": [{"feature": k, "weight": round(v, 3)} for k, v in sorted(c_beh, key=lambda x: -abs(x[1]))[:4]],
            },
            "policy_hits": policy_hits,
            "graph_evidence": {
                "entity_id": txn["consumer_id"],
                "community_id": f.get("community_id"),
                "device_sharing_max": f["device_sharing_max"],
                "linked_high_risk_entities": f["linked_high_risk"],
                "entity_risk": f["entity_risk"],
            },
            "counterfactuals": counterfactuals,
            "explanation": self._narrative(txn, f, risk, action, reasons),
            "review_required": action in ("review", "decline"),
            "latency_ms": latency_ms,
            "engine": self.model_versions["blend"],
        }
        self.decisions.append(decision)
        return decision

    def _resolve_action(self, risk: float, policy_hits: list[dict]) -> str:
        order = {a: i for i, a in enumerate(ACTIONS)}
        if risk >= 0.85:
            base = "decline"
        elif risk >= 0.65:
            base = "review"
        elif risk >= 0.45:
            base = "step_up"
        elif risk >= 0.30:
            base = "monitor"
        else:
            base = "approve"
        for hit in policy_hits:
            if order[hit["action"]] > order[base]:
                base = hit["action"]
        return base

    def _reason_codes(self, f, contribs, policy_hits) -> list[str]:
        reasons = []
        if f["device_sharing_max"] >= 3:
            reasons.append("device_shared_by_multiple_accounts")
        if f["linked_high_risk"] > 0:
            reasons.append("linked_to_high_risk_entities")
        if f["velocity_1h"] >= 4:
            reasons.append("velocity_spike_last_1h")
        if f["identity_conf"] < 0.5:
            reasons.append("identity_graph_inconsistency")
        if f["geo_mismatch"]:
            reasons.append("geo_anomaly")
        if f["merchant_colluding"]:
            reasons.append("merchant_collusion_watchlist")
        if f["amount"] < 5 and f["velocity_1h"] >= 3:
            reasons.append("card_testing_pattern")
        if f["community_risk"] > 0.6:
            reasons.append("high_risk_community_membership")
        for p in policy_hits:
            reasons.append(f"policy:{p['name']}")
        return reasons or ["no_adverse_signals"]

    def _counterfactuals(self, txn, f, risk) -> list[dict]:
        out = []
        if f["device_sharing_max"] >= 3:
            f2 = dict(f, device_sharing_max=1, linked_high_risk=0, community_risk=f["community_risk"] * 0.4)
            s, _ = self.score_graph(f2)
            r2 = 0.38 * self.score_tabular(f)[0] + 0.40 * s + 0.22 * self.score_behavioral(f)[0]
            out.append({"intervention": "device_not_shared",
                        "narrative": f"If this device were not shared across accounts, risk would drop from {risk:.2f} to {r2:.2f}.",
                        "risk_delta": round(r2 - risk, 4)})
        if f["geo_mismatch"]:
            f2 = dict(f, geo_mismatch=False)
            s, _ = self.score_tabular(f2)
            r2 = 0.38 * s + 0.40 * self.score_graph(f)[0] + 0.22 * self.score_behavioral(f)[0]
            out.append({"intervention": "home_geo",
                        "narrative": f"If originating from the account's home geo, risk would move from {risk:.2f} to {r2:.2f}.",
                        "risk_delta": round(r2 - risk, 4)})
        return out

    def _narrative(self, txn, f, risk, action, reasons) -> str:
        drivers = ", ".join(r.replace("_", " ") for r in reasons[:3])
        tier = "HIGH" if risk >= 0.65 else ("ELEVATED" if risk >= 0.45 else "LOW")
        return (f"{tier} risk ({risk:.2f}) → {action.upper()}. "
                f"${f['amount']:.2f} via device with {f['device_sharing_max']} linked account(s); "
                f"primary drivers: {drivers}.")
