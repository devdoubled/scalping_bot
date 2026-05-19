# XAUUSD Scalping Bot

An automated scalping bot for **Gold (XAUUSD)** on **MetaTrader 5**, built in Python.
Uses the M5 EMA Momentum strategy with a 7-filter entry gate, partial take-profit system, and a real-time monitoring dashboard.

---

## Strategy Overview

| Property | Value |
|----------|-------|
| Symbol | XAUUSD (Gold) |
| Timeframe | M5 (5-minute candles) |
| Entry Logic | EMA 8/13/21 momentum + RSI + ATR confirmation |
| Stop Loss | 1.5 × ATR (dynamic, volatility-scaled) |
| Take Profit | 3 levels: TP1 (1.5×), TP2 (2.5×), TP3 (3.5×) |
| Risk per Trade | 1% of account balance |
| Trading Sessions | London (07:00–12:00 UTC), NY Overlap (12:00–16:00 UTC) |

### How the bot decides to enter a trade

All **7 filters must pass at the same time** on a closed M5 candle:

| # | Filter | Description |
|---|--------|-------------|
| 1 | **ATR Active** | Market must be moving — ATR(14) above minimum threshold |
| 2 | **Spread OK** | Current spread must be ≤ 30 points (avoids high-cost entries) |
| 3 | **EMA Stack** | EMA8 > EMA13 > EMA21 (LONG) or EMA8 < EMA13 < EMA21 (SHORT) |
| 4 | **EMA Slope** | EMA8 must be sloping steeply enough — not sideways |
| 5 | **Pullback** | Price must have retraced to touch EMA13 or EMA21 in the last 3 bars |
| 6 | **RSI Zone** | RSI(14) in 45–65 for LONG, 35–55 for SHORT (momentum, not extreme) |
| 7 | **Candle Confirm** | Last closed candle body must be > 50% of its total range in trend direction |

### Take Profit & Exit system

```
Entry
  │
  ├─ TP1 reached (1.5 × SL distance) → Close 40% of position
  │
  ├─ TP2 reached (2.5 × SL distance) → Close 40% of position
  │                                     Move SL to Breakeven (entry price)
  │
  ├─ TP3 reached (3.5 × SL distance) → Close remaining 20%
  │
  ├─ EMA8 crosses EMA13 against trade → Close all remaining
  │
  └─ SL hit by MT5                   → Full loss, recorded
```

### Risk Management

- **1% risk per trade** — lot size auto-calculated from account balance and ATR-based stop distance
- **3% daily loss limit** — bot stops itself for the day if total losses exceed 3%
- **3 consecutive losses** — bot stops the session after 3 losses in a row
- **Max 2 concurrent positions** — never overexposed

---

## Requirements

- Windows 10 / 11
- Python 3.8 or higher
- MetaTrader 5 terminal installed and logged into a demo or live account
- A broker that supports XAUUSD with spreads under 30 points

---

## Installation

### Step 1 — Install Python dependencies

Open a terminal in the `ScalpingBot` folder and run:

```powershell
pip install -r requirements.txt
```

Or run the automated setup script:

```powershell
.\setup.ps1
```

**Dependencies installed:**
- `MetaTrader5` — MT5 Python API
- `pandas`, `numpy` — data processing
- `matplotlib`, `mplfinance` — charting

---

### Step 2 — Open MetaTrader 5

1. Launch your **MetaTrader 5** terminal
2. Log in to your **demo account** (recommended for first-time use)
3. Make sure **XAUUSD** is visible in your Market Watch

> The bot connects automatically to the running MT5 terminal — no manual login configuration needed.

---

### Step 3 — Configure the bot

Open `config.json` and review the settings:

```json
{
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "paper_mode": true,
  "risk_percent": 1.0,
  "max_daily_loss_percent": 3.0,
  "max_consecutive_losses": 3,
  "max_concurrent_positions": 2,
  ...
}
```

**Key settings to check before running:**

| Setting | Default | Description |
|---------|---------|-------------|
| `paper_mode` | `true` | `true` = simulate orders (no real trades). Set to `false` for live/demo trading |
| `entry_confirmation_ticks` | `1` | `1` = enter immediately when a valid signal appears; higher values require repeated confirmation |
| `paper_force_entry` | `true` | Paper-only test mode: create a forced paper signal when normal filters do not pass |
| `paper_auto_take_profit` | `true` | Paper-only test mode: simulate price reaching TP so TP handling can be verified |
| `risk_percent` | `1.0` | % of balance risked per trade |
| `max_daily_loss_percent` | `3.0` | Bot stops for the day at this loss threshold |
| `max_consecutive_losses` | `3` | Bot stops after this many losses in a row |
| `max_concurrent_positions` | `2` | Maximum open trades at one time |
| `max_spread_points` | `30` | Reject entry if spread exceeds this (in points) |

---

## Starting the Bot

### Option A — Double-click launcher

Double-click `run_bot.bat` in the ScalpingBot folder.

### Option B — Command line

```powershell
python main.py
```

---

## Using the Dashboard

When the bot starts, a GUI window opens automatically.

```
┌─────────────────────────────────────────────────────────────────────┐
│  ◈ XAUUSD  Scalping Bot   M5 · EMA 8/13/21 · RSI · ATR            │
│                    ● STOPPED          15:09 UTC  ◉ NY_OVERLAP  ● CONNECTED │
├─────────────────────────────────────────────────────────────────────┤
│  TODAY'S P&L  │ TOTAL ORDERS │ WINS │ LOSSES │ WIN RATE │ BEST │ WORST │
│   $+245.30    │      12      │   8  │   4    │  66.7%   │ ...  │ ...   │
├──────────────────────────────────────┬──────────────────────────────┤
│  ACCOUNT OVERVIEW                    │                              │
│  MARKET                              │  EVENT LOG                   │
│  BOT STATE                           │  (real-time feed)            │
├──────────────────────────────────────┤                              │
│  INDICATORS: EMA8 | EMA13 | EMA21 | RSI | ATR | Signal             │
├──────────────────────────────────────┤                              │
│  ENTRY FILTERS (7 pills)             │  CONTROLS                    │
│  Green = PASS  Dark = WAIT           │  ▶ START                     │
│                                      │  ■ STOP                      │
│                                      │  ⚠ CLOSE ALL                │
├──────────────────────────────────────┴──────────────────────────────┤
│  OPEN POSITIONS table                                               │
└─────────────────────────────────────────────────────────────────────┘
```

### Header indicators

| Indicator | Meaning |
|-----------|---------|
| `● CONNECTED` green | Bot is connected to MT5 terminal |
| `● DISCONNECTED` red | MT5 not found — open MT5 terminal first |
| `◉ NY_OVERLAP` green | Inside a trading session — bot will scan |
| `○ MARKET CLOSED` gray | Outside session hours — bot waits |
| `● SCANNING` blue | Running, looking for setups |
| `● ENTRY_READY` yellow | All 7 filters passed — entering next tick |
| `● IN_TRADE` green | Position is open, monitoring every 500ms |
| `● COOLING_DOWN` purple | Brief pause after trade closes |
| `● STOPPED` gray | Bot is not running |

### Entry filter pills

Each filter shows its current status:
- **Green background** = PASS
- **Dark background** = WAIT (not yet met)

A trade only fires when **all 7 turn green at the same time**.

### Controls

| Button | Action |
|--------|--------|
| **▶ START** | Begin scanning for trades |
| **■ STOP** | Stop the bot (existing trades remain open) |
| **⚠ CLOSE ALL** | Emergency — immediately close all open positions |

---

## Step-by-Step First Run (Recommended)

### Phase 1 — Paper Mode Test

1. Set `"paper_mode": true` in `config.json`
2. Open MetaTrader 5 and log in to your **demo account**
3. Run `python main.py`
4. Watch the Event Log — you will see `⚠ PAPER MODE — no real orders`
5. Click **▶ START**
6. Wait for London session (07:00 UTC) or NY Overlap (12:00 UTC)
7. Watch filters — when all 7 go green, you'll see in the log:
   ```
   Entry ready: SHORT | RSI=42.5 | ATR=8.23
   [PAPER] SHORT 0.08L XAUUSD | SL=2548.30 | TP=2561.80
   ```
8. Run through a full session (3–5 hours) and review the performance strip

### Phase 2 — Demo Live Test

1. Stop the bot
2. Set `"paper_mode": false` in `config.json`
3. Restart: `python main.py`
4. Click **▶ START**
5. The Event Log will now show `LIVE TRADING`
6. When all 7 filters pass, a **real order is placed on your MT5 demo account**
7. Check MT5 → Trade tab for the order with comment `XAUUSD_SCALPER`

### Phase 3 — Live Account

Only after running Phase 2 successfully for at least 1–2 weeks.

---

## Trading Sessions

The bot only trades during high-liquidity sessions:

| Session | UTC Time | Why |
|---------|----------|-----|
| London Open | 07:00 – 12:00 | High volatility, strong directional moves |
| London–NY Overlap | 12:00 – 16:00 | Highest liquidity of the day, tightest spreads |

Outside these hours the bot sits in SCANNING state but skips all signals.

---

## File Structure

```
ScalpingBot/
├── main.py                  # Entry point — bot logic + state machine
├── config.json              # All settings (edit this to customize)
├── mt5_credentials.json     # Optional — only needed if MT5 is not already running
├── requirements.txt
├── run_bot.bat              # Double-click launcher
├── setup.ps1                # One-time setup script
│
├── core/
│   ├── mt5_connector.py     # MT5 connection, data, order execution
│   ├── signal_engine.py     # EMA/RSI/ATR calculations + 7-filter logic
│   ├── risk_manager.py      # Position sizing, daily limits, trade stats
│   ├── trade_manager.py     # Partial TP, breakeven SL, reversal exit
│   └── session_filter.py    # UTC session time gate
│
├── gui/
│   └── dashboard.py         # Tkinter monitoring dashboard
│
└── logs/                    # Auto-generated daily trade logs
```

---

## Troubleshooting

**Bot shows DISCONNECTED**
- Make sure MetaTrader 5 is open and logged in before starting the bot
- The bot connects automatically — no credentials file needed

**No trades firing during session hours**
- Check which filters are dark (not passing) in the Entry Filters section
- Most common: Pullback filter — price hasn't retraced to EMA13/21 yet
- If spread is red, your broker's spread is too wide at that moment — the bot waits

**Bot stopped itself**
- Check Event Log — either daily loss limit (3%) or consecutive loss limit (3) was hit
- This is a safety feature — review trades before restarting

**Orders not appearing in MT5**
- Make sure `paper_mode` is `false` in config.json
- Restart the bot after changing the config

---

## Important Warnings

> **Always test on a DEMO account first.**
> Do not run on a live account until you have verified the bot's behavior over multiple sessions.

> **Past performance does not guarantee future results.**
> Markets change. Review and adjust strategy parameters periodically.

> **Do not risk money you cannot afford to lose.**
> Set appropriate `risk_percent` and `max_daily_loss_percent` for your account size.
