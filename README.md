# Crypto-trading-bot-point72hackathon
Strategy Overview
This bot employs a Volatility-Adjusted Momentum Strategy optimized for Calmar and Sortino ratios.

Key Features:
Global Regime Filter: Uses the BTC 200 EMA to identify broad market trends. Halts buying during downtrends to heavily restrict Max Drawdown and protect capital.
Dynamic Position Sizing: Allocates exactly 10% of available USD capital per trade, maximizing capital efficiency and compounding returns.
Volatility-Adjusted Risk: Uses the Average True Range (ATR) to calculate dynamic trailing stop-losses. This prevents premature exits during normal market noise while strictly cutting losses during volatile drops.
