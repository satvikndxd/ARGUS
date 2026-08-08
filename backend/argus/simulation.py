"""ARGUS simulation engine — synthetic attack campaigns with shock injection.

Runs a scenario over a freshly seeded world variant, streams every transaction
through the decision engine, and reports per-day detection performance,
prevented-loss economics and shock response — deterministically replayable
from (scenario, seed).
"""

from __future__ import annotations

from .engine import DecisionEngine
from .graph import RiskGraph
from .synth import World

SCENARIOS = {
    "synthetic_identity_wave": {
        "name": "synthetic_identity_wave",
        "description": "Coordinated synthetic-identity cash-out ramp with a device-farm spike shock",
        "duration_days": 14, "shock_day": 5, "shock_type": "device_farm_spike", "multiplier": 3.2,
    },
    "card_testing_storm": {
        "name": "card_testing_storm",
        "description": "Low-value enumeration burst across stolen card ranges",
        "duration_days": 7, "shock_day": 3, "shock_type": "enumeration_burst", "multiplier": 4.0,
    },
    "mule_cashout_campaign": {
        "name": "mule_cashout_campaign",
        "description": "Layered mule-network cash-out through colluding merchants",
        "duration_days": 10, "shock_day": 6, "shock_type": "mule_activation", "multiplier": 2.5,
    },
}


def run_scenario(scenario_id: str, seed: int = 1337) -> dict:
    spec = SCENARIOS.get(scenario_id)
    if spec is None:
        raise KeyError(scenario_id)

    world = World(seed=seed + hash(scenario_id) % 1000, n_txns=3000)
    graph = RiskGraph(world)
    engine = DecisionEngine(world, graph)

    days = spec["duration_days"]
    steps_per_day = max(1, (world.txns[-1].step if world.txns else 1) // days)
    timeline = [{"day": d + 1, "txns": 0, "fraud": 0, "caught": 0, "missed": 0,
                 "false_pos": 0, "loss_prevented": 0.0, "loss_incurred": 0.0,
                 "shock": (d + 1 == spec["shock_day"])} for d in range(days)]

    for txn in world.txns:
        day = min(days - 1, txn.step // steps_per_day)
        amount = txn.amount
        is_fraud = txn.is_fraud
        # shock: amplify attack volume on shock day
        if timeline[day]["shock"] and is_fraud:
            amount *= spec["multiplier"] ** 0.5
        d = engine.evaluate({
            "id": txn.id, "step": txn.step, "consumer_id": txn.consumer_id,
            "merchant_id": txn.merchant_id, "device_id": txn.device_id,
            "card_id": txn.card_id, "amount": amount, "geo": txn.geo,
        })
        blocked = d["decision"] in ("review", "decline", "step_up")
        row = timeline[day]
        row["txns"] += 1
        if is_fraud:
            row["fraud"] += 1
            if blocked:
                row["caught"] += 1
                row["loss_prevented"] += amount
            else:
                row["missed"] += 1
                row["loss_incurred"] += amount
        elif blocked and d["decision"] in ("review", "decline"):
            row["false_pos"] += 1

    for row in timeline:
        row["loss_prevented"] = round(row["loss_prevented"], 2)
        row["loss_incurred"] = round(row["loss_incurred"], 2)
        row["detection_rate"] = round(row["caught"] / row["fraud"], 3) if row["fraud"] else None
        row["fpr"] = round(row["false_pos"] / max(1, row["txns"] - row["fraud"]), 4)

    total_fraud = sum(r["fraud"] for r in timeline)
    total_caught = sum(r["caught"] for r in timeline)
    return {
        "scenario": spec,
        "seed": seed,
        "timeline": timeline,
        "summary": {
            "transactions": sum(r["txns"] for r in timeline),
            "fraud_attempts": total_fraud,
            "fraud_caught": total_caught,
            "detection_rate": round(total_caught / max(1, total_fraud), 4),
            "loss_prevented_usd": round(sum(r["loss_prevented"] for r in timeline), 2),
            "loss_incurred_usd": round(sum(r["loss_incurred"] for r in timeline), 2),
            "avg_daily_fpr": round(sum(r["fpr"] for r in timeline) / days, 4),
        },
    }
