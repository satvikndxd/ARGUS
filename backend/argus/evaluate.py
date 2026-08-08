"""Offline evaluation harness — scores the full synthetic world against
ground truth and computes the platform report card:

  AUC-ROC, PR-AUC, recall @ 1% FPR, calibration error (ECE), confusion
  matrix at the action threshold, per-archetype detection, ring-detection
  precision/recall, and decision latency percentiles.

Run:  python -m argus.evaluate   (writes docs/metrics/metrics.json)
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .agents import InvestigationOrchestrator
from .engine import DecisionEngine
from .graph import RiskGraph
from .synth import FRAUD_ARCHETYPES, World


def auc_roc(scores: list[tuple[float, bool]]) -> float:
    pos = sorted(s for s, y in scores if y)
    neg = sorted(s for s, y in scores if not y)
    if not pos or not neg:
        return 0.0
    # rank-based (Mann-Whitney U)
    import bisect
    wins = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        hi = bisect.bisect_right(neg, p)
        wins += lo + (hi - lo) * 0.5
    return wins / (len(pos) * len(neg))


def pr_auc(scores: list[tuple[float, bool]]) -> float:
    pts = sorted(scores, key=lambda x: -x[0])
    tp = fp = 0
    total_pos = sum(1 for _, y in pts if y)
    if total_pos == 0:
        return 0.0
    area, prev_recall = 0.0, 0.0
    for s, y in pts:
        if y:
            tp += 1
        else:
            fp += 1
        recall = tp / total_pos
        precision = tp / (tp + fp)
        area += precision * (recall - prev_recall)
        prev_recall = recall
    return area


def recall_at_fpr(scores: list[tuple[float, bool]], target_fpr: float = 0.01) -> float:
    neg = sorted((s for s, y in scores if not y), reverse=True)
    if not neg:
        return 0.0
    k = max(0, int(len(neg) * target_fpr) - 1)
    threshold = neg[k] if k < len(neg) else neg[-1]
    pos = [s for s, y in scores if y]
    return sum(1 for s in pos if s > threshold) / max(1, len(pos))


def ece(scores: list[tuple[float, bool]], bins: int = 10) -> float:
    total = len(scores)
    err = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(s, y) for s, y in scores if lo <= s < hi or (b == bins - 1 and s == 1.0)]
        if not bucket:
            continue
        conf = sum(s for s, _ in bucket) / len(bucket)
        acc = sum(1 for _, y in bucket if y) / len(bucket)
        err += (len(bucket) / total) * abs(conf - acc)
    return err


def evaluate(seed: int = 1337) -> dict:
    world = World(seed=seed)
    graph = RiskGraph(world)
    engine = DecisionEngine(world, graph)
    orch = InvestigationOrchestrator(engine)

    scores: list[tuple[float, bool]] = []
    latencies: list[float] = []
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    per_archetype: dict[str, dict] = {a: {"n": 0, "caught": 0} for a in FRAUD_ARCHETYPES}
    action_mix: dict[str, int] = {}
    high_risk_decisions = []

    for txn in world.txns:
        d = engine.evaluate({
            "id": txn.id, "step": txn.step, "consumer_id": txn.consumer_id,
            "merchant_id": txn.merchant_id, "device_id": txn.device_id,
            "card_id": txn.card_id, "amount": txn.amount, "geo": txn.geo,
        })
        s = d["risk_score"]
        scores.append((s, txn.is_fraud))
        latencies.append(d["latency_ms"])
        action_mix[d["decision"]] = action_mix.get(d["decision"], 0) + 1
        flagged = d["decision"] in ("review", "decline")
        if txn.is_fraud:
            confusion["tp" if flagged else "fn"] += 1
            per_archetype[txn.archetype]["n"] += 1
            if flagged:
                per_archetype[txn.archetype]["caught"] += 1
        else:
            confusion["fp" if flagged else "tn"] += 1
        if s >= 0.8 and txn.is_fraud and len(high_risk_decisions) < 12:
            high_risk_decisions.append(d)

    # ring detection: does the graph layer isolate ring members in
    # high-risk communities?
    ring_members = {m for r in world.rings.values() for m in r.members}
    flagged_entities = {n for n, r in graph.node_risk.items()
                        if graph.nodes[n]["type"] == "consumer" and r >= 0.6}
    ring_tp = len(flagged_entities & ring_members)
    ring_precision = ring_tp / max(1, len(flagged_entities))
    ring_recall = ring_tp / max(1, len(ring_members))

    # run investigations over the sampled high-risk decisions
    verdicts = {}
    for d in high_risk_decisions:
        case = orch.investigate(d)
        verdicts[case["verdict"]] = verdicts.get(case["verdict"], 0) + 1

    lat_sorted = sorted(latencies)

    def pct(p):
        return round(lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * p))], 3)

    tp, fp, tn, fn = confusion["tp"], confusion["fp"], confusion["tn"], confusion["fn"]
    metrics = {
        "version": "0.1.0",
        "seed": seed,
        "dataset": world.stats(),
        "scores": {
            "auc_roc": round(auc_roc(scores), 4),
            "pr_auc": round(pr_auc(scores), 4),
            "recall_at_1pct_fpr": round(recall_at_fpr(scores, 0.01), 4),
            "recall_at_5pct_fpr": round(recall_at_fpr(scores, 0.05), 4),
            "calibration_ece": round(ece(scores), 4),
            "precision_at_action": round(tp / max(1, tp + fp), 4),
            "recall_at_action": round(tp / max(1, tp + fn), 4),
            "f1_at_action": round(2 * tp / max(1, 2 * tp + fp + fn), 4),
            "false_positive_rate": round(fp / max(1, fp + tn), 4),
        },
        "confusion_matrix": confusion,
        "action_mix": action_mix,
        "per_archetype_detection": {
            a: {"attempts": v["n"], "caught": v["caught"],
                "rate": round(v["caught"] / v["n"], 3) if v["n"] else None}
            for a, v in per_archetype.items()
        },
        "ring_detection": {
            "ring_members": len(ring_members),
            "entities_flagged": len(flagged_entities),
            "precision": round(ring_precision, 4),
            "recall": round(ring_recall, 4),
            "f1": round(2 * ring_precision * ring_recall / max(1e-9, ring_precision + ring_recall), 4),
        },
        "latency_ms": {
            "p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99),
            "mean": round(statistics.mean(latencies), 3),
        },
        "investigations": {
            "cases_run": len(high_risk_decisions),
            "verdicts": verdicts,
            "avg_agents_per_case": round(statistics.mean(
                [c["agents_dispatched"] for c in orch.cases]) if orch.cases else 0, 1),
        },
        "risk_histogram": _histogram(scores),
    }
    return metrics


def _histogram(scores, bins: int = 20):
    legit = [0] * bins
    fraud = [0] * bins
    for s, y in scores:
        b = min(bins - 1, int(s * bins))
        (fraud if y else legit)[b] += 1
    return {"bins": bins, "legit": legit, "fraud": fraud}


def main():
    metrics = evaluate()
    out = Path(__file__).resolve().parents[2] / "docs" / "metrics" / "metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics["scores"], indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
