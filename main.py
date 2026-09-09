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

from data_feed import build_exchange, fetch_ohlcv, fetch_last_price, fetch_funding_rate
from wave_detector import zigzag_pivots, detect_wave3_setup, build_signal_levels, atr_pct, WaveParams
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
    "ETHFI/USDT",
    "PENGU/USDT",
    "NEAR/USDT",
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

    params: WaveParams = learner.select(symbol)

    df = fetch_ohlcv(exchange, symbol, timeframe, limit=limit)
    vol_pct = atr_pct(df)
    effective_dev_pct = max(params.deviation_pct * vol_pct, 0.05)
    pivots = zigzag_pivots(df, deviation_pct=effective_dev_pct)
    setup = detect_wave3_setup(pivots, params)

    if setup is None:
        log(f"{symbol} {timeframe} | atr_x={params.deviation_pct} (atr%={vol_pct:.2f} -> "
            f"esik%={effective_dev_pct:.2f}) tp_x={params.tp_mult} -> gecerli kurulum yok.")
        return

    last_close = float(df["close"].iloc[-1])
    entry, tp, sl = build_signal_levels(setup, params, last_close)

    # TP/SL seviyeleri pivot noktasina (p2) gore hesaplaniyor ama entry, o anki
    # (daha guncel) son kapanis fiyati -- fiyat pivot'tan bu yana TP seviyesini
    # zaten gecmis/asmissa entry, TP'nin "yanlis" tarafinda kalabilir (orn. BUY'da
    # tp < entry). Boyle bir kurulumu acarsak, fiyat bir tik bile hareket etmeden
    # "TP'ye carpti" diye kapanir ama gercekte zararla kapanir (entry > tp).
    # Bu tutarsiz TP/zarar etiketlemesini onlemek icin gecersiz kurulumlari eliyoruz.
    valid = (sl < entry < tp) if setup["direction"] == "BUY" else (tp < entry < sl)
    if not valid:
        log(f"{symbol} {timeframe} | gecersiz kurulum (entry TP/SL disinda: "
            f"entry={entry:.6f} tp={tp:.6f} sl={sl:.6f}) -> atlaniyor.")
        return

    pos = account.open_trade(
        symbol=symbol, side=setup["direction"], entry=entry, tp=tp, sl=sl,
        param_key=params.key(),
    )
    if pos:
        log(f">>> SANAL ISLEM ACILDI: {setup['direction']} {symbol} | entry={entry:.6f} "
            f"tp={tp:.6f} sl={sl:.6f} | kaldirac={pos.leverage}x margin={pos.margin:.2f} "
            f"| param(atr_x={params.deviation_pct}, tp_x={params.tp_mult}) "
            f"| dalga2 retrace=%{setup['retrace_pct']:.1f}")


def try_close_position(exchange, symbol, learner: Learner, account: PaperAccount):
    """Bu sembolde acik pozisyon varsa funding'i isler, guncel fiyata gore
    anlik kar/zarari (mark-to-market) gunceller ve TP/SL kontrolu yapar."""
    if not account.has_open_position(symbol):
        return

    try:
        funding_rate = fetch_funding_rate(exchange, symbol)
        account.accrue_funding(symbol, funding_rate)
    except Exception as e:
        log(f"{symbol}: funding orani alinamadi (atlaniyor): {e}")

    last_price = fetch_last_price(exchange, symbol)
    result = account.check_and_close(symbol, last_price)

    if result is None:
        pos = account.open_positions[symbol]
        sign = "+" if pos.unrealized_pnl >= 0 else ""
        log(f"Pozisyon acik: {pos.side} {symbol} | entry={pos.entry:.6f} "
            f"guncel={last_price:.6f} | anlik_kz={sign}{pos.unrealized_pnl:.2f} USDT (R={pos.unrealized_r:.2f}) "
            f"| tp={pos.tp:.6f} sl={pos.sl:.6f}")
        return

    win = result["result"] == "TP"
    learner.update(result["symbol"], tuple(result["param_key"]), reward=result["r_multiple"], win=win)

    sonuc_str = "KAZANC (TP)" if win else "KAYIP (SL)"
    log(f"<<< SANAL ISLEM KAPANDI: {sonuc_str} | {result['side']} {result['symbol']} "
        f"| pnl={result['pnl']} USDT | funding={result['funding_paid']} USDT | R={result['r_multiple']} "
        f"| yeni bakiye={result['balance_after']} USDT")


def print_report(account: PaperAccount, learner: Learner, symbols):
    s = account.stats()
    print("-" * 70)
    print(f"SANAL HESAP OZETI  | izlenen_coin={len(symbols)} acik_pozisyon={s['open_positions']} "
          f"islem={s['trades']} kazanma_orani=%{s['win_rate']} toplam_pnl={s['total_pnl']} "
          f"anlik_kz={s['unrealized_pnl']} funding_maliyeti={s['funding_total']} "
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
    parser.add_argument("--trade-margin", type=float, default=500.0,
                         help="Her islemde kullanilacak SABIT teminat (USDT); pozisyon buyuklugu "
                              "bu tutar x kaldirac olarak hesaplanir (bakiye yuzdesi degil)")
    parser.add_argument("--leverage", type=float, default=1.0, help="Sanal pozisyonlarda kullanilacak kaldirac (orn. 10 = 10x)")
    parser.add_argument("--max-open", type=int, default=5, help="Ayni anda acik olabilecek en fazla pozisyon sayisi")
    parser.add_argument("--max-portfolio-risk-pct", type=float, default=8.0,
                         help="Tum acik pozisyonlarin TOPLAM riskinin bakiyeye orani ust siniri "
                              "(korelasyonlu coinlerin ayni anda vurmasina karsi)")
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
        trade_margin=args.trade_margin,
        max_open_positions=args.max_open,
        leverage=args.leverage,
        max_portfolio_risk_pct=args.max_portfolio_risk_pct,
    )
    learner = Learner(state_path=f"{args.data_dir}/learner_state.json", epsilon=args.epsilon)

    log(f"Ajan baslatildi | market={args.market} | {len(symbols)} coin izleniyor: {', '.join(symbols)}")
    log(f"Sanal bakiye={account.balance} USDT | islem_basina_teminat={args.trade_margin} USDT | kaldirac={args.leverage}x "
        f"| max_ayni_anda_pozisyon={args.max_open} | max_portfoy_riski=%{args.max_portfolio_risk_pct}")

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
