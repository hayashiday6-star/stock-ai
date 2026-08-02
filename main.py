"""Executable entry point so the app can be launched with ``python main.py``.

Prefer ``uv run stock-ai <command>`` in day-to-day use; this thin wrapper
exists for environments that invoke the module file directly.
"""

from stock_ai.cli import main

if __name__ == "__main__":
    main()
