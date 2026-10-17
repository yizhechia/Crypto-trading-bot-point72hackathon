#  Trading Bot

A Python momentum trading bot that reads Binance candles and places market orders through Roostoo's mock exchange API. It watches BTC, ETH, BNB and SOL, using Bitcoin's trend as a filter for new entries.

## Setup

Save the Python script as `bot.py` in the same folder as this README. The commands below assume that filename.

Install the dependencies:

```bash
python -m pip install requests pandas python-dotenv
```

Create a `.env` file in the project folder:

```dotenv
ROOSTOO_API_KEY=your_api_key
ROOSTOO_SECRET_KEY=your_secret_key
```

Use credentials for the Roostoo mock exchange and keep `.env` out of version control. The script exits if either variable is missing.

The bot needs access to:

- `https://api.binance.com/api/v3/klines` for market data
- `https://mock-api.roostoo.com` for balances and orders

## Run

Start the trading loop:

```bash
python bot.py
```

Press Ctrl+C to stop it.

Print nonzero wallet balances, including free and locked amounts:

```bash
python bot.py check
```

Show estimated total P&L from recorded sells and the latest 20 sell records:

```bash
python bot.py log
```

The bot is usually quiet while running. Successful order responses are recorded in `trade_log.csv`. Errors caught by the main loop go to the console and `bot_activity.log`.

## Strategy

### Entries

New buys require Bitcoin's latest price to be above its 200-period EMA on 15-minute candles. If that data cannot be fetched, the bot skips new buys.

For each asset without a position worth more than $10, all five conditions must also hold on 1-minute candles:

1. Price is above the 20-period EMA.
2. MACD crosses above its signal line, using periods 12, 26 and 9.
3. The 14-period RSI is strictly between 50 and 62.
4. Volume is above its 20-period moving average.
5. Price is above the upper Bollinger Band, calculated over 20 periods with a standard deviation multiplier of 2.

The Bollinger condition is a breakout check. Band width is calculated, but it is not used to require a preceding squeeze.

### Position sizing

Each buy targets 10% of the free USD balance available at that point in the loop. Quantity is rounded to four decimal places, and the bot only attempts a buy when free USD exceeds $20.

The setting is named `RISK_PER_TRADE_PCT`; it controls cash allocation. Loss exposure also depends on the distance to the exit price.

After an accepted buy, the bot deducts the intended allocation from its cash estimate before evaluating the next asset.

### Exits

For positions worth more than $10, the bot checks these conditions in order:

1. **Take profit:** price reaches the recorded entry price plus four times the ATR recorded at entry.
2. **Trailing stop:** price falls to the highest observed price since entry minus twice the latest ATR.
3. **MACD exit:** MACD crosses below its signal line.

Exits use market orders. A five-minute cooldown begins when a sell signal is generated, before the order response is checked. During that cooldown, signal evaluation for the asset returns `HOLD`.

## Default settings

Settings are constants near the top of `bot.py`.

| Setting | Default |
| --- | --- |
| Watchlist | BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT |
| Bitcoin trend filter | 200-period EMA on 15-minute candles |
| Entry data | Latest 100 candles at 1-minute intervals |
| Allocation per buy | 10% of remaining free USD |
| Trailing stop distance | 2 times the latest ATR |
| Take-profit distance | 4 times the ATR at entry |
| Cooldown | 300 seconds per asset |
| Loop delay | 60 seconds, plus request time and 1-second pauses between assets |

Binance symbols are mapped to Roostoo pairs such as `BTC/USD` when orders are submitted.

## Logs

The bot creates two files in the directory it runs from:

- `bot_activity.log`: errors caught by the main loop. Some API and data-fetch failures return silently.
- `trade_log.csv`: buy and sell records after a successful API response.

The CSV columns are:

```text
timestamp,symbol,side,price,qty,pnl_usd,reason
```

Trade timestamps use UTC. Sell reasons are `SELL_TP`, `SELL_STOP` and `SELL_MACD`.

Logged prices come from Binance candles. The sell P&L estimate uses those reference prices and the stored entry price, without fees or actual fill prices.

## Limitations

This is a prototype. The supplied script has no backtest or measured performance results.

- Entry prices, take-profit levels, price peaks and cooldowns are held in memory. Restarting loses that state, and existing wallet positions are not reconstructed.
- The latest candle is used without checking whether it has closed. Signals can change while that candle is forming.
- Binance USDT prices drive decisions for orders on Roostoo USD pairs, so signal prices and execution prices may differ.
- Stops and profit targets are checked by the running loop. They are not submitted as standing exchange orders.
- A rejected sell still starts the cooldown, delaying another exit attempt.
- Holdings valued at $10 or less are treated as having no position and are not sold by the exit rules.
