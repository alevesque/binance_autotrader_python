import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import time
import csv
import datetime

BINANCE_US_API_URL = "https://api.binance.us/api/v3/klines"

def fetch_klines(symbol, interval, start_ms, end_ms, max_retries=5):
    """
    Fetch kline data for a given time window with retry logic.
    """
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000
    }

    retries = 0
    while retries < max_retries:
        try:
            response = requests.get(BINANCE_US_API_URL, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            wait_time = 2 ** retries
            print(f"Error: {e}. Retrying in {wait_time} seconds...")
            time.sleep(wait_time)
            retries += 1

    raise Exception("Failed to fetch data after multiple retries.")

def chunked_fetch(symbol, interval, start_date, end_date):
    """
    Fetch ALL klines for a symbol/interval without skipping any candles.
    Dynamically adjusts chunk size so each request returns <= 1000 candles.
    """

    # Minutes per interval
    interval_to_minutes = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "6h": 360,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
        "3d": 4320,
        "1w": 10080,
        "1M": 43200
    }

    minutes = interval_to_minutes[interval]

    # Max time span per request = 1000 candles
    chunk_minutes = minutes * 1000
    chunk_delta = datetime.timedelta(minutes=chunk_minutes)

    start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")

    all_data = []
    current_start = start_dt

    while current_start < end_dt:
        current_end = min(current_start + chunk_delta, end_dt)

        start_ms = int(current_start.timestamp() * 1000)
        end_ms = int(current_end.timestamp() * 1000)

        data = fetch_klines(symbol, interval, start_ms, end_ms)

        # Append results
        all_data.extend(data)

        # Move to next chunk
        if data:
            # Use last candle close time to avoid overlap
            last_close_time = data[-1][6]  # index 6 = close time in ms
            current_start = datetime.datetime.utcfromtimestamp(last_close_time / 1000)
        else:
            # No data returned — advance by chunk_delta to avoid infinite loop
            current_start = current_end

    return all_data

def download_batch():
    """
    GUI callback to download multiple trading pairs with chunking + progress bar.
    """
    symbols = symbols_entry.get().split(",")
    interval = interval_combo.get()
    start_date = start_entry.get()
    end_date = end_entry.get()

    if not symbols or not interval or not start_date or not end_date:
        messagebox.showerror("Error", "Please fill in all fields.")
        return

    filename = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV files", "*.csv")],
        title="Save Batch Kline Data"
    )
    if not filename:
        return

    progress_bar["maximum"] = len(symbols)
    progress_bar["value"] = 0
    root.update_idletasks()

    try:
        with open(filename, mode="w", newline="") as file:
            writer = csv.writer(file)
            headers = [
                "Symbol", "Open Time", "Open", "High", "Low", "Close", "Volume",
                "Close Time", "Quote Asset Volume", "Number of Trades",
                "Taker Buy Base Asset Volume", "Taker Buy Quote Asset Volume", "Ignore"
            ]
            writer.writerow(headers)

            for i, symbol in enumerate(symbols, start=1):
                data = chunked_fetch(symbol.strip(), interval, start_date, end_date)
                for row in data:
                    writer.writerow([symbol.strip()] + row)
                progress_bar["value"] = i
                root.update_idletasks()

        messagebox.showinfo("Success", f"Batch kline data saved to {filename}")
    except Exception as e:
        messagebox.showerror("Error", str(e))

# ---------------- GUI ----------------
root = tk.Tk()
root.title("Binance.US Kline Downloader (Chunked)")

frame = ttk.Frame(root, padding="10")
frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))


ttk.Label(frame, text="Trading Pairs (comma-separated, e.g., BTCUSDT,ETHUSDT):").grid(row=0, column=0, sticky=tk.W)
symbols_entry = ttk.Entry(frame, width=40)
symbols_entry.grid(row=0, column=1)
symbols_entry.insert(0, "BTCUSDT") # DEFAULT

ttk.Label(frame, text="Interval:").grid(row=1, column=0, sticky=tk.W)
interval_combo = ttk.Combobox(frame, values=[
    "1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"
], width=10)
interval_combo.grid(row=1, column=1)
interval_combo.set("15m") # DEFAULT

ttk.Label(frame, text="Start Date (YYYY-MM-DD):").grid(row=2, column=0, sticky=tk.W)
start_entry = ttk.Entry(frame, width=20)
start_entry.grid(row=2, column=1)
start_entry.insert(0, "2023-01-01") # DEFAULT

ttk.Label(frame, text="End Date (YYYY-MM-DD):").grid(row=3, column=0, sticky=tk.W)
end_entry = ttk.Entry(frame, width=20)
end_entry.grid(row=3, column=1)
end_entry.insert(0, "2025-12-31") # DEFAULT

download_button = ttk.Button(frame, text="Download Batch Data", command=download_batch)
download_button.grid(row=4, column=0, columnspan=2, pady=10)

progress_bar = ttk.Progressbar(frame, orient="horizontal", length=300, mode="determinate")
progress_bar.grid(row=5, column=0, columnspan=2, pady=5)

root.mainloop()