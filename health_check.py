"""
Sistem saglik denetimi -- botun kendi hakkinda soyledigi ile GERCEK piyasa
verisinin tutarli olup olmadigini otomatik kontrol eder.

Bu script, ETHFI/USDT'de elle bulunan bir hatayi (bkz. main.py'deki
intrabar TP/SL duzeltmesi) tam olarak nasil bulduysak onun otomatik
halidir: her kapanan islemin "exit" fiyatinin, o zaman araliginda
GERCEKTEN piyasada gorulup gorulmedigini OKX'in gecmis mum verisiyle
tekrar dogrular. Ayrica hesap durumunda mantiksiz bir sey olup olmadigini
(negatif bakiye, asiri margin, bozuk TP/SL siralamasi) ve GitHub
Actions'in duzenli calisip calismadigini kontrol eder.

Kullanim
--------
    python health_check.py
    python health_check.py --profiles vwap,4h --hours 24

Sorun bulunursa (varsa) Telegram'a bildirim gonderir (TELEGRAM_BOT_TOKEN/
TELEGRAM_CHAT_ID tanimliysa) ve script sifir olmayan bir exit code ile
biter -- GitHub Actions'ta bu, is'in (job) kirmizi/basarisiz gorunmesini
saglar, yani sorunlar Actions sekmesinden de gorulebilir.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from data_feed import build_exchange
from backtest import fetch_historical_ohlcv
from telegram_notify import send_telegram, telegram_enabled

DEFAULT_PROFILES = ["15m", "4h", "1d", "vwap"]


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def check_account_sanity(data: dict, profile: str) -> list:
    """Hesap durumunda olmamasi gereken (mantiksiz) bir sey var mi kontrol eder."""
    issues = []
    balance = data.get("balance", 0)
    starting = data.get("starting_balance", 10000)

    if balance <= 0:
        issues.append(f"[{profile}] Bakiye sifir veya negatif: {balance:.2f} USDT")
    elif balance < starting * 0.3:
        issues.append(f"[{profile}] Bakiye baslangicin %30'unun altina dustu "
                       f"({balance:.2f} / {starting:.2f} USDT) -- ciddi kayip, incelemeye deger")

    open_positions = data.get("open_positions", {})
    total_margin = sum(p.get("margin", 0) for p in open_positions.values())
    if total_margin > balance * 1.02:
        issues.append(f"[{profile}] Kullanilan margin ({total_margin:.2f}) bakiyeyi "
                       f"({balance:.2f}) asiyor -- olmamasi gereken bir durum")

    for sym, p in open_positions.items():
        side, entry, tp, sl = p.get("side"), p.get("entry"), p.get("tp"), p.get("sl")
        if None in (side, entry, tp, sl):
            issues.append(f"[{profile}] {sym}: pozisyonda eksik alan (side/entry/tp/sl)")
            continue
        # SL <= entry (BUY) / SL >= entry (SELL) durumu HATA DEGIL: basabas
        # (breakeven) tetiklenince SL tam giris fiyatina cekilir (esitlik normal).
        ok = (sl <= entry < tp) if side == "BUY" else (tp < entry <= sl)
        if not ok:
            issues.append(f"[{profile}] {sym}: {side} pozisyonda SL/TP siralamasi bozuk "
                           f"(sl={sl} entry={entry} tp={tp})")

    return issues


def check_recent_trades(exchange, data: dict, profile: str, hours: int, max_checks: int) -> list:
    """Son kapanan islemlerin 'exit' fiyatinin GERCEKTEN piyasada gorulup
    gorulmedigini OKX'in gecmis 1dk mum verisiyle dogrular."""
    issues = []
    history = data.get("history", [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    recent = []
    for t in history:
        if t.get("result") not in ("TP", "SL"):
            continue
        try:
            if _parse_iso(t["close_time"]) >= cutoff:
                recent.append(t)
        except (KeyError, ValueError):
            continue
    recent = recent[-max_checks:]

    for t in recent:
        symbol = t["symbol"]
        side = t["side"]
        exit_price = t["exit"]
        try:
            since_ms = int(_parse_iso(t["open_time"]).timestamp() * 1000)
            until_ms = int(_parse_iso(t["close_time"]).timestamp() * 1000) + 60_000
        except (KeyError, ValueError):
            continue

        try:
            df = fetch_historical_ohlcv(exchange, symbol, "1m", since_ms, until_ms)
        except Exception as e:
            issues.append(f"[{profile}] {symbol}: dogrulama icin mum verisi cekilemedi ({e})")
            continue

        if df.empty:
            issues.append(f"[{profile}] {symbol}: dogrulama icin mum verisi bos döndü "
                           f"({t['open_time']} -> {t['close_time']})")
            continue

        tol = abs(exit_price) * 0.001  # %0.1 tolerans (yuvarlama farklari icin)
        if t["result"] == "TP":
            touched = (df["high"] >= exit_price - tol).any() if side == "BUY" else (df["low"] <= exit_price + tol).any()
        else:
            touched = (df["low"] <= exit_price + tol).any() if side == "BUY" else (df["high"] >= exit_price - tol).any()

        if not touched:
            issues.append(f"[{profile}] {symbol}: {t['result']} olarak kapanan islemin exit fiyati "
                           f"({exit_price:.6f}) gercek piyasa verisinde DOGRULANAMADI -- olasi hata! "
                           f"({t['open_time']} -> {t['close_time']})")

    return issues


def check_actions_health(repo: str = "klonnist/Hasanwavebot", max_gap_minutes: int = 45) -> list:
    """GitHub Actions'in duzenli calisip calismadigini kontrol eder."""
    issues = []
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"https://api.github.com/repos/{repo}/actions/workflows/run-bot.yml/runs?per_page=5"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        issues.append(f"GitHub Actions durumu kontrol edilemedi: {e}")
        return issues

    runs = data.get("workflow_runs", [])
    if not runs:
        issues.append("Hic 'Run wave bot scan' calismasi bulunamadi.")
        return issues

    latest = runs[0]
    created = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
    age_min = (datetime.now(timezone.utc) - created).total_seconds() / 60
    if age_min > max_gap_minutes:
        issues.append(f"Son tarama {age_min:.0f} dakika once baslamis (beklenen ~15dk) "
                       f"-- Actions duraklamis olabilir.")

    fails = [r for r in runs if r.get("conclusion") not in (None, "success")]
    if fails:
        issues.append(f"Son {len(runs)} taramadan {len(fails)} tanesi basarisiz oldu.")

    return issues


def main():
    parser = argparse.ArgumentParser(description="Botun hesap durumunu ve son islemlerini gercek piyasa verisiyle dogrular")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--hours", type=int, default=48, help="Islem dogrulamasi icin geriye bakilacak sure")
    parser.add_argument("--max-checks-per-profile", type=int, default=10)
    parser.add_argument("--market", default="swap", choices=["swap", "spot"])
    parser.add_argument("--repo", default="klonnist/Hasanwavebot")
    args = parser.parse_args()

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    exchange = build_exchange(args.market)

    all_issues = []
    for profile in profiles:
        path = f"{args.data_dir}/{profile}/account_state.json"
        if not os.path.exists(path):
            all_issues.append(f"[{profile}] account_state.json bulunamadi.")
            continue
        with open(path) as f:
            data = json.load(f)

        all_issues += check_account_sanity(data, profile)
        try:
            all_issues += check_recent_trades(exchange, data, profile, args.hours, args.max_checks_per_profile)
        except Exception as e:
            all_issues.append(f"[{profile}] islem dogrulamasi sirasinda beklenmeyen hata: {e}")

    all_issues += check_actions_health(args.repo)

    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"SISTEM SAGLIK RAPORU | {now.isoformat(timespec='seconds')}")
    print("=" * 70)
    if not all_issues:
        print("Her sey yolunda -- hicbir sorun bulunamadi.")
    else:
        for i, issue in enumerate(all_issues, 1):
            print(f"{i}. {issue}")
    print("=" * 70)

    if telegram_enabled():
        if not all_issues:
            send_telegram(
                f"✅ <b>Sistem Sağlık Kontrolü</b>\n\n"
                f"Her şey yolunda — {len(profiles)} profil, son {args.hours} saat kontrol edildi.\n"
                f"{now.strftime('%d.%m.%Y %H:%M UTC')}"
            )
        else:
            lines = "\n".join(f"• {x}" for x in all_issues[:12])
            more = f"\n… ve {len(all_issues) - 12} sorun daha" if len(all_issues) > 12 else ""
            send_telegram(
                f"⚠️ <b>Sistem Sağlık Kontrolü — {len(all_issues)} sorun bulundu</b>\n\n"
                f"{lines}{more}\n\n{now.strftime('%d.%m.%Y %H:%M UTC')}"
            )

    os.makedirs(f"{args.data_dir}/health", exist_ok=True)
    with open(f"{args.data_dir}/health/latest.json", "w") as f:
        json.dump({
            "checked_at": now.isoformat(timespec="seconds"),
            "ok": not all_issues,
            "issues": all_issues,
            "profiles_checked": profiles,
        }, f, indent=2)

    if all_issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
