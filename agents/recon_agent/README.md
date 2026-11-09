# recon agent

Reconciliation assistant hosted in Domino. Tools: fetch_ledger, compare_entries, propose_adjustment;
breaks are classified by the registered `break_classifier` model.

## Environment

| Variable | Meaning |
|---|---|
| `RECON_AGENT_ID` | Required. Must equal the Domino agent's native id as listed by the Domino API (the id ACT's Domino adapter reads). `run()` raises before any LLM call when unset. It is set as `gen_ai.agent.id` on the OTLP resource and on every span. |
| `LLM_GATEWAY_URL` | LLM Gateway App URL. |
| `ACT_TRACES_URL`, `ACT_TOKEN_URL` | ACT OTLP endpoint and token source (export is a no-op without the URL). |
| `BREAK_CLASSIFIER_ENDPOINT_URL` | Domino sync endpoint. Unset: local `predict()` (span `act.classifier.source=local`); a warning is logged when running in Domino (`DOMINO_RUN_ID`). |
| `BREAK_CLASSIFIER_ACCESS_KEY` | Endpoint access key, sent as HTTP Basic user and password (`endpoint` source). The platform bearer token is never sent to the endpoint. |

Experiment: `act-recon-<DOMINO_PROJECT_NAME>-<DOMINO_STARTING_USERNAME>` (root `experiment_setup.py`, `tracing_setup.py`).
