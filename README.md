# MacroAgent 📈
**Institutional Intelligence. Retail Execution.**

MacroAgent is a lightweight, Python-based quantitative trading terminal. It was built to demonstrate how large language models (LLMs) and high-speed data pipelines can be integrated to synthesize macroeconomic data into actionable portfolio strategies. 

## 🚀 Key Features

* **AI Portfolio Strategist:** Utilizes Google's Gemini API to generate customized Investment Policy Statements (IPS) based on user risk tolerance and financial goals.
* **Retrieval-Augmented Generation (RAG):** Ingests local PDF documents (e.g., institutional research or market outlooks) to provide the AI with highly specific, hyper-relevant context.
* **Optimized Data Pipeline:** Leverages **Polars** to rapidly parse and structure market and macroeconomic telemetry.
* **Simulated Execution Desk:** Integrates with the Alpaca Trading API to route simulated paper-trading orders and track live portfolio P&L.
* **Resilient Architecture:** Features custom network-routing logic (exponential backoff) to gracefully handle API rate limits and ensure zero-downtime UI rendering.

## 🛠️ Technology Stack

* **Language:** Python 3.10+
* **Frontend:** Streamlit, Plotly
* **Data Processing:** Polars, Pandas
* **AI/LLM:** Google GenAI SDK (`gemini-2.0-flash`)
* **APIs:** Alpaca Markets, FRED (Federal Reserve), Alpha Vantage

## 💻 Running the Application Locally

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YourUsername/MacroAgent.git](https://github.com/YourUsername/MacroAgent.git)
   cd MacroAgent