/**
 * ARGUS TypeScript SDK — thin, typed client for the ARGUS decision API.
 *
 *   import { ArgusClient } from "@argus/sdk";
 *   const client = new ArgusClient({ baseUrl: "http://localhost:8000" });
 *   const d = await client.evaluate({ consumer_id: "usr_r00_00", merchant_id: "mer_031",
 *                                     device_id: "farm_00_0", amount: 520 });
 *   console.log(d.risk_score, d.decision);
 */

export type Action = "approve" | "monitor" | "step_up" | "review" | "decline";

export interface Decision {
  transaction_id: string;
  decision: Action;
  risk_score: number;
  confidence: number;
  action_reasons: string[];
  model_scores: Record<string, { score: number; version: string; weight: number }>;
  graph_evidence: {
    entity_id: string;
    community_id: string | null;
    device_sharing_max: number;
    linked_high_risk_entities: number;
    entity_risk: number;
  };
  counterfactuals: { intervention: string; narrative: string; risk_delta: number }[];
  policy_hits: { id: string; name: string; version: number; action: Action; desc: string }[];
  explanation: string;
  review_required: boolean;
  latency_ms: number;
}

export interface EvaluateRequest {
  transaction_id?: string;
  consumer_id: string;
  merchant_id: string;
  device_id: string;
  card_id?: string;
  amount: number;
  geo?: string;
}

export class ArgusClient {
  private baseUrl: string;
  private apiKey?: string;

  constructor(opts: { baseUrl?: string; apiKey?: string } = {}) {
    this.baseUrl = (opts.baseUrl ?? "http://localhost:8000").replace(/\/$/, "");
    this.apiKey = opts.apiKey;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(this.baseUrl + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`ARGUS ${method} ${path} → ${res.status}`);
    return res.json() as Promise<T>;
  }

  evaluate(req: EvaluateRequest): Promise<Decision> {
    return this.request("POST", "/v1/transactions:evaluate", req);
  }

  entityGraph(entityId: string, depth = 2, limit = 120) {
    return this.request("GET", `/v1/entities/${entityId}/graph?depth=${depth}&limit=${limit}`);
  }

  communities() {
    return this.request("GET", "/v1/graph/communities");
  }

  openInvestigation(transactionId: string) {
    return this.request("POST", "/v1/investigations", { transaction_id: transactionId });
  }

  runSimulation(scenario: string, seed = 1337) {
    return this.request("POST", `/v1/simulations/${scenario}:run?seed=${seed}`);
  }

  metrics() {
    return this.request("GET", "/api/metrics");
  }
}
