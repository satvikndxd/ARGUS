"""Deterministic synthetic payment ecosystem generator.

Builds a seeded world of consumers, merchants, devices, cards, fraud rings,
mule networks, synthetic-identity clusters and a transaction stream with
ground-truth fraud labels — the substrate for the risk graph, the decision
engine, the simulation engine, and offline evaluation.

Everything is derived from a single RNG seed => fully reproducible replay.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

FRAUD_ARCHETYPES = [
    "card_testing",
    "account_takeover",
    "synthetic_identity",
    "refund_fraud",
    "merchant_collusion",
    "mule_cashout",
]

MCC_POOL = [
    ("5411", "GROCERY"), ("5812", "RESTAURANT"), ("5732", "ELECTRONICS"),
    ("4816", "DIGITAL_GOODS"), ("5967", "DIRECT_MARKETING"), ("7995", "GAMBLING"),
    ("4829", "MONEY_TRANSFER"), ("5944", "JEWELRY"), ("5999", "MISC_RETAIL"),
    ("4121", "RIDESHARE"),
]

HIGH_RISK_MCC = {"7995", "4829", "5967", "5944"}

GEOS = ["US", "GB", "DE", "BR", "NG", "IN", "SG", "MX", "CA", "FR"]


@dataclass
class Consumer:
    id: str
    kind: str            # legit | mule | synthetic | ato_victim
    ring_id: str | None
    geo: str
    age_days: int
    identity_conf: float
    devices: list[str] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)


@dataclass
class Merchant:
    id: str
    name: str
    mcc: str
    category: str
    geo: str
    colluding: bool
    ring_id: str | None
    chargeback_rate: float


@dataclass
class Device:
    id: str
    kind: str            # phone | desktop | emulator | device_farm
    fingerprint_entropy: float
    owners: list[str] = field(default_factory=list)


@dataclass
class Ring:
    id: str
    archetype: str
    members: list[str] = field(default_factory=list)
    merchants: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)


@dataclass
class Txn:
    id: str
    step: int            # simulated minute index
    consumer_id: str
    merchant_id: str
    device_id: str
    card_id: str
    amount: float
    currency: str
    geo: str
    is_fraud: bool
    archetype: str | None


MERCHANT_ADJ = ["NEON", "VOID", "PRIME", "ORBIT", "CIPHER", "DELTA", "NOVA", "ZERO",
                "ONYX", "HALO", "FLUX", "APEX", "ECHO", "IRON", "LUNAR", "QUARTZ"]
MERCHANT_NOUN = ["MARKET", "GOODS", "PAY", "TRADE", "SUPPLY", "DIRECT", "STORE",
                 "EXPRESS", "WORKS", "LABS", "OUTLET", "HUB"]


class World:
    """A seeded synthetic payments world."""

    def __init__(self, seed: int = 1337, n_consumers: int = 420, n_merchants: int = 60,
                 n_rings: int = 7, n_txns: int = 6000):
        self.seed = seed
        self.rng = random.Random(seed)
        self.consumers: dict[str, Consumer] = {}
        self.merchants: dict[str, Merchant] = {}
        self.devices: dict[str, Device] = {}
        self.rings: dict[str, Ring] = {}
        self.txns: list[Txn] = []
        self._build(n_consumers, n_merchants, n_rings, n_txns)

    # ------------------------------------------------------------------ build
    def _build(self, n_consumers, n_merchants, n_rings, n_txns):
        rng = self.rng

        # --- merchants
        for i in range(n_merchants):
            mcc, cat = rng.choice(MCC_POOL)
            m = Merchant(
                id=f"mer_{i:03d}",
                name=f"{rng.choice(MERCHANT_ADJ)} {rng.choice(MERCHANT_NOUN)}",
                mcc=mcc, category=cat, geo=rng.choice(GEOS),
                colluding=False, ring_id=None,
                chargeback_rate=round(rng.uniform(0.001, 0.012), 4),
            )
            self.merchants[m.id] = m

        # --- legit consumers with mostly-private devices
        for i in range(n_consumers):
            c = Consumer(
                id=f"usr_{i:04d}", kind="legit", ring_id=None,
                geo=rng.choice(GEOS),
                age_days=rng.randint(90, 2400),
                identity_conf=round(rng.uniform(0.72, 0.99), 3),
            )
            for d in range(rng.choice([1, 1, 1, 2])):
                dev = Device(id=f"dev_{i:04d}_{d}", kind=rng.choice(["phone", "phone", "desktop"]),
                             fingerprint_entropy=round(rng.uniform(0.75, 0.99), 3))
                dev.owners.append(c.id)
                self.devices[dev.id] = dev
                c.devices.append(dev.id)
            c.cards = [f"card_{i:04d}_{k}" for k in range(rng.choice([1, 1, 2]))]
            self.consumers[c.id] = c

        # household devices: some legit users legitimately share a device
        legit_all = [c for c in self.consumers.values() if c.kind == "legit"]
        for h in range(len(legit_all) // 12):
            members = rng.sample(legit_all, k=rng.choice([2, 2, 3]))
            dev = Device(id=f"home_{h:03d}", kind="desktop",
                         fingerprint_entropy=round(rng.uniform(0.6, 0.9), 3))
            self.devices[dev.id] = dev
            for c in members:
                dev.owners.append(c.id)
                c.devices.append(dev.id)

        # --- fraud rings: clusters of accounts sharing device farms
        merchant_ids = list(self.merchants)
        for r in range(n_rings):
            archetype = FRAUD_ARCHETYPES[r % len(FRAUD_ARCHETYPES)]
            ring = Ring(id=f"ring_{r:02d}", archetype=archetype)

            # shared device farm
            n_farm = rng.randint(2, 4)
            for d in range(n_farm):
                dev = Device(id=f"farm_{r:02d}_{d}", kind="device_farm" if rng.random() < 0.6 else "emulator",
                             fingerprint_entropy=round(rng.uniform(0.05, 0.35), 3))
                self.devices[dev.id] = dev
                ring.devices.append(dev.id)

            # ring member accounts (mules / synthetics)
            n_members = rng.randint(6, 14)
            for j in range(n_members):
                kind = "synthetic" if archetype == "synthetic_identity" or rng.random() < 0.35 else "mule"
                # a minority of ring members are "aged" accounts with groomed identities
                groomed = rng.random() < 0.25
                c = Consumer(
                    id=f"usr_r{r:02d}_{j:02d}", kind=kind, ring_id=ring.id,
                    geo=rng.choice(GEOS),
                    age_days=rng.randint(120, 700) if groomed else rng.randint(1, 45),
                    identity_conf=round(rng.uniform(0.55, 0.8), 3) if groomed
                    else round(rng.uniform(0.12, 0.5), 3),
                )
                # members share farm devices (the graph signal)
                for dev_id in rng.sample(ring.devices, k=min(len(ring.devices), rng.randint(1, 2))):
                    self.devices[dev_id].owners.append(c.id)
                    c.devices.append(dev_id)
                # some members also keep a clean personal device for cover traffic
                if rng.random() < 0.45:
                    pd = Device(id=f"pdev_r{r:02d}_{j:02d}", kind="phone",
                                fingerprint_entropy=round(rng.uniform(0.6, 0.95), 3))
                    pd.owners.append(c.id)
                    self.devices[pd.id] = pd
                    c.devices.append(pd.id)
                c.cards = [f"card_r{r:02d}_{j:02d}_{k}" for k in range(rng.randint(1, 4))]
                ring.members.append(c.id)
                self.consumers[c.id] = c

            # colluding merchants for collusion/refund archetypes
            if archetype in ("merchant_collusion", "refund_fraud", "mule_cashout"):
                for m_id in rng.sample(merchant_ids, k=rng.randint(1, 2)):
                    self.merchants[m_id].colluding = True
                    self.merchants[m_id].ring_id = ring.id
                    self.merchants[m_id].chargeback_rate = round(rng.uniform(0.04, 0.15), 4)
                    ring.merchants.append(m_id)

            self.rings[ring.id] = ring

        # some ATO victims among legit users
        legit_ids = [c.id for c in self.consumers.values() if c.kind == "legit"]
        for cid in rng.sample(legit_ids, k=max(3, len(legit_ids) // 40)):
            self.consumers[cid].kind = "ato_victim"

        self._gen_txns(n_txns)

    # ------------------------------------------------------- transaction flow
    def _gen_txns(self, n_txns: int):
        rng = self.rng
        legit = [c for c in self.consumers.values() if c.kind in ("legit", "ato_victim")]
        ringers = [c for c in self.consumers.values() if c.ring_id]
        merchant_ids = list(self.merchants)
        t = 0
        for i in range(n_txns):
            t += rng.randint(1, 3)  # minutes advance
            fraud_roll = rng.random()

            if fraud_roll < 0.14 and ringers:          # ring-driven fraud txn
                c = rng.choice(ringers)
                ring = self.rings[c.ring_id]
                archetype = ring.archetype
                dev_id = rng.choice(c.devices)
                if ring.merchants and rng.random() < 0.7:
                    m_id = rng.choice(ring.merchants)
                else:
                    m_id = rng.choice(merchant_ids)
                stealth = rng.random() < 0.22   # blend-in "cover" transactions
                if stealth:
                    amount = round(min(400.0, rng.lognormvariate(math.log(40), 0.8)), 2)
                    dev_id = rng.choice(c.devices)
                    m_id = rng.choice(merchant_ids)
                else:
                    amount = {
                        "card_testing": round(rng.uniform(0.5, 4.0), 2),
                        "account_takeover": round(rng.uniform(180, 1400), 2),
                        "synthetic_identity": round(rng.uniform(60, 900), 2),
                        "refund_fraud": round(rng.uniform(40, 400), 2),
                        "merchant_collusion": round(rng.uniform(90, 1200), 2),
                        "mule_cashout": round(rng.uniform(300, 2500), 2),
                    }[archetype]
                txn = Txn(
                    id=f"txn_{i:06d}", step=t, consumer_id=c.id, merchant_id=m_id,
                    device_id=dev_id, card_id=rng.choice(c.cards),
                    amount=amount, currency="USD",
                    geo=rng.choice(GEOS) if rng.random() < 0.5 else c.geo,
                    is_fraud=True, archetype=archetype,
                )
            elif fraud_roll < 0.16 and legit:          # ATO burst on victim account
                c = rng.choice([x for x in legit if x.kind == "ato_victim"] or legit)
                farm = [d for d in self.devices.values() if d.kind in ("emulator", "device_farm")]
                dev = rng.choice(farm) if farm else self.devices[c.devices[0]]
                if c.id not in dev.owners:
                    dev.owners.append(c.id)
                if dev.id not in c.devices:
                    c.devices.append(dev.id)
                txn = Txn(
                    id=f"txn_{i:06d}", step=t, consumer_id=c.id,
                    merchant_id=rng.choice(merchant_ids), device_id=dev.id,
                    card_id=rng.choice(c.cards),
                    amount=round(rng.uniform(200, 1800), 2), currency="USD",
                    geo=rng.choice([g for g in GEOS if g != c.geo]),
                    is_fraud=True, archetype="account_takeover",
                )
            else:                                       # normal spend
                c = rng.choice(legit)
                mu = math.log(45)
                amount = round(min(2500.0, rng.lognormvariate(mu, 0.9)), 2)
                txn = Txn(
                    id=f"txn_{i:06d}", step=t, consumer_id=c.id,
                    merchant_id=rng.choice(merchant_ids),
                    device_id=rng.choice(c.devices),
                    card_id=rng.choice(c.cards),
                    amount=amount, currency="USD",
                    geo=c.geo if rng.random() < 0.93 else rng.choice(GEOS),
                    is_fraud=False, archetype=None,
                )
            self.txns.append(txn)

    # ------------------------------------------------------------- summaries
    def stats(self) -> dict:
        fraud = [t for t in self.txns if t.is_fraud]
        return {
            "seed": self.seed,
            "consumers": len(self.consumers),
            "merchants": len(self.merchants),
            "devices": len(self.devices),
            "rings": len(self.rings),
            "transactions": len(self.txns),
            "fraud_txns": len(fraud),
            "fraud_rate": round(len(fraud) / max(1, len(self.txns)), 4),
            "fraud_volume_usd": round(sum(t.amount for t in fraud), 2),
            "total_volume_usd": round(sum(t.amount for t in self.txns), 2),
            "archetypes": {a: sum(1 for t in fraud if t.archetype == a) for a in FRAUD_ARCHETYPES},
        }


_default_world: World | None = None


def get_world(seed: int = 1337) -> World:
    global _default_world
    if _default_world is None or _default_world.seed != seed:
        _default_world = World(seed=seed)
    return _default_world
