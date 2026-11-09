# Integration switches and stand-ins

ACT runs end to end today with stand-ins for four live pieces: the LLM Gateway, a deployed Domino Agent, a
service-account identity and a second approver. Each is swapped by environment variable only, and each stand-in
labels itself honestly in the UI and the evidence pack. Set variables as Domino project or user variables
(secrets as secret variables), then restart the App or rerun the Job.

| Switch | Current state | What is mocked | Go live |
|---|---|---|---|
| `ACT_LLM_PROVIDER` | `mock` for the demo (unset = `gateway`) | All LLM calls: recon agent tool loop, validation judge, drift explanation. Responses carry model `mock-llm`. | Unset it, or set `gateway`, and set `LLM_GATEWAY_URL` (next row). |
| `LLM_GATEWAY_URL` | Not set | Needed only with the `gateway` provider | 1. Open the gateway App once in a browser and accept consent: https://apps.se-demo.domino.tech/apps/llmgateway/ . 2. Set `LLM_GATEWAY_URL` to that URL. 3. Confirm `GET <url>/v1/models` (with the in-run bearer) lists the `sonnet` and `haiku` aliases, or change the aliases in `agents/recon_agent/llm.py` callers. 4. Unset `ACT_LLM_PROVIDER`. Outside Domino also set `LLM_GATEWAY_TOKEN`. |
| `ACT_DOMINO_LOCAL_AGENTS` | Points at `config/local_agents.json`: lists `recon-agent` (native id `local-recon-agent`, hosting "workspace (not deployed)") | The agent is not a deployed Domino Agent; it runs in a workspace or Job. Spans and the classifier call are real. | Deploy `agents/recon_agent` as a Domino Agent. Set `RECON_AGENT_ID` to the Domino app id of that Agent, unset `ACT_DOMINO_LOCAL_AGENTS` (or delete the entry), rerun intake. The deployed Agent then appears from the Apps API. Its ACT id changes, so rerun validation (below) for the new id. |
| `ACT_LOCAL_AGENT_OWNER`, `ACT_LOCAL_AGENT_PRINCIPAL` | Owner defaults to `nick_goble` | Owner of the local entry | Set to the real owner; the principal defaults to the owner. |
| `ACT_SERVICE_ACCOUNTS` | Not set: the agent's identity is reported as a workspace user, `distinct: false` (honest) | The distinct service-account identity | Create a Domino service account for the agent, run the Agent or Job as it, set `ACT_SERVICE_ACCOUNTS` to a comma-separated list of those principals. Intake then records `domino_service_account`, `distinct: true`. The local entry's principal must be in the list. |
| Second approver | Approval requires a different Domino user than the one who registered the agent (HTTP 403 otherwise). With one user, approval cannot be completed. | Nothing is mocked; the control is real | Sign in to the ACT App as a second Domino user (any user with App access) and use Approve on the agent. The registrant is recorded at intake or registration; same user id or username is refused and audited. |
| `BREAK_CLASSIFIER_ENDPOINT_URL`, `BREAK_CLASSIFIER_ACCESS_KEY` | Unset: the local model (`models/break_classifier`) is used (span attribute `act.classifier.source=local`) | The registry endpoint | Set the URL of the sync endpoint `break-classifier` (serves registry model `break_classifier`, champion v6) and the endpoint access key (secret). Spans then show source `endpoint`. `BREAK_CLASSIFIER_REGISTRY_VERSION` (default `6`) is the version recorded in the dependency list. |
| `ACT_URL` | Unset for in-process runs | Job-to-App HTTP | Set to the ACT App URL so `jobs/validate.py` posts with the in-run bearer. Unset: the job writes the record straight to the ACT database (same validation rules as the route). |
| `ACT_TRACES_URL` | Set per run | None | OTLP endpoint of ACT (`<app>/v1/traces`); unset means no export. |
| `ACT_EXTERNAL_ALLOW_HOSTS` | Not set: external agent-card fetches are refused | Hosts allowed for external card fetch | Comma-separated hostnames to permit. Private, link-local and port 8899 stay refused. |
| `ACT_TRUSTED_JWKS_PATH` | Not set: signed cards cannot be verified | Trusted signer keys | Path to a JWK or `{"keys": [...]}` file. Cards signed by those keys show as verified. |
| `ACT_POLICY_AGENTIC_ID`, `ACT_POLICY_MODEL_ID` | Not set | Governance policy ids | Set to the Domino Governance policy ids for agentic and model bundles. |

## Running the stand-ins

```
export ACT_LLM_PROVIDER=mock
export RECON_AGENT_ID=local-recon-agent
export ACT_DOMINO_LOCAL_AGENTS=config/local_agents.json
python -m agents.recon_agent "Reconcile the GL and custodian ledgers"   # one run, prints the summary
```

Mock labels: validation records carry `judge_model_id` `mock-llm` and the evidence pack says "mock judge (not a
model) — rerun with the LLM Gateway for a model-graded record". Drift explanations are stored with
`explanation_model` `mock-llm` and the pack's monitoring section marks them as mock template text.

## Validation rerun as a Domino Job

Rerun after going live (gateway, or the new Agent id) to replace a mock record:

```
python jobs/validate.py          # command of the Job; RECON_AGENT_ID must be set
```

Start it from Jobs > Run (command above, project environment), or via `POST /api/jobs/v1/jobs` with `runCommand`.
Files must be synced to the project first. With `ACT_URL` set it posts to the App; unset it writes in-process.
