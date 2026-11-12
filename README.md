# ACT: Agent Control Tower

ACT is an evidence layer for AI agents in a bank. It inventories agents from several platforms, tiers them by
consequence, records what they actually did (hash-chained events from OpenTelemetry traces), detects drift from
each agent's permitted-action manifest, mirrors approvals into Domino Governance, and produces a dated evidence
pack (Markdown and PDF). **It does not enforce anything**: it never blocks an agent or a call. It produces the
records that a reviewer, an auditor or a regulator can check.

## Quick start

```
python jobs/seed_demo.py        # cold start to the demo state (add --governance for Domino Governance bundles)
bash app.sh                     # builds the frontend if needed, serves API + console (9888 in a workspace)
```

`seed_demo.py` runs intake (12 agents), runs the recon agent once, ingests the weekend-drift run, records a
validation (5 repetitions) and prints a summary. It is idempotent and never approves an agent. Then follow
[docs/DEMO.md](docs/DEMO.md) for the ten-minute walk, including the fixture card server and the environment
variables for registering an external agent.

`app.sh` rebuilds `frontend/dist` with npm when sources are newer. If `frontend/dist` exists and npm is not
available it serves the existing build without failing. **A published Domino App should ship a prebuilt
`frontend/dist` or run in an environment with node**; `frontend/dist` is not committed to git.

## Documentation

- [docs/architecture.md](docs/architecture.md): integration map, trace-path sequence and data model (Mermaid).
- [docs/DEMO.md](docs/DEMO.md): the ten-minute demo script, cold-start commands and what is mocked. One-command demo: `ACT_DEMO_MODE=1 bash app.sh` (demo mode, see the first section of DEMO.md and the `ACT_DEMO_MODE` row in [docs/INTEGRATION.md](docs/INTEGRATION.md)).
- [docs/INTEGRATION.md](docs/INTEGRATION.md): every switch that moves a stand-in to a live piece.
- [docs/governance-api.md](docs/governance-api.md), [docs/BORROWED.md](docs/BORROWED.md): Governance API notes and borrowed schemas.

## Live versus mock

| Piece | Demo state | Switch |
|---|---|---|
| Domino Apps and Agents intake, Audit Trail, MLflow, Governance API | Live | Always on inside Domino |
| AWS, Google, Microsoft, Databricks intake | Replay fixtures shaped like the real APIs; live calls written but untested | `ACT_LIVE_<PLATFORM>=1` |
| LLM (recon agent loop, validation judge, drift explanations) | Mock provider; explanations are template text | Unset `ACT_LLM_PROVIDER`, set `LLM_GATEWAY_URL` |
| recon-agent hosting | Local entry, runs in the workspace (not deployed) | `ACT_DOMINO_LOCAL_AGENTS`, deploy as a Domino Agent |
| `break_classifier` | Live registry model (champion), endpoint when its key is set | `BREAK_CLASSIFIER_ENDPOINT_URL`, `BREAK_CLASSIFIER_ACCESS_KEY` |
| Service-account identity | Reported as a workspace user, `distinct: false` | `ACT_SERVICE_ACCOUNTS` |
| Second approver | Control is real; the demo uses a simulated second user | Sign in as a second Domino user |
| External agent cards | Fixture card served from localhost | `ACT_EXTERNAL_ALLOW_HOSTS`, `ACT_TRUSTED_JWKS_PATH` |

Details and go-live steps are in [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Tests

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q -p no:cacheprovider --ignore=tests/e2e
PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright python3 -m pytest tests/e2e -q -p no:cacheprovider -p no:xdist   # regression
PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright python3 -m pytest tests/e2e -q -p no:cacheprovider -p no:xdist -m smoke   # ~15 tests
```

Browser suite (`tests/e2e`, serial on a 1-core machine; needs Playwright browsers):

- `pytest.ini` runs `-m "not capture"` by default: capture tests (`@pytest.mark.capture`, screenshots only) are not
  regression tests. Run them explicitly with `-m capture`.
- `-m smoke` selects about 15 tests (shell, nav, Fleet, Agent, Run, Settings load) for a quick check after a change.
- Apps start on free OS-assigned ports (never a fixed port), once per distinct environment per run, and are shared by
  the modules that need that environment (the seed runs once; each module starts from the seeded DB again). See
  `tests/e2e/conftest.py` (`apps(...)`) and `tests/e2e/_server.py`.
- Screenshots: regression runs write a viewport PNG only for a failed test (`<test>-FAILED.png`). Set `QA_SHOTS=1` to
  also write the `shot("then")` PNGs; `QA_RUN_DIR=<dir>` chooses the folder (default `test-results/`).
  `shot(step, full_page=True)` takes a full-page one.
- `E2E_REQUIRE_PROXY=1` fails the run if the Domino proxy URL (`APP_URL`) is not reachable; without it the suite prints
  one `[e2e] proxy URL ... falling back to http://localhost:PORT/` line and uses localhost.
- Parallel runs: `pytest-xdist` is installed (environment image) but runs here use `-p no:xdist`. The app registry is not
  xdist-safe yet, so parallel runs are not supported; they would also need a Medium/Large hardware tier.
- No `wait_for_timeout` / `time.sleep` in `test_*.py` (a guard test fails if one is added); use `expect(...)`, the helpers
  in `tests/e2e/_waits.py`, or `_server.wait_until`.

### Capture CLI (design review)

`tests/e2e/capture.py` starts the app on a free port, takes full-page PNGs of the routes you name, and stops the app:

```
python3 tests/e2e/capture.py --out DIR --widths 1440,1024 --demo --route '#/agents/{treasury_id}' --route '#/settings' \
    [--delay-api 'api/settings:3000'] [--fail-api 'api/agents/*:500'] [--as owner|admin|NAME] [--no-auth]
```

`--delay-api` holds matching API responses (loading states); `--fail-api` answers with an HTTP status (error states).
`python3 tests/e2e/capture.py -h` lists all options.

## App Preview and published Apps

App Preview and published Apps run `app.sh` in an environment without Node, so they serve the committed `frontend/dist`. After frontend changes run `npm run build` and commit `frontend/dist` (after the source changes).
