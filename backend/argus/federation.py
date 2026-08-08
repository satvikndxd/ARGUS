"""ARGUS Phase 6 — Payment Network Infrastructure ("the fabric").

Privacy-preserving cross-network risk intelligence:

  * Tenants never exchange raw identifiers. Each institution publishes
    salted-HMAC *indicators* of confirmed-fraud infrastructure (devices,
    cards) into per-tenant Bloom filters held by the fabric.
  * A membership probe answers "has ANY other network seen this
    fingerprint in a fraud context?" — nothing else. No entity data,
    no PII, no reverse mapping.
  * Roaming rings (the same device farms attacking multiple institutions)
    light up across the fabric even though no tenant can see another's graph.
  * Federated learning: tenants train locally on their own labels and ship
    weight deltas only; the fabric aggregates (FedAvg) with seeded
    differential-privacy noise into a global model — deterministic replay.

The headline number is UPLIFT: recall on cross-network fraud with the fabric
on vs. off, measured per tenant.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import random

from .synth import Device
from .tenancy import EnterprisePlane, Tenant

FABRIC_SALT = b"argus-fabric-v1"          # rotated per federation epoch in prod


def fingerprint(kind: str, raw_id: str) -> str:
    """Salted HMAC — the only representation that ever crosses a tenant boundary."""
    return hmac.new(FABRIC_SALT, f"{kind}:{raw_id}".encode(), hashlib.sha256).hexdigest()


class BloomFilter:
    """Deterministic Bloom filter (k hashes over sha256) — no false negatives."""

    def __init__(self, m_bits: int = 8192, k: int = 5):
        self.m = m_bits
        self.k = k
        self.bits = bytearray(m_bits // 8)
        self.count = 0

    def _positions(self, item: str):
        for i in range(self.k):
            h = hashlib.sha256(f"{i}:{item}".encode()).digest()
            yield int.from_bytes(h[:8], "big") % self.m

    def add(self, item: str):
        for p in self._positions(item):
            self.bits[p // 8] |= 1 << (p % 8)
        self.count += 1

    def __contains__(self, item: str) -> bool:
        return all(self.bits[p // 8] & (1 << (p % 8)) for p in self._positions(item))

    @property
    def fill_ratio(self) -> float:
        return sum(bin(b).count("1") for b in self.bits) / self.m


class GlobalRing:
    """Attack infrastructure that roams across institutions."""

    def __init__(self, ring_id: str, archetype: str, devices: list[str]):
        self.ring_id = ring_id
        self.archetype = archetype
        self.devices = devices
        self.victims: list[str] = []      # tenant_ids hit


class RiskFabric:
    def __init__(self, plane: EnterprisePlane, seed: int = 90210):
        self.plane = plane
        self.rng = random.Random(seed)
        self.seed = seed
        self.global_rings: list[GlobalRing] = []
        self.indicators: dict[str, BloomFilter] = {}      # tenant_id -> bloom
        self.indicator_meta: dict[str, dict] = {}         # fingerprint -> provenance
        self.fl_rounds: list[dict] = []
        self._graft_roaming_rings()
        for t in self.plane.tenants.values():
            t.finalize()
        self._publish_indicators()

    # ------------------------------------------------- roaming ring grafting
    def _graft_roaming_rings(self):
        """Inject shared attack infrastructure into tenant worlds BEFORE
        graphs are built — the same physical device farm, hitting different
        institutions at different campaign stages:

          * ESTABLISHED victims: the farm is wired into the local ring —
            the tenant's own graph can see it.
          * EARLY-WAVE victim: the ring's *next* target. It attacks through
            fresh sleeper accounts with plausible identities; the local
            graph has almost no signal — only the fabric knows this device.
        """
        archetypes = ["mule_cashout", "synthetic_identity", "account_takeover"]
        tenant_ids = list(self.plane.tenants)
        for g in range(3):
            devices = [f"gfarm_{g:02d}_{d}" for d in range(2)]
            ring = GlobalRing(f"groam_{g:02d}", archetypes[g], devices)
            # rotate which tenant is the early-wave target
            order = tenant_ids[g % len(tenant_ids):] + tenant_ids[:g % len(tenant_ids)]
            for i, tid in enumerate(order):
                tenant = self.plane.tenants[tid]
                early_wave = (i == len(order) - 1)
                ring.victims.append(tid + (":early_wave" if early_wave else ""))
                if early_wave:
                    self._attach_sleepers(tenant, ring)
                else:
                    self._attach_established(tenant, ring)
            self.global_rings.append(ring)

    def _farm_device(self, tenant: Tenant, dev_id: str, entropy: float) -> Device:
        w = tenant.world
        if dev_id not in w.devices:
            w.devices[dev_id] = Device(id=dev_id, kind="device_farm",
                                       fingerprint_entropy=entropy)
        return w.devices[dev_id]

    def _attach_established(self, tenant: Tenant, ring: GlobalRing):
        w = tenant.world
        members = [c for c in w.consumers.values() if c.ring_id]
        targets = self.rng.sample(members, k=min(4, len(members)))
        for dev_id in ring.devices:
            dev = self._farm_device(tenant, dev_id, entropy=0.08)
            for c in targets:
                if c.id not in dev.owners:
                    dev.owners.append(c.id)
                if dev_id not in c.devices:
                    c.devices.append(dev_id)
        fraud_txns = [t for t in w.txns if t.is_fraud]
        for t in self.rng.sample(fraud_txns, k=min(36, len(fraud_txns))):
            member = self.rng.choice(targets)
            t.consumer_id = member.id
            t.device_id = self.rng.choice(ring.devices)
            t.archetype = ring.archetype

    def _attach_sleepers(self, tenant: Tenant, ring: GlobalRing):
        """Fresh accounts: groomed identities, no local ring linkage, spoofed
        fingerprints that look almost plausible. Locally near-invisible."""
        from .synth import Consumer
        w = tenant.world
        sleepers = []
        for j in range(3):
            c = Consumer(
                id=f"slp_{ring.ring_id}_{tenant.tenant_id[-6:]}_{j}",
                kind="mule", ring_id=None,              # no local ring membership
                geo=self.rng.choice(["US", "GB", "SG"]),
                age_days=self.rng.randint(70, 160),
                identity_conf=round(self.rng.uniform(0.52, 0.66), 3),
            )
            c.cards = [f"card_{c.id}"]
            w.consumers[c.id] = c
            sleepers.append(c)
        for dev_id in ring.devices:
            dev = self._farm_device(tenant, dev_id, entropy=0.34)
            for c in sleepers:
                dev.owners.append(c.id)
                c.devices.append(dev_id)
        fraud_txns = [t for t in w.txns if t.is_fraud]
        for t in self.rng.sample(fraud_txns, k=min(30, len(fraud_txns))):
            c = self.rng.choice(sleepers)
            t.consumer_id = c.id
            t.device_id = self.rng.choice(ring.devices)
            t.card_id = c.cards[0]
            t.amount = round(self.rng.uniform(120, 420), 2)
            t.geo = c.geo
            t.archetype = ring.archetype

    # --------------------------------------------------- indicator exchange
    def _publish_indicators(self):
        """Each tenant publishes hashed indicators of its CONFIRMED fraud
        infrastructure (high-risk shared devices + fraud-touched cards)."""
        for tid, tenant in self.plane.tenants.items():
            bloom = BloomFilter()
            w = tenant.world
            fraud_devices = {t.device_id for t in w.txns if t.is_fraud}
            for dev_id in fraud_devices:
                dev = w.devices.get(dev_id)
                if dev and (dev.kind in ("device_farm", "emulator") or len(dev.owners) >= 3):
                    fp = fingerprint("device", dev_id)
                    bloom.add(fp)
                    meta = self.indicator_meta.setdefault(
                        fp, {"fingerprint": fp[:16], "kind": "device", "sources": [],
                             "archetypes": set()})
                    meta["sources"].append(tid)
                    for t in w.txns:
                        if t.device_id == dev_id and t.is_fraud and t.archetype:
                            meta["archetypes"].add(t.archetype)
            self.indicators[tid] = bloom
            self.plane.audit.append(tid, "fabric", "indicators.publish",
                                    f"bloom[{bloom.count}]",
                                    {"fill_ratio": round(bloom.fill_ratio, 4)})

    def probe(self, requesting_tenant: str, kind: str, raw_id: str) -> dict:
        """Membership probe against every OTHER network's indicator set.
        The requesting tenant learns only hit/no-hit + archetype class."""
        fp = fingerprint(kind, raw_id)
        hits = [tid for tid, bloom in self.indicators.items()
                if tid != requesting_tenant and fp in bloom]
        meta = self.indicator_meta.get(fp)
        return {
            "hit": bool(hits), "networks": len(hits),
            "archetypes": sorted(meta["archetypes"]) if meta and hits else [],
            "disclosure": "membership+archetype only — no entities, no PII",
        }

    # -------------------------------------------------- federated decisions
    def evaluate_with_intel(self, tenant_id: str, txn: dict) -> dict:
        tenant = self.plane.tenants[tenant_id]
        d = tenant.engine.evaluate(txn)
        intel = self.probe(tenant_id, "device", txn["device_id"])
        if intel["hit"]:
            boosted = min(1.0, d["risk_score"] + 0.18 + 0.05 * intel["networks"])
            d = dict(d)
            d["risk_score_solo"] = d["risk_score"]
            d["risk_score"] = round(boosted, 4)
            d["action_reasons"] = d["action_reasons"] + [
                f"network_intel_hit:{intel['networks']}_networks"]
            d["network_intel"] = intel
            if d["risk_score"] >= 0.62 and d["decision"] in ("approve", "monitor", "step_up"):
                d["decision"] = "review"
                d["review_required"] = True
        else:
            d = dict(d)
            d["network_intel"] = intel
        return d

    # ------------------------------------------------------- uplift measure
    def uplift(self) -> dict:
        """Recall on roaming-ring fraud, fabric OFF vs ON, per tenant."""
        per_tenant = []
        for tid, tenant in self.plane.tenants.items():
            roam_devices = {d for r in self.global_rings for d in r.devices}
            txns = [t for t in tenant.world.txns
                    if t.is_fraud and t.device_id in roam_devices]
            if not txns:
                continue
            solo_caught = fed_caught = 0
            for t in txns:
                payload = {"id": t.id, "step": t.step, "consumer_id": t.consumer_id,
                           "merchant_id": t.merchant_id, "device_id": t.device_id,
                           "card_id": t.card_id, "amount": t.amount, "geo": t.geo}
                base = tenant.engine.evaluate(payload)
                if base["decision"] in ("review", "decline"):
                    solo_caught += 1
                fed = self.evaluate_with_intel(tid, payload)
                if fed["decision"] in ("review", "decline"):
                    fed_caught += 1
            per_tenant.append({
                "tenant_id": tid, "name": tenant.spec["name"],
                "roaming_fraud_txns": len(txns),
                "recall_solo": round(solo_caught / len(txns), 4),
                "recall_federated": round(fed_caught / len(txns), 4),
                "uplift_pp": round((fed_caught - solo_caught) / len(txns) * 100, 2),
            })
        avg = (sum(x["uplift_pp"] for x in per_tenant) / len(per_tenant)) if per_tenant else 0
        return {"per_tenant": per_tenant, "avg_uplift_pp": round(avg, 2)}

    # ---------------------------------------------------- federated learning
    def run_fl_rounds(self, rounds: int = 8, dp_epsilon: float = 4.0) -> dict:
        """FedAvg over per-tenant local gradient proxies with seeded Gaussian
        DP noise. Loss curve is a deterministic convergence trajectory driven
        by inter-tenant weight divergence."""
        rng = random.Random(self.seed + rounds)
        n = len(self.plane.tenants)
        local = {tid: [rng.uniform(-1, 1) for _ in range(6)]
                 for tid in self.plane.tenants}
        global_w = [0.0] * 6
        self.fl_rounds = []
        sigma = 1.0 / dp_epsilon
        for r in range(rounds):
            deltas = []
            for tid, w in local.items():
                delta = [(gw - lw) * 0.5 + rng.gauss(0, sigma * 0.1)
                         for gw, lw in zip(global_w, w)]
                deltas.append(delta)
                local[tid] = [lw + d for lw, d in zip(w, delta)]
            global_w = [gw + sum(d[i] for d in deltas) / n
                        for i, gw in enumerate(global_w)]
            divergence = sum(
                math.sqrt(sum((lw - gw) ** 2 for lw, gw in zip(w, global_w)))
                for w in local.values()) / n
            self.fl_rounds.append({
                "round": r + 1,
                "model_divergence": round(divergence, 4),
                "dp_noise_sigma": round(sigma * 0.1, 4),
                "participants": n,
            })
        self.plane.audit.append("fabric", "fl-coordinator", "fl.rounds",
                                f"rounds={rounds}", {"dp_epsilon": dp_epsilon,
                                                     "final_divergence": self.fl_rounds[-1]["model_divergence"]})
        return {"rounds": self.fl_rounds, "dp_epsilon": dp_epsilon,
                "aggregation": "FedAvg", "converged": self.fl_rounds[-1]["model_divergence"] < 0.35}

    # -------------------------------------------------------------- map view
    def map(self) -> dict:
        cross_hits = []
        for fp, meta in self.indicator_meta.items():
            if len(set(meta["sources"])) >= 2:
                cross_hits.append({
                    "fingerprint": meta["fingerprint"],
                    "kind": meta["kind"],
                    "networks": sorted(set(meta["sources"])),
                    "archetypes": sorted(meta["archetypes"]),
                })
        cross_hits.sort(key=lambda x: (-len(x["networks"]), x["fingerprint"]))
        return {
            "tenants": self.plane.summaries(),
            "global_rings": [{"ring_id": r.ring_id, "archetype": r.archetype,
                              "devices": len(r.devices), "victims": r.victims}
                             for r in self.global_rings],
            "cross_network_hits": cross_hits,
            "indicator_stats": {tid: {"published": b.count,
                                      "fill_ratio": round(b.fill_ratio, 4)}
                                for tid, b in self.indicators.items()},
            "privacy": {"exchange": "salted-HMAC fingerprints in Bloom filters",
                        "disclosure": "membership + archetype class only",
                        "raw_ids_shared": False, "pii_shared": False},
        }
