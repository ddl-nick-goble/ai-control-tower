# Ten-minute demo

The walk: fleet, recon-agent, approve as a second user, treasury-sweep-agent, run timeline, evidence pack, PDF,
register an external agent, passport. Read "What is mocked or local" first; say it out loud at the start of the demo.

## What is mocked or local

| Piece | State in this demo | Where to read more |
|---|---|---|
| LLM | The `mock` provider (`ACT_LLM_PROVIDER=mock`, the seed default when `LLM_GATEWAY_URL` is unset). The recon agent loop and the validation judge are offline stand-ins (model `mock-llm`). The drift explanation is a template, labelled "template text (no model)". | docs/INTEGRATION.md, rows `ACT_LLM_PROVIDER`, `LLM_GATEWAY_URL` |
| recon-agent hosting | A local entry from `config/local_agents.json` (hosting "workspace (not deployed)"). It runs in a workspace or Job, not as a deployed Domino Agent. Its spans and the `break_classifier` registry call are real. | docs/INTEGRATION.md, row `ACT_DOMINO_LOCAL_AGENTS` |
| Second approver | One person drives the demo, and the registrant may not approve (HTTP 403, audited). The approval is made by a simulated second user, `demo-approver (simulated second user)`, through `python jobs/seed_demo.py --approve recon-agent`. It stands in for a real second Domino user. The control itself is real. | docs/INTEGRATION.md, row "Second approver" |
| Hyperscaler sources | AWS, Google, Microsoft and Databricks agents come from replay fixtures shaped like the real APIs. Live calls are gated behind `ACT_LIVE_<PLATFORM>=1` and untested here. | README, live-versus-mock table |
| Service-account identity | recon-agent is reported as a workspace user (`distinct: false`) unless `ACT_SERVICE_ACCOUNTS` is set. | docs/INTEGRATION.md |

## Cold start

From the repo root, in a Domino workspace (or locally with Python 3.12 and node):

```
# 1. Seed the database: 12 agents, one recon-agent run, the weekend-drift run, one validation record.
python jobs/seed_demo.py
#    add --governance to also create the Domino Governance bundles for recon-agent and treasury-sweep-agent

# 2. Serve the fixture agent card (leave running, second terminal)
python fixtures/external/serve.py --port 8765

# 3. Start the app (port 9888 in a workspace; it prints its access URL)
export ACT_EXTERNAL_ALLOW_HOSTS=127.0.0.1,localhost
export ACT_TRUSTED_JWKS_PATH=fixtures/external/signing-key.pub.jwk.json
export ACT_LLM_PROVIDER=mock
export ACT_DOMINO_LOCAL_AGENTS=config/local_agents.json
bash app.sh
```

`ACT_DB` selects the database file for both the seed and the app (default: under `/mnt/data/<project>/act/` in a
Domino workspace, otherwise `./act.db`). Use the same value for both. To use the signed card, start the fixture
server with `--signed`; the passport then shows the identity banner "signed and verified".

## The walk (about ten minutes)

1. **Fleet (1 min).** Open the app. The Fleet table lists 12 agents across five sources: 3 AWS AgentCore, 3 Google,
   3 Microsoft Agent 365, 2 Databricks and 1 Domino (recon-agent). A thirteenth row, the external agent, appears
   after step 8. Use the Source filter to show one platform. Point out tier and status columns.
2. **recon-agent (2 min).** Click `recon-agent`. Show, panel by panel: Tier and criteria (tier 2), Identity binding,
   Permitted-action manifest (three tools: fetch_ledger, compare_entries, propose_adjustment), Dependencies
   (`break_classifier`, the champion version, v6 in the registry), Governance bundle (two policies on one bundle
   when the seed ran with `--governance`) and Runs. The validation record (mean, p5, p95, pass rate over 5
   repetitions) has no panel of its own: show it in recon-agent's evidence pack, or at
   `/api/agents/<id>/validation`. Say that the validation judge is the mock judge here.
3. **Approve as a second user (1 min).** In the Approvals panel click **Approve** as the signed-in user: it is
   refused with a message that the user who registered the agent cannot approve it. That is the control working.
   Then, in a terminal:
   `python jobs/seed_demo.py --approve recon-agent`
   Reload the agent page: status approved, an approval by `demo-approver-simulated`, and an expiry (tier 2: 180 days).
   This simulated second user stands in for a real second Domino user (docs/INTEGRATION.md).
4. **treasury-sweep-agent (1 min).** Back to Fleet, open `treasury-sweep-agent` (AWS AgentCore fixture). It is
   tier 1. Under Findings there is one open `scope_drift` finding from Saturday 2026-11-07 02:14 with a one-line
   explanation (template text, labelled as such).
5. **Run timeline (2 min).** In the finding click **View run timeline**. Two events are highlighted: `transfer_funds`
   with an amount of 4,750,000 against the manifest limit of 250,000, and `list_credentials`, which is not in the
   manifest.
6. **Evidence pack (1 min).** Back to treasury-sweep-agent. In the Evidence pack panel click **Generate evidence
   pack**. The preview opens in OCC 2026-54 order; show the finding in the monitoring section.
7. **PDF (30 s).** Click **Download PDF** (and **Download Markdown** if asked). Open the PDF and show the same finding.
8. **Register an external agent (1 min).** Go to Fleet and click **Register external agent**. Enter the card URL
   `http://localhost:8765/.well-known/agent-card.json`, set the consequence criteria (for example Financial exposure 5,
   Autonomy 4) and data scopes, and click **Register**.
9. **Passport (30 s).** The passport shows the card fields (name, skills, endpoints), the tier computed from the
   criteria and the identity banner (unsigned unless the signed card is served). Click **Open the agent page**;
   the fleet now has 13 rows and the new one shows its card host.

## Resetting

Delete the database file and rerun `python jobs/seed_demo.py`. The seed is idempotent: running it twice changes
nothing that already exists, and it never approves an agent.

## If something looks different

- Fleet shows fewer than 12 rows: check `python jobs/seed_demo.py` printed no `intake_errors`.
- Register returns 422 for a loopback URL: set `ACT_EXTERNAL_ALLOW_HOSTS=127.0.0.1,localhost` in the app environment
  and restart.
- Signed card not verified: set `ACT_TRUSTED_JWKS_PATH` as above.
