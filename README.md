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

Fundamentals (PER, PBR, ROE, revenue, net income, dividend yield, market cap):

```bash
uv run stock-ai fundamentals AAPL MSFT
```

Each run stores one snapshot per symbol per day; missing metrics are kept as
`NULL` rather than failing the whole fetch.

## Screening

Filter stored securities by fundamentals and export the matches:

```bash
uv run stock-ai screen --min-roe 0.15 --max-pbr 5 --format csv --out result.csv
uv run stock-ai screen --max-per 15 --min-dividend-yield 0.03   # prints a table
```

Available criteria: `--min-roe`, `--max-per`, `--max-pbr`,
`--min-dividend-yield`, `--min-market-cap` (combined with AND). Output formats:
`csv`, `json`, `xlsx`.

## Backtesting

```bash
uv run stock-ai fetch AAPL --start 2022-01-01 --end 2024-01-01
uv run stock-ai backtest AAPL --strategy sma --fast 20 --slow 50 --commission 0.001
```

The engine is deliberately conservative to avoid phantom profits:

- signals are filled at the **next bar's open** (no same-day-close look-ahead);
- equity is **marked to market daily** (`cash + shares * close`), so drawdown and
  Sharpe reflect open positions, not just closed trades.

Results are always shown next to a benchmark (buy-and-hold of the same symbol, or
`--benchmark SPY`) and compared on risk-adjusted terms (Sharpe, max drawdown).

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
