"""Guard: no fixed waits in e2e tests. Wait for the condition instead (expect(...), wait_for_function, _waits.py).

The only sleeps allowed live in the helper modules (_server.py wait_until/wait_port, startup polling), never in test_*.py.
"""
import io
import pathlib
import re
import tokenize

HERE = pathlib.Path(__file__).resolve().parent
FIXED_WAIT = re.compile(r"wait_for_timeout\s*\(|time\s*\.\s*sleep\s*\(|\bsleep\s*\(|asyncio\s*\.\s*sleep")


def _code_lines(src):
    """(line number, text) per source line with comments removed (tokenize-based, so a '#' inside a string is kept)."""
    lines = src.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            r, c = tok.start
            lines[r - 1] = lines[r - 1][:c]
    return list(enumerate(lines, 1))


def test_no_fixed_waits_in_e2e_tests():
    bad = []
    for f in sorted(HERE.glob("test_*.py")):
        if f.name == pathlib.Path(__file__).name:
            continue
        for n, line in _code_lines(f.read_text()):
            if FIXED_WAIT.search(line):
                bad.append(f"{f.name}:{n}: {line.strip()}")
    assert not bad, "fixed waits found (use a condition-based wait):\n" + "\n".join(bad)


def test_guard_detects_a_sleep():
    assert FIXED_WAIT.search("    page.wait_for_timeout(300)")
    assert FIXED_WAIT.search("    time.sleep(1)")


def test_guard_sees_sleep_after_hash_in_string():
    src = "x = '#'; time.sleep(1)  # note\n"
    assert any(FIXED_WAIT.search(t) for _, t in _code_lines(src))
    assert not any(FIXED_WAIT.search(t) for _, t in _code_lines("x = 1  # time.sleep(1)\n"))
