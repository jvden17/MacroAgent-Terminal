import streamlit as st
import os
import glob
import time
import requests
import re
import pandas as pd
import polars as pl
import plotly.graph_objects as go
import fedfred as fd
from pypdf import PdfReader
from google import genai
from dotenv import load_dotenv

# 1. Page Config
st.set_page_config(page_title="MacroAgent", page_icon="📈", layout="wide")

# Initialize Session State
if "memo_text" not in st.session_state:
    st.session_state.memo_text = None
if "rag_text" not in st.session_state:
    st.session_state.rag_text = None
if "chart_gdp" not in st.session_state:
    st.session_state.chart_gdp = None
if "chart_unrate" not in st.session_state:
    st.session_state.chart_unrate = None
if "chart_cpi" not in st.session_state:
    st.session_state.chart_cpi = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
# --- Progressive UI State ---
if "app_step" not in st.session_state:
    st.session_state.app_step = 1
if "memo_index" not in st.session_state:
    st.session_state.memo_index = 0
if "parsed_intro" not in st.session_state:
    st.session_state.parsed_intro = ""
if "parsed_sections" not in st.session_state:
    st.session_state.parsed_sections = []
# --- NEW: Hybrid Local Portfolio State ---
if "local_positions" not in st.session_state:
    st.session_state.local_positions = {}

# 2. Custom CSS Injection 
custom_css = """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    .edgy-title {
        font-family: 'Courier New', monospace;
        color: #ffffff; 
        text-shadow: 0px 0px 30px #00ffa3; 
        font-size: 10vw; 
        font-weight: 900;
        letter-spacing: 4px; 
        text-transform: uppercase;
        margin-bottom: 0px;
        padding-bottom: 0px;
        line-height: 1.1; 
        text-align: center;
    }
    
    .edgy-subtitle {
        color: #00ffa3;
        font-family: 'Courier New', monospace;
        font-size: 2vw; 
        font-weight: 700;
        margin-top: 10px;
        margin-bottom: 70px;
        letter-spacing: 4px;
        text-transform: uppercase;
        text-align: center;
    }

    .stButton > button {
        background-color: transparent !important;
        color: #00ffa3 !important;
        border: 2px solid #00ffa3 !important;
        border-radius: 0px !important; 
        font-weight: 700 !important;
        font-family: 'Courier New', monospace;
        text-transform: uppercase;
        letter-spacing: 2px;
        width: 100%;
        padding: 14px !important; 
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        background-color: #00ffa3 !important;
        color: #000000 !important;
        box-shadow: 0 0 20px #00ffa3;
    }

    .output-box {
        background-color: #080808;
        border-left: 6px solid #00ffa3;
        padding: 40px;
        margin-top: 30px;
        font-family: 'Arial', sans-serif;
        line-height: 1.8;
        font-size: 1.1rem; 
        color: #f0f0f0;
    }
    
    /* Neon Header Fix */
    .output-box h3 {
        color: #00ffa3 !important;
        font-family: 'Courier New', monospace !important;
        text-transform: uppercase !important;
        font-size: 1.4rem !important; 
        margin-top: 0px !important;
        margin-bottom: 15px !important;
        display: block !important;
    }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# RAG ENGINE
def get_institutional_context(uploaded_files=None):
    context = ""
    # Search local directory
    pdf_files = glob.glob("knowledge_base/*.pdf")
    for file in pdf_files:
        try:
            reader = PdfReader(file)
            for i in range(min(len(reader.pages), 3)):
                context += reader.pages[i].extract_text()
        except Exception as e:
            print(f"Error reading PDF {file}: {e}")
    
    # Search user-uploaded files
    if uploaded_files:
        for uploaded_file in uploaded_files:
            try:
                reader = PdfReader(uploaded_file)
                for i in range(min(len(reader.pages), 3)):
                    context += reader.pages[i].extract_text()
            except Exception as e:
                st.error(f"Error reading uploaded PDF: {e}")
    return context

# DATA CLEANING
def to_polars_clean(pd_data, value_name):
    df = pd.DataFrame(pd_data)
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    pl_df = pl.from_pandas(df)
    
    cols = pl_df.columns
    date_col = next((c for c in cols if 'date' in c.lower() or 'index' in c.lower()), cols[0])
    val_col = next((c for c in cols if 'value' in c.lower()), cols[-1])
    
    return pl_df.select([
        pl.col(date_col).alias("Date"),
        pl.col(val_col).cast(pl.Float64).alias(value_name) 
    ])

# CHART
def create_ticker_chart(polars_df, title):
    x_vals = polars_df["Date"].to_list()
    y_vals = polars_df[polars_df.columns[1]].to_list()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_vals, 
        y=y_vals,
        mode='lines+markers',
        line=dict(color='#00ffa3', width=2),
        marker=dict(color='#ffffff', size=4),
        name=title
    ))
    
    fig.update_layout(
        title=dict(text=title, font=dict(color='#ffffff', family='Courier New', size=18)),
        paper_bgcolor='#111111', 
        plot_bgcolor='#111111',
        margin=dict(l=50, r=20, t=50, b=50),
        xaxis=dict(showgrid=True, gridcolor='#333', tickfont=dict(color='#888')),
        yaxis=dict(showgrid=True, gridcolor='#333', tickfont=dict(color='#888'), title=dict(text="PRICE/VALUE", font=dict(color='#888'))),
        height=300
    )
    return fig

# AI HANDLER GEMINI 2.0, OFFLINE MODE & RETRY LOGIC
class MockResponse:
    def __init__(self, text):
        self.text = text

def call_ai_with_retry(client, prompt_text):
    max_retries = 2 
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.1-pro-preview',
                contents=prompt_text,
                config={"temperature": 0.1} 
            )
            return response
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "503" in error_msg or "quota" in error_msg:
                if attempt < max_retries - 1:
                    time.sleep(1) 
                else:
                    st.warning("⚠️ Google API Congested. Engaging Offline Dev Mode to unlock your UI.")
                    fake_memo = """
### 🚨 OFFLINE DEV MODE: MOCK STRATEGY MEMO 🚨

**Macro-Economic Synthesis:** Google's API is currently blocking requests due to free-tier rate limits. However, this placeholder text allows you to bypass the crash and view your terminal's UI.

**Strategic Asset Allocation:** - **70% SPY (Core Equities):** Broad market exposure.
- **30% BIL (Cash Equivalents):** Capital preservation while awaiting API unlock.

**Tactical Execution:**
Scroll down to test the Markdown Export button, ask a mock question in the Chat UI, and test your Alpaca Execution Ledger!
                    """
                    return MockResponse(fake_memo)
            else:
                return MockResponse(f"Unexpected Error: {e}")

# 3. Header & System Controls
top_spacer, top_button = st.columns([9.3, 0.7]) 
with top_button:
    if st.button("🔄 RESET"):
        for key in st.session_state.keys():
            del st.session_state[key]
        st.rerun()

st.markdown(
    '<p style="font-family:\'Courier New\', monospace; color:#ffffff; text-shadow:0px 0px 30px #00ffa3; font-size:10vw; font-weight:900; letter-spacing:4px; text-transform:uppercase; margin-bottom:0px; padding-bottom:0px; line-height:1.1; text-align:center;">MacroAgent</p>', 
    unsafe_allow_html=True
)
st.markdown(
    '<p style="color:#00ffa3; font-family:\'Courier New\', monospace; font-size:2vw; font-weight:700; margin-top:10px; margin-bottom:70px; letter-spacing:4px; text-transform:uppercase; text-align:center;">Institutional Intelligence. Retail Execution.</p>', 
    unsafe_allow_html=True
)

# UI STEP 1: INVESTOR PROFILING
st.markdown("### INVESTOR PROFILING")

literacy = st.selectbox("Financial Literacy Level", ["Beginner (Teach me the absolute basics)", "Novice (I know a few concepts, still learning)", "Intermediate (Familiar with markets, please keep jargon light)", "Advanced (I understand financial statements & market cycles)", "Expert (Give me raw data and institutional framing)"])
budget = st.number_input("Initial Investment Budget ($)", min_value=100, max_value=10000000, value=5000, step=500)
horizon = st.selectbox("Time Horizon (When do you need this money?)", ["Short-Term (0-3 Years)", "Medium-Term (3-7 Years)", "Long-Term (7+ Years)"])
goal = st.selectbox("Realistic Growth Goal", ["Income & Yield (Focus on Dividends/Bonds, ~3-5% yearly)", "Steady Wealth Accumulation (Market average, ~7-10% yearly)", "Aggressive Capital Appreciation (Targeting 12%+ yearly, willing to accept high volatility)"])
strategy = st.selectbox("Preferred Strategic Approach", ["Core-Satellite (70% safe index funds, 30% aggressive stock picking)", "Trend Following (Riding market momentum and sectors)", "Value Investing (Hunting for undervalued, beaten-down assets)", "Pure Passive (Set it and forget it in broad ETFs)"])
options_trading = st.checkbox("I am interested in learning Options Trading (Calls/Puts)")

# --- USER RESEARCH DROP ZONE ---
st.write("")
st.markdown("#### INSTITUTIONAL UPLOAD (OPTIONAL)")
user_research = st.file_uploader("Drop PDF research papers here to inform MacroAgent's analysis", type="pdf", accept_multiple_files=True)

# Step 2
if st.session_state.app_step == 1:
    st.write("")
    if st.button("CONFIRM INVESTOR PROFILE"):
        st.session_state.app_step = 2
        st.rerun()

# UI STEP 2: RISK CALIBRATION
if st.session_state.app_step >= 2:
    st.write("")
    st.markdown("### RISK CALIBRATION")
    risk_levels = ["1 - Capital Preservation", "2 - Highly Conservative", "3 - Conservative", "4 - Moderate", "5 - Balanced", "6 - Growth-Oriented", "7 - Aggressive Growth", "8 - Highly Aggressive", "9 - Maximum Alpha", "10 - Speculative"]
    risk_tolerance = st.select_slider("Select your exact risk appetite:", options=risk_levels, value="5 - Balanced")
    st.divider()

    # Action Engine
    if st.session_state.app_step == 2:
        if st.button("INITIALIZE STRATEGY & EDUCATION ENGINE"):
            with st.spinner("QUANT ENGINE ACTIVE: Harvesting Live Fed, Market & Institutional Flows..."):
                try:
                    load_dotenv()
                    fred = fd.FredAPI(api_key=os.getenv("FRED_API_KEY"))
                    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
                    
                    # Pull RAG Context (Combined local + uploaded)
                    st.session_state.rag_text = get_institutional_context(user_research)
                    
                    # Pull & Clean Fed Data
                    gdp_full = to_polars_clean(fred.get_series_observations("GDPC1"), "Real GDP")
                    unrate_full = to_polars_clean(fred.get_series_observations("UNRATE"), "Unemployment")
                    cpi_full = to_polars_clean(fred.get_series_observations("CPIAUCSL"), "CPI")
                    
                    # Generate & Save Charts to Memory
                    st.session_state.chart_gdp = create_ticker_chart(gdp_full.tail(20), "REAL GDP T-20")
                    st.session_state.chart_unrate = create_ticker_chart(unrate_full.tail(20), "UNEMPLOYMENT T-20")
                    st.session_state.chart_cpi = create_ticker_chart(cpi_full.tail(20), "INFLATION (CPI) T-20")
                    
                    # Raw data passed directly to the final prompt
                    pulse_data = f"Real GDP (Last 3 Quarters): {gdp_full.tail(3).to_dicts()}\nUnemployment Rate (Last 3 Months): {unrate_full.tail(3).to_dicts()}\nInflation/CPI (Last 3 Months): {cpi_full.tail(3).to_dicts()}"
                    
                    av_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey={os.getenv('ALPHA_VANTAGE_API_KEY')}"
                    av_response = requests.get(av_url).json()
                    
                    try:
                        spy_price = av_response["Global Quote"]["05. price"]
                        market_pulse = f"S&P 500 ETF (SPY) Current Price: ${float(spy_price):.2f}"
                    except:
                        market_pulse = "SPY live data temporarily unavailable."
                    
                    lit_clean = literacy.split(" (")[0]
                    horizon_clean = horizon.split(" (")[0]
                    goal_clean = goal.split(" (")[0]
                    strat_clean = strategy.split(" (")[0]
                    risk_clean = risk_tolerance.split(" - ")[1]
                    
                    # AI CALL
                    memo_prompt = f"""
                    You are MacroAgent, an advanced AI Institutional Portfolio Strategist. 
                    Translate complex institutional capital flows, global macro data, and the provided house view into a predictive, alpha-generating Investment Policy Statement (IPS) for a retail client.
                    
                    CLIENT PROFILE:
                    - Literacy Level: {lit_clean}
                    - Risk Tolerance: {risk_clean}
                    - Budget: ${budget:,.2f}
                    - Time Horizon: {horizon_clean}
                    - Primary Goal: {goal_clean}
                    - Strategy: {strat_clean}
                    - Options Interest: {"Yes" if options_trading else "No"}
                    
                    CURRENT MACRO DATA (Raw Fed Telemetry - Extract and explain the trends):
                    {pulse_data}
                    
                    LIVE MARKET DATA (Alpha Vantage):
                    {market_pulse}

                    INSTITUTIONAL RESEARCH (PDF Context):
                    {st.session_state.rag_text[:4000]}
                    
                    OUTPUT STRUCTURE & SPECIFICITY INSTRUCTIONS:
                    - ### Macro-Economic Synthesis: Explain the 'Why' behind the Fed data and the current market environment. Connect it directly to the Live Market Data (SPY).
                    - ### Institutional Thesis & Sector Rotation: Explicitly cite the "Institutional Research" provided above. Based on this house view, provide a predictive forecast on specific sector rotation.
                    - ### Strategic Asset Allocation: Provide a precise % allocation breakdown. Detail exactly what to buy (specific ETF types, asset classes, or stock profiles) to hit their {goal_clean} goal while respecting their {risk_clean} limit.
                    - ### Tactical Execution: Walk them through the execution. If Options Interest is "Yes", provide a sophisticated but accessible primer on a specific hedging strategy.
                    
                    TONE & FORMATTING:
                    - Professional, authoritative, and highly specific. Do not use generic filler. Introduce yourself as MacroAgent.
                    - Do NOT use numbered lists for the main headers.
                    - Do NOT use backticks (`) or code blocks anywhere.
                    """
                    
                    memo = call_ai_with_retry(client, memo_prompt)
                    
                    # Save to memory, advance step, and refresh to render
                    st.session_state.memo_text = memo.text
                    st.session_state.app_step = 3
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"INTELLIGENCE OVERFLOW: {e}")

#UI STEP 3: RENDER DASHBOARD & CHAT
if st.session_state.app_step >= 3 and st.session_state.memo_text:
    st.markdown("### MACRO TELEMETRY // STANDARD TICKER FEED")
    chart_col1, chart_col2, chart_col3 = st.columns(3)
    with chart_col1:
        st.plotly_chart(st.session_state.chart_gdp, use_container_width=True)
    with chart_col2:
        st.plotly_chart(st.session_state.chart_unrate, use_container_width=True)
    with chart_col3:
        st.plotly_chart(st.session_state.chart_cpi, use_container_width=True)

    # --- DYNAMIC MEMO PARSER & PROGRESSIVE LEARNING UI ---
    st.write("")
    
    # Parse the memo only once and save it to memory
    if not st.session_state.parsed_sections:
        parts = re.split(r'(?m)^###\s+', st.session_state.memo_text)
        st.session_state.parsed_intro = parts[0] if parts else ""
        
        for part in parts[1:]:
            lines = part.split('\n', 1)
            title = lines[0].strip().replace('**', '')
            content = lines[1] if len(lines) > 1 else ""
            st.session_state.parsed_sections.append({"title": title, "content": content})

    # Show the Intro greeting only on the first page
    if st.session_state.memo_index == 0 and st.session_state.parsed_intro.strip():
        st.markdown(f'<div class="output-box"><em>{st.session_state.parsed_intro}</em></div>', unsafe_allow_html=True)

    # Render the current section
    if st.session_state.parsed_sections:
        current_section = st.session_state.parsed_sections[st.session_state.memo_index]
        
        st.markdown(
            f'<div class="output-box"><h3>{current_section["title"]}</h3>{current_section["content"]}</div>', 
            unsafe_allow_html=True
        )
        
        st.write("") 
        
        # Pagination Buttons
        nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
        with nav_col1:
            if st.session_state.memo_index > 0:
                if st.button("⬅️ PREVIOUS"):
                    st.session_state.memo_index -= 1
                    st.rerun()
        with nav_col3:
            if st.session_state.memo_index < len(st.session_state.parsed_sections) - 1:
                if st.button("NEXT MODULE ➡️"):
                    st.session_state.memo_index += 1
                    st.rerun()
            else:
                st.success("✅ STRATEGY REVIEW COMPLETE")
    
    st.write("")
    st.download_button(
        label="📄 DOWNLOAD STRATEGY DOCUMENT",
        data=st.session_state.memo_text,
        file_name="MacroAgent_IPS.md",
        mime="text/markdown"
    )
    
    with st.expander("VIEW SOURCE MATERIAL (RAG INGESTION)"):
        if st.session_state.rag_text:
            st.info("The following text was extracted from your knowledge_base and sent to the AI:")
            st.text(st.session_state.rag_text[:2000]) 
        else:
            st.warning("No PDF text detected. Check your knowledge_base folder.")

    # INTERACTIVE STRATEGY DEBRIEF (CHAT)
    st.divider()
    st.markdown("### STRATEGIC DEBRIEF (Q&A)")
    st.markdown("Interrogate MacroAgent on the logic behind this allocation.")
    
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if user_question := st.chat_input("Ask MacroAgent about the strategy..."):
        st.session_state.chat_history.append({"role": "user", "content": user_question})
        with st.chat_message("user"):
            st.markdown(user_question)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing strategy..."):
                try:
                    chat_context = f"""
                    You are MacroAgent. You just wrote the following Investment Policy Statement for a client:
                    ---
                    {st.session_state.memo_text}
                    ---
                    The client is asking a follow-up question. Answer concisely, professionally, and stay in character. 
                    Client Question: {user_question}
                    """
                    load_dotenv()
                    chat_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
                    chat_response = call_ai_with_retry(chat_client, chat_context)
                    
                    st.markdown(chat_response.text)
                    st.session_state.chat_history.append({"role": "assistant", "content": chat_response.text})
                except Exception as e:
                    st.error(f"COMMS ERROR: {e}")
                    
    # Unlock Step 4
    if st.session_state.app_step == 3:
        st.write("")
        if st.button("AUTHORIZE EXECUTION DESK"):
            st.session_state.app_step = 4
            st.rerun()

# UI STEP 4: THE EXECUTION DESK & LIVE LEDGER
if st.session_state.app_step >= 4:
    st.divider()
    st.markdown("### EXECUTION DESK & SESSION LEDGER")
    st.markdown("Execute MacroAgent's recommended allocations and monitor your local session P&L.")

    load_dotenv()
    ALPACA_KEY = os.getenv("ALPACA_API_KEY")
    ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")

    if not ALPACA_KEY or not ALPACA_SECRET:
        st.warning("SYSTEM HALT: Alpaca API keys missing from .env file. Execution offline.")
    else:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        
        trading_client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)

        desk_col, ledger_col = st.columns([1, 1.5]) 
        
        # --- LEFT SIDE: THE TRADE DESK ---
        with desk_col:
            st.markdown("#### ROUTE ORDER")
            trade_ticker = st.text_input("Ticker Symbol (e.g. SPY, AAPL)", value="SPY").upper()
            
            trade_col_a, trade_col_b = st.columns(2)
            with trade_col_a:
                trade_qty = st.number_input("Shares", min_value=0.0001, value=1.0000, step=0.0100, format="%.4f")
            with trade_col_b:
                trade_side = st.selectbox("Action", ["BUY", "SELL"])
                
            # Improved Live Price Preview Fallback
            preview_price = 0.0
            try:
                y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{trade_ticker}"
                y_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                y_res = requests.get(y_url, headers=y_headers, timeout=3).json()
                preview_price = float(y_res['chart']['result'][0]['meta']['regularMarketPrice'])
                est_value = preview_price * trade_qty
                st.info(f"💸 **Est. Order Value:** ${est_value:,.2f} (@ ${preview_price:,.2f}/sh)")
            except:
                preview_price = 150.00 # Safe fallback so math never breaks if Yahoo blocks the request
                st.info("⚠️ Live price preview unavailable. Order will execute at market.")
                
            if st.button("TRANSMIT TRADE"):
                with st.spinner(f"Routing {trade_side} for {trade_qty}x {trade_ticker}..."):
                    try:
                        # 1. Route the actual order to Alpaca (Backend)
                        side_enum = OrderSide.BUY if trade_side == "BUY" else OrderSide.SELL
                        market_order_data = MarketOrderRequest(
                            symbol=trade_ticker,
                            qty=trade_qty,
                            side=side_enum,
                            time_in_force=TimeInForce.DAY 
                        )
                        order = trading_client.submit_order(order_data=market_order_data)
                        time.sleep(1) # Brief pause for Alpaca network 
                        
                        # 2. Fetch the current market price for the entry data
                        try:
                            pos = trading_client.get_open_position(trade_ticker)
                            current_price = float(pos.current_price)
                        except:
                            current_price = preview_price # Fallback to preview if Alpaca API lags
                            
                        # 3. Update the Local Hybrid Portfolio
                        if trade_ticker not in st.session_state.local_positions:
                            st.session_state.local_positions[trade_ticker] = {"shares": 0.0, "avg_entry": current_price}
                            
                        curr_shares = st.session_state.local_positions[trade_ticker]["shares"]
                        curr_entry = st.session_state.local_positions[trade_ticker]["avg_entry"]
                        
                        if trade_side == "BUY":
                            new_shares = curr_shares + trade_qty
                            new_entry = ((curr_shares * curr_entry) + (trade_qty * current_price)) / new_shares if new_shares > 0 else current_price
                            st.session_state.local_positions[trade_ticker] = {"shares": new_shares, "avg_entry": new_entry}
                        else: # SELL
                            new_shares = curr_shares - trade_qty
                            if new_shares <= 0:
                                del st.session_state.local_positions[trade_ticker]
                            else:
                                st.session_state.local_positions[trade_ticker]["shares"] = new_shares

                        # BUG FIX: Removed st.rerun() so the success message and ledger update don't get wiped!
                        st.success(f"✔️ {trade_side} EXECUTED: {trade_qty}x {trade_ticker}")
                        
                    except Exception as e:
                        # If Alpaca rejects the fractional share, you will now explicitly see why here
                        st.error(f"REJECTED: {e}")

        # --- RIGHT SIDE: THE HYBRID LEDGER ---
        with ledger_col:
            st.markdown("#### HYBRID TRADE LEDGER")
            
            if not st.session_state.local_positions:
                st.info("Portfolio is currently flat. No active positions in this session.")
            else:
                # Silently pull global Alpaca positions just to extract live market prices
                try:
                    global_positions = trading_client.get_all_positions()
                    live_prices = {p.symbol: float(p.current_price) for p in global_positions}
                except:
                    live_prices = {}
                    
                pos_data = []
                for ticker, data in st.session_state.local_positions.items():
                    shares = data["shares"]
                    entry = data["avg_entry"]
                    
                    # Live Math Calculation
                    current_price = live_prices.get(ticker, entry) 
                    market_value = shares * current_price
                    unrealized_pl = market_value - (shares * entry)
                    
                    pos_data.append({
                        "Ticker": ticker,
                        "Shares": f"{shares:.4f}", 
                        "Avg Entry": f"${entry:.2f}",
                        "Current Price": f"${current_price:.2f}",
                        "Market Value": f"${market_value:.2f}",
                        "Unrealized P&L": f"${unrealized_pl:.2f}"
                    })
                    
                pl_positions = pl.DataFrame(pos_data)
                st.dataframe(pl_positions, use_container_width=True, hide_index=True)