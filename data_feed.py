"""OKX veri cekme yardimcilari."""
import json
import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

# Evrenden her zaman cikarilan stablecoin tabanli kontratlar.
_STABLE_BASES = {"USDC", "USDG", "FDUSD", "DAI", "TUSD", "USDD", "PYUSD", "USDE", "EURT"}


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


_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def fetch_ohlcv_since(exchange: ccxt.okx, symbol: str, timeframe: str, since_ms: int,
                      max_candles: int = 1500) -> pd.DataFrame:
    """since_ms'ten (dahil) simdiye kadar TUM mumlari sayfa sayfa ceker (en eskiden en yeniye).

    Bot seyrek calistiginda (GitHub zamanlanmis calismalari saatlerce gecikebiliyor) iki calisma
    arasinda olusan fiyat hareketini kacirmamak icin kullanilir."""
    step = _TF_MS[timeframe]
    rows, cursor = [], int(since_ms)
    while len(rows) < max_candles:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=100)
        if not batch:
            break
        rows.extend(batch)
        last_ts = int(batch[-1][0])
        if last_ts + step <= cursor:
            break
        cursor = last_ts + step
        if cursor > time.time() * 1000:
            break
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def fetch_last_price(exchange: ccxt.okx, symbol: str) -> float:
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])


def fetch_funding_rate(exchange: ccxt.okx, symbol: str) -> float:
    """Guncel perpetual funding oranini dondurur (orn. 0.0001 = %0.01).
    Spot piyasada veya funding bilgisi olmayan sembollerde 0.0 doner."""
    info = exchange.fetch_funding_rate(symbol)
    rate = info.get("fundingRate")
    return float(rate) if rate is not None else 0.0


def fetch_top_crypto_symbols(exchange: ccxt.okx, n: int = 50, min_age_days: int = 60) -> list:
    """OKX USDT-M swap'lari arasindan 24 saatlik USDT hacmine gore ilk `n` KRIPTO sembolu dondurur.

    OKX'in swap listesinde hisse/ETF (instCategory 3) ve emtia (4) perp'leri de var
    (NVDA, SOXL, XAU...) -- ilk 50'nin ~%40'i bunlar, o yuzden yalniz instCategory 1
    (kripto) alinir. ccxt'de OKX swap icin `quoteVolume` None geliyor; gercek USDT
    hacmi volCcy24h (baz coin cinsinden 24s hacim) x son fiyattir. Listelenmesinden
    `min_age_days` gun gecmemis coinler elenir (4h icin 300 mumluk gecmis gerekiyor).
    """
    exchange.load_markets()
    tickers = exchange.fetch_tickers()
    now_ms = time.time() * 1000
    rows = []
    for sym, t in tickers.items():
        m = exchange.markets.get(sym)
        if not m or not m.get("swap") or not m.get("linear") or m.get("settle") != "USDT" or not m.get("active"):
            continue
        minfo = m.get("info", {})
        if str(minfo.get("instCategory")) != "1":
            continue
        if (m.get("base") or "").upper() in _STABLE_BASES:
            continue
        try:
            age_days = (now_ms - float(minfo["listTime"])) / 86_400_000
            tinfo = t.get("info", {})
            quote_volume = float(tinfo["volCcy24h"]) * float(tinfo["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if age_days < min_age_days:
            continue
        rows.append((quote_volume, sym))
    rows.sort(reverse=True)
    return [sym for _, sym in rows[:n]]


def load_or_refresh_universe(exchange: ccxt.okx, path: str, n: int = 50, max_age_hours: float = 24.0,
                              min_age_days: int = 60) -> list:
    """Hacme gore ilk `n` kripto evrenini `path` JSON onbellegiyle dondurur.

    Onbellek `max_age_hours`'tan tazeyse ve en az `n` sembol iceriyorsa dosyadan okunur
    (profiller ayni listeyi paylassin, ticker cagrisi tekrarlanmasin); degilse yeniden
    hesaplanip yazilir. Yenileme hata verirse (ag vb.) eski onbellek kullanilir, onbellek
    de yoksa hata yukari firlatilir.
    """
    cached = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError):
            cached = None

    if cached and len(cached.get("symbols", [])) >= n:
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["updated_at"])).total_seconds() / 3600
        except (KeyError, ValueError):
            age_h = float("inf")
        if age_h < max_age_hours:
            return cached["symbols"][:n]

    try:
        symbols = fetch_top_crypto_symbols(exchange, n=n, min_age_days=min_age_days)
        if len(symbols) < n:
            raise RuntimeError(f"evren yetersiz: {len(symbols)} < {n}")
    except Exception:
        if cached and cached.get("symbols"):
            return cached["symbols"][:n]
        raise

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "n": n, "symbols": symbols}, f, indent=2)
    return symbols
