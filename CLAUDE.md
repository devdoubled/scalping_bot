# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Install dependencies
pip install -r requirements.txt

# One-time setup (creates venv, installs deps)
.\setup.ps1

# Run the bot (GUI opens automatically)
python main.py

# Double-click launcher alternative
.\run_bot.bat
```

There are no automated tests or linting configurations in this project.

## Architecture

The bot is a single-process application with a Tkinter GUI. The main thread runs the GUI event loop (`dashboard.run()`); the bot logic runs in a background thread started via `dashboard.schedule()`.

### State machine (`main.py` — `ScalpingBot`)

`ScalpingBot._loop()` drives a polling loop with two tick rates:
- **Scan mode**: every `polling_interval_scan_ms` (5 s default)
- **Trade mode**: every `polling_interval_trade_ms` (500 ms default)

States: `SCANNING → ENTRY_READY → IN_TRADE → COOLING_DOWN → SCANNING`. The bot also self-transitions to `STOPPED` when daily risk limits are hit. A separate `_start_price_ticker()` daemon thread updates the dashboard price/spread display at 200 ms independent of the main loop.

### Module responsibilities

| Module | Role |
|--------|------|
| `core/mt5_connector.py` | Wraps the `MetaTrader5` C extension. Handles connect/disconnect (auto-attach to running terminal first, credentials file as fallback), OHLCV fetch, tick price, order placement, partial close, and SL modification. All methods return `None`/`False` on failure. In paper mode, orders log a `[PAPER]` line and return ticket `-1`. |
| `core/signal_engine.py` | Pure calculation layer. `SignalEngine.analyse()` computes EMA 8/13/21, RSI, ATR from a DataFrame. `get_signal()` runs all 7 entry filters and returns a dict with `direction`, `filters` status map, and pre-computed `sl`/`tp1`/`tp2`/`tp3`/`sl_distance` when all filters pass. `check_ema_reversal()` is called by `TradeManager` each tick while in a trade. |
| `core/trade_manager.py` | Owns `ManagedTrade` objects during their lifetime. `monitor()` checks TP1/TP2/TP3 thresholds and EMA reversal on every trade tick; fires partial closes and breakeven SL moves via the connector. Calls `RiskManager.record_trade_result()` when a trade closes. |
| `core/risk_manager.py` | Tracks daily P&L and consecutive losses. `calculate_lot_size()` sizes position so loss at SL equals `risk_percent` of balance. `check_daily_limits()` returns `(False, reason)` to halt trading. Stats reset with `reset_daily()`. |
| `core/session_filter.py` | Stateless UTC time-gate. `is_trading_session()` and `get_active_session_name()` compare current UTC time against the `sessions` array from config. |
| `gui/dashboard.py` | Tkinter UI. Exposes `update_*` methods called by the bot loop. The three control buttons (`START`, `STOP`, `CLOSE ALL`) call callbacks passed in at construction. `dashboard.schedule(ms, fn)` wraps `after()`. |

### Configuration

All runtime parameters live in `config.json` — no hardcoded strategy constants in source files. The config dict is passed through to every module at construction. Key fields:

- `paper_mode: true` — simulates orders; no real MT5 trades placed
- `sessions` — list of `{name, start_utc, end_utc}` objects; bot only trades inside these windows
- `magic_number` — MT5 identifier tag used to filter positions belonging to this bot

### MT5 connection flow

`MT5Connector.connect()` tries two strategies:
1. `mt5.initialize()` with no args — attaches to whichever account is logged in the running MT5 terminal
2. Reads `mt5_credentials.json` and calls `mt5.initialize(path=...)` + `mt5.login(...)` — only if strategy 1 fails and the file has non-template values

If `MetaTrader5` is not installed, the module sets `MT5_AVAILABLE = False` and the bot runs in paper-only mode.

### Order filling quirk

`place_order()` and `close_partial()` detect the broker's supported filling mode (`FOK → IOC → RETURN`) and automatically retry with `ORDER_FILLING_RETURN` if the first attempt fails. This handles ECN/STP brokers that reject IOC fills.
