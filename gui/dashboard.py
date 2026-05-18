# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import queue
from datetime import datetime, timezone
from typing import Callable, Dict


# ── Design tokens ─────────────────────────────────────────────────────────────
C = {
    "bg":          "#0d1117",
    "card":        "#161b22",
    "border":      "#21262d",
    "input":       "#0d1117",
    "header":      "#010409",

    "text":        "#e6edf3",
    "sub":         "#8b949e",
    "muted":       "#484f58",

    "green":       "#3fb950",
    "green_dim":   "#1a3d26",
    "green_bg":    "#0d2318",

    "red":         "#f85149",
    "red_dim":     "#4a1916",
    "red_bg":      "#2a1010",

    "yellow":      "#d29922",
    "yellow_bg":   "#2a1e08",

    "blue":        "#58a6ff",
    "blue_bg":     "#0c1f38",

    "purple":      "#bc8cff",

    "SCANNING":    "#58a6ff",
    "ENTRY_READY": "#d29922",
    "IN_TRADE":    "#3fb950",
    "COOLING_DOWN":"#bc8cff",
    "STOPPED":     "#484f58",
}

F_MONO = "Consolas"
F_UI   = "Segoe UI"

FILTERS = {
    "atr_active":     ("ATR Active",      "Market volatility OK"),
    "spread_ok":      ("Spread OK",       "Below 30 pts limit"),
    "ema_aligned":    ("EMA Stack",       "8 > 13 > 21 aligned"),
    "slope_ok":       ("EMA Slope",       "Trend is steep enough"),
    "pullback":       ("Pullback",        "Price retraced to EMA"),
    "rsi_zone":       ("RSI Zone",        "45-65 long / 35-55 short"),
    "candle_confirm": ("Candle Confirm",  "Body > 50% of range"),
}


def _card(parent, title: str, pady_top=10) -> tk.Frame:
    """Bordered card widget. Returns the inner content frame."""
    outer = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
    outer.pack(fill="x", pady=(0, 8))
    inner = tk.Frame(outer, bg=C["card"])
    inner.pack(fill="both", expand=True)
    lbl_frame = tk.Frame(inner, bg=C["card"])
    lbl_frame.pack(fill="x", padx=14, pady=(pady_top, 6))
    tk.Label(lbl_frame, text=title, bg=C["card"], fg=C["sub"],
             font=(F_UI, 7, "bold"), anchor="w").pack(side="left")
    tk.Frame(inner, bg=C["border"], height=1).pack(fill="x", padx=14)
    return inner


def _row(parent, label: str, value: str = "--", label_w=18) -> tk.Label:
    """Key-value row. Returns the value Label for later updates."""
    f = tk.Frame(parent, bg=C["card"])
    f.pack(fill="x", padx=14, pady=3)
    tk.Label(f, text=label, bg=C["card"], fg=C["sub"],
             font=(F_UI, 9), anchor="w", width=label_w).pack(side="left")
    v = tk.Label(f, text=value, bg=C["card"], fg=C["text"],
                 font=(F_MONO, 10, "bold"), anchor="e")
    v.pack(side="right")
    return v


def _spacer(parent, h=6):
    tk.Frame(parent, bg=C["card"], height=h).pack()


class Dashboard:
    def __init__(self, on_start: Callable, on_stop: Callable, on_close_all: Callable):
        self._on_start     = on_start
        self._on_stop      = on_stop
        self._on_close_all = on_close_all
        self._q: queue.Queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title("XAUUSD Scalping Bot")
        self.root.configure(bg=C["bg"])
        self.root.geometry("1200x820")
        self.root.minsize(900, 600)

        self._setup_styles()
        self._build()
        self._poll_log()

    # ─── ttk styles ──────────────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("T.Treeview",
            background=C["card"], foreground=C["text"],
            fieldbackground=C["card"], rowheight=24,
            font=(F_MONO, 9), borderwidth=0,
        )
        s.configure("T.Treeview.Heading",
            background=C["border"], foreground=C["sub"],
            font=(F_UI, 8, "bold"), borderwidth=0, relief="flat",
        )
        s.map("T.Treeview",
            background=[("selected", C["blue_bg"])],
            foreground=[("selected", C["blue"])],
        )

    # ─── Top-level layout ─────────────────────────────────────────────────────

    def _build(self):
        self._build_header()
        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")
        self._build_perf_strip()
        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")

        # Body: left main + fixed right sidebar
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True)

        # Right sidebar (log) — fixed width, pack first so it doesn't get squeezed
        sidebar = tk.Frame(body, bg=C["bg"], width=300)
        sidebar.pack(side="right", fill="y", padx=(0, 10), pady=10)
        sidebar.pack_propagate(False)
        self._build_sidebar(sidebar)

        # Left main content
        main = tk.Frame(body, bg=C["bg"])
        main.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        self._build_main(main)

    # ─── Performance strip ───────────────────────────────────────────────────

    def _build_perf_strip(self):
        strip = tk.Frame(self.root, bg=C["card"], height=72)
        strip.pack(fill="x")
        strip.pack_propagate(False)

        inner = tk.Frame(strip, bg=C["card"])
        inner.place(relx=0.5, rely=0.5, anchor="center")

        def _stat(label, value, color=None, big=False):
            cell = tk.Frame(inner, bg=C["card"], padx=22)
            cell.pack(side="left")
            tk.Label(cell, text=label, bg=C["card"], fg=C["muted"],
                     font=(F_UI, 7, "bold")).pack()
            lbl = tk.Label(cell, text=value, bg=C["card"],
                           fg=color or C["text"],
                           font=(F_MONO, 14 if big else 12, "bold"))
            lbl.pack()
            return lbl

        def _divider():
            tk.Frame(inner, bg=C["border"], width=1, height=40).pack(side="left")

        self._perf_pnl     = _stat("TODAY'S P&L",   "$0.00",  C["sub"],   big=True)
        _divider()
        self._perf_trades  = _stat("TOTAL ORDERS",  "0",      C["text"])
        _divider()
        self._perf_wins    = _stat("WINS",           "0",      C["green"])
        _divider()
        self._perf_losses  = _stat("LOSSES",         "0",      C["red"])
        _divider()
        self._perf_winrate = _stat("WIN RATE",       "0.0%",   C["text"])
        _divider()
        self._perf_best    = _stat("BEST TRADE",     "$0.00",  C["green"])
        _divider()
        self._perf_worst   = _stat("WORST TRADE",    "$0.00",  C["red"])
        _divider()
        self._perf_avg     = _stat("AVG TRADE",      "$0.00",  C["sub"])

    # ─── Header bar ───────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=C["header"], height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Brand
        b = tk.Frame(hdr, bg=C["header"])
        b.pack(side="left", padx=16, pady=0)
        b.place(relx=0, rely=0.5, anchor="w", x=16)
        tk.Label(b, text="◈ XAUUSD", bg=C["header"], fg=C["yellow"],
                 font=(F_UI, 12, "bold")).pack(side="left")
        tk.Label(b, text=" Scalping Bot", bg=C["header"], fg=C["text"],
                 font=(F_UI, 12)).pack(side="left")
        tk.Label(b, text="  M5 · EMA 8/13/21 · RSI · ATR",
                 bg=C["header"], fg=C["muted"],
                 font=(F_UI, 8)).pack(side="left", padx=(10, 0))

        # Right cluster
        r = tk.Frame(hdr, bg=C["header"])
        r.place(relx=1.0, rely=0.5, anchor="e", x=-16)
        self._lbl_conn = tk.Label(r, text="● DISCONNECTED",
                                   bg=C["header"], fg=C["red"],
                                   font=(F_UI, 9, "bold"))
        self._lbl_conn.pack(side="right", padx=(10, 0))
        self._lbl_session = tk.Label(r, text="○ MARKET CLOSED",
                                      bg=C["header"], fg=C["muted"],
                                      font=(F_UI, 9))
        self._lbl_session.pack(side="right", padx=10)
        self._lbl_time = tk.Label(r, text="--:--:-- UTC",
                                   bg=C["header"], fg=C["sub"],
                                   font=(F_MONO, 9))
        self._lbl_time.pack(side="right")

        # State badge — centered
        self._lbl_state = tk.Label(hdr, text="● STOPPED",
                                    bg=C["header"], fg=C["STOPPED"],
                                    font=(F_UI, 11, "bold"))
        self._lbl_state.place(relx=0.5, rely=0.5, anchor="center")

    # ─── Left main area ───────────────────────────────────────────────────────

    def _build_main(self, parent):
        # Row 1: Account + Market side by side
        row1 = tk.Frame(parent, bg=C["bg"])
        row1.pack(fill="x")

        acc_outer = tk.Frame(row1, bg=C["border"], padx=1, pady=1)
        acc_outer.pack(side="left", fill="both", expand=True, padx=(0, 6))
        acc = tk.Frame(acc_outer, bg=C["card"])
        acc.pack(fill="both", expand=True)
        self._section_title(acc, "ACCOUNT OVERVIEW")
        self._bal_val  = _row(acc, "Balance",        label_w=16)
        self._eq_val   = _row(acc, "Equity",         label_w=16)
        self._pnl_val  = _row(acc, "Daily P&L",      label_w=16)
        self._loss_val = _row(acc, "Consec. Losses", label_w=16)
        _spacer(acc)

        mkt_outer = tk.Frame(row1, bg=C["border"], padx=1, pady=1)
        mkt_outer.pack(side="left", fill="both", expand=True, padx=(0, 6))
        mkt = tk.Frame(mkt_outer, bg=C["card"])
        mkt.pack(fill="both", expand=True)
        self._section_title(mkt, "MARKET")
        self._price_val  = _row(mkt, "XAUUSD",  label_w=12)
        self._spread_val = _row(mkt, "Spread",   label_w=12)
        self._atr_val    = _row(mkt, "ATR (14)", label_w=12)
        _spacer(mkt)

        bot_outer = tk.Frame(row1, bg=C["border"], padx=1, pady=1)
        bot_outer.pack(side="left", fill="both", expand=True)
        bot = tk.Frame(bot_outer, bg=C["card"])
        bot.pack(fill="both", expand=True)
        self._section_title(bot, "BOT STATE")
        self._state_val   = _row(bot, "Phase",    label_w=14)
        self._signal_val  = _row(bot, "Signal",   label_w=14)
        self._trades_val  = _row(bot, "Positions",label_w=14)
        _spacer(bot)

        # Row 2: Indicators strip
        ind_outer = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
        ind_outer.pack(fill="x", pady=(8, 0))
        ind = tk.Frame(ind_outer, bg=C["card"])
        ind.pack(fill="both")
        self._section_title(ind, "INDICATORS")

        strip = tk.Frame(ind, bg=C["card"])
        strip.pack(fill="x", padx=14, pady=(4, 12))

        self._box_ema8  = self._ind_box(strip, "EMA 8",   "--")
        self._box_ema13 = self._ind_box(strip, "EMA 13",  "--")
        self._box_ema21 = self._ind_box(strip, "EMA 21",  "--")
        self._box_rsi   = self._ind_box(strip, "RSI (14)","--")
        self._box_atr   = self._ind_box(strip, "ATR (14)","--")
        self._box_sig   = self._ind_box(strip, "Signal",  "--", wide=True)

        for box in (self._box_ema8, self._box_ema13, self._box_ema21,
                    self._box_rsi, self._box_atr, self._box_sig):
            box["f"].pack(side="left", padx=(0, 6))

        # Row 3: Filters + Controls side by side
        row3 = tk.Frame(parent, bg=C["bg"])
        row3.pack(fill="x", pady=(8, 0))

        # Filters
        flt_outer = tk.Frame(row3, bg=C["border"], padx=1, pady=1)
        flt_outer.pack(side="left", fill="both", expand=True, padx=(0, 8))
        flt = tk.Frame(flt_outer, bg=C["card"])
        flt.pack(fill="both", expand=True)
        self._section_title(flt, "ENTRY FILTERS  —  ALL 7 MUST PASS")

        grid = tk.Frame(flt, bg=C["card"])
        grid.pack(fill="x", padx=14, pady=(4, 12))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._fw: Dict[str, dict] = {}
        for i, (key, (label, tip)) in enumerate(FILTERS.items()):
            r, c = divmod(i, 2)
            w = self._filter_pill(grid, label, tip)
            w["f"].grid(row=r, column=c, sticky="ew",
                        padx=(0, 4) if c == 0 else 0, pady=2)
            self._fw[key] = w

        # Controls
        ctrl_outer = tk.Frame(row3, bg=C["border"], padx=1, pady=1, width=210)
        ctrl_outer.pack(side="right", fill="y")
        ctrl_outer.pack_propagate(False)
        ctrl = tk.Frame(ctrl_outer, bg=C["card"])
        ctrl.pack(fill="both", expand=True)
        self._section_title(ctrl, "CONTROLS")

        bf = tk.Frame(ctrl, bg=C["card"])
        bf.pack(fill="x", padx=14, pady=(4, 14))

        self._btn_start = self._btn(bf, "▶  START",
                                     C["green"], C["green_dim"],
                                     command=self._start)
        self._btn_start.pack(fill="x", pady=(0, 6))

        self._btn_stop = self._btn(bf, "■  STOP",
                                    C["red"], C["red_dim"],
                                    command=self._stop, state="disabled")
        self._btn_stop.pack(fill="x", pady=(0, 6))

        self._btn_close = self._btn(bf, "⚠  CLOSE ALL",
                                     C["yellow"], C["yellow_bg"],
                                     fg=C["yellow"],
                                     command=self._close_all)
        self._btn_close.pack(fill="x")

        # Row 4: Open positions
        pos_outer = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
        pos_outer.pack(fill="both", expand=True, pady=(8, 0))
        pos = tk.Frame(pos_outer, bg=C["card"])
        pos.pack(fill="both", expand=True)
        self._section_title(pos, "OPEN POSITIONS")

        cols = ("Ticket", "Direction", "Lots", "Entry", "SL", "TP1", "TP2", "TP3", "P&L")
        widths = [75, 70, 55, 76, 76, 76, 76, 76, 76]
        self._tree = ttk.Treeview(pos, columns=cols, show="headings",
                                   height=4, style="T.Treeview")
        for c, w in zip(cols, widths):
            self._tree.heading(c, text=c)
            self._tree.column(c, width=w, anchor="center", minwidth=w)
        self._tree.tag_configure("long",  foreground=C["green"])
        self._tree.tag_configure("short", foreground=C["red"])
        self._tree.pack(fill="both", expand=True, padx=14, pady=(4, 12))

    # ─── Right sidebar (log) ──────────────────────────────────────────────────

    def _build_sidebar(self, parent):
        outer = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=C["card"])
        inner.pack(fill="both", expand=True)
        self._section_title(inner, "EVENT LOG")

        self._log = scrolledtext.ScrolledText(
            inner, bg=C["input"], fg=C["sub"],
            font=(F_MONO, 8), wrap="word", state="disabled",
            relief="flat", insertbackground=C["text"],
            selectbackground=C["blue_bg"],
        )
        self._log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._log.tag_configure("ts",    foreground=C["muted"])
        self._log.tag_configure("INFO",  foreground=C["sub"])
        self._log.tag_configure("WARN",  foreground=C["yellow"])
        self._log.tag_configure("ERROR", foreground=C["red"])
        self._log.tag_configure("TRADE", foreground=C["green"])

    # ─── Widget helpers ───────────────────────────────────────────────────────

    def _section_title(self, parent, text: str):
        f = tk.Frame(parent, bg=C["card"])
        f.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(f, text=text, bg=C["card"], fg=C["sub"],
                 font=(F_UI, 7, "bold")).pack(side="left")
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=14)

    def _ind_box(self, parent, label: str, value: str, wide=False) -> dict:
        w = 105 if wide else 88
        f = tk.Frame(parent, bg=C["border"], padx=1, pady=1, width=w)
        inn = tk.Frame(f, bg=C["input"], padx=10, pady=8)
        inn.pack(fill="both")
        tk.Label(inn, text=label, bg=C["input"], fg=C["muted"],
                 font=(F_UI, 7, "bold")).pack(anchor="w")
        v = tk.Label(inn, text=value, bg=C["input"], fg=C["text"],
                     font=(F_MONO, 11, "bold"))
        v.pack(anchor="w")
        return {"f": f, "inn": inn, "v": v}

    def _filter_pill(self, parent, label: str, tip: str) -> dict:
        f = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
        inn = tk.Frame(f, bg=C["input"], padx=8, pady=5)
        inn.pack(fill="both")
        dot = tk.Label(inn, text="●", bg=C["input"], fg=C["muted"],
                       font=(F_UI, 9))
        dot.pack(side="right")
        lf = tk.Frame(inn, bg=C["input"])
        lf.pack(side="left", fill="x", expand=True)
        nl = tk.Label(lf, text=label, bg=C["input"], fg=C["sub"],
                      font=(F_UI, 9, "bold"), anchor="w")
        nl.pack(anchor="w")
        tl = tk.Label(lf, text=tip, bg=C["input"], fg=C["muted"],
                      font=(F_UI, 7), anchor="w")
        tl.pack(anchor="w")
        return {"f": f, "inn": inn, "dot": dot, "nl": nl, "tl": tl, "lf": lf}

    def _btn(self, parent, text, color, bg_color,
             command=None, fg=None, state="normal") -> tk.Button:
        btn = tk.Button(parent, text=text,
                        bg=bg_color, fg=fg or color,
                        activebackground=color, activeforeground=C["bg"],
                        font=(F_UI, 10, "bold"), relief="flat", bd=0,
                        padx=10, pady=9, cursor="hand2",
                        command=command, state=state)
        def enter(e):
            if str(btn["state"]) == "normal":
                btn.config(bg=color, fg=C["bg"])
        def leave(e):
            if str(btn["state"]) == "normal":
                btn.config(bg=bg_color, fg=fg or color)
        btn.bind("<Enter>", enter)
        btn.bind("<Leave>", leave)
        return btn

    # ─── Public updaters ──────────────────────────────────────────────────────

    def update_connection(self, connected: bool):
        self._lbl_conn.config(
            text="● CONNECTED" if connected else "● DISCONNECTED",
            fg=C["green"] if connected else C["red"],
        )

    def update_state(self, state: str):
        self._lbl_state.config(text=f"● {state}", fg=C.get(state, C["text"]))
        self._state_val.config(text=state, fg=C.get(state, C["text"]))

    def update_session(self, name: str):
        if name == "CLOSED":
            self._lbl_session.config(text="○ MARKET CLOSED", fg=C["muted"])
        else:
            self._lbl_session.config(text=f"◉ {name.upper()}", fg=C["green"])

    def update_time(self, utc_time: datetime):
        self._lbl_time.config(text=utc_time.strftime("%H:%M:%S UTC"))

    def update_account(self, balance: float, equity: float,
                        daily_pnl: float, consec_losses: int):
        self._bal_val.config(text=f"${balance:,.2f}", fg=C["text"])
        self._eq_val.config(text=f"${equity:,.2f}", fg=C["text"])
        self._pnl_val.config(text=f"${daily_pnl:+,.2f}",
                              fg=C["green"] if daily_pnl >= 0 else C["red"])
        lc = C["red"] if consec_losses >= 2 else C["yellow"] if consec_losses == 1 else C["text"]
        self._loss_val.config(text=f"{consec_losses} / 3", fg=lc)

    def update_indicators(self, data: dict):
        self._box_ema8["v"].config( text=f"{data.get('ema_fast',   0):,.2f}", fg=C["text"])
        self._box_ema13["v"].config(text=f"{data.get('ema_medium', 0):,.2f}", fg=C["text"])
        self._box_ema21["v"].config(text=f"{data.get('ema_slow',   0):,.2f}", fg=C["text"])

        rsi = data.get("rsi", 50)
        rsi_c = C["red"] if rsi > 70 else C["green"] if rsi < 30 else C["text"]
        self._box_rsi["v"].config(text=f"{rsi:.1f}", fg=rsi_c)
        self._box_atr["v"].config(text=f"{data.get('atr', 0):.3f}", fg=C["text"])

        spread = data.get("spread", 0)
        self._spread_val.config(text=f"{spread:.0f} pts",
                                 fg=C["red"] if spread > 25 else C["green"])
        self._atr_val.config(text=f"{data.get('atr', 0):.3f}", fg=C["text"])
        price = data.get("price", 0)
        if price:
            self._price_val.config(text=f"{price:,.2f}", fg=C["text"])

        direction = data.get("direction", "NEUTRAL")
        if direction == "LONG":
            self._box_sig["v"].config(text="▲ LONG", fg=C["green"])
            self._box_sig["inn"].config(bg=C["green_bg"])
            self._signal_val.config(text="▲ LONG", fg=C["green"])
        elif direction == "SHORT":
            self._box_sig["v"].config(text="▼ SHORT", fg=C["red"])
            self._box_sig["inn"].config(bg=C["red_bg"])
            self._signal_val.config(text="▼ SHORT", fg=C["red"])
        else:
            self._box_sig["v"].config(text="— WAIT", fg=C["muted"])
            self._box_sig["inn"].config(bg=C["input"])
            self._signal_val.config(text="— WAIT", fg=C["muted"])

    def update_filters(self, filters: dict):
        for key, w in self._fw.items():
            ok = bool(filters.get(key, False))
            bg = C["green_bg"] if ok else C["input"]
            dot_c = C["green"] if ok else C["muted"]
            nl_c  = C["green"] if ok else C["sub"]
            w["inn"].config(bg=bg)
            w["dot"].config(fg=dot_c, bg=bg)
            w["nl"].config( fg=nl_c,  bg=bg)
            w["tl"].config( bg=bg)
            w["lf"].config( bg=bg)

    def update_positions(self, trades: list):
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._trades_val.config(text=str(len(trades)), fg=C["text"])
        for t in trades:
            tag = "long" if t.direction == "LONG" else "short"
            self._tree.insert("", "end", tags=(tag,), values=(
                t.ticket,
                "▲ BUY" if t.direction == "LONG" else "▼ SELL",
                f"{t.lot_remaining:.2f}",
                f"{t.entry_price:.2f}",
                f"{t.sl:.2f}",
                f"{t.tp1:.2f}",
                f"{t.tp2:.2f}",
                f"{t.tp3:.2f}",
                "--",
            ))

    def update_performance(self, stats: dict):
        """Update the today's performance strip with stats from RiskManager."""
        pnl   = stats.get("total_pnl",    0.0)
        total = stats.get("total_trades",  0)
        wins  = stats.get("wins",          0)
        loss  = stats.get("losses",        0)
        wr    = stats.get("win_rate",      0.0)
        best  = stats.get("best_trade",    0.0)
        worst = stats.get("worst_trade",   0.0)
        avg   = stats.get("avg_trade",     0.0)

        pnl_color = C["green"] if pnl > 0 else C["red"] if pnl < 0 else C["sub"]
        self._perf_pnl.config(    text=f"${pnl:+,.2f}",  fg=pnl_color)
        self._perf_trades.config( text=str(total),         fg=C["text"])
        self._perf_wins.config(   text=str(wins),          fg=C["green"] if wins > 0 else C["muted"])
        self._perf_losses.config( text=str(loss),          fg=C["red"]   if loss > 0 else C["muted"])
        self._perf_winrate.config(text=f"{wr:.1f}%",
                                   fg=C["green"] if wr >= 50 else C["red"] if wr > 0 else C["muted"])
        self._perf_best.config(   text=f"${best:+,.2f}",  fg=C["green"] if best > 0 else C["muted"])
        self._perf_worst.config(  text=f"${worst:+,.2f}", fg=C["red"]   if worst < 0 else C["muted"])
        avg_c = C["green"] if avg > 0 else C["red"] if avg < 0 else C["sub"]
        self._perf_avg.config(    text=f"${avg:+,.2f}",   fg=avg_c)

    def log(self, message: str, level: str = "INFO"):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._q.put((ts, message, level))

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while not self._q.empty():
                ts, msg, level = self._q.get_nowait()
                self._log.config(state="normal")
                self._log.insert("end", f"[{ts}] ", "ts")
                self._log.insert("end", msg + "\n", level)
                self._log.see("end")
                self._log.config(state="disabled")
        except Exception:
            pass
        self.root.after(150, self._poll_log)

    def _start(self):
        self._btn_start.config(state="disabled", bg=C["green_dim"])
        self._btn_stop.config(state="normal")
        threading.Thread(target=self._on_start, daemon=True).start()

    def _stop(self):
        self._btn_stop.config(state="disabled")
        self._btn_start.config(state="normal", bg=C["green_dim"])
        self._on_stop()

    def _close_all(self):
        self._on_close_all()

    def schedule(self, ms: int, fn):
        self.root.after(ms, fn)

    def run(self):
        self.root.mainloop()
