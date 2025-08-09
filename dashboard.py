import streamlit as st
import pandas as pd
import json
from datetime import datetime

# Set the page configuration to use a wide layout for better data display
st.set_page_config(layout="wide")

# Main title of the dashboard
st.title("LLM Trading Signal Performance Dashboard")

def load_trade_history(filepath="trading_data/trade_history.json"):
    """
    Loads trade history from a specified JSON file.
    Handles potential errors like the file not being found or being empty/corrupted.
    """
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"Error: The file was not found at {filepath}. Please run the trade generation script.")
        return []
    except json.JSONDecodeError:
        st.error(f"Error: Could not decode the JSON file. It might be empty or malformed.")
        return []

def process_trades(history):
    """
    Processes the raw trade history from the JSON file into a structured log of closed trades.
    It pairs OPEN (BUY/SELL) actions with their corresponding CLOSE actions.
    """
    trades = []
    open_trades = {} # A dictionary to hold trades that have been opened but not yet closed

    for record in history:
        action = record.get("action", "").upper()
        
        # If the record is an opening trade, store it
        if action in ["BUY", "SELL"]:
            direction = "LONG" if action == "BUY" else "SHORT"
            open_trades[direction] = record
        
        # If the record is a closing trade, find its opening counterpart and process it
        elif action in ["CLOSE_LONG", "CLOSE_SHORT"]:
            direction = "LONG" if action == "CLOSE_LONG" else "SHORT"
            if direction in open_trades:
                entry_record = open_trades.pop(direction)
                
                # Extract and convert data types
                entry_time = datetime.fromisoformat(entry_record['timestamp'])
                exit_time = datetime.fromisoformat(record['timestamp'])
                
                entry_price = entry_record['price']
                exit_price = record['price']
                position_size = entry_record['position_size']

                # Calculate dollar P&L based on trade direction
                if direction == "LONG":
                    pnl_dollars = (exit_price - entry_price) * position_size
                else:  # SHORT
                    pnl_dollars = (entry_price - exit_price) * position_size

                # Append the processed trade data to our list
                trades.append({
                    "Entry Time": entry_time,
                    "Exit Time": exit_time,
                    "Direction": direction,
                    "Entry Price": entry_price,
                    "Exit Price": exit_price,
                    "Position Size": position_size,
                    "P&L ($)": pnl_dollars,
                    "Holding Period": exit_time - entry_time,
                })
    return trades

# --- Main Application Logic ---
trade_history = load_trade_history()

if not trade_history:
    st.warning("No trade history found or there was an error loading the file. Please ensure 'trading_data/trade_history.json' exists and is valid.")
else:
    processed_trades = process_trades(trade_history)
    
    if not processed_trades:
        st.info("No closed trades to analyze yet. The history file may only contain open positions.")
    else:
        df = pd.DataFrame(processed_trades)

        # --- Key Performance Indicators (KPIs) ---
        st.header("Key Performance Metrics")
        
        total_trades = len(df)
        winning_trades = df[df['P&L ($)'] > 0]
        losing_trades = df[df['P&L ($)'] < 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
        avg_holding_period = df['Holding Period'].mean()

        gross_profit_dollars = winning_trades['P&L ($)'].sum()
        gross_loss_dollars = losing_trades['P&L ($)'].sum()
        net_pnl_dollars = df['P&L ($)'].sum()

        # Display KPIs in columns
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Trades", total_trades)
        col2.metric("Win Rate", f"{win_rate:.2f}%")
        col3.metric("Avg. Holding Period", str(avg_holding_period).split('.')[0])

        st.markdown("---")
        
        col4, col5, col6 = st.columns(3)
        col4.metric("Net P&L ($)", f"${net_pnl_dollars:,.2f}")
        col5.metric("Gross Profit ($)", f"${gross_profit_dollars:,.2f}")
        col6.metric("Gross Loss ($)", f"${gross_loss_dollars:,.2f}")


        # --- Performance Charts ---
        st.header("Performance Charts")
        
        # Sort trades by exit time for cumulative calculations
        df_sorted = df.sort_values(by="Exit Time").reset_index(drop=True)
        
        # Cumulative P&L in Dollars
        df_sorted['Cumulative P&L ($)'] = df_sorted['P&L ($)'].cumsum()
        st.subheader("Cumulative P&L ($) Over Time")
        st.line_chart(df_sorted, x="Exit Time", y="Cumulative P&L ($)")
        
        # P&L per Trade in Dollars
        st.subheader("P&L ($) per Trade")
        st.bar_chart(df_sorted['P&L ($)'])

        # --- Detailed Trade Log ---
        st.header("Detailed Trade Log")
        # Display the sorted dataframe, excluding the P&L (%) column
        st.dataframe(df_sorted[['Entry Time', 'Exit Time', 'Direction', 'Position Size', 'Entry Price', 'Exit Price', 'P&L ($)', 'Holding Period']])
