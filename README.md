# 📈 Nifty 50 — LIVE Price vs LIVE VWAP vs EMA9 Screener (NSE data only)

A Streamlit app that screens all **Nifty 50** stocks in real time and flags:

- 🟢 **Bullish** — Live price (LTP) is above **both** the live session VWAP and EMA9
- 🔴 **Bearish** — Live price (LTP) is below **both** the live session VWAP and EMA9

All data comes straight from **nseindia.com**'s live endpoints.
**No yfinance / Yahoo Finance, and no stale daily/historical data — everything
updates during market hours.**

## How the "live" data works

| Metric | Source | Behaviour |
|---|---|---|
| **LTP (price)** | `/api/quote-equity` → `priceInfo.lastPrice` | Updates continuously during market hours |
| **VWAP** | `/api/quote-equity` → `priceInfo.vwap` | NSE's own live session VWAP (cumulative turnover ÷ cumulative volume so far today) — a genuine real-time number, not a fixed daily figure |
| **EMA9** | `/api/chart-databyindex` (today's intraday price series, resampled to 1-min bars) → 9-period EMA | Recomputed from today's session every time you scan, so it moves as the day progresses |

Turn on **Auto-refresh** in the sidebar to keep pulling fresh data on an
interval (default 60s). NSE rate-limits aggressively, so keep the refresh
interval ≥ 30s and the per-call delay ≥ 1s.

Outside market hours (before 09:15 or after 15:30 IST, or on weekends), NSE
simply has no new data to serve — the app will show the last values from the
most recent session, same as NSE's own website would.

---

## 🖥️ Run locally

```bash
git clone https://github.com/<your-username>/nifty50-vwap-ema9-screener.git
cd nifty50-vwap-ema9-screener
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (usually `http://localhost:8501`).

Click **"Run Nifty 50 Scan"** in the sidebar — it fetches ~60 days of daily
history per stock directly from NSE (rate-limited to be polite, so a full
scan takes a couple of minutes).

---

## ☁️ Deploy for free on Streamlit Community Cloud

1. Push this repo to your own GitHub account (steps below).
2. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with GitHub.
3. Click **"New app"**, pick this repo, branch `main`, and set the main file to `app.py`.
4. Click **Deploy**. You'll get a public URL like `https://<app-name>.streamlit.app`.

That's it — no server management needed.

---

## 📤 Push this project to GitHub

```bash
cd nifty50-vwap-ema9-screener
git init
git add .
git commit -m "Initial commit: Nifty 50 VWAP/EMA9 screener (NSE data, Streamlit app)"
git branch -M main
git remote add origin https://github.com/<your-username>/nifty50-vwap-ema9-screener.git
git push -u origin main
```

(Create the empty repo on GitHub first via **github.com/new**, without a
README/license so it doesn't conflict with this one.)

---

## 📁 Project structure

```
nifty50-vwap-ema9-screener/
├── app.py              # Streamlit UI
├── nse_data.py          # NSE session + data-fetching logic (shared, reusable)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## ⚠️ Disclaimer

This tool is for educational / informational purposes only and is **not**
financial advice. NSE's public endpoints are undocumented and may change or
rate-limit without notice — this app includes retry/backoff logic but you
should not rely on it for time-critical trading decisions.
