"""Phase 5/6 test suite — tenancy, RBAC/ABAC, audit chain, crypto-shredding,
privacy-preserving federation, and uplift."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest

from argus.federation import BloomFilter, RiskFabric, fingerprint
from argus.tenancy import EnterprisePlane


@pytest.fixture(scope="module")
def plane():
    return EnterprisePlane()


@pytest.fixture(scope="module")
def fabric(plane):
    return RiskFabric(plane)


# ------------------------------------------------------------ Phase 5: RBAC
def test_rbac_denies_out_of_role(plane):
    viewer = next(u for u in plane.scim_users if u["role"] == "viewer")
    assert plane.check_access(viewer["api_key"], "policies.write")["allow"] is False
    assert plane.check_access(viewer["api_key"], "cases.read")["allow"] is True


def test_abac_overlay_blocks_sensitive_reads(plane):
    viewer = next(u for u in plane.scim_users if u["role"] == "viewer")
    r = plane.check_access(viewer["api_key"], "cases.read", {"case_sensitivity": True})
    assert r["allow"] is False and r["layer"] == "ABAC"


def test_unknown_key_rejected(plane):
    assert plane.check_access("ak_forged", "cases.read")["allow"] is False


# ------------------------------------------------------- Phase 5: audit chain
def test_audit_chain_valid_and_tamper_evident(plane):
    v = plane.audit.verify()
    assert v["valid"] and v["checked"] >= 10
    forged = [dict(e) for e in plane.audit.events]
    forged[1] = dict(forged[1], action="access.allow")
    v2 = plane.audit.verify(forged)
    assert v2["valid"] is False and v2["broken_at_seq"] == 1


# ---------------------------------------------------- Phase 5: crypto-shred
def test_crypto_shred_destroys_key(plane):
    tenant = plane.tenants["tn_helion"]
    entity = next(iter(tenant.world.consumers))
    r = plane.erase("tn_helion", entity)
    assert r["erased"] and r["method"] == "crypto_shred"
    assert entity in tenant.shredded and entity not in tenant.dek
    # audit trail records the erasure
    assert any(e["action"] == "privacy.erase" and e["resource"] == entity
               for e in plane.audit.events)


# ------------------------------------------------- Phase 5: tenant isolation
def test_tenant_worlds_are_isolated(plane):
    helion = set(plane.tenants["tn_helion"].world.consumers)
    vulcan = set(plane.tenants["tn_vulcan"].world.consumers)
    assert not (helion & vulcan)


# -------------------------------------------------- Phase 6: bloom + privacy
def test_bloom_no_false_negatives():
    b = BloomFilter()
    items = [fingerprint("device", f"dev_{i}") for i in range(200)]
    for i in items:
        b.add(i)
    assert all(i in b for i in items)


def test_probe_discloses_membership_only(fabric):
    r = fabric.probe("tn_nimbus", "device", "gfarm_00_0")
    assert r["hit"] is True
    assert set(r) == {"hit", "networks", "archetypes", "disclosure"}
    assert "entity" not in str(r).lower() or "no entities" in r["disclosure"]


def test_probe_excludes_requesting_tenant(fabric):
    # a device known ONLY to helion must not "hit" when helion asks
    helion = fabric.plane.tenants["tn_helion"].world
    local_only = next(d for d in helion.devices if d.startswith("hel_farm_"))
    r = fabric.probe("tn_helion", "device", local_only)
    assert r["hit"] is False and r["networks"] == 0


# ------------------------------------------------------- Phase 6: uplift + FL
def test_federation_uplift_positive(fabric):
    u = fabric.uplift()
    assert u["per_tenant"]
    assert u["avg_uplift_pp"] > 5.0
    for row in u["per_tenant"]:
        assert row["recall_federated"] >= row["recall_solo"]


def test_intel_hit_annotates_decision(fabric):
    tenant = fabric.plane.tenants["tn_helion"]
    sleeper = next(c for c in tenant.world.consumers.values()
                   if c.id.startswith("slp_"))
    d = fabric.evaluate_with_intel("tn_helion", {
        "id": "t_fed", "step": 999999, "consumer_id": sleeper.id,
        "merchant_id": next(iter(tenant.world.merchants)),
        "device_id": sleeper.devices[0], "card_id": sleeper.cards[0],
        "amount": 260.0, "geo": sleeper.geo,
    })
    assert d["network_intel"]["hit"] is True
    assert any(r.startswith("network_intel_hit") for r in d["action_reasons"])
    assert d["risk_score"] > d["risk_score_solo"]


def test_fl_rounds_deterministic_and_converging(fabric):
    a = fabric.run_fl_rounds(rounds=8, dp_epsilon=4.0)
    b = fabric.run_fl_rounds(rounds=8, dp_epsilon=4.0)
    assert a["rounds"] == b["rounds"]
    assert a["rounds"][-1]["model_divergence"] < a["rounds"][0]["model_divergence"]
