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

## break_classifier endpoint (live)

Published as Domino sync endpoint `break-classifier` from registry model `break_classifier`, serving the version
currently behind alias `champion`, model ID `6af239a468f40b2f5611dd6c`, hardware tier `small-model-api`.

| Variable | Value |
|---|---|
| `BREAK_CLASSIFIER_ENDPOINT_URL` | `https://se-demo.domino.tech/models/6af239a468f40b2f5611dd6c/latest/model` |
| `BREAK_CLASSIFIER_ACCESS_KEY` | Project environment variable (secret, not recorded here). It is the endpoint access key named `BREAK_CLASSIFIER_ACCESS_KEY (agent-created)` under the endpoint's Settings > Invocation. |

The registry endpoint answers `{"result": ["<class>"]}`; `classify_break` unwraps the list.

