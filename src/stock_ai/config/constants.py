"""Project-wide constants and filesystem paths.

Everything path-related is derived from :data:`PROJECT_ROOT` so nothing is
hard-coded to an absolute location. ``PROJECT_ROOT`` is resolved relative to
this file, which lives at ``<root>/src/stock_ai/config/constants.py``.
"""

from __future__ import annotations

from pathlib import Path

# <root>/src/stock_ai/config/constants.py -> parents[3] == <root>
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]

SRC_DIR: Path = PROJECT_ROOT / "src"
DATA_DIR: Path = PROJECT_ROOT / "data"
LOG_DIR: Path = PROJECT_ROOT / "logs"
DOCS_DIR: Path = PROJECT_ROOT / "docs"

ENV_FILE: Path = PROJECT_ROOT / ".env"
LOG_FILE: Path = LOG_DIR / "stock_ai.log"

APP_NAME: str = "stock-ai"

# moomoo's API is reached through OpenD, a gateway that runs on this machine
# and holds a logged-in brokerage session. The host is loopback on purpose:
# binding it anywhere reachable publishes that session to the network.
OPEND_HOST: str = "127.0.0.1"
OPEND_PORT: int = 11111
