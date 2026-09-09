"""
Gecmis veri uzerinde strateji testi (backtest) -- "bu tarih araliginda
bu strateji ne kazandirirdi?" sorusuna cevap verir.

Ayni tespit mantigini (wave_detector / vwap_detector), ayni sanal hesabi
(paper_account.PaperAccount -- margin/portfoy risk kontrolleri, funding
HARIC, kademeli kar alma/trailing stop DAHIL) ve ayni ogrenen ajani
(learner.Learner) kullanir; main.py'nin canli tarama dongusu yerine
gecmis mumlari sirayla "oynatir".

Kullanim
--------
    python backtest.py --symbols BTC/USDT,ETH/USDT --strategy wave \
        --timeframe 4h --from 2026-01-01 --to 2026-09-01

    python backtest.py --strategy vwap --timeframe 15m \
        --from 2026-06-01 --to 2026-09-01 --out-dir ./backtest_out

ONEMLI SINIRLAMALAR
--------------------
- Canli bot gibi PERIYODIK (mum kapanisina gore) karar verir: bir mum
  icinde fiyatin TP/SL seviyesine degip geri donmesi (intrabar iğne/wick)
  yakalanmaz -- sadece kapanis fiyatlari kullanilir. Bu, canli botun
  zaten 15 dakikada bir kontrol etme davranisina yakindir, ama gercek
  sonuclar (ozellikle daha genis bantlarda) biraz farkli olabilir.
- Funding ucreti SIMULE EDILMEZ (gecmis funding oran verisi cekmek ayri
  bir maliyet/karmasiklik gerektirir) -- sadece fiyat bazli kar/zarar.
- Ayni anda calisan tek bir "ajan" gibi davranir: tum semboller sirayla,
  kendi tarihsel mum dizileri uzerinden, ayni ORTAK sanal hesaba islem
  acar (canli moddaki gibi).
"""
import argparse
import time
from datetime import datetime, timezone

import pandas as pd

from data_feed import build_exchange
from wave_detector import zigzag_pivots, detect_wave3_setup, build_signal_levels, atr_pct, WaveParams
from vwap_detector import detect_vwap_signal, VwapParams
from paper_account import PaperAccount
from learner import Learner
from main import POPULAR_COINS, normalize_symbol


def fetch_historical_ohlcv(exchange, symbol, timeframe, since_ms, until_ms, page_limit=300):
    """OKX'ten sayfalayarak (pagination) gecmis mum verisini ceker."""
    all_rows = []
    cursor = since_ms
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=page_limit)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:
            break
        cursor = last_ts + 1
        if len(batch) < page_limit:
            break
        time.sleep(exchange.rateLimit / 1000)

    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    until_ts = pd.Timestamp(until_ms, unit="ms", tz="UTC")
    df = df[df["timestamp"] <= until_ts].reset_index(drop=True)
    return df


def replay_symbol(exchange, symbol, timeframe, strategy, since_ms, until_ms, window,
                   learner: Learner, account: PaperAccount, log_every: int = 500):
    df_full = fetch_historical_ohlcv(exchange, symbol, timeframe, since_ms, until_ms)
    if len(df_full) < window + 5:
        print(f"  {symbol}: yeterli gecmis veri yok ({len(df_full)} mum) -- atlaniyor.")
        return 0

    bars_processed = 0
    for i in range(window, len(df_full)):
        window_df = df_full.iloc[i - window:i + 1].reset_index(drop=True)
        bar_close = float(window_df["close"].iloc[-1])

        if account.has_open_position(symbol):
            result = account.check_and_close(symbol, bar_close)
            if result and result["result"] in ("TP", "SL"):
                win = result["pnl"] >= 0
                learner.update(symbol, tuple(result["param_key"]), reward=result["r_multiple"], win=win)
        else:
            params = learner.select(symbol)
            direction = entry = tp = sl = None

            if strategy == "wave":
                vol_pct = atr_pct(window_df)
                effective_dev = max(params.deviation_pct * vol_pct, 0.05)
                pivots = zigzag_pivots(window_df, deviation_pct=effective_dev)
                setup = detect_wave3_setup(pivots, params)
                if setup is not None:
                    entry, tp, sl = build_signal_levels(setup, params, bar_close)
                    direction = setup["direction"]
            else:
                sig = detect_vwap_signal(window_df, params)
                if sig is not None:
                    direction, entry, tp, sl = sig["direction"], sig["entry"], sig["tp"], sig["sl"]

            if direction is not None:
                valid = (sl < entry < tp) if direction == "BUY" else (tp < entry < sl)
                if valid:
                    account.open_trade(symbol=symbol, side=direction, entry=entry, tp=tp, sl=sl,
                                        param_key=params.key())

        bars_processed += 1
        if log_every and bars_processed % log_every == 0:
            ts = window_df["timestamp"].iloc[-1]
            print(f"  {symbol}: {bars_processed}/{len(df_full) - window} mum islendi ({ts})")

    return bars_processed


def compute_max_drawdown(history, starting_balance) -> float:
    if not history:
        return 0.0
    ordered = sorted(history, key=lambda t: t["close_time"])
    peak = starting_balance
    max_dd = 0.0
    for t in ordered:
        bal = t.get("balance_after", peak)
        peak = max(peak, bal)
        if peak > 0:
            max_dd = max(max_dd, (peak - bal) / peak)
    return max_dd * 100


def print_summary(account: PaperAccount, learner: Learner, symbols, strategy, since_ms, until_ms):
    s = account.stats()
    dd = compute_max_drawdown(account.history, account.starting_balance)
    since_str = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).date()
    until_str = datetime.fromtimestamp(until_ms / 1000, tz=timezone.utc).date()

    print("\n" + "=" * 72)
    print(f"BACKTEST SONUCU | strateji={strategy} | {since_str} -> {until_str} | {len(symbols)} coin")
    print("=" * 72)
    print(f"Baslangic bakiye : {account.starting_balance:.2f} USDT")
    print(f"Bitis bakiye     : {s['balance']:.2f} USDT")
    print(f"Toplam K/Z       : {s['total_pnl']:+.2f} USDT ({100 * s['total_pnl'] / account.starting_balance:+.1f}%)")
    print(f"Toplam islem     : {s['trades']}  |  Kazanma orani: %{s['win_rate']:.1f}")
    print(f"Maksimum drawdown: %{dd:.1f}")
    print(f"Kapanista acik kalan pozisyon: {s['open_positions']}")
    print("-" * 72)
    print("Ogrenilen en iyi parametre kombinasyonlari:")
    print(learner.leaderboard(top_n=8))
    print("=" * 72 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Elliott Wave / VWAP botunu gecmis veride test eder (backtest)")
    parser.add_argument("--symbols", default=",".join(POPULAR_COINS))
    parser.add_argument("--strategy", default="wave", choices=["wave", "vwap"])
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--market", default="swap", choices=["swap", "spot"])
    parser.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD (UTC), varsayilan: bugun")
    parser.add_argument("--window", type=int, default=150, help="Her karar aninda kullanilacak mum penceresi")
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--trade-margin", type=float, default=500.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--max-open", type=int, default=5)
    parser.add_argument("--max-portfolio-risk-pct", type=float, default=8.0)
    parser.add_argument("--breakeven-r", type=float, default=1.0)
    parser.add_argument("--partial-tp-r", type=float, default=1.5)
    parser.add_argument("--partial-tp-fraction", type=float, default=0.5)
    parser.add_argument("--trail-giveback-pct", type=float, default=0.5)
    parser.add_argument("--epsilon", type=float, default=0.25)
    parser.add_argument("--out-dir", default=None, help="Verilirse sonuc account/learner state buraya JSON olarak kaydedilir")
    args = parser.parse_args()

    since_ms = int(pd.Timestamp(args.date_from, tz="UTC").timestamp() * 1000)
    until_ms = int((pd.Timestamp(args.date_to, tz="UTC") if args.date_to else pd.Timestamp.now(tz="UTC")).timestamp() * 1000)
    if since_ms >= until_ms:
        raise SystemExit("--from, --to'dan once olmali.")

    symbols = [normalize_symbol(s.strip(), args.market) for s in args.symbols.split(",") if s.strip()]
    exchange = build_exchange(args.market)

    state_path = f"{args.out_dir}/account_state.json" if args.out_dir else "/tmp/_backtest_account_state.json"
    learner_path = f"{args.out_dir}/learner_state.json" if args.out_dir else "/tmp/_backtest_learner_state.json"
    account = PaperAccount(
        state_path=state_path, starting_balance=args.balance, trade_margin=args.trade_margin,
        max_open_positions=args.max_open, leverage=args.leverage,
        max_portfolio_risk_pct=args.max_portfolio_risk_pct,
        breakeven_r=args.breakeven_r, partial_tp_r=args.partial_tp_r,
        partial_tp_fraction=args.partial_tp_fraction, trail_giveback_pct=args.trail_giveback_pct,
    )
    learner = Learner(state_path=learner_path, epsilon=args.epsilon, strategy=args.strategy)

    print(f"Backtest basliyor | strateji={args.strategy} | {len(symbols)} coin | "
          f"timeframe={args.timeframe} | pencere={args.window} mum")
    for symbol in symbols:
        try:
            replay_symbol(exchange, symbol, args.timeframe, args.strategy, since_ms, until_ms,
                          args.window, learner, account)
        except Exception as e:
            print(f"  {symbol}: hata, atlaniyor -- {e}")

    print_summary(account, learner, symbols, args.strategy, since_ms, until_ms)

    if args.out_dir:
        print(f"Sonuc dosyalari kaydedildi: {args.out_dir}/")
    else:
        import os
        for p in (state_path, learner_path, learner_path.replace(".json", "_by_symbol.json")):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    main()
