# ACT fixtures (T-3)

Fixture feeds for AC-1 (11 platform agents; with one live Domino agent they make the fleet) and AC-12 (weekend drift).
Each platform keeps its own envelope. Bank extras (owner, tools, data scopes, systems, value limits, criteria) sit in the
platform's free-form slot. Exactly one agent per platform has `identity.type == "none"` (no distinct identity).

| File | Source API shape | Count |
|---|---|---|
| aws/list-agent-runtimes.json | Bedrock AgentCore `ListAgentRuntimes` (`agentRuntimes[]`); extras in `tags`, `identity`, `metadata` | 3 (treasury-sweep-agent, kyc-refresh-agent, trade-break-triage; the last has no distinct identity) |
| aws/list-discoverable-registry-records.json | Agent Registry `ListDiscoverableRegistryRecords` (`registryRecords[]`) | 3 records for the same agents |
| google/agent-registry-services.json | Google Agent Registry `Service` resources (`services[]`, names `projects/.../locations/.../services/...`); extras in `labels`, `properties`, `identity` | 3 (payments-fraud-screener, collections-outreach-agent, regulatory-report-drafter; last has no identity) |
| google/agent-registry-bindings.json | Agent Registry `Binding` resources, grouped by service name under `bindingsByService` | 3 |
| microsoft/list-packages.json | Agent 365 Graph "List packages" (`@odata.context`, `value[]`); extras in `identity`, `customMetadata`, `owner` | 3 (relationship-manager-copilot, credit-memo-drafter, branch-ops-assistant; last has no identity) |
| databricks/agent-services.json | Unity Catalog `/api/2.1/unity-catalog/agent-services` (`agent_services[]`); extras in `owner`, `tags`, `properties`, `identity` | 2 (liquidity-forecast-agent, model-risk-doc-agent; last has no identity) |
| runs/otel-genai-kyc-refresh.json | OTLP/HTTP JSON, OTel GenAI dialect, in-manifest run | 8 spans |
| runs/otel-genai-treasury-sweep-normal.json | OTel GenAI, in-manifest run (transfer_funds 180000 under the 250000 limit) | 8 spans |
| runs/openinference-credit-memo.json | OTLP JSON, OpenInference dialect (AGENT/TOOL/LLM) | 5 spans |
| runs/mlflow-liquidity-forecast.json | OTLP JSON, MLflow-exported dialect (AGENT/TOOL/CHAT_MODEL) | 5 spans |
| runs/weekend-drift.json | AC-12: treasury-sweep-agent, OTel GenAI, Sat 2026-11-07 UTC. transfer_funds amount 4750000 at 02:14:00Z (limit 250000), one list_credentials call (not in manifest), rest in-manifest | 40 spans |
| external/agent-card.json | A2A 1.0 Agent Card for a client-side treasury bot | 3 skills |
| external/serve.py | stdlib server: `python3 serve.py --port 8765` serves `/.well-known/agent-card.json` on 0.0.0.0 | n/a |

treasury-sweep-agent manifest: tools get_balance, sweep_plan, get_fx_rate, transfer_funds, post_journal_entry (no list_credentials);
`value_limits.transfer_funds = {amount: 250000, currency: USD}`; criteria autonomous / moves_money (Tier 1).
`gen_ai.agent.id` in OTel runs is the AWS `agentRuntimeId`.
