# Integration switches and stand-ins

ACT runs end to end today with stand-ins for four live pieces: the LLM Gateway, a deployed Domino Agent, a
service-account identity and a second approver. Each is swapped by environment variable only, and each stand-in
labels itself honestly: template text in the console, the mock judge in the validation record and the evidence pack. Set variables as Domino project or user variables
(secrets as secret variables), then restart the App or rerun the Job.

| Switch | Current state | What is mocked | Go live |
|---|---|---|---|
| `ACT_DEMO_MODE` | Unset (off). `1` or `bash app.sh --demo` turns on demo mode | Everything that needs a live prerequisite: mock LLM, locally hosted recon agent, simulated Governance (`demo-` bundle ids), demo classifier (`act.classifier.source=demo`), simulated service account `svc-act-recon-demo`, simulated second approver `demo-approver-simulated`; separate demo DB; auto-seed. All labelled in the UI banner and `GET /api/health`. See [DEMO.md](DEMO.md). | Unset `ACT_DEMO_MODE` and use the rows below, one stand-in at a time. |
| `ACT_LLM_PROVIDER` | `mock` for the demo (unset = `gateway`) | Recon agent tool loop and validation judge (responses carry model `mock-llm`). Drift explanations make no LLM call in mock mode: a deterministic template is used. `GET /api/health` reports `llm_provider`; a warning is logged at startup. | Unset it, or set `gateway`, and set `LLM_GATEWAY_URL` (next row). |
| `LLM_GATEWAY_URL` | Not set | Needed only with the `gateway` provider | 1. Open the gateway App once in a browser and accept consent: https://apps.se-demo.domino.tech/apps/llmgateway/ . 2. Set `LLM_GATEWAY_URL` to that URL. 3. Confirm `GET <url>/v1/models` (with the in-run bearer) lists the `sonnet` and `haiku` aliases, or change the aliases in `agents/recon_agent/llm.py` callers. 4. Unset `ACT_LLM_PROVIDER`. Outside Domino also set `LLM_GATEWAY_TOKEN`. |
| `ACT_DOMINO_LOCAL_AGENTS` | Points at `config/local_agents.json`: lists `recon-agent` (native id `local-recon-agent`, hosting "workspace (not deployed)") | The agent is not a deployed Domino Agent; it runs in a workspace or Job. Spans and the classifier call are real. | Deploy `agents/recon_agent` as a Domino Agent. Set `RECON_AGENT_ID` to the Domino app id of that Agent, unset `ACT_DOMINO_LOCAL_AGENTS` (or delete the entry), rerun intake. The deployed Agent then appears from the Apps API. Its ACT id changes, so rerun validation (below) for the new id. Cleanup: archive or delete the old `act:domino` row for `local-recon-agent` once the real Agent is deployed, so the placeholder does not linger beside it. |
| `ACT_LOCAL_AGENT_OWNER`, `ACT_LOCAL_AGENT_PRINCIPAL` | Owner defaults to the placeholder `unassigned-owner` | Owner of the local entry | Set to the real owner; the principal defaults to the owner. |
| `ACT_SERVICE_ACCOUNTS` | Not set: the agent's identity is reported as a workspace user, `distinct: false` (honest) | The distinct service-account identity | Create a Domino service account for the agent, run the Agent or Job as it, set `ACT_SERVICE_ACCOUNTS` to a comma-separated list of those principals. Intake then records `domino_service_account`, `distinct: true`. The local entry's principal must be in the list. |
| Second approver | Approval requires a different Domino user than the one who registered the agent (HTTP 403 otherwise). With one user, approval cannot be completed. | Nothing is mocked; the control is real | Sign in to the ACT App as a second Domino user (any user with App access) and use Approve on the agent. The registrant is recorded at intake or registration; same user id or username is refused and audited. |
| `BREAK_CLASSIFIER_ENDPOINT_URL`, `BREAK_CLASSIFIER_ACCESS_KEY` | Fallback order: endpoint (URL + `BREAK_CLASSIFIER_ACCESS_KEY`) -> `BREAK_CLASSIFIER_MODEL_PATH` (local model) -> registry champion (`models:/break_classifier@champion`) -> `unclassified` with a warning | The registry endpoint | Set the URL of the sync endpoint `break-classifier` (serves registry model `break_classifier`, champion v6) and the endpoint access key (secret). Spans then show source `endpoint`. `BREAK_CLASSIFIER_REGISTRY_VERSION` (default `6`) is the version recorded in the dependency list. |
| `ACT_DOMINO_LOCAL_AGENTS` bad file | A missing or invalid file logs a warning and adds no local agents; intake is unaffected | | |
| `ACT_URL` | Unset for in-process runs | Job-to-App HTTP | Set to the ACT App URL so `jobs/validate.py` posts with the in-run bearer. Unset: the job writes the record straight to the ACT database (same validation rules as the route). |
| `ACT_TRACES_URL` | Set per run | None | OTLP endpoint of ACT (`<app>/v1/traces`); unset means no export. |
| `ACT_EXTERNAL_ALLOW_HOSTS` | Not set: external agent-card fetches are refused | Hosts allowed for external card fetch | Comma-separated hostnames to permit. Private, link-local and port 8899 stay refused. |
| `ACT_TRUSTED_JWKS_PATH` | Not set: signed cards cannot be verified | Trusted signer keys | Path to a JWK or `{"keys": [...]}` file. Cards signed by those keys show as verified. |
| `ACT_POLICY_AGENTIC_ID`, `ACT_POLICY_MODEL_ID` | Not set | Governance policy ids | Set to the Domino Governance policy ids for agentic and model bundles. |
| `ACT_SETTINGS_OPEN` | Unset (open): anyone who can reach the App can change Settings; saves record the caller or `anonymous` | The Settings page and `/api/settings` routes | Set `ACT_SETTINGS_OPEN=0` to restore owner and `ACT_ADMINS` only access (anonymous 401, non-admin 403). |

## Settings page (T-39)

An admin (the project owner plus the users in the `Admins` field) opens **Settings** in the app to set the demo switch, the integrations above and the platform credentials, with no restart. A Domino Project or User variable with the same name always wins and the field shows "Set by project variable". Secrets are encrypted in the ACT database (key from `ACT_SECRET_KEY`, 32 or more characters; in demo mode it is optional and a generated key file beside the database is used) and are never returned by the API. Changing `ACT_SECRET_KEY` makes stored secrets unreadable: the page marks them "Re-enter this secret". Every change writes an audit event with the setting name and actor, never the value. The app runs as a single process (one uvicorn worker); the demo switch is per process.

The recon agent runs as its own process and reads the LLM provider, LLM Gateway URL and break-classifier URL and access key through the same accessor (`agents/recon_agent/config.py`, which calls `backend.settings.get_setting`) against the same ACT database, so a saved change reaches it within about 5 seconds.

The platform credential settings (AWS AgentCore, Google Agent Platform, Microsoft Agent 365, Databricks) are stored and editable now but are consumed by the import wizard (T-40 onward); nothing reads them yet. Their variable names, which win over the saved value, are: `ACT_AWS_ROLE_ARN`, `ACT_AWS_EXTERNAL_ID`, `ACT_AWS_ACCESS_KEY_ID`, `ACT_AWS_SECRET_ACCESS_KEY`, `ACT_GOOGLE_SERVICE_ACCOUNT_JSON`, `ACT_MICROSOFT_TENANT_ID`, `ACT_MICROSOFT_CLIENT_ID`, `ACT_MICROSOFT_CLIENT_SECRET`, `ACT_DATABRICKS_HOST`, `ACT_DATABRICKS_CLIENT_ID`, `ACT_DATABRICKS_CLIENT_SECRET` (all ACT_-prefixed so ambient workspace credentials such as AWS_ACCESS_KEY_ID are never picked up).

Demo mode only seeds or resets its own demo database (the default demo DB, or an `ACT_DB` file that did not exist at startup); a hand-set `ACT_DB` that already exists needs `ACT_DEMO_ALLOW_DB=1`, and a refusal shows in the banner and as `demo_guard` in `GET /api/health`.

## Running the stand-ins

```
export ACT_LLM_PROVIDER=mock
export RECON_AGENT_ID=local-recon-agent
export ACT_DOMINO_LOCAL_AGENTS=config/local_agents.json
python -m agents.recon_agent "Reconcile the GL and custodian ledgers"   # one run, prints the summary
```

Mock labels: validation records carry `judge_model_id` `mock-llm` and the evidence pack says "mock judge (not a
model) — rerun with the LLM Gateway for a model-graded record". Drift explanations in mock mode are
template text with no model (`explanation_model` absent); the console labels them "template text (no model)"
next to the explanation. Mock spans carry the resource attribute `act.llm.provider=mock`.

Classifier order: endpoint (`BREAK_CLASSIFIER_ENDPOINT_URL` + key), then `BREAK_CLASSIFIER_MODEL_PATH`, then the
MLflow registry `models:/break_classifier@champion` (best effort, short timeout), else class `unclassified` with a
"break classifier unavailable: ..." warning in the output. Breaks are always reported.

All commands and relative paths here (`config/local_agents.json`, `python -m agents.recon_agent`) assume the
repo root as the working directory.

## Validation rerun as a Domino Job

Rerun after going live (gateway, or the new Agent id) to replace a mock record:

```
python jobs/validate.py          # command of the Job; RECON_AGENT_ID must be set
```

Start it from Jobs > Run (command above, project environment), or via `POST /api/jobs/v1/jobs` with `runCommand`.
Files must be synced to the project first. With `ACT_URL` set it posts to the App; unset it writes in-process.
