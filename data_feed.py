"""OKX veri cekme yardimcilari."""
import ccxt
import pandas as pd


def build_exchange(market_type: str) -> ccxt.okx:
    """market_type: 'spot' veya 'swap' (USDT-M perpetual futures)"""
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": market_type},
    })


def fetch_ohlcv(exchange: ccxt.okx, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def fetch_last_price(exchange: ccxt.okx, symbol: str) -> float:
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])
