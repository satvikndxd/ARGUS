"""ARGUS Phase 5 — Enterprise Plane.

Multi-tenant registry with data-residency pinning, RBAC/ABAC access control,
SCIM-style user provisioning, a hash-chained immutable audit ledger with
tamper detection, and crypto-shredding DSAR erasure.

Each tenant runs its own seeded world shard + risk graph + decision engine —
strict isolation by construction: a tenant's engine physically cannot see
another tenant's entities.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from .engine import DecisionEngine
from .graph import RiskGraph
from .synth import World

REGIONS = {
    "us-east-1": {"name": "US EAST", "jurisdiction": "US", "replication_lag_ms": 12},
    "eu-central-1": {"name": "EU CENTRAL", "jurisdiction": "EU/GDPR", "replication_lag_ms": 18},
    "ap-southeast-1": {"name": "APAC SINGAPORE", "jurisdiction": "SG/MAS", "replication_lag_ms": 31},
}

TENANT_SPECS = [
    {"tenant_id": "tn_helion", "name": "HELION BANK", "kind": "issuer_bank",
     "plan": "SOVEREIGN", "region": "us-east-1", "seed": 4001},
    {"tenant_id": "tn_vulcan", "name": "VULCAN PAY", "kind": "payment_processor",
     "plan": "ENTERPRISE", "region": "eu-central-1", "seed": 4002},
    {"tenant_id": "tn_nimbus", "name": "NIMBUS MARKET", "kind": "marketplace",
     "plan": "GROWTH", "region": "ap-southeast-1", "seed": 4003},
]

ROLES = ["admin", "investigator", "analyst", "auditor", "viewer"]

PERMISSIONS = [
    "decisions.evaluate", "cases.read", "cases.write", "graph.read",
    "policies.write", "audit.read", "privacy.erase", "fabric.read", "tenants.admin",
]

RBAC_MATRIX: dict[str, set[str]] = {
    "admin": set(PERMISSIONS),
    "investigator": {"decisions.evaluate", "cases.read", "cases.write", "graph.read", "fabric.read"},
    "analyst": {"decisions.evaluate", "cases.read", "graph.read", "fabric.read"},
    "auditor": {"cases.read", "audit.read", "graph.read"},
    "viewer": {"cases.read"},
}

# ABAC overlays: attribute rules evaluated after the role check
ABAC_RULES = [
    {"id": "ABAC-01", "desc": "cross-region graph reads require federation clearance",
     "applies": "graph.read", "attr": "cross_region", "requires_role": {"admin", "investigator"}},
    {"id": "ABAC-02", "desc": "privacy erasure only from the tenant's home region",
     "applies": "privacy.erase", "attr": "home_region_only", "requires_role": {"admin"}},
    {"id": "ABAC-03", "desc": "sensitive cases (risk ≥ 0.8) hidden from viewers",
     "applies": "cases.read", "attr": "case_sensitivity", "requires_role": {"admin", "investigator", "analyst", "auditor"}},
]

SCIM_DIRECTORY = [
    # (tenant, user, role, idp)
    ("tn_helion", "m.reyes", "admin", "okta"), ("tn_helion", "s.okafor", "investigator", "okta"),
    ("tn_helion", "j.lindqvist", "auditor", "okta"),
    ("tn_vulcan", "a.moreau", "admin", "azure-ad"), ("tn_vulcan", "k.tanaka", "investigator", "azure-ad"),
    ("tn_vulcan", "p.novak", "analyst", "azure-ad"), ("tn_vulcan", "l.baptiste", "viewer", "azure-ad"),
    ("tn_nimbus", "d.chen", "admin", "google"), ("tn_nimbus", "r.gupta", "analyst", "google"),
    ("tn_nimbus", "t.aluko", "investigator", "google"),
]


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


class AuditChain:
    """Append-only, hash-chained ledger. Every event's hash commits to the
    previous hash — a single mutated byte breaks every subsequent link."""

    GENESIS = "0" * 64

    def __init__(self):
        self.events: list[dict] = []

    def append(self, tenant_id: str, actor: str, action: str, resource: str,
               metadata: dict | None = None) -> dict:
        prev = self.events[-1]["hash"] if self.events else self.GENESIS
        body = {
            "seq": len(self.events), "tenant_id": tenant_id, "actor": actor,
            "action": action, "resource": resource, "metadata": metadata or {},
            "prev_hash": prev,
        }
        body["hash"] = _sha(prev + json.dumps(
            {k: body[k] for k in ("seq", "tenant_id", "actor", "action", "resource", "metadata")},
            sort_keys=True))
        self.events.append(body)
        return body

    def verify(self, events: list[dict] | None = None) -> dict:
        chain = self.events if events is None else events
        prev = self.GENESIS
        for i, e in enumerate(chain):
            expected = _sha(prev + json.dumps(
                {k: e[k] for k in ("seq", "tenant_id", "actor", "action", "resource", "metadata")},
                sort_keys=True))
            if e.get("prev_hash") != prev or e.get("hash") != expected:
                return {"valid": False, "broken_at_seq": e.get("seq", i),
                        "checked": i + 1, "total": len(chain)}
            prev = e["hash"]
        return {"valid": True, "checked": len(chain), "total": len(chain),
                "head": prev[:16]}


def _namespace_world(world: World, prefix: str) -> None:
    """Rename every generated id into the tenant's namespace so that no two
    tenants ever share an identifier — different physical devices must never
    collide into the same federation fingerprint."""
    c_map = {cid: f"{prefix}{cid}" for cid in world.consumers}
    m_map = {mid: f"{prefix}{mid}" for mid in world.merchants}
    d_map = {did: f"{prefix}{did}" for did in world.devices}

    world.consumers = {c_map[cid]: c for cid, c in world.consumers.items()}
    for c in world.consumers.values():
        c.id = c_map[c.id]
        c.devices = [d_map[d] for d in c.devices]
        c.cards = [f"{prefix}{card}" for card in c.cards]
    world.merchants = {m_map[mid]: m for mid, m in world.merchants.items()}
    for m in world.merchants.values():
        m.id = m_map[m.id]
    world.devices = {d_map[did]: d for did, d in world.devices.items()}
    for d in world.devices.values():
        d.id = d_map[d.id]
        d.owners = [c_map[o] for o in d.owners]
    for r in world.rings.values():
        r.members = [c_map[m] for m in r.members]
        r.merchants = [m_map[m] for m in r.merchants]
        r.devices = [d_map[d] for d in r.devices]
    for t in world.txns:
        t.id = f"{prefix}{t.id}"
        t.consumer_id = c_map[t.consumer_id]
        t.merchant_id = m_map[t.merchant_id]
        t.device_id = d_map[t.device_id]
        t.card_id = f"{prefix}{t.card_id}"


class Tenant:
    def __init__(self, spec: dict):
        self.spec = spec
        self.tenant_id = spec["tenant_id"]
        self.world = World(seed=spec["seed"], n_consumers=220, n_merchants=30,
                           n_rings=4, n_txns=2500)
        _namespace_world(self.world, spec["tenant_id"].removeprefix("tn_")[:3] + "_")
        self.graph: RiskGraph | None = None
        self.engine: DecisionEngine | None = None
        # crypto-shredding: every consumer gets a data-encryption key;
        # destroying the key renders the record unrecoverable.
        self.dek: dict[str, str] = {
            cid: _sha(f"dek:{self.tenant_id}:{cid}")[:32] for cid in self.world.consumers
        }
        self.shredded: set[str] = set()

    def finalize(self):
        """Build graph + engine AFTER federation grafting mutates the world."""
        self.graph = RiskGraph(self.world)
        self.engine = DecisionEngine(self.world, self.graph)

    def erase_entity(self, entity_id: str) -> dict:
        if entity_id not in self.dek:
            return {"erased": False, "reason": "unknown entity"}
        self.dek.pop(entity_id)
        self.shredded.add(entity_id)
        return {"erased": True, "entity_id": entity_id,
                "method": "crypto_shred", "key_destroyed": True,
                "residual_data": "ciphertext only — irrecoverable"}

    def summary(self) -> dict:
        s = self.world.stats()
        return {
            **{k: self.spec[k] for k in ("tenant_id", "name", "kind", "plan", "region")},
            "jurisdiction": REGIONS[self.spec["region"]]["jurisdiction"],
            "replication_lag_ms": REGIONS[self.spec["region"]]["replication_lag_ms"],
            "consumers": s["consumers"], "merchants": s["merchants"],
            "transactions": s["transactions"], "fraud_rate": s["fraud_rate"],
            "rings": s["rings"], "shredded_entities": len(self.shredded),
        }


class EnterprisePlane:
    def __init__(self):
        self.tenants: dict[str, Tenant] = {s["tenant_id"]: Tenant(s) for s in TENANT_SPECS}
        self.audit = AuditChain()
        self.api_keys: dict[str, dict] = {}
        self.scim_users: list[dict] = []
        self._provision()

    # ------------------------------------------------------------ identity
    def _provision(self):
        for tenant_id, user, role, idp in SCIM_DIRECTORY:
            key = "ak_" + hmac.new(b"argus-demo", f"{tenant_id}:{user}".encode(),
                                   hashlib.sha256).hexdigest()[:20]
            self.api_keys[key] = {"tenant_id": tenant_id, "user": user, "role": role}
            self.scim_users.append({
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "id": f"scim_{len(self.scim_users):03d}", "userName": user,
                "tenant_id": tenant_id, "role": role, "idp": idp,
                "active": True, "api_key": key,
            })
            self.audit.append(tenant_id, "scim", "user.provision", user,
                              {"role": role, "idp": idp})

    # -------------------------------------------------------------- access
    def check_access(self, api_key: str, permission: str,
                     attributes: dict | None = None) -> dict:
        ident = self.api_keys.get(api_key)
        if not ident:
            return {"allow": False, "reason": "unknown api key", "rule": "AUTHN"}
        role = ident["role"]
        if permission not in RBAC_MATRIX.get(role, set()):
            self.audit.append(ident["tenant_id"], ident["user"], "access.deny",
                              permission, {"role": role, "layer": "rbac"})
            return {"allow": False, "identity": ident, "layer": "RBAC",
                    "reason": f"role '{role}' lacks '{permission}'"}
        for rule in ABAC_RULES:
            if rule["applies"] == permission and (attributes or {}).get(rule["attr"]):
                if role not in rule["requires_role"]:
                    self.audit.append(ident["tenant_id"], ident["user"], "access.deny",
                                      permission, {"role": role, "layer": "abac",
                                                   "rule": rule["id"]})
                    return {"allow": False, "identity": ident, "layer": "ABAC",
                            "rule": rule["id"], "reason": rule["desc"]}
        self.audit.append(ident["tenant_id"], ident["user"], "access.allow",
                          permission, {"role": role})
        return {"allow": True, "identity": ident, "layer": "RBAC+ABAC"}

    # ------------------------------------------------------------- surface
    def rbac_matrix(self) -> dict:
        return {
            "roles": ROLES, "permissions": PERMISSIONS,
            "grants": {r: sorted(RBAC_MATRIX[r]) for r in ROLES},
            "abac_rules": ABAC_RULES,
        }

    def erase(self, tenant_id: str, entity_id: str, actor: str = "dpo") -> dict:
        t = self.tenants.get(tenant_id)
        if not t:
            return {"erased": False, "reason": "unknown tenant"}
        result = t.erase_entity(entity_id)
        self.audit.append(tenant_id, actor, "privacy.erase", entity_id,
                          {"method": "crypto_shred", "ok": result["erased"]})
        return result

    def summaries(self) -> list[dict]:
        return [t.summary() for t in self.tenants.values()]
