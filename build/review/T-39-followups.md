# T-39 follow-ups (final code review @138abeb: approve with follow-ups)
1. settings.py: DemoGuardError from switch_off(own=1) isn't caught on the delete_setting path or put_setting(v=="false"), nor on the _store-failure rollback. The stored setting and the live mode disagree until a restart, with a 500. Catch it and return 409/503 "try again", and keep the stored value and live mode consistent.
2. Two simultaneous demo toggles stall each other for 15s, then 409. Don't count requests waiting on _switch_mutex, or retry.
3. /api/demo/run holds _rt_lock for the whole recon run (long with a live gateway).
4. Clear dialog: drop the sr-only "(confirm)" and update test_t39_settings.py:204 to the "Clear key" name; non-secret fields say "Clear value".
5. e2e env-strip list (fixed by QA in 3879718); the settings.py:384-386 comment should mention the banner.
