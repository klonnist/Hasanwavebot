"""OKX USDT-M perpetual (swap) gecmis mum verisini indirir -> research/data/<SYM>_<bar>.csv.gz

Backtest arastirmasi icin: public /api/v5/market/history-candles, 100'er mum geriye sayfalar.
"""
import csv, gzip, json, os, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

COINS = os.environ.get("COINS", "BTC,ETH,SOL,XRP,BNB,DOGE,ADA,AVAX,LINK,TON,ETHFI,NEAR").split(",")
BARS = {"15m": os.environ.get("FROM_15M", "2024-01-01"), "4H": os.environ.get("FROM_4H", "2021-01-01")}
OUT = os.path.join(os.path.dirname(__file__), "data")
URL = "https://www.okx.com/api/v5/market/history-candles"


def ts(d):
    return int(time.mktime(time.strptime(d, "%Y-%m-%d")) * 1000)


def get(params):
    for attempt in range(6):
        try:
            req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "research"})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            if j.get("code") == "0":
                return j["data"]
            time.sleep(1 + attempt)
        except Exception as e:
            print("retry", params, e, file=sys.stderr)
            time.sleep(1 + attempt * 2)
    raise RuntimeError(f"failed {params}")


def fetch(coin, bar, start):
    inst = f"{coin}-USDT-SWAP"
    stop = ts(start)
    rows, after = {}, None
    while True:
        p = {"instId": inst, "bar": bar, "limit": 100}
        if after:
            p["after"] = after
        data = get(p)
        if not data:
            break
        for c in data:
            rows[int(c[0])] = c
        oldest = min(int(c[0]) for c in data)
        if oldest <= stop:
            break
        after = oldest
        time.sleep(0.12)
    keys = sorted(k for k in rows if k >= stop)
    path = os.path.join(OUT, f"{coin}_{bar.lower()}.csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "vol", "confirm"])
        for k in keys:
            c = rows[k]
            w.writerow([k, c[1], c[2], c[3], c[4], c[5], c[8] if len(c) > 8 else "1"])
    print(f"{inst} {bar}: {len(keys)} mum", flush=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    jobs = [(c, b, s) for c in COINS for b, s in BARS.items()]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda j: fetch(*j), jobs))
