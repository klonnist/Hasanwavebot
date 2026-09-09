"""
Elliott Wave Dalga-3 Ogrenen Ajan (OKX) -- SANAL (paper trading), COKLU-COIN
==============================================================================
Bu ajan GERCEK PARA KULLANMAZ. OKX'ten canli veri okur, populer coin
listesindeki HER coin icin ayri ayri Elliott Wave Dalga 1-2-3 kurulumu
arar, ORTAK bir sanal hesap uzerinden (varsayilan 10.000 USDT) BUY/SELL
pozisyonu "acar", TP/SL'e carpinca kapatir ve sonuca gore hangi parametre
kombinasyonunun daha iyi calistigini ogrenir.

Kurulum
-------
    pip install -r requirements.txt

Kullanim
--------
    # Varsayilan populer coin listesiyle, 10.000 USDT sanal bakiye
    python main.py

    # Kendi coin listeni belirle
    python main.py --symbols BTC/USDT,ETH/USDT,SOL/USDT

    # Tek dongu (test / debug icin)
    python main.py --once

Ogrenilen veriler ve islem gecmisi ./data/ klasorunde JSON olarak saklanir,
boylece ajani durdurup tekrar baslattiginda hafizasi kaybolmaz.

ONEMLI: Bu script hicbir zaman gercek emir gondermez. Sadece simulasyon
yapar ve sinyal/sonuc yazdirir. Yatirim tavsiyesi degildir.
"""
import argparse
import time
from datetime import datetime, timezone

import ccxt

from data_feed import build_exchange, fetch_ohlcv, fetch_last_price
from wave_detector import zigzag_pivots, detect_wave3_setup, build_signal_levels, WaveParams
from paper_account import PaperAccount
from learner import Learner

# Populer coinler (OKX'te USDT paritesi olan, yuksek islem hacimli basliklar).
# Futures/swap modunda calisirken sembollere otomatik olarak ":USDT" eklenir.
POPULAR_COINS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "TON/USDT",
]


def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}")


def normalize_symbol(symbol: str, market: str) -> str:
    """swap modunda OKX perpetual formatina (BASE/QUOTE:QUOTE) cevirir."""
    if market == "swap" and ":" not in symbol:
        quote = symbol.split("/")[-1]
        return f"{symbol}:{quote}"
    return symbol


def try_open_position(exchange, symbol, timeframe, limit, learner: Learner, account: PaperAccount):
    """Bu sembolde acik pozisyon yoksa yeni bir Dalga-3 kurulumu arar ve sanal islem acar."""
    if account.has_open_position(symbol) or not account.can_open_new():
        return

    params: WaveParams = learner.select()

    df = fetch_ohlcv(exchange, symbol, timeframe, limit=limit)
    pivots = zigzag_pivots(df, deviation_pct=params.deviation_pct)
    setup = detect_wave3_setup(pivots, params)

    if setup is None:
        log(f"{symbol} {timeframe} | dev%={params.deviation_pct} tp_x={params.tp_mult} "
            f"-> gecerli kurulum yok.")
        return

    last_close = float(df["close"].iloc[-1])
    entry, tp, sl = build_signal_levels(setup, params, last_close)

    pos = account.open_trade(
        symbol=symbol, side=setup["direction"], entry=entry, tp=tp, sl=sl,
        param_key=params.key(),
    )
    if pos:
        log(f">>> SANAL ISLEM ACILDI: {setup['direction']} {symbol} | entry={entry:.6f} "
            f"tp={tp:.6f} sl={sl:.6f} | param(dev%={params.deviation_pct}, tp_x={params.tp_mult}) "
            f"| dalga2 retrace=%{setup['retrace_pct']:.1f}")


def try_close_position(exchange, symbol, learner: Learner, account: PaperAccount):
    """Bu sembolde acik pozisyon varsa guncel fiyata gore TP/SL kontrolu yapar."""
    if not account.has_open_position(symbol):
        return

    last_price = fetch_last_price(exchange, symbol)
    result = account.check_and_close(symbol, last_price)

    if result is None:
        pos = account.open_positions[symbol]
        log(f"Pozisyon acik: {pos.side} {symbol} | entry={pos.entry:.6f} "
            f"guncel={last_price:.6f} | tp={pos.tp:.6f} sl={pos.sl:.6f}")
        return

    win = result["result"] == "TP"
    learner.update(tuple(result["param_key"]), reward=result["r_multiple"], win=win)

    sonuc_str = "KAZANC (TP)" if win else "KAYIP (SL)"
    log(f"<<< SANAL ISLEM KAPANDI: {sonuc_str} | {result['side']} {result['symbol']} "
        f"| pnl={result['pnl']} USDT | R={result['r_multiple']} | yeni bakiye={result['balance_after']} USDT")


def print_report(account: PaperAccount, learner: Learner, symbols):
    s = account.stats()
    print("-" * 70)
    print(f"SANAL HESAP OZETI  | izlenen_coin={len(symbols)} acik_pozisyon={s['open_positions']} "
          f"islem={s['trades']} kazanma_orani=%{s['win_rate']} toplam_pnl={s['total_pnl']} "
          f"bakiye={s['balance']} USDT")
    if account.open_positions:
        print("Acik pozisyonlar: " + ", ".join(
            f"{sym}({p.side})" for sym, p in account.open_positions.items()))
    print("Ogrenilen en iyi parametre kombinasyonlari:")
    print(learner.leaderboard())
    print("-" * 70)


def run_cycle(exchange, symbols, timeframe, limit, learner, account):
    for symbol in symbols:
        try:
            if account.has_open_position(symbol):
                try_close_position(exchange, symbol, learner, account)
            else:
                try_open_position(exchange, symbol, timeframe, limit, learner, account)
        except ccxt.NetworkError as e:
            log(f"{symbol}: ag hatasi, tekrar denenecek: {e}")
        except ccxt.ExchangeError as e:
            log(f"{symbol}: borsa hatasi: {e}")
        except Exception as e:
            log(f"{symbol}: beklenmeyen hata: {e}")


def main():
    parser = argparse.ArgumentParser(description="Elliott Wave Dalga-3 Ogrenen Ajan (OKX, SANAL/paper trading, coklu-coin)")
    parser.add_argument("--symbols", default=",".join(POPULAR_COINS),
                         help="Virgulle ayrilmis coin listesi, orn: BTC/USDT,ETH/USDT,SOL/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--market", default="swap", choices=["swap", "spot"])
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--interval", type=int, default=60, help="Her tam tarama arasi bekleme (saniye)")
    parser.add_argument("--balance", type=float, default=10000.0, help="Baslangic sanal bakiye (USDT, TUM coinler icin ORTAK)")
    parser.add_argument("--risk-pct", type=float, default=2.0, help="Islem basina riske edilecek bakiye yuzdesi")
    parser.add_argument("--max-open", type=int, default=5, help="Ayni anda acik olabilecek en fazla pozisyon sayisi")
    parser.add_argument("--epsilon", type=float, default=0.25, help="Ogrenen ajanin kesif (explore) orani")
    parser.add_argument("--data-dir", default="./data", help="Ogrenme/islem gecmisi kayit klasoru")
    parser.add_argument("--once", action="store_true", help="Tek tam tarama yap ve cik (debug icin)")
    parser.add_argument("--report-every", type=int, default=10, help="Kac taramada bir ozet rapor yazdirilsin")
    args = parser.parse_args()

    symbols = [normalize_symbol(s.strip(), args.market) for s in args.symbols.split(",") if s.strip()]

    exchange = build_exchange(args.market)
    account = PaperAccount(
        state_path=f"{args.data_dir}/account_state.json",
        starting_balance=args.balance,
        risk_per_trade_pct=args.risk_pct,
        max_open_positions=args.max_open,
    )
    learner = Learner(state_path=f"{args.data_dir}/learner_state.json", epsilon=args.epsilon)

    log(f"Ajan baslatildi | market={args.market} | {len(symbols)} coin izleniyor: {', '.join(symbols)}")
    log(f"Sanal bakiye={account.balance} USDT | risk/islem=%{args.risk_pct} | max_ayni_anda_pozisyon={args.max_open}")

    if args.once:
        run_cycle(exchange, symbols, args.timeframe, args.limit, learner, account)
        print_report(account, learner, symbols)
        return

    cycle = 0
    while True:
        run_cycle(exchange, symbols, args.timeframe, args.limit, learner, account)
        cycle += 1
        if cycle % args.report_every == 0:
            print_report(account, learner, symbols)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
