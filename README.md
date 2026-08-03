# stock-ai

AI-driven stock **screening / backtesting / trading** system for Japanese and US equities.

> ⚠️ This is a fresh, standalone project. It is **not** related to any existing
> real-money trading repository on this machine.

## Status

Built phase by phase; all ten phases are in place.

- [x] **Phase 1** — Dev environment (uv, ruff/black, pytest, pre-commit, CI)
- [x] **Phase 2** — Data acquisition (US via yfinance, SQLite, daily updates)
- [x] **Phase 3** — Screening (composable conditions, CSV/JSON/XLSX export)
- [x] **Phase 4** — Technical analysis (SMA/EMA/RSI/MACD/Bollinger/ATR/ADX/Stochastic/OBV)
- [x] **Phase 5** — Backtesting (next-open fills, daily mark-to-market, benchmark)
- [x] **Phase 6** — AI (Claude / OpenAI / Gemini, swappable + dummy)
- [x] **Phase 7** — AI scoring (100-point, configurable weights)
- [x] **Phase 8** — Notifications (Console / Discord / Telegram / LINE)
- [x] **Phase 9** — Automated trading (paper broker; IBKR skeleton, opt-in)
- [x] **Phase 10** — Streamlit dashboard

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

## Financial statement history (JP)

Snapshots answer "what are the ratios today"; a *series* answers "is revenue
growing, has the dividend been raised every year". The latter needs a
fiscal-period axis, which `statements` ingests:

```bash
uv run stock-ai statements 7203 4593   # one request returns the whole history
```

Rows are keyed by `(symbol, fiscal year, period)`, so re-running updates
restated periods instead of duplicating them.

## Screening

Filter stored securities by fundamentals and export the matches:

```bash
uv run stock-ai screen --min-roe 0.15 --max-pbr 5 --format csv --out result.csv
uv run stock-ai screen --max-per 15 --min-dividend-yield 0.03   # prints a table
```

Snapshot criteria: `--min-roe`, `--max-per`, `--max-pbr`,
`--min-dividend-yield`, `--min-market-cap`, `--max-market-cap`.

Series criteria (require `statements` to have been run first):
`--min-revenue-growth`, `--min-profit-growth`, `--min-dividend-growth`,
`--growth-years`, `--min-dividend-streak`, `--max-payout-ratio`.

All criteria combine with AND. Output formats: `csv`, `json`, `xlsx`.

```bash
# 割安成長株: 増収・増益・増配かつ割安
uv run stock-ai screen --min-revenue-growth 0.05 --min-profit-growth 0.05 \
                       --min-dividend-growth 0.001 --max-per 20

# 配当: 3年以上の連続増配で、配当性向は 30% 以下
uv run stock-ai screen --min-dividend-streak 3 --max-payout-ratio 0.30
```

`--min-dividend-growth 0` means "not cut"; pass a positive floor to require an
actual raise. A criterion that cannot be computed never passes — an unverifiable
metric is excluded rather than assumed good.

## Portfolio

Sector is what the breakdown groups by, so fetch it first — it is normalized
onto one taxonomy so JP and US holdings land in the same buckets:

```bash
uv run stock-ai profile AAPL MSFT               # US, via yfinance
uv run stock-ai profile 7203 8306 --source jquants

uv run stock-ai hold AAPL --quantity 100 --cost 120
uv run stock-ai hold 7203.T --quantity 1000 --cost 2000 --market JP
uv run stock-ai hold AAPL --quantity 0          # clears the position

uv run stock-ai portfolio --fx JPY=0.0066
```

The report gives per-position value and weight, sector and market exposure,
and realized risk (annualized volatility, max drawdown, and a Herfindahl
concentration index with its effective-position count). Positions are valued in
one base currency, since a ¥ position and a $ position cannot be weighted
against each other otherwise. A holding with no stored price is listed
separately and left out of the weights rather than counted as zero.

There is deliberately **no expected-return figure**. The usual implementation —
annualizing a trailing mean — carries enough estimation error to swamp the
signal, so the report sticks to what actually happened.

## Cross-market ranking (JP + US)

```bash
uv run stock-ai rank                       # live FX
uv run stock-ai rank --fx JPY=0.0064       # pinned rate, reproducible
uv run stock-ai rank --max-market-cap 1e9  # small caps, both markets
```

The composite score is built from unitless ratios, so it already compares
across markets. Market cap does not — it arrives in the listing market's
currency — so it is converted to `--base` (default USD) before being shown or
filtered on.

## Scoring, AI, notifications

```bash
uv run stock-ai score AAPL MSFT                 # 0-100 weighted score + factor breakdown
uv run stock-ai summarize "..." --provider dummy  # dummy|claude|openai|gemini
uv run stock-ai sentiment "..." --provider dummy
uv run stock-ai notify "buy: AAPL" --channel console  # console|discord|telegram|line
```

## Dashboard

```bash
uv sync --extra dashboard
uv run streamlit run src/stock_ai/dashboard/app.py
```

Sections: Rankings (scores), Backtest (equity curves + metrics), AI Analysis.

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
