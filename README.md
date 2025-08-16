# Kraken Trade Analyzer (Interactive)

A Python tool to fetch your **Kraken** private trade history and compute:

- **VWAP** average buy/sell prices per `(asset, quote)` pair  
- **FIFO** cost basis & average buy price of remaining units  
- **Total fees**  
- **Realized PnL** (in quote currency) based on FIFO  
- **Unrealized PnL** (in quote currency) using Kraken public ticker  
- **Optional interactive balance adjustment** (e.g., if you sold elsewhere or moved to cold storage)

---

## Security

- Use a **Query-only** API key with *no trading* and *no withdrawal* permissions.  
- Load keys from **environment variables** — never hardcode them.  
- Consider Kraken’s key restrictions (IP allowlisting, expiration).  

---

## Setup

### Environment Variables

Set your Kraken API key/secret in the environment:

**macOS/Linux (bash/zsh):**
```bash
export KRAKEN_KEY='your_api_key'
export KRAKEN_SECRET='your_private_key'
```

**Windows (PowerShell):**
```powershell
$env:KRAKEN_KEY    = "your_api_key"
$env:KRAKEN_SECRET = "your_private_key"
```
To persist on Windows
```powershell
[Environment]::SetEnvironmentVariable("KRAKEN_KEY","your_api_key","User")
[Environment]::SetEnvironmentVariable("KRAKEN_SECRET","your_private_key","User")
```

**Run**
```bash
python Kraken.py
```
The script prints a summary table and writes a CSV file:
```
kraken_trade_averages.csv
```

---

## Columns Explained

| Column                        | Meaning                                                                 |
|-------------------------------|-------------------------------------------------------------------------|
| **asset / quote**             | e.g. `ETH / USD` for `ETHUSD` trades                                    |
| **total_bought / total_sold** | Total units bought/sold (from history)                                  |
| **avg_buy_price**             | VWAP average buy price (includes fees if configured)                    |
| **avg_sell_price**            | VWAP average sell price (net of fees if configured)                     |
| **net_from_history**          | `total_bought - total_sold` (in units). *Not PnL — just net units.*     |
| **remaining_unsold_volume**   | Units still “held” per FIFO lots                                        |
| **avg_buy_price_of_remaining**| Average cost of remaining units                                         |
| **fees_total**                | Sum of fees across buys and sells (quote currency)                      |
| **realized_pnl**              | Profit/loss realized from Kraken-tracked sells (FIFO, in quote currency)|
| **current_price**             | Latest Kraken price (last trade or mid, depending on config)            |
| **unrealized_pnl**            | PnL on remaining lots if sold at current price (quote currency)         |

---

## Interactive Balance Adjustment

At runtime you’ll be asked:
```markdown
Adjust current balances? (Y/N):
```

- **N (No)** → The script continues using the raw Kraken trade history.  
- **Y (Yes)** →  
  1. The script lists all assets with **remaining unsold volume** (according to your Kraken history).  
  2. You can select which ones to adjust (e.g., `1,3,5` or `all`).  
  3. For each, you set a **target remaining volume** (often `0` if you sold everything elsewhere).  
  4. The script shrinks the FIFO lots to that target.  

> Adjustments **do not** change realized PnL — because Kraken doesn’t know about trades you did outside of Kraken. They only affect the **remaining balance**, **average buy price of remaining**, and **unrealized PnL**.

---

## Configuration Options

You can customize the behavior of the script by editing the variables at the top of **`Kraken.py`**:

```python
INCLUDE_FEES_IN_COST = True   # include fees in buy cost / subtract from sell proceeds
ONLY_THESE_QUOTES = None      # e.g. {"USD", "USDT"} to limit analysis to certain quote currencies
REQUEST_SLEEP = 0.2           # pacing for pagination; increase if you hit rate limits
USE_MIDPRICE = False          # True = use (bid+ask)/2, False = use last trade price
```

### What they mean

| Option                   | Description                                                                                      |
|--------------------------|--------------------------------------------------------------------------------------------------|
| **INCLUDE_FEES_IN_COST** | `True`: Buy cost = cost + fee, Sell proceeds = proceeds − fee. <br> `False`: Ignores fees.       |
| **ONLY_THESE_QUOTES**    | Restrict analysis to specific quote currencies, e.g. `{"USD", "USDT"}`. Leave `None` for all.    |
| **REQUEST_SLEEP**        | Delay (seconds) between API page requests. Increase if you hit `EAPI:Rate limit exceeded`.       |
| **USE_MIDPRICE**         | `True`: Use midpoint of bid/ask as current price. <br> `False`: Use last trade price from Kraken. |

---

## Rate Limits

Kraken’s private API endpoints (like `TradesHistory`) enforce strict rate limits.  
If you make too many requests too quickly, you may see an error:

```yaml
EAPI:Rate limit exceeded
```

This script already handles rate limits automatically by using **exponential backoff with jitter**.  
That means when Kraken says “slow down,” the script waits a bit longer each time and then retries.

### If you still hit rate limits:

- Increase `REQUEST_SLEEP` in `Kraken.py` (e.g., from `0.2` → `1.0` or `2.0`)  
- Avoid running multiple copies of the script at once with the same API key  
- Ensure your API key has only the permissions you need (Query-only)  

---

## Troubleshooting

Common issues and how to resolve them:

| Issue / Error                                     | Solution                                                                                   |
|---------------------------------------------------|-------------------------------------------------------------------------------------------|
| **`Set KRAKEN_KEY and KRAKEN_SECRET`**            | You need to set your environment variables (see **Setup → Environment Variables**).        |
| **No trades found**                               | Ensure your API key has the **Query Trades** permission enabled.                          |
| **`EAPI:Rate limit exceeded`**                    | Increase `REQUEST_SLEEP` in `Kraken.py` (e.g., `1.0–2.0`) and avoid running multiple instances in parallel. |
| **Weird pair names** (e.g. `XETHZUSD`, `XXBTZUSD`)| Kraken uses legacy codes. The script automatically normalizes them (e.g., `XBT → BTC`, `XETHZUSD → ETH/USD`). |
| **CSV not created**                               | Check you have write permissions in the directory. Ensure the script runs without exceptions. |

---

## Safety & Good Hygiene

To keep your Kraken account safe while using this script:

- **Use Query-only API keys**  
  Do not grant trading or withdrawal permissions. Query-only is enough to fetch history.

- **Never hardcode secrets**  
  Always load API keys from environment variables or a `.env` file (ignored in Git).

- **.gitignore** best practices  
  Add these to `.gitignore` if you’re committing this project:
  ```yaml
  pycache/
  *.csv
  ```
  
This ensures you don’t leak secrets or generated files.

- **Rate limits**  
Be patient with Kraken’s API. The script already retries on `EAPI:Rate limit exceeded`, but you can increase `REQUEST_SLEEP` to reduce throttling.

- **Analytics only**  
This tool is meant for **portfolio analytics**, not for live trading. Don’t reuse the API key for trading bots.

---

## License

This project is licensed under the **NonCommercial Software License**.  

- Free to use, copy, and modify for **personal, educational, or research purposes**.  
- **Commercial use is prohibited**. This includes selling, sublicensing, or using the software in paid products/services.  
- Redistributions must keep this license and attribution.  

See the [LICENSE](./LICENSE) file for the full terms.

---

## Disclaimer

This tool is provided **for educational and analytics purposes only**.  

- It is **not** financial, investment, tax, or accounting advice.  
- Numbers are based on your Kraken trade history and may not fully reflect your entire portfolio.  
- Always double-check results before relying on them for trading or tax reporting.  

Use at your own risk.


  