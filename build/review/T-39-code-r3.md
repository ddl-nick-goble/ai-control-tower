# T-39 code re-review r3 (@694b69e): approve with follow-ups, plus one merge-blocking item

## Blocker (round 3)
1. backend/demo.py:295-307 reset() can wipe the real DB. The marker check (L299) uses the demo engine, and drop_all(db.engine) (L303) re-reads db.engine. If an admin switches demo off in between, every table except app_settings is dropped from the real DB. The /api/demo/reset _demo_only check runs only at request start, and reset() doesn't take _rt_lock. Fix: wrap all of reset() in `with _rt_lock:`, re-check enabled() and the marker inside the lock, capture `eng = db.engine` once and use eng for the marker check, drop_all, init_db and the seed. Apply the same capture-once pattern to run_recon_once and the other demo routes (low risk).

## Follow-up, should be done in round 3 since it's cheap: in-flight drain for the demo switch
Use a pure ASGI middleware, not BaseHTTPMiddleware. In demo.py: a Condition on _state_lock with an _inflight counter; enter() atomically checks _switching and increments; leave() decrements and notifies; _begin_switch(own) sets _switching and wait_for(_inflight <= own, timeout=15), otherwise unsets it and raises DemoGuardError("requests still running; try again"). The gate calls enter() (polling with asyncio.sleep while False), runs the app, and calls leave() in finally. put_setting/delete_setting pass own=1; startup passes 0. Exempt /api/health.

## Minor
1. tests/e2e/test_t39_settings.py:51-53 still strips the old env names, not the ACT_ ones (QA fixes this).
2. settings.py:384-386: the comment should also mention the banner.
3. _write_new_key runs the stale-tmp glob on every key creation; acceptable.
