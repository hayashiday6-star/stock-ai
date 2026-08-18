"""Shared test setup.

Fixes the console width before anything imports the CLI. Several tests read
values out of Rich-rendered tables, and Rich wraps to the terminal it finds:
the same correct code passes on a wide developer console and fails in CI, which
is a failure that says nothing about the code and costs a round trip to
diagnose. Pinning the width makes those assertions mean what they look like
they mean.
"""

from __future__ import annotations

import os

# Set before the first import of stock_ai.cli, which builds its Console at
# module scope - after that the width is already decided.
# Assigned, not setdefault: an inherited COLUMNS from the calling shell is
# exactly the variability this exists to remove.
os.environ["COLUMNS"] = "200"
os.environ["LINES"] = "50"
