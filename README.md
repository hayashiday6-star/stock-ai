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

## What is verified, and what is only implemented

"All ten phases in place" says the code exists. It does not say the code has
met reality, and on this project the gap between those two mattered: ten real
bugs surfaced only when real data arrived, and every one of them produced
plausible wrong numbers rather than an error. So the table separates them.

| Area | State |
|---|---|
| JP prices & financials (J-Quants v2) | **Verified** — 1,564 TSE symbols, 5.0 years of history (the plan's full rolling window); PER median 14.94, PBR 1.26, ROE 9.1%, dividend 2.82%, all consistent with TSE norms; Toyota PER 10.0 / ¥43.25tn cross-checked |
| JP screening | **Verified** — run on the full universe, results reviewed by name |
| Factor / walk-forward validation | **Verified** — 12 windows, 1,253–1,492 names scored per window |
| EDINET disclosures | **Verified** — key accepted and 648 filings returned for 2026-08-14 |
| US prices & fundamentals (yfinance) | **Verified on 10 large caps** (1,030 bars each) — not a full US universe |
| Cross-market ranking (JP + US) | **Verified** — 1,564 securities ranked on one scale, JPY converted at a live rate |
| Backtest engine | **Verified** — and the run found a real bug: `--strategy sma200` was running a 50-day filter |
| AI scoring / chat / notifications | **Not validated** — needs a paid key. `6-AI検証.bat` (or `scripts/6-verify-ai.ps1`) runs the check: free steps first, then the billed ones only after you confirm |
| Automated trading | **Paper broker only.** The IBKR path is a skeleton, opt-in, and has never placed an order. Do not point it at a funded account |

One known gap in the data, not the code: **a company that pays no dividend is
recorded as "dividend unknown", not "dividend zero".** yfinance simply omits
the field for a non-payer, and an absent field is indistinguishable from a
fetch that returned less than it should have. The consequence is visible in a
ranking — Amazon shows `div = -` and 85% coverage — and it cuts the wrong way
twice: a definite non-payer is marked under-measured, and it escapes the
dividend factor entirely while a company yielding 0.3% carries that drag. The
safe reading is that **the dividend factor ranks payers against payers**, and
a non-payer's score is built from the rest.

Two conclusions this system produced about itself, both negative and both
worth knowing before use:

- **The tenbagger score has no demonstrated edge.** Tested properly, it failed.
  Use it to screen, not to rank. See
  [Does the score actually work?](#does-the-score-actually-work)
- **Expected returns are not implemented**, deliberately — annualising a past
  mean produces a number whose estimation error exceeds its signal, and
  printing it would dress an unknown as a forecast.

Nothing here should be read as investment advice, and no screen output is a
recommendation.

## Requirements

- Windows 11
- [uv](https://docs.astral.sh/uv/) (manages Python 3.13 automatically)

> **PowerShell users:** every command below is a single line — run them from the
> project folder. The examples use no shell-specific syntax, but note that
> Windows PowerShell 5.1 (the Windows 11 default) does not support `&&`; run
> chained commands on separate lines, or use PowerShell 7.

## Setup

**Double-click these, in order** — no terminal needed. Each one moves to the
project folder itself, so it does not matter where Windows opens:

| File | What it does |
|---|---|
| `1-セットアップ.bat` | Install dependencies, create `.env`, list the keys still missing |
| `APIキー設定.bat` | Store one API key in `.env`, hidden as you paste it |
| `2-動作確認.bat` | Check every data source, write `verify-output.txt` |
| `3-データ取得.bat` | Load a 20-symbol trial, so a problem shows up cheaply |
| `4-日次自動化.bat` | Register the daily job with Windows (asks for admin) |
| `5-分析.bat` | Fill any gaps, screen, then test whether the score holds up |
| `ダッシュボード起動.bat` | Open the dashboard in a browser |
| `ダッシュボード起動(スマホ).bat` | Same, reachable from a phone on the same Wi-Fi |
| `EDINET確認.bat` | Diagnose an EDINET key by trying every way of sending it |
| `PowerShellを開く.bat` | A terminal already in this folder, for one-off commands |

The phone launcher is separate on purpose. The dashboard has **no login**:
serving it to the network means anyone on that network can read the database,
start data fetches, and spend whatever an AI key is attached to. Home Wi-Fi,
not a cafe.

That last one exists because a new PowerShell window starts in
`C:\WINDOWS\system32`, where `uv` and `git` cannot see the project and every
command fails with `program not found` or `not a git repository`. Open the
terminal with that file and any command below works as written.

Between 1 and 2, run the API key one once per key that step 1 reported missing.
It hides the value while you paste, which matters: a key typed into a normal
command line is written to the PowerShell history file in plain text and stays
there. Editing `.env` in Notepad works too.

From a terminal instead, run the same scripts directly — but note two things
that bite: PowerShell opens in `C:\WINDOWS\system32`, so `cd` to the project
folder first, and Windows blocks unsigned `.ps1` files by default, hence
`-ExecutionPolicy Bypass`:

```powershell
cd C:\path\to\stock-ai
powershell -ExecutionPolicy Bypass -File .\scripts\1-setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\2-verify.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\3-load-data.ps1 -Segment prime
powershell -ExecutionPolicy Bypass -File .\scripts\4-daily.ps1 -Register -At 18:00 -Provider claude
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

**`uv sync --extra X` replaces the installed extras, it does not add to them.**
A second `uv sync --extra ai` uninstalls pandas, sqlalchemy and streamlit,
and the next command dies with `No module named 'pandas'`. Name every extra
you want in one command, or just take them all:

```bash
uv sync --all-extras                       # what 1-セットアップ.bat does
uv sync --extra data --extra db --extra ai # or name them together
```

## Fetching prices

```bash
uv sync --all-extras
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
trailing fundamentals picks future multi-baggers reliably — and this particular
weighting **was tested on the TSE universe and showed no edge**, so use it to
find growing companies, not to decide position sizes. The measurement is in
[Does the score actually work?](#does-the-score-actually-work) below.

## Does the score actually work?

**It was tested on the real TSE universe, and no edge was found.** The answer
is below; the tools that produced it are described after.

```
12 quarterly formations, 2022–2025, 252-bar hold
1,253–1,492 names scored per window

3 of 12 windows cleared 2σ
median t = +0.52
monotonic across buckets in 3 of 12
5 of 12 had a positive excess at all — a coin flip
```

The pass/fail bands were **declared before the numbers were seen** (13+/16
consistent, 8–12 a regime bet, 7 or fewer not demonstrated; scaled to 12
windows ≈ 10 / 6–9 / 5 or fewer). 3 of 12 falls below every band.

The strongest window argues against the score rather than for it. Formation
2024-08-06 (`t = +4.25`, excess `+7.64%`) begins the day after the Nikkei's
largest single-day fall of the period — a small-cap growth tilt formed at a
crash low measures the rebound, not stock selection. *(That the 2024-08-05
crash was the largest of the sample is market history, not something measured
from the local database; the t-statistic and excess return are measured.)*
Two of the three significant windows are one quarter apart and share nine
months of forward returns, so they are closer to one episode than two.

So `--preset tenbagger` is a **screening** preset — useful for finding
companies that are actually growing — and not a ranking to allocate on. Those
are different claims, and only the first survived testing.

### The tools that produced that answer

```bash
uv run stock-ai factor-test 2024-06-28 --preset tenbagger --horizon 252
uv run stock-ai factor-test 2022-06-30 --preset tenbagger --walk-forward 12
```

`factor-test` ranks the stored universe using **only data available on the
formation date** (prices truncated there, statements filtered on their
disclosure date), holds the top bucket for the horizon, and compares against
the equal-weight universe.

Read the **t-statistic before the excess return**. On a universe of a few dozen
names, sampling noise alone routinely hands the top bucket a several-percent
"edge" — in testing, a universe with returns drawn purely at random still
produced +5.4% for the top bucket, at t = +1.39. Anything inside 2σ is not
distinguishable from chance, and the report says so.

**`--walk-forward` is the one that matters.** A single formation date is one
observation: the first date tried here, 2024-06, came back at `t = +2.78` and
looked like evidence. Across twelve windows it was one draw in twelve. The
function deliberately cannot return a single summary number, because that is
the shape that invites picking the flattering window.

Two limits neither form can remove: the universe is whatever is in the local
database, so delisted names are missing (survivorship bias), and 252 bars is a
short horizon for a thesis about multi-year compounding. Neither is a reason to
use a score that failed — "not yet disproven over a longer horizon" is not an
edge.

The composite score is built from unitless ratios, so it already compares
across markets. Market cap does not — it arrives in the listing market's
currency — so it is converted to `--base` (default USD) before being shown or
filtered on.

## Calendar-month seasonality

`stock-ai history` reports how far back the stored prices reach and flags a
floor shared by the universe. J-Quants subscriptions are a **rolling window**
(measured: 5.0 years), so `--lookback` beyond it is narrowed automatically
rather than failing the symbol — but the window itself only moves with the
plan, not with another fetch.

```bash
uv run stock-ai seasonality 7203                    # one name, month by month
uv run stock-ai seasonality 7203 --split-year 2024  # found before / measured after
uv run stock-ai seasonality-scan --month 9          # every stored symbol
```

**Read the verdict, not the table.** Searching a universe for "always rises in
September" is tens of thousands of hypotheses, and a calendar month gives one
observation per year — so the sample is tiny exactly where the search is
widest. Measured on 300 random walks with no seasonality in them at all:

| History | Cleared \|t\| ≥ 2 | Strongest "pattern" |
|---|---|---|
| 4 years (n=3) | **15.4%** | +11.97% mean, t = +17.47, up 100% of years |
| 10 years (n=9) | **11.0%** | −5.61% mean, t = −6.41, up 0% of years |

Those came out of a random number generator. A screen that ranked by mean
return would have presented the first row as a discovery.

So `seasonality-scan` re-runs itself with each symbol's month labels shuffled
and reports both numbers. If the real scan finds 555 and the shuffled one finds
554, there is nothing there — and that is the normal result. `--split-year`
is the only part that can support a claim rather than deflate one: pick the
months on the early years, then measure them on the years held back.

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

**Verified against the live API on 2026-08-15: authentication only.** The key
is accepted and days are served. What is **not** yet confirmed against real
filings is the parsing — `secCode`, `docTypeCode`, `submitDateTime` — because
the day tested was a Saturday during Obon and returned zero filings. A drifted
field name would show up as zero disclosures rather than as an error, so check
a weekday before trusting an empty result:

```bash
uv run stock-ai edinet-check --date 2026-08-14   # Documents > 0 = parsing path exercised
```

`monitor` also logs `N filings scanned, M carried a securities code`, which
separates "nobody filed" from "the field we match on is gone".

If EDINET refuses the key, run `edinet-check` rather than guessing. The gateway
answers `invalid subscription key` both when the key is wrong and when it is
somewhere the gateway does not read, so one failure cannot tell those apart —
measured on 2026-08-15 with a **valid** key:

| how the key is sent | result |
|---|---|
| `Subscription-Key` query parameter | 200 |
| `Ocp-Apim-Subscription-Key` header | 200 |
| `Subscription-Key` header | **401** |

Pasting the URL into a browser only ever tests the first row.

## What will an AI run cost?

```bash
uv run stock-ai ai-cost --feed edinet
```

Prices the **next** watchlist run before paying for it. Both halves are
knowable in advance: input tokens are counted exactly by the provider's
`count_tokens` endpoint (which generates nothing and is not billed), and output
is capped by the `max_tokens` on each call — 8 tokens to rate a disclosure,
1024 to summarise one.

What is *not* knowable is how many summaries a run needs: a disclosure is only
summarised if the model rates it above the watch entry's threshold. So the
answer is a range, and it is reported as one:

Measured on this project's own watchlist, one pending disclosure, `claude-opus-5`:

| | disclosures | input tokens | output cap | cost (USD) |
|---|---|---|---|---|
| rate only (floor) | 1 | 260 | 8 | <$0.01 (0.00150) |
| rate + summarize (ceiling) | 1 | 364 | 1,032 | $0.0276 |

Rating is cheap because the prompt asks for one word. Summaries are where the
money goes, so the cost scales with **how many disclosures were filed**, not
with how many symbols you watch. `--provider dummy` (the default in
`4-日次自動化.bat`) costs nothing at all and needs no key.

Prices are a cached copy of Anthropic's published rates and drift — the
invoice is the authority, and an unpriced model shows a dash rather than a
guessed figure.

### And what it actually cost

An estimate nobody checks is a claim. Every AI command prints what it really
spent when it finishes:

```
spent: 3 call(s) to claude-opus-5, 1,204 in / 118 out - <$0.01 (0.00895)
```

That is the API's own `usage` figures summed over the run, priced the same way
as the estimate, so the two can be read against each other directly. A run that
lands above the ceiling means the estimate is wrong — worth reporting, because
a cost preview that cannot be trusted is worse than none.

### Choosing a cheaper model

```bash
# in .env
ANTHROPIC_MODEL=claude-haiku-4-5
```

Model choice is a five-fold cost difference on the identical run
(`$1/$5` per million tokens against opus's `$5/$25`). `ai-cost` prices whatever
`ANTHROPIC_MODEL` selects, and `stock-ai info` shows which model is active and
whether that came from `.env` or the built-in default — so the estimate and the
run can never disagree about which model is being paid for.

## Verifying the AI and notification features

```powershell
.\scripts\6-verify-ai.ps1            # free checks, then asks before spending
.\scripts\6-verify-ai.ps1 -SkipPaid  # free checks only
.\scripts\6-verify-ai.ps1 -Channel discord
```

These are the last two subsystems that have never met reality, for one reason:
they are the only ones that need a paid key. The script runs them in the order
that costs least to learn most — key and model, then the notifier, then
`ai-cost`, and only then, after an explicit `yes`, the four billed checks
(`sentiment`, `summarize`, `ask`, `monitor`) with the cheapest first. Everything
lands in `verify-ai-output.txt`.

Two details worth knowing about it:

- The Japanese sample text means this one script is UTF-8 **with a BOM**, unlike
  every other script here. It checks its own literals against their real code
  points before spending anything: a lost BOM would otherwise pay Claude to
  analyse mojibake and return a confident answer about it.
- `notify --channel console` proves the notifier path, not a webhook. To check a
  real one, put `DISCORD_WEBHOOK_URL` in `.env` and re-run with
  `-Channel discord` — and then go and look at Discord. A clean exit means the
  service accepted the POST, not that the message arrived.

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
uv sync --all-extras
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
