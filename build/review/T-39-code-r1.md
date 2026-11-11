# T-39 code review, round 1 (@a75c895): changes needed. No secret leakage found.

## Blockers
1. backend/demo.py switch_on (diff ~L177-200): `_runtime = True` is set before `db.use_path(path)` and the seed, so `demo.enabled()` is true while requests still hit the real DB. After that, a half-seeded demo DB is served. switch_off has the mirror problem (`_runtime = False` before `db.use_real()`). Fix: build and seed the demo engine off to the side, then swap db.engine and `_runtime` together under one lock; `enabled()` reads one state object.
2. backend/settings.py:549-564 `_file_key`: the loser of the O_EXCL race reads the file before the winner writes it, so `Fernet(b"")` raises ValueError. put_setting turns that into a 500; get_setting swallows it and reports the setting as unset. Fix: write the key to a 0600 temp file and os.link/os.replace it into place, or retry the read until it isn't empty. Cache the Fernet instance.
3. ST-4 is not met for BREAK_CLASSIFIER_ACCESS_KEY/BREAK_CLASSIFIER_ENDPOINT_URL or ACT_LLM_PROVIDER/LLM_GATEWAY_URL in the recon agent, which still reads os.environ: agents/recon_agent/agent.py:53,163; agents/recon_agent/llm.py:27,47,85; agents/recon_agent/act_export.py:87. Nothing consumes the platform credential settings (AWS/Google/Microsoft/Databricks), and their env names are invented (e.g. ACT_AWS_ROLE_ARN). Fix: migrate the recon agent readers (it runs in its own process, so it needs a read path to the settings), and document that the platform credentials are consumed by the import wizard (T-40+).

## Major
1. settings.py:716-734: for demo_mode=true, demo.switch_on() runs before _store. If the DB or audit write fails, the app is left in demo mode with no saved setting and no audit event, and the value write and _audit are separate transactions. Fix: write the row and the audit event in one begin(); switch demo mode only after the commit; roll back on failure.
2. settings.py:587-594 `_rows`: any exception yields {} and is cached for 2s, so a transient lock makes every secret look unset. Fix: catch only OperationalError for a missing table; otherwise keep the previous cache and log.
3. settings.py:567-579 / get_setting L617-622: if ACT_SECRET_KEY changes, existing secrets can't be decrypted, but the API still reports is_set with "changed by" while get_setting returns None. Fix: add `decryptable: false` to the API, show a "Re-enter this secret" state in the UI, and say in the warning that changing the key invalidates stored secrets.
4. demo.py switch_off: `old.dispose()` runs while in-flight requests may hold the old engine. Fix: keep the demo engine cached and reuse it on the next switch_on, or dispose after a drain.
5. Multiple uvicorn workers would make `_runtime` and db.engine per-process. Fix: confirm app.sh runs a single worker, or re-sync the switch from the persisted setting in the `_rows()` poll.
6. is_admin L630-635: with no DOMINO_PROJECT_OWNER and no admins, nobody can open Settings. Fix: log a warning and show a clear message.

## Minor
- Edits outside the allowed list: frontend/src/api.js, main.jsx, settings.css, backend/routes/demo.py (changes how /api/demo/* is mounted), backend/drift.py, governance.py, adapters/domino.py. These are accepted if they are env-reader migrations or route wiring; explain the routes/demo.py change in your report.
- main.py health: demo_guard is hidden when demo is off. Check that nothing relied on it.
- Settings.jsx: Clear uses window.confirm; use the designed ConfirmDialog for it too. ConfirmDialog has an unused `confirm` prop.
- _audit detail: add the previous and new source (app or cleared).
- Note in the settings.py docstring that ACT_SECRET_KEY is read straight from os.environ on purpose.
