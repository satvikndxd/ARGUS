# ARGUS — Architecture Notes

This document maps the reference implementation onto the full platform
specification (see README for diagrams and the report card).

## Planes

| Plane | Module | Responsibility |
|---|---|---|
| Ingestion / Decision (hot path) | `backend/api/main.py`, `backend/argus/engine.py` | sync scoring, policy evaluation, explanation bundles |
| Graph (risk memory) | `backend/argus/graph.py` | entities, relationships, communities, risk propagation, subgraph extraction |
| Agentic (investigation) | `backend/argus/agents.py` | supervisor + 14 specialists, tool-tagged traces, sealed reports |
| Simulation (stress testing) | `backend/argus/simulation.py`, `simulation/scenarios/` | attack campaigns, shocks, deterministic replay |
| Evaluation (report card) | `backend/argus/evaluate.py` | AUC/PR-AUC/recall@FPR/ECE, ring detection, latency percentiles |
| Console (operations) | `frontend/` | five-view Matrix console over the API |

## Architectural principles (from the spec, enforced here)

1. **Hot path is separated from deep investigation.** `evaluate()` only touches
   precomputed graph feature summaries and sliding-window velocity counters —
   no unbounded traversals. Investigations run as a separate orchestration.
2. **The graph is the system of record for risk context.** Transactions are
   events; entities/relationships are durable memory (`RiskGraph`).
3. **Agents are investigators, not gatekeepers.** No agent blocks the hot
   path; they enrich, debate (support/refute), and escalate to human queues.
4. **Every decision is explainable.** Reason codes, per-model attributions,
   policy hits, graph evidence, counterfactuals, and a natural-language
   narrative ship in the same response as the score.
5. **Determinism is first-class.** Seeded world, process-stable CRC32 for
   stochastic outcomes, replay asserted by tests.

## Hot-path latency budget (measured, in-process)

| Step | Budget (spec, networked) | Measured (reference, in-process) |
|---|---:|---:|
| Feature retrieval | 8 ms | ~0.01 ms |
| Graph lookup | 20 ms | ~0.02 ms |
| Model inference | 20 ms | ~0.01 ms |
| Policy evaluation | 5 ms | ~0.005 ms |
| **Total p99** | **< 100 ms** | **0.098 ms** |

The spec budget applies to the networked production topology (Redis, Neo4j
replicas, model servers); the reference implementation demonstrates the
algorithmic path is nowhere near the budget.

## Failure modes carried over from the spec

| Failure | Mitigation in reference implementation |
|---|---|
| Graph latency spike | depth/size-limited `neighborhood()` extraction |
| Label leakage | risk propagation reads only observable signals (tested) |
| Agent hallucination | agents compute over retrieved evidence only; every claim tool-tagged |
| Non-reproducible replay | no wall-clock/`hash()` in any scoring path |
