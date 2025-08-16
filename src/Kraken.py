#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kraken Trade Analyzer (interactive)
-----------------------------------
Pulls your private trade history from Kraken and computes:

- VWAP average buy/sell prices per (base, quote) pair
- FIFO cost basis & average buy price for remaining unsold units
- Total fees
- Realized PnL (in quote currency) based on FIFO
- Unrealized PnL (in quote currency) using current ticker price
- Optional: interactively adjust remaining balances to account for trades done on other exchanges/wallets

Why FIFO?
  FIFO (first-in-first-out) is a common, simple accounting method that matches sells against the oldest buys first.

Security:
  Use an API key with **Query-only** permissions (no trading/withdrawals).
  Load keys from environment variables — never hardcode secrets.

Requirements:
  pip install krakenex

Environment Variables:
  KRAKEN_KEY, KRAKEN_SECRET
"""

import os
import csv
import sys
import time
from decimal import Decimal, getcontext
from collections import defaultdict, deque

import krakenex
import random

# ========= Config (tweak as you like) =========
INCLUDE_FEES_IN_COST = True   # True => buy cost includes fees; sell proceeds are net of fees
ONLY_THESE_QUOTES = None      # e.g., {"USD", "USDT"} to only analyze those quotes; None for all
CSV_OUT = "kraken_trade_averages.csv"
REQUEST_SLEEP = 0.2           # base pacing for pagination; private endpoints are stricter
USE_MIDPRICE = False          # True => use mid (bid+ask)/2; False => last traded price

# Robust rate-limit handling (exponential backoff with jitter)
MAX_RETRIES = 8               # maximum backoff attempts per call
BASE_BACKOFF = 0.8            # starting backoff seconds
BACKOFF_JITTER = 0.35         # +/- random jitter to avoid thundering herds

# Use high precision decimals for financial math
getcontext().prec = 28

# ========= Rate limit helpers =========
def is_rate_limit_error(resp):
    """
    Kraken errors come back in resp["error"] (list).
    We detect the "EAPI:Rate limit exceeded" style messages and trigger backoff.
    """
    if not isinstance(resp, dict):
        return False
    errs = resp.get("error") or []
    if isinstance(errs, list):
        msg = " ".join(errs).lower()
    else:
        msg = str(errs).lower()
    return ("rate limit" in msg) or ("exceeded" in msg)

def kraken_private_with_retry(k, endpoint, params=None):
    """
    Wrapper for private endpoints with exponential backoff on rate-limit.
    Will retry up to MAX_RETRIES before returning the last response (caller raises).
    """
    params = params or {}
    tries = 0
    while True:
        resp = k.query_private(endpoint, params)
        if not is_rate_limit_error(resp):
            return resp
        # Backoff with jitter
        sleep_s = (BASE_BACKOFF * (2 ** tries)) * (1 + random.uniform(-BACKOFF_JITTER, BACKOFF_JITTER))
        sleep_s = max(sleep_s, 0.2)
        time.sleep(sleep_s)
        tries += 1
        if tries > MAX_RETRIES:
            # Give up; caller will inspect resp["error"]
            return resp

# ========= Small utility helpers =========
def d(x): return Decimal(str(x))
def safe_div(n, dnm): return (n / dnm) if dnm != 0 else Decimal("0")

def parse_pair(pair: str):
    """
    Normalize Kraken pair strings into (base, quote).
    Handles formats like 'XXBTZUSD', 'XETHZUSD', 'ETHUSD', 'ETH/USDT'.
    Maps XBT -> BTC for familiarity.
    """
    if not pair:
        return "", ""
    p = pair.replace("/", "").upper().replace("XBT", "BTC")
    # Legacy "BASEZQUOTE" format (e.g. XETHZUSD)
    if "Z" in p and len(p) >= 7:
        i = p.rfind("Z")
        left, right = p[:i], p[i+1:]
        if left and right and 3 <= len(right) <= 4:
            if left.startswith("X") and len(left) >= 2:
                left = left[1:]  # drop leading 'X'
            return left, right
    # Otherwise, assume last 3–4 chars are quote symbol
    for qlen in (4, 3):
        if len(p) > qlen:
            return p[:-qlen], p[-qlen:]
    return p, ""

# ========= Output helpers =========
def pretty_print(rows):
    """Print the final summary table to stdout."""
    if not rows:
        print("No trades found.")
        return
    headers = [
        "asset","quote","total_bought","avg_buy_price",
        "total_sold","avg_sell_price","net_from_history",
        "remaining_unsold_volume","avg_buy_price_of_remaining",
        "fees_total","realized_pnl","current_price","unrealized_pnl"
    ]
    widths = {h: max(len(h), max((len(r[h]) for r in rows), default=0)) for h in headers}
    line = " | ".join(h.ljust(widths[h]) for h in headers)
    print(line)
    print("-" * len(line))
    for r in rows:
        print(" | ".join(r[h].ljust(widths[h]) for h in headers))

def write_csv(rows, path):
    """Write the summary table to CSV so you can open it in Excel/Sheets."""
    if not rows:
        return
    headers = [
        "asset","quote","total_bought","avg_buy_price",
        "total_sold","avg_sell_price","net_from_history",
        "remaining_unsold_volume","avg_buy_price_of_remaining",
        "fees_total","realized_pnl","current_price","unrealized_pnl"
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {path}")

# ========= Krakenex client & fetch =========
def get_client():
    """
    Build a Krakenex client using env vars.
    API key should be Query-only. Do NOT grant withdraw/trade unless you know what you’re doing.
    """
    api_key = os.environ.get("KRAKEN_KEY")
    api_secret = os.environ.get("KRAKEN_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("Set KRAKEN_KEY and KRAKEN_SECRET environment variables.")
    return krakenex.API(key=api_key, secret=api_secret)

def fetch_all_trades(k):
    """
    Page through TradesHistory and return a flat list of trade dicts.
    Kraken returns trades in pages via 'ofs'. We respect rate limits and pace requests.
    """
    trades = []
    ofs = 0
    while True:
        resp = kraken_private_with_retry(k, "TradesHistory", {"ofs": ofs})
        if resp.get("error"):
            raise RuntimeError(f"TradesHistory error: {resp['error']}")
        result = resp.get("result", {}) or {}
        trades_map = result.get("trades", {}) or {}
        page = list(trades_map.values())
        trades.extend(page)
        count = result.get("count", 0)
        ofs += len(page)
        if ofs >= count:
            break
        # Private endpoints are stricter; go slower if you still see throttling
        time.sleep(max(REQUEST_SLEEP, 0.8))
    return trades

def fetch_current_prices(k, pair_names):
    """
    Use the public Ticker endpoint to fetch current prices for the Kraken-native pair names we saw in history.
    Returns: { pair_name: Decimal(price) }
    """
    if not pair_names:
        return {}
    joined = ",".join(sorted(pair_names))
    resp = k.query_public("Ticker", {"pair": joined})
    if resp.get("error"):
        return {}
    result = resp.get("result", {}) or {}
    prices = {}
    for pair_name, data in result.items():
        if USE_MIDPRICE:
            # midpoint of best bid/ask
            bid = d((data.get("b", ["0"])[0]))
            ask = d((data.get("a", ["0"])[0]))
            prices[pair_name] = safe_div((bid + ask), d(2))
        else:
            # last traded price
            last = data.get("c", ["0"])[0]
            prices[pair_name] = d(last)
    return prices

# ========= Core aggregation (build lots & stats) =========
def aggregate_trades(trades):
    """
    Build an aggregated state per (base, quote):
      - Totals for buys/sells/fees
      - FIFO lot deque representing remaining units (for unrealized PnL & avg cost of remaining)
      - Realized PnL based on FIFO (only Kraken-tracked sells)
    """
    agg = defaultdict(lambda: {
        "buy_vol": Decimal("0"),
        "buy_cost": Decimal("0"),
        "sell_vol": Decimal("0"),
        "sell_proceeds": Decimal("0"),
        "fees": Decimal("0"),
        "lots": deque(),      # each lot: [remaining_vol, unit_cost, total_cost]
        "realized_pnl": Decimal("0"),
        "last_ts": 0.0,
        "pair_name": None,    # remember a Kraken-native pair name for pulling Ticker later
    })

    for t in trades:
        try:
            pair_name = (t.get("pair") or "").strip()  # e.g., XXBTZUSD
            typ       = (t.get("type") or "").lower()  # 'buy' | 'sell'
            vol       = d(t.get("vol") or "0")
            price     = d(t.get("price") or "0")
            cost      = d(t.get("cost") or (vol * price))  # Kraken usually provides 'cost'
            fee       = d(t.get("fee") or "0")
            ts        = float(t.get("time") or 0)
        except Exception:
            # Skip malformed rows just in case
            continue

        base, quote = parse_pair(pair_name)
        if ONLY_THESE_QUOTES and quote not in ONLY_THESE_QUOTES:
            continue

        rec = agg[(base, quote)]
        rec["last_ts"] = max(rec["last_ts"], ts)
        if not rec["pair_name"]:
            rec["pair_name"] = pair_name  # store one example name

        if typ == "buy":
            # Add buy to FIFO (including fee if configured)
            buy_cost = cost + (fee if INCLUDE_FEES_IN_COST else Decimal("0"))
            rec["buy_vol"] += vol
            rec["buy_cost"] += buy_cost
            rec["fees"] += fee
            unit_cost = safe_div(buy_cost, vol) if vol > 0 else Decimal("0")
            rec["lots"].append([vol, unit_cost, buy_cost])

        elif typ == "sell":
            # Compute sell proceeds (net of fees if configured)
            proceeds = cost - (fee if INCLUDE_FEES_IN_COST else Decimal("0"))
            rec["sell_vol"] += vol
            rec["sell_proceeds"] += proceeds
            rec["fees"] += fee
            per_unit_proceeds = safe_div(proceeds, vol) if vol > 0 else Decimal("0")

            # FIFO: match this sell against oldest buy lots to compute realized PnL
            remaining = vol
            realized = Decimal("0")
            while remaining > 0 and rec["lots"]:
                lot_vol, lot_px, _lot_cost = rec["lots"][0]
                use = min(lot_vol, remaining)
                lot_cost_used = use * lot_px
                portion_proceeds = use * per_unit_proceeds
                realized += portion_proceeds - lot_cost_used

                lot_vol -= use
                remaining -= use
                if lot_vol <= 0:
                    rec["lots"].popleft()
                else:
                    rec["lots"][0][0] = lot_vol
                    rec["lots"][0][2] = lot_vol * lot_px
            rec["realized_pnl"] += realized

    return agg

# ========= Interactive adjustments =========
def total_remaining(rec):
    """Return (remaining_volume, remaining_cost) over all FIFO lots for a pair."""
    rem_vol = sum((lot[0] for lot in rec["lots"]), Decimal("0"))
    rem_cost = sum((lot[2] for lot in rec["lots"]), Decimal("0"))
    return rem_vol, rem_cost

def shrink_lots_fifo_to_target(rec, target_vol):
    """
    Reduce remaining FIFO lots down to 'target_vol' (if target is smaller).
    This simulates external sales (e.g., you sold on another CEX or moved to cold wallet).
    NOTE: This does NOT change realized PnL since those external trades aren't in Kraken history.
    """
    current_vol, _ = total_remaining(rec)
    target_vol = d(target_vol)
    if target_vol >= current_vol:
        return  # We don't "add" external buys; only shrink
    to_reduce = current_vol - target_vol
    while to_reduce > 0 and rec["lots"]:
        lot_vol, lot_px, _lot_cost = rec["lots"][0]
        use = min(lot_vol, to_reduce)
        lot_vol -= use
        to_reduce -= use
        if lot_vol <= 0:
            rec["lots"].popleft()
        else:
            rec["lots"][0][0] = lot_vol
            rec["lots"][0][2] = lot_vol * lot_px

def maybe_adjust_balances(agg):
    """
    Ask the user if they want to adjust remaining balances.
    If yes: show all pairs that still have remaining volume, let the user select,
    and set a target remaining volume (usually 0 if fully sold elsewhere).
    """
    try:
        answer = input("Adjust current balances? (Y/N): ").strip().lower()
    except EOFError:
        return  # Non-interactive environment (e.g., cron), skip prompts
    if answer not in ("y", "yes"):
        return

    # Build list of adjustable entries (those with remaining inventory)
    items = []
    for (base, quote), rec in sorted(agg.items()):
        rem_vol, _rem_cost = total_remaining(rec)
        if rem_vol > 0:
            items.append(((base, quote), rec, rem_vol))

    if not items:
        print("Nothing to adjust (no remaining inventory from history).")
        return

    # Show a simple menu
    print("\nSelect which assets to adjust (by index, comma-separated) or type 'all':")
    for idx, (key, rec, rem_vol) in enumerate(items, start=1):
        base, quote = key
        print(f"[{idx}] {base}/{quote}  remaining={rem_vol}")

    try:
        choice = input("Your choice: ").strip().lower()
    except EOFError:
        return

    if choice == "all":
        indices = list(range(1, len(items)+1))
    else:
        try:
            indices = [int(x) for x in choice.split(",") if x.strip().isdigit()]
            indices = [i for i in indices if 1 <= i <= len(items)]
        except Exception:
            print("No valid selection; skipping adjustments.")
            return
        if not indices:
            print("No valid selection; skipping adjustments.")
            return

    # Prompt a target remaining volume for each selected entry and shrink lots
    for i in indices:
        (base, quote), rec, rem_vol = items[i-1]
        while True:
            try:
                target_str = input(f"Set target remaining volume for {base}/{quote} (current {rem_vol}, usually 0): ").strip()
            except EOFError:
                return
            try:
                target = d(target_str)
                if target < 0:
                    print("Target cannot be negative.")
                    continue
                shrink_lots_fifo_to_target(rec, target)
                break
            except Exception:
                print("Please enter a valid number (e.g., 0 or 0.123456).")

# ========= Build final rows (prices, unrealized PnL) =========
def build_rows_with_prices(agg, k):
    """
    Augment the aggregated data with current prices and compute unrealized PnL.
    Returns a list of serializable dicts for display/CSV.
    """
    pair_names = {r["pair_name"] for r in agg.values() if r["pair_name"]}
    prices_by_pair = fetch_current_prices(k, pair_names)

    rows = []
    for (base, quote), r in sorted(agg.items()):
        buy_vol = r["buy_vol"]
        sell_vol = r["sell_vol"]
        buy_cost = r["buy_cost"]
        sell_proceeds = r["sell_proceeds"]

        avg_buy = safe_div(buy_cost, buy_vol) if buy_vol > 0 else Decimal("0")
        avg_sell = safe_div(sell_proceeds, sell_vol) if sell_vol > 0 else Decimal("0")

        remaining_vol = sum((lot[0] for lot in r["lots"]), Decimal("0"))
        remaining_cost = sum((lot[2] for lot in r["lots"]), Decimal("0"))
        remaining_avg_buy = safe_div(remaining_cost, remaining_vol) if remaining_vol > 0 else Decimal("0")

        current_price = prices_by_pair.get(r["pair_name"], Decimal("0"))

        # Unrealized PnL = sum over remaining lots: (current_price - lot_unit_cost) * lot_vol
        unrealized = Decimal("0")
        if current_price > 0 and remaining_vol > 0:
            for lot_vol, lot_px, _lot_cost in r["lots"]:
                unrealized += (current_price - lot_px) * lot_vol

        rows.append({
            "asset": base,
            "quote": quote,
            "total_bought": str(buy_vol),
            "avg_buy_price": str(avg_buy),
            "total_sold": str(sell_vol),
            "avg_sell_price": str(avg_sell),
            "net_from_history": str(buy_vol - sell_vol),  # units, not money
            "remaining_unsold_volume": str(remaining_vol),
            "avg_buy_price_of_remaining": str(remaining_avg_buy),
            "fees_total": str(r["fees"]),
            "realized_pnl": str(r["realized_pnl"]),       # realized PnL in quote currency
            "current_price": str(current_price),          # price from Ticker
            "unrealized_pnl": str(unrealized),            # unrealized PnL in quote currency
        })
    return rows

# ========= Main =========
def main():
    k = get_client()
    print("Fetching trades from Kraken (read-only)…")
    trades = fetch_all_trades(k)
    print(f"Fetched {len(trades)} trades.")

    # 1) Aggregate trades into FIFO lots + stats
    agg = aggregate_trades(trades)

    # 2) Optional interactive adjustment of remaining balances
    maybe_adjust_balances(agg)

    # 3) Compute prices + unrealized PnL and render outputs
    rows = build_rows_with_prices(agg, k)
    pretty_print(rows)
    write_csv(rows, CSV_OUT)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e)
        sys.exit(1)