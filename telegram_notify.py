"""Telegram Bot API'ye basit bildirim gonderici.

Kullanmak icin iki ortam degiskeni gerekli:
    TELEGRAM_BOT_TOKEN -- @BotFather'dan alinan bot token
    TELEGRAM_CHAT_ID   -- bildirimlerin gonderilecegi sohbet/kullanici id'si

Ikisi de tanimli degilse fonksiyonlar sessizce hicbir sey yapmaz -- bu
ozellik olmadan da bot normal calismaya devam eder. Gonderim hatasi
(agdaki gecici bir sorun, yanlis token vb.) da sessizce loglanir, ASLA
ana tarama dongusunu durdurmaz.
"""
import json
import os
import urllib.error
import urllib.request

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(os.environ.get("TELEGRAM_CHAT_ID"))


def send_telegram(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    url = TELEGRAM_API.format(token=token)
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[telegram] bildirim gonderilemedi: {e}")
