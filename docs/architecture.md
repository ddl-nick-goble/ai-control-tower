# ACT architecture

ACT (Agent Control Tower) is an evidence layer. It inventories agents from several platforms, records what they
did, and produces dated evidence for governance. It does not block or enforce anything.

## Integration map

Five intake adapters feed one normalizer and one store. Traces from any agent take a second path through the
span normalizer into a hash-chained event log, where the drift detector turns out-of-manifest behaviour into
findings. LLM calls go through one seam that is either the Domino LLM Gateway or an offline mock.

```mermaid
flowchart LR
  subgraph intake["Intake adapters (adapters/)"]
    aws["AWS AgentCore (replay)"]
    goog["Google Agent Registry (replay)"]
    ms["Microsoft Agent 365 (replay)"]
    dbx["Databricks Unity Catalog (replay)"]
    dom["Domino Apps and Agents (live) + local agents file"]
  end
  aws --> norm
  goog --> norm
  ms --> norm
  dbx --> norm
  dom --> norm
  norm["normalize(): Agent Record, tier, ACT id"] --> store[("SQLite store: 12 core tables plus overrides")]
  ext["POST /api/agents/external (agent card URL)"] --> norm
  store --> gov["governance sync: bundle, evidence, approval mirror"]
  gov --> dgov["Domino Governance API"]
  store --> api["FastAPI routes"]
  api --> console["React console: fleet, agent, timeline, evidence pack, passport"]

  traces["POST /v1/traces (OTLP JSON or protobuf)"] --> ns["normalize_span(): canonical event"]
  ns --> chain["hash chain (events.prev_hash, events.hash)"]
  chain --> store
  chain --> drift["drift detector: tools outside manifest, value limits"]
  drift --> findings["findings (scope_drift, shadow_agent)"]
  findings --> store

  subgraph llm["LLM seam (backend/llm.py, agents/recon_agent/llm.py)"]
    mock["mock provider (ACT_LLM_PROVIDER=mock): template text, mock-llm judge"]
    gw["Domino LLM Gateway (LLM_GATEWAY_URL)"]
  end
  drift -. explanation .-> llm
  recon["recon-agent"] --> llm
  recon --> traces
  validate["jobs/validate.py"] --> llm
  validate --> store
  store --> evid["evidence pack: Markdown and PDF"]
  evid --> console
```

## Trace path

```mermaid
sequenceDiagram
  autonumber
  participant Ag as Agent (recon-agent)
  participant Ex as MLflow tracing / ACT exporter
  participant In as POST /v1/traces
  participant Ns as normalize_span
  participant Ch as Hash chain
  participant Dd as Drift detector
  participant St as Store (findings)
  Ag->>Ex: spans (invoke_agent, execute_tool, chat)
  Ex->>In: OTLP/HTTP JSON or protobuf, bearer token
  In->>Ns: parsed spans (OTel GenAI, OpenInference, MLflow dialects)
  Ns->>Ch: canonical events (run_id, agent_id, ts, kind, name, args_redacted)
  Ch->>St: append events with prev_hash and hash
  Ch->>Dd: run committed (hook)
  Dd->>St: compare observed tools and values with the manifest
  Dd->>St: scope_drift finding with violations and explanation
  In-->>Ex: 200 {accepted, agents_matched}
```

## Data model

The diagram shows the 12 core tables. An additional overrides table (recorded human overrides of findings) is not drawn.

```mermaid
erDiagram
  agents ||--o{ agent_versions : has
  agents ||--o{ manifests : has
  agents ||--o{ identities : bound_to
  agents ||--o{ dependencies : depends_on
  agents ||--o{ runs : executes
  agents ||--o{ findings : raises
  agents ||--o{ approvals : approved_by
  agents ||--o{ validation_records : validated_by
  agents ||--o{ evidence_packs : documented_by
  runs ||--o{ events : records
  runs ||--o{ findings : triggers
  agents {
    string id PK
    string source_platform
    string native_id
    string name
    string tier
    string status
    string registered_by
    string bundle_id
  }
  agent_versions {
    int id PK
    string agent_id FK
    string version
  }
  manifests {
    int id PK
    string agent_id FK
    json content
  }
  identities {
    int id PK
    string agent_id FK
    string kind
    string principal
  }
  dependencies {
    int id PK
    string agent_id FK
    string kind
    string name
  }
  runs {
    string id PK
    string agent_id FK
    string status
    string started_at
  }
  events {
    int id PK
    string run_id
    string agent_id
    string kind
    string prev_hash
    string hash
  }
  findings {
    int id PK
    string agent_id FK
    string run_id
    string type
    string status
  }
  approvals {
    int id PK
    string agent_id FK
    string approver
    string expires_at
  }
  validation_records {
    int id PK
    string agent_id FK
    json detail
  }
  evidence_packs {
    int id PK
    string agent_id FK
    string path
  }
  audit_events {
    int id PK
    string actor_user_id
    string action
    string target
    string ts
  }
```
