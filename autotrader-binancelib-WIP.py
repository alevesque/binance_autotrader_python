import asyncio
import io
import json, os, csv, time, threading, queue, math, subprocess
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import List, Dict, Optional
import traceback
import statistics
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
from binance.client import Client
from binance import ThreadedWebsocketManager
from binance.exceptions import BinanceAPIException, BinanceOrderException, BinanceRequestException
from tkcalendar import DateEntry
import argparse, sys, signal
import matplotlib
import matplotlib.dates as mdates
import matplotlib.ticker
from matplotlib.lines import Line2D
import pandas as pd
import numpy as np

# Choose backend before any matplotlib submodule is imported.
# Headless mode uses the file-output Agg backend; GUI mode uses TkAgg.
matplotlib.use("Agg" if "--headless" in sys.argv else "TkAgg")
from matplotlib.figure import Figure
if "--headless" not in sys.argv:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import mplcursors
from datetime import datetime, timedelta, date as _date
from collections import defaultdict
import pprint

# ----------------------------
# Config and logging utilities
# ----------------------------

@dataclass
class AppConfig:
    # Exchange credentials and base settings
    api_key: str = ""
    api_secret: str = ""
    base_asset: str = "BTC"
    quote_asset: str = "USDT"
    starting_cash: float = 100000.0
    starting_cash_live: float = 1100
    fee_rate: float = 0.001
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = True
    send_real_orders: bool = False
    # Live trading defaults (map directly to GUI widget defaults)
    live_symbol: str = "BNBUSD"
    live_interval: str = "1h"
    # Shared strategy params used by both live and backtest
    rsi_period: int = 2
    rsi_mode: str = "simple"
    rsi_oversold: float = 31.0
    rsi_overbought: float = 70.0
    # Backtest defaults
    backtest_symbol: str = "BNBUSD"
    backtest_interval: str = "1h"
    backtest_lookback: str = "1 year ago UTC"
    # Order execution
    order_type: str = "limit"           # "limit" or "trailing_stop"
    trailing_stop_pct: float = 0.1      # trail trigger distance in % (e.g. 0.1 = 0.1%)
    # Trailing-stop limit order settings (used when order_type == "trailing_stop")
    trail_limit_offset_pct: float = 0.1  # extra % beyond trigger for limit price; total slippage budget = trailing_stop_pct + trail_limit_offset_pct
    trail_limit_timeout_s: int = 60      # seconds to wait for fill before widening
    trail_limit_widen_pct: float = 0.05  # % to widen limit per retry
    trail_limit_max_retries: int = 1     # retries before falling back to market
    # Crash recovery — persisted buy price so P&L survives restarts
    buy_price: Optional[float] = None
    # Number of recent closed candles to replay through on_price() on startup
    warmup_replay_count: int = 5


class ConfigLoader:
    @staticmethod
    def load(path: str = "config.json") -> AppConfig:
        if not os.path.exists(path):
            return AppConfig()
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                print(f"Config parse error ({e}) — using defaults.")
                return AppConfig()
        return AppConfig(
            api_key=data.get("api_key", ""),
            api_secret=data.get("api_secret", ""),
            base_asset=data.get("base_asset", "BTC"),
            quote_asset=data.get("quote_asset", "USDT"),
            starting_cash=float(data.get("starting_cash", 100000.0)),
            starting_cash_live=float(data.get("starting_cash_live", 1100.0)),
            fee_rate=float(data.get("fee_rate", 0.001)),
            telegram_bot_token=data.get("telegram_bot_token", ""),
            telegram_chat_id=data.get("telegram_chat_id", ""),
            telegram_enabled=bool(data.get("telegram_enabled", True)),
            send_real_orders=bool(data.get("send_real_orders", False)),
            live_symbol=data.get("live_symbol", "BNBUSD"),
            live_interval=data.get("live_interval", "1h"),
            rsi_period=int(data.get("rsi_period", 2)),
            rsi_mode=data.get("rsi_mode", "simple"),
            rsi_oversold=float(data.get("rsi_oversold", 31.0)),
            rsi_overbought=float(data.get("rsi_overbought", 70.0)),
            backtest_symbol=data.get("backtest_symbol", "BNBUSD"),
            backtest_interval=data.get("backtest_interval", "1h"),
            backtest_lookback=data.get("backtest_lookback", "1 year ago UTC"),
            order_type=data.get("order_type", "limit"),
            trailing_stop_pct=float(data.get("trailing_stop_pct", 0.1)),
            trail_limit_offset_pct=float(data.get("trail_limit_offset_pct", 0.1)),
            trail_limit_timeout_s=int(data.get("trail_limit_timeout_s", 60)),
            trail_limit_widen_pct=float(data.get("trail_limit_widen_pct", 0.05)),
            trail_limit_max_retries=int(data.get("trail_limit_max_retries", 1)),
            buy_price=float(data["buy_price"]) if data.get("buy_price") is not None else None,
            warmup_replay_count=int(data.get("warmup_replay_count", 5)),
        )

    @staticmethod
    def save_field(key: str, value, path: str = "config.json") -> None:
        """Persist a single key/value to config.json without touching other fields."""
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {}
            data[key] = value
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"ConfigLoader.save_field: failed to write {key}={value}: {e}")

class OrderLogger:
    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self.log_dir = log_dir
        # Weekly rotating filenames — a new file is started each calendar week
        week_str = datetime.now().strftime("%Y-W%W")
        self.session_log_path = os.path.join(log_dir, f"session_{week_str}.log")
        self.orders_csv_path  = os.path.join(log_dir, f"orders_{week_str}.csv")
        if not os.path.exists(self.orders_csv_path) or os.path.getsize(self.orders_csv_path) == 0:
            with open(self.orders_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "side", "size_base", "price",
                    "fee_quote", "trade_type", "profit_quote", "equity",
                ])

    def log_event(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(self.session_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
        print(msg)

    def log_trade(self, side: str, size_base: float, price: float,
                  fee_quote: float, trade_type: str,
                  profit_quote: float = 0.0) -> None:
        """Append one trade row to orders.csv."""
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(self.orders_csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                ts, side, f"{size_base:.8f}", f"{price:.6f}",
                f"{fee_quote:.8f}", trade_type, f"{profit_quote:.8f}",
            ])

    def log_equity_snapshot(self, equity: float) -> None:
        """Append a SNAPSHOT row to orders.csv with the current account equity.

        SNAPSHOT rows have side='SNAPSHOT' and zeroed trade fields so that the
        summary-image builder can distinguish them from BUY/SELL rows while
        keeping all time-series data in a single file.
        """
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(self.orders_csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([ts, "SNAPSHOT", "0", "0", "0", "", "0", f"{equity:.6f}"])

    def send_telegram_message(self, bot_token: str, chat_id: str, text: str):
        """Sends a plain-text Telegram message."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {"chat_id": chat_id, "text": text}
            requests.post(url, data=payload, timeout=5)
        except Exception as e:
            print(f"Telegram send error: {e}")

    def send_telegram_photo(self, bot_token: str, chat_id: str,
                            image_bytes: bytes, caption: str = "") -> Optional[int]:
        """Send a PNG image to Telegram; returns the message_id for pinning."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("summary.png", image_bytes, "image/png")},
                timeout=30,
            )
            data = resp.json()
            if data.get("ok"):
                return data["result"]["message_id"]
            print(f"Telegram photo error: {data.get('description', 'unknown')}")
        except Exception as e:
            print(f"Telegram photo send error: {e}")
        return None

    def pin_telegram_message(self, bot_token: str, chat_id: str,
                             message_id: int) -> None:
        """Pin a Telegram message (bot must be admin in the chat/channel)."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/pinChatMessage"
            requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "disable_notification": True,
                },
                timeout=10,
            )
        except Exception as e:
            print(f"Telegram pin error: {e}")

# ----------------------------
# Daily summary image builder
# ----------------------------

def build_daily_summary_image(
    log_dir: str,
    starting_equity: float,
    symbol: str,
    summary_date: "_date",   # the calendar day being summarised (yesterday)
) -> bytes:
    """Render a mobile-optimised portrait summary PNG and return the raw bytes.

    Layout (portrait 9×14 in, 120 DPI):
        Title bar
        ──────────────────────────────
        Stats panel   (day + all-time)
        ──────────────────────────────
        Equity curve  (x = hours of summary_date, y = equity, autoscaled)

    Data source: ALL orders_YYYY-WNN.csv files found in log_dir.
    Only trades on summary_date appear in the chart and 'day' statistics;
    all-time figures aggregate every available CSV file.
    """
    # ── Read all orders_YYYY-WNN.csv files: split into trades and equity snapshots ─
    all_trades:   list = []   # side == BUY or SELL
    equity_snaps: list = []   # side == SNAPSHOT
    try:
        for fname in sorted(os.listdir(log_dir)):
            if not (fname.startswith("orders_") and fname.endswith(".csv")):
                continue
            fpath = os.path.join(log_dir, fname)
            with open(fpath, "r", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    try:
                        ts   = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                        side = row.get("side", "")
                        if side == "SNAPSHOT":
                            eq_val = float(row.get("equity", 0) or 0)
                            if eq_val > 0:
                                equity_snaps.append({"ts": ts, "equity": eq_val})
                        else:
                            all_trades.append({
                                "ts":         ts,
                                "side":       side,
                                "size":       float(row.get("size_base", 0) or 0),
                                "price":      float(row.get("price", 0) or 0),
                                "trade_type": row.get("trade_type", ""),
                                "profit":     float(row.get("profit_quote", 0) or 0),
                            })
                    except (ValueError, KeyError):
                        continue
    except OSError:
        pass
    all_trades.sort(key=lambda x: x["ts"])
    equity_snaps.sort(key=lambda x: x["ts"])

    # ── Partition: summary day vs. all-time (orders) ─────────────────────────
    day_trades = [t for t in all_trades if t["ts"].date() == summary_date]

    all_sells  = [t for t in all_trades if t["side"] == "SELL"]
    all_profit = sum(t["profit"] for t in all_sells)

    day_sells  = [t for t in day_trades if t["side"] == "SELL"]
    day_buys   = [t for t in day_trades if t["side"] == "BUY"]
    day_wins   = [t for t in day_sells  if t["profit"] > 0]
    day_losses = [t for t in day_sells  if t["profit"] <= 0]
    day_profit = sum(t["profit"] for t in day_sells)
    win_rate   = 100.0 * len(day_wins) / len(day_sells) if day_sells else 0.0

    best_trade  = max(day_sells, key=lambda x: x["profit"]) if day_sells else None
    worst_trade = min(day_sells, key=lambda x: x["profit"]) if day_sells else None

    day_start_dt = datetime(summary_date.year, summary_date.month, summary_date.day,  0,  0,  0)
    day_end_dt   = datetime(summary_date.year, summary_date.month, summary_date.day, 23, 59, 59)

    # Day-start equity: last snapshot strictly before summary_date midnight
    pre_snaps    = [s for s in equity_snaps if s["ts"].date() < summary_date]
    day_snaps    = [s for s in equity_snaps if s["ts"].date() == summary_date]

    if pre_snaps:
        day_start_eq = pre_snaps[-1]["equity"]
    else:
        # Fallback: synthetic computation from orders history
        pre_day_profit = sum(t["profit"] for t in all_sells if t["ts"].date() < summary_date)
        day_start_eq   = starting_equity + pre_day_profit

    day_end_eq     = day_snaps[-1]["equity"]  if day_snaps  else day_start_eq
    current_equity = equity_snaps[-1]["equity"] if equity_snaps else (starting_equity + all_profit)

    # Equity change for the day (from real snapshots when available)
    day_equity_delta = day_end_eq - day_start_eq
    day_profit_pct   = 100.0 * day_equity_delta / day_start_eq if day_start_eq else 0.0
    all_profit_pct   = 100.0 * (current_equity - starting_equity) / starting_equity if starting_equity else 0.0

    # ── Equity curve for summary_date ────────────────────────────────────────
    if day_snaps:
        # Real account snapshots: plot them directly as a line
        eq_times  = [day_start_dt] + [s["ts"] for s in day_snaps] + [day_end_dt]
        eq_values = [day_start_eq] + [s["equity"] for s in day_snaps] + [day_snaps[-1]["equity"]]
        # Marker y-positions: map each day_trade to nearest preceding equity snapshot
        eq_at_trade: list = []
        for t in day_trades:
            preceding = [s["equity"] for s in day_snaps if s["ts"] <= t["ts"]]
            eq_at_trade.append(preceding[-1] if preceding else day_start_eq)
    else:
        # Fallback: step-function derived from profit_quote in orders.csv
        eq_times  = [day_start_dt]
        eq_values = [day_start_eq]
        running_eq = day_start_eq
        for t in day_trades:
            if t["side"] == "SELL":
                running_eq += t["profit"]
                eq_times.append(t["ts"])
                eq_values.append(running_eq)
        eq_times.append(day_end_dt)
        eq_values.append(running_eq)
        eq_at_trade = []
        r2 = day_start_eq
        for t in day_trades:
            if t["side"] == "SELL":
                r2 += t["profit"]
            eq_at_trade.append(r2)

    running_eq = eq_values[-1]  # used in stats display

    # ── Colour palette ────────────────────────────────────────────────────────
    C_BG     = "#0d1117"
    C_PANEL  = "#161b22"
    C_TEXT   = "#e6edf3"
    C_MUTED  = "#8b949e"
    C_GREEN  = "#3fb950"
    C_RED    = "#f85149"
    C_GOLD   = "#e3b341"
    C_BLUE   = "#58a6ff"
    C_GRID   = "#21262d"
    C_BORDER = "#30363d"

    def _pc(v):  return C_GREEN if v >= 0 else C_RED
    def _ps(v):  return "+" if v >= 0 else ""

    # ── Figure — portrait, mobile-optimised (9×14 in, 120 DPI → 1080×1680 px) ─
    fig = Figure(figsize=(9, 14), dpi=120)
    fig.patch.set_facecolor(C_BG)

    gs = fig.add_gridspec(2, 1, height_ratios=[1, 1.6],
                          hspace=0.08,
                          left=0.06, right=0.97, top=0.93, bottom=0.06)
    ax_s = fig.add_subplot(gs[0])   # stats panel
    ax_c = fig.add_subplot(gs[1])   # equity chart

    fig.text(
        0.5, 0.97,
        f"AUTOTRADER  |  {symbol}  |  {summary_date.strftime('%Y-%m-%d')}",
        ha="center", va="top",
        color=C_TEXT, fontsize=16, fontweight="bold", fontfamily="monospace",
    )

    # ── Stats panel ───────────────────────────────────────────────────────────
    ax_s.set_facecolor(C_PANEL)
    for sp in ax_s.spines.values():
        sp.set_edgecolor(C_BORDER); sp.set_linewidth(1.5)
    ax_s.set_xticks([]); ax_s.set_yticks([])

    rows = [
        ("DAILY PERFORMANCE",                     None,                                        C_GOLD,  14, True),
        ("─" * 34,                                None,                                        C_BORDER, 8, False),
        ("Completed trades (today)",              str(len(day_sells)),                         C_TEXT,  13, False),
        ("  Buys placed",                         str(len(day_buys)),                          C_MUTED, 13, False),
        ("  Wins / Losses",                       f"{len(day_wins)} / {len(day_losses)}",      C_TEXT,  13, False),
        ("  Win rate",                            f"{win_rate:.1f}%",                          _pc(win_rate - 50), 13, False),
        ("─" * 34,                                None,                                        C_BORDER, 8, False),
        ("PROFIT  (today)",                       None,                                          C_GOLD,  13, True),
        ("  Account change",                     f"{_ps(day_equity_delta)}{day_equity_delta:.4f}", _pc(day_equity_delta), 13, False),
        ("  Percent",                            f"{_ps(day_profit_pct)}{day_profit_pct:.2f}%", _pc(day_equity_delta), 13, False),
        ("  Realised P&L",                       f"{_ps(day_profit)}{day_profit:.4f}",         _pc(day_profit), 13, False),
        ("─" * 34,                               None,                                          C_BORDER, 8, False),
        ("PROFIT  (all time)",                   None,                                          C_GOLD,  13, True),
        ("  Realised P&L",                       f"{_ps(all_profit)}{all_profit:.4f}",          _pc(all_profit), 13, False),
        ("  Percent",                            f"{_ps(all_profit_pct)}{all_profit_pct:.2f}%", _pc(all_profit), 13, False),
        ("─" * 34,                               None,                                          C_BORDER, 8, False),
        ("EQUITY",                               None,                                          C_GOLD,  13, True),
        ("  Start of day",                       f"{day_start_eq:,.2f}",                        C_TEXT,  13, False),
        ("  End of day",                         f"{day_end_eq:,.2f}",                          _pc(day_equity_delta), 13, False),
        ("  Current (account)",                  f"{current_equity:,.2f}",                      _pc(current_equity - starting_equity), 13, False),
    ]
    if best_trade:
        rows += [
            ("─" * 34,                            None,                                        C_BORDER, 8, False),
            ("BEST TRADE (today)",                None,                                        C_GOLD,  13, True),
            ("  P&L",                             f"+{best_trade['profit']:.4f}",              C_GREEN, 13, False),
            ("  Signal",                          best_trade["trade_type"],                    C_MUTED, 11, False),
        ]
    if worst_trade and worst_trade is not best_trade:
        rows += [
            ("WORST TRADE (today)",               None,                                        C_GOLD,  13, True),
            ("  P&L",                             f"{worst_trade['profit']:.4f}",              C_RED,   13, False),
            ("  Signal",                          worst_trade["trade_type"],                   C_MUTED, 11, False),
        ]

    n = len(rows)
    y0, dy = 0.97, 0.97 / (n + 1)
    for i, (label, value, color, fs, bold) in enumerate(rows):
        y = y0 - i * dy
        kw = dict(transform=ax_s.transAxes, va="top", color=color,
                  fontsize=fs, fontweight="bold" if bold else "normal",
                  fontfamily="monospace")
        if value is None:
            ax_s.text(0.5, y, label, ha="center", **kw)
        else:
            ax_s.text(0.04, y, label, ha="left", **kw)
            kw["color"] = color
            ax_s.text(0.96, y, value, ha="right", **kw)

    # ── Equity chart ──────────────────────────────────────────────────────────
    ax_c.set_facecolor(C_PANEL)
    for sp in ax_c.spines.values():
        sp.set_edgecolor(C_BORDER); sp.set_linewidth(1.5)
    ax_c.tick_params(colors=C_MUTED, labelsize=11)
    ax_c.grid(True, color=C_GRID, linestyle="--", linewidth=0.6, alpha=0.8)
    ax_c.set_title("Equity Curve", color=C_TEXT, pad=8,
                   fontsize=14, fontweight="bold", fontfamily="monospace")
    ax_c.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:,.2f}")
    )

    # x-axis: hours of summary_date (fixed range 00:00–23:59, tick every 2 h)
    ax_c.set_xlim(day_start_dt, day_end_dt)
    ax_c.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax_c.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_c.tick_params(axis="x", rotation=30)

    if len(eq_times) >= 2:
        # Real snapshots → smooth line; fallback step-function → steps-post
        _using_snaps = bool(day_snaps)
        _draw = "default" if _using_snaps else "steps-post"
        ax_c.plot(eq_times, eq_values, color=C_BLUE, linewidth=2.5,
                  drawstyle=_draw, zorder=3)

        # y-axis: autoscale to the day's equity range with padding
        _lo  = min(eq_values)
        _hi  = max(eq_values)
        _pad = (_hi - _lo) * 0.10 or 1.0
        ax_c.set_ylim(_lo - _pad, _hi + _pad)

        _fb_kw = {"step": "post"} if not _using_snaps else {}
        ax_c.fill_between(eq_times, _lo - _pad, eq_values,
                          alpha=0.13, color=C_BLUE, zorder=2, **_fb_kw)

        # Day-start equity reference line
        ax_c.axhline(day_start_eq, color=C_MUTED, linestyle=":",
                     linewidth=1.0, alpha=0.55, zorder=1)

        # Trade markers (BUY ▲ green, SELL profit ▼ green, SELL loss ▼ red)
        for t, eq in zip(day_trades, eq_at_trade):
            if t["side"] == "BUY":
                ax_c.scatter(t["ts"], eq, color=C_GREEN, marker="^",
                             s=100, zorder=6, edgecolors="white", linewidths=0.5)
            else:
                mc = C_GREEN if t["profit"] >= 0 else C_RED
                ax_c.scatter(t["ts"], eq, color=mc, marker="v",
                             s=100, zorder=6, edgecolors="white", linewidths=0.5)
    else:
        ax_c.text(0.5, 0.5, "No trade data for this day",
                  transform=ax_c.transAxes, ha="center", va="center",
                  color=C_MUTED, fontsize=16, fontfamily="monospace")

    legend_elems = [
        Line2D([0], [0], marker="^", color="w", markerfacecolor=C_GREEN,
               markersize=10, label="BUY", linestyle="None"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor=C_GREEN,
               markersize=10, label="SELL (profit)", linestyle="None"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor=C_RED,
               markersize=10, label="SELL (loss)", linestyle="None"),
        Line2D([0], [0], color=C_MUTED, linestyle=":", linewidth=1.5,
               label="Day-start equity"),
    ]
    ax_c.legend(handles=legend_elems, loc="upper left",
                facecolor=C_BG, edgecolor=C_BORDER,
                labelcolor=C_MUTED, fontsize=11, framealpha=0.85)

    # ── Render ────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor=C_BG, bbox_inches="tight")
    buf.seek(0)
    return buf.read()


# ----------------------------
# Binance Exchange
# ----------------------------

class BinanceUSExchange:
    """
    Pure exchange wrapper:
    - time sync
    - safe requests
    - reconnection
    - buy/sell
    - historical prices
    No GUI, no queues, no WebSocket here.
    """
    def __init__(self, api_key, api_secret, base_asset, quote_asset, logger, max_retries=2):
        self.client = Client(api_key, api_secret, tld='us', base_endpoint='')
        # self.client._request = self.debug_wrapper(self.client._request)
        self.base_asset = base_asset
        self.quote_asset = quote_asset
        self.symbol = f"{base_asset}{quote_asset}"
        self.logger = logger
        self.time_offset_ms = 0
        self.max_retries = max_retries
        self.max_slippage_pct = 0.001   # 0.3% default, adjust as needed
        self.info = self._safe_request(self.client.get_symbol_info, symbol=self.symbol)
        self.order_manager = OrderManager(exchange=self, logger=logger)
        self.toggle_trade_flag = False
        self.step = self._parse_lot_step()
        
    def _log(self, msg: str):
        if self.logger:
            self.logger.log_event(msg)
    
    def force_windows_time_resync(self):
        try:
            # need to start process before resync
            subprocess.run(
                ["w32tm", "/register"],
                capture_output=True,
                text=True,
                check=True
            )
            time.sleep(5)
            try:
                subprocess.run(
                    ["net", "start", "w32time"],
                    capture_output=True,
                    text=True,
                    check=True
                )
            except Exception as e:
                print("Error:", str(e))
            time.sleep(5)
            subprocess.run(
                ["w32tm", "/resync"],
                capture_output=True,
                text=True,
                check=True
            )
            print("Windows time resync triggered successfully.")
        except Exception as e:
            self._log(f"Windows time resync failed: {e}")

    
    def sync_time(self):
        """
        Synchronizes local time with Binance server time using RTT compensation.
        """
        time_NOK = True
        sync_attempts = 0
        try:
            while time_NOK:
                sync_attempts += 1
                if sync_attempts > 5:
                    self.toggle_trade_flag = True
                    sync_attempts = 0
                    break
                t0 = int(time.time() * 1000)
                # server = self.client.get_server_time()
                server = requests.get("https://api.binance.us/api/v3/time").json()
                t1 = int(time.time() * 1000)
        
                server_time = server["serverTime"]
                
    
                # Round-trip latency
                rtt = (t1 - t0) // 2
        
                # Corrected server time at t1
                corrected_server_time = server_time + rtt
        
                # Offset = server_time - local_time
                self.time_offset_ms = corrected_server_time - t1
                
                if abs(self.time_offset_ms) > 1200:
                    self._log("Large drift detected. Forcing Windows time resync.")
                    self.force_windows_time_resync()
                    time_NOK = True
                    time.sleep(2)
                else:
                    time_NOK = False
                    self._log(f"TIME SYNC | offset={self.time_offset_ms} ms | rtt={rtt} ms | server time: {server_time} | local time: {time.time() * 1000} | corr local time: {self._timestamp()}")
    
        except Exception as e:
            self._log(f"TIME SYNC ERROR: {e}")
    
    def _timestamp(self) -> int:
        return int(time.time() * 1000 + self.time_offset_ms)

    def debug_wrapper(self, original_request):
        def wrapped(method, uri, signed, force_params=False, **kwargs):
            print("\n=== OUTGOING REQUEST ===")
            print("Method:", method)
            print("URI:", uri)
            print("Signed:", signed)
            print("Params:", kwargs)
            print("========================\n")
            print("Local corrected timestamp:", self._timestamp())
            return original_request(method, uri, signed, force_params, **kwargs)
        return wrapped

    def _safe_request(self, func, *args, **kwargs):
        """
        Wrapper for all Binance API calls.
        Ensures timestamp is preserved and retries use a fresh timestamp.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                # Always refresh timestamp for signed endpoints
                if "timestamp" in kwargs:
                    kwargs["timestamp"] = self._timestamp()
                # kwargs['recvWindow'] = 5000
                # print(f"SAFE REQUEST CALL: {func.__name__}")
                return func(*args, **kwargs)

            except BinanceAPIException as e:
                self._log(f"BinanceAPIException in {func}: {e}")
                print(f"kwargs: {kwargs}")
                if e.code in [-1021, -1022]:
                    self._log("Timestamp error detected. Resyncing time.")
                    self.sync_time()
                time.sleep(1)

            except BinanceOrderException as e:
                self._log(f"BinanceOrderException: {e}")
                return None

            except BinanceRequestException as e:
                self._log(f"BinanceRequestException: {e}")
                time.sleep(2)

            except Exception as e:
                self._log(f"Unexpected error: {e}\n{traceback.format_exc()}")
                time.sleep(2)

            self._log(f"Retrying Binance request (attempt {attempt}/{self.max_retries})")

        self._log("Max retries reached. Attempting full reconnection.")
        self._reconnect_client()
        return None

    def _reconnect_client(self):
        try:
            self._log("Reinitializing Binance client...")
            api_key = self.client.API_KEY
            api_secret = self.client.API_SECRET
            self.client = Client(api_key, api_secret, tld="us", requests_params={"timeout": 5})
            self.sync_time()
            self._log("Reconnection successful.")
        except Exception as e:
            self._log(f"Failed to reconnect: {e}")

    def get_account_snapshot(self):
        """
        Retrieves:
        - cash (free quote asset)
        - position (free base asset)
        Returns a dict or None on failure.
        """
        try:
            # Fetch balances
            account = self._safe_request(
                self.client.get_account,
                timestamp=self._timestamp()
            )
            if not account:
                self._log("ACCOUNT SNAPSHOT ERROR: Failed to fetch account info")
                return None
    
            balances = account.get("balances", [])
            
            # Extract free balances for base + quote assets
            cash = 0.0
            position = 0.0

            for b in balances:
                asset = b.get("asset")
                free_amt = float(b.get("free", 0))

                # Base asset of the trading pair
                if asset == self.base_asset:
                    position = free_amt

                # Quote asset of the trading pair
                if asset == self.quote_asset:
                    cash = free_amt
                    continue
    
            local_readable_timestamp = datetime.fromtimestamp(int(self._timestamp()/1000))
            local_date = local_readable_timestamp.strftime("%Y-%m-%d")
            local_time = local_readable_timestamp.strftime("%H:%M %Z")
            
            # Get current price for equity calculation
            price = None
            if hasattr(self, 'strat') and self.strat and self.strat.prices:
                price = self.strat.prices[-1]
            else:
                ticker = self._safe_request(self.client.get_symbol_ticker, symbol=self.symbol)
                if ticker:
                    price = float(ticker["price"])

            equity = (cash + position * price) if price is not None else None

            snapshot = {
                "cash": cash,
                "position": position,
                "equity": equity,
            }

            equity_str = f"{equity:.2f}" if equity is not None else "N/A"
            self._log(
                f"[ACCOUNT SNAPSHOT] | {local_date} {local_time} | Cash ({self.quote_asset}): {cash} | Position ({self.base_asset}): {position} | Equity: {equity_str}"
            )
            if equity is not None and self.logger is not None:
                self.logger.log_equity_snapshot(equity)

            return snapshot
    
        except Exception as e:
            self._log(f"ACCOUNT SNAPSHOT ERROR: {e}")
            return None
    
    def buy(self, symbol, quantity, use_market=False, orig_price=None, limit_price=None):
        """
        Places a BUY order and handles Binance confirmation response.
        Returns a structured dict with order details or None on failure.

        limit_price: when provided, the limit order is placed at this price
                     (tick-rounded) instead of the current ticker price.
                     Ignored when use_market=True.
        """
        try:
            # Get current price
            ticker = self._safe_request(self.client.get_symbol_ticker, symbol=symbol)
            if not ticker:
                self._log("BUY ERROR: Failed to fetch ticker")
                return None

            current_price = float(ticker["price"])
            order_price = limit_price if (limit_price is not None and not use_market) else current_price
            ok, new_price, new_qty = self.prepare_order(symbol, order_price, quantity)
            if not ok:
                self._log(f"ORDER VALIDATION FAILED: {new_price}")  # new_price holds the error message
                return None
            
            if orig_price is not None:
                # Slippage check: price must not rise above allowed threshold
                max_allowed = orig_price * (1 + self.max_slippage_pct)
        
                if current_price > max_allowed:
                    self._log(
                        f"BUY ABORTED: Slippage too high. "
                        f"Original={orig_price}, Current={current_price}, "
                        f"MaxAllowed={max_allowed}"
                    )
                    return None
            # -------------------------
            # MARKET BUY
            # -------------------------
            if use_market:
                order = self._safe_request(
                    self.client.order_market_buy,
                    symbol=symbol,
                    quantity=new_qty,
                    timestamp=self._timestamp()
                )
                if not order:
                    self._log("BUY ERROR: Market order failed")
                    return None
    
                return self._process_order_confirmation(order, "BUY", current_price)
    
            # -------------------------
            # MAKER-ONLY LIMIT BUY
            # -------------------------
            
            order = self._safe_request(
                self.client.order_limit_buy,
                symbol=symbol,
                price=str(new_price),
                quantity=str(new_qty),
                timeInForce="GTC",
                timestamp=self._timestamp()
            )
            if not order:
                self._log("BUY ERROR: Limit order failed")
                return None
            
            if "orderId" not in order:
                self._log("Order failed: no orderId returned")
                return None
            
            self.order_id = order.get("orderId")
            self.order_manager.register(
                symbol=symbol,
                order_id=self.order_id,
                limit_price=float(order["price"]),
                timestamp=self._timestamp()
            )
            return self._process_order_confirmation(order, "BUY", current_price)

        except Exception as e:
            self._log(f"BUY ERROR: {e}")
            return None

    def sell(self, symbol, quantity, use_market=False, orig_price=None, limit_price=None):
        """
        Places a SELL order and handles Binance confirmation response.
        Returns a structured dict with order details or None on failure.

        limit_price: when provided, the limit order is placed at this price
                     (tick-rounded) instead of the current ticker price.
                     Ignored when use_market=True.
        """
        try:
            ticker = self._safe_request(self.client.get_symbol_ticker, symbol=symbol)
            if not ticker:
                self._log("SELL ERROR: Failed to fetch ticker")
                return None
            current_price = float(ticker["price"])
            order_price = limit_price if (limit_price is not None and not use_market) else current_price
            ok, new_price, new_qty = self.prepare_order(symbol, order_price, quantity)
            if not ok:
                self._log(f"ORDER VALIDATION FAILED: {new_price}")  # new_price holds the error message
                return None
            
            if orig_price is not None:
                # Slippage check: price must not fall below allowed threshold
                min_allowed = orig_price * (1 - self.max_slippage_pct)
        
                if current_price < min_allowed:
                    self._log(
                        f"SELL ABORTED: Slippage too high. "
                        f"Original={orig_price}, Current={current_price}, "
                        f"Min. allowed={min_allowed}"
                    )
                    return None
            # -------------------------
            # MARKET SELL
            # -------------------------
            if use_market:
                order = self._safe_request(
                    self.client.order_market_sell,
                    symbol=symbol,
                    quantity=new_qty,
                    timestamp=self._timestamp()
                )
                
                if not order:
                    self._log("SELL ERROR: Market order failed")
                    return None
                
                return self._process_order_confirmation(order, "SELL", current_price)

            # -------------------------
            # MAKER-ONLY LIMIT SELL
            # -------------------------
            
            order = self._safe_request(
                self.client.order_limit_sell,
                symbol=symbol,
                quantity=new_qty,
                price=str(new_price),
                timeInForce="GTC",
                timestamp=self._timestamp()
            )
            
            # If limit order failed entirely → fallback to market
            if not order:
                self._log("Maker SELL rejected — falling back to MARKET SELL")
                return self._fallback_market_sell(symbol, quantity, current_price)
            
            self.order_id = order.get("orderId")
            self.order_manager.register(
                symbol=symbol,
                order_id=self.order_id,
                limit_price=new_price,
                timestamp=self._timestamp()
            )
            # If Binance returns a REJECTED status → fallback
            status = order.get("status", "")
            if status in ("REJECTED", "EXPIRED", "CANCELED"):
                self._log(f"Maker SELL returned status {status} — falling back to MARKET SELL")
                return self._fallback_market_sell(symbol, quantity, current_price)
    
            # Otherwise, process normally
            return self._process_order_confirmation(order, "SELL", current_price)

        except Exception as e:
            self._log(f"SELL ERROR: {e}")
            return None

    def _fallback_market_sell(self, symbol, quantity, original_price):
        """
        Executes a fallback MARKET SELL with slippage protection.
        Ensures the market price has not moved too far below the original price.
        """
        try:
            # Re-fetch current price before executing market order
            ticker = self._safe_request(self.client.get_symbol_ticker, symbol=symbol)
            if not ticker:
                self._log("FALLBACK SELL ERROR: Could not fetch ticker for slippage check")
                return None
    
            current_price = float(ticker["price"])
            ok, new_price, new_qty = self.prepare_order(symbol, current_price, quantity)
            if not ok:
                self._log("ORDER VALIDATION FAILED 01")
                return None
            
            # Slippage check: price must not fall below allowed threshold
            min_allowed = original_price * (1 - self.max_slippage_pct)
    
            if current_price < min_allowed:
                self._log(
                    f"FALLBACK SELL ABORTED: Slippage too high. "
                    f"Original={original_price}, Current={current_price}, "
                    f"MinAllowed={min_allowed}"
                )
                return None
    
            # Execute market order safely
            order = self._safe_request(
                self.client.order_market_sell,
                symbol=symbol,
                quantity=new_qty,
                timestamp=self._timestamp()
            )
    
            if not order:
                self._log("FALLBACK MARKET SELL FAILED")
                return None
    
            return self._process_order_confirmation(order, "SELL", current_price)
    
        except Exception as e:
            self._log(f"FALLBACK SELL ERROR: {e}")
            return None

    def _process_order_confirmation(self, order, side, market_price):
        """
        Processes Binance order response and extracts:
        - status
        - executed quantity
        - executed price
        - commission
        - fills
        """
        try:
            status = order.get("status", "UNKNOWN")
            executed_qty = float(order.get("executedQty", 0))
            orig_qty = float(order.get("origQty", 0))
            fills = order.get("fills", [])
    
            # Compute weighted-average fill price and total commission from fills.
            # Fall back to market_price (ticker at order time) only when Binance
            # returns no fills (e.g. for GTC limit orders still pending).
            if fills:
                total_cost = 0.0
                total_qty = 0.0
                total_commission = 0.0

                for f in fills:
                    f_price = float(f.get("price", 0))
                    f_qty = float(f.get("qty", 0))
                    total_cost += f_price * f_qty
                    total_qty += f_qty
                    total_commission += float(f.get("commission", 0))

                avg_price = total_cost / total_qty if total_qty > 0 else market_price
            else:
                avg_price = market_price
                total_commission = 0.0

            self._log(
                f"{side} CONFIRMATION | Status: {status} | "
                f"Executed: {executed_qty}/{orig_qty} | "
                f"Avg Price: {avg_price:.3f} | "
                f"Commission: {total_commission}"
            )

            return {
                "side": side,
                "status": status,
                "executed_qty": executed_qty,
                "orig_qty": orig_qty,
                "avg_price": avg_price,
                "commission": total_commission,
                "fills": fills,
                "raw": order,
                "oid": order.get("orderId", 0)
            }
    
        except Exception as e:
            self._log(f"ORDER CONFIRMATION ERROR: {e}")
            return None

    def prepare_order(self, symbol, price, quantity):
        """
        Fully sanitizes and validates price + quantity for Binance.
        Returns (True, new_price, new_qty) or (False, error_message).
        """
        
        # Helper: convert Decimal to Binance-safe string
        def dec_to_str(d: Decimal) -> str:
            s = format(d, 'f')  # fixed-point, no exponent
            return s.rstrip('0').rstrip('.') if '.' in s else s

        info = self.info
        # info = self._safe_request(self.client.get_symbol_info, symbol=symbol)
        if not info:
            return False, "Could not fetch symbol info", ""
    
        price_dec = Decimal(str(price)).normalize()
        qty_dec = Decimal(str(quantity)).normalize()
    
        # -----------------------------
        # 1. Sanitize price + quantity
        # -----------------------------
        step = None
        for f in info["filters"]:
            ftype = f["filterType"]
    
            if ftype == "PRICE_FILTER":
                tick = Decimal(str(f["tickSize"])).normalize()
                if tick == 0 or tick is None:
                    return False, "PRICE_FILTER filter missing", ""
                price_dec = price_dec.quantize(tick, rounding=ROUND_DOWN)
    
            if ftype == "LOT_SIZE":
                step = Decimal(str(f["stepSize"])).normalize()
                if step is None or step == 0:
                    return False, "LOT_SIZE filter missing", ""
                qty_dec = qty_dec.quantize(step, rounding=ROUND_DOWN)
                self.step = step
        # -----------------------------
        # 2. Validate against filters
        # -----------------------------
        for f in info["filters"]:
            ftype = f["filterType"]
    
            # PRICE_FILTER
            if ftype == "PRICE_FILTER":
                min_price = Decimal(f["minPrice"]).normalize()
                max_price = Decimal(f["maxPrice"]).normalize()
                tick = Decimal(f["tickSize"]).normalize()
    
                if price_dec < min_price:
                    return False, f"Price {price_dec} below minPrice {min_price}", ""
                if price_dec > max_price:
                    return False, f"Price {price_dec} above maxPrice {max_price}", ""
    
                # Decimal precision check
                tick_decimals = -tick.as_tuple().exponent
                price_decimals = -price_dec.as_tuple().exponent
                if price_decimals > tick_decimals:
                    return False, f"Price {price_dec} has too many decimals for tickSize {tick}", ""
    
            # LOT_SIZE
            if ftype == "LOT_SIZE":
                min_qty = Decimal(f["minQty"]).normalize()
                max_qty = Decimal(f["maxQty"]).normalize()
    
                if qty_dec < min_qty:
                    return False, f"Quantity {qty_dec} below minQty {min_qty}", ""
                if qty_dec > max_qty:
                    return False, f"Quantity {qty_dec} above maxQty {max_qty}", ""
    
                # Decimal precision check
                step_decimals = -step.as_tuple().exponent
                qty_decimals = -qty_dec.as_tuple().exponent
                if qty_decimals > step_decimals:
                    return False, f"Quantity {qty_dec} has too many decimals for stepSize {step}", ""
               
                # Ensure qty is an exact multiple of stepSize
                try:
                    if (qty_dec / step) % 1 != 0:
                        return False, f"Quantity {qty_dec} is not aligned to stepSize {step}", ""
                except Exception:
                    return False, "Invalid LOT_SIZE stepSize", ""

            # MIN_NOTIONAL
            if ftype == "MIN_NOTIONAL":
                min_notional = Decimal(f["minNotional"]).normalize()
                if price_dec * qty_dec < min_notional:
                    return False, f"Order value {price_dec * qty_dec} below minNotional {min_notional}", ""
    
        # -----------------------------
        # 3. Return sanitized values
        # -----------------------------
        return True, dec_to_str(price_dec), dec_to_str(qty_dec)

    def get_order_status(self, symbol, order_id):
        """
        Returns Binance order status or None on failure.
        """
        try:
            order = self._safe_request(
                self.client.get_order,
                symbol=symbol,
                orderId=order_id,
                timestamp=self._timestamp()
            )
            return order
        except Exception as e:
            self._log(f"ORDER STATUS ERROR: {e}")
            return None
    
    def cancel_order(self, symbol, order_id):
        """
        Cancels an active order.
        """
        try:
            result = self._safe_request(
                self.client.cancel_order,
                symbol=symbol,
                orderId=order_id,
                timestamp=self._timestamp()
            )
            self._log(f"ORDER CANCELED | ID: {order_id}")
            return result
        except Exception as e:
            self._log(f"ORDER CANCEL ERROR: {e}")
            return None
        
    def _parse_lot_step(self):
        """Parse and cache the LOT_SIZE stepSize from symbol info at startup."""
        if not self.info:
            return 0
        for f in self.info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                from decimal import Decimal
                return Decimal(str(f["stepSize"])).normalize()
        return 0

    def get_recent_prices(self, symbol: str, interval: str, limit: int) -> list:
        """Fetch the most recent `limit` closing prices via _safe_request."""
        klines = self._safe_request(
            self.client.get_klines,
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        if not klines:
            return []
        return [float(k[4]) for k in klines]

    def get_recent_klines_full(self, symbol: str, interval: str, limit: int) -> list:
        """Fetch the most recent `limit` klines as candle dicts (full OHLC + timestamps)."""
        klines = self._safe_request(
            self.client.get_klines,
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        if not klines:
            return []
        return [
            {
                "symbol":    symbol,
                "open_time": k[0],
                "close_time": k[6],
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
                "trades": int(k[8]),
            }
            for k in klines
        ]

    def get_historical_klines_raw(self, symbol: str, interval: str, lookback: str) -> list:
        """Fetch full raw klines for a lookback window via _safe_request."""
        return self._safe_request(
            self.client.get_historical_klines,
            symbol=symbol,
            interval=interval,
            start_str=lookback
        ) or []

class OrderManager:
    """
    Tracks all open orders and handles:
    - registration
    - status polling
    - stale timeout cancellation
    - price deviation cancellation
    - cleanup when filled or canceled
    """

    def __init__(self, exchange=None, logger=None, interval: str = "1h"):
        self.exchange = exchange
        self.open_orders = {}
        self.closed_orders = {}
        self.logger = logger if logger is not None else OrderLogger()
        self.interval = interval  # trading interval; set at live-trading startup

    @staticmethod
    def _parse_interval_to_seconds(interval: str) -> int:
        units = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}
        try:
            return int(interval[:-1]) * units.get(interval[-1], 3600)
        except (ValueError, IndexError):
            return 3600

    def register(self, symbol, order_id, limit_price, timestamp):
        self.open_orders[order_id] = {
            "symbol": symbol,
            "limit_price": limit_price,
            "timestamp": timestamp,
            "status": "NEW"
        }
        return

    def update_status(self, order_id):
        if order_id not in self.open_orders:
            self.logger.log_event(f"OrderManager.update_status: order {order_id} not in open_orders")
            return None, ""
        order = self.exchange.get_order_status(
            self.open_orders[order_id]["symbol"],
            order_id
        )
        if not order:
            self.logger.log_event("Order failed: no order returned")
            return None, ""
        if "status" not in order:
            self.logger.log_event("Order failed: no order status returned")
            return None, ""

        status = order.get("status")
        side = order.get("side")        
        # print(f"Order status response for order {order_id}:")
        # print(f"Type: {order.get('type')} {order.get('side')} | Status: {status} | Price: {float(order.get('price')):.3f} | Qty: {order.get('origQty')}")
        self.open_orders[order_id]["status"] = status
        return status, side

    def cancel(self, order_id):
        try:
            if order_id not in self.open_orders:
                self.logger.log_event(f"OrderManager.cancel: order {order_id} not in open_orders")
                return False
            symbol = self.open_orders[order_id]["symbol"]
            self.closed_orders[order_id] = self.open_orders[order_id]
            self.exchange.cancel_order(symbol, order_id)
            self.open_orders.pop(order_id, None)
            return True
        except Exception as e:
            self.logger.log_event(f"Cancel order error: {e}")
            traceback.print_exc()
            return False

    def check_stale(self, order_id) -> bool:
        timeout_seconds = int(self._parse_interval_to_seconds(self.interval) * 0.9)
        if order_id not in self.open_orders:
            return False
        placed = self.open_orders[order_id]["timestamp"]
        if (time.time() - float(placed)/1000) > timeout_seconds:
            status, side = self.update_status(order_id)
            if status in ("NEW", "PARTIALLY_FILLED"): # and side == "BUY": # do we want to cancel sell orders too, to give them a chance to sell again, or just wait indefinitely for them to fill?
                self.logger.log_event(f"ORDER MANAGER: Canceling stale order {order_id}")
                resp = self.cancel(order_id)
                return resp
        return False

    def check_price_deviation(self, order_id, max_dev_pct=0.002):
        if order_id not in self.open_orders:
            return
        symbol = self.open_orders[order_id]["symbol"]
        limit_price = float(self.open_orders[order_id]["limit_price"])

        ticker = self.exchange._safe_request(
            self.exchange.client.get_symbol_ticker,
            symbol=symbol
        )
        if not ticker:
            return

        current_price = float(ticker["price"])
        deviation = (current_price - limit_price) / limit_price

        # deviation up is OK for sell and down is OK for buy - TODO update this
        if deviation > 0 and abs(deviation) > max_dev_pct:
            status, side = self.update_status(order_id)
            if side == "BUY":
                if status in ("NEW", "PARTIALLY_FILLED"):
                    self.logger.log_event(
                        f"ORDER MANAGER: Price deviation too high for {order_id}. Canceling."
                    )
                    self.cancel(order_id)
        if deviation < 0 and abs(deviation) > max_dev_pct:
            status, side = self.update_status(order_id)
            if side == "SELL":
                if status in ("NEW", "PARTIALLY_FILLED"):
                    self.logger.log_event(
                        f"ORDER MANAGER: Price deviation too high for {order_id}. Canceling."
                    )
                    self.cancel(order_id)
        return

    def sweep(self):
        """
        Called periodically to:
        - update statuses
        - cancel stale orders
        - cancel price‑invalid orders
        - remove filled/canceled orders
        """
        to_remove = []

        for order_id in list(self.open_orders.keys()):
            status, _ = self.update_status(order_id)
            
            if status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                to_remove.append(order_id)
                continue

            resp = self.check_stale(order_id)
            if not resp:
                self.check_price_deviation(order_id)

        for oid in to_remove:
            self.closed_orders[oid] = self.open_orders[oid]
            self.open_orders.pop(oid, None)
            
        return
    
class SimpleRSI:
    """
    Unified RSI engine supporting:
    - simple RSI
    - SMA-smoothed RSI
    """

    @staticmethod
    def compute(prices: list, period: int, mode: str = "simple"):
        if len(prices) < period + 1:
            return None

        # Compute raw gains/losses
        gains = []
        losses = []

        for i in range(1, period + 1):
            # original line that gave +4m profit
            # reinstate below if fixed RSI not giving same results
            # change = prices[-i] - prices[-i - 1]
            
            # line that copilot "fixed" that gave shoddy results all day on 1/26/26
            change = prices[-period - 1 + i] - prices[-period - 2 + i]

            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(-change)
                
        # --- SIMPLE RSI (default) ---
        if mode == "simple":
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period

        # --- SMA-SMOOTHED RSI ---
        elif mode == "sma":
            # Smooth gains/losses with SMA before computing RS
            if len(gains) < period or len(losses) < period:
                return None

            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period

        # --- EMA-SMOOTHED RSI / WILDER'S RMA (TradingView / Binance) ---
        elif mode in ("ema", "wilder"):
            # "wilder" is the canonical label for this algorithm.
            # "ema" is kept as an alias for backward compatibility.
            #
            # Algorithm: Wilder's Smoothed Moving Average (SMMA / RMA)
            #   alpha = 1/period  (NOT the standard EMA alpha of 2/(period+1))
            #   recursive: avg[i] = (avg[i-1] * (period-1) + value[i]) / period
            #   seeded:    avg[period] = SMA(values[0..period-1])
            #
            # This is the exact algorithm used by TradingView's ta.rsi() and
            # therefore by Binance's built-in RSI chart indicator.
            # Requires the full price history to properly seed the EMA.
            if len(prices) < period + 1:
                return None

            all_gains = []
            all_losses = []
            for i in range(1, len(prices)):
                change = prices[i] - prices[i - 1]
                all_gains.append(max(change, 0.0))
                all_losses.append(max(-change, 0.0))

            # SMA seed from the first `period` changes
            avg_gain = sum(all_gains[:period]) / period
            avg_loss = sum(all_losses[:period]) / period

            # Wilder's RMA: run forward through the full price history
            for i in range(period, len(all_gains)):
                avg_gain = (avg_gain * (period - 1) + all_gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + all_losses[i]) / period

        # --- TRADINGVIEW RSI (lukaszbinden/rsi_tradingview) ---
        elif mode == "tv":
            # Direct port of https://github.com/lukaszbinden/rsi_tradingview/blob/main/rsi.py
            # Uses pandas ewm with alpha=1/period and adjust=True (weighted-sum form).
            # This differs from the recursive Wilder RMA in how early bars are weighted
            # but converges to the same values given sufficient history.
            if len(prices) < period + 1:
                return None

            series = pd.Series(prices, dtype=float)
            delta = series.diff()

            up   = delta.clip(lower=0).ewm(alpha=1/period, adjust=True).mean()
            down = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=True).mean()

            last_up   = up.iloc[-1]
            last_down = down.iloc[-1]

            if last_up == 0:
                return 0.0
            if last_down == 0:
                return 100.0
            return round(100 - (100 / (1 + last_up / last_down)), 2)

        else:
            raise ValueError(f"Unknown RSI mode: {mode}")

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # print(f"rs: {rs}, avg_gain: {avg_gain}, avg_loss: {avg_loss}, RSI: {rsi}")
        return rsi
    
# ----------------------------
# Strategies
# ----------------------------

class StrategyBase:
    name = "Base"

    def __init__(
            self, 
            rsi_period,
            rsi_mode,
            rsi_os,
            rsi_ob,
            exchange=None,
            symbol='BTCUSDT',
            multiplier=2,
            trade_size_base=0.01,
            interval="1h",
            bband_period = 14,
            bband_mult = 2,
            timeout = 999999,
            logger=None,
            fee_rate=0.001,
            warmup_replay_count=5
            ):
        self.period = rsi_period
        self.mode=rsi_mode
        self.oversold=rsi_os
        self.overbought=rsi_ob
        self.interval = interval
        self.trade_size_base = trade_size_base
        self.macd_values = []
        self.signal_values = []
        self.buy_time = 0
        self.buy_size = None
        self.rsi = []
        self.prices: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.buy_price = None
        self.last_order_type = "SELL"   # "BUY" or "SELL"
        self.exchange = exchange
        self.symbol = symbol
        self.order = None
        self._order_lock = threading.Lock()  # guards reads/writes of self.order across threads
        self.last_rsi = None
        # self.macd_fast = app.bt_macd_fast.get()
        # self.macd_slow = app.bt_macd_slow.get()
        # self.macd_signal = app.bt_macd_signal.get()
        self.bband_period = bband_period
        self.bband_multiplier = bband_mult
        self.timeout = timeout
        self.warmup_bars = 500
        self.warmup_replay_count = warmup_replay_count
        self.fee_rate = fee_rate
        self.logger = logger if logger is not None else OrderLogger()
        # These must be ready before _load_initial_klines() because the
        # warmup replay calls on_price(), which appends to them.
        self.candles = []
        self.k_values = []   # KDJ K line
        self.d_values = []   # KDJ D line
        self.j_values = []   # KDJ J line
        self.last_eval_time = -99
        self._load_initial_klines()
        self.ema_score = None
        self.buy_trigger = None
        self.atr_series = []
        self.trend_filter = None
        self.prev_trend_filter = None
        self.secondary_strategy_state = "FLAT"
        self.mr_cooldown_until = -1
        self.pft_per_strat = defaultdict(lambda: defaultdict(float))


        
    def _load_initial_klines(self):
        try:
            klines = self.exchange.get_recent_klines_full(
                self.symbol, self.interval, self.warmup_bars
            )
            if not klines:
                self.logger.log_event(f"Warmup load returned no candles for {self.symbol}")
                return

            # The REST API always appends the currently-open (unclosed) candle as
            # the last element — drop it so we only work with closed candles.
            klines = klines[:-1]

            # Seed all but the last N candles without logging.
            # on_price() will add the final N (printing close messages for each).
            replay_count = min(self.warmup_replay_count, len(klines) - 1)
            seed_klines   = klines[:-replay_count]
            replay_klines = klines[len(seed_klines):]

            for k in seed_klines:
                self.prices.append(float(k["close"]))
                self.highs.append(k["high"])
                self.lows.append(k["low"])

            self.logger.log_event(
                f"Loaded {len(klines)} warmup candles for {self.symbol} — "
                f"replaying last {len(replay_klines)} to console:"
            )

            # Replay through on_price so the close messages (RSI, BB, KDJ, …)
            # are printed exactly as they would appear during live trading.
            for k in replay_klines:
                self.on_price(k)

            # Reset any signal that fired during replay — the live loop has not
            # started yet and we don't want a stale order queued up.
            with self._order_lock:
                self.order = None
            self.last_order_type = "SELL"

        except Exception as e:
            self.logger.log_event(f"Warmup candle load failed: {e}")
    
    def _ema(self, prices, period):
        if len(prices) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # seed: SMA of first `period` values
        for p in prices[period:]:            # apply smoothing only to values after the seed window
            ema = p * k + ema * (1 - k)
        return ema

    # def _macd(self):
    #     if len(self.prices) < self.macd_slow:
    #         return None, None
    #     fast_ema = self._ema(self.prices, self.macd_fast)
    #     slow_ema = self._ema(self.prices, self.macd_slow)
    #     if fast_ema is None or slow_ema is None:
    #         return None, None
    #     macd = fast_ema - slow_ema
    #     self.macd_values.append(macd)
    #     if len(self.macd_values) < self.macd_signal:
    #         return macd, None
    #     signal = self._ema(self.macd_values, self.macd_signal)
    #     self.signal_values.append(signal)
    #     return macd, signal

    # def compute_rsi(self, live=False):
    #     return SimpleRSI.compute(self.prices, self.period, mode=self.mode)

    def _bands(self):
        if len(self.prices) < self.bband_period:
            return None, None, None
        window = self.prices[-self.bband_period:]
        ma = sum(window) / self.bband_period

        variance = sum((p - ma) ** 2 for p in window) / self.bband_period
        std = variance ** 0.5

        upper = ma + self.bband_multiplier * std
        lower = ma - self.bband_multiplier * std
        return lower, ma, upper

    def _update_kdj(self, length=9, k_smooth=3, d_smooth=3):
        """
        KDJ indicator with adjustable parameters:
            length     → RSV lookback (default 9)
            k_smooth   → smoothing factor for K (default 3)
            d_smooth   → smoothing factor for D (default 3)
        """
    
        # Need enough candles for RSV
        if len(self.prices) < length:
            self.k_values.append(None)
            self.d_values.append(None)
            self.j_values.append(None)
            return
    
        # -----------------------------
        # 1. RSV (uses LENGTH)
        # -----------------------------
        window_high = max(self.highs[-length:])
        window_low = min(self.lows[-length:])
    
        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = (self.prices[-1] - window_low) / (window_high - window_low) * 100
    
        # -----------------------------
        # 2. K smoothing (uses K%)
        # -----------------------------
        prev_k = self.k_values[-1] if self.k_values and self.k_values[-1] is not None else 50.0
        alpha_k = 1 / k_smooth
        k = (1 - alpha_k) * prev_k + alpha_k * rsv
    
        # -----------------------------
        # 3. D smoothing (uses D%)
        # -----------------------------
        prev_d = self.d_values[-1] if self.d_values and self.d_values[-1] is not None else 50.0
        alpha_d = 1 / d_smooth
        d = (1 - alpha_d) * prev_d + alpha_d * k
    
        # -----------------------------
        # 4. J line
        # -----------------------------
        j = 3 * k - 2 * d
    
        self.k_values.append(k)
        self.d_values.append(d)
        self.j_values.append(j)
    
    def atr(self, candles, period=14):
        """
        Stateful ATR using Wilder's smoothing.
        Updates self.atr_series automatically.
        """
    
        if len(candles) < period + 1:
            return None
    
        # Compute TR for the newest candle
        high = candles[-1]["high"]
        low = candles[-1]["low"]
        prev_close = candles[-2]["close"]
    
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
    
        # First ATR value uses simple average
        if len(self.atr_series) < 1:
            # Build initial ATR from scratch
            trs = []
            for i in range(1, period + 1):
                h = candles[-i]["high"]
                l = candles[-i]["low"]
                pc = candles[-i - 1]["close"]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    
            atr = sum(trs) / period
            self.atr_series.append(atr)
            return atr
    
        # Wilder's smoothing for subsequent ATR values
        prev_atr = self.atr_series[-1]
        atr = (prev_atr * (period - 1) + tr) / period
    
        self.atr_series.append(atr)
        return atr
    
    def trend_filter_long(self):
        """
        Returns True if the trend conditions allow RSI(2) long entries:
        
        1. EMA(9) > EMA(21) > EMA(50)
        2. EMA(21) slope > 0
        3. ATR(14) > SMA(ATR, 20)
        """
        try:
            # --- Ensure we have enough data ---
            if not len(self.prices) > 60:   # safe buffer
                return False
            # --- Compute EMAs ---
            ema9  = self._ema(self.prices, 9)
            ema21 = self._ema(self.prices, 21)
            ema50 = self._ema(self.prices, 50)
            if ema9 is None or ema21 is None or ema50 is None:
                return False
            # --- EMA stack condition ---
            # if not (ema9 > ema21 > ema50): # very restrictive, relaxed to next line
            if not (ema21 > ema50):
                return False
            
            # Soft short-term alignment
            if ema9 is not None and ema9 < ema21: 
                return False
            # --- EMA21 slope (over lookback bars) ---
            lookback = 5  # 25 minutes on 5m
            prev_ema21 = self._ema(self.prices[:-lookback], 21)
            if prev_ema21 is None:
                return False
            ema21_slope = (ema21 - prev_ema21) / lookback

            if ema21_slope <= 0:
                return False

        
            # ATR SMA(20)
            atr_series = self.atr_series  # you should maintain this list
            if len(atr_series) < 20:
                return False
        
            atr14 = sum(atr_series[-14:]) / 14
            atr_sma20 = sum(atr_series[-20:]) / 20
        
            # if atr14 <= atr_sma20:
            #     return False
            if atr14 < 0.7 * atr_sma20:
                return False

            # --- All conditions satisfied ---
            return True
        
        except Exception as e:
            print(f"Trend filter error: {e}")
            return False    

    def regime_compression(self,
                       ema_fast=9, ema_mid=21, ema_slow=50,
                       atr_len=14, atr_sma_len=20,
                       ema_slope_lookback=6,
                       ema_slope_atr_frac=0.05,
                       ema_spread_atr_frac=0.25) -> bool:
        if len(self.prices) < max(ema_slow + 5, 80):
            return False
        if len(self.candles) < atr_len + 2:
            return False
        if len(self.atr_series) < atr_sma_len:
            return False
    
        ema_f = self._ema(self.prices, ema_fast)
        ema_m = self._ema(self.prices, ema_mid)
        ema_s = self._ema(self.prices, ema_slow)
        if ema_f is None or ema_m is None or ema_s is None:
            return False
    
        # ATR (use latest already-updated ATR series)
        atr = self.atr_series[-1] if self.atr_series else None
        if atr is None or atr <= 0:
            return False
    
        atr_sma = sum(self.atr_series[-atr_sma_len:]) / atr_sma_len
        vol_suppressed = atr < atr_sma
    
        # EMA21 slope over lookback (approx by recomputing on truncated price list)
        if len(self.prices) < ema_mid + ema_slope_lookback + 5:
            return False
        ema_m_prev = self._ema(self.prices[:-ema_slope_lookback], ema_mid)
        if ema_m_prev is None:
            return False
        ema_slope_per_bar = (ema_m - ema_m_prev) / ema_slope_lookback
        slope_flat = abs(ema_slope_per_bar) <= (ema_slope_atr_frac * atr)
    
        ema_spread = max(ema_f, ema_m, ema_s) - min(ema_f, ema_m, ema_s)
        spread_compressed = ema_spread <= (ema_spread_atr_frac * atr)
    
        return bool(vol_suppressed and slope_flat and spread_compressed)


    def on_price(self, candle: dict): # (self, price=None, time=None, prices=None, candle: dict = {}):
        try:
            with self._order_lock:
                self.order = None
            if not candle:
                return None
            close = candle["close"]
            high = candle["high"]
            low = candle["low"]
            self.prices.append(float(close))
            self.highs.append(high)
            self.lows.append(low)
            self.candles.append(candle)
            
            candle_time = candle["close_time"]
            if candle_time == self.last_eval_time:
                return
            self.last_eval_time = candle_time

            # Update indicators every candle once enough OHLC exists
            # _ = self.atr(self.candles, period=14)
            self._update_kdj()


            # enforce data quality: don't overload the array and dont do anything with insufficient data
            if len(self.prices) < self.period + 1:
                return None  # not enough data yet
            max_needed = self.period + 500
            if len(self.prices) > max_needed:
                self.prices = self.prices[-max_needed:]

            # attempt to prevent situation where youre getting BS signals off of basically duplicate klines because there's no trading volume
            if self.prices[-1] == self.prices[-2]:
                return None
            
            # TODO: where do these go?
            # self.mr_cooldown_until = len(self.prices) + 24  # 24 bars = 2 hours on 5m
            # if len(self.prices) < getattr(self, "mr_cooldown_until", -1):
            #     return (None, False)

            last_price = self.prices[-1]
            
            # Always compute all four variants for display; active mode drives signals.
            rsi_simple = SimpleRSI.compute(self.prices, self.period, mode="simple")
            rsi_ema    = SimpleRSI.compute(self.prices, self.period, mode="ema")
            rsi_wilder = SimpleRSI.compute(self.prices, self.period, mode="wilder")
            rsi_tv     = SimpleRSI.compute(self.prices, self.period, mode="tv")
            if self.mode == "simple":
                rsi = rsi_simple
            elif self.mode in ("ema", "wilder"):
                rsi = rsi_ema   # ema and wilder share the same computation
            elif self.mode == "tv":
                rsi = rsi_tv
            else:
                rsi = SimpleRSI.compute(self.prices, self.period, mode=self.mode)
            self.last_rsi = rsi
            self.rsi.append(rsi)

            if last_price is None:
                return None

            lower, mid, upper = self._bands()
            if lower is None:
                return (None, False)

            local_readable_timestamp = datetime.fromtimestamp(int(candle['close_time']/1000))
            local_date = local_readable_timestamp.strftime("%Y-%m-%d")
            local_time = local_readable_timestamp.strftime("%H:%M %Z")
            _rsi_s  = f"{rsi_simple:>6.2f}" if rsi_simple is not None else "   N/A"
            _rsi_e  = f"{rsi_ema:>6.2f}"    if rsi_ema    is not None else "   N/A"
            _rsi_w  = f"{rsi_wilder:>6.2f}" if rsi_wilder is not None else "   N/A"
            _rsi_tv = f"{rsi_tv:>6.2f}"     if rsi_tv     is not None else "   N/A"
            msg = f"[CLOSE: {local_date}, {local_time}] {self.symbol} Price: {last_price:.2f} | RSI(s): {_rsi_s} | RSI(e): {_rsi_e} | RSI(w): {_rsi_w} | RSI(tv): {_rsi_tv} | BB_L: {lower:>10.3f} | BB_M: {mid:>10.3f} | BB_U: {upper:>10.3f} | "
            if len(self.prices) > 55:
                msg += f" K: {self.k_values[-1]:>5.2f} |"
                msg += f" D: {self.d_values[-1]:>5.2f} |"
                msg += f" J: {self.j_values[-1]:>5.2f} |"
                
            
            # apply trend filter
            # self.prev_trend_filter = self.trend_filter
            # self.trend_filter = self.trend_filter_long()
            
            _ema21 = self._ema(self.prices, 21)
            _ema55 = self._ema(self.prices, 55)
            _ema_cmp = (_ema21 > _ema55) if (_ema21 is not None and _ema55 is not None) else "N/A"
            msg += f" EMA7>99: {_ema_cmp} |"
            
            self.logger.log_event(msg)
            
            # if not self.trend_filter or not self.prev_trend_filter:
            #     return None
            
            if self.last_order_type != "BUY":
                
                trade_type, buy_bool = self.buy_condition(last_price, candle_time)
                if buy_bool:
                    self.buy_price = last_price
                    self.buy_time = candle_time
                    self.last_order_type = "BUY"
                    with self._order_lock:
                        self.order = {"side": "BUY", "trade_type": trade_type}
                    self.buy_type = trade_type
                    return True

            if self.last_order_type != "SELL":

                trade_type, sell_bool = self.sell_condition(last_price, candle_time)
                if sell_bool:
                    self.last_order_type = "SELL"
                    with self._order_lock:
                        self.order = {"side": "SELL", "trade_type": trade_type}
                    self.sell_type = trade_type
                    return True
                else:
                    return None
                    
            return None
        
        except Exception as e:
            self.logger.log_event(f"Error in on_price: {e}")
            traceback.print_exc()

    def buy_condition(self, price: float = None, time=0) -> bool:
        return (None, True)

    def sell_condition(self, price: float = None, time=0) -> bool:
        return (None, True)


class BTRSIStrategy(StrategyBase):
    name = "BTRSI"

    def buy_condition(self, price: float = None, trade_time=0) -> bool:
        
        if self.rsi[-1] is not None:
            _ema21 = self._ema(self.prices, 21)
            _ema55 = self._ema(self.prices, 55)
            if self.rsi[-1] < self.oversold and _ema21 is not None and _ema55 is not None and _ema21 > _ema55:
                return ("RSI2", True)

        return (None, False)

    def sell_condition(self, price: float = None, trade_time=0):
        if not self.buy_price:
            self.buy_price = price

        if self.rsi[-1] is not None and self.rsi[-1] > self.overbought:
            return ("Overbought", True)

        if self.buy_size and self.buy_price:
            buy_cost = self.buy_size * self.buy_price
            sell_cost = self.buy_size * price
            fee = self.fee_rate * sell_cost
            if sell_cost > (buy_cost + 2.5*fee):
                return ("Take Profit", True)

        # if sell_cost > (buy_cost*1.003):
        #     selltype = "Take Profit"
        #     print("SELL", selltype, trade_delta)
        #     return (selltype, True)
        
        # if self.j_values[-2] > self.k_values[-2] and self.j_values[-1] <= self.k_values[-1] and trade_delta > 0:
        #         selltype = "KDJ Crossdown"
        #         print("SELL", selltype, trade_delta)
        #         return (selltype, True)
            
        # if trade_delta/self.buy_price < -0.015:
        #     selltype = "Stop loss"
        #     print("SELL", selltype, trade_delta/self.buy_price, -0.01*self.buy_price)
        #     return (selltype, True)
        
        # if self.candles[-1]['close_time'] > self.buy_time+3600*1000*48 and trade_delta < 0:
        #     selltype = "Timeout"
        #     print("SELL", selltype, trade_delta)
        #     return (selltype, True)
        # if self.candles[-1]['close_time'] > self.buy_time+3600*1000*24 and 100*abs(price - self.buy_price)/self.buy_price < 2:
        #     selltype = "Long Timeout"
        #     print("SELL", selltype, trade_delta)
        #     return (selltype, True)
        # elif self.candles[-1]['close_time'] > self.buy_time+3600*1000*4 and (self.k_values[-1] - self.d_values[-1]) < 10 and 100*abs(price - self.buy_price)/self.buy_price < 2:
        #     selltype = "Short Timeout"
        #     print("SELL", selltype, trade_delta)
        #     return (selltype, True)    
        
        return (None, False)


class LiveRSIStrategy(StrategyBase):
    name = "LiveRSI"

    def buy_condition(self, price: float = None, time=0) -> bool:
        
        # original buy condition, disabled because testing double zero and 2x under buy conds
        # rsi = self.rsi[-1]
        # if rsi is not None and rsi != 0:
        #     if rsi < self.oversold:
        #         # TODO return this to True. ONly False for testing purposes after low rsi buy conditions keep losing. Check klines and rethink normal buy/sell conditions maybe
        #         return False
        #     else:
        #         return False
        if len(self.rsi) > 2:
            # if self.ema_score <= 0.1:
                # if round(self.rsi[-2]) == 0 and round(self.rsi[-1]) == 0: # needs rounding, may be double 0.00 on term but maybe not actually 0s TODO
                #     self.buy_trigger = "double zero"
                #     return True
                # elif self.rsi[-2] < self.oversold and self.rsi[-1] < self.oversold:
                #     self.buy_trigger = "2x under"
                #     return (self.buy_trigger, True)
            r1, r2, r3 = self.rsi[-1], self.rsi[-2], self.rsi[-3]
            if r1 is not None and r2 is not None and r3 is not None:
                if r3 < self.oversold and r2 < self.oversold and r1 < self.oversold:
                    self.buy_trigger = "triple low"
                    return (self.buy_trigger, True)
        return (None, False)

    def sell_condition(self, price: float = None, time=0):

        # if len(self.rsi) > 2: 
        #     if self.rsi[-2] > self.overbought and self.rsi[-1] > self.overbought and self.buy_trigger == "2x under":
        #         self.buy_trigger = None
        #         return ("2x under", True)
        
        # if self.buy_price is not None: 
            # if self.rsi[-1] > 90 and price > self.buy_price and self.buy_trigger != "2x under": # or time since buy is > something and loss is < some %?
            #     self.buy_trigger = None    
            #     return ("double 0", True)
        if self.rsi[-1] is not None and round(self.rsi[-1]) == 100:
            buy_trigger = self.buy_trigger
            self.buy_trigger = None    
            return (buy_trigger, True)
            
        # TODO temporarily disabled next 2 lines to test conditions in real world
        #     if rsi > self.overbought:
        #         return ("indicator", True)
            # elif time > (self.buy_time + timeout if self.buy_time else 200 + timeout):
            #     return ("timeout", True)
        return (None, False)

class CandleEventBus:
    def __init__(self):
        self._subscribers = []  # list of callables

    def subscribe(self, callback):
        self._subscribers.append(callback)

    def publish(self, candle: dict):
        for cb in self._subscribers:
            try:
                cb(candle)
            except Exception as e:
                print(f"Candle subscriber error: {e}")
# ----------------------------
# Shared live-trading core
# ----------------------------

class _LiveTradingMixin:
    """Shared live-trading loop used by both TradingApp (GUI) and HeadlessRunner (headless).
    Subclasses must provide: strat, exchange, config_data, logger, send_real_orders,
    telegram_enabled, live_running, num_trades, cum_profit, perc_gain.
    Override the hook methods below for GUI-specific behaviour; the defaults
    are no-ops suitable for headless use.
    """

    # ------------------------------------------------------------------
    # Overrideable hooks (defaults are headless-safe no-ops)
    # ------------------------------------------------------------------

    def _on_tick_update(self, price: float, rsi: float, index: int) -> None:
        """Called every loop iteration with the latest price/RSI. Override for GUI."""
        pass

    def _on_account_update(self, cash: float, pos: float, price: float) -> None:
        """Called after each account snapshot on a trade signal. Override for GUI."""
        pass

    def _on_trade_placed(self, side: str, price: float, index: int) -> None:
        """Called immediately after an order is submitted. Override for GUI."""
        pass

    def _on_stop_requested(self) -> None:
        """Called when a clock-sync error triggers a live stop.
        Default sets live_running=False; GUI overrides to schedule via self.after()."""
        self.live_running = False

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def _maybe_send_daily_summary(self) -> None:
        """Build the daily performance image and post+pin it to Telegram."""
        if not (self.telegram_enabled
                and self.config_data.telegram_bot_token
                and self.config_data.telegram_chat_id):
            self.logger.log_event("Daily summary skipped — Telegram not configured.")
            return
        try:
            yesterday = (datetime.now() - timedelta(days=1)).date()
            self.logger.log_event(f"Generating daily summary image for {yesterday}...")
            img_bytes = build_daily_summary_image(
                self.logger.log_dir,
                self.config_data.starting_cash_live,
                self.exchange.symbol,
                yesterday,
            )
            msg_id = self.logger.send_telegram_photo(
                self.config_data.telegram_bot_token,
                self.config_data.telegram_chat_id,
                img_bytes,
                caption=f"Daily Summary — {yesterday.strftime('%Y-%m-%d')}",
            )
            if msg_id:
                self.logger.pin_telegram_message(
                    self.config_data.telegram_bot_token,
                    self.config_data.telegram_chat_id,
                    msg_id,
                )
                self.logger.log_event(
                    f"Daily summary posted and pinned (message_id={msg_id})"
                )
            else:
                self.logger.log_event(
                    "Daily summary image sent (pinning unavailable — "
                    "ensure bot is admin in the channel)."
                )
        except Exception as e:
            self.logger.log_event(f"Daily summary error: {e}")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Trailing stop helper
    # ------------------------------------------------------------------

    def _wait_for_trail(self, side: str, signal_price: float, trail_pct: float) -> Optional[float]:
        """Software trailing stop tracker.

        Polls the live REST ticker every 5 s — intentionally bypasses
        self.strat.prices, which only updates on candle closes (e.g. once per
        hour for a 1 h interval), so the trail reacts to real price movement.

        BUY : tracks the trough downward from signal_price; returns when price
              rises trail_pct above the lowest seen trough.
        SELL: tracks the peak  upward  from signal_price; returns when price
              falls trail_pct below the highest seen peak.

        Cancels (returns None) if live_running goes False or an opposing signal
        arrives in strat.order while waiting.
        """
        def _live_price() -> Optional[float]:
            ticker = self.exchange._safe_request(
                self.exchange.client.get_symbol_ticker,
                symbol=self.exchange.symbol,
            )
            return float(ticker["price"]) if ticker else None

        if side == "BUY":
            ref = signal_price
            self.logger.log_event(
                f"[TRAIL BUY ] tracking trough from {signal_price:.4f}  (trail={trail_pct*100:.3f}%)"
            )
            while self.live_running:
                time.sleep(20) # 20 seconds strikes a fine balance between minimizing slippage and matching the relative order of magnitude of the rate of change of prices, which in practice dont update more than once a minute or so.
                with self.strat._order_lock:
                    new_order = self.strat.order
                if new_order and new_order.get("side") != side:
                    self.logger.log_event("[TRAIL BUY ] cancelled — opposing signal received")
                    return None
                current = _live_price()
                if current is None:
                    continue  # ticker fetch failed — retry next iteration
                if current < ref:
                    ref = current
                    self.logger.log_event(f"[TRAIL BUY ] new trough: {ref:.4f}")
                if current >= ref * (1 + trail_pct):
                    self.logger.log_event(
                        f"[TRAIL BUY ] triggered @ {current:.4f}  (trough={ref:.4f})"
                    )
                    return current
        else:  # SELL
            ref = signal_price
            self.logger.log_event(
                f"[TRAIL SELL] tracking peak from {signal_price:.4f}  (trail={trail_pct*100:.3f}%)"
            )
            while self.live_running:
                time.sleep(20)
                with self.strat._order_lock:
                    new_order = self.strat.order
                if new_order and new_order.get("side") != side:
                    self.logger.log_event("[TRAIL SELL] cancelled — opposing signal received")
                    return None
                current = _live_price()
                if current is None:
                    continue  # ticker fetch failed — retry next iteration
                if current > ref:
                    ref = current
                    self.logger.log_event(f"[TRAIL SELL] new peak: {ref:.4f}")
                if current <= ref * (1 - trail_pct):
                    self.logger.log_event(
                        f"[TRAIL SELL] triggered @ {current:.4f}  (peak={ref:.4f})"
                    )
                    return current
                if current > signal_price*1.005: # 1.005 is chosen specifically with BNBUSD where it seems unlikely for many trades to do much better than this
                    self.logger.log_event(
                        f"[TRAIL SELL TAKE PROFIT] triggered @ {current:.4f}  (peak={ref:.4f})"
                    )
                    return current
        return None  # live_running went False while waiting

    # ------------------------------------------------------------------
    # Trailing-stop limit order with widen-and-retry + market fallback
    # ------------------------------------------------------------------

    def _exec_trail_limit(self, side: str, symbol: str, size: float,
                           trigger_price: float) -> Optional[dict]:
        """Place a limit order at trigger ± offset_pct, poll for fill, widen on
        timeout, and fall back to market after trail_limit_max_retries failures.

        For SELL: limit is placed *below* trigger (trigger × (1 − offset)).
        For BUY:  limit is placed *above* trigger (trigger × (1 + offset)).
        Both placements put the order aggressively inside the spread so it acts
        like a near-market order while still getting a bounded execution price.
        Each retry widens the offset by trail_limit_widen_pct; if all retries
        exhaust without a fill the final fallback is a regular market order.
        """
        offset_pct  = self.config_data.trail_limit_offset_pct / 100.0
        timeout_s   = self.config_data.trail_limit_timeout_s
        widen_pct   = self.config_data.trail_limit_widen_pct / 100.0
        max_retries = self.config_data.trail_limit_max_retries

        for attempt in range(max_retries + 1):
            total_offset = offset_pct + attempt * widen_pct
            if side == "BUY":
                limit_price = trigger_price * (1.0 + total_offset)
            else:
                limit_price = trigger_price * (1.0 - total_offset)

            self.logger.log_event(
                f"[TRAIL LIMIT] {side} attempt {attempt + 1}/{max_retries + 1}  "
                f"trigger={trigger_price:.4f}  limit={limit_price:.4f}  "
                f"offset={total_offset * 100:.3f}%"
            )

            if side == "BUY":
                resp = self.exchange.buy(symbol, quantity=size,
                                         orig_price=trigger_price,
                                         limit_price=limit_price)
            else:
                resp = self.exchange.sell(symbol, quantity=size,
                                          orig_price=trigger_price,
                                          limit_price=limit_price)

            if resp is None:
                self.logger.log_event(
                    f"[TRAIL LIMIT] Limit order placement failed on attempt {attempt + 1}")
                continue

            # Immediate fill (limit crossed the spread at submission time)
            if resp["status"] in ("FILLED", "PARTIALLY_FILLED"):
                return resp

            if resp["status"] in ("REJECTED", "EXPIRED", "CANCELED"):
                self.logger.log_event(
                    f"[TRAIL LIMIT] Order status={resp['status']} on attempt {attempt + 1}")
                continue

            # Poll until filled or timeout
            oid      = resp["oid"]
            deadline = time.time() + timeout_s
            filled   = False

            while time.time() < deadline and self.live_running:
                time.sleep(10)
                self.exchange.order_manager.sweep()
                try:
                    status = self.exchange.order_manager.open_orders[oid]["status"]
                except KeyError:
                    try:
                        status = self.exchange.order_manager.closed_orders[oid]["status"]
                    except KeyError:
                        status = "UNKNOWN"

                if status in ("FILLED", "PARTIALLY_FILLED"):
                    # Re-fetch to get weighted-average fill price and commission
                    order_info = self.exchange._safe_request(
                        self.exchange.client.get_order,
                        symbol=symbol,
                        orderId=oid,
                        timestamp=self.exchange._timestamp(),
                    )
                    if order_info:
                        return self.exchange._process_order_confirmation(
                            order_info, side, limit_price)
                    filled = True   # re-fetch failed; use original resp as fallback
                    break
                if status in ("REJECTED", "EXPIRED", "CANCELED"):
                    break

            if filled:
                return resp

            # Timeout: cancel the resting order
            self.logger.log_event(
                f"[TRAIL LIMIT] No fill within {timeout_s}s on attempt {attempt + 1} "
                f"— cancelling order {oid}"
            )
            cancel_result = self.exchange.cancel_order(symbol, oid)
            if cancel_result is None:
                # Cancel may have failed because the order just filled
                order_info = self.exchange._safe_request(
                    self.exchange.client.get_order,
                    symbol=symbol,
                    orderId=oid,
                    timestamp=self.exchange._timestamp(),
                )
                if order_info and order_info.get("status") in ("FILLED", "PARTIALLY_FILLED"):
                    self.logger.log_event(
                        f"[TRAIL LIMIT] Order {oid} filled during cancel — using fill")
                    return self.exchange._process_order_confirmation(
                        order_info, side, limit_price)

            if attempt < max_retries:
                self.logger.log_event(
                    f"[TRAIL LIMIT] Widening by {widen_pct * 100:.3f}% and retrying …")

        # All attempts exhausted — unconditional market fallback
        self.logger.log_event(
            f"[TRAIL LIMIT] {max_retries + 1} attempt(s) without fill "
            f"— falling back to market order at trigger={trigger_price:.4f}"
        )
        if side == "BUY":
            return self.exchange.buy(symbol, quantity=size,
                                     orig_price=trigger_price, use_market=True)
        else:
            return self.exchange.sell(symbol, quantity=size,
                                      orig_price=trigger_price, use_market=True)

    # ------------------------------------------------------------------
    # Canonical live-trading loop
    # ------------------------------------------------------------------

    def _live_loop(self, symbol: str, interval: str) -> None:
        """Core live-trading loop, shared by GUI and headless modes."""
        index = 0
        order = None
        _last_summary_date: Optional[str] = None   # tracks the last day a summary was sent

        while self.live_running:
            try:
                time.sleep(5)

                # ── Daily summary: fire once on the first loop tick after midnight ──
                _today_str = datetime.now().strftime("%Y-%m-%d")
                if _last_summary_date is not None and _today_str != _last_summary_date:
                    self._maybe_send_daily_summary()
                _last_summary_date = _today_str

                if self.exchange.toggle_trade_flag:
                    self.logger.log_event("Live trading automatically stopped due to clock sync NOK!")
                    self.exchange.toggle_trade_flag = False
                    self._on_stop_requested()
                    continue  # while condition exits if live_running is now False

                with self.strat._order_lock:
                    if self.strat.order:
                        order = self.strat.order

                if not self.strat.prices:
                    continue
                price = self.strat.prices[-1]
                last_rsi = self.strat.last_rsi if self.strat.last_rsi is not None else 0.0
                self._on_tick_update(price, last_rsi, index)

                if len(self.strat.prices) < self.strat.period + 1:
                    continue

                if not order:
                    continue

                trade_response = None
                order_status = None
                side = order["side"]
                trade_type_signal = order.get("trade_type")

                # Recovery-mode guard: after a crash restart with an existing position,
                # only allow "Take Profit" sells so we never lock in a loss based on
                # an RSI overbought signal that fired at a lower price.
                if (side == "SELL"
                        and getattr(self, "recovery_mode", False)
                        and trade_type_signal != "Take Profit"):
                    self.logger.log_event(
                        f"[RECOVERY] Skipping '{trade_type_signal}' SELL — "
                        f"only 'Take Profit' sells allowed in recovery mode."
                    )
                    order = None
                    with self.strat._order_lock:
                        self.strat.order = None
                    # on_price() already flipped last_order_type to "SELL" before
                    # this guard ran — restore it so the cadence does not change
                    # unless an actual sell executes.
                    self.strat.last_order_type = "BUY"
                    continue

                acc_info = self.exchange.get_account_snapshot()
                if acc_info is None:
                    self.logger.log_event("WARNING: Account snapshot failed — skipping this candle.")
                    order = None
                    with self.strat._order_lock:
                        self.strat.order = None
                    continue
                pos = acc_info["position"]
                cash = acc_info["cash"]

                size = pos if (pos > 0 and side == "SELL") else (0.8 * (cash / price))
                profit = 0.0

                self.logger.log_event(
                    f"[{order['trade_type']}] {side} signal @ {price:.4f}  RSI: {last_rsi:.2f}"
                    f"  Equity: {cash + pos * price:.2f}"
                )
                self._on_account_update(cash, pos, price)

                # --- Order-type routing ---
                order_type = self.config_data.order_type
                trail_pct = self.config_data.trailing_stop_pct / 100.0
                if order_type == "trailing_stop":
                    triggered = self._wait_for_trail(side, price, trail_pct)
                    if triggered is None:
                        # live stopped or opposing signal arrived while waiting.
                        # Only clear the local order reference — do NOT touch
                        # strat.order, which may now hold the new opposing signal
                        # that should be picked up on the very next loop iteration.
                        order = None
                        continue
                    price = triggered
                    # Recalculate size at the now-triggered price
                    size = pos if (pos > 0 and side == "SELL") else (0.8 * (cash / price))

                if side == "BUY":
                    self.strat.buy_size = size

                if self.send_real_orders:
                    if side == "BUY":
                        size = 0.8 * (cash / price)
                        cost = size * price
                        fee = 0.0095 / 100 * cost
                        if cash >= (cost + fee) and size > 0:
                            if order_type == "trailing_stop":
                                trade_response = self._exec_trail_limit(
                                    side, symbol, size, price)
                            else:
                                trade_response = self.exchange.buy(
                                    symbol, quantity=size, orig_price=price)
                            if trade_response is None:
                                self.logger.log_event("WARNING: BUY returned None. Aborting trade.")
                                order = None; self.strat.order = None
                                self.strat.last_order_type = "SELL"
                                continue
                            if trade_response["status"] in ("REJECTED", "EXPIRED", "CANCELED"):
                                self.logger.log_event(f"WARNING: BUY status {trade_response['status']}. Aborting trade.")
                                order = None; self.strat.order = None
                                self.strat.last_order_type = "SELL"
                                continue
                            if trade_response["status"] == "FILLED":
                                if trade_response["avg_price"] is not None and trade_response["commission"] is not None:
                                    self.strat.buy_price = (
                                        trade_response["avg_price"]
                                        + trade_response["commission"] / size
                                        if size > 0 else None
                                    )
                    if side == "SELL":
                        if pos > self.exchange.step:
                            if order_type == "trailing_stop":
                                trade_response = self._exec_trail_limit(
                                    side, symbol, pos, price)
                            else:
                                trade_response = self.exchange.sell(
                                    symbol, quantity=pos, orig_price=price)
                            if trade_response is None:
                                self.logger.log_event("WARNING: SELL returned None. Aborting trade.")
                                order = None; self.strat.order = None
                                self.strat.last_order_type = "BUY"
                                continue
                            if trade_response["status"] in ("REJECTED", "EXPIRED", "CANCELED"):
                                self.logger.log_event(f"WARNING: SELL status {trade_response['status']}. Aborting trade.")
                                order = None; self.strat.order = None
                                self.strat.last_order_type = "BUY"
                                continue
                        else:
                            self.logger.log_event("Warning: position too small to sell.")
                            order = None; self.strat.order = None
                            self.strat.last_order_type = "SELL"
                            continue

                self._on_trade_placed(side, price, index)

                # Wait for fill
                if trade_response is None:
                    order_status = "FILLED"  # paper trading: treat as instant fill
                elif trade_response.get("status") in ("FILLED", "PARTIALLY_FILLED"):
                    # Market orders (e.g. trailing stop) fill immediately and are never
                    # registered with order_manager — use the status from the response directly.
                    order_status = trade_response["status"]
                else:
                    order_status = None
                    while order_status not in ("FILLED", "REJECTED", "EXPIRED", "CANCELED", "CLOSED"):
                        time.sleep(10)
                        self.exchange.order_manager.sweep()
                        try:
                            order_status = self.exchange.order_manager.open_orders[trade_response["oid"]]["status"]
                        except KeyError:
                            # order moved to closed_orders — already filled or cancelled
                            order_status = self.exchange.order_manager.closed_orders[trade_response["oid"]]["status"]
                            break

                if order_status not in ("FILLED", "PARTIALLY_FILLED"):
                    self.logger.log_event(
                        f"Order not filled (status={order_status}). Flipping cadence back to try again."
                    )
                    self.strat.last_order_type = "SELL" if self.strat.last_order_type == "BUY" else "BUY"
                    order = None; self.strat.order = None
                    continue

                # Post-fill accounting
                _fee = (float(trade_response["commission"])
                        if trade_response and trade_response.get("commission") is not None
                        else 0.0)
                # Prefer the average executed price from the exchange response; fall
                # back to the trigger/signal price for paper trades (no real fill).
                fill_price = (float(trade_response["avg_price"])
                              if trade_response and trade_response.get("avg_price") is not None
                              else price)
                if fill_price != price:
                    self.logger.log_event(
                        f"[FILL] avg executed price: {fill_price:.4f}  (trigger was {price:.4f})"
                    )

                if side == "BUY":
                    self.strat.buy_size = size
                    self.num_trades += 1
                    # For paper trades strat.buy_price is never set inside the
                    # send_real_orders block, so set it here using the fill price
                    # (trail-triggered price for trailing_stop, candle price otherwise).
                    if not self.send_real_orders:
                        self.strat.buy_price = fill_price
                    # Persist buy_price so it survives a crash/restart
                    ConfigLoader.save_field("buy_price", self.strat.buy_price)
                    # Log to trade history for daily summary
                    self.logger.log_trade("BUY", size, fill_price, _fee,
                                         trade_type_signal or "", 0.0)

                if side == "SELL" and order is not None:
                    trade_type = order.get("trade_type")
                    _sell_profit = 0.0
                    if self.strat.buy_price is not None and size > 0:
                        unit_delta = fill_price - self.strat.buy_price
                        pft = unit_delta * size
                        _sell_profit = pft
                        self.strat.pft_per_strat[trade_type]['sells'] += 1
                        if pft > 0:
                            self.strat.pft_per_strat[trade_type]['wins'] += 1
                            self.strat.pft_per_strat[trade_type]['avg_win_amt'] += pft
                        else:
                            self.strat.pft_per_strat[trade_type]['losses'] += 1
                            self.strat.pft_per_strat[trade_type]['avg_loss_amt'] += pft
                        self.strat.pft_per_strat[trade_type]['profit'] += pft
                    self.strat.buy_size = 0
                    # Clear persisted buy_price — position is now closed
                    ConfigLoader.save_field("buy_price", None)
                    self.recovery_mode = False
                    # Log to trade history for daily summary
                    self.logger.log_trade("SELL", size, fill_price, _fee,
                                         trade_type_signal or "", _sell_profit)

                # Telegram notification
                if (self.telegram_enabled
                        and self.config_data.telegram_bot_token
                        and self.config_data.telegram_chat_id
                        and order is not None):
                    exec_size = trade_response['orig_qty'] if trade_response is not None else 0
                    _trade_delta = None
                    if not self.send_real_orders:
                        if self.strat.buy_price and side == "SELL":
                            profit = fill_price - self.strat.buy_price
                            _trade_delta = profit
                            self.cum_profit += profit
                            self.perc_gain = 100 * profit / self.strat.buy_price
                            self.strat.buy_price = None
                    else:
                        if side == "SELL":
                            if self.strat.buy_price:
                                profit = (
                                    float(trade_response["executed_qty"])
                                    * (fill_price - float(self.strat.buy_price))
                                    - 2 * float(trade_response["commission"])
                                )
                                _trade_delta = fill_price - float(self.strat.buy_price)
                                self.cum_profit += profit
                                self.perc_gain = 100 * profit / float(self.strat.buy_price)
                                self.strat.buy_price = None
                            else:
                                self.logger.log_event(f"Calculation error: No buy_price! | {self.strat.buy_price}")
                    msg = (
                        f"{side} Executed, Session trade#{self.num_trades:.0f}\n"
                        f"Fill price: {fill_price}\n"
                        f"Signal: {order['trade_type']} | RSI: {last_rsi:.2f}\n"
                        f"Size: {exec_size}\n"
                    )
                    if side == "SELL":
                        msg += (
                            f"Profit: ${profit:.6f}\n"
                            f"% gain: {self.perc_gain:.4f}%\n"
                            f"Cumulative profit: ${self.cum_profit:.6f}\n"
                        )
                        if _trade_delta is not None:
                            msg += f"Trade delta: ${_trade_delta:.6f}\n"
                    if self.strat.ema_score:
                        msg += f"EMA Score: {self.strat.ema_score:.2f}\n"
                    self.logger.send_telegram_message(
                        self.config_data.telegram_bot_token,
                        self.config_data.telegram_chat_id,
                        msg,
                    )

                # Post-trade equity snapshot (logs to orders.csv via get_account_snapshot)
                self.exchange.get_account_snapshot()

                order = None
                with self.strat._order_lock:
                    self.strat.order = None
                index += 1

            except Exception as e:
                msg = f"Live trading error: {e}"
                traceback.print_exc()
                self.logger.log_event(msg)
                if (self.telegram_enabled
                        and self.config_data.telegram_bot_token
                        and self.config_data.telegram_chat_id):
                    self.logger.send_telegram_message(
                        self.config_data.telegram_bot_token,
                        self.config_data.telegram_chat_id,
                        msg,
                    )
                time.sleep(1)

        self.logger.log_event("Live loop exited.")

# ----------------------------
# GUI Application
# ----------------------------

class TradingApp(tk.Tk, _LiveTradingMixin):
    def __init__(self):
        
        super().__init__()
        self._apply_dark_theme()
        
        self.title("Crypto Trading Simulator")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        self.geometry(f"{screen_width}x{screen_height}+0+0")

        # Load config and exchange
        self.config_data = ConfigLoader.load()
        self.logger = OrderLogger()
        
        
        self.exchange = BinanceUSExchange(
            self.config_data.api_key,
            self.config_data.api_secret,
            self.config_data.base_asset,
            self.config_data.quote_asset,
            self.logger
        )
        self.exchange.sync_time()
        try:
            self.exchange_info = self.exchange.client.get_exchange_info()
            self.all_symbols = [s['symbol'] for s in self.exchange_info['symbols']]
        except Exception as e:
            self.logger.log_event(f"Warning: Could not fetch exchange info: {e} — symbol list will be empty.")
            self.exchange_info = {"symbols": []}
            self.all_symbols = []
        
        # Constants and declarations
        self.soft_green = '#50C878'
        self.soft_red = '#FF7F7F'
        
        # Notebook with tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        # Live Trading tab
        frm_live = ttk.Frame(self.notebook)
        self.live_running = False
        self.live_thread = None
        self.cum_profit = 0.0
        self.perc_gain = 0.0
        self.send_real_orders = self.config_data.send_real_orders
        self.telegram_enabled = self.config_data.telegram_enabled
        self.notebook.add(frm_live, text="Live Trading")
        self._build_live_ui(frm_live)

        # Backtest tab
        frm_backtest = ttk.Frame(self.notebook)
        self.notebook.add(frm_backtest, text="Backtest")
        self._build_backtest_ui(frm_backtest)

        # Histories (for backtest)
        self.equity_history = []
        self.pnl_history = []
        self.trades = []

        # Queues
        self.trade_queue = queue.Queue()

        # Data for live charting
        self.live_prices: List[float] = []
        self.live_rsi_values: List[float] = []
        self.live_trades: List[Dict] = []  # {"index": i, "price": p, "side": "BUY"/"SELL"}

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.backtest_queue = queue.Queue()
        self.backtest_running = False
        self.backtest_thread = None
        
        self.num_trades = 0
        # otherwise it will immediately timeout and restart twm
        self.last_kline_time = time.time()

        # Candle pub/sub bus: lives here, not in the exchange
        self.candle_bus = CandleEventBus()

    def _apply_dark_theme(self):
        style = ttk.Style(self)
    
        # Use a built‑in theme as a base
        style.theme_use("clam")
    
        # General colors
        bg = "#1e1e1e"
        fg = "#d0d0d0"
        accent = "#3a3a3a"
        highlight = "#4a4a4a"
    
        # Window background
        self.configure(bg=bg)
    
        # TTK widget defaults
        style.configure(".", 
            background=bg,
            foreground=fg,
            fieldbackground=accent,
            bordercolor=highlight,
            lightcolor=highlight,
            darkcolor=highlight
        )
    
        # Frames
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
    
        # Labels
        style.configure("TLabel", background=bg, foreground=fg)
    
        # Buttons
        style.configure("TButton",
            background=accent,
            foreground=fg,
            borderwidth=1,
            focusthickness=3,
            focuscolor=highlight
        )
        style.map("TButton",
            background=[("active", highlight)],
            foreground=[("active", fg)]
        )
    
        # Entries
        style.configure("TEntry",
            fieldbackground=accent,
            foreground=fg,
            insertcolor=fg
        )
    
        # Combobox
        style.configure("TCombobox",
            fieldbackground=accent,
            background=accent,
            foreground=fg
        )
        style.map("TCombobox",
            fieldbackground=[("readonly", accent)],
            foreground=[("readonly", fg)],
            background=[("readonly", accent)]
        )
    
        # Notebook tabs
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab",
            background=accent,
            foreground=fg,
            padding=[10, 5]
        )
        style.map("TNotebook.Tab",
            background=[("selected", highlight)],
            foreground=[("selected", fg)]
        )
    
    def on_close(self):
        try:
            if hasattr(self, "twm") and self.twm:
                self._stop_price_stream()
        except Exception as e:
            self.logger.log_event(f"Error stopping stream: {e}")
        
        self.destroy()

    # ----------------------------
    # WebSocket price stream
    # ----------------------------

    def _stop_price_stream(self):
        if hasattr(self, "twm") and self.twm:
            # Proactively signal the TWM's event loop to stop before calling twm.stop().
            # This prevents the "This event loop is already running" error on the next
            # reconnect: if twm.stop() fails to terminate the thread, the old loop stays
            # _running=True, and the new TWM would inherit it via get_loop().
            try:
                old_loop = getattr(self.twm, '_loop', None)
                if old_loop and not old_loop.is_closed() and old_loop.is_running():
                    old_loop.call_soon_threadsafe(old_loop.stop)
                    time.sleep(0.5)  # brief window for the stop signal to propagate
            except Exception:
                pass
            try:
                self.twm.stop()
            except Exception as e:
                self.logger.log_event(f"Error stopping WebSocket: {type(e).__name__}: {e}")
            finally:
                self.twm = None

    def _parse_interval_to_seconds(self, interval: str) -> int:
        """Convert a Binance interval string (e.g. '5m', '1h') to seconds."""
        units = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}
        try:
            num = int(interval[:-1])
            unit = interval[-1]
            return num * units.get(unit, 3600)
        except (ValueError, IndexError):
            return 3600

    def start_kline_stream(self, symbol: str, interval: str = '1h'):
        self._stop_price_stream()  # clean up any existing TWM before creating a new one
        # Install a fresh event loop for this thread so ThreadedWebsocketManager.__init__
        # (which calls get_loop() → asyncio.get_event_loop()) does not receive a
        # still-running loop left over from a failed stop, causing "already running".
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass
        try:
            self.twm = ThreadedWebsocketManager(
                api_key=self.config_data.api_key,
                api_secret=self.config_data.api_secret,
                tld='us'
            )
            self.twm.start()
            stream_name = f"{symbol.lower()}@kline_{interval}"
            self.logger.log_event(f"Starting kline stream: {stream_name}")
            self.twm.start_kline_socket(
                symbol=symbol,
                interval=interval,
                callback=self._on_kline_message
            )
        except Exception as e:
            self.logger.log_event(f"Failed to start kline stream: {e}")
            self.twm = None
            if self.live_running:
                self.logger.log_event("Will retry stream start in 30s.")
                self.after(30000, lambda: self._restart_ws_stream())
    
    def _on_kline_message(self, msg: dict):
        # TWM delivers connection errors as {"e": "error", "m": "<reason>"}.
        # Trigger a lightweight WebSocket-only restart so the live loop keeps running.
        if msg.get("e") == "error":
            err_text = msg.get("m", str(msg))
            self.logger.log_event(f"WebSocket stream error: {err_text}")
            if self.live_running:
                self.after(0, self._restart_ws_stream)
            return

        if msg.get("e") != "kline":
            return
    
        k = msg["k"]
        is_closed = k["x"]
    
        if not is_closed:
            return  # ignore in-progress candles
    
        symbol = msg["s"]
        candle = {
            "symbol": symbol,
            "open_time": k["t"],
            "close_time": k["T"],
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "trades": k["n"],
        }
        # close_time is in ms so convert to seconds for comparison elsewhere with time.time() which is in seconds
        self.last_kline_time = candle["close_time"]/1000
        # self.exchange._log(f"[CANDLE CLOSED] {symbol} timestamp: {candle['close_time']} close={candle['close']}")
        self.candle_bus.publish(candle)

    def _restart_ws_stream(self):
        """
        Lightweight WebSocket-only reconnect. Stops the current TWM and starts
        a fresh stream without disturbing live_running, self.strat, or the candle_bus
        subscriptions. Must be called on the main (Tkinter) thread via self.after().
        """
        if not self.live_running:
            return
        symbol = self.bt_symbol_live.get()
        interval = self.bt_interval_live.get()
        self.logger.log_event("WebSocket reconnect: stopping existing stream...")
        self._stop_price_stream()
        # Brief pause so the OS can release the socket before reconnecting
        self.after(5000, lambda: self._do_restart_ws_stream(symbol, interval))

    def _do_restart_ws_stream(self, symbol: str, interval: str):
        """Second half of reconnect: starts the new stream after the teardown delay."""
        if not self.live_running:
            return
        self.logger.log_event(f"WebSocket reconnect: restarting stream for {symbol} @ {interval}")
        self.last_kline_time = time.time()  # reset so watchdog does not immediately re-trigger
        self.start_kline_stream(symbol, interval)

    # ----------------------------
    # UI functions begin here
    # ----------------------------

    def _style_axis(self, ax, fg="#d0d0d0"):
        '''
        Parameters
        ----------
        ax : matplotlib axis to format
        fg : str, optional
            Color hex code. The default is "#d0d0d0".

        Returns
        -------
        None.

        '''
        ax.title.set_color(fg)
        ax.xaxis.label.set_color(fg)
        ax.yaxis.label.set_color(fg)
    
        # Tick labels
        ax.tick_params(colors=fg)
    
        # Axis spines (borders)
        for spine in ax.spines.values():
            spine.set_color(fg)                
    
    def _toggle_real_orders(self):
        self.send_real_orders = not self.send_real_orders
    
        if self.send_real_orders:
            if not messagebox.askyesno(
                "Enable Real Trading?",
                "This will send REAL orders to Binance.\nAre you absolutely sure?"
            ):
                self.send_real_orders = False
                self.btn_toggle_live_orders.config(text="Real Orders: OFF", bg=self.soft_red)
                return

            
            self.btn_toggle_live_orders.config(text="Real Orders: ON", bg=self.soft_green)
            self.logger.log_event("⚠️ REAL ORDER MODE ENABLED ⚠️ — trades will be sent to Binance")
        else:
            self.btn_toggle_live_orders.config(text="Real Orders: OFF", bg=self.soft_red)
            self.logger.log_event("Real order mode disabled — paper trading only")
    
    def _toggle_telegram_alerts(self):
        self.telegram_enabled = not self.telegram_enabled
    
        if self.telegram_enabled:
            self.btn_toggle_telegram.config(text="Telegram Alerts: ON", bg=self.soft_green)
            self.logger.log_event("Telegram alerts enabled")
        else:
            self.btn_toggle_telegram.config(text="Telegram Alerts: OFF", bg=self.soft_red)
            self.logger.log_event("Telegram alerts disabled") 
    # ----------------------------
    # Live UI
    # ----------------------------

    def _build_live_ui(self, parent):
        # Labels
        self.var_price = tk.StringVar(value="Price: --")
        self.var_cash = tk.StringVar(value="Cash: --")
        self.var_pos = tk.StringVar(value="Position: --")
        self.var_eq = tk.StringVar(value="Equity: --")
        self.var_rsi = tk.StringVar(value="RSI: --")

        ttk.Label(parent, textvariable=self.var_price).pack(anchor="w")
        ttk.Label(parent, textvariable=self.var_cash).pack(anchor="w")
        ttk.Label(parent, textvariable=self.var_pos).pack(anchor="w")
        ttk.Label(parent, textvariable=self.var_eq).pack(anchor="w")
        ttk.Label(parent, textvariable=self.var_rsi).pack(anchor="w")

        # Controls
        frm_controls_live = ttk.LabelFrame(parent, text="Live Trading Controls")
        frm_controls_live.pack(fill="x", padx=10, pady=8)

        self.bt_symbol_live = tk.StringVar(value=self.config_data.live_symbol)
        ttk.Label(frm_controls_live, text="Symbol:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frm_controls_live, textvariable=self.bt_symbol_live,
                     values=self.all_symbols, width=15).grid(row=0, column=1)
        
        self.btn_test_telegram = ttk.Button(
            frm_controls_live,
            text="Send Test Telegram",
            command=self._send_test_telegram
        )
        self.btn_test_telegram.grid(row=3, column=3, padx=5, pady=5)
    
        if self.live_running:
            trade_btn_text = "Live Trading: ON"
            live_bg_col = self.soft_green
        else:
            trade_btn_text = "Live Trading: OFF"
            live_bg_col = self.soft_red
        self.btn_start_live = tk.Button(
            frm_controls_live,
            text=trade_btn_text,
            command=self._toggle_live_trading,
            bg=live_bg_col
        )
        self.btn_start_live.grid(row=0, column=2, padx=5)
        
        self.btn_toggle_live_orders = tk.Button(
            frm_controls_live,
            text="Real Orders: OFF",
            command=self._toggle_real_orders,
            bg=self.soft_red
        )
        self.btn_toggle_live_orders.grid(row=2, column=2, padx=5, pady=5)

        # self.btn_stop_live = ttk.Button(
        #     frm_controls_live,
        #     text="Stop Live Trading",
        #     command=self._stop_live_trading,
        #     state="disabled"
        # )
        # self.btn_stop_live.grid(row=1, column=2, padx=5)
        
        if self.telegram_enabled:
            telegram_btn_text = "Telegram Alerts: ON"
            tg_bg_col = self.soft_green
        else:
            telegram_btn_text = "Telegram Alerts: OFF"
            tg_bg_col = self.soft_red
            
        self.btn_toggle_telegram = tk.Button(
            frm_controls_live,
            text=telegram_btn_text,
            command=self._toggle_telegram_alerts,
            bg = tg_bg_col
        )
        self.btn_toggle_telegram.grid(row=3, column=2, padx=5, pady=5)

        self.bt_interval_live = tk.StringVar(value=self.config_data.live_interval)
        ttk.Label(frm_controls_live, text="Interval:").grid(row=1, column=0, sticky="w")
        ttk.Combobox(frm_controls_live, textvariable=self.bt_interval_live,
                     values=["1m", "3m", "5m", "15m", "30m",
                             "1h", "2h", "4h", "6h", "8h", "12h",
                             "1d", "3d", "1w", "1M"], width=10).grid(row=1, column=1)

        self.bt_bb_to_live = tk.DoubleVar(value=9998)
        ttk.Label(frm_controls_live, text="Timeout:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_controls_live, textvariable=self.bt_bb_to_live, width=8).grid(row=2, column=1)

        # RSI params
        frm_strategies = ttk.Frame(frm_controls_live)
        frm_strategies.grid(row=8, column=0, columnspan=3, pady=10, sticky="ew")

        frm_rsi = ttk.LabelFrame(frm_strategies, text="RSI Parameters", labelanchor="n")
        frm_rsi.grid(row=0, column=1, padx=10, sticky="n")
        self.bt_rsi_period_live = tk.IntVar(value=self.config_data.rsi_period)
        self.bt_rsi_os_live = tk.DoubleVar(value=self.config_data.rsi_oversold)
        self.bt_rsi_ob_live = tk.DoubleVar(value=self.config_data.rsi_overbought)
        self.bt_rsi_mode_live = tk.StringVar(value=self.config_data.rsi_mode)
        ttk.Label(frm_rsi, text="Mode:").grid(row=3, column=0, sticky="w")
        ttk.Combobox(frm_rsi, textvariable=self.bt_rsi_mode_live,
                     values=["simple", "sma", "ema", "wilder", "tv"], width=10).grid(row=3, column=1)
        ttk.Label(frm_rsi, text="Period:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_rsi, textvariable=self.bt_rsi_period_live, width=8).grid(row=0, column=1)
        ttk.Label(frm_rsi, text="Oversold:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_rsi, textvariable=self.bt_rsi_os_live, width=8).grid(row=1, column=1)
        ttk.Label(frm_rsi, text="Overbought:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_rsi, textvariable=self.bt_rsi_ob_live, width=8).grid(row=2, column=1)

        # Chart: price + RSI
        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.fig.patch.set_facecolor("#1e1e1e")
        
        self.ax_price = self.fig.add_subplot(2, 1, 1)
        self.ax_rsi = self.fig.add_subplot(2, 1, 2, sharex=self.ax_price)
        self.ax_price.set_facecolor("#1e1e1e")
        self.ax_rsi.set_facecolor("#1e1e1e")
        
        self._style_axis(self.ax_price, fg="#e0e0e0")
        self._style_axis(self.ax_rsi, fg="#e0e0e0")
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # RSI baseline lines
        self.ax_rsi.axhline(70, color="red", linestyle="--", linewidth=0.8)
        self.ax_rsi.axhline(30, color="green", linestyle="--", linewidth=0.8)
        self.ax_rsi.set_ylim(0, 100)
        self.ax_rsi.set_ylabel("RSI")

    
    def _send_test_telegram(self):
        print(self.config_data.telegram_bot_token)
        print(self.config_data.telegram_chat_id)
        if not self.config_data.telegram_bot_token or not self.config_data.telegram_chat_id:
            self.logger.log_event("Telegram test failed: Missing bot token or chat ID")
            return
    
        try:
            self.logger.send_telegram_message(
                self.config_data.telegram_bot_token,
                self.config_data.telegram_chat_id,
                "Test message: Telegram alerts are working!"
            )
            self.logger.log_event("Test Telegram message sent successfully")
        except Exception as e:
            self.logger.log_event(f"Telegram test error: {e}")
            
    # ----------------------------
    # Backtest UI (unchanged structure, you can refine later)
    # ----------------------------

    def _select_price_file(self):
        file_path = filedialog.askopenfilename(
            title="Select Price File",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if file_path:
            self.bt_file_path.set(file_path)

    def _build_backtest_ui(self, parent):
        frm_controls = ttk.LabelFrame(parent, text="Backtest Controls")
        frm_controls.pack(fill="x", padx=10, pady=8)
    
        # Symbol
        self.bt_symbol = tk.StringVar(value=self.config_data.backtest_symbol)
        ttk.Label(frm_controls, text="Symbol:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_symbol,
                     values=self.all_symbols, width=15).grid(row=0, column=1)

        # Interval
        self.bt_interval = tk.StringVar(value=self.config_data.backtest_interval)
        ttk.Label(frm_controls, text="Interval:").grid(row=1, column=0, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_interval,
                     values=["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"],
                     width=10).grid(row=1, column=1)

        # Lookback
        self.bt_period = tk.StringVar(value=self.config_data.backtest_lookback)
        ttk.Label(frm_controls, text="Lookback:").grid(row=2, column=0, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_period,
                     values=["1 day ago UTC","7 days ago UTC","30 days ago UTC",
                             "90 days ago UTC","6 months ago UTC",
                             "1 year ago UTC","2 years ago UTC"],
                     width=15).grid(row=2, column=1)
    
        # Strategy
        self.bt_strategy = tk.StringVar(value="BTRSI")
        ttk.Label(frm_controls, text="Strategy:").grid(row=3, column=0, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_strategy,
                     values=["TrendRSI + CompressionMR (Long-only)","BTRSI", "MicroPullback VWAP + RSI(2) (Maker-only, Long)"],
                     width=25).grid(row=3, column=1)
        
        self.bt_rsi_mode = tk.StringVar(value=self.config_data.rsi_mode)
        ttk.Label(frm_controls, text="Mode:").grid(row=3, column=2, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_rsi_mode,
                     values=["simple", "sma", "ema", "wilder", "tv"], width=10).grid(row=3, column=3)
        
        # File selection
        self.bt_file_path = tk.StringVar(value="")
        ttk.Label(frm_controls, text="Price File:").grid(row=6, column=2, sticky="w")
        ttk.Entry(frm_controls, textvariable=self.bt_file_path, width=25).grid(row=7, column=2, sticky="w")
        ttk.Button(frm_controls, text="Browse", command=self._select_price_file).grid(row=7, column=3, padx=5)

        
        # --- Strategy columns ---
        frm_strategies = ttk.Frame(frm_controls)
        frm_strategies.grid(row=8, column=0, columnspan=3, pady=10, sticky="ew")
        
        # SMA column
        frm_sma = ttk.LabelFrame(frm_strategies, text="SMA Crossover", labelanchor="n")
        frm_sma.grid(row=0, column=0, padx=10, sticky="n")
        self.bt_sma_fast = tk.IntVar(value=10)
        self.bt_sma_slow = tk.IntVar(value=30)
        ttk.Label(frm_sma, text="Fast Period:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_sma, textvariable=self.bt_sma_fast, width=8).grid(row=0, column=1)
        ttk.Label(frm_sma, text="Slow Period:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_sma, textvariable=self.bt_sma_slow, width=8).grid(row=1, column=1)
        
        # RSI column
        frm_rsi = ttk.LabelFrame(frm_strategies, text="RSI Mean Reversion", labelanchor="n")
        frm_rsi.grid(row=0, column=1, padx=10, sticky="n")
        self.bt_rsi_period = tk.IntVar(value=self.config_data.rsi_period)
        self.bt_rsi_os = tk.DoubleVar(value=self.config_data.rsi_oversold)
        self.bt_rsi_ob = tk.DoubleVar(value=self.config_data.rsi_overbought)
        ttk.Label(frm_rsi, text="Period:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_rsi, textvariable=self.bt_rsi_period, width=8).grid(row=0, column=1)
        ttk.Label(frm_rsi, text="Oversold:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_rsi, textvariable=self.bt_rsi_os, width=8).grid(row=1, column=1)
        ttk.Label(frm_rsi, text="Overbought:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_rsi, textvariable=self.bt_rsi_ob, width=8).grid(row=2, column=1)
        
        # MACD column (for RSI+MACD hybrid strategy)
        frm_macd = ttk.LabelFrame(frm_strategies, text="MACD Parameters", labelanchor="n")
        frm_macd.grid(row=0, column=2, padx=10, sticky="n")
        self.bt_macd_fast = tk.IntVar(value=8)
        self.bt_macd_slow = tk.IntVar(value=17)
        self.bt_macd_signal = tk.IntVar(value=9)
        ttk.Label(frm_macd, text="Fast EMA:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_macd, textvariable=self.bt_macd_fast, width=8).grid(row=0, column=1)
        ttk.Label(frm_macd, text="Slow EMA:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_macd, textvariable=self.bt_macd_slow, width=8).grid(row=1, column=1)
        ttk.Label(frm_macd, text="Signal EMA:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_macd, textvariable=self.bt_macd_signal, width=8).grid(row=2, column=1)
        
        # Bollinger column
        frm_bb = ttk.LabelFrame(frm_strategies, text="Bollinger Bands", labelanchor="n")
        frm_bb.grid(row=0, column=3, padx=10, sticky="n")
        self.bt_bb_period = tk.IntVar(value=16)
        self.bt_bb_mult = tk.DoubleVar(value=1.6)
        self.bt_bb_perc_buy = tk.DoubleVar(value=2.0)
        self.bt_bb_perc_sell = tk.DoubleVar(value=2.0)
        self.bt_bb_to = tk.DoubleVar(value=9997)
        ttk.Label(frm_bb, text="Period:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_bb, textvariable=self.bt_bb_period, width=8).grid(row=0, column=1)
        ttk.Label(frm_bb, text="Multiplier:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_bb, textvariable=self.bt_bb_mult, width=8).grid(row=1, column=1)
        ttk.Label(frm_bb, text="Buy Cond Percentage:").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_bb, textvariable=self.bt_bb_perc_buy, width=8).grid(row=2, column=1)
        ttk.Label(frm_bb, text="Sell Cond Percentage:").grid(row=3, column=0, sticky="w")
        ttk.Entry(frm_bb, textvariable=self.bt_bb_perc_sell, width=8).grid(row=3, column=1)
        ttk.Label(frm_bb, text="Timeout:").grid(row=4, column=0, sticky="w")
        ttk.Entry(frm_bb, textvariable=self.bt_bb_to, width=8).grid(row=4, column=1)

        # Buttons
        ttk.Button(frm_controls, text="Run Backtest", command=self._start_backtest).grid(row=4, column=0, pady=5)
        ttk.Button(frm_controls, text="Stop", command=self._stop_backtest).grid(row=4, column=1, pady=5)
    
        # Chart
        self.fig_bt = Figure(figsize=(6,4), dpi=100)
        self.ax_bt_price = self.fig_bt.add_subplot(2,1,1)
        self.ax_bt_equity = self.fig_bt.add_subplot(2,1,2, sharex=self.ax_bt_price)
    
        self.fig_bt.patch.set_facecolor("#1e1e1e")
        self.ax_bt_price.set_facecolor("#1e1e1e")
        self.ax_bt_equity.set_facecolor("#1e1e1e")
        self.canvas_bt = FigureCanvasTkAgg(self.fig_bt, master=parent)
        self.canvas_bt.get_tk_widget().pack(fill="both", expand=True)
    
        # Buffers
        self.bt_prices = []
        self.bt_equity = []
        self.bt_trades = []
        
        # --- Stats table ---
        frm_stats = ttk.LabelFrame(parent, text="Strategy Comparison")
        frm_stats.pack(fill="x", padx=10, pady=8)
        col_list = ["Equity","Trades","WinRate","Drawdown","Peak","PPT"]
        self.stats_table = ttk.Treeview(frm_stats, columns=col_list, show="headings")
        for col in col_list:
            self.stats_table.heading(col, text=col,anchor="w")
        self.stats_table.pack(fill="x")
        
    def _start_backtest(self):
        if self.backtest_running:
            return
    
        self.backtest_running = True
        symbol = self.bt_symbol.get()
        interval = self.bt_interval.get()
        lookback = self.bt_period.get()
        strat_name = self.bt_strategy.get()
    
        self.bt_prices.clear()
        self.bt_equity.clear()
        self.bt_trades.clear()
    
        self.backtest_thread = threading.Thread(
            target=lambda: self._backtest_loop(symbol, interval, lookback, strat_name),
            daemon=True
        )
        self.backtest_thread.start()
    
        self._schedule_backtest_updates()
    
    
    def _stop_backtest(self):
        self.backtest_running = False
    
    def _backtest_loop(self, symbol, interval, lookback, strat_name):
        self.logger.log_event(f"Backtest started: {symbol} {interval} {lookback}")
        klines = []
        # ----------------------------------------
        # 1. Load historical klines
        # ----------------------------------------
        if not self.bt_file_path.get():
            try:
                klines = self.exchange.get_historical_klines_raw(symbol, interval, lookback)
            except Exception as e:
                self.logger.log_event(f"Error loading klines: {e}")
                self.backtest_running = False
                return
            
        else:
            # Load from CSV file
            try:
                with open(self.bt_file_path.get(), "r") as f:
                    reader = csv.DictReader(f)
        
                    for row in reader:
                        open_time = int(float(row["Open Time"])) if "Open Time" in row else None
                        close_time = int(float(row["Close Time"])) if "Close Time" in row else None
        
                        # Build a Binance-style kline array
                        kline = [
                            open_time,                          # 0 open time
                            float(row["Open"]),                 # 1 open
                            float(row["High"]),                 # 2 high
                            float(row["Low"]),                  # 3 low
                            float(row["Close"]),                # 4 close
                            float(row.get("Volume", 0)),        # 5 volume
                            close_time,                         # 6 close time
                            0,                                  # 7 quote asset volume (unused)
                            int(row.get("Trades", 0)),          # 8 number of trades
                            0,                                  # 9 taker buy base
                            0,                                  # 10 taker buy quote
                            0                                   # 11 ignore
                        ]
        
                        klines.append(kline)
        
            except Exception as e:
                self.logger.log_event(f"Error loading klines from file: {e}")
                self.backtest_running = False
                return
                    
        if not klines:
            self.logger.log_event("No kline data returned")
            self.backtest_running = False
            return
    
        # ----------------------------------------
        # 2. Convert klines → candle dicts
        # ----------------------------------------
        candles = []
        for k in klines:
            candle = {
                "symbol": symbol,
                "open_time": k[0],
                "close_time": k[6],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "trades": k[8],
            }
            candles.append(candle)
        
        # ----------------------------------------
        # 3. Initialize strategy
        # ----------------------------------------
        self.strat = BTRSIStrategy(
            exchange=self.exchange,
            symbol=symbol,
            interval=interval,
            rsi_period=self.bt_rsi_period.get(),
            rsi_mode=self.bt_rsi_mode.get(),
            rsi_os=self.bt_rsi_os.get(),
            rsi_ob=self.bt_rsi_ob.get(),
            trade_size_base=0.01,
            logger=self.logger,
            fee_rate=self.config_data.fee_rate,
            warmup_replay_count=self.config_data.warmup_replay_count,
        )
        # self.strat = EMA21PullbackReclaim15m(
        #     exchange=self.exchange,
        #     symbol=symbol,
        #     rsi_period=self.bt_rsi_period.get(),
        #     rsi_mode=self.bt_rsi_mode.get(),
        #     rsi_os=self.bt_rsi_os.get(),
        #     rsi_ob=self.bt_rsi_ob.get(),
        #     trade_size_base=0.01,
        #     bband_period = app.bt_bb_period.get(),
        #     bband_mult = app.bt_bb_mult.get(),
        #     timeout = app.bt_bb_to.get(),
        #     interval = app.bt_interval.get(),
        # )

            
        cash = float(self.config_data.starting_cash)
        position = 0.0
        last_buy_price = None
        wins = 0
        losses = 0
    
        # mr_entries = 0
        # mr_exits = 0
        # mr_stops = 0
        # mr_stop_by_month = {}

        # ----------------------------------------
        # 4. Candle-by-candle backtest
        # ----------------------------------------
        for i, candle in enumerate(candles):
            order = None
            if not self.backtest_running:
                break
    
            price = candle["close"]
    
            # Strategy processes candle
            orderBool = self.strat.on_price(candle)
            
            if orderBool:
                if self.strat.order:
                    order = self.strat.order
            # ----------------------------------------
            # 5. Execute simulated trades
            # ----------------------------------------
            if order:
                side = order["side"]
    
                if side == "BUY" and cash > 0:
                    # if order.get("engine") == "COMP_MR":
                    #     mr_entries += 1

                    size = 0.8 * (cash / price)
                    self.strat.buy_size = size
                    # size = 100000 / price # makes PPT actually relevant and not having profits scale as you go along
                    cost = size * price
                    feepct = 0.00#95
                    fee = cost * feepct/100
                    cash -= (cost + fee)
                    position += size
                    last_buy_price = price
                    self.bt_trades.append({"index": i, "price": price, "side": "BUY"})
    
                elif side == "SELL" and position > 0:
                    # if order.get("engine") == "COMP_MR":
                    #     mr_exits += 1
                    # if order["trade_type"] == "RangeBreak_Stop": 
                    #     mr_stops += 1 
                    #     month = datetime.fromtimestamp(candle["close_time"]/1000).strftime("%Y-%m") 
                    #     mr_stop_by_month[month] = mr_stop_by_month.get(month, 0) + 1

                    revenue = position * price
                    feepct = 0.0095
                    fee = revenue * feepct/100
                    cash += (revenue - fee)
                    profit = (price - last_buy_price) * position - fee
                    
                    # win/loss accounting — written to strategy's pft_per_strat (same as live mode)
                    trade_type = order.get("trade_type")
                    if profit > 0:
                        wins += 1
                        self.strat.pft_per_strat[trade_type]['wins'] += 1
                        self.strat.pft_per_strat[trade_type]['avg_win_amt'] += profit
                    else:
                        losses += 1
                        self.strat.pft_per_strat[trade_type]['losses'] += 1
                        self.strat.pft_per_strat[trade_type]['avg_loss_amt'] += profit

                    position = 0
                    self.bt_trades.append({"index": i, "price": price, "side": "SELL"})

                    # Hard reset state
                    self.strat.last_order_type = "SELL"

    
            # ----------------------------------------
            # 6. Compute equity + push to GUI queue
            # ----------------------------------------
            equity = cash + position * price
    
            self.backtest_queue.put({
                "type": "tick",
                "price": price,
                "equity": equity,
                "index": i
            })
    
        # ----------------------------------------
        # 7. Final stats
        # ----------------------------------------
        self.logger.log_event("Backtest finished")
        # print("MR entries:", mr_entries)
        # print("MR exits:", mr_exits)
        # print("MR stops:", mr_stops)
        # print("MR stop rate:", mr_stops / mr_exits if mr_exits else 0)
        # print("Stops by month:", mr_stop_by_month)
        # print("\n=== MicroPullback Gate Blocks ===")
        # pprint.pprint(dict(self.strat.debug_block))

        pprint.pprint(dict(self.strat.pft_per_strat))
        total_closed = wins + losses
        final_eq = cash + (position * candles[-1]["close"])
        winrate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
    
        stats = {
            "final": final_eq,
            "trades": len(self.bt_trades),
            "winrate": winrate,
            "drawdown": 0.0,
            "peak": max(self.bt_equity) if self.bt_equity else final_eq,
            "PPT": (final_eq - float(self.config_data.starting_cash)) / len(self.bt_trades)
                   if self.bt_trades else 0
        }
            
        # Push stats to GUI thread
        self.after(0, lambda: self._update_backtest_stats(stats))
    
        self.backtest_running = False
        
    def _update_backtest_stats(self, stats):
        for row in self.stats_table.get_children():
            self.stats_table.delete(row)
    
        self.stats_table.insert(
            "", "end",
            values=(
                f"{stats['final']:.2f}",
                stats["trades"],
                f"{stats['winrate']:.1f}%",
                f"{stats['drawdown']:.1f}%",
                f"{stats['peak']:.2f}",
                f"{stats['PPT']:.2f}"
            )
        )
    
    def _schedule_backtest_updates(self):
        if not self.backtest_running:
            return
        self._process_backtest_queue()
        self._update_backtest_chart()
        self.after(200, self._schedule_backtest_updates)
    
    def _process_backtest_queue(self):
        while True:
            try:
                msg = self.backtest_queue.get_nowait()
            except queue.Empty:
                break
    
            if msg["type"] == "tick":
                self.bt_prices.append(msg["price"])
                self.bt_equity.append(msg["equity"])
        
    def _update_backtest_chart(self):
        if not self.bt_prices:
            return
    
        self.ax_bt_price.clear()
        self.ax_bt_price.plot(self.bt_prices, color="blue", label="Price")
        self.ax_bt_price.legend(loc="upper left")
    
        # Trade markers
        buys = [(t["index"], t["price"]) for t in self.bt_trades if t["side"]=="BUY"]
        sells = [(t["index"], t["price"]) for t in self.bt_trades if t["side"]=="SELL"]
    
        if buys:
            bx, by = zip(*buys)
            self.ax_bt_price.scatter(bx, by, marker="^", color="green", s=40)
        if sells:
            sx, sy = zip(*sells)
            self.ax_bt_price.scatter(sx, sy, marker="v", color="red", s=40)
    
        self.ax_bt_equity.clear()
        self.ax_bt_equity.plot(self.bt_equity, color="purple", label="Equity")
        self.ax_bt_equity.legend(loc="upper left")
    
        self.canvas_bt.draw()
        
        
    # ----------------------------
    # Live trading engine
    # ----------------------------

    def _toggle_live_trading(self):
        self.live_running = not self.live_running
    
        if self.live_running:
            symbol = self.bt_symbol_live.get()
            interval = self.bt_interval_live.get()
            self.exchange.order_manager.interval = interval
            # self._start_price_stream(symbol)
            self.start_kline_stream(symbol, interval=self.bt_interval_live.get())
            self.btn_start_live.config(text="Live Trading: ON", bg=self.soft_green)
            self.logger.log_event("Live trading enabled")
            live_thread = threading.Thread(
                target=lambda: self._live_trading_loop(symbol=symbol, interval=interval),
                daemon=True
            )
            live_thread.start()
            
            websocket_watchdog = threading.Thread(
                target=lambda: self._ws_watchdog(),
                daemon=True
            )
            websocket_watchdog.start()
            
            self._schedule_gui_updates()
        else:
            self._stop_price_stream()
            self.logger.log_event("Requested stop of live trading")
            self.btn_start_live.config(text="Live Trading: OFF", bg=self.soft_red)
        

    def _ws_watchdog(self):
        self.logger.log_event("Websocket watchdog started - checking for kline timeout")
        interval_str = self.bt_interval_live.get()
        interval_secs = self._parse_interval_to_seconds(interval_str)
        # Allow 1.2x the candle interval before treating silence as a dead stream.
        # Floor at 120s so short intervals (e.g. 1m) don't trigger on normal variance.
        timeout = max(120, interval_secs * 1.2)
        self.logger.log_event(
            f"Watchdog timeout set to {timeout:.0f}s for {interval_str} candles"
        )
        while self.live_running:
            time.sleep(10)
            cur_time = time.time()
            time_delta = cur_time - self.last_kline_time
            if time_delta > timeout:
                self.logger.log_event(
                    f"KLINE TIMEOUT — {time_delta:.0f}s since last candle "
                    f"(limit {timeout:.0f}s). Triggering WebSocket reconnect."
                )
                # Reset the timer so consecutive wakeups don't re-trigger immediately
                self.last_kline_time = time.time()
                # Lightweight WebSocket-only restart; live loop keeps running
                self.after(0, self._restart_ws_stream)
                # Wait for the reconnect to settle before watching again
                time.sleep(timeout / 2)

    # ------------------------------------------------------------------
    # GUI-specific live loop hooks (override _LiveTradingMixin no-ops)
    # ------------------------------------------------------------------

    def _on_tick_update(self, price: float, rsi: float, index: int) -> None:
        self.trade_queue.put({"type": "tick", "price": price, "rsi": rsi, "index": index})

    def _on_account_update(self, cash: float, pos: float, price: float) -> None:
        self.var_cash.set(f"Cash: {cash:.2f}")
        self.var_pos.set(f"Position: {pos:.4f}")
        self.var_eq.set(f"Equity: {cash + pos * price:.2f}")

    def _on_trade_placed(self, side: str, price: float, index: int) -> None:
        self.trade_queue.put({"type": "trade", "side": side, "price": price, "index": index})

    def _on_stop_requested(self) -> None:
        self.after(0, self._toggle_live_trading)

    def _live_trading_loop(self, symbol="BNBUSD", interval="1h"):
        self.perc_gain = 0.0
        self.cum_profit = 0.0
        self.strat = BTRSIStrategy(
            exchange=self.exchange,
            symbol=symbol,
            rsi_period=self.bt_rsi_period_live.get(),
            rsi_mode=self.bt_rsi_mode_live.get(),
            rsi_os=self.bt_rsi_os_live.get(),
            rsi_ob=self.bt_rsi_ob_live.get(),
            interval=self.bt_interval_live.get(),
            logger=self.logger,
            fee_rate=self.config_data.fee_rate,
            warmup_replay_count=self.config_data.warmup_replay_count,
        )
        self.candle_bus.subscribe(self.strat.on_price)
        self.logger.log_event(f"Live trading started for {symbol} with period {self.strat.period}")

        acc_info = self.exchange.get_account_snapshot()
        if acc_info is not None:
            # Use exchange.step instead of strat.prices (prices is empty at boot —
            # WebSocket hasn't delivered any candles yet, so the old guard always
            # failed on startup, causing the bot to attempt a BUY into a position).
            if acc_info['position'] > self.exchange.step:
                self.strat.last_order_type = "BUY"
                self.strat.buy_size = acc_info['position']
                # Crash recovery: restore buy_price from config so that SELL P&L
                # is correct even if the process restarted after a BUY fill.
                if self.config_data.buy_price is not None:
                    self.strat.buy_price = self.config_data.buy_price
                    self.recovery_mode = True
                    self.logger.log_event(
                        f"[RECOVERY] Position detected. Restoring buy_price="
                        f"{self.strat.buy_price:.4f} from config. "
                        f"Only 'Take Profit' sells will execute until position is closed."
                    )
                else:
                    self.recovery_mode = False
                    self.logger.log_event(
                        "[RECOVERY] Position detected but no buy_price in config "
                        "— SELL P&L will not be calculated for this position."
                    )
            else:
                self.recovery_mode = False
            self.var_cash.set(f"Cash: {acc_info['cash']:.2f}")
            self.var_pos.set(f"Position: {acc_info['position']:.4f}")

        self._live_loop(symbol, interval)
        self.logger.log_event("Live trading loop exited")

    # ----------------------------
    # GUI update loop for live
    # ----------------------------

    def _schedule_gui_updates(self):
        if not self.live_running:
            return
        self._process_trade_queue()
        self._update_live_chart()
        self.after(500, self._schedule_gui_updates)

    def _process_trade_queue(self):
        while True:
            try:
                msg = self.trade_queue.get_nowait()
            except queue.Empty:
                break

            msg_type = msg.get("type")
            # msg["price"] = prices[-1] # not pretty but minimal invasive changes, rework later
            if msg_type == "tick":
                price = msg["price"]
                rsi = msg["rsi"]
                index = msg["index"]

                self.live_prices.append(price)
                if rsi is not None:
                    self.live_rsi_values.append(rsi)
                    self.var_rsi.set(f"RSI: {rsi:.2f}")
                else:
                    self.var_rsi.set("RSI: --")

                self.var_price.set(f"Price: {price:.2f}")

            elif msg_type == "trade":
                self.live_trades.append({
                    "index": msg["index"],
                    "price": msg["price"],
                    "side": msg["side"]
                })

        max_points = 2000
        self.live_prices = self.live_prices[-max_points:]
        self.live_rsi_values = self.live_rsi_values[-max_points:]
        self.live_trades = self.live_trades[-max_points:]

    def _update_live_chart(self):
        if not self.live_prices:
            return

        self.ax_price.clear()
        self.ax_price.plot(self.live_prices, color="blue", label="Price")
        self.ax_price.set_title("Live Price")
        self.ax_price.legend(loc="upper left")

        buy_x, buy_y, sell_x, sell_y = [], [], [], []
        for t in self.live_trades:
            i = t["index"]
            if 0 <= i < len(self.live_prices):
                if t["side"] == "BUY":
                    buy_x.append(i)
                    buy_y.append(self.live_prices[i])
                else:
                    sell_x.append(i)
                    sell_y.append(self.live_prices[i])

        self.ax_price.scatter(buy_x, buy_y, marker="^", color="green", s=50, label="BUY")
        self.ax_price.scatter(sell_x, sell_y, marker="v", color="red", s=50, label="SELL")
        self.ax_price.legend(loc="upper left")

        self.ax_rsi.clear()
        self.ax_rsi.plot(self.live_rsi_values, color="purple", label="RSI")
        self.ax_rsi.axhline(70, color="red", linestyle="--", linewidth=0.8)
        self.ax_rsi.axhline(30, color="green", linestyle="--", linewidth=0.8)
        self.ax_rsi.set_ylim(0, 100)
        self.ax_rsi.set_ylabel("RSI")
        self.ax_rsi.legend(loc="upper left")

        self.canvas.draw()

# ----------------------------
# Headless runner
# ----------------------------

class HeadlessRunner(_LiveTradingMixin):
    """
    Runs live trading or backtesting without any Tkinter GUI.
    All status output goes to the OrderLogger (stdout + session.log).
    Backtest charts are saved as PNG files under logs/.

    The WebSocket stream is managed with the same TWM used by TradingApp, but
    without self.after(): error-triggered reconnects spawn a daemon thread and
    watchdog-triggered reconnects happen directly in the watchdog thread.
    """

    def __init__(self, config: AppConfig):
        self.config_data = config
        self.logger = OrderLogger()
        self.live_running = False
        self.last_kline_time = time.time()
        self.candle_bus = CandleEventBus()
        self.strat = None
        self.twm = None
        self.send_real_orders = config.send_real_orders
        self.telegram_enabled = config.telegram_enabled
        self.perc_gain = 0.0
        self.cum_profit = 0.0
        self.num_trades = 0
        self._reconnect_lock = threading.Lock()
        self._reconnect_delay     = 5    # current backoff delay (seconds)
        self._reconnect_delay_base = 5   # reset-to value after a successful reconnect
        self._reconnect_delay_max  = 120 # cap

        self.logger.log_event("Initializing exchange...")
        self.exchange = BinanceUSExchange(
            config.api_key,
            config.api_secret,
            config.base_asset,
            config.quote_asset,
            self.logger,
        )
        self.exchange.sync_time()

    # ------------------------------------------------------------------
    # WebSocket stream management (no self.after() — thread-safe directly)
    # ------------------------------------------------------------------

    def _stop_price_stream(self):
        if self.twm:
            try:
                old_loop = getattr(self.twm, '_loop', None)
                if old_loop and not old_loop.is_closed() and old_loop.is_running():
                    old_loop.call_soon_threadsafe(old_loop.stop)
                    time.sleep(0.5)
            except Exception:
                pass
            try:
                self.twm.stop()
            except Exception as e:
                self.logger.log_event(f"Error stopping WebSocket: {type(e).__name__}: {e}")
            finally:
                self.twm = None

    def _parse_interval_to_seconds(self, interval: str) -> int:
        units = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}
        try:
            return int(interval[:-1]) * units.get(interval[-1], 3600)
        except (ValueError, IndexError):
            return 3600

    def _start_kline_stream(self, symbol: str, interval: str):
        self._stop_price_stream()
        # Reset the event loop for this thread before creating the new TWM.
        # get_loop() (called inside ThreadedWebsocketManager.__init__) uses
        # asyncio.get_event_loop(), which returns the old still-running loop if
        # twm.stop() failed — causing "This event loop is already running".
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass
        try:
            self.twm = ThreadedWebsocketManager(
                api_key=self.config_data.api_key,
                api_secret=self.config_data.api_secret,
                tld='us',
            )
            self.twm.start()
            self.logger.log_event(f"Kline stream started: {symbol.lower()}@kline_{interval}")
            self.twm.start_kline_socket(
                symbol=symbol,
                interval=interval,
                callback=self._on_kline_message,
            )
        except Exception as e:
            self.logger.log_event(f"Failed to start kline stream: {e}")
            self.twm = None

    def _on_kline_message(self, msg: dict):
        if msg.get("e") == "error":
            err_text = msg.get("m", str(msg))
            self.logger.log_event(f"WebSocket error: {err_text}")
            if self.live_running:
                threading.Thread(target=self._restart_ws_stream, daemon=True).start()
            return

        if msg.get("e") != "kline":
            return

        k = msg["k"]
        if not k["x"]:
            return

        candle = {
            "symbol": msg["s"],
            "open_time": k["t"],
            "close_time": k["T"],
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "trades": k["n"],
        }
        self.last_kline_time = candle["close_time"] / 1000
        self.candle_bus.publish(candle)

    def _restart_ws_stream(self):
        """Stop and restart the stream with exponential backoff on repeated failures."""
        if not self.live_running:
            return
        with self._reconnect_lock:   # serialise concurrent restart attempts
            if not self.live_running:
                return
            symbol   = self.config_data.live_symbol
            interval = self.config_data.live_interval
            self.logger.log_event("WebSocket reconnect: tearing down old stream...")
            self._stop_price_stream()
            delay = self._reconnect_delay
            self.logger.log_event(f"WebSocket reconnect: waiting {delay:.0f}s before retry...")
            time.sleep(delay)
            if not self.live_running:
                return
            self.logger.log_event(f"WebSocket reconnect: starting new stream {symbol}@{interval}")
            self.last_kline_time = time.time()
            self._start_kline_stream(symbol, interval)
            if self.twm is not None:
                # Success — reset backoff
                self._reconnect_delay = self._reconnect_delay_base
            else:
                # Failure — double the delay up to the cap
                self._reconnect_delay = min(self._reconnect_delay * 2, self._reconnect_delay_max)
                self.logger.log_event(
                    f"WebSocket reconnect: stream failed — next retry in {self._reconnect_delay:.0f}s"
                )

    def _ws_watchdog(self, symbol: str, interval: str):
        interval_secs = self._parse_interval_to_seconds(interval)
        timeout = max(120, interval_secs * 2)
        self.logger.log_event(f"Watchdog started — timeout {timeout:.0f}s for {interval} candles")
        while self.live_running:
            time.sleep(10)
            if time.time() - self.last_kline_time > timeout:
                self.logger.log_event(
                    f"KLINE TIMEOUT — {time.time() - self.last_kline_time:.0f}s silence. Reconnecting."
                )
                self.last_kline_time = time.time()
                self._restart_ws_stream()
                time.sleep(timeout / 2)

    # ------------------------------------------------------------------
    # Graceful shutdown helpers
    # ------------------------------------------------------------------

    def _stdin_stop_listener(self):
        """Background thread: type 'q' + Enter to stop live trading gracefully."""
        try:
            while self.live_running:
                line = sys.stdin.readline()
                if line.strip().lower() in ("q", "quit", "stop", "exit"):
                    self.logger.log_event("Stop command received — stopping live trading.")
                    self.live_running = False
                    break
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Live trading
    # ------------------------------------------------------------------

    def run_live(self):
        symbol   = self.config_data.live_symbol
        interval = self.config_data.live_interval

        self.logger.log_event("=" * 60)
        self.logger.log_event("  HEADLESS LIVE TRADING")
        self.logger.log_event(f"  Symbol: {symbol}   Interval: {interval}")
        self.logger.log_event(
            f"  RSI period={self.config_data.rsi_period}  mode={self.config_data.rsi_mode}"
            f"  OS={self.config_data.rsi_oversold}  OB={self.config_data.rsi_overbought}"
        )
        self.logger.log_event(
            f"  Real orders: {self.send_real_orders}   Telegram: {self.telegram_enabled}"
        )
        self.logger.log_event("=" * 60)

        self.live_running = True
        self.last_kline_time = time.time()
        self.exchange.order_manager.interval = interval

        # Register SIGTERM so process managers (systemd, task schedulers, etc.)
        # trigger the same clean shutdown path as Ctrl+C.
        def _sigterm_handler(signum, frame):
            self.logger.log_event("SIGTERM received — stopping live trading.")
            self.live_running = False
        try:
            signal.signal(signal.SIGTERM, _sigterm_handler)
        except (OSError, ValueError):
            pass  # not on main thread or OS doesn't support it

        # Allow typing 'q' + Enter in the console as an alternative to Ctrl+C.
        threading.Thread(target=self._stdin_stop_listener, daemon=True).start()
        self.logger.log_event("Type 'q' + Enter to stop gracefully.")

        self._start_kline_stream(symbol, interval)

        self.strat = BTRSIStrategy(
            exchange=self.exchange,
            symbol=symbol,
            rsi_period=self.config_data.rsi_period,
            rsi_mode=self.config_data.rsi_mode,
            rsi_os=self.config_data.rsi_oversold,
            rsi_ob=self.config_data.rsi_overbought,
            interval=interval,
            logger=self.logger,
            fee_rate=self.config_data.fee_rate,
            warmup_replay_count=self.config_data.warmup_replay_count,
        )
        self.candle_bus.subscribe(self.strat.on_price)

        threading.Thread(
            target=lambda: self._ws_watchdog(symbol, interval),
            daemon=True,
        ).start()

        acc_info = self.exchange.get_account_snapshot()
        if acc_info is not None:
            # Use exchange.step instead of strat.prices (prices is empty at boot —
            # WebSocket hasn't delivered any candles yet, so the old guard always
            # failed on startup, causing the bot to attempt a BUY into a position).
            if acc_info['position'] > self.exchange.step:
                self.strat.last_order_type = "BUY"
                self.strat.buy_size = acc_info['position']
                # Crash recovery: restore buy_price from config so that SELL P&L
                # is correct even if the process restarted after a BUY fill.
                if self.config_data.buy_price is not None:
                    self.strat.buy_price = self.config_data.buy_price
                    self.recovery_mode = True
                    self.logger.log_event(
                        f"[RECOVERY] Position detected. Restoring buy_price="
                        f"{self.strat.buy_price:.4f} from config. "
                        f"Only 'Take Profit' sells will execute until position is closed."
                    )
                else:
                    self.recovery_mode = False
                    self.logger.log_event(
                        "[RECOVERY] Position detected but no buy_price in config "
                        "— SELL P&L will not be calculated for this position."
                    )
            else:
                self.recovery_mode = False
            self.logger.log_event(
                f"Account  Cash: {acc_info['cash']:.2f}  Position: {acc_info['position']:.4f}"
            )

        try:
            self._live_loop(symbol, interval)
        except KeyboardInterrupt:
            self.logger.log_event("KeyboardInterrupt — stopping live trading.")
        finally:
            self.live_running = False
            self._stop_price_stream()
            self.logger.log_event("Headless live trading stopped.")

    # ------------------------------------------------------------------
    # Backtest
    # ------------------------------------------------------------------

    def run_backtest(self):
        symbol   = self.config_data.backtest_symbol
        interval = self.config_data.backtest_interval
        lookback = self.config_data.backtest_lookback

        self.logger.log_event("=" * 60)
        self.logger.log_event("  HEADLESS BACKTEST")
        self.logger.log_event(f"  Symbol: {symbol}   Interval: {interval}   Lookback: {lookback}")
        self.logger.log_event(
            f"  RSI period={self.config_data.rsi_period}  mode={self.config_data.rsi_mode}"
            f"  OS={self.config_data.rsi_oversold}  OB={self.config_data.rsi_overbought}"
        )
        self.logger.log_event("=" * 60)

        klines = self.exchange.get_historical_klines_raw(symbol, interval, lookback)
        if not klines:
            self.logger.log_event("No klines returned. Aborting.")
            return

        self.logger.log_event(f"Loaded {len(klines)} candles.")

        candles = [
            {
                "symbol":    symbol,
                "open_time": k[0],
                "close_time": k[6],
                "open":  float(k[1]),
                "high":  float(k[2]),
                "low":   float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "trades": k[8],
            }
            for k in klines
        ]

        self.strat = BTRSIStrategy(
            exchange=self.exchange,
            symbol=symbol,
            interval=interval,
            rsi_period=self.config_data.rsi_period,
            rsi_mode=self.config_data.rsi_mode,
            rsi_os=self.config_data.rsi_oversold,
            rsi_ob=self.config_data.rsi_overbought,
            trade_size_base=0.01,
            logger=self.logger,
            fee_rate=self.config_data.fee_rate,
            warmup_replay_count=self.config_data.warmup_replay_count,
        )

        cash           = float(self.config_data.starting_cash)
        position       = 0.0
        last_buy_price = None
        wins = losses  = 0
        equity_series: List[float] = []
        trades: List[dict] = []

        total        = len(candles)
        report_every = max(1, total // 20)   # progress every 5 %

        for i, candle in enumerate(candles):
            order = None
            price = candle["close"]

            if self.strat.on_price(candle) and self.strat.order:
                order = self.strat.order

            if order:
                side = order["side"]

                if side == "BUY" and cash > 0:
                    size           = 0.8 * (cash / price)
                    self.strat.buy_size = size
                    cost           = size * price
                    fee            = cost * 0.0095 / 100
                    cash          -= (cost + fee)
                    position      += size
                    last_buy_price = price
                    trades.append({"index": i, "price": price, "side": "BUY"})

                elif side == "SELL" and position > 0:
                    revenue    = position * price
                    fee        = revenue * 0.0095 / 100
                    cash      += (revenue - fee)
                    profit     = (price - last_buy_price) * position - fee
                    trade_type = order.get("trade_type")

                    if profit > 0:
                        wins += 1
                        self.strat.pft_per_strat[trade_type]['wins'] += 1
                        self.strat.pft_per_strat[trade_type]['avg_win_amt'] += profit
                    else:
                        losses += 1
                        self.strat.pft_per_strat[trade_type]['losses'] += 1
                        self.strat.pft_per_strat[trade_type]['avg_loss_amt'] += profit

                    position = 0.0
                    self.strat.last_order_type = "SELL"
                    trades.append({"index": i, "price": price, "side": "SELL"})

            equity = cash + position * price
            equity_series.append(equity)

            if i % report_every == 0:
                pct = 100 * i / total
                print(f"  {pct:5.1f}%  candle {i:>6}/{total}  equity {equity:>12,.2f}", end="\r", flush=True)

        print()  # clear progress line

        # ---- Final stats ----
        total_closed = wins + losses
        final_eq  = cash + (position * candles[-1]["close"]) if candles else cash
        winrate   = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        peak      = max(equity_series) if equity_series else final_eq
        ppt       = (final_eq - self.config_data.starting_cash) / len(trades) if trades else 0.0
        drawdown  = 100.0 * (peak - final_eq) / peak if peak > 0 else 0.0
        ret_pct   = 100.0 * (final_eq - self.config_data.starting_cash) / self.config_data.starting_cash

        print()
        print("=" * 60)
        print(f"  BACKTEST RESULTS  {symbol} {interval}  ({lookback})")
        print("=" * 60)
        print(f"  Starting equity : ${self.config_data.starting_cash:>12,.2f}")
        print(f"  Final equity    : ${final_eq:>12,.2f}  ({ret_pct:+.2f}%)")
        print(f"  Peak equity     : ${peak:>12,.2f}")
        print(f"  Max drawdown    :  {drawdown:.2f}%")
        print(f"  Total trades    :  {len(trades)}")
        print(f"  Win rate        :  {winrate:.1f}%  ({wins}W / {losses}L)")
        print(f"  Profit/trade    : ${ppt:>+.2f}")
        print("=" * 60)
        print()
        print("Per-signal P&L:")
        pprint.pprint(dict(self.strat.pft_per_strat))
        print()

        self._save_backtest_chart(candles, equity_series, trades, symbol, interval)
        self.logger.log_event(
            f"Backtest complete — final equity {final_eq:.2f}  winrate {winrate:.1f}%"
        )

    def _save_backtest_chart(
        self,
        candles: list,
        equity_series: list,
        trades: list,
        symbol: str,
        interval: str,
    ):
        """Save a price + equity chart to logs/<symbol>_<interval>_backtest.png."""
        try:
            import matplotlib.pyplot as plt

            prices = [c["close"] for c in candles]

            fig, (ax_p, ax_e) = plt.subplots(2, 1, figsize=(16, 8), sharex=True)
            fig.suptitle(f"Backtest: {symbol} {interval}", color="#d0d0d0")
            fig.patch.set_facecolor("#1e1e1e")

            for ax in (ax_p, ax_e):
                ax.set_facecolor("#1e1e1e")
                ax.tick_params(colors="#d0d0d0")
                ax.yaxis.label.set_color("#d0d0d0")
                for spine in ax.spines.values():
                    spine.set_color("#555555")

            ax_p.plot(prices, color="steelblue", linewidth=0.7, label="Price")
            buys  = [(t["index"], t["price"]) for t in trades if t["side"] == "BUY"]
            sells = [(t["index"], t["price"]) for t in trades if t["side"] == "SELL"]
            if buys:
                bx, by = zip(*buys)
                ax_p.scatter(bx, by, marker="^", color="lime",   s=25, zorder=3, label="BUY")
            if sells:
                sx, sy = zip(*sells)
                ax_p.scatter(sx, sy, marker="v", color="tomato", s=25, zorder=3, label="SELL")
            ax_p.legend(loc="upper left", fontsize=7, labelcolor="#d0d0d0")
            ax_p.set_ylabel("Price", color="#d0d0d0")

            ax_e.plot(equity_series, color="mediumpurple", linewidth=0.7, label="Equity")
            ax_e.legend(loc="upper left", fontsize=7, labelcolor="#d0d0d0")
            ax_e.set_ylabel("Equity ($)", color="#d0d0d0")
            ax_e.set_xlabel("Candle index", color="#d0d0d0")

            os.makedirs("logs", exist_ok=True)
            chart_path = os.path.join("logs", f"{symbol}_{interval}_backtest.png")
            plt.tight_layout()
            plt.savefig(chart_path, dpi=120, facecolor=fig.get_facecolor())
            plt.close(fig)

            print(f"  Chart saved → {chart_path}")
            self.logger.log_event(f"Backtest chart saved: {chart_path}")
        except Exception as e:
            self.logger.log_event(f"Chart save failed: {e}")


def parse_cli_args():
    parser = argparse.ArgumentParser(
        description="Binance autotrader — GUI or headless mode",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- Mode ---
    parser.add_argument("--headless", action="store_true",
                        help="Run without GUI (terminal output + chart files)")
    parser.add_argument("--live", action="store_true",
                        help="Start live trading (headless mode)")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest and exit (headless mode)")
    parser.add_argument("--real-orders", action="store_true",
                        help="Send real Binance orders instead of paper trading")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable Telegram notifications for this session")

    # --- Symbol / interval ---
    parser.add_argument("--symbol", type=str, default=None,
                        help="Trading pair symbol, e.g. BNBUSD")
    parser.add_argument("--interval", type=str, default=None,
                        help="Candle interval, e.g. 5m, 1h, 4h")

    # --- RSI strategy params ---
    parser.add_argument("--rsi-period", type=int, default=None,
                        help="RSI lookback period")
    parser.add_argument("--rsi-mode", type=str, choices=["simple", "sma"], default=None,
                        help="RSI smoothing mode")
    parser.add_argument("--rsi-os", type=float, default=None,
                        help="RSI oversold threshold (buy trigger)")
    parser.add_argument("--rsi-ob", type=float, default=None,
                        help="RSI overbought threshold (sell trigger)")

    # --- Backtest-specific ---
    parser.add_argument("--lookback", type=str, default=None,
                        help='Backtest lookback window, e.g. "6 months ago UTC"')
    parser.add_argument("--starting-cash", type=float, default=None,
                        help="Starting cash for backtest simulation")
    parser.add_argument("--starting-cash-live", type=float, default=None,
                        help="Starting cash for live session equity tracking")

    return parser.parse_args()


def _apply_cli_overrides(config: AppConfig, args) -> None:
    """
    Mutate *config* in-place with any values explicitly passed on the command
    line.  Only non-None values override the config so the config file always
    acts as the default.
    """
    if args.real_orders:
        config.send_real_orders = True
    if args.no_telegram:
        config.telegram_enabled = False
    if args.symbol is not None:
        config.live_symbol = args.symbol
        config.backtest_symbol = args.symbol
    if args.interval is not None:
        config.live_interval = args.interval
        config.backtest_interval = args.interval
    if args.rsi_period is not None:
        config.rsi_period = args.rsi_period
    if args.rsi_mode is not None:
        config.rsi_mode = args.rsi_mode
    if args.rsi_os is not None:
        config.rsi_oversold = args.rsi_os
    if args.rsi_ob is not None:
        config.rsi_overbought = args.rsi_ob
    if args.lookback is not None:
        config.backtest_lookback = args.lookback
    if args.starting_cash is not None:
        config.starting_cash = args.starting_cash
    if args.starting_cash_live is not None:
        config.starting_cash_live = args.starting_cash_live

if __name__ == "__main__":
    args   = parse_cli_args()
    config = ConfigLoader.load()
    _apply_cli_overrides(config, args)

    if args.headless:
        runner = HeadlessRunner(config)
        if args.backtest:
            runner.run_backtest()
        else:
            # Default headless action is live trading (--live flag or implicit)
            runner.run_live()
    else:
        app = TradingApp()
        app.mainloop()