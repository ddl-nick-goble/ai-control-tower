"""Condition-based waits for e2e tests (no fixed sleeps; the guard test forbids wait_for_timeout/time.sleep in test_*.py)."""


def settle(page):
    """Let layout, scroll and style transitions flush: resolves after two animation frames."""
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def round_trip(page):
    """One HTTP round trip through the page's network stack (orders after requests already issued by the page)."""
    page.evaluate("() => fetch('api/health').then(r => r.status)")


def wait_true(page, expr, timeout=3000):
    """Poll a JS expression in the page until truthy; returns whether it became truthy (never raises)."""
    try:
        page.wait_for_function(expr, timeout=timeout)
        return True
    except Exception:
        return False
