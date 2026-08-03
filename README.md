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

> **PowerShell users:** every command below is a single line — run them from the
> project folder. The examples use no shell-specific syntax, but note that
> Windows PowerShell 5.1 (the Windows 11 default) does not support `&&`; run
> chained commands on separate lines, or use PowerShell 7.

## Setup

The numbered scripts in `scripts\` automate the whole first-run sequence. Run
them in order from the project folder:

```powershell
.\scripts\1-setup.ps1                            # install deps, create .env, list missing keys
.\scripts\2-verify.ps1                           # check the integrations, save a report
.\scripts\3-load-data.ps1 -Segment growth -Limit 20   # small trial
.\scripts\3-load-data.ps1 -Segment prime         # the real load
.\scripts\4-daily.ps1 -Register -At 18:00 -Provider claude -Channel discord
```

`2-verify.ps1` exists because three data sources fail *silently* — with zero
rows, not an error. It runs each one and writes `verify-output.txt`; paste that
file when asking for help. `3-load-data.ps1` is safe to interrupt: re-running
skips whatever is already current. `4-daily.ps1 -Register` creates a Windows
scheduled task (needs an elevated PowerShell) that catches up after the machine
has been asleep, which the blocking `daily --at` mode cannot do.

Or do it by hand:

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

## Loading a whole universe (JP)

Everything above takes symbols as input. This is where the list comes from:

```bash
uv run stock-ai universe --segment prime          # ~1,600 names, one request
uv run stock-ai universe --segment growth --limit 20   # trial run first

uv run stock-ai bulk-fetch --what prices --segment prime --lookback 1500
uv run stock-ai bulk-fetch --what statements --segment stored
```

`universe` stores each listing's code, name, and sector. `bulk-fetch` then
backfills prices or statements across them.

**Safe to interrupt.** A symbol whose data is already current is skipped
without a request, so re-running after a Ctrl-C or a dropped connection costs
only the remainder — and re-running after failures retries just those. One
symbol failing never ends the run.

Budget roughly `symbols × --throttle` seconds plus network time; the default
0.2s pause is there because a rate-limited free-tier key costs more time than
the pause does. Start with `--segment growth --limit 20` to confirm the
pipeline works before committing to Prime.

ETFs, REITs, and index products are excluded — they share the code format with
equities but have no revenue to grow or sector to group by, and letting them in
poisons every screen built on the universe.

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
uv run stock-ai screen --min-revenue-growth 0.05 --min-profit-growth 0.05 --min-dividend-growth 0.001 --max-per 20

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

### Multi-bagger candidates

```bash
uv run stock-ai statements 4593 7203              # the preset reads the series
uv run stock-ai rank --preset tenbagger --max-market-cap 2e9
```

`--preset tenbagger` swaps the default ratio factors for sustained revenue
CAGR, latest-year revenue and profit growth, retained earnings, and smallness
(converted, so a JP small cap is not mistaken for a mega cap). Momentum is left
out on purpose — it measures what the market has already paid for.

Treat the output as a shortlist to research, not a prediction. No weighting of
trailing fundamentals picks future multi-baggers reliably — check it with
`factor-test` below before trusting the ranking.

## Does the score actually work?

```bash
uv run stock-ai factor-test 2024-06-28 --preset tenbagger --horizon 252
```

Ranks the stored universe using **only data available on the formation date**
(prices truncated there, statements filtered on their disclosure date), holds
the top bucket for the horizon, and compares against the equal-weight universe:

```
bucket   n     mean   median  hit rate  vs universe
top     12   +8.99%   +9.15%       92%      +7.97%
mid1    12   +3.82%   +4.86%       58%      +2.80%
bottom  12   -9.75%  -11.50%       25%     -10.77%
Top-bottom spread t = +4.95 (clears 2σ).
```

Read the **t-statistic before the excess return**. On a universe of a few dozen
names, sampling noise alone routinely hands the top bucket a several-percent
"edge" — in testing, a universe with returns drawn purely at random still
produced +5.4% for the top bucket, at t = +1.39. Anything inside 2σ is not
distinguishable from chance, and the report says so.

Two limits it cannot remove: the universe is whatever is in the local database,
so delisted names are missing (survivorship bias), and a single formation date
is one draw. This can falsify a score; it cannot prove one works.

The composite score is built from unitless ratios, so it already compares
across markets. Market cap does not — it arrives in the listing market's
currency — so it is converted to `--base` (default USD) before being shown or
filtered on.

## Ask in plain language

```bash
uv run stock-ai ask "PER15以下でROE20%以上の半導体株" --provider claude
uv run stock-ai ask "連続増配5年以上の日本株" --explain-only   # check the reading first
```

The model never writes a query and never sees the database. It fills in a fixed
JSON schema of screening criteria, which is validated and turned into the same
condition tree the `screen` flags build — so an unsupported or hallucinated
field is refused rather than executed. The interpretation is always printed
first, so you can see what the question was understood to mean before trusting
the tickers:

```
Understood as: (ROE >= 0.2 AND PER <= 15.0) AND sector in [Technology]
```

Sector questions need `profile`, and growth or dividend-streak questions need
`statements`. Percentages are normalized on the way in, since a model asked for
"ROE 20%" answers `20` about as often as `0.2`.

## Watchlist monitoring

```bash
uv run stock-ai watch 4593.T --note "ヘリオス: 再生医療" --market JP
uv run stock-ai watch AAPL --importance high     # quieter name, high only
uv run stock-ai watch                            # list
uv run stock-ai watch AAPL --remove

uv run stock-ai monitor --provider claude --channel discord
```

Each new disclosure is rated (high / medium / low) and summarized by the AI
provider; anything at or above a name's threshold becomes an alert. Reported
items are recorded, so a daily run does not re-deliver the same news, and an
item judged routine is not re-classified (or re-billed) either.

If the AI provider itself fails, those items are deliberately **not** recorded —
they stay unseen and are retried, so a few minutes of downtime cannot bury a
filing permanently. The count is reported so an incomplete pass is visible.

### Disclosure feeds

`--source` picks where disclosures come from:

| feed | covers | needs |
|---|---|---|
| `edinet` | JP statutory filings (有報・四半期・臨時報告書…) | `EDINET_API_KEY` |
| `news` | yfinance headlines — decent for US large caps, thin elsewhere | — |
| `all` (default) | both, de-duplicated | — |

EDINET is indexed by date, not by company, so a pass costs one request per day
of `--lookback-days` regardless of how many names are watched.

> **TDnet (適時開示) is not covered.** It has no public API, so the only route
> is scraping an unofficial HTML endpoint — not something to ship unverified.
> If you have a third-party TDnet feed, implement `fetch` on `DisclosureSource`
> or pass a callable to `ir.sources.from_callable`; nothing else changes.

> **The EDINET adapter is written to the published v2 spec but has not been run
> against the live API** (the environment it was built in has no outbound access
> to it). Check the first real run: a drifted field name would show up as zero
> disclosures rather than as an error.

## Daily automation

```bash
uv run stock-ai daily --once AAPL MSFT --provider claude --channel discord
uv run stock-ai daily --at 18:00 AAPL MSFT       # blocks, fires daily
```

Refreshes prices, then checks the watchlist. A job that fails is logged and the
run continues — a broken price fetch must not silence the monitor.

`--once` is the form to put in cron or Task Scheduler, and is the recommended
way to run this: the blocking mode has no catch-up if the machine was asleep.

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

Ten screens: データ取得 / ランキング / 日米統合ランキング / スクリーニング /
ポートフォリオ / 監視リスト / バックテスト / ファクター検証 / AI分析 / 通知テスト —
the CLI features above, without the command line.

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
