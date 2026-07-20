# MacroAgent — Project Context

Educational macro/markets tool. **Not** a live-trading or advice product: everything
stays paper/simulation and educational. No real money is ever routed. This is not
financial advice software.

## Current migration
Moving OFF Streamlit (v1) ONTO a NiceGUI app (v2). v1 still works and is deployed;
v2 is where new work happens.

## Files
- `macroagent_app.py` — **the v2 app. This is the active file.** NiceGUI + ECharts.
- `app1.py` — legacy v1 Streamlit app (still deployed). Live candlestick charts were
  already merged into it, replacing the old FRED display charts.
- `v1_live_charts.py`, `macroagent_charts.py` — earlier stepping-stone files, kept for
  reference. Not the active app.
- `requirements.txt` — v1 deps (+ yfinance). v2 needs: `nicegui`, `yfinance`,
  `pandas`, plus (for the next phase) `google-genai`, `fedfred`, `pypdf`, `alpaca-py`.
- `.env` — API keys (gitignored). Keys in use: `FRED_API_KEY`, `GOOGLE_API_KEY`,
  `AV_API_KEY` (note: v1 code reads `ALPHA_VANTAGE_API_KEY` — mismatch, low priority),
  `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`.

## Run
    python macroagent_app.py      # serves on http://localhost:8090

## Design system (keep everything consistent with this)
- Neon terminal aesthetic. Accent `#00ffa3` (NEON). Bearish/negative `#ff3b6b` (DOWN).
  Background `#0a0a0a`, panels `#111111`, gridlines `#222`, muted text `#888`.
- Font: Courier New monospace everywhere.
- Brand color is set globally via `ui.colors(primary=NEON, ...)` — do NOT hardcode
  per-widget colors like `color=green`; let widgets inherit primary so the UI stays
  cohesive. All buttons are one neon color (global `.q-btn` CSS rule in GLOBAL_CSS).

## Architecture patterns
- Single `@ui.page("/")` `main_page()`; each browser gets its own `state` dict
  (`{"profile": ..., "research": [...]}`) — per-client isolation, multi-tenant-ready.
- Tab shell: PROFILE (landing/default) · CHARTS · STRATEGY · EXECUTION.
- Section functions build their own UI; `charts_section()` returns its chart element so
  the shell can call `chart.run_chart_method("resize")` when the Charts tab is shown
  (ECharts renders at 0px while hidden).
- Data layer uses `yfinance`; suffixes span asset classes (BTC-USD, EURUSD=X, ES=F).

## Built so far
- CHARTS: live candlestick + volume (ECharts), interval selector, asset-class presets,
  a TRENDING row (tap to load), 15s live polling.
- PROFILE: ported from v1 Steps 1 & 2 (literacy, budget, horizon, goal, strategy,
  options interest, 1–10 risk slider) + a LEARNING FOCUS multiselect of industries
  + optional institutional PDF upload. Saves into `state["profile"]`.

## Next up (this phase)
1. **STRATEGY tab** — port the Gemini engine from `app1.py`. Read `state["profile"]`
   and generate a personalized education/investment memo (IPS) that centers on the
   user's selected **industries + risk level + live FRED macro data + uploaded PDFs
   (RAG)**. Keep tone educational. Reuse the v1 prompt structure as a starting point.
2. **EXECUTION tab** — port the Alpaca **paper-trading** ledger + P&L from `app1.py`.
   Paper/simulation only. Never enable live trading.

## Later roadmap
- Split this single file into modules once Strategy/Execution land.
- Supabase: auth (incl. optional SSO), Postgres persistence (profiles/trades) with
  Row-Level Security, and pgvector to upgrade the RAG from text-concat to real
  semantic search.

## Constraints / reminders
- Educational + paper only. No live order routing, no personalized financial advice.
- Don't hardcode secrets. Read from `.env` via `python-dotenv`.
- Keep the neon design system consistent; prefer `ui.colors`/inherited colors.
