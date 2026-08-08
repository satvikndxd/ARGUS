"""ARGUS Python SDK — thin, typed client for the ARGUS decision API.

    from argus_sdk import ArgusClient

    client = ArgusClient(base_url="http://localhost:8000", api_key="sk_test_...")
    decision = client.evaluate(
        consumer_id="usr_r00_00", merchant_id="mer_031",
        device_id="farm_00_0", amount=520.0, geo="US",
    )
    print(decision["risk_score"], decision["decision"])
"""

from __future__ import annotations

import json
import urllib.request

__version__ = "0.1.0"


class ArgusClient:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str | None = None,
                 timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------ transport
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.base_url + path, method=method)
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        data = json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(req, data=data, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    # ------------------------------------------------------------- surface
    def evaluate(self, *, consumer_id: str, merchant_id: str, device_id: str,
                 amount: float, geo: str = "US", card_id: str = "card_adhoc",
                 transaction_id: str | None = None) -> dict:
        """Score a transaction in real time. Returns the full decision bundle
        (risk_score, decision, reason codes, graph evidence, counterfactuals)."""
        return self._request("POST", "/v1/transactions:evaluate", {
            "transaction_id": transaction_id, "consumer_id": consumer_id,
            "merchant_id": merchant_id, "device_id": device_id,
            "card_id": card_id, "amount": amount, "geo": geo,
        })

    def entity_graph(self, entity_id: str, depth: int = 2, limit: int = 120) -> dict:
        return self._request("GET", f"/v1/entities/{entity_id}/graph?depth={depth}&limit={limit}")

    def communities(self) -> dict:
        return self._request("GET", "/v1/graph/communities")

    def open_investigation(self, transaction_id: str) -> dict:
        return self._request("POST", "/v1/investigations", {"transaction_id": transaction_id})

    def run_simulation(self, scenario: str, seed: int = 1337) -> dict:
        return self._request("POST", f"/v1/simulations/{scenario}:run?seed={seed}")

    def metrics(self) -> dict:
        return self._request("GET", "/api/metrics")
