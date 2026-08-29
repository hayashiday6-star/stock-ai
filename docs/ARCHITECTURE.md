# Architecture

`stock-ai` is a **pipeline of independent layers**. Data flows in one direction;
each layer depends only on abstractions of the layer(s) below it, so any layer
can be swapped (e.g. a different broker or AI provider) without touching the rest.

```
config ─┐
        ▼
  data ─▶ database ─▶ screening / technical / fundamental
                              │
                              ▼
                        ai / news / ir      (scoring inputs)
                              │
                              ▼
                     portfolio / scoring     (decision)
                              │
                              ▼
                          broker             (execution — dummy first)
                              │
                              ▼
                notification / dashboard     (output)
```

## Layers

| Package | Responsibility | Key abstraction (planned) |
|---|---|---|
| `config` | Settings, constants, paths | `Settings` (pydantic-settings) |
| `core` | Shared types, exceptions, logging, DI | `StockAIError`, `configure_logging` |
| `data` | Fetch JP/US prices & fundamentals | `DataProvider` protocol |
| `database` | Persistence (SQLite → PostgreSQL) | `Repository` per entity |
| `screening` | Composable filter conditions | `Condition` protocol |
| `technical` | Indicators (SMA, RSI, MACD, ...) | `Indicator` protocol |
| `fundamental` | PER/PBR/ROE/growth metrics | pure functions |
| `backtest` | Strategy evaluation vs benchmarks | `Strategy` + engine |
| `ai` | LLM abstraction (Claude/OpenAI/Gemini) | `AIProvider` protocol |
| `news` / `ir` | Collection + analysis | `Analyzer` protocol |
| `portfolio` | Scoring, weighting, sizing | `Scorer`, `Sizer` |
| `broker` | Order execution; moomoo OpenD authentication | `Broker` protocol (dummy → IBKR); `moomoo.diagnose` |
| `notification` | LINE/Discord/Telegram delivery | `Notifier` protocol |
| `dashboard` | Streamlit UI | reads via services |

## Design principles

- **SOLID / DIP** — high-level code depends on `Protocol`/ABC interfaces, not
  concrete implementations. Implementations are injected.
- **Open for extension** — screening conditions, indicators, notifiers, and
  strategies are registered as plugins, so adding one never edits existing code.
- **Side effects at the edges** — network, disk, order placement, and message
  sending live only in boundary layers; the core stays pure and testable.
- **No hard-coded values** — all configuration flows through `config`.
- **Secrets are `SecretStr`** — never logged, never in `repr`.

## Conventions

- Python 3.13+, src-layout, `uv` for dependency management.
- Type hints everywhere; Google-style docstrings.
- Ruff (lint + format) and pytest enforced by pre-commit and CI.
- Phase-scoped dependencies live in `[project.optional-dependencies]`.
