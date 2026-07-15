"""
nse_data.py
===========
NSE-only data layer (no yfinance / Yahoo Finance anywhere).

Provides:
    - NSESession            : cookie warm-up + retry/backoff HTTP session for nseindia.com
    - get_nifty50_symbols   : live Nifty 50 constituent list (falls back to a hardcoded list)
    - fetch_live_quote      : REAL-TIME last traded price + live session VWAP per stock
    - fetch_intraday_series : today's minute-by-minute price series (for a live EMA9)
    - fetch_daily_history   : daily OHLCV + NSE's official VWAP field (fallback / historical)
    - compute_ema9          : EMA helper
    - classify              : Bullish / Bearish / Mixed classification

Real-time data
---------------
NSE's `/api/quote-equity` endpoint returns a live `priceInfo.vwap` value that
updates continuously through the trading session (Turnover / Volume so far
today) alongside `priceInfo.lastPrice` — this is genuine real-time VWAP, not
a stale daily figure.

For EMA9, NSE doesn't provide a "live EMA" number directly, so this module
pulls today's intraday price series from `/api/chart-databyindex` (the same
feed that powers charting.nseindia.com) and computes a 9-period EMA on that
series. Every time you re-fetch during market hours, both numbers reflect
the current live market, not yesterday's close.

`fetch_daily_history` (daily OHLCV + VWAP) is kept as a fallback for
after-hours use or historical charting, but the live functions are what the
app now uses for screening.
"""

import time
import random
import datetime

import requests
import pandas as pd

# ─────────────────────────────────────────────────────────────
#  NSE SESSION — cookie warm-up, headers, retry/backoff
# ─────────────────────────────────────────────────────────────
class NSESession:
    BASE = "https://www.nseindia.com"
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._warm()

    def _warm(self):
        try:
            self.session.get(self.BASE, timeout=10)
            time.sleep(0.4)
            self.session.get(self.BASE + "/get-quotes/equity?symbol=RELIANCE", timeout=10)
        except Exception:
            pass

    def get_json(self, url, retries=3):
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=12)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        pass
                self._warm()
                time.sleep(1.5 + attempt + random.random())
            except Exception:
                time.sleep(1.5 + attempt + random.random())
        return None


# ─────────────────────────────────────────────────────────────
#  UNIVERSE — NIFTY 50 (fallback list; also fetched live from NSE)
# ─────────────────────────────────────────────────────────────
NIFTY50_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "LT", "SBIN",
    "BHARTIARTL", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "M&M",
    "MARUTI", "SUNPHARMA", "NTPC", "TATAMOTORS", "TITAN", "ASIANPAINT",
    "ULTRACEMCO", "BAJAJFINSV", "WIPRO", "ADANIENT", "POWERGRID", "HCLTECH",
    "JSWSTEEL", "TATASTEEL", "COALINDIA", "GRASIM", "NESTLEIND", "TECHM",
    "INDUSINDBK", "HINDALCO", "CIPLA", "DRREDDY", "EICHERMOT", "BPCL", "ONGC",
    "SBILIFE", "HDFCLIFE", "BAJAJ-AUTO", "BRITANNIA", "APOLLOHOSP", "DIVISLAB",
    "TATACONSUM", "LTIM", "ADANIPORTS", "SHRIRAMFIN", "TRENT",
]


def get_nifty50_symbols(nse: NSESession) -> list:
    """Live Nifty 50 constituent list from NSE; falls back to hardcoded list."""
    url = f"{NSESession.BASE}/api/equity-stockIndices?index=NIFTY%2050"
    data = nse.get_json(url)
    if data and "data" in data:
        symbols = [row["symbol"] for row in data["data"] if row.get("symbol") != "NIFTY 50"]
        if len(symbols) >= 45:
            return sorted(symbols)
    return sorted(NIFTY50_FALLBACK)


# ─────────────────────────────────────────────────────────────
#  REAL-TIME — live quote (LTP + live session VWAP)
# ─────────────────────────────────────────────────────────────
def fetch_live_quote(nse: NSESession, symbol: str) -> dict | None:
    """
    Live snapshot for one stock: last traded price + live session VWAP,
    straight from NSE's quote-equity endpoint. Updates continuously during
    market hours (VWAP = cumulative turnover / cumulative volume so far today).
    """
    url = f"{NSESession.BASE}/api/quote-equity?symbol={requests.utils.quote(symbol)}"
    data = nse.get_json(url)
    if not data:
        return None

    price_info = data.get("priceInfo", {})
    metadata = data.get("metadata", {})

    last_price = price_info.get("lastPrice")
    vwap = price_info.get("vwap")
    if last_price is None or vwap is None:
        return None

    return {
        "symbol": symbol,
        "lastPrice": float(last_price),
        "vwap": float(vwap),
        "open": price_info.get("open"),
        "previousClose": price_info.get("previousClose"),
        "change": price_info.get("change"),
        "pChange": price_info.get("pChange"),
        "lastUpdateTime": metadata.get("lastUpdateTime"),
    }


# ─────────────────────────────────────────────────────────────
#  REAL-TIME — today's intraday price series (for a live EMA9)
# ─────────────────────────────────────────────────────────────
def fetch_intraday_series(nse: NSESession, symbol: str) -> pd.DataFrame | None:
    """
    Today's minute-level price series for one stock, from the same feed that
    powers charting.nseindia.com. Used to compute a live-updating EMA9 — as
    more of today's session prints, the EMA9 value updates with it.
    """
    url = (
        f"{NSESession.BASE}/api/chart-databyindex"
        f"?index={requests.utils.quote(symbol + 'EQN')}&indices=false"
    )
    payload = nse.get_json(url)
    if not payload:
        return None

    points = payload.get("grapthData") or payload.get("graphData")
    if not points:
        return None

    df = pd.DataFrame(points, columns=["ts", "price"])
    if df.empty:
        return None

    # NSE's chart feed uses epoch milliseconds
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["ts", "price"]).sort_values("ts").set_index("ts")

    # Resample to 1-minute bars (raw feed can be tick-level / noisy)
    close = df["price"].resample("1min").last().dropna()
    if close.empty:
        return None

    out = close.to_frame("Close")
    out["EMA9"] = out["Close"].ewm(span=9, adjust=False).mean()
    return out


# ─────────────────────────────────────────────────────────────
#  DATA FETCH — daily OHLCV + VWAP history per stock
# ─────────────────────────────────────────────────────────────
def fetch_daily_history(nse: NSESession, symbol: str, days: int) -> pd.DataFrame | None:
    to_date = datetime.date.today()
    from_date = to_date - datetime.timedelta(days=days)

    url = (
        f"{NSESession.BASE}/api/historical/cm/equity"
        f"?symbol={requests.utils.quote(symbol)}"
        f"&series=[%22EQ%22]"
        f"&from={from_date.strftime('%d-%m-%Y')}"
        f"&to={to_date.strftime('%d-%m-%Y')}"
    )
    payload = nse.get_json(url)
    if not payload or "data" not in payload or not payload["data"]:
        return None

    df = pd.DataFrame(payload["data"])
    if df.empty:
        return None

    rename_map = {
        "CH_TIMESTAMP": "Date",
        "TIMESTAMP": "Date",
        "CH_OPENING_PRICE": "Open",
        "CH_TRADE_HIGH_PRICE": "High",
        "CH_TRADE_LOW_PRICE": "Low",
        "CH_CLOSING_PRICE": "Close",
        "CH_TOT_TRADED_QTY": "Volume",
        "VWAP": "VWAP",
    }
    df = df.rename(columns=rename_map)

    needed = ["Date", "Open", "High", "Low", "Close", "Volume", "VWAP"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        return None

    df = df[needed].copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ["Open", "High", "Low", "Close", "Volume", "VWAP"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Date", "Close", "VWAP"]).sort_values("Date")
    df = df.set_index("Date")
    return df if not df.empty else None


def compute_ema9(df: pd.DataFrame, span: int = 9) -> pd.Series:
    return df["Close"].ewm(span=span, adjust=False).mean()


def classify(price: float, vwap: float, ema9: float) -> str:
    if pd.isna(vwap) or pd.isna(ema9):
        return "No data"
    if price > vwap and price > ema9:
        return "Bullish"
    if price < vwap and price < ema9:
        return "Bearish"
    return "Mixed"
