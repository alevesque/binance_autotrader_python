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
import argparse, sys
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplcursors
from datetime import datetime
from collections import defaultdict
import pprint

# ----------------------------
# Config and logging utilities
# ----------------------------

@dataclass
class AppConfig:
    api_key: str = ""
    api_secret: str = ""
    base_asset: str = "BTC"
    quote_asset: str = "USDT"
    starting_cash: float = 100000.0
    fee_rate: float = 0.001
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


class ConfigLoader:
    @staticmethod
    def load(path: str = "config.json") -> AppConfig:
        if not os.path.exists(path):
            return AppConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig(
            api_key=data.get("api_key", ""),
            api_secret=data.get("api_secret", ""),
            base_asset=data.get("base_asset", "BTC"),
            quote_asset=data.get("quote_asset", "USDT"),
            starting_cash=float(data.get("starting_cash", 10000.0)),
            fee_rate=float(data.get("fee_rate", 0.001)),
            telegram_bot_token=data.get("telegram_bot_token", ""),
            telegram_chat_id=data.get("telegram_chat_id", ""),
        )

class OrderLogger:
    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self.session_log_path = os.path.join(log_dir, "session.log")
        self.orders_csv_path = os.path.join(log_dir, "orders.csv")
        if not os.path.exists(self.orders_csv_path) or os.path.getsize(self.orders_csv_path) == 0:
            with open(self.orders_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "side", "size_base", "price", "fee_quote",
                ])

    def log_event(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(self.session_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
        print(msg)

    def send_telegram_message(self, bot_token: str, chat_id: str, text: str):
        """
        Sends a Telegram message using a bot token and chat ID.
        """
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {"chat_id": chat_id, "text": text}
            requests.post(url, data=payload, timeout=5)
        except Exception as e:
            print(f"Telegram send error: {e}")

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
        self.order_manager = OrderManager(exchange=self)
        self.toggle_trade_flag = False
        self.step = 0
        self.candle_bus = CandleEventBus()
        self.var_cash = 0
        self.var_pos = 0
        
    def _log(self, msg: str):
        if self.logger:
            self.logger.log_event(msg)
    
    def _publish_candle_event(self, candle: dict):
        self.candle_bus.publish(candle)

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

    def get_historical_prices(self, interval="1h", lookback="7 days ago UTC"):
        klines = self._safe_request(
            self.client.get_historical_klines,
            symbol=self.symbol,
            interval=interval,
            start_str=lookback
        )
        if not klines:
            return []
        return [float(k[4]) for k in klines]
    
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
            
            snapshot = {
                "cash": cash,
                "position": position,
            }
            self.var_cash = f"Cash: {cash:.2f}"
            self.var_pos = f"Position: {position:.4f}"
            # self.var_eq = f"Equity: {(position*price + cash):.4f}" # TODO need price here
            self._log(
                f"[ACCOUNT SNAPSHOT] | Cash ({self.quote_asset}): {cash} | Position ({self.base_asset}): {position} | {local_date} {local_time}"
            )
    
            return snapshot
    
        except Exception as e:
            self._log(f"ACCOUNT SNAPSHOT ERROR: {e}")
            return None
    
    def buy(self, symbol, quantity, use_market=False, orig_price=None):
        """
        Places a BUY order and handles Binance confirmation response.
        Returns a structured dict with order details or None on failure.
        """
        try:
            # Get current price
            ticker = self._safe_request(self.client.get_symbol_ticker, symbol=symbol)
            if not ticker:
                self._log("BUY ERROR: Failed to fetch ticker")
                return None
    
            current_price = float(ticker["price"])
            
            ok, new_price, new_qty = self.prepare_order(symbol, current_price, quantity)
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

    def sell(self, symbol, quantity, use_market=False, orig_price=None):
        """
        Places a SELL order and handles Binance confirmation response.
        Returns a structured dict with order details or None on failure.
        """        
        try:
            ticker = self._safe_request(self.client.get_symbol_ticker, symbol=symbol)
            if not ticker:
                self._log("SELL ERROR: Failed to fetch ticker")
                return None
            current_price = float(ticker["price"])
            ok, new_price, new_qty = self.prepare_order(symbol, current_price, quantity)
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

            if not order:
                self._log("SELL ERROR: Limit order failed")
                return None
    
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
                quantity=quantity,
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
    
            # Compute average fill price if fills exist
            if fills:
                total_cost = 0
                total_qty = 0
                total_commission = 0

                for f in fills:
                    price = float(f.get("price", 0))
                    qty = float(f.get("qty", 0))
                    commission = float(f.get("commission", 0))
    
                    total_cost += price * qty
                    total_qty += qty
                    total_commission += commission
    
                if total_qty > 0:
                    avg_price = total_cost / total_qty 
                else:
                    avg_price = market_price / orig_qty
            else:
                avg_price = market_price
                total_commission = 0
    
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
        
    def cancel_if_stale(self, symbol, order_id, placed_time_ms, timeout_seconds=20):
        """
        Cancels a GTC order if it has been open longer than timeout_seconds.
        """
        now = time.time()
        if now - placed_time_ms/1000 < timeout_seconds:
            return None  # still fresh
    
        order = self.get_order_status(symbol, order_id)
        if not order:
            return None
    
        status = order.get("status")
    
        # If still open after timeout → cancel it
        if status in ("NEW", "PARTIALLY_FILLED"):
            self._log(f"ORDER STALE | Canceling order {order_id}")
            return self.cancel_order(symbol, order_id)
    
        return None

    def cancel_if_price_invalid(self, symbol, order_id, limit_price, max_deviation_pct=0.002):
        limit_price = float(limit_price)
        ticker = self._safe_request(self.client.get_symbol_ticker, symbol=symbol)
        if not ticker:
            return None
        if "price" not in ticker:
            self._log("Ticker missing price field")
            return None

        current_price = float(ticker["price"])
        deviation = abs(current_price - limit_price) / limit_price
    
        if deviation > max_deviation_pct:
            self._log(f"ORDER PRICE DEVIATED | Canceling order {order_id}")
            return self.cancel_order(symbol, order_id)
        
class OrderManager:
    """
    Tracks all open orders and handles:
    - registration
    - status polling
    - stale timeout cancellation
    - price deviation cancellation
    - cleanup when filled or canceled
    """

    def __init__(self, exchange=None):
        self.exchange = exchange
        self.open_orders = {}
        self.closed_orders = {}
        self.logger = OrderLogger()

    def register(self, symbol, order_id, limit_price, timestamp):
        self.open_orders[order_id] = {
            "symbol": symbol,
            "limit_price": limit_price,
            "timestamp": timestamp,
            "status": "NEW"
        }
        return

    def update_status(self, order_id):
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
            symbol = self.open_orders[order_id]["symbol"]
            self.closed_orders[order_id] = self.open_orders[order_id]
            self.exchange.cancel_order(symbol, order_id)
            self.open_orders.pop(order_id, None)
            return True
        except Exception as e:
            self.logger.log_event(f"Cancel order error: {e}")
            traceback.print_exc()
            return False
        return False

    # timeout needs to come from somewhere. currently hardcoded at 4min
    def check_stale(self, order_id, timeout_seconds=270) -> bool:
        placed = self.open_orders[order_id]["timestamp"]
        if (time.time() - float(placed)/1000) > timeout_seconds:
            status, side = self.update_status(order_id)
            if status in ("NEW", "PARTIALLY_FILLED"): # and side == "BUY": # do we want to cancel sell orders too, to give them a chance to sell again, or just wait indefinitely for them to fill?
                self.logger.log_event(f"ORDER MANAGER: Canceling stale order {order_id}")
                resp = self.cancel(order_id)
                return resp
        return False

    def check_price_deviation(self, order_id, max_dev_pct=0.002):
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
            interval="5m",
            bband_period = 14,
            bband_mult = 2,
            timeout = 999999
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
        self.buy_price = None
        self.last_order_type = "SELL"   # "BUY" or "SELL"
        self.exchange = exchange
        self.symbol = symbol
        self.order = None
        self.last_rsi = None
        # self.macd_fast = app.bt_macd_fast.get()
        # self.macd_slow = app.bt_macd_slow.get()
        # self.macd_signal = app.bt_macd_signal.get()
        self.bband_period = bband_period
        self.bband_multiplier = bband_mult
        self.timeout = timeout
        self.warmup_bars = 80
        self.logger = OrderLogger()
        self._load_initial_klines()
        self.ema_score = None
        self.buy_trigger = None
        self.atr_series = []
        self.candles = []
        self.trend_filter = None
        self.prev_trend_filter = None
        self.secondary_strategy_state = "FLAT"
        self.last_eval_time = -99
        self.mr_cooldown_until = -1
        self.pft_per_strat = defaultdict(lambda: defaultdict(float))


        
    def _load_initial_klines(self):
        closes = []
        try:
            klines = self.exchange.client.get_klines(
                symbol=self.symbol,
                interval=self.interval,
                limit=self.warmup_bars
            )
            closes = [float(k[4]) for k in klines] if klines else []
            if closes is not []:
                self.prices.extend(closes)
    
            # Optionally compute initial RSI
            if len(self.prices) > self.period:
                self.last_rsi = SimpleRSI.compute(self.prices, period=self.period)
    
            self.logger.log_event(
                f"Loaded {len(self.prices)} warmup candles for {self.symbol}"
            )
    
        except Exception as e:
            self.logger.log_event(f"Warmup candle load failed: {e}")
    
    def _ema(self, prices, period):
        if len(prices) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(prices[1:period]) / len(prices[1:period])
        for p in prices[1:]:
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
            # --- EMA21 slope ---
            # slope = current EMA21 - previous EMA21
            prev_ema21 = self._ema(self.prices[:-1], 21)
            if prev_ema21 is None:
                return False
        
            # ema21_slope = ema21 - prev_ema21
            # if ema21_slope <= 0:
            #     return False
        
            lookback = 5  # 25 minutes on 5m
            prev_ema21 = self._ema(self.prices[:-lookback], 21)
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
            self.order = None
            if not candle:
                return None
            close = candle["close"]
            candle_time = candle["close_time"]
            if candle_time == self.last_eval_time:
                return
            self.last_eval_time = candle_time

            self.prices.append(float(close))
            self.candles.append(candle)
            # Update ATR every candle once enough OHLC exists
            _ = self.atr(self.candles, period=14)

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
            msg = f"[CLOSE: {local_date}, {local_time}] {self.symbol} Price: {last_price:.2f} | RSI: {self.last_rsi:>6.2f} | BB_L: {lower:>10.3f} | BB_M: {mid:>10.3f} | BB_U: {upper:>10.3f} | "
            if len(self.prices) > 55:
                msg += f" EMA21: {self._ema(self.prices, 21):>10.2f} |"
                msg += f" EMA55: {self._ema(self.prices, 55):>10.2f} |"
            
            # apply trend filter
            self.prev_trend_filter = self.trend_filter
            self.trend_filter = self.trend_filter_long()
            
            msg += f" EMA21>55: {self._ema(self.prices, 21) > self._ema(self.prices, 55)} |"
            
            self.logger.log_event(msg)
            
            # if not self.trend_filter or not self.prev_trend_filter:
            #     return None
            
            if self.last_order_type != "BUY":
                
                trade_type, buy_bool = self.buy_condition(last_price, candle_time)
                if buy_bool:
                    self.buy_price = last_price
                    self.buy_time = candle_time
                    self.last_order_type = "BUY"
                    self.order = {"side": "BUY", "size_base": self.trade_size_base, "trade_type": trade_type, "engine": getattr(self, "engine", None), "price": self.buy_price, "time": self.buy_time}                    
                    self.pft_per_strat[trade_type]['buys'] += 1
                    self.buy_type = trade_type
                    return True
    
            if self.last_order_type != "SELL":
                
                trade_type, sell_bool = self.sell_condition(last_price, candle_time)
                if sell_bool: 
                    self.last_order_type = "SELL"
                    self.order = {"side": "SELL", "size_base": self.trade_size_base, "trade_type": trade_type, "engine": getattr(self, "engine", None)}
                    profit = (last_price - self.buy_price) * self.buy_size
                    self.buy_size = 0
                    if profit > 0:
                        self.pft_per_strat[trade_type]['wins'] += 1
                        self.pft_per_strat[trade_type]['avg_win_amt'] += profit
                    else:
                        self.pft_per_strat[trade_type]['losses'] += 1
                        self.pft_per_strat[trade_type]['avg_loss_amt'] += profit
                        
                    self.pft_per_strat[trade_type]['profit'] += profit
                    self.pft_per_strat[trade_type]['sells'] += 1
                    self.sell_type = trade_type
                    self.engine = "None"
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
        
        # if self.trend_filter and self.prev_trend_filter:
            # original buy condition, disabled because testing other buy conds
        if self.rsi[-1] is not None:# and self.rsi[-1] != 0 and self.ema_score <=0.25: 
            if self.rsi[-1] < self.oversold and (self._ema(self.prices, 21) > self._ema(self.prices, 55)): # TODO TODO re-enable this, only disabled for testing order flow logic with more frequent orders
                return ("RSI2", True)
        # else:
            # Compute Bollinger Bands
            # lower, mid, upper = self._bands()
            # if lower is None:
            #     return (None, False)
            # LONG SETUP:
            # Price pierces lower band + RSI oversold
            # if price <= lower:# and self.rsi[-1] is not None and self.rsi[-1] < self.oversold:
            #     if self.secondary_strategy_state == "FLAT":
            #         self.secondary_strategy_state = "LONG"
            #         return ("LONG", True)
            # EXIT SHORT:
            # if self.secondary_strategy_state == "SHORT" and price <= mid:
            #     self.secondary_strategy_state = "FLAT"
            #     return ("SHORT", True)

        return (None, False)

    def sell_condition(self, price: float = None, trade_time=0):
        # deal with starting with position already bought and no buy size known
        if not self.buy_size:
            acc_bal = self.exchange.get_account_snapshot()
            self.buy_size = acc_bal['position']    
        if not self.buy_price:
            self.buy_price = price
            
        buy_cost = self.buy_size * self.buy_price
        sell_cost = self.buy_size * price
        trade_delta = price - self.buy_price
        fee = 0.0095/100 * sell_cost
        # deal with starting with position. dont want to trade immediately and it forgot the buy position so start where you start
        # other option is logging to disk for persistence
        
        # if self.buy_type == "RSI2":
            # if self.trend_filter:
                
        if self.rsi[-1] > self.overbought:
            return ("Overbought", True)
        elif sell_cost > (buy_cost + 2.5*fee) and trade_delta > 100:
            return ("Take Profit", True)
            # elif trade_time - self.last_trade_time > 10800000: # 3 hours
            #     return ("Timeout", True)
        # else:
        #     # Compute Bollinger Bands
        #     lower, mid, upper = self._bands()
        #     if lower is None:
        #         return (None, False)
            # EXIT LONG:
            # Price reverts to mid-band
            # if self.secondary_strategy_state == "LONG" and price >= mid:
            #     self.secondary_strategy_state = "FLAT"
            #     return ("LONG", True)
            # SHORT SETUP (optional):
            # Price pierces upper band + RSI overbought
            # if close >= upper and rsi is not None and rsi > self.rsi_ob:
            #     if self.secondary_strategy_state == "FLAT":
            #         self.secondary_strategy_state = "SHORT"
            #         return ("FLAT", True)
            # return (None, False)

        # TODO temporarily disabled next 2 lines to test conditions in real world
        # if self.rsi[-1] > self.overbought and price > self.buy_price:
        #     return ("indicator", True)
            # elif time > (self.buy_time + timeout if self.buy_time else 200 + timeout):
            #     return ("timeout", True)
        return (None, False)


class MicroPullbackVWAPRSI2(StrategyBase):
    """
    Micro pullback continuation (long-only), intended for 1m BTCUSDT.

    Entry (flat -> long):
      - EMA20 > EMA50
      - Close > session VWAP
      - ATR14 > 0.8 * SMA(ATR14, 20)
      - RSI(2) <= 10
      - Close >= EMA50
      - Cooldown: >= 10 bars since last exit

    Exit (long -> flat):
      - TP: entry + 0.03 * ATR14
      - SL: entry - 0.50 * ATR14
      - Time stop: 15 bars
    """

    name = "MicroPullback VWAP + RSI(2) (Maker-only, Long)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- Parameters (tune later) ---
        self.ema_fast = 20
        self.ema_slow = 50

        self.atr_len = 14
        self.atr_sma_len = 20
        self.atr_floor_mult = 0.8

        self.rsi_os = 10.0

        self.tp_atr_mult = 0.03
        self.sl_atr_mult = 0.25

        self.time_stop_bars = 15
        self.cooldown_bars = 10

        # --- State ---
        self.engine = "MICRO_PULLBACK"
        self.entry_idx = None
        self.tp_price = None
        self.sl_price = None
        self._cooldown_until_idx = -10**9

        # VWAP session accumulator (UTC day session by default)
        self._vwap_pv = 0.0
        self._vwap_v = 0.0
        self._vwap_session_key = None
        self.debug_block = defaultdict(int)


    # -------------------------
    # Helpers
    # -------------------------

    def _utc_day_key(self, candle_time_ms: int) -> str:
        # "YYYY-MM-DD" in UTC
        return datetime.utcfromtimestamp(candle_time_ms / 1000).strftime("%Y-%m-%d")

    def _update_session_vwap(self, candle: dict) -> float:
        """
        Session VWAP computed from candle typical price and volume.
        Resets each UTC day.
        """
        t = candle["close_time"]
        key = self._utc_day_key(t)
    
        if self._vwap_session_key != key:
            self._vwap_session_key = key
            self._vwap_pv = 0.0
            self._vwap_v = 0.0
            self._vwap_bars = 0  # <-- add a bar counter
    
        typical = (candle["high"] + candle["low"] + candle["close"]) / 3.0
        vol = float(candle.get("volume", 0.0))
    
        self._vwap_pv += typical * vol
        self._vwap_v += vol
        self._vwap_bars += 1
    
        if self._vwap_v <= 0:
            return candle["close"]
    
        return self._vwap_pv / self._vwap_v


    def _atr14(self) -> Optional[float]:
        # Your atr() appends Wilder ATR each candle, so latest is atr_series[-1]
        if not self.atr_series:
            return None
        return self.atr_series[-1]

    def _atr_sma(self, n: int) -> Optional[float]:
        if len(self.atr_series) < n:
            return None
        return sum(self.atr_series[-n:]) / n

    # -------------------------
    # Strategy hooks
    # -------------------------

    def on_price(self, candle: dict):
        """
        Override to ensure VWAP updates each candle before buy/sell logic.
        Then reuse StrategyBase.on_price() behavior by calling super().
        """
        if candle:
            self._last_vwap = self._update_session_vwap(candle)
        return super().on_price(candle)

    def buy_condition(self, price: float = None, candle_time=0):
        if len(self.prices) < max(self.ema_slow + 5, self.period + 2):
            self.debug_block["warmup"] += 1
            return (None, False)
    
        i = len(self.prices) - 1
    
        # --- Cooldown ---
        # if i < self._cooldown_until_idx:
        #     self.debug_block["cooldown"] += 1
        #     return (None, False)
    
        rsi = self.rsi[-1] if self.rsi else None
        if rsi is None:
            self.debug_block["rsi_missing"] += 1
            return (None, False)
    
        ema20 = self._ema(self.prices, self.ema_fast)
        ema50 = self._ema(self.prices, self.ema_slow)
        if ema20 is None or ema50 is None:
            self.debug_block["ema_missing"] += 1
            return (None, False)
    
        # --- VWAP warm-up ---
        if getattr(self, "_vwap_bars", 0) < 30:
            self.debug_block["vwap_warmup"] += 1
            return (None, False)
    
        vwap = getattr(self, "_last_vwap", None)
        if vwap is None:
            self.debug_block["vwap_missing"] += 1
            return (None, False)
    
        atr = self._atr14()
        atr_sma = self._atr_sma(self.atr_sma_len)
        if atr is None or atr_sma is None:
            self.debug_block["atr_missing"] += 1
            return (None, False)
    
        # --- Entry gates ---
        if not (ema20 > ema50):
            self.debug_block["ema_trend"] += 1
            return (None, False)
    
        if not (price > vwap):
            self.debug_block["vwap_position"] += 1
            return (None, False)
    
        if not (atr > self.atr_floor_mult * atr_sma):
            self.debug_block["atr_floor"] += 1
            return (None, False)
    
        # Require RSI oversold *and* reclaim
        if not (rsi <= self.rsi_os):
            self.debug_block["rsi_level"] += 1
            return (None, False)
        
        # Reclaim: price must close back above EMA20
        if price <= ema20:
            self.debug_block["no_reclaim"] += 1
            return (None, False)

        ema_slope = ema20 - self._ema(self.prices[:-5], self.ema_fast)
        if ema_slope < 0.05 * atr:
            self.debug_block["slow_drift"] += 1
            return (None, False)

        if not (price >= ema50):
            self.debug_block["ema50_hold"] += 1
            return (None, False)
    
        if self.last_order_type != "SELL":
            self.debug_block["state_lock"] += 1
            return (None, False)
    
        # --- Entry accepted ---
        self.debug_block["entry_ok"] += 1
    
        self.entry_idx = i
        self.engine = "MICRO_PULLBACK"
    
        self.tp_price = price + self.tp_atr_mult * atr
        self.sl_price = price - self.sl_atr_mult * atr
    
        return ("MicroPullback_Entry", True)




    def sell_condition(self, price: float = None, candle_time=0):
        if self.entry_idx is None or self.tp_price is None or self.sl_price is None:
            return (None, False)
    
        i = len(self.prices) - 1
        bars_held = i - self.entry_idx
    
        # --- TAKE PROFIT ---
        if price >= self.tp_price:
            # FORCE state reset immediately
            self.last_order_type = "SELL"
            return ("MicroPullback_TP", True)
    
        # --- STOP LOSS ---
        if price <= self.sl_price:
            self.last_order_type = "SELL"
            return ("MicroPullback_SL", True)
    
        # --- TIME STOP ---
        if bars_held >= self.time_stop_bars:
            self.last_order_type = "SELL"
            return ("MicroPullback_TIME", True)
    
        return (None, False)

class EMA21PullbackReclaim15m(StrategyBase):
    """
    15m EMA pullback + reclaim continuation strategy (long-only).
    Maker-realistic: enters only after reclaim confirmation.
    """

    name = "EMA21 Pullback Reclaim (15m)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.ema_fast = 21
        self.ema_slow = 50

        self.atr_len = 14
        self.atr_sma_len = 20

        self.tp_atr_mult = 0.75
        self.sl_atr_mult = 0.75
        self.time_stop_bars = 12

        self.entry_idx = None
        self.tp_price = None
        self.sl_price = None

        self.engine = "EMA21_RECLAIM"

        self.debug_block = defaultdict(int)

    def buy_condition(self, price=None, candle_time=0):
        if len(self.prices) < 100:
            self.debug_block["warmup"] += 1
            return (None, False)

        ema21 = self._ema(self.prices, self.ema_fast)
        ema50 = self._ema(self.prices, self.ema_slow)
        if ema21 is None or ema50 is None:
            self.debug_block["ema_missing"] += 1
            return (None, False)

        if ema21 <= ema50:
            self.debug_block["trend"] += 1
            return (None, False)

        atr = self.atr_series[-1] if self.atr_series else None
        atr_sma = (
            sum(self.atr_series[-self.atr_sma_len:]) / self.atr_sma_len
            if len(self.atr_series) >= self.atr_sma_len else None
        )
        if atr is None or atr_sma is None:
            self.debug_block["atr_missing"] += 1
            return (None, False)

        if atr < 0.8 * atr_sma:
            self.debug_block["low_vol"] += 1
            return (None, False)

        # EMA slope filter (blocks drift)
        ema21_prev = self._ema(self.prices[:-4], self.ema_fast)
        if ema21_prev is None:
            self.debug_block["slope_missing"] += 1
            return (None, False)

        ema_slope = ema21 - ema21_prev
        if ema_slope < 0.15 * atr:
            self.debug_block["slow_trend"] += 1
            return (None, False)

        # Pullback occurred?
        recent_closes = self.prices[-4:-1]
        if not any(c < ema21 for c in recent_closes):
            self.debug_block["no_pullback"] += 1
            return (None, False)

        # Reclaim confirmation
        if price <= ema21:
            self.debug_block["no_reclaim"] += 1
            return (None, False)

        if self.last_order_type != "SELL":
            self.debug_block["state_lock"] += 1
            return (None, False)

        # Arm trade
        self.entry_idx = len(self.prices) - 1
        self.tp_price = price + self.tp_atr_mult * atr
        self.sl_price = price - self.sl_atr_mult * atr

        self.debug_block["entry_ok"] += 1
        return ("EMA21_Reclaim_Entry", True)

    def sell_condition(self, price=None, candle_time=0):
        if self.last_order_type != "BUY":
            return (None, False)

        i = len(self.prices) - 1
        bars_held = i - self.entry_idx

        if price >= self.tp_price:
            self.last_order_type = "SELL"
            return ("EMA21_Reclaim_TP", True)

        if price <= self.sl_price:
            self.last_order_type = "SELL"
            return ("EMA21_Reclaim_SL", True)

        if bars_held >= self.time_stop_bars:
            self.last_order_type = "SELL"
            return ("EMA21_Reclaim_TIME", True)

        return (None, False)


class TrendRSIPlusCompressionMR(StrategyBase):
    name = "TrendRSI + CompressionMR (Long-only)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # tighter thresholds for chop regime
        self.mr_rsi_os = 25.0

        # debounce to prevent rapid re-entries in chop
        self._last_mr_entry_idx = None
        self.mr_min_bars_between_entries = 3

        # simple state
        self.engine = "NONE"  # "TREND_RSI" or "COMP_MR"
        self._dbg = {"comp_true": 0,
                     "touch_lower": 0,
                     "reclaim_ok": 0, 
                     "RSI2_TrendPullback": 0,
                     "BB_Lower_Reclaim_Long": 0,
                     "RSI2_Overbought": 0,
                     "Take Profit": 0, 
                     "RangeBreak_Stop": 0, 
                     "Midband_Exit": 0,
                     "RSITimeout": 0,
                     }


    def buy_condition(self, price: float = None, candle_time=0):
        rsi = self.rsi[-1] if self.rsi else None
        prev_rsi = self.rsi[-2] if self.rsi else None
        if rsi is None:
            return (None, False)
        
        # track how often each gate is true
        self._dbg = getattr(self, "_dbg", {"comp_true": 0, "touch_lower": 0, "RSI2_TrendPullback": 0, "BB_Lower_Reclaim_Long": 0})
        
        # Engine 1: Trend pullback RSI(2)
        # if self.trend_filter and self.prev_trend_filter:
        if rsi < self.oversold and self.last_order_type == "SELL" and prev_rsi < self.oversold:
            self.engine = "TREND_RSI"
            self._dbg["RSI2_TrendPullback"] += 1
            return ("RSI2_TrendPullback", True)
    
        # Engine 2: Volatility-compression mean reversion (long-only)
        if not self.regime_compression():
            return (None, False)

        lower, mid, upper = self._bands()
        if lower is None:
            return (None, False)
        
        if self.regime_compression():
            self._dbg["comp_true"] += 1
            if price <= lower:
                self._dbg["touch_lower"] += 1

        # i = len(self.prices) - 1
        # if self._last_mr_entry_idx is not None and (i - self._last_mr_entry_idx) < self.mr_min_bars_between_entries:
        #     return (None, False)

        prev_close = self.prices[-2]
        
        # Require reclaim: previous close below lower, current close above lower
        # if not (prev_close <= 1.02*lower and price > 1.02*lower): 
        #     return (None, False)
        
        if self.last_order_type == "SELL" and (prev_close <= lower and price > lower):
            self._dbg["reclaim_ok"] += 1
            # self._last_mr_entry_idx = i
            self.engine = "COMP_MR"
            self.entry_atr = self.atr_series[-1]
            self._dbg["BB_Lower_Reclaim_Long"] += 1
            return ("BB_Lower_Reclaim_Long", True)

        return (None, False)
        
    def sell_condition(self, price: float = None, candle_time=0):
        rsi = self.rsi[-1] if self.rsi else None
        prev_rsi = self.rsi[-2] if self.rsi else None
        if rsi is None:
            return (None, False)
        
        # track how often each gate is true
        self._dbg = getattr(self, "_dbg", {"comp_true": 0, "touch_lower": 0, "RSI2_Overbought": 0, "Take Profit": 0, "RangeBreak_Stop": 0, "Midband_Exit": 0, "RSITimeout": 0})
        
        # Trend RSI exit: overbought
        if self.engine == "TREND_RSI":
            time_delta = candle_time/1000 - self.buy_time/1000
            # if self.trend_filter and rsi > self.overbought and price > self.buy_price:
            if rsi > self.overbought and prev_rsi > self.overbought:
                self._dbg["RSI2_Overbought"] += 1
                return ("RSI2_Overbought", True)
            elif time_delta > 900:
                if price > self.buy_price:
                    self._dbg["Take Profit"] += 1
                    return ("Take Profit", True)

            # if still holding after 6 hours, dump. is this timeframe OK?            
            # if time_delta > self.timeout:
            #     self._dbg["RSITimeout"] += 1
            #     return ("RSITimeout", True)
            return (None, False)

        # Compression MR exits
        if self.engine == "COMP_MR":
            # 1) Hard stop on range break
            if self.entry_atr is not None:
                stop_price = self.buy_price - self.entry_atr
                if price <= stop_price:
                    self._dbg["RangeBreak_Stop"] += 1
                    return ("RangeBreak_Stop", True)
        
            # 2) Normal mean reversion exit
            lower, mid, upper = self._bands()
            if mid is not None and price >= mid:
                # self.engine = "NONE"
                self._dbg["Midband_Exit"] += 1
                return ("Midband_Exit", True)


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
                #     return True
            if self.rsi[-3] < self.oversold and self.rsi[-2] < self.oversold and self.rsi[-1] < self.oversold:
                self.buy_trigger = "triple low"
                return True
        return False

    def sell_condition(self, price: float = None, time=0):

        # if len(self.rsi) > 2: 
        #     if self.rsi[-2] > self.overbought and self.rsi[-1] > self.overbought and self.buy_trigger == "2x under":
        #         self.buy_trigger = None
        #         return ("2x under", True)
        
        # if self.buy_price is not None: 
            # if self.rsi[-1] > 90 and price > self.buy_price and self.buy_trigger != "2x under": # or time since buy is > something and loss is < some %?
            #     self.buy_trigger = None    
            #     return ("double 0", True)
        if round(self.rsi[-1]) == 100:
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
# GUI Application
# ----------------------------

class TradingApp(tk.Tk):
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
        self.exchange_info = self.exchange.client.get_exchange_info()
        self.all_symbols = [s['symbol'] for s in self.exchange_info['symbols']]
        
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
        self.send_real_orders = False
        self.telegram_enabled = True
        self.notebook.add(frm_live, text="Live Trading")
        self._build_live_ui(frm_live)
        self.buy_price = None

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
        
        self.pft_per_strat = defaultdict(lambda: defaultdict(float))

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
            try:
                self.twm.stop()
            except Exception as e:
                self.logger.log_event(f"Error stopping WebSocket: {e}")
                
    def start_kline_stream(self, symbol: str, interval: str = '5m'):
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
    
    def _on_kline_message(self, msg: dict):
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
        self.exchange._publish_candle_event(candle)
    
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

        self.bt_symbol_live = tk.StringVar(value="BTCUSDT")
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

        self.bt_interval_live = tk.StringVar(value="5m")
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
        self.bt_rsi_period_live = tk.IntVar(value=2)
        self.bt_rsi_os_live = tk.DoubleVar(value=31)
        self.bt_rsi_ob_live = tk.DoubleVar(value=70)
        self.bt_rsi_mode = tk.StringVar(value="simple")
        ttk.Label(frm_rsi, text="Mode:").grid(row=3, column=0, sticky="w")
        ttk.Combobox(frm_rsi, textvariable=self.bt_rsi_mode,
                     values=["simple", "sma"], width=10).grid(row=3, column=1)
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
        self.bt_symbol = tk.StringVar(value="BTCUSDT")
        ttk.Label(frm_controls, text="Symbol:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_symbol,
                     values=self.all_symbols, width=15).grid(row=0, column=1)
    
        # Interval
        self.bt_interval = tk.StringVar(value="5m")
        ttk.Label(frm_controls, text="Interval:").grid(row=1, column=0, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_interval,
                     values=["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"],
                     width=10).grid(row=1, column=1)
    
        # Lookback
        self.bt_period = tk.StringVar(value="1 year ago UTC")
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
        
        self.bt_rsi_mode = tk.StringVar(value="simple")
        ttk.Label(frm_controls, text="Mode:").grid(row=3, column=2, sticky="w")
        ttk.Combobox(frm_controls, textvariable=self.bt_rsi_mode,
                     values=["simple", "sma"], width=10).grid(row=3, column=3)
        
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
        self.bt_rsi_period = tk.IntVar(value=2)
        self.bt_rsi_os = tk.DoubleVar(value=31)
        self.bt_rsi_ob = tk.DoubleVar(value=70.0)
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
                klines = self.exchange._safe_request(
                    self.exchange.client.get_historical_klines,
                    symbol=symbol,
                    interval=interval,
                    start_str=lookback
                )
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
            exchange=self.exchange,          # no live exchange needed
            symbol=symbol,
            rsi_period=self.bt_rsi_period.get(),
            rsi_mode=self.bt_rsi_mode.get(),
            rsi_os=self.bt_rsi_os.get(),
            rsi_ob=self.bt_rsi_ob.get(),
            trade_size_base=0.01
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
                    fee = cost * 0.000095
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
                    fee = revenue * 0.000095
                    cash += (revenue - fee)
                    profit = (price - last_buy_price) * position - fee
                    
                    # win accounting
                    if profit > 0:
                        wins += 1
                        self.pft_per_strat[order.get("trade_type")]['wins'] += 1
                        self.pft_per_strat[order.get("trade_type")]['win_amt'] += profit
                    
                    # loss accounting
                    if profit <= 0:
                        losses += 1
                        self.pft_per_strat[order.get("trade_type")]['losses'] += 1
                        self.pft_per_strat[order.get("trade_type")]['loss_amt'] += profit
                    
                    position = 0
                    self.bt_trades.append({"index": i, "price": price, "side": "SELL"})
                    
                    
                    
                    # Hard reset state
                    self.strat.last_order_type = "SELL"
                    self.strat.entry_idx = None
                    self.strat.tp_price = None
                    self.strat.sl_price = None

    
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

        for strategy in self.strat.pft_per_strat.keys():
            if self.strat.pft_per_strat[strategy]['losses'] != 0:
                self.strat.pft_per_strat[strategy]['avg_loss_amt'] = self.strat.pft_per_strat[strategy]['loss_amt'] / self.strat.pft_per_strat[strategy]['losses']
                
            if self.strat.pft_per_strat[strategy]['wins'] != 0:
                self.strat.pft_per_strat[strategy]['avg_win_amt'] = self.strat.pft_per_strat[strategy]['win_amt'] / self.strat.pft_per_strat[strategy]['wins']
                
        # pprint.pprint(self.strat._dbg)
        pprint.pprint(self.pft_per_strat)
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
        

    # def _stop_live_trading(self):
    #     self.live_running = False
    #     self._stop_price_stream()
    #     # self.btn_start_live.config(state="normal")
    #     # self.btn_stop_live.config(state="disabled")
    #     self.logger.log_event("Requested stop of live trading")

    def _ws_watchdog(self):
        self.logger.log_event("Websocket watchdog Started - checking for kline timeout")
        # symbol = self.bt_symbol_live.get()
        # interval = self.bt_interval_live.get()
        while self.live_running:
            # 5 min is 300s, if more than 400s between klines retry
            # time.time() returns in seconds
            cur_time = time.time()
            time_delta = cur_time - self.last_kline_time
            # print(cur_time, self.last_kline_time, time_delta)
            if time_delta > 400:
                self.logger.log_event("KLINE TIMEOUT — restarting stream")
                # self._stop_price_stream()
                # self.start_kline_stream(symbol, interval=interval)
                self.last_kline_time = time.time()
                self._toggle_live_trading()
                time.sleep(30)
                self._toggle_live_trading()
            time.sleep(5)

    def _live_trading_loop(self, symbol="BTCUSDT", interval="5m"):
        
        self.perc_gain = 0.0
        self.cum_profit = 0.0
        # self.period = strat.period
        self.strat = BTRSIStrategy(
            exchange=self.exchange,
            symbol=symbol,
            rsi_period=self.bt_rsi_period_live.get(),
            rsi_mode=self.bt_rsi_mode.get(),
            rsi_os=self.bt_rsi_os.get(),
            rsi_ob=self.bt_rsi_ob.get(),
            interval = self.bt_interval_live.get(),
            )
        self.exchange.candle_bus.subscribe(self.strat.on_price)
        self.logger.log_event(f"Live trading started for {symbol} with period {self.strat.period}")
        
        # here to break early if the endpoint is not synced or some other error
        acc_info = self.exchange.get_account_snapshot()
        if acc_info['position'] > 0.001:
            self.strat.last_order_type = "BUY"
        self.var_cash.set(self.exchange.var_cash)
        self.var_pos.set(self.exchange.var_pos)
        
        index = 0
        order = None
        while self.live_running:
            try:
                time.sleep(5)
                if self.exchange.toggle_trade_flag:
                    print("Live trading automatically stopped due to clock sync NOK!")
                    self._toggle_live_trading()
                    self.exchange.toggle_trade_flag = False
                
                if self.strat.order:
                    order = self.strat.order
                if not self.strat.prices:
                    continue
                price = self.strat.prices[-1]
                last_rsi = self.strat.last_rsi if self.strat.last_rsi is not None else 0.1
                self.trade_queue.put({
                    "type": "tick",
                    "price": price,
                    "rsi": last_rsi,
                    "index": index
                })
                
                if len(self.strat.prices) < self.strat.period + 1:
                    continue
                
                if order:
                    trade_response = None
                    order_status = None
                    side = order["side"]
                    
                    acc_info = self.exchange.get_account_snapshot()
                    if acc_info is None:
                        raise ValueError("Get account snapshot failed")
                        break
                    pos = acc_info["position"]
                    cash = acc_info["cash"]
                        
                    size = pos if (pos > 0 and side == "SELL") else (0.8 * (cash / price))
                    profit = 0
                    
                    self.var_eq = cash + pos * price
                    self.logger.log_event(f"{side} signal @ {price:.2f} - RSI: {self.strat.last_rsi:.2f} - ")
                    if side == "BUY":
                        self.buy_price = price
                        self.strat.buy_size = size
                    if self.send_real_orders:
                        if side == "BUY":
                            size = 0.8 * (cash / price) # use 80% of balance to order
                            cost = size * price
                            fee = 0.0095/100 * cost
                            cost = cost + fee
                            if cash >= cost and size > 0:
                                trade_response = self.exchange.buy(symbol, quantity=size, orig_price=price)  # maker order
                                if trade_response is None:
                                    self.logger.log_event("WARNING: Maker BUY returned None. Aborting trade")
                                    order = None
                                    self.strat.order = None
                                    self.strat.last_order_type == "SELL"
                                    continue
                                if trade_response["status"] in ("REJECTED", "EXPIRED", "CANCELED"):
                                    self.logger.log_event(f"WARNING: Maker BUY returned status {trade_response['status']}. Aborting trade")
                                    order = None
                                    self.strat.order = None
                                    self.strat.last_order_type == "SELL"
                                    continue
                                if trade_response["status"] == "FILLED":
                                    if trade_response["avg_price"] is not None and trade_response["commission"] is not None:
                                        self.buy_price = trade_response["avg_price"] + trade_response["commission"]/size if size > 0 else None
    
                        if side == "SELL":
                            # print(f"pos: {pos}")
                            # print(f"self.exchange.step: {self.exchange.step}")
                            if pos > self.exchange.step: # if the balance is enough to warrant an order
                                # print(f"Selling: Position: {str(pos)}, min qty: {str(self.exchange.step)}")    
                                profit = 0.0
                                fee = 0.0095/100 * (pos*price)
                                trade_response = self.exchange.sell(symbol, quantity=pos, orig_price=price)  # maker order
                                if trade_response is None:
                                    self.logger.log_event("WARNING: Maker SELL returned None. Aborting trade")
                                    order = None
                                    self.strat.order = None
                                    self.strat.last_order_type == "BUY"
                                    continue
                                if trade_response["status"] in ("REJECTED", "EXPIRED", "CANCELED"):
                                    self.logger.log_event(f"WARNING: Maker SELL returned status {trade_response['status']}. Aborting trade")
                                    order = None
                                    self.strat.order = None
                                    self.strat.last_order_type == "BUY"
                                    continue
                                
                            else:
                                print('Warning: Not enough to sell!')
                                order = None
                                self.strat.order = None
                                self.strat.last_order_type == "SELL"
                                continue
                    self.trade_queue.put({
                        "type": "trade",
                        "side": side,
                        "price": price,
                        "index": index
                    })
                    order_status = None
                    while order_status not in ("FILLED", "REJECTED", "EXPIRED", "CANCELED", "CLOSED"):
                        time.sleep(10)
                        self.exchange.order_manager.sweep()
                        try:
                            order_status = self.exchange.order_manager.open_orders[trade_response["oid"]]["status"]
                        except KeyError:
                            # if order id is not found, it means it's filled or cancelled now. here to not trigger the continue after "order not filled at all"
                            order_status = self.exchange.order_manager.closed_orders[trade_response["oid"]]["status"]
                            break
                    # if the order wasn't filled at all, swap the trade type back so can try again. otherwise, it will try to sell whatever it bought or buy something new
                    if order_status not in ("FILLED", "PARTIALLY_FILLED"):
                        self.logger.log_event(f"Order not filled at all. Status: {order_status}. Side: {side}. Last order type: {self.strat.last_order_type}. Flipping trade cadence markers (BUY/SELL) back to try again.")
                        if self.strat.last_order_type == "BUY":
                            self.strat.last_order_type = "SELL"
                        elif self.strat.last_order_type == "SELL":
                            self.strat.last_order_type = "BUY"
                        order = None
                        self.strat.order = None
                        self.logger.log_event(f"Trade cadence flipped back to {self.strat.last_order_type}, so next trade will be the opposite.")
                        continue
                    if side == "SELL" and order is not None:
                        self.num_trades += 1
                    # build message with trade info and send to Telegram feed
                    # outside the if send_real_orders because want to see this even if doing non-trade testing
                    if self.telegram_enabled and self.config_data.telegram_bot_token and self.config_data.telegram_chat_id and order is not None:
                        exec_size = trade_response['orig_qty'] if trade_response is not None else 0
                        # this block defines and calcs things that will be sent to the telegram thread
                        if not self.send_real_orders:
                            if self.buy_price and side == "SELL":
                                profit = price - self.buy_price
                                self.cum_profit += profit
                                self.perc_gain = 100*profit/self.buy_price
                                self.buy_price = None
                        else:
                            if self.buy_price:
                                profit = float(trade_response["executed_qty"]) * (float(trade_response["avg_price"]) - float(self.buy_price)) - 2*float(trade_response["commission"])
                                price = float(trade_response["avg_price"])
                                # profit = float(trade_response["orig_qty"]) * (price - self.buy_price)
                                self.cum_profit += profit
                                self.perc_gain = 100*profit/self.buy_price
                            else:
                                self.logger.log_event(f"Calculation error: No self.buy_price! | {self.buy_price}")
                        msg = (
                            f"{side} Executed, Session trade#{self.num_trades:.0f}\n"
                            f"Price: {price}\n"
                            f"Signal: {order['trade_type']} | RSI: {last_rsi:.2f}\n"
                            f"Size: {exec_size}\n"
                            f"Profit: ${profit:.6f}\n"
                            f"% gain: {self.perc_gain:.4f}%\n"
                            f"Cumulative profit: ${self.cum_profit:.6f}\n"
                        )
                        if self.strat.ema_score:
                            msg += f"EMA Score: {self.strat.ema_score:.2f}"
                        if side == "SELL":
                            msg += f"Trade delta: ${price - self.buy_price:.6f}\n"
                        self.logger.send_telegram_message(self.config_data.telegram_bot_token, self.config_data.telegram_chat_id, msg)
                    order = None
                    self.strat.order = None
                index += 1
            except Exception as e:
                msg = f"Live trading error: {e}"
                traceback.print_exc()
                self.logger.log_event(msg)
                if self.telegram_enabled and self.config_data.telegram_bot_token and self.config_data.telegram_chat_id:
                    self.logger.send_telegram_message(self.config_data.telegram_bot_token, self.config_data.telegram_chat_id, msg)
                time.sleep(1)

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

def parse_cli_args():
    parser = argparse.ArgumentParser(description="Paper Trade App Headless Mode")

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without GUI"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Start live trading immediately"
    )

    parser.add_argument(
        "--real-orders",
        action="store_true",
        help="Send real Binance orders instead of paper trading"
    )

    return parser.parse_args()

if __name__ == "__main__":
    app = TradingApp()
    app.mainloop()