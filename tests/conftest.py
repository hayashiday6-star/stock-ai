"""Shared test setup.

Isolates the suite from the developer's ``.env``, and fixes the console width
before anything imports the CLI. Several tests read
values out of Rich-rendered tables, and Rich wraps to the terminal it finds:
the same correct code passes on a wide developer console and fails in CI, which
is a failure that says nothing about the code and costs a round trip to
diagnose. Pinning the width makes those assertions mean what they look like
they mean.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from stock_ai.config.settings import Settings, get_settings

# Set before the first import of stock_ai.cli, which builds its Console at
# module scope - after that the width is already decided.
# Assigned, not setdefault: an inherited COLUMNS from the calling shell is
# exactly the variability this exists to remove.
os.environ["COLUMNS"] = "200"
os.environ["LINES"] = "50"


@pytest.fixture(autouse=True)
def _isolate_settings_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop the suite reading whatever happens to be in the project's ``.env``.

    ``Settings`` loads that file by design, which is right in the application
    and wrong in a test: the same code then passes in CI, where no ``.env``
    exists, and fails on the machine of anyone who has configured the tool.
    Four tests failed exactly that way - two on ``AI_PROVIDER=claude`` and two
    on ``MOOMOO_TRD_ENV=REAL``, both values this project tells people to set.

    One of them did more than fail. ``test_summarize_cli_dummy`` expects the
    dummy provider; with a real key in ``.env`` it reached the live API and
    billed a call. A test suite that spends money because of a config file is
    not a test suite that can be run freely, which is most of what a test suite
    is for.

    Tests that need a setting still set it: an explicit ``monkeypatch.setenv``
    goes through the environment, which outranks the file and is unaffected.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
