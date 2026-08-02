# stock-ai

AI-driven stock **screening / backtesting / trading** system for Japanese and US equities.

> ⚠️ This is a fresh, standalone project. It is **not** related to any existing
> real-money trading repository on this machine.

## Status

Under active development, built phase by phase.

- [x] **Phase 1** — Dev environment (in progress)
- [ ] Phase 2 — Data acquisition (JP/US, SQLite)
- [ ] Phase 3 — Screening
- [ ] Phase 4 — Technical analysis
- [ ] Phase 5 — Backtesting
- [ ] Phase 6 — AI (Claude / OpenAI / Gemini)
- [ ] Phase 7 — AI scoring
- [ ] Phase 8 — Notifications (LINE / Discord / Telegram)
- [ ] Phase 9 — Automated trading (dummy → IBKR)
- [ ] Phase 10 — Streamlit dashboard

## Requirements

- Windows 11
- [uv](https://docs.astral.sh/uv/) (manages Python 3.13 automatically)

## Setup

```bash
uv sync                     # create .venv and install base + dev deps
uv run pre-commit install   # enable commit-time lint/format hooks
uv run stock-ai version     # verify it runs
uv run stock-ai info        # show active config (secrets masked)
```

Install phase-specific extras as you reach them:

```bash
uv sync --extra data    # pandas, numpy, httpx, yfinance
uv sync --extra db      # sqlalchemy, alembic
```

## Fetching prices

```bash
uv sync --extra data --extra db
uv run stock-ai fetch AAPL MSFT --start 2024-01-02 --end 2024-01-10
uv run stock-ai fetch AAPL MSFT   # incremental: only bars newer than what's stored
```

Prices are stored in `data/stock_ai.db` (SQLite). Re-running is idempotent —
already-stored dates are updated, not duplicated — so this is safe to schedule
daily. A symbol with no data yet is backfilled `--lookback` days (default 365).

## Development

```bash
uv run pytest                    # tests + coverage
uv run ruff check .              # lint
uv run ruff format .             # or: uv run black .
uv run pre-commit run --all-files # run every hook manually
```

## Layout

`src/stock_ai/` uses a src-layout; each subpackage is one pipeline layer
(`data`, `screening`, `technical`, `backtest`, `ai`, `broker`, ...).
See [docs/](docs/) for design notes.
