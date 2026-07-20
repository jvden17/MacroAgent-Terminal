"""
MacroAgent -- Main App (NiceGUI)
================================
This is now your app's ENTRYPOINT. The live chart is one tab; the other tabs
are placeholders for the sections we'll port over from your Streamlit app
(app1.py) one at a time.

Run it (same environment as before):
    python macroagent_app.py
Then open http://localhost:8080

Structure, so you know where your future UI goes:
    main_page()        -> the app shell: header + tabs + panels
    charts_section()   -> everything for the CHARTS tab (self-contained)
    stub()             -> the "coming next" placeholders

To add a new section later: add one ui.tab(...) in the header and one matching
ui.tab_panel(...) below. That's it. When the file gets big, we split these into
separate module files -- that's the point where a tool like Claude Code starts
to earn its keep, but we're nowhere near that yet.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import pathlib
import re
import threading
import time
import uuid

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from nicegui import run, ui

load_dotenv()  # pull FRED_API_KEY / GOOGLE_API_KEY from .env for the Strategy engine


# ----------------------------------------------------------------------
# METRICS  (lightweight local event log -- swap for a Supabase table later)
# ----------------------------------------------------------------------
METRICS_FILE = pathlib.Path(__file__).with_name("metrics.jsonl")
_metrics_lock = threading.Lock()


def log_event(name: str, props: dict | None = None, session: str | None = None):
    """Append one analytics event as a JSON line to metrics.jsonl.

    Best-effort: logging must never raise into the UI. This is deliberately just a
    local JSONL file (grep-able, or `pd.read_json(..., lines=True)`); when Supabase
    lands, this one function becomes the insert into an `events` table."""
    event = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "event": name,
        "session": session,
        "props": props or {},
    }
    try:
        with _metrics_lock, open(METRICS_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except Exception as exc:            # noqa: BLE001 -- metrics are never critical
        print(f"[metrics] log failed: {exc}")


def feedback_row(event_name: str, session, label: str = "Was this helpful?", **extra):
    """A tiny 👍/👎 row that logs a `*_feedback` event. The single highest-signal
    metric for an AI feature: did the user find the output useful?"""
    with ui.row().classes("items-center gap-2"):
        cap = ui.label(label).style(
            f"color:{MUTED};font-family:{MONO};font-size:13px")

        def _vote(rating: str):
            log_event(event_name, {"rating": rating, **extra}, session=session)
            cap.text = "✓ Thanks for the feedback."
            up.set_visibility(False)
            down.set_visibility(False)

        up = ui.button("👍", on_click=lambda: _vote("up")).props("flat dense round")
        down = ui.button("👎", on_click=lambda: _vote("down")).props("flat dense round")

# ----------------------------------------------------------------------
# BRAND / THEME  (your existing MacroAgent neon-terminal look)
# ----------------------------------------------------------------------
NEON = "#00ffa3"     # signature accent
UP = "#00ffa3"       # bullish candle
DOWN = "#ff3b6b"     # bearish candle
BG = "#0a0a0a"       # page background
PANEL = "#111111"    # chart panel
GRID = "#222222"     # gridlines / borders
MUTED = "#888888"    # captions / axis labels
MONO = "Courier New, monospace"

POLL_SECONDS = 15    # how often LIVE mode refetches

# yfinance suffixes cover every asset class -> your multi-asset roadmap,
# demonstrated with one key-free data source.
ASSET_PRESETS = {
    "Equities": ["SPY", "AAPL", "NVDA"],
    "Crypto":   ["BTC-USD", "ETH-USD", "SOL-USD"],
    "Forex":    ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    "Futures":  ["ES=F", "NQ=F", "CL=F", "GC=F"],
}

INTERVAL_PERIOD = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "1mo", "1d": "1y"}

# App-wide styling (dark shell + neon tabs to match the brand)
GLOBAL_CSS = f"""
<style>
    body {{ background: {BG}; font-size: 16px; }}
    .ma-title {{
        font-family: {MONO}; color: #fff; text-shadow: 0 0 20px {NEON};
        letter-spacing: 4px; text-transform: uppercase; font-weight: 900;
    }}
    .q-header {{ background: {BG} !important; border-bottom: 1px solid {GRID}; }}
    .q-tab {{ color: {MUTED} !important; font-family: {MONO}; letter-spacing: 2px;
             font-size: 15px; }}
    .q-tab--active {{ color: {NEON} !important; }}
    .q-tab__indicator {{ background: {NEON} !important; }}
    .q-btn {{ color: {NEON} !important; font-family: {MONO} !important;
             letter-spacing: 1px; font-size: 15px !important; padding: 10px 18px; }}
    .q-tab-panels, .q-tab-panel {{ background: transparent !important; }}

    /* Readability: scale up form controls, options and helper text a touch */
    .q-field, .q-field__native, .q-field__input,
    .q-field__prefix, .q-field__suffix {{ font-size: 16px !important; }}
    .q-field__label {{ font-size: 15px !important; }}
    .q-checkbox__label {{ font-size: 16px !important; }}
    .q-menu .q-item, .q-item__label {{ font-size: 15px !important; }}
    .q-markdown, .q-markdown p, .q-markdown li {{ font-size: 16px; line-height: 1.75; }}
</style>
"""


# ----------------------------------------------------------------------
# DATA LAYER
# ----------------------------------------------------------------------
def fetch_ohlcv(symbol: str, interval: str):
    """Return (categories, candles, volumes) shaped for ECharts.

    Candles are [open, close, low, high] -- ECharts' required candlestick order
    (NOT open/high/low/close, a common gotcha).
    """
    period = INTERVAL_PERIOD.get(interval, "1mo")
    df = yf.download(
        tickers=symbol, interval=interval, period=period,
        auto_adjust=False, progress=False,
    )
    if df is None or df.empty:
        return [], [], []

    if getattr(df.columns, "nlevels", 1) > 1:  # flatten single-ticker multiindex
        df.columns = df.columns.get_level_values(0)

    intraday = interval.endswith("m") or interval.endswith("h")
    fmt = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"

    categories, candles, volumes = [], [], []
    for ts, row in df.iterrows():
        if pd.isna(row.get("Close")):
            continue
        o, h = float(row["Open"]), float(row["High"])
        low, c = float(row["Low"]), float(row["Close"])
        vol = 0.0 if pd.isna(row.get("Volume")) else float(row["Volume"])

        categories.append(ts.strftime(fmt))
        candles.append([round(o, 4), round(c, 4), round(low, 4), round(h, 4)])
        volumes.append({"value": round(vol, 2),
                        "itemStyle": {"color": UP if c >= o else DOWN}})
    return categories, candles, volumes


# ----------------------------------------------------------------------
# CHART BUILDER
# ----------------------------------------------------------------------
def build_chart_options(symbol: str, interval: str, categories, candles, volumes):
    return {
        "backgroundColor": PANEL,
        "animation": False,
        "textStyle": {"color": MUTED, "fontFamily": MONO},
        "title": {
            "text": f"{symbol}   \u00b7   {interval}", "left": "center",
            "textStyle": {"color": "#ffffff", "fontFamily": MONO,
                          "fontSize": 16, "letterSpacing": 2},
        },
        "tooltip": {
            "trigger": "axis", "axisPointer": {"type": "cross"},
            "backgroundColor": "#000000", "borderColor": NEON,
            "textStyle": {"color": "#f0f0f0", "fontFamily": MONO},
        },
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": [
            {"left": 62, "right": 24, "top": 52, "height": "56%"},
            {"left": 62, "right": 24, "top": "72%", "height": "16%"},
        ],
        "xAxis": [
            {"type": "category", "data": categories, "gridIndex": 0,
             "boundaryGap": True, "axisLine": {"lineStyle": {"color": GRID}},
             "axisLabel": {"color": MUTED}, "splitLine": {"show": False}},
            {"type": "category", "data": categories, "gridIndex": 1,
             "boundaryGap": True, "axisLine": {"lineStyle": {"color": GRID}},
             "axisLabel": {"show": False}, "splitLine": {"show": False}},
        ],
        "yAxis": [
            {"scale": True, "gridIndex": 0,
             "splitLine": {"lineStyle": {"color": GRID}},
             "axisLine": {"lineStyle": {"color": GRID}},
             "axisLabel": {"color": MUTED}},
            {"scale": True, "gridIndex": 1, "splitNumber": 2,
             "axisLabel": {"show": False}, "axisLine": {"show": False},
             "splitLine": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 45, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 45, "end": 100,
             "bottom": 8, "height": 18, "borderColor": GRID,
             "fillerColor": "rgba(0,255,163,0.12)",
             "handleStyle": {"color": NEON},
             "textStyle": {"color": MUTED, "fontFamily": MONO}},
        ],
        "series": [
            {"type": "candlestick", "name": symbol, "data": candles,
             "xAxisIndex": 0, "yAxisIndex": 0,
             "itemStyle": {"color": UP, "color0": DOWN,
                           "borderColor": UP, "borderColor0": DOWN}},
            {"type": "bar", "name": "Volume", "data": volumes,
             "xAxisIndex": 1, "yAxisIndex": 1},
        ],
    }


# ----------------------------------------------------------------------
# SECTIONS
# ----------------------------------------------------------------------
TRENDING = ["SPY", "QQQ", "NVDA", "AAPL", "TSLA", "AMD", "BTC-USD", "ETH-USD"]


def fetch_trending():
    """Latest daily move for a curated set of tickers (one batched request)."""
    try:
        data = yf.download(" ".join(TRENDING), period="5d", interval="1d",
                           auto_adjust=False, progress=False, group_by="ticker")
    except Exception:
        return []
    out = []
    for s in TRENDING:
        try:
            closes = data[s]["Close"].dropna()
            if len(closes) >= 2:
                prev, last = float(closes.iloc[-2]), float(closes.iloc[-1])
                pct = (last - prev) / prev * 100 if prev else 0.0
                out.append((s, pct))
        except Exception:
            continue
    return out


def charts_section():
    """The CHARTS tab -- fully self-contained (controls + chart + live loop)."""
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-3"):
        # Trending tickers -- tap one to load it into the chart
        with ui.row().classes("gap-2 flex-wrap items-center"):
            ui.label("TRENDING").style(
                f"color:{MUTED};font-family:{MONO};font-size:11px")
            for _sym, _pct in fetch_trending():
                _arrow = "\u25B2" if _pct >= 0 else "\u25BC"
                ui.button(f"{_sym} {_arrow}{_pct:+.1f}%",
                          on_click=lambda s=_sym: set_symbol(s)).props("flat dense")

        # Asset-class presets (your multi-asset roadmap, one tap each)
        with ui.row().classes("gap-5 flex-wrap items-center"):
            for cls, syms in ASSET_PRESETS.items():
                with ui.row().classes("gap-1 items-center"):
                    ui.label(cls.upper()).style(
                        f"color:{MUTED};font-family:{MONO};font-size:11px")
                    for s in syms:
                        ui.button(s, on_click=lambda s=s: set_symbol(s)) \
                            .props("outline dense") \
                            .style(f"color:{NEON};border-color:{NEON};font-family:{MONO}")

        # Controls
        with ui.row().classes("items-end gap-3 flex-wrap"):
            symbol_in = ui.input("Symbol", value="BTC-USD") \
                .props("dark dense").style(f"color:{NEON}")
            interval_sel = ui.select(list(INTERVAL_PERIOD.keys()),
                                     value="5m", label="Interval").props("dark dense")
            live_switch = ui.switch("LIVE", value=True)
            ui.button("REFRESH", on_click=lambda: refresh()) \
                .props("outline").style(f"color:{NEON};border-color:{NEON}")

        ui.label(f"LIVE polls every {POLL_SECONDS}s \u00b7 crypto trades 24/7 \u00b7 "
                 f"stocks/forex/futures update during their market hours") \
            .style(f"color:{MUTED};font-family:{MONO};font-size:11px")

        chart = ui.echart(build_chart_options("BTC-USD", "5m", [], [], [])) \
            .classes("w-full").style("height:520px")
        status = ui.label("Loading\u2026").style(f"color:{MUTED};font-family:{MONO}")

    def set_symbol(s: str):
        symbol_in.value = s
        refresh()

    def refresh():
        symbol = (symbol_in.value or "").strip().upper()
        interval = interval_sel.value
        if not symbol:
            status.text = "Enter a symbol."
            return
        try:
            cats, candles, vols = fetch_ohlcv(symbol, interval)
            if not candles:
                status.text = f"No data for {symbol} \u2014 check the symbol/interval."
                return
            new_opts = build_chart_options(symbol, interval, cats, candles, vols)
            chart.options.clear()
            chart.options.update(new_opts)
            chart.update()
            status.text = (f"{symbol} \u00b7 last {candles[-1][1]} \u00b7 "
                           f"{len(candles)} bars \u00b7 {interval}")
        except Exception as e:
            status.text = f"Fetch error: {e}"

    def tick():
        if live_switch.value:
            refresh()

    ui.timer(POLL_SECONDS, tick)
    ui.timer(0.2, refresh, once=True)
    return chart


def stub(title: str, subtitle: str):
    """On-brand placeholder for a section still to be ported."""
    with ui.column().classes("w-full max-w-5xl mx-auto p-8 gap-3 items-center") \
            .style("min-height:420px; justify-content:center"):
        ui.label(title.upper()).classes("ma-title text-2xl")
        ui.label(subtitle).style(
            f"color:{MUTED};font-family:{MONO};text-align:center;max-width:520px")
        ui.label("// SECTION UNDER CONSTRUCTION").style(
            f"color:{NEON};font-family:{MONO};font-size:12px;letter-spacing:2px")


# ----------------------------------------------------------------------
# PROFILE  (ported from app1.py Steps 1 & 2 -- fields kept identical)
# ----------------------------------------------------------------------
LITERACY_OPTS = [
    "Beginner (Teach me the absolute basics)",
    "Novice (I know a few concepts, still learning)",
    "Intermediate (Familiar with markets, please keep jargon light)",
    "Advanced (I understand financial statements & market cycles)",
    "Expert (Give me raw data and institutional framing)",
]
HORIZON_OPTS = ["Short-Term (0-3 Years)", "Medium-Term (3-7 Years)", "Long-Term (7+ Years)"]
GOAL_OPTS = [
    "Income & Yield (Focus on Dividends/Bonds, ~3-5% yearly)",
    "Steady Wealth Accumulation (Market average, ~7-10% yearly)",
    "Aggressive Capital Appreciation (Targeting 12%+ yearly, willing to accept high volatility)",
]
STRATEGY_OPTS = [
    "Core-Satellite (70% safe index funds, 30% aggressive stock picking)",
    "Trend Following (Riding market momentum and sectors)",
    "Value Investing (Hunting for undervalued, beaten-down assets)",
    "Pure Passive (Set it and forget it in broad ETFs)",
]
RISK_LABELS = {1: "Capital Preservation", 2: "Highly Conservative", 3: "Conservative",
               4: "Moderate", 5: "Balanced", 6: "Growth-Oriented", 7: "Aggressive Growth",
               8: "Highly Aggressive", 9: "Maximum Alpha", 10: "Speculative"}
SECTOR_OPTS = ["Technology", "Semiconductors / AI", "Healthcare / Biotech", "Financials",
               "Energy", "Consumer Discretionary", "Consumer Staples", "Industrials",
               "Materials", "Utilities", "Real Estate", "Communication Services",
               "Crypto / Digital Assets", "Clean Energy"]


def profile_section(state: dict):
    """Investor profiling + risk calibration. Writes state['profile'], which the
    Strategy tab will consume once the memo engine is ported."""
    with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
        ui.label("Create Your Investor Profile").classes("ma-title text-3xl")
        ui.label("Welcome to MacroAgent. Tell us who you are as an investor — a few "
                 "quick questions — and we'll tailor every lesson, strategy, and macro "
                 "read to you. There are no wrong answers; you can change these anytime.") \
            .style(f"color:{NEON};font-family:{MONO};font-size:16px;line-height:1.6;"
                   "margin-bottom:8px;max-width:640px")

        literacy = ui.select(LITERACY_OPTS, value=LITERACY_OPTS[0],
                             label="Financial Literacy Level").props("dark").classes("w-full")
        budget = ui.number("Initial Investment Budget ($)", value=5000, min=100,
                           max=10_000_000, step=500, format="%.0f").props("dark").classes("w-full")
        horizon = ui.select(HORIZON_OPTS, value=HORIZON_OPTS[0],
                            label="Time Horizon (When do you need this money?)").props("dark").classes("w-full")
        goal = ui.select(GOAL_OPTS, value=GOAL_OPTS[0],
                        label="Realistic Growth Goal").props("dark").classes("w-full")
        strategy = ui.select(STRATEGY_OPTS, value=STRATEGY_OPTS[0],
                            label="Preferred Strategic Approach").props("dark").classes("w-full")
        options = ui.checkbox("Interested in learning Options Trading (Calls/Puts)")

        ui.label("LEARNING FOCUS").style(
            f"color:{MUTED};font-family:{MONO};font-size:12px;margin-top:6px")
        industries = ui.select(
            SECTOR_OPTS, multiple=True, value=[],
            label="Which industries / sectors do you want to learn about?"
        ).props("dark use-chips").classes("w-full")

        ui.label("RISK CALIBRATION").classes("ma-title text-lg").style("margin-top:6px")
        with ui.row().classes("w-full items-center gap-4"):
            risk = ui.slider(min=1, max=10, value=5, step=1).props("label-always").classes("grow")
            ui.label().bind_text_from(
                risk, "value",
                lambda v: f"{int(v)} \u2014 {RISK_LABELS.get(int(v), '')}"
            ).style(f"color:{NEON};font-family:{MONO};min-width:190px")

        ui.label("INSTITUTIONAL DOCUMENT UPLOAD (OPTIONAL)").style(
            f"color:{MUTED};font-family:{MONO};font-size:12px;margin-top:6px")
        ui.label("Upload institutional documents \u2014 research notes, market outlooks, "
                 "house views (PDF) \u2014 to ground your education and strategy.").style(
            f"color:{MUTED};font-family:{MONO};font-size:11px")

        def _on_upload(e):
            state["research"].append(e.name)
            # Extract text now so the Strategy engine can ground the memo (RAG)
            state.setdefault("research_text", {})[e.name] = _extract_pdf_text(e.content)
            upload_status.text = "Loaded: " + ", ".join(state["research"])

        ui.upload(on_upload=_on_upload, multiple=True, auto_upload=True) \
            .props("accept=.pdf flat bordered").classes("w-full")
        upload_status = ui.label("").style(f"color:{MUTED};font-family:{MONO};font-size:12px")

        ui.button("SAVE INVESTOR PROFILE", on_click=lambda: save()) \
            .props("outline").style(f"color:{NEON};border-color:{NEON};font-family:{MONO}")

        summary = ui.column().classes("w-full gap-1")

    def save():
        prof = {
            "literacy": literacy.value.split(" (")[0],
            "budget": float(budget.value or 0),
            "horizon": horizon.value.split(" (")[0],
            "goal": goal.value.split(" (")[0],
            "strategy": strategy.value.split(" (")[0],
            "risk_level": int(risk.value),
            "risk": RISK_LABELS.get(int(risk.value), ""),
            "options": bool(options.value),
            "industries": list(industries.value or []),
            "research_files": list(state["research"]),
        }
        state["profile"] = prof
        log_event("profile_saved", {
            "literacy": prof["literacy"], "risk_level": prof["risk_level"],
            "horizon": prof["horizon"], "goal": prof["goal"],
            "strategy": prof["strategy"], "options": prof["options"],
            "industries_count": len(prof["industries"]),
            "research_files": len(prof["research_files"]),
        }, session=state.get("session_id"))
        summary.clear()
        with summary:
            ui.separator().props("color=grey-9")
            ui.label("// PROFILE SAVED").style(
                f"color:{NEON};font-family:{MONO};font-size:12px;letter-spacing:2px")
            rows = [
                ("Literacy", prof["literacy"]),
                ("Budget", f"${prof['budget']:,.0f}"),
                ("Horizon", prof["horizon"]),
                ("Goal", prof["goal"]),
                ("Strategy", prof["strategy"]),
                ("Risk", f"{prof['risk_level']} \u2014 {prof['risk']}"),
                ("Options", "Yes" if prof["options"] else "No"),
                ("Learning Focus", ", ".join(prof["industries"]) or "(none selected)"),
                ("Research PDFs", f"{len(prof['research_files'])} uploaded"),
            ]
            for k, v in rows:
                with ui.row().classes("gap-2"):
                    ui.label(f"{k}:").style(f"color:{MUTED};font-family:{MONO};min-width:130px")
                    ui.label(str(v)).style(f"color:#eaeaea;font-family:{MONO}")
            focus = ", ".join(prof["industries"]) or "the broad market"
            ui.label(f"Education will center on {focus}, combined with live macro "
                     "intelligence (Strategy tab ports next).").style(
                f"color:{MUTED};font-family:{MONO};font-size:12px;margin-top:6px")


# ----------------------------------------------------------------------
# STRATEGY  (ported from app1.py's Gemini engine -- reads state["profile"])
# ----------------------------------------------------------------------
GEMINI_MODEL = "gemini-3.1-pro-preview"   # same model v1 used

# FRED series that make up the macro pulse: (series_id, display_name)
FRED_SERIES = [
    ("GDPC1", "Real GDP"),
    ("UNRATE", "Unemployment"),
    ("CPIAUCSL", "CPI"),
]


class _MockResponse:
    """Stand-in for a Gemini response so the UI still renders if the API is down
    (ported from v1's Offline Dev Mode)."""

    def __init__(self, text: str):
        self.text = text


def _extract_pdf_text(source, max_pages: int = 3) -> str:
    """Pull text from the first few pages of an uploaded PDF (RAG ingestion).

    `source` is a file-like object (NiceGUI upload .content). Failures are
    swallowed -- a bad PDF should never break profiling."""
    try:
        from pypdf import PdfReader
        data = source.read()
        reader = PdfReader(io.BytesIO(data))
        text = ""
        for i in range(min(len(reader.pages), max_pages)):
            text += reader.pages[i].extract_text() or ""
        return text
    except Exception as exc:            # noqa: BLE001 -- RAG is best-effort
        print(f"[strategy] PDF extract failed: {exc}")
        return ""


def _clean_fred(raw, value_name: str) -> pd.DataFrame:
    """Normalize a fedfred observation frame to two columns: Date + value_name.

    fedfred returns a DatetimeIndex frame with a 'value' column; this mirrors
    v1's to_polars_clean but stays in pandas (v2 has no polars dep)."""
    df = pd.DataFrame(raw)
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    cols = [str(c) for c in df.columns]
    df.columns = cols
    date_col = next((c for c in cols if "date" in c.lower() or "index" in c.lower()), cols[0])
    val_col = next((c for c in cols if "value" in c.lower()), cols[-1])
    out = df[[date_col, val_col]].copy()
    out.columns = ["Date", value_name]
    out["Date"] = out["Date"].astype(str)
    out[value_name] = pd.to_numeric(out[value_name], errors="coerce")
    return out.dropna(subset=[value_name]).reset_index(drop=True)


def build_macro_chart_options(df: pd.DataFrame, title: str):
    """A compact neon line chart for a single FRED series (last ~20 prints)."""
    tail = df.tail(20)
    return {
        "backgroundColor": PANEL,
        "animation": False,
        "textStyle": {"color": MUTED, "fontFamily": MONO},
        "title": {"text": title, "left": "center",
                  "textStyle": {"color": "#ffffff", "fontFamily": MONO,
                                "fontSize": 13, "letterSpacing": 1}},
        "tooltip": {"trigger": "axis", "backgroundColor": "#000000",
                    "borderColor": NEON,
                    "textStyle": {"color": "#f0f0f0", "fontFamily": MONO}},
        "grid": {"left": 58, "right": 16, "top": 40, "bottom": 40},
        "xAxis": {"type": "category", "data": tail["Date"].tolist(),
                  "axisLine": {"lineStyle": {"color": GRID}},
                  "axisLabel": {"color": MUTED, "fontSize": 9},
                  "splitLine": {"show": False}},
        "yAxis": {"type": "value", "scale": True,
                  "axisLine": {"lineStyle": {"color": GRID}},
                  "axisLabel": {"color": MUTED},
                  "splitLine": {"lineStyle": {"color": GRID}}},
        "series": [{
            "type": "line", "smooth": True, "showSymbol": False,
            "data": tail[df.columns[1]].tolist(),
            "lineStyle": {"color": NEON, "width": 2},
            "areaStyle": {"color": "rgba(0,255,163,0.10)"},
        }],
    }


def call_ai_with_retry(client, prompt_text: str):
    """v1's retry + Offline Dev Mode, minus Streamlit. Returns an object with
    a .text attribute either way so callers never crash."""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_text,
                config={"temperature": 0.1},
            )
        except Exception as exc:        # noqa: BLE001
            error_msg = str(exc).lower()
            transient = any(k in error_msg for k in ("429", "503", "quota"))
            if transient and attempt < max_retries - 1:
                time.sleep(1)
                continue
            if transient:
                return _MockResponse(
                    "### OFFLINE MODE: MOCK STRATEGY MEMO\n\n"
                    "The Google API is rate-limited right now, so this placeholder "
                    "keeps the terminal usable. Your saved profile, live macro charts, "
                    "and the chat debrief all still work -- re-run when the quota "
                    "resets for the full personalized memo.\n\n"
                    "### Illustrative Allocation (educational only)\n"
                    "- Core index exposure for the bulk of the book\n"
                    "- A satellite sleeve tilted toward your selected industries\n"
                    "- Cash/short-duration buffer sized to your risk level\n")
            return _MockResponse(f"Unexpected Error: {exc}")


def _gather_rag(state: dict) -> str:
    """RAG context = only THIS user's own uploaded PDFs (text extracted at upload
    time in the PROFILE tab). Scoped per-client for tenant isolation; Gemini's own
    macro knowledge covers the general baseline. This is the seam that the roadmap's
    pgvector semantic search will later replace."""
    return "".join((state.get("research_text") or {}).values())


def _build_memo_prompt(profile: dict, pulse_data: str, market_pulse: str,
                       rag_text: str) -> str:
    """The v1 IPS prompt, re-centered on the user's selected industries and kept
    explicitly educational (this product is not financial advice)."""
    industries = ", ".join(profile.get("industries") or []) or "the broad market"
    return f"""
You are MacroAgent, an AI markets *educator*. Produce a personalized, educational
Investment Policy Statement (IPS) that teaches a retail learner how professionals
would think about their situation. This is educational only -- it is NOT financial
advice, and no real money is involved (paper/simulation).

CLIENT PROFILE:
- Literacy Level: {profile.get('literacy')}
- Risk Tolerance: {profile.get('risk_level')} -- {profile.get('risk')}
- Budget: ${profile.get('budget', 0):,.2f}
- Time Horizon: {profile.get('horizon')}
- Primary Goal: {profile.get('goal')}
- Preferred Strategy: {profile.get('strategy')}
- Options Interest: {"Yes" if profile.get('options') else "No"}
- LEARNING FOCUS (industries the learner chose): {industries}

CURRENT MACRO DATA (Raw Fed telemetry -- extract and explain the trends):
{pulse_data}

LIVE MARKET DATA:
{market_pulse}

INSTITUTIONAL RESEARCH (uploaded PDF context, may be empty):
{rag_text[:4000]}

OUTPUT STRUCTURE (use these exact ### headers, in this order):
- ### Macro-Economic Synthesis: Explain the 'why' behind the Fed data and the
  current environment, connecting it to the live market reading.
- ### Industry Deep-Dive: {industries}: Teach the learner how the macro backdrop
  above specifically affects EACH of their chosen industries. Name representative
  ETF *types* or company profiles for each; explain the drivers, not just tickers.
- ### Strategic Asset Allocation: A precise % allocation that expresses the chosen
  industries as a satellite tilt while respecting their {profile.get('risk')} risk
  level and {profile.get('horizon')} horizon.
- ### Tactical Execution: Walk through how a learner would build this over time.
  If Options Interest is "Yes", add an accessible primer on one hedging strategy.

TONE & FORMATTING:
- Educational, specific, and encouraging. Introduce yourself as MacroAgent.
- Tie every recommendation back to the learner's selected industries and risk level.
- Do NOT use numbered lists for the main headers. Do NOT use backticks or code blocks.
- End with a one-line reminder that this is educational, not financial advice.
"""


def _build_memo(profile: dict, state: dict) -> dict:
    """BLOCKING pipeline (runs in a worker thread via run.io_bound): pull FRED,
    grab a live SPY read, gather RAG, and call Gemini. Returns everything the UI
    needs to render, or an {'error': ...} dict."""
    import fedfred as fd
    from google import genai

    fred_key = os.getenv("FRED_API_KEY")
    google_key = os.getenv("GOOGLE_API_KEY")
    if not fred_key or not google_key:
        return {"error": "Missing FRED_API_KEY or GOOGLE_API_KEY in .env -- "
                         "add them and restart the app."}

    try:
        fred = fd.FredAPI(api_key=fred_key)
        series = {name: _clean_fred(fred.get_series_observations(sid), name)
                  for sid, name in FRED_SERIES}
    except Exception as exc:            # noqa: BLE001
        return {"error": f"FRED pull failed: {exc}"}

    pulse_data = "\n".join(
        f"{name} (last 3 prints): {df.tail(3).to_dict('records')}"
        for name, df in series.items()
    )

    # Live market pulse via yfinance (key-free) -- reuses the CHARTS data layer,
    # and sidesteps v1's ALPHA_VANTAGE_API_KEY / AV_API_KEY env-name mismatch.
    try:
        _, spy_candles, _ = fetch_ohlcv("SPY", "1d")
        market_pulse = (f"S&P 500 ETF (SPY) latest close: ${spy_candles[-1][1]:,.2f}"
                        if spy_candles else "SPY live data temporarily unavailable.")
    except Exception:                   # noqa: BLE001
        market_pulse = "SPY live data temporarily unavailable."

    rag_text = _gather_rag(state)
    prompt = _build_memo_prompt(profile, pulse_data, market_pulse, rag_text)

    try:
        client = genai.Client(api_key=google_key)
        memo = call_ai_with_retry(client, prompt)
    except Exception as exc:            # noqa: BLE001
        return {"error": f"Gemini call failed: {exc}"}

    return {"memo": memo.text, "series": series,
            "market_pulse": market_pulse, "rag_len": len(rag_text),
            # degraded = we served the offline/mock text (quota) or an error stub,
            # so metrics can track how often Gemini is actually reachable.
            "degraded": isinstance(memo, _MockResponse)}


def _parse_memo(memo_text: str):
    """Split the memo on '### ' headers into (intro, [(title, body), ...])."""
    parts = re.split(r"(?m)^###\s+", memo_text)
    intro = parts[0].strip() if parts else ""
    sections = []
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        sections.append((head.strip().replace("**", ""), body.strip()))
    return intro, sections


def strategy_section(state: dict):
    """The STRATEGY tab: reads state['profile'], pulls live macro data + RAG, and
    renders a personalized educational memo, macro charts, and a Q&A debrief."""
    with ui.column().classes("w-full max-w-4xl mx-auto p-4 gap-3"):
        ui.label("STRATEGY & EDUCATION").classes("ma-title text-xl")
        ui.label("A personalized, industry-focused education memo built from your "
                 "saved profile + live Fed macro data + any uploaded research. "
                 "Educational only — not financial advice.").style(
            f"color:{MUTED};font-family:{MONO};font-size:12px;max-width:640px")

        with ui.row().classes("items-center gap-3"):
            gen_btn = ui.button("INITIALIZE STRATEGY ENGINE",
                                on_click=lambda: generate()) \
                .props("outline").style(f"color:{NEON};border-color:{NEON};font-family:{MONO}")
            spinner = ui.spinner(size="lg", color="primary")
            spinner.set_visibility(False)
            status = ui.label("").style(f"color:{MUTED};font-family:{MONO};font-size:12px")

        results = ui.column().classes("w-full gap-4")

    async def generate():
        profile = state.get("profile")
        if not profile:
            status.text = "Save your investor profile first (PROFILE tab)."
            ui.notify("No profile found — fill out & save the PROFILE tab first.",
                      type="warning")
            return
        gen_btn.disable()
        spinner.set_visibility(True)
        status.text = "QUANT ENGINE ACTIVE: harvesting live Fed, market & research flows…"
        try:
            data = await run.io_bound(_build_memo, profile, state)
        finally:
            spinner.set_visibility(False)
            gen_btn.enable()

        if "error" in data:
            status.text = data["error"]
            ui.notify(data["error"], type="negative")
            log_event("strategy_error", {"error": data["error"][:120]},
                      session=state.get("session_id"))
            return

        state["memo"] = data["memo"]
        state["chat_history"] = []
        status.text = (f"Memo generated · {data['market_pulse']} · "
                       f"{data['rag_len']} chars of research ingested")
        log_event("strategy_generated", {
            "degraded": data.get("degraded", False),
            "rag_len": data["rag_len"],
            "modules": len(_parse_memo(data["memo"])[1]),
            "industries_count": len(profile.get("industries") or []),
            "risk_level": profile.get("risk_level"),
        }, session=state.get("session_id"))
        _render_results(data)

    def _render_results(data: dict):
        results.clear()
        with results:
            # Live macro context that fed the memo
            ui.label("// LIVE MACRO CONTEXT").style(
                f"color:{NEON};font-family:{MONO};font-size:12px;letter-spacing:2px")
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for name, df in data["series"].items():
                    ui.echart(build_macro_chart_options(df, f"{name.upper()} T-20")) \
                        .classes("grow").style("height:220px;min-width:280px")

            # The parsed memo, shown one "module" at a time so the learner can go
            # at their own pace (PREVIOUS / NEXT) instead of a wall of text.
            intro, sections = _parse_memo(data["memo"])
            if intro:
                ui.markdown(intro).classes("w-full").style(
                    f"color:#d8d8d8;font-family:{MONO};font-style:italic;"
                    f"border-left:4px solid {NEON};padding:10px 16px;background:#080808")

            if sections:
                idx = {"i": 0}
                module_box = ui.column().classes("w-full")
                with ui.row().classes("w-full items-center justify-between"):
                    prev_btn = ui.button("← PREVIOUS", on_click=lambda: _step(-1)) \
                        .props("flat").style(f"color:{NEON};font-family:{MONO}")
                    progress = ui.label().style(
                        f"color:{MUTED};font-family:{MONO};font-size:12px;letter-spacing:2px")
                    next_btn = ui.button("NEXT MODULE →", on_click=lambda: _step(1)) \
                        .props("outline").style(f"color:{NEON};border-color:{NEON};font-family:{MONO}")

                def _show_module():
                    i = idx["i"]
                    title, body = sections[i]
                    module_box.clear()
                    with module_box:
                        with ui.column().classes("w-full gap-1").style(
                                f"border-left:6px solid {NEON};background:#080808;padding:20px 24px"):
                            ui.label(f"MODULE {i + 1:02d} · {title.upper()}").style(
                                f"color:{NEON};font-family:{MONO};font-weight:700;"
                                f"letter-spacing:1px;text-transform:uppercase")
                            ui.markdown(body).classes("w-full").style(
                                "color:#f0f0f0;line-height:1.7")
                        if i == len(sections) - 1:
                            ui.label("✓ STRATEGY REVIEW COMPLETE").style(
                                f"color:{NEON};font-family:{MONO};font-size:12px;"
                                f"letter-spacing:2px;margin-top:6px")
                    progress.text = f"MODULE {i + 1:02d} / {len(sections):02d}"
                    prev_btn.set_enabled(i > 0)
                    next_btn.set_enabled(i < len(sections) - 1)

                def _step(delta: int):
                    idx["i"] = max(0, min(len(sections) - 1, idx["i"] + delta))
                    _show_module()

                _show_module()
            else:
                # Memo had no ### headers -- just show it whole rather than paginate.
                ui.markdown(data["memo"]).classes("w-full").style(
                    f"color:#f0f0f0;line-height:1.7;border-left:6px solid {NEON};"
                    f"background:#080808;padding:20px 24px")

            def _download():
                ui.download(data["memo"].encode("utf-8"), "MacroAgent_IPS.md")

            ui.button("DOWNLOAD STRATEGY DOCUMENT", on_click=_download) \
                .props("outline").style(f"color:{NEON};border-color:{NEON};font-family:{MONO}")

            # Was the generated memo useful? (highest-signal quality metric)
            feedback_row("memo_feedback", state.get("session_id"),
                         label="Was this strategy memo helpful?",
                         degraded=data.get("degraded", False))

            # Interactive strategy debrief (Q&A), grounded in the memo just written
            ui.separator().props("color=grey-9")
            ui.label("STRATEGIC DEBRIEF (Q&A)").classes("ma-title text-lg")
            ui.label("Interrogate MacroAgent on the logic behind this education.").style(
                f"color:{MUTED};font-family:{MONO};font-size:12px")
            chat_log = ui.column().classes("w-full gap-2")
            with ui.row().classes("w-full items-center gap-2"):
                question = ui.input(placeholder="Ask MacroAgent about the strategy…") \
                    .props("dark dense").classes("grow").style(f"color:{NEON}")
                ui.button("SEND", on_click=lambda: ask()) \
                    .props("outline").style(f"color:{NEON};border-color:{NEON}")

            def _bubble(role: str, text: str):
                who = "YOU" if role == "user" else "MACROAGENT"
                accent = MUTED if role == "user" else NEON
                with chat_log:
                    with ui.column().classes("w-full gap-0").style(
                            f"border-left:3px solid {accent};padding:6px 14px;background:#080808"):
                        ui.label(who).style(
                            f"color:{accent};font-family:{MONO};font-size:11px;letter-spacing:1px")
                        ui.markdown(text).classes("w-full").style("color:#eaeaea")
                        if role == "assistant":
                            feedback_row("chat_feedback", state.get("session_id"),
                                         label="Helpful?")

            async def ask():
                q = (question.value or "").strip()
                if not q:
                    return
                question.value = ""
                log_event("chat_question", {"len": len(q)},
                          session=state.get("session_id"))
                _bubble("user", q)
                state.setdefault("chat_history", []).append({"role": "user", "content": q})
                answer = await run.io_bound(_answer_question, state["memo"], q)
                _bubble("assistant", answer)
                state["chat_history"].append({"role": "assistant", "content": answer})


def _answer_question(memo_text: str, question: str) -> str:
    """Blocking Gemini follow-up for the debrief chat, grounded in the memo."""
    from google import genai
    google_key = os.getenv("GOOGLE_API_KEY")
    if not google_key:
        return "GOOGLE_API_KEY missing from .env — chat is offline."
    prompt = f"""
You are MacroAgent, an educational markets assistant. You just wrote this IPS for a
learner (educational only, not financial advice):
---
{memo_text}
---
Answer their follow-up concisely, in character, and stay educational.
Learner question: {question}
"""
    try:
        client = genai.Client(api_key=google_key)
        return call_ai_with_retry(client, prompt).text
    except Exception as exc:            # noqa: BLE001
        return f"COMMS ERROR: {exc}"


# ----------------------------------------------------------------------
# EXECUTION  (ported from app1.py Step 4 -- Alpaca PAPER trading ONLY)
# ----------------------------------------------------------------------
def _alpaca_client():
    """A paper-only Alpaca trading client, or None if keys/lib are unavailable.
    `paper=True` is hard-wired -- this product never routes live orders."""
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    try:
        from alpaca.trading.client import TradingClient
        return TradingClient(key, secret, paper=True)
    except Exception as exc:            # noqa: BLE001
        print(f"[execution] Alpaca client init failed: {exc}")
        return None


def _live_price(symbol: str) -> float:
    """Latest price via the yfinance data layer (key-free). 0.0 if unavailable."""
    try:
        _, candles, _ = fetch_ohlcv(symbol, "5m")
        return float(candles[-1][1]) if candles else 0.0
    except Exception:                   # noqa: BLE001
        return 0.0


def _prices_for(symbols) -> dict:
    """BLOCKING batch of live quotes for the ledger (run via run.io_bound)."""
    return {s: _live_price(s) for s in symbols}


def _submit_paper_order(symbol: str, qty: float, side: str, fallback: float) -> dict:
    """BLOCKING: route a PAPER market order to Alpaca, report the fill price.
    Returns {'price': float} or {'error': str}. Falls back to a live quote if
    Alpaca's position lookup lags right after the fill (same as v1)."""
    client = _alpaca_client()
    if client is None:
        return {"error": "Alpaca paper keys missing from .env — execution offline."}
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        side_enum = OrderSide.BUY if side == "BUY" else OrderSide.SELL
        client.submit_order(order_data=MarketOrderRequest(
            symbol=symbol, qty=qty, side=side_enum, time_in_force=TimeInForce.DAY))
        time.sleep(1)                   # brief pause for the paper fill to settle
        try:
            price = float(client.get_open_position(symbol).current_price)
        except Exception:               # noqa: BLE001
            price = fallback
        return {"price": price if price > 0 else fallback}
    except Exception as exc:            # noqa: BLE001
        return {"error": str(exc)}


def _apply_fill(positions: dict, symbol: str, qty: float, side: str, price: float):
    """Update the session's hybrid local ledger with a fill (v1 avg-entry math)."""
    pos = positions.get(symbol, {"shares": 0.0, "avg_entry": price})
    shares, entry = pos["shares"], pos["avg_entry"]
    if side == "BUY":
        new_shares = shares + qty
        new_entry = (((shares * entry) + (qty * price)) / new_shares
                     if new_shares > 0 else price)
        positions[symbol] = {"shares": new_shares, "avg_entry": new_entry}
    else:  # SELL
        new_shares = shares - qty
        if new_shares <= 1e-9:
            positions.pop(symbol, None)      # closed out (or flipped flat)
        else:
            positions[symbol] = {"shares": new_shares, "avg_entry": entry}


LEDGER_COLUMNS = [
    {"name": "ticker", "label": "TICKER", "field": "ticker", "align": "left"},
    {"name": "shares", "label": "SHARES", "field": "shares", "align": "right"},
    {"name": "entry", "label": "AVG ENTRY", "field": "entry", "align": "right"},
    {"name": "price", "label": "PRICE", "field": "price", "align": "right"},
    {"name": "value", "label": "MKT VALUE", "field": "value", "align": "right"},
    {"name": "pl", "label": "UNREAL P&L", "field": "pl", "align": "right"},
]

_ASSET_CACHE = {"assets": None}   # process-wide: the asset list is fetched once


def _fetch_assets() -> list[dict]:
    """BLOCKING: active, tradable US-equity assets from Alpaca, cached for the
    process. Returns [{symbol, name, exchange, fractionable}] sorted by symbol.
    Powers the EXECUTION symbol directory; ~11k rows, so we fetch it only once."""
    if _ASSET_CACHE["assets"] is not None:
        return _ASSET_CACHE["assets"]
    client = _alpaca_client()
    if client is None:
        return []
    try:
        from alpaca.trading.requests import GetAssetsRequest
        from alpaca.trading.enums import AssetClass, AssetStatus
        assets = client.get_all_assets(GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE))
        out = [{"symbol": a.symbol, "name": a.name or "",
                "exchange": getattr(a.exchange, "value", str(a.exchange)),
                "fractionable": bool(a.fractionable)}
               for a in assets if a.tradable]
        out.sort(key=lambda x: x["symbol"])
        _ASSET_CACHE["assets"] = out
        return out
    except Exception as exc:            # noqa: BLE001
        print(f"[execution] asset directory fetch failed: {exc}")
        return []


def execution_section(state: dict):
    """The EXECUTION tab: a PAPER trade desk + a live-priced session P&L ledger.
    Simulation only -- never routes real money (Alpaca paper endpoint)."""
    state.setdefault("positions", {})

    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-3"):
        ui.label("EXECUTION DESK & SESSION LEDGER").classes("ma-title text-xl")
        ui.label("Route MacroAgent's ideas as PAPER trades and track session P&L. "
                 "Simulation only — no real money is ever routed.").style(
            f"color:{MUTED};font-family:{MONO};font-size:12px;max-width:660px")

        if _alpaca_client() is None:
            ui.label("SYSTEM HALT: Alpaca paper keys missing from .env "
                     "(ALPACA_API_KEY / ALPACA_SECRET_KEY). Execution offline.").style(
                f"color:{DOWN};font-family:{MONO};font-size:13px;margin-top:8px")
            return

        with ui.row().classes("w-full gap-6 flex-wrap items-start"):
            # LEFT: route order ------------------------------------------------
            with ui.column().classes("gap-3").style("min-width:280px;flex:1"):
                ui.label("// ROUTE ORDER").style(
                    f"color:{NEON};font-family:{MONO};font-size:12px;letter-spacing:2px")
                ticker_in = ui.input("Ticker", value="SPY") \
                    .props("dark dense").style(f"color:{NEON}")
                with ui.row().classes("gap-2 items-end w-full"):
                    qty_in = ui.number("Shares", value=1.0, min=0.0001, step=0.01,
                                       format="%.4f").props("dark dense").classes("grow")
                    side_in = ui.select(["BUY", "SELL"], value="BUY",
                                        label="Action").props("dark dense")
                preview = ui.label("").style(
                    f"color:{MUTED};font-family:{MONO};font-size:12px")
                transmit = ui.button("TRANSMIT TRADE", on_click=lambda: transmit_trade()) \
                    .props("outline").style(
                        f"color:{NEON};border-color:{NEON};font-family:{MONO}")

            # RIGHT: hybrid ledger --------------------------------------------
            with ui.column().classes("gap-2").style("min-width:340px;flex:1.4"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("// HYBRID TRADE LEDGER").style(
                        f"color:{NEON};font-family:{MONO};font-size:12px;letter-spacing:2px")
                    ui.button("REFRESH P&L", on_click=lambda: refresh_ledger()) \
                        .props("flat dense").style(f"color:{NEON};font-family:{MONO}")
                ledger_box = ui.column().classes("w-full")

        # SYMBOL DIRECTORY (searchable Alpaca asset list -> click loads the form)
        ui.separator().props("color=grey-9")
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("// SYMBOL DIRECTORY").style(
                    f"color:{NEON};font-family:{MONO};font-size:12px;letter-spacing:2px")
                dir_status = ui.label("").style(
                    f"color:{MUTED};font-family:{MONO};font-size:12px")
            dir_search = ui.input(
                placeholder="Search ticker or company — e.g. AAPL, Tesla, energy") \
                .props("dark dense clearable debounce=350").classes("w-full") \
                .style(f"color:{NEON}")
            dir_results = ui.column().classes("w-full")

    last = {"price": 0.0}   # cached quote so qty edits don't re-hit the network

    def _draw_preview():
        qty = float(qty_in.value or 0)
        p = last["price"]
        preview.text = (f"Est. order value: ${p * qty:,.2f}  (@ ${p:,.2f}/sh)"
                        if p > 0 else
                        "Live price preview unavailable — order routes at market.")

    async def refresh_price():
        sym = (ticker_in.value or "").strip().upper()
        last["price"] = await run.io_bound(_live_price, sym) if sym else 0.0
        _draw_preview()

    def render_ledger(prices: dict):
        ledger_box.clear()
        positions = state["positions"]
        with ledger_box:
            if not positions:
                ui.label("Portfolio is flat — no positions this session.").style(
                    f"color:{MUTED};font-family:{MONO};font-size:12px")
                return
            rows, total_pl = [], 0.0
            for tk, d in positions.items():
                shares, entry = d["shares"], d["avg_entry"]
                price = prices.get(tk) or entry     # fall back to entry if no quote
                mv = shares * price
                pl = mv - shares * entry
                total_pl += pl
                rows.append({"ticker": tk, "shares": f"{shares:.4f}",
                             "entry": f"${entry:,.2f}", "price": f"${price:,.2f}",
                             "value": f"${mv:,.2f}", "pl": f"${pl:,.2f}"})
            ui.table(columns=LEDGER_COLUMNS, rows=rows, row_key="ticker") \
                .props("dark flat").classes("w-full")
            color = NEON if total_pl >= 0 else DOWN
            ui.label(f"SESSION P&L: ${total_pl:,.2f}").style(
                f"color:{color};font-family:{MONO};font-weight:700;margin-top:4px")

    async def refresh_ledger():
        prices = await run.io_bound(_prices_for, list(state["positions"].keys()))
        render_ledger(prices)

    async def transmit_trade():
        sym = (ticker_in.value or "").strip().upper()
        qty = float(qty_in.value or 0)
        side = side_in.value
        if not sym or qty <= 0:
            ui.notify("Enter a ticker and a positive share quantity.", type="warning")
            return
        transmit.disable()
        try:
            fallback = await run.io_bound(_live_price, sym)
            result = await run.io_bound(_submit_paper_order, sym, qty, side, fallback)
        finally:
            transmit.enable()
        if "error" in result:
            ui.notify(f"REJECTED: {result['error']}", type="negative")
            log_event("paper_trade_rejected",
                      {"symbol": sym, "side": side, "qty": qty,
                       "error": result["error"][:120]},
                      session=state.get("session_id"))
            return
        price = result["price"] or fallback
        _apply_fill(state["positions"], sym, qty, side, price)
        ui.notify(f"✔️ {side} EXECUTED: {qty:.4f}x {sym} @ ${price:,.2f}",
                  type="positive")
        log_event("paper_trade",
                  {"symbol": sym, "side": side, "qty": qty, "price": price},
                  session=state.get("session_id"))
        await refresh_ledger()

    dir_assets = {"list": None}   # lazy-loaded once, then filtered client-side

    async def _pick_symbol(sym: str):
        ticker_in.value = sym
        dir_status.text = f"Loaded {sym} ↑ into the order form"
        await refresh_price()

    async def _do_search():
        if dir_assets["list"] is None:            # first search pays the fetch
            dir_status.text = "Loading symbol directory…"
            dir_assets["list"] = await run.io_bound(_fetch_assets)
            dir_status.text = (f"{len(dir_assets['list']):,} tradable symbols"
                               if dir_assets["list"] else
                               "Directory unavailable (Alpaca offline).")
        term = (dir_search.value or "").strip()
        dir_results.clear()
        if not term:
            return
        up = term.upper()
        matches = [a for a in dir_assets["list"]
                   if a["symbol"].startswith(up) or term.lower() in a["name"].lower()][:40]
        with dir_results:
            if not matches:
                ui.label("No matches.").style(
                    f"color:{MUTED};font-family:{MONO};font-size:12px")
                return
            with ui.list().props("dense bordered separator").classes("w-full"):
                for a in matches:
                    with ui.item(on_click=lambda a=a: _pick_symbol(a["symbol"])) \
                            .props("clickable"):
                        with ui.item_section():
                            ui.item_label(a["symbol"]).style(
                                f"color:{NEON};font-family:{MONO};font-weight:700")
                            ui.item_label(a["name"] or "—").props("caption").style(
                                f"color:{MUTED};font-family:{MONO}")
                        with ui.item_section().props("side"):
                            tag = a["exchange"] + (" · frac" if a["fractionable"] else "")
                            ui.label(tag).style(
                                f"color:{MUTED};font-family:{MONO};font-size:11px")

    ticker_in.on("blur", lambda: refresh_price())
    qty_in.on_value_change(lambda: _draw_preview())
    dir_search.on_value_change(lambda: _do_search())
    ui.timer(0.3, refresh_price, once=True)       # seed the SPY preview
    ui.timer(0.3, refresh_ledger, once=True)      # show flat-portfolio message


# ----------------------------------------------------------------------
# APP SHELL  (@ui.page = each browser gets its own state; multi-tenant-ready)
# ----------------------------------------------------------------------
@ui.page("/")
def main_page():
    ui.add_head_html(GLOBAL_CSS)
    ui.colors(primary=NEON, secondary=NEON, accent=NEON, positive=NEON)
    # shared across tabs this session (per-client isolation, multi-tenant-ready)
    state = {"profile": None, "research": [], "research_text": {},
             "memo": None, "chat_history": [], "positions": {},
             "session_id": uuid.uuid4().hex[:12]}   # correlate this client's events
    log_event("session_start", session=state["session_id"])

    with ui.header().classes("items-center"):
        ui.label("MacroAgent").classes("ma-title text-xl")
        ui.space()
        with ui.tabs() as tabs:
            t_profile = ui.tab("PROFILE")
            t_strategy = ui.tab("STRATEGY")
            t_charts = ui.tab("CHARTS")
            t_exec = ui.tab("EXECUTION")

    with ui.tab_panels(tabs, value=t_profile).classes("w-full"):
        with ui.tab_panel(t_profile):
            profile_section(state)
        with ui.tab_panel(t_strategy):
            strategy_section(state)
        with ui.tab_panel(t_charts):
            chart = charts_section()
        with ui.tab_panel(t_exec):
            execution_section(state)

    # ECharts renders at zero size while its tab is hidden; resize on first view
    def _on_tab(e):
        if e.value == "CHARTS":
            chart.run_chart_method("resize")
    tabs.on_value_change(_on_tab)


if __name__ in {"__main__", "__mp_main__"}:
    # Managed hosts (Render/Railway) inject the port via $PORT and expect the app
    # to bind 0.0.0.0; locally these default to 8090. reload stays False for prod.
    ui.run(
        title="MacroAgent",
        dark=True,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8090")),
        reload=False,
        show=False,                                    # never auto-open a browser server-side
        storage_secret=os.getenv("NICEGUI_STORAGE_SECRET"),
    )
