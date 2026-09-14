"""Telegram Bot API'ye basit bildirim gonderici.

Kullanmak icin iki ortam degiskeni gerekli:
    TELEGRAM_BOT_TOKEN -- @BotFather'dan alinan bot token
    TELEGRAM_CHAT_ID   -- bildirimlerin gonderilecegi sohbet/kullanici id'si.
                          Birden fazla kisiye (her biri botla kendi ozel
                          sohbetinde) gondermek icin VIRGULLE AYRILMIS liste
                          verilebilir, orn: "111111111,222222222"

Ikisi de tanimli degilse fonksiyonlar sessizce hicbir sey yapmaz -- bu
ozellik olmadan da bot normal calismaya devam eder. Bir alicidaki gonderim
hatasi (agdaki gecici bir sorun, o kisi botu hic baslatmamis vb.) sessizce
loglanir ve digerlerine gonderime engel olmaz; ASLA ana tarama dongusunu
durdurmaz.
"""
import json
import os
import urllib.error
import urllib.request

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _chat_ids() -> list:
    raw = os.environ.get("TELEGRAM_CHAT_ID", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def telegram_enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(_chat_ids())


def send_telegram(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = _chat_ids()
    if not token or not chat_ids:
        return

    url = TELEGRAM_API.format(token=token)
    for chat_id in chat_ids:
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"[telegram] {chat_id} icin bildirim gonderilemedi: {e}")
