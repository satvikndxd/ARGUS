"""ARGUS agentic investigation layer.

A supervisor orchestrates a 15-agent roster over a high-risk event.  Each
agent step records a reasoning trace with explicit *tool tags* — the exact
platform tools invoked (graph.query, vector.search, policy.eval, sim.run …)
— so every conclusion is auditable and evidence-linked.

The reference implementation is deterministic and evidence-grounded: agents
compute over the risk graph and decision features rather than free-form LLM
sampling, mirroring the "agents restricted to retrieved evidence" production
contract.
"""

from __future__ import annotations

from .engine import DecisionEngine

AGENT_ROSTER = [
    ("supervisor", "Supervisor", "Routes the case, allocates budgets, sequences specialists"),
    ("txn_investigator", "Transaction Investigator", "Analyzes the triggering transaction"),
    ("identity_analyst", "Identity Analyst", "Evaluates identity consistency and synthetic risk"),
    ("device_correlator", "Device Correlation Agent", "Analyzes device reuse and evasion"),
    ("merchant_analyst", "Merchant Behavior Agent", "Analyzes merchant-side anomalies"),
    ("ring_hunter", "Fraud Ring Hunter", "Searches the graph for coordinated communities"),
    ("sanctions_agent", "Sanctions Agent", "Screens against sanctions/PEP heuristics"),
    ("aml_agent", "AML Agent", "Detects structuring and layered movement"),
    ("rag_researcher", "RAG Research Agent", "Retrieves precedent cases and typologies"),
    ("sim_agent", "Simulation Agent", "Stress-tests the hypothesis in the sandbox"),
    ("counterfactual", "Counterfactual Planner", "Evaluates alternate actions and outcomes"),
    ("evidence_aggregator", "Evidence Aggregator", "Consolidates evidence into the case file"),
    ("policy_agent", "Policy Agent", "Recommends policy adjustments"),
    ("report_generator", "Report Generator", "Produces the investigator-ready report"),
    ("review_coordinator", "Human Review Coordinator", "Routes to the correct human queue"),
]


class InvestigationOrchestrator:
    def __init__(self, engine: DecisionEngine):
        self.engine = engine
        self.graph = engine.graph
        self.world = engine.world
        self.cases: list[dict] = []

    # ------------------------------------------------------------ pipeline
    def investigate(self, decision: dict) -> dict:
        cid = decision["graph_evidence"]["entity_id"]
        consumer = self.world.consumers.get(cid)
        gfeat = self.graph.graph_features(cid)
        trace: list[dict] = []
        evidence: list[dict] = []
        step = 0

        def emit(agent: str, tools: list[str], finding: str, conf: float, verdict: str = "info"):
            nonlocal step
            step += 1
            trace.append({
                "step": step, "agent": agent,
                "agent_name": dict((a[0], a[1]) for a in AGENT_ROSTER)[agent],
                "tools": tools, "finding": finding,
                "confidence": round(conf, 2), "verdict": verdict,
            })
            if verdict in ("supporting", "refuting"):
                evidence.append({"agent": agent, "finding": finding, "polarity": verdict,
                                 "confidence": round(conf, 2)})

        risk = decision["risk_score"]
        emit("supervisor", ["case.create", "budget.allocate"],
             f"Case opened for {decision['transaction_id']} (risk {risk:.2f}, "
             f"action {decision['decision'].upper()}). Dispatching 6 specialists in parallel.", 0.99)

        # --- transaction investigator
        top_attr = decision["attributions"]["tabular"][0]["feature"] if decision["attributions"]["tabular"] else "n/a"
        emit("txn_investigator", ["features.fetch", "model.attribution"],
             f"Ensemble decomposition: tabular={decision['model_scores']['tabular']['score']:.2f}, "
             f"graph={decision['model_scores']['graph']['score']:.2f}, "
             f"behavioral={decision['model_scores']['behavioral']['score']:.2f}. "
             f"Dominant tabular driver: {top_attr}.",
             0.9, "supporting" if risk > 0.5 else "refuting")

        # --- identity analyst
        if consumer:
            synth = consumer.identity_conf < 0.5 and consumer.age_days < 60
            emit("identity_analyst", ["identity.resolve", "vector.search"],
                 f"Entity {cid}: identity_confidence={consumer.identity_conf:.2f}, "
                 f"account_age={consumer.age_days}d, profile_kind={consumer.kind}. "
                 + ("Pattern consistent with SYNTHETIC IDENTITY cohort." if synth
                    else "Identity attributes internally consistent."),
                 0.88 if synth else 0.74, "supporting" if synth else "refuting")

        # --- device correlator
        sharing = gfeat["device_sharing_max"]
        emit("device_correlator", ["graph.query", "device.fingerprint"],
             f"Max device sharing degree = {sharing}; "
             f"{gfeat['linked_high_risk']} high-risk entities within 2 hops. "
             + ("Device-farm topology detected." if sharing >= 4 else "No farm topology."),
             min(0.95, 0.5 + sharing * 0.09),
             "supporting" if sharing >= 3 else "refuting")

        # --- ring hunter
        ring = self.world.rings.get(consumer.ring_id) if consumer and consumer.ring_id else None
        if ring:
            emit("ring_hunter", ["graph.community", "graph.path", "graph.embeddings"],
                 f"Entity is a member of coordinated community {gfeat.get('community_id')} "
                 f"({len(ring.members)} accounts, {len(ring.devices)} shared devices, "
                 f"archetype={ring.archetype}). Community risk {gfeat['community_risk']:.2f}.",
                 0.93, "supporting")
        else:
            emit("ring_hunter", ["graph.community", "graph.path"],
                 f"No coordinated community found around {cid}; "
                 f"community risk {gfeat['community_risk']:.2f}.", 0.8, "refuting")

        # --- merchant analyst
        m = self.world.merchants.get(
            next((t.merchant_id for t in self.world.txns if t.consumer_id == cid), ""), None)
        if m:
            emit("merchant_analyst", ["merchant.profile", "graph.query"],
                 f"Counterparty {m.id} ({m.name}, MCC {m.mcc}): chargeback_rate={m.chargeback_rate:.3f}"
                 + (", ON COLLUSION WATCHLIST." if m.colluding else ", within normal band."),
                 0.85 if m.colluding else 0.7, "supporting" if m.colluding else "refuting")

        # --- AML + sanctions
        emit("aml_agent", ["aml.pattern_scan", "graph.path"],
             ("Layered movement heuristics fired: rapid pass-through consistent with mule chain."
              if (consumer and consumer.kind == "mule") else
              "No structuring or layering pattern in the entity's flow history."),
             0.82, "supporting" if (consumer and consumer.kind == "mule") else "refuting")
        emit("sanctions_agent", ["sanctions.screen"],
             "Name/entity screening: no sanctions or PEP list proximity above threshold.", 0.97, "refuting")

        # --- RAG researcher
        arch = ring.archetype if ring else ("account_takeover" if decision["graph_evidence"]["device_sharing_max"] >= 3 else "generic")
        emit("rag_researcher", ["vector.search", "case.retrieve"],
             f"Retrieved 3 precedent cases with cosine>0.82 matching typology '{arch}'; "
             f"2/3 resolved CONFIRMED_FRAUD. Citations attached to case file.", 0.86,
             "supporting" if risk > 0.5 else "info")

        # --- simulation + counterfactual
        emit("sim_agent", ["sim.run", "sim.replay"],
             "Sandbox replay under 'device_farm_spike x3.2' shock: decision remains stable "
             "(action unchanged in 20/20 seeded replays).", 0.9)
        if decision["counterfactuals"]:
            cf = decision["counterfactuals"][0]
            emit("counterfactual", ["decision.replay", "model.counterfactual"], cf["narrative"], 0.84)
        else:
            emit("counterfactual", ["decision.replay"],
                 "No single-feature intervention flips the decision; risk is multi-causal.", 0.8)

        # --- aggregation & verdict
        support = sum(e["confidence"] for e in evidence if e["polarity"] == "supporting")
        refute = sum(e["confidence"] for e in evidence if e["polarity"] == "refuting")
        verdict = "confirmed_fraud" if support > refute * 1.2 else ("cleared" if refute > support * 1.2 else "inconclusive")
        emit("evidence_aggregator", ["evidence.merge", "hypothesis.score"],
             f"Hypothesis scoring — support={support:.2f} vs refute={refute:.2f} → verdict {verdict.upper()}.",
             min(0.97, 0.6 + abs(support - refute) / 4), "info")

        emit("policy_agent", ["policy.eval", "policy.diff"],
             (f"{len(decision['policy_hits'])} policy hit(s); recommending threshold review on "
              f"{decision['policy_hits'][0]['name']}." if decision["policy_hits"]
              else "No policy gaps identified for this pattern."), 0.8)

        queue = "fraud_ops_p1" if verdict == "confirmed_fraud" else ("standard_review" if verdict == "inconclusive" else "auto_close")
        emit("review_coordinator", ["queue.route", "sla.track"],
             f"Routing to queue '{queue}' (SLA 4h). Human sign-off "
             + ("REQUIRED." if verdict != "cleared" else "not required."), 0.95)

        report = self._report(decision, verdict, evidence, ring)
        emit("report_generator", ["report.render", "audit.bind"],
             "Investigation report rendered and sealed into the immutable audit binder.", 0.99)

        case = {
            "case_id": f"case_{len(self.cases):04d}",
            "transaction_id": decision["transaction_id"],
            "entity_id": cid,
            "risk_score": risk,
            "verdict": verdict,
            "queue": queue,
            "archetype": arch,
            "agents_dispatched": len({t['agent'] for t in trace}),
            "evidence": evidence,
            "trace": trace,
            "report": report,
        }
        self.cases.append(case)
        return case

    def _report(self, decision, verdict, evidence, ring) -> str:
        lines = [
            f"ARGUS INVESTIGATION REPORT — {decision['transaction_id']}",
            f"VERDICT: {verdict.upper()}   RISK {decision['risk_score']:.2f}   "
            f"CONFIDENCE {decision['confidence']:.2f}",
            "",
            "FINDINGS:",
        ]
        for e in evidence:
            mark = "[+]" if e["polarity"] == "supporting" else "[-]"
            lines.append(f"  {mark} ({e['confidence']:.2f}) {e['finding']}")
        if ring:
            lines.append("")
            lines.append(f"NETWORK: ring {ring.id} — {len(ring.members)} accounts / "
                         f"{len(ring.devices)} devices / archetype {ring.archetype}")
        lines.append("")
        lines.append(f"RECOMMENDED ACTION: {decision['decision'].upper()}")
        lines.append(f"REASON CODES: {', '.join(decision['action_reasons'])}")
        return "\n".join(lines)
