"""
Streamlit app — Nifty 50: LIVE Price vs LIVE VWAP vs EMA9  (NSE data only)

Run locally:
    streamlit run app.py

Deploy free:
    Push this repo to GitHub, then deploy on https://share.streamlit.io
    (Streamlit Community Cloud) pointing at app.py.

Real-time notes
----------------
- Price + VWAP come from NSE's live quote endpoint (updates continuously
  during market hours: 09:15–15:30 IST). Outside market hours NSE returns
  the last traded session's closing values (there's no "live" data when the
  market is shut — that's true of any data source, not a limitation of this
  app).
- EMA9 is computed from today's intraday minute-by-minute price series, so
  it updates as the session progresses too.
- Use the "Auto-refresh" toggle in the sidebar to keep pulling fresh data
  automatically. Keep the interval sensible (>= 30s) — NSE rate-limits
  aggressively and will start returning errors if hit too fast.
"""

import time
import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from nse_data import (
    NSESession,
    get_nifty50_symbols,
    fetch_live_quote,
    fetch_intraday_series,
    classify,
)

st.set_page_config(
    page_title="Nifty 50 — LIVE VWAP vs EMA9",
    page_icon="📈",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────
#  CACHED RESOURCE — the HTTP session itself (not the data)
# ─────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_session() -> NSESession:
    return NSESession()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_symbol_list(_nse: NSESession) -> list:
    return get_nifty50_symbols(_nse)


def is_market_hours() -> bool:
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t


# ─────────────────────────────────────────────────────────────
#  SIDEBAR CONTROLS
# ─────────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Settings")
fetch_delay = st.sidebar.slider("Delay between NSE calls (sec)", 0.5, 3.0, 1.0, step=0.1)

auto_refresh = st.sidebar.checkbox("🔄 Auto-refresh", value=False)
refresh_secs = st.sidebar.slider("Refresh every (sec)", 30, 300, 60, step=10,
                                  disabled=not auto_refresh)

run_scan = st.sidebar.button("🔍 Run Live Scan Now", type="primary", use_container_width=True)

st.sidebar.markdown("---")
if is_market_hours():
    st.sidebar.success("🟢 NSE market hours (09:15–15:30 IST)")
else:
    st.sidebar.warning("🔴 Market closed — showing last available data")

st.sidebar.caption(
    "Data source: **nseindia.com live endpoints** — `quote-equity` for real-time "
    "LTP + live session VWAP, `chart-databyindex` for today's intraday series "
    "(used to compute a live-updating EMA9). No Yahoo Finance / yfinance, no "
    "daily/historical data.\n\n"
    "NSE rate-limits aggressively — keep the delay ≥ 1s and refresh interval "
    "≥ 30s to avoid errors."
)

st.title("📈 Nifty 50 — LIVE Price vs LIVE VWAP vs EMA9")
st.caption(
    "Bullish = LTP > live session VWAP **and** LTP > EMA9(today, 1-min) · "
    "Bearish = LTP < live session VWAP **and** LTP < EMA9(today, 1-min)"
)

if auto_refresh:
    st_autorefresh(interval=refresh_secs * 1000, key="live_refresh")

# ─────────────────────────────────────────────────────────────
#  SESSION STATE
# ─────────────────────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results = None
    st.session_state.series_cache = {}
    st.session_state.last_run = None

should_scan = run_scan or (auto_refresh and st.session_state.results is None)
# On auto-refresh reruns, scan again automatically without needing the button
if auto_refresh and st.session_state.last_run is not None:
    should_scan = True

# ─────────────────────────────────────────────────────────────
#  RUN LIVE SCAN  (no caching on the data itself — it must be fresh)
# ─────────────────────────────────────────────────────────────
if should_scan:
    nse = get_session()
    symbols = cached_symbol_list(nse)

    progress = st.progress(0.0, text="Starting live scan...")
    rows = []
    series_cache = {}

    for i, symbol in enumerate(symbols, 1):
        progress.progress(i / len(symbols), text=f"Fetching live data: {symbol} ({i}/{len(symbols)})")

        quote = fetch_live_quote(nse, symbol)
        series = fetch_intraday_series(nse, symbol)

        if quote is None:
            rows.append({"Symbol": symbol, "LTP": None, "Live VWAP": None,
                         "EMA9": None, "Chg %": None, "Signal": "No data",
                         "Updated": None})
        else:
            ema9 = None
            if series is not None and not series.empty:
                series_cache[symbol] = series
                ema9 = series["EMA9"].iloc[-1]

            price, vwap = quote["lastPrice"], quote["vwap"]
            rows.append({
                "Symbol": symbol,
                "LTP": round(price, 2),
                "Live VWAP": round(vwap, 2),
                "EMA9": round(ema9, 2) if ema9 is not None else None,
                "Chg %": quote.get("pChange"),
                "Signal": classify(price, vwap, ema9) if ema9 is not None else "Mixed",
                "Updated": quote.get("lastUpdateTime"),
            })

        time.sleep(fetch_delay)

    progress.empty()
    st.session_state.results = pd.DataFrame(rows)
    st.session_state.series_cache = series_cache
    st.session_state.last_run = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ─────────────────────────────────────────────────────────────
#  DISPLAY RESULTS
# ─────────────────────────────────────────────────────────────
def plot_stock(symbol: str, series: pd.DataFrame, live_vwap: float, ltp: float):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=series.index, y=series["Close"], name="Price (1-min)",
                              line=dict(color="black", width=2)))
    fig.add_trace(go.Scatter(x=series.index, y=series["EMA9"], name="EMA9",
                              line=dict(color="purple", width=1.5, dash="dot")))
    if pd.notna(live_vwap):
        fig.add_hline(y=live_vwap, line=dict(color="orange", width=1.5, dash="dash"),
                       annotation_text=f"Live VWAP {live_vwap:.2f}", annotation_position="top left")
    if pd.notna(ltp):
        fig.add_hline(y=ltp, line=dict(color="steelblue", width=1, dash="dot"),
                       annotation_text=f"LTP {ltp:.2f}", annotation_position="bottom left")
    fig.update_layout(
        title=f"{symbol} — Live Price vs VWAP vs EMA9 (today)",
        height=380,
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        template="plotly_white",
    )
    st.plotly_chart(fig, use_container_width=True)


def style_results(df: pd.DataFrame):
    """Apply row highlighting on the Signal column.

    pandas removed Styler.applymap() in 2.2+ (and it's gone entirely in
    pandas 3.x) in favor of Styler.map(). We try the new API first and
    fall back to the old one so this keeps working regardless of which
    pandas version is installed (e.g. on Streamlit Cloud vs. locally).
    """
    def highlight_signal(val):
        colors = {"Bullish": "background-color:#d4f7dc",
                  "Bearish": "background-color:#fbdada",
                  "Mixed": "background-color:#f0f0f0",
                  "No data": "background-color:#fff3cd"}
        return colors.get(val, "")

    styler = df.style
    if hasattr(styler, "map"):
        return styler.map(highlight_signal, subset=["Signal"])
    return styler.applymap(highlight_signal, subset=["Signal"])


if st.session_state.results is None:
    st.info("👈 Click **Run Live Scan Now** (or enable Auto-refresh) in the sidebar to pull live NSE data.")
else:
    results = st.session_state.results
    series_cache = st.session_state.series_cache
    st.caption(f"Last scan: {st.session_state.last_run}"
               + (f" · auto-refreshing every {refresh_secs}s" if auto_refresh else ""))

    bullish = results[results["Signal"] == "Bullish"]["Symbol"].tolist()
    bearish = results[results["Signal"] == "Bearish"]["Symbol"].tolist()
    mixed = results[results["Signal"] == "Mixed"]["Symbol"].tolist()
    nodata = results[results["Signal"] == "No data"]["Symbol"].tolist()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Bullish", len(bullish))
    c2.metric("🔴 Bearish", len(bearish))
    c3.metric("⚪ Mixed", len(mixed))
    c4.metric("⚠️ No data", len(nodata))

    if len(nodata) == len(results):
        st.error(
            "All 50 symbols came back with **No data**. This isn't the styling bug — "
            "it means `fetch_live_quote` / `fetch_intraday_series` in `nse_data.py` "
            "returned `None` for every symbol. Common causes: NSE blocked/rate-limited "
            "this server's IP (very common on Streamlit Community Cloud, since NSE's "
            "anti-bot checks often block cloud/datacenter IP ranges), the session's "
            "cookies weren't set up correctly (NSE requires visiting the homepage first "
            "to get cookies before hitting the API endpoints), or NSE changed its "
            "endpoint/response shape. Check the 'Manage app' logs for the actual "
            "exception being swallowed inside `fetch_live_quote`/`fetch_intraday_series`."
        )

    tab_summary, tab_bull, tab_bear, tab_all = st.tabs(
        ["📋 Summary Table", "🟢 Bullish Charts", "🔴 Bearish Charts", "📊 All Charts"]
    )

    with tab_summary:
        st.dataframe(
            style_results(results),
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "⬇️ Download results as CSV",
            data=results.to_csv(index=False).encode("utf-8"),
            file_name=f"nifty50_live_vwap_ema9_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
        )

    def _row(symbol):
        r = results[results["Symbol"] == symbol].iloc[0]
        return r["Live VWAP"], r["LTP"]

    with tab_bull:
        if not bullish:
            st.write("No bullish stocks found in this scan.")
        for symbol in bullish:
            if symbol in series_cache:
                vwap, ltp = _row(symbol)
                plot_stock(symbol, series_cache[symbol], vwap, ltp)

    with tab_bear:
        if not bearish:
            st.write("No bearish stocks found in this scan.")
        for symbol in bearish:
            if symbol in series_cache:
                vwap, ltp = _row(symbol)
                plot_stock(symbol, series_cache[symbol], vwap, ltp)

    with tab_all:
        available = sorted(series_cache.keys())
        if available:
            pick = st.selectbox("Choose a stock", available)
            if pick:
                vwap, ltp = _row(pick)
                plot_stock(pick, series_cache[pick], vwap, ltp)
        else:
            st.write("No intraday series available yet.")
