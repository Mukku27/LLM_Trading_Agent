import streamlit as st
import pandas as pd
import json
from datetime import datetime

st.set_page_config(layout="wide")

st.title("LLM Trading Signal Performance Dashboard")

def load_trade_history(filepath="trading_data/trade_history.json"):
    """Loads trade history from a JSON file."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []

def process_trades(history):
    """Processes raw trade history to create a structured log of closed trades."""
    trades = []
    open_trades = {}

    for record in history:
        action = record.get("action", "").upper()
        
        if action in ["BUY", "SELL"]:
            direction = "LONG" if action == "BUY" else "SHORT"
            open_trades[direction] = record
        
        elif action in ["CLOSE_LONG", "CLOSE_SHORT"]:
            direction = "LONG" if action == "CLOSE_LONG" else "SHORT"
            if direction in open_trades:
                entry_record = open_trades.pop(direction)
                
                entry_time = datetime.fromisoformat(entry_record['timestamp'])
                exit_time = datetime.fromisoformat(record['timestamp'])
                
                trades.append({
                    "Entry Time": entry_time,
                    "Exit Time": exit_time,
                    "Direction": direction,
                    "Entry Price": entry_record['price'],
                    "Exit Price": record['price'],
                    "Position Size": entry_record['position_size'],
                    "P&L (%)": record.get('pnl', 0),
                    "Holding Period": exit_time - entry_time,
                })
    return trades

trade_history = load_trade_history()

if not trade_history:
    st.warning("No trade history found. Please run the trading bot to generate data.")
else:
    processed_trades = process_trades(trade_history)
    
    if not processed_trades:
        st.info("No closed trades to analyze yet.")
    else:
        df = pd.DataFrame(processed_trades)

        # --- Key Performance Indicators ---
        st.header("Key Performance Metrics")
        
        total_trades = len(df)
        winning_trades = df[df['P&L (%)'] > 0]
        losing_trades = df[df['P&L (%)'] < 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = df['P&L (%)'].sum()
        avg_pnl = df['P&L (%)'].mean()
        avg_holding_period = df['Holding Period'].mean()

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Trades", total_trades)
        col2.metric("Win Rate", f"{win_rate:.2f}%")
        col3.metric("Total P&L", f"{total_pnl:.2f}%")
        col4.metric("Avg. Holding Period", str(avg_holding_period).split('.')[0])

        # --- Charts ---
        st.header("Performance Charts")
        
        # Cumulative P&L
        df_sorted = df.sort_values(by="Exit Time").reset_index(drop=True)
        df_sorted['Cumulative P&L'] = df_sorted['P&L (%)'].cumsum()
        
        st.subheader("Cumulative P&L Over Time")
        st.line_chart(df_sorted, x="Exit Time", y="Cumulative P&L")
        
        # P&L per Trade
        st.subheader("P&L per Trade")
        st.bar_chart(df_sorted['P&L (%)'])

        # --- Trade Log ---
        st.header("Detailed Trade Log")
        st.dataframe(df_sorted[['Entry Time', 'Exit Time', 'Direction', 'Entry Price', 'Exit Price', 'P&L (%)', 'Holding Period']])

