"""ARGUS risk graph — in-memory property graph over the payment world.

Production deployments back this interface with Neo4j; this reference
implementation keeps the identical query surface (neighborhood expansion,
path finding, community detection, risk propagation) in-process so the whole
platform runs anywhere with zero external services.
"""

from __future__ import annotations

from collections import defaultdict, deque

from .synth import World


class RiskGraph:
    def __init__(self, world: World):
        self.world = world
        self.nodes: dict[str, dict] = {}
        self.adj: dict[str, set[str]] = defaultdict(set)
        self.edges: list[dict] = []
        self._build()
        self.communities = self._detect_communities()
        self.node_risk = self._propagate_risk()

    # ------------------------------------------------------------------ build
    def _add_node(self, nid: str, ntype: str, **props):
        self.nodes[nid] = {"id": nid, "type": ntype, **props}

    def _add_edge(self, a: str, b: str, etype: str):
        if b in self.adj[a]:
            return
        self.adj[a].add(b)
        self.adj[b].add(a)
        self.edges.append({"source": a, "target": b, "type": etype})

    def _build(self):
        w = self.world
        for c in w.consumers.values():
            self._add_node(c.id, "consumer", kind=c.kind, ring_id=c.ring_id,
                           identity_conf=c.identity_conf, age_days=c.age_days, geo=c.geo)
        for m in w.merchants.values():
            self._add_node(m.id, "merchant", name=m.name, mcc=m.mcc,
                           colluding=m.colluding, ring_id=m.ring_id,
                           chargeback_rate=m.chargeback_rate)
        for d in w.devices.values():
            self._add_node(d.id, "device", kind=d.kind,
                           fingerprint_entropy=d.fingerprint_entropy,
                           owner_count=len(d.owners))
        for c in w.consumers.values():
            for dev in c.devices:
                self._add_edge(c.id, dev, "HAS_DEVICE")
        # consumer->merchant edges from transaction co-occurrence
        pair_counts: dict[tuple[str, str], int] = defaultdict(int)
        for t in w.txns:
            pair_counts[(t.consumer_id, t.merchant_id)] += 1
        for (cid, mid), n in pair_counts.items():
            if n >= 2:
                self._add_edge(cid, mid, "TRANSACTS_WITH")

    # -------------------------------------------------------------- queries
    def neighborhood(self, node_id: str, depth: int = 2, limit: int = 150) -> dict:
        """Depth-limited subgraph extraction (the hot-path primitive)."""
        if node_id not in self.nodes:
            return {"nodes": [], "edges": []}
        seen = {node_id}
        frontier = deque([(node_id, 0)])
        while frontier and len(seen) < limit:
            nid, d = frontier.popleft()
            if d >= depth:
                continue
            for nb in sorted(self.adj[nid]):
                if nb not in seen and len(seen) < limit:
                    seen.add(nb)
                    frontier.append((nb, d + 1))
        nodes = [dict(self.nodes[n], risk=round(self.node_risk.get(n, 0.0), 3),
                      community=self.communities.get(n))
                 for n in seen]
        edges = [e for e in self.edges if e["source"] in seen and e["target"] in seen]
        return {"nodes": nodes, "edges": edges, "center": node_id}

    def shortest_path(self, a: str, b: str, max_depth: int = 6) -> list[str]:
        if a not in self.nodes or b not in self.nodes:
            return []
        prev = {a: None}
        q = deque([a])
        while q:
            cur = q.popleft()
            if cur == b:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return list(reversed(path))
            for nb in sorted(self.adj[cur]):
                if nb not in prev:
                    prev[nb] = cur
                    q.append(nb)
        return []

    # ------------------------------------------------- community detection
    def _detect_communities(self) -> dict[str, int]:
        """Deterministic label propagation over connected components."""
        labels: dict[str, int] = {}
        comp = 0
        for start in sorted(self.nodes):
            if start in labels:
                continue
            q = deque([start])
            labels[start] = comp
            while q:
                cur = q.popleft()
                for nb in sorted(self.adj[cur]):
                    if nb not in labels:
                        labels[nb] = comp
                        q.append(nb)
            comp += 1
        return labels

    def community_summary(self) -> list[dict]:
        groups: dict[int, list[str]] = defaultdict(list)
        for nid, c in self.communities.items():
            groups[c].append(nid)
        out = []
        for c, members in groups.items():
            consumers = [m for m in members if self.nodes[m]["type"] == "consumer"]
            ring_members = [m for m in consumers if self.nodes[m].get("ring_id")]
            shared_devices = [m for m in members
                              if self.nodes[m]["type"] == "device" and self.nodes[m]["owner_count"] > 2]
            risk = max((self.node_risk.get(m, 0.0) for m in members), default=0.0)
            out.append({
                "community_id": f"comm_{c:03d}",
                "size": len(members),
                "consumers": len(consumers),
                "ring_overlap": round(len(ring_members) / max(1, len(consumers)), 3),
                "shared_devices": len(shared_devices),
                "max_risk": round(risk, 3),
            })
        return sorted(out, key=lambda x: (-x["max_risk"], -x["size"]))

    # ---------------------------------------------------- risk propagation
    def _propagate_risk(self, iterations: int = 3, damping: float = 0.5) -> dict[str, float]:
        risk: dict[str, float] = {}
        for nid, n in self.nodes.items():
            if n["type"] == "consumer":
                # NO label leakage: risk derives only from observable signals
                base = (1.0 - n["identity_conf"]) * 0.55
                if n["age_days"] < 45:
                    base += 0.12
                risk[nid] = min(1.0, base)
            elif n["type"] == "device":
                base = (1.0 - n["fingerprint_entropy"]) * 0.4
                if n["owner_count"] > 2:
                    base = max(base, min(0.95, 0.3 + 0.12 * n["owner_count"]))
                risk[nid] = base
            else:  # merchant
                risk[nid] = min(0.9, n["chargeback_rate"] * 8) + (0.35 if n["colluding"] else 0.0)
        for _ in range(iterations):
            nxt = {}
            for nid in self.nodes:
                nbs = self.adj[nid]
                if not nbs:
                    nxt[nid] = risk[nid]
                    continue
                nb_avg = sum(risk[n] for n in nbs) / len(nbs)
                nxt[nid] = min(1.0, (1 - damping) * risk[nid] + damping * max(risk[nid], nb_avg * 0.9))
            risk = nxt
        return risk

    # ------------------------------------------------------------ features
    def graph_features(self, consumer_id: str) -> dict:
        """Hot-path graph feature summary for the decision engine."""
        if consumer_id not in self.nodes:
            return {"known_entity": False, "device_sharing_max": 0, "linked_high_risk": 0,
                    "community_risk": 0.0, "entity_risk": 0.0}
        devices = [n for n in self.adj[consumer_id] if self.nodes[n]["type"] == "device"]
        sharing = max((self.nodes[d]["owner_count"] for d in devices), default=1)
        two_hop = set()
        for d in devices:
            two_hop |= {n for n in self.adj[d] if self.nodes[n]["type"] == "consumer" and n != consumer_id}
        linked_high_risk = sum(1 for n in two_hop if self.node_risk.get(n, 0) > 0.6)
        comm = self.communities.get(consumer_id)
        comm_members = [n for n, c in self.communities.items() if c == comm]
        comm_risk = max((self.node_risk.get(n, 0.0) for n in comm_members), default=0.0) if len(comm_members) > 1 else 0.0
        return {
            "known_entity": True,
            "device_sharing_max": sharing,
            "linked_high_risk": linked_high_risk,
            "community_risk": round(comm_risk, 3),
            "entity_risk": round(self.node_risk.get(consumer_id, 0.0), 3),
            "community_id": f"comm_{comm:03d}" if comm is not None else None,
        }
