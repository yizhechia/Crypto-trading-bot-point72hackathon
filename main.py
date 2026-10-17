import os, sys, time, hmac, hashlib, csv, requests, logging
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://mock-api.roostoo.com"
WATCHLIST = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

REGIME_INTERVAL = "15m"
REGIME_LIMIT = 210
GLOBAL_TREND_PERIOD = 200

ENTRY_INTERVAL = "1m"
ENTRY_LIMIT = 100
LOCAL_TREND_PERIOD = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0

ATR_STOP_MULTIPLIER = 2.0
ATR_TP_MULTIPLIER = 4.0
RISK_PER_TRADE_PCT = 0.10
VOLUME_MA_PERIOD = 20
RSI_ENTRY_LOW = 50
RSI_ENTRY_HIGH = 62
COOLDOWN_SECONDS = 300

logger = logging.getLogger("QuantBot")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler("bot_activity.log")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
logger.addHandler(fh)
logger.addHandler(ch)

TRADE_LOG_FILE = "trade_log.csv"

def init_trade_log():
    if not os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(["timestamp", "symbol", "side", "price", "qty", "pnl_usd", "reason"])

def log_trade(symbol, side, price, qty, pnl=0.0, reason=""):
    with open(TRADE_LOG_FILE, 'a', newline='') as f:
        csv.writer(f).writerow([datetime.utcnow().isoformat(), symbol, side, f"{price:.4f}", f"{qty:.6f}", f"{pnl:.4f}", reason])

class ExchangeAPI:
    def __init__(self):
        self.api_key = os.getenv("ROOSTOO_API_KEY")
        self.secret_key = os.getenv("ROOSTOO_SECRET_KEY")
        if not self.api_key or not self.secret_key:
            sys.exit(1)

    def sign(self, payload):
        payload['timestamp'] = str(int(time.time() * 1000))
        query_string = "&".join([f"{k}={payload[k]}" for k in sorted(payload.keys())])
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        headers = {
            'RST-API-KEY': self.api_key,
            'MSG-SIGNATURE': signature,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        return headers, query_string

    def request(self, method, endpoint, payload=None):
        payload = payload or {}
        req_payload = payload.copy()
        url = f"{BASE_URL}{endpoint}"
        headers, query_string = self.sign(req_payload)

        try:
            res = requests.request(
                method, url, headers=headers,
                data=query_string if method == 'POST' else None,
                params=req_payload if method == 'GET' else None,
                timeout=10
            )
            if res.status_code == 200:
                return res.json()
            return None
        except Exception:
            return None

def get_klines(symbol, interval="1m", limit=100):
    url = "https://api.binance.com/api/v3/klines"
    try:
        res = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=5)
        res.raise_for_status()
        df = pd.DataFrame(res.json(), columns=['ts','o','h','l','c','v','ct','qv','nt','tb','tq','i'])
        for col in ['o', 'h', 'l', 'c', 'v']:
            df[col] = df[col].astype(float)
        return df
    except:
        return None

def apply_indicators(df):
    df['ema_global'] = df['c'].ewm(span=GLOBAL_TREND_PERIOD, adjust=False).mean()
    df['ema_local']  = df['c'].ewm(span=LOCAL_TREND_PERIOD, adjust=False).mean()

    df['macd'] = df['c'].ewm(span=12, adjust=False).mean() - df['c'].ewm(span=26, adjust=False).mean()
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['signal']

    delta = df['c'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, float('nan'))
    df['rsi'] = 100 - (100 / (1 + rs))

    high_low = df['h'] - df['l']
    high_close = (df['h'] - df['c'].shift()).abs()
    low_close = (df['l'] - df['c'].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = true_range.rolling(ATR_PERIOD).mean()

    df['bb_mid'] = df['c'].rolling(BB_PERIOD).mean()
    bb_std = df['c'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * bb_std
    df['bb_lower'] = df['bb_mid'] - BB_STD * bb_std
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

    df['vol_ma'] = df['v'].rolling(VOLUME_MA_PERIOD).mean()
    return df

class TradingBot:
    def __init__(self):
        self.api = ExchangeAPI()
        self.peaks = {sym: 0.0 for sym in WATCHLIST}
        self.entry_price = {sym: 0.0 for sym in WATCHLIST}
        self.tp_price = {sym: 0.0 for sym in WATCHLIST}
        self.cooldown_until = {sym: 0 for sym in WATCHLIST}
        init_trade_log()

    def check_global_trend(self):
        df = get_klines("BTCUSDT", interval=REGIME_INTERVAL, limit=REGIME_LIMIT)
        if df is None or len(df) < GLOBAL_TREND_PERIOD:
            return False
        df = apply_indicators(df)
        last = df.iloc[-1]
        return last['c'] > last['ema_global']

    def eval_signal(self, symbol, df, has_pos, is_bull):
        if len(df) < BB_PERIOD + 2:
            return "HOLD"

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        price = curr['c']
        atr = curr['atr']

        if pd.isna(atr) or atr == 0:
            return "HOLD"

        if time.time() < self.cooldown_until[symbol]:
            return "HOLD"

        if has_pos:
            if price > self.peaks[symbol]:
                self.peaks[symbol] = price

            stop_price = self.peaks[symbol] - (atr * ATR_STOP_MULTIPLIER)

            if self.tp_price[symbol] > 0 and price >= self.tp_price[symbol]:
                self.set_cooldown(symbol)
                return "SELL_TP"

            if price <= stop_price:
                self.set_cooldown(symbol)
                return "SELL_STOP"

            if curr['macd'] < curr['signal'] and prev['macd'] >= prev['signal']:
                self.set_cooldown(symbol)
                return "SELL_MACD"

        else:
            self.peaks[symbol] = 0.0
            self.entry_price[symbol] = 0.0
            self.tp_price[symbol] = 0.0

            if not is_bull:
                return "HOLD"

            above_ema = price > curr['ema_local']
            macd_cross = (curr['macd'] > curr['signal'] and prev['macd'] <= prev['signal'])
            rsi_ok = RSI_ENTRY_LOW < curr['rsi'] < RSI_ENTRY_HIGH
            vol_ok = (not pd.isna(curr['vol_ma'])) and (curr['v'] > curr['vol_ma'])
            bb_breakout = price > curr['bb_upper']

            if above_ema and macd_cross and rsi_ok and vol_ok and bb_breakout:
                return "BUY"

        return "HOLD"

    def set_cooldown(self, symbol):
        self.cooldown_until[symbol] = time.time() + COOLDOWN_SECONDS

    def buy(self, symbol, price, atr, usd_free):
        pair = f"{symbol.replace('USDT', '')}/USD"
        invest = usd_free * RISK_PER_TRADE_PCT
        qty = round(invest / price, 4)

        if qty <= 0: return 0.0

        res = self.api.request('POST', '/v3/place_order', {
            'pair': pair, 'side': 'BUY', 'type': 'MARKET', 'quantity': str(qty)
        })

        if res and res.get("Success"):
            self.entry_price[symbol] = price
            self.tp_price[symbol] = price + (atr * ATR_TP_MULTIPLIER)
            self.peaks[symbol] = price
            log_trade(symbol, "BUY", price, qty, reason="entry")
            return invest
        return 0.0

    def sell(self, symbol, price, qty, reason):
        pair = f"{symbol.replace('USDT', '')}/USD"
        res = self.api.request('POST', '/v3/place_order', {
            'pair': pair, 'side': 'SELL', 'type': 'MARKET', 'quantity': str(qty)
        })

        if res and res.get("Success"):
            entry = self.entry_price.get(symbol, 0.0)
            pnl = (price - entry) * qty if entry > 0 else 0.0
            log_trade(symbol, "SELL", price, qty, pnl=pnl, reason=reason)

            self.entry_price[symbol] = 0.0
            self.tp_price[symbol] = 0.0
            self.peaks[symbol] = 0.0

    def run(self):
        while True:
            try:
                bal = self.api.request('GET', '/v3/balance')
                wallet = bal.get("SpotWallet", {}) if bal else {}
                usd_free = float(wallet.get("USD", {}).get("Free", 0))

                bull_regime = self.check_global_trend()

                for sym in WATCHLIST:
                    df = get_klines(sym, interval=ENTRY_INTERVAL, limit=ENTRY_LIMIT)
                    if df is None or len(df) < 30:
                        time.sleep(1)
                        continue

                    df = apply_indicators(df)
                    asset_code = sym.replace('USDT', '')
                    owned_qty = float(wallet.get(asset_code, {}).get("Free", 0))
                    curr_price = df.iloc[-1]['c']
                    has_pos = owned_qty * curr_price > 10.0

                    signal = self.eval_signal(sym, df, has_pos, bull_regime)

                    if signal == "BUY" and usd_free > 20:
                        spent = self.buy(sym, curr_price, df.iloc[-1]['atr'], usd_free)
                        usd_free -= spent

                    elif signal.startswith("SELL") and has_pos:
                        self.sell(sym, curr_price, owned_qty, reason=signal)

                    time.sleep(1)

            except Exception as e:
                logger.error(f"Error: {e}")

            time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        api = ExchangeAPI()
        res = api.request('GET', '/v3/balance')
        if res and res.get("Success"):
            for asset, data in res.get("SpotWallet", {}).items():
                total = float(data.get('Free', 0)) + float(data.get('Lock', 0))
                if total > 1e-8:
                    print(f"{asset}: {total}")

    elif len(sys.argv) > 1 and sys.argv[1] == "log":
        if os.path.exists(TRADE_LOG_FILE):
            df = pd.read_csv(TRADE_LOG_FILE)
            if not df.empty:
                sells = df[df['side'] == 'SELL']
                print(f"Total PnL: ${sells['pnl_usd'].astype(float).sum():.4f}")
                print(sells.tail(20).to_string(index=False))

    else:
        bot = TradingBot()
        bot.run()
