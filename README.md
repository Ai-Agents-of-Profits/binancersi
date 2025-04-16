# RSI Divergence Strategy Bot

A fully automated trading bot for Bybit, implementing a robust RSI Divergence strategy with dynamic trailing stops and strict risk management. This bot is designed to closely mirror your backtest logic and parameters for live trading.

## Features
- **RSI Divergence Detection**: Enters trades on bullish/bearish RSI divergences.
- **Trailing Stop Loss**: Dynamically follows price in your favor, locking in profits and minimizing drawdown.
- **Take Profit Target**: Exits trades at a fixed profit percentage.
- **Persistent State Management**: All trade and trailing stop state is saved and restored across bot restarts.
- **Bybit Integration**: Trade live or on testnet using your API keys.
- **Proxy Support**: Optional Fixie proxy for network routing (set via `USE_PROXY` in `.env`).

## Requirements
- Python 3.8+
- Bybit account and API keys
- See `requirements.txt` for full dependencies

## Installation
```bash
pip install -r requirements.txt
```

## Setup
1. **API Keys**: Create a `.env` file in this folder:
   ```
   BYBIT_API_KEY=your_api_key
   BYBIT_API_SECRET=your_api_secret
   # Optional proxy:
   USE_PROXY=false
   ```
2. **Edit Parameters**: Adjust strategy parameters at the top of `rsi_divergence_bot.py` if needed.

## Usage
```bash
python rsi_divergence_bot.py
```
- The bot will print status, trades, and errors to the console and log file.
- To stop the bot, press `Ctrl+C`.

## Strategy Parameters (default)
- **RSI Length**: 6
- **ATR Length**: 6
- **ATR Multiplier**: 1.1
- **Profit Target**: 1%
- **Stop Loss**: 0.8%
- **Swing Window**: 4
- **Order Size**: $25 USD

## Notes
- **Proxy**: To use the Fixie proxy, set `USE_PROXY=true` in your `.env` file.
- **Testnet**: Set `USE_TESTNET = True` in the script to use Bybit testnet.
- **State**: State is saved in a JSON file for persistent trade management.
- **Backtesting**: Use the provided backtest script to optimize or validate your parameters.

## Disclaimer
This bot is for educational and research purposes only. Trade at your own risk. Always test thoroughly before deploying with real funds.
