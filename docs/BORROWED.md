# Borrowed schemas and ideas

| Source | What ACT borrows | License / status |
|---|---|---|
| A2A 1.0 Agent Card (a2a-protocol.org) | name, description, version, url, provider, capabilities, skills, securitySchemes in the Agent Record | Apache-2.0; fields copied, not imported |
| AGNTCY OASF (docs.agntcy.org) | skill and domain taxonomy tags (`oasf.skills`, `oasf.domains`) | Apache-2.0; taxonomy strings only |
| OpenTelemetry GenAI semantic conventions (open-telemetry/semantic-conventions) | invoke_agent, execute_tool, gen_ai.agent.id span dialect for the normalizer | Apache-2.0; attribute names only |
| OpenInference (Arize-ai/openinference) | span kinds and attributes for the second span dialect | Apache-2.0; attribute names only |
| OCSF 1.8 `ai_agent` object (schema.ocsf.io) | actor fields of the canonical event | Apache-2.0; field shape copied |
| ValidMind Atryum (github.com/validmind/atryum) | shape of a per-action decision record, used in event attrs | Apache-2.0; shape copied, no code |
| Microsoft Agent Governance Toolkit (open source, April 2026) | runtime record type ideas for findings | MIT per upstream repository; verify before reuse; ideas only |
| MLflow 3 tracing | MLflow-exported span dialect | Apache-2.0; used as a pip dependency elsewhere |
