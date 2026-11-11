# T-39 code re-review (@a1531d3): changes needed. All round-1 items fixed or mostly fixed; no secret leakage.

## Blocker
1. backend/settings.py:99: there's no fallback when os.link fails with anything other than FileExistsError. On a filesystem without hard links (EPERM/ENOTSUP/EXDEV), the OSError propagates, so put_setting, entry() (every GET /api/settings) and get_setting for secrets all return 500 and ST-2 fails. Fix: on OSError from os.link, fall back to os.open(path, O_CREAT|O_EXCL, 0o600) and write the key there; the existing empty-file retry covers half-written reads. Test it on /mnt/data.

## Major
1. settings.py:46-59 _DEFS: the renamed env names are the real SDK names (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AZURE_CLIENT_SECRET, DATABRICKS_HOST, AZURE_TENANT_ID, ...). Ambient workspace credentials would lock the field, be passed silently to the import wizard, and echo non-secret ids. Fix: ACT_-prefixed names (ACT_AWS_ACCESS_KEY_ID ...); update docs/INTEGRATION.md.
2. main.py:49-54 demo_switch_gate: the check-then-wait is racy (a request that passed switching() just before the flip still runs against the swapped or half-seeded DB), and each waiter holds an anyio worker thread (about 40) for the whole in-process seed, which has no timeout. Fix: gate on lock-protected state, wait in async (`while demo.switching(): await asyncio.sleep(0.05)`), exempt /api/health, and return 503 + Retry-After after about 30s.
3. agents/recon_agent/config.py:78-84: the first get_setting opens db.real_engine and SQLite creates a stray ./act.db when ACT_DB or DOMINO_PROJECT_NAME is missing. Fix: resolve the same path as the app, or skip the DB read when the file doesn't exist. The bare except hides failures; log once at warning level.

## Minor
- settings.py:134: _file_key() reads the file on every key_state(); cache by mtime.
- settings.py:92-106: clean up stale .act-secret.key.*.tmp files.
- settings.py:346 apply_demo_setting: main.py:32 catches only DemoGuardError; catch Exception, log, and set demo_guard so a seed failure doesn't abort startup.
- Docs: an in-app demo switch isn't visible to a separate recon agent process.
- db.py:138 engine_for mutates _engines without a lock; add one or document that callers hold _rt_lock.
