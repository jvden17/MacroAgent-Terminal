import os
import fedfred as fd
from google import genai
from dotenv import load_dotenv

def main():
    print("-> System Initializing...")
    
    # 1. LOAD THE VAULT
    load_dotenv()
    FRED_KEY = os.getenv("FRED_API_KEY")
    GOOGLE_KEY = os.getenv("GOOGLE_API_KEY")

    if not FRED_KEY or not GOOGLE_KEY:
        raise ValueError("Missing API Keys. Ensure your .env file is correctly named.")

    # 2. INITIALIZE SERVICES
    try:
        fred = fd.FredAPI(api_key=FRED_KEY)
    except Exception as e:
        print(f"[!] Failed to connect to FRED: {e}")
        return

    # Initialize the modern Gemini Client
    client = genai.Client(api_key=GOOGLE_KEY)

    # 3. PULL DATA (The Engine)
    print("-> Fetching live macroeconomic data via FedFred...")
    try:
        gdp = fred.get_series_observations("GDPC1")
        unemployment = fred.get_series_observations("UNRATE")
        inflation = fred.get_series_observations("CPIAUCSL")
        
        # Package the tail ends of the data (the most recent prints)
        pulse_data = f"""
        Real GDP (Last 3 Quarters):
        {gdp.tail(3)}
        
        Unemployment Rate (Last 3 Months):
        {unemployment.tail(3)}
        
        Inflation/CPI (Last 3 Months):
        {inflation.tail(3)}
        """
    except Exception as e:
        print(f"[!] Data extraction failed. Check your FRED API key. Error: {e}")
        return

    # 4. THE AI PIPELINE (The Brain)
    print("-> Data secured. Handing over to Gemini 2.5 Flash for parsing...")
    try:
        # Step A: Fast data processing with 2.5 Flash (Free Tier)
        structured_data = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"You are a quantitative data parser. Extract the latest numeric trends from this raw data and summarize the trajectory strictly in short bullet points:\n{pulse_data}"
        )
        
        print("-> Trends parsed. Activating Gemini 2.5 Flash for Strategy Advisory...")
        
        # Step B: Strategy reasoning with 2.5 Flash (Free Tier)
        memo = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"""
            You are a Chief Investment Strategist at a top-tier asset management firm like BlackRock. 
            Based on the following parsed macroeconomic data, write a short, highly professional 
            investment memo (3 paragraphs max) advising portfolio managers on asset allocation. 
            Focus strictly on equities vs. fixed income based on the momentum of GDP, Unemployment, and Inflation.
            
            Parsed Macro Data:
            {structured_data.text}
            """
        )
        
        # 5. OUTPUT
        print("\n" + "="*60)
        print(" INSTITUTIONAL MACRO MEMO GENERATED")
        print("="*60 + "\n")
        print(memo.text)
        print("\n" + "="*60)

    except Exception as e:
        print(f"[!] AI Generation failed. Error: {e}")

if __name__ == "__main__":
    main()