"""Guard: no fixed waits in e2e tests. Wait for the condition instead (expect(...), wait_for_function, _waits.py).

The only sleeps allowed live in the helper modules (_server.py wait_until/wait_port, startup polling), never in test_*.py.
"""
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
FIXED_WAIT = re.compile(r"wait_for_timeout\s*\(|time\s*\.\s*sleep\s*\(|\bsleep\s*\(|asyncio\s*\.\s*sleep")


def test_no_fixed_waits_in_e2e_tests():
    bad = []
    for f in sorted(HERE.glob("test_*.py")):
        if f.name == pathlib.Path(__file__).name:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if FIXED_WAIT.search(line.split("#")[0]):
                bad.append(f"{f.name}:{n}: {line.strip()}")
    assert not bad, "fixed waits found (use a condition-based wait):\n" + "\n".join(bad)


def test_guard_detects_a_sleep():
    assert FIXED_WAIT.search("    page.wait_for_timeout(300)")
    assert FIXED_WAIT.search("    time.sleep(1)")
