import os
import pathlib

import pytest


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture
def shot(page, request):
    run_dir = pathlib.Path(os.environ.get("QA_RUN_DIR", "test-results"))
    run_dir.mkdir(parents=True, exist_ok=True)

    def _shot(step: str) -> str:
        path = run_dir / f"{request.node.originalname}-{step}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    return _shot
