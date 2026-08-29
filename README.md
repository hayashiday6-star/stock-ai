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
met reality, and on this project the gap between those two mattered: twenty-four
real bugs surfaced only when real data arrived, and nearly every one of them
produced plausible wrong numbers rather than an error. So the table separates
them.

The clearest example: `sentiment` capped the model at 8 output tokens — exactly
what a one-word answer costs — and against the live API that returned a 200
with no content at all. The retest after the fix spent **52** output tokens to
say `positive`. The same ceiling sat on the importance rating the nightly
monitor runs on, where it would not have looked like a failure at all: every
disclosure would have come back unjudged and the run would have reported no
alerts, which is indistinguishable from a quiet day.

Two more came out of the same session, both invisible to the test suite because
both need a real terminal and a real model:

- A summary of a Japanese filing came back **in English** on the second run
  after coming back in Japanese on the first. Nothing in the prompt named a
  language, so it was a coin flip — and a notification whose language is a coin
  flip cannot be read at a glance.
- `¥` printed as `\xa5`, in the money figure, because a Japanese Windows
  console is cp932 and cp932 has no U+00A5. It does have the fullwidth `￥`,
  so the fix is a mapping, not a codepage change (see
  `stock_ai/core/encoding.py`).
- An alert did not say **which feed it came from**. The first one this system
  ever produced was a press article about Toyota, rendered identically to how a
  statutory EDINET filing would have been. `--feed all` mixes the two, and a
  news summary read as a company filing carries a weight it has not earned.
- The one-word output ceiling was wrong **twice**: 8 broke `sentiment`, and 64
  — chosen after measuring `sentiment` at 49 tokens — then broke the importance
  rating on a real filing the very next run. It is 512 now, set from
  measurement plus a wide margin rather than from a third estimate of what
  ought to be enough. The cost of being generous is a wider printed range; the
  cost of being tight is a monitor that reports no alerts on a day something
  was filed.
- A blocked network was reported as **"no price data returned"** — that is, as
  a verdict about the ticker. yfinance swallows a refused connection and hands
  back an empty frame, so a blip during a 500-name US load would have marked
  all 500 as symbols the provider does not know. It logs the real reason even
  though it does not raise it, so the log is captured and turned back into an
  error.
- The AI packages **uninstalled themselves.** A machine that had been calling
  Claude for an hour answered `No module named 'anthropic'` after a `git pull`
  and nothing else, because `uv run` re-syncs to the project's default
  environment and an extra is not part of it. The same clean-up exposed
  `schedule`, imported by the daily scheduler and declared nowhere — CI had
  stayed green only because `vectorbt` happened to pull it in. Both are now in
  the `runtime` dependency group, and CI installs what a user installs so the
  two cannot drift apart again.

| Area | State |
|---|---|
| JP prices & financials (J-Quants v2) | **Verified** — 1,564 TSE symbols, 5.0 years of history (the plan's full rolling window); PER median 14.94, PBR 1.26, ROE 9.1%, dividend 2.82%, all consistent with TSE norms; Toyota PER 10.0 / ¥43.25tn cross-checked |
| JP screening | **Verified** — run on the full universe, results reviewed by name |
| Factor / walk-forward validation | **Verified** — 12 windows, 1,253–1,492 names scored per window |
| EDINET disclosures | **Verified** — key accepted and 648 filings returned for 2026-08-14 |
| US prices & fundamentals (yfinance) | **Verified on 10 large caps** (1,030 bars each) — not a full US universe |
| Cross-market ranking (JP + US) | **Verified** — 1,564 securities ranked on one scale, JPY converted at a live rate |
| Backtest engine | **Verified** — and the run found a real bug: `--strategy sma200` was running a 50-day filter |
| AI: `ask` (plain-language screening) | **Verified** — "PER15倍以下でROE10%以上の日本株" parsed to `(ROE >= 0.1 AND 0 < PER <= 15.0) AND market in [JP]`; 344 of 1,552 matched. 318 input / 33 output tokens, $0.0024 |
| AI: `summarize` | **Verified** — a Japanese IR excerpt condensed with every figure preserved and none invented. 155 / 109 tokens, $0.0035 |
| AI: `sentiment` | **Verified** — a Japanese revision notice classified `positive`. 76 / 52 tokens, $0.0017. The 52 output tokens are why the old 8-token ceiling returned nothing |
| AI: `monitor`, end to end | **Verified** — real disclosures rated, thresholded, summarized and delivered as alerts, 0 unjudged, across several runs. A JP filing and a US news item in the same run came back in Japanese and English respectively, following each source |
| **What an EDINET alert is judged from** | **The filing index, not the filing.** EDINET serves the document separately as XBRL and this project does not open it, so a `[HIGH]` on a 変更報告書 is a verdict on its title, filer and metadata. The model said so unprompted on the first real one |
| Cost estimate vs. actual | **Verified on two runs.** `ai-cost` predicted 827 input tokens and the run consumed exactly 827; predicted 676 and the run consumed exactly 676 ($0.0120 against a $0.0418 ceiling) |
| Notifications | **Console and Discord verified** — the webhook returned 204. Telegram/LINE unexercised |
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

That is all of it. Everything the CLI, dashboard and AI commands need is in
the `runtime` dependency group, which `[tool.uv] default-groups` installs on a
bare `uv sync` — **do not pass `--extra`.**

The extras are still declared, because a consumer installing this as a library
should get the small base. But an extra is not part of the default environment,
and `uv run` re-syncs to the default environment before it runs anything. So
`uv sync --extra ai` holds only until the next `uv run` quietly prunes it —
which is how a machine that had been making Claude calls for an hour started
answering `No module named 'anthropic'` with nothing changed but a `git pull`.
The group is maintained rather than pruned, so the environment stops decaying
under normal use.

`--extra X` also *replaces* rather than adds: `uv sync --extra ai` on its own
uninstalls pandas, sqlalchemy and streamlit. `stock-ai info` reports whether
the SDK is actually importable, so a key that is set and a call that cannot be
made are distinguishable without reading a traceback.

## Fetching prices

```bash
uv run stock-ai fetch AAPL MSFT --start 2024-01-02 --end 2024-01-10
uv run stock-ai fetch AAPL MSFT   # incremental: only bars newer than what's stored
uv run stock-ai fetch --symbols-file us.txt --lookback 1500
```

`--symbols-file` is how a **US universe** gets loaded. `bulk-fetch` is J-Quants
throughout, and yfinance has no listing endpoint to enumerate a market from, so
the list has to come from a file — one symbol per line, `#` comments and commas
allowed, duplicates ignored:

```text
# core holdings
AAPL, MSFT, NVDA
GOOGL   # Alphabet class A
```

Deliberately not a scraped index membership list: this project does not ship
data it cannot verify, and a stale S&P 500 would look exactly like a correct
one. Follow it with `uv run stock-ai fundamentals` (no arguments), which
refreshes every stored US symbol, and the names join the
[cross-market ranking](#cross-market-ranking-jp--us).

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

## Choosing what to watch

```bash
uv run stock-ai watch-suggest --lookback-days 30
uv run stock-ai watch-suggest --lookback-days 30 --add    # add them
```

Ranks the JP names already in your database by **how many EDINET filings they
actually made**, and proposes the ones you are not watching yet.

This is not a view on which companies are worth owning, and nothing it prints
should be read as one. A watchlist decides *what you hear about*, and on that
question the data has something to say: a name that never files produces no
EDINET alert however long you watch it, while still costing a news-feed pull on
every run. So the ranking is filings made, not merit.

`--per-sector` (default 2) caps how many names one sector contributes, because
filing frequency clusters — banks and real-estate trusts file constantly, and an
uncapped list is mostly those.

Names with no stored prices are never proposed: an alert about a company whose
financials you do not hold has nothing to be read against.

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

### Which provider actually runs

Commands with no `--provider` use `AI_PROVIDER` from `.env`, and that defaults
to `dummy`. **A dummy run is hard to tell from a real one**: it completes, lists
alerts, names symbols and bills nothing, so it reads as a cheap run rather than
a fake one — the giveaway is that every item comes back `[HIGH]` with the prompt
echoed back as its summary. Set `AI_PROVIDER=claude` once a key is in place.

A dummy pass deliberately records nothing as seen. A seen disclosure is never
fetched again, so remembering an echo would hide those filings from every later
real run, and re-running would not bring them back.

If something has already been recorded that should not have been:

```bash
uv run stock-ai forget          # everything
uv run stock-ai forget 2502     # one name
```

The next run re-judges what it forgets, and bills for it — price it with
`ai-cost` first.

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

**An EDINET alert is rated from the document index, not the document.** The
filing itself is a separate endpoint serving XBRL, and this project does not
open it — so the model sees a title, the filer, the document type, and whatever
metadata the index carries. That is enough to say "a large-holding report was
filed" and not enough to say what it contained. The body now states this
outright, so a `[HIGH]` cannot be read as a judgement on text nobody fetched,
and `edinet-check` prints which fields a live record actually carries — the set
this project reads was chosen from the published spec, and a spec is not a
response.

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

Measured on this project's own watchlist, `claude-opus-5`:

| | disclosures | input tokens | output cap | cost (USD) |
|---|---|---|---|---|
| rate only (floor) | 2 | 827 | 128 | <$0.01 (0.00734) |
| rate + summarize (ceiling) | 2 | 1,394 | 2,176 | $0.0614 |

The runs that followed reported `827 in` and, a run later, `676 in` — **both
exactly what was predicted**, with totals well under the ceiling. That is the
whole claim this feature makes.

Read the two rows as *worst cases*, not as a range around a likely figure.
Input is counted, but output is the `max_tokens` ceiling on every call, and a
rating that answers in one word uses a small fraction of it — measured replies
run 30–60 tokens against a 512-token cap. So even the `rate only` row sits well
above what a rating-only run costs. The `spent:` line the run prints when it
finishes is the figure to compare against a bill.

Rating is cheap because the prompt asks for one word. Summaries are where the
money goes. How the total scales depends on which feed is on, and the two are
not alike:

- **EDINET** matches a security code against the day's filings, so a watched
  name that filed nothing costs nothing. Adding names is close to free.
- **The news feed** returns up to `--limit` items per symbol whether or not
  anything was filed, so **each new name arrives with a backlog** of up to
  `--limit` items to judge. Measured: adding two names to a three-name list
  took the next run from 1 pending disclosure to 20.

That backlog is one-off — everything judged is remembered — but it lands on the
first run after you extend the watchlist, which is exactly when you are least
expecting a bill. Price it with `ai-cost` first, and use `--limit` to cap it.

`--provider dummy` (the default in `4-日次自動化.bat`) costs nothing at all and
needs no key.

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

## Connecting a moomoo account (OpenD)

```powershell
.\scripts\moomoo-check.ps1               # paper account
.\scripts\moomoo-check.ps1 -Real         # live account, read only
.\scripts\moomoo-check.ps1 -Real -Unlock # and test the 6-digit trading PIN
```

moomoo has no API key. Authentication is a gateway program — **OpenD** — that
runs on your own PC, that you log into with your moomoo securities account, and
that this code then talks to on `127.0.0.1:11111`. Logging out or closing OpenD
is the same as deleting a key.

That design makes every failure look identical from Python, and the way it looks
is the worst available one: **the call blocks**. Confirmed while building this —
constructing `OpenQuoteContext` against a port with nothing listening does not
raise, it retries in a background thread and never returns. So "OpenD was never
installed", "OpenD is sitting on a verification-code prompt", and "the account
has no permission for this market" are all, at the prompt, the same nothing
happening.

`moomoo-check` therefore probes the port with a plain socket *before* the client
gets a chance to hang, puts a deadline on the handshake after that, and then
walks the rest of the chain one link at a time — client installed, port open,
logged in (quotes and trading judged separately, because they drop separately),
account visible, account answering, PIN accepted — stopping at the first break
and naming it. The empty-account case gets its own treatment: a login that
worked and an account list that came back empty is not "no accounts", it is the
wrong entity or market filter, so the report lists what *was* found next to what
was asked for.

It never places an order, and a PIN that is tested is re-locked immediately
afterwards. Account numbers are masked to the last four digits and balances are
withheld unless `--show-assets` is given, because `moomoo-output.txt` is a file
people paste when asking for help.

Once that passes, `moomoo-flow` reads per-symbol capital flow through the same
gateway — `uv run stock-ai moomoo-flow 9842 --start 2026-08-17 --end 2026-08-28`.
It takes this project's own symbol forms (`9842`, `9842.T`, `AAPL`) and converts
them to moomoo's market-first `JP.9842`, and it goes through the same port probe
and handshake deadline, so a missing gateway is a message rather than a hang.
Calling the client directly instead, note three things. The argument names are
`start`/`end` — `begin_time`/`end_time` raise `TypeError`. `main_in_flow` is
documented as valid only for the dated periods, so the command omits that column
on intraday rather than printing a figure that reads as a real zero. And do not
pass `security_firm` to `OpenQuoteContext`: it is a crypto-only field there and
it is not ignored — setting it to `FUTUJP` made OpenD refuse a plain US stock as
an unsupported *crypto* quote. It belongs on the trade context.

One thing to know before reaching for it as a data source: moomoo grants
*quote* access per market, separately from what the account may trade, and it
currently lists Japanese equities as not available through the API at all. A JP
symbol is refused however the connection is configured, so JP prices here keep
coming from J-Quants and yfinance. `moomoo-flow AAPL` is the one-command test
that separates an account problem from a per-market one, and the command says
so on any refusal. The market table is deliberately *not* hard-coded — moomoo
reserves the right to change it, and a stale copy would contradict the gateway.

Setup, from downloading OpenD to the first successful check, is in
**[docs/MOOMOO_OPEND.md](docs/MOOMOO_OPEND.md)** (Japanese). Execution through
moomoo is deliberately *not* implemented — same stance as the IBKR skeleton.

## Accumulation screen (US)

```powershell
uv run stock-ai accumulation --symbols-file watchlist.txt
uv run stock-ai accumulation                     # the whole market, a few minutes
```

Three phases: a price/volume pass over every NYSE/NASDAQ/AMEX common stock, then
funding flow (through moomoo OpenD), the short side and the chart on what
survives, then a breakout test with stop levels as prices.

The design point is what it refuses to do. Several metrics the brief for this
asked for — dark-pool share, block prints, borrow fees — are sold rather than
published, and the ratio it wanted as "large orders ÷ volume" cannot be formed
at all from a feed that reports net currency per order-size band. Those are
printed as 取得不可 with the reason, never as a plausible number, and absence is
a *type* here rather than a blank: a `Missing` cannot be added, compared or
formatted as a digit, so there is no code path from an unmeasured metric to a
table cell that reads like a measurement.

Cost ordering is the other half. Price and volume come from one bulk download;
market cap is one request per symbol and moomoo's flow is capped at 30 calls per
30 seconds, so those are asked only of names that already passed everything
free. Details, including the full obtainable/not-obtainable table, are in
**[docs/ACCUMULATION.md](docs/ACCUMULATION.md)** (Japanese). It is not advice.

## Daily automation

```bash
uv run stock-ai daily --once --provider claude --channel discord --max-cost 0.20
uv run stock-ai daily --at 18:00 AAPL MSFT       # blocks, fires daily
```

**A failed run notifies; a quiet one does not.** Alerts are only sent when
there are alerts, which for a scheduled job leaves the channel silent in four
situations that mean opposite things: nothing was filed, nothing cleared the
threshold, the cap skipped the job, and the job broke. So a failure always
sends a summary to `--channel`. Success stays quiet unless you pass
`--heartbeat`, because a message every single morning is one people stop
reading — and then the failure message is unread too.

**Set `--max-cost` whenever `--provider` is a paid one.** A scheduled run bills
an account every night with nobody watching, and how many disclosures get filed
on a given day is not something the schedule controls. The check itself costs
nothing — it counts tokens, which is a separate unbilled endpoint — and it runs
before a single billed call.

Over the cap, the monitor job is skipped and the run is reported as failed, so
it shows up rather than passing quietly in a log nobody opens. Nothing is marked
seen, so the next run picks the same disclosures up. The cap is compared against
the *ceiling*, which assumes every disclosure is summarized, so it will
sometimes refuse a run that would in fact have been cheap — the right way round
for a job nobody is watching.

Refreshes prices, then checks the watchlist. A job that fails is logged and the
run continues — a broken price fetch must not silence the monitor.

`--once` is the form to put in cron or Task Scheduler, and is the recommended
way to run this: the blocking mode has no catch-up if the machine was asleep.

**Naming no symbols skips the price refresh** and runs only the watchlist
check. It does not refresh everything stored — a nightly pass over 1,500-odd
names belongs in `bulk-fetch`, which throttles and resumes, and this job has
neither.

The task `scripts/4-daily.ps1 -Register` creates runs **as you, only while you
are logged on** (that is Windows' default principal). It catches up after sleep,
but a day spent logged out is a day it does not run. Task Scheduler's *Run
whether user is logged on or not* changes that, at the cost of storing your
password.

## Scoring, AI, notifications

```bash
uv run stock-ai score AAPL MSFT                 # 0-100 weighted score + factor breakdown
uv run stock-ai summarize "..." --provider dummy  # dummy|claude|openai|gemini
uv run stock-ai sentiment "..." --provider dummy
uv run stock-ai notify "buy: AAPL" --channel console  # console|discord|telegram|line
```

## Dashboard

```bash
uv run streamlit run src/stock_ai/dashboard/app.py
```

Ten screens: データ取得 / ランキング / 日米統合ランキング / スクリーニング /
ポートフォリオ / 監視リスト / バックテスト / ファクター検証 / AI分析 / 通知テスト —
the CLI features above, without the command line.

The two screens that spend money carry the same cost machinery the CLI does:
the watchlist has a free **費用を見積もる** button that counts tokens without
generating any, both AI screens print what the run actually spent when it
finishes, and the sidebar shows the active model — or warns that the SDK is
missing, which otherwise looks identical to a working setup. A button in a
browser has no console line to notice afterwards, so a control that bills an
account silently is the one thing this had to avoid.

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
