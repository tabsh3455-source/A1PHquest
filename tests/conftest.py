import asyncio
import sys
import warnings

import pytest

if sys.platform.startswith("win") and "WindowsSelectorEventLoopPolicy" in dir(asyncio):
    # The project ships for Linux VPS, but our local review/test environment can be
    # Windows. Using the selector policy keeps repeated asyncio.run()/TestClient
    # startup stable during the test suite.
    warnings.filterwarnings(
        "ignore",
        message=".*WindowsSelectorEventLoopPolicy.*",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=".*set_event_loop_policy.*",
        category=DeprecationWarning,
    )
    asyncio.set_event_loop_policy(getattr(asyncio, "WindowsSelectorEventLoopPolicy")())


@pytest.fixture(scope="session")
def async_runner():
    """
    Reuse one asyncio runner for sync-style tests that need to await coroutines.

    Repeated asyncio.run() calls can exhaust Windows socketpair resources under
    Python 3.14 during a large suite, so we keep a single session runner here.
    """
    with asyncio.Runner() as runner:
        yield runner.run
