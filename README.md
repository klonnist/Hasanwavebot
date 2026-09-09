# Elliott Wave Dalga-3 Öğrenen Ajan (OKX, Çoklu-Coin)

OKX'ten (varsayılan: **USDT-M Perpetual Futures / swap**) canlı veri okuyan,
**birden fazla popüler coini aynı anda tarayan**, her birinde Elliott Wave
Dalga 1-2-3 kurulumlarını arayan ve bulduğunda **sanal (paper trading) ortak
bir 10.000 USDT'lik hesap üzerinden** BUY/SELL pozisyonu açıp TP/SL'e göre
kapatan bir ajan. Her kapanan işlemden sonra hangi parametre kombinasyonunun
daha iyi sonuç verdiğini öğrenir ve zamanla kazandıran kombinasyonları daha
sık, kaybettirenleri daha az kullanır.

## Varsayılan izlenen coinler

BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, TON, ETHFI, PENGU, NEAR
(`main.py` içinde `POPULAR_COINS` listesi, `--symbols` ile değiştirilebilir).

> Not: XAUT/USDT (Tether Gold) OKX'te sadece spot piyasada işlem görüyor,
> USDT-M perpetual futures (swap) karşılığı yok — bu yüzden listeye
> eklenmedi.

## GitHub Actions ile 7/24 çoklu zaman dilimi taraması

[`.github/workflows/run-bot.yml`](.github/workflows/run-bot.yml) her 15
dakikada bir **üç ayrı zaman dilimini birbirinden bağımsız** tarar; her
biri kendi ayrı sanal 10.000 USDT hesabına ve kendi öğrenen ajanına
sahiptir, böylece sonuçları birbirinden bağımsız karşılaştırabilirsiniz:

| Zaman dilimi | Klasör       | Kaldıraç | Profil            |
|--------------|--------------|----------|--------------------|
| `15m`        | `data/15m/`  | 10x      | Scalp               |
| `4h`         | `data/4h/`   | 5x       | Swing                |
| `1d`         | `data/1d/`   | 3x       | Pozisyon             |

Sonuçlar [klonnist.github.io/Hasanwavebot](https://klonnist.github.io/Hasanwavebot/)
adresindeki panelde sekmeler halinde canlı gösterilir.

**Kaldıraç hakkında:** Kaldıraç yalnızca margin/teminat hesabını
etkiler (`margin = notional / leverage`), pozisyon büyüklüğü ve risk
her zaman `--risk-pct` ile belirlenen sabit yüzdeye göre hesaplanır —
yani kaldıraç, işlem başına riske edilen tutarı değiştirmez, sadece o
pozisyonu açmak için ne kadar teminat "bağlandığını" gösterir.

Ajan her taramada listedeki **her coin için ayrı ayrı** kurulum arar; aynı
anda birden fazla coinde pozisyon açık olabilir (varsayılan sınır: **5**,
`--max-open` ile ayarlanır). Tüm pozisyonlar **tek ortak 10.000 USDT'lik
sanal bakiyeden** risk alır — yani 5 pozisyon aynı anda açıksa, toplam risk
yaklaşık `5 × risk-pct` kadar olur (varsayılan %2 risk ile ~%10).

## ⚠️ Uyarı (Disclaimer)

- **Bu ajan gerçek para kullanmaz, hiçbir gerçek emir göndermez.** Tamamen
  sanal bir hesap üzerinde simülasyon yapar. Gerçek kâr/zarar oluşturmaz.
- Yatırım tavsiyesi değildir. Elliott Wave analizi sübjektiftir, doğru dalga
  sayımını garanti edemez.
- Amacı: bir stratejiyi risksiz şekilde canlı piyasa verisiyle test etmek ve
  hangi parametrelerin işe yaradığını gözlemlemek.

## Mimari

| Dosya               | Görev                                                             |
|----------------------|--------------------------------------------------------------------|
| `data_feed.py`       | OKX'ten `ccxt` ile mum/fiyat verisi çeker                          |
| `wave_detector.py`   | Zigzag pivot tespiti + Elliott Wave Dalga 1-2-3 kurulum tespiti     |
| `paper_account.py`   | Sanal bakiye, pozisyon açma/kapama, işlem geçmişi (JSON'a kaydeder)|
| `learner.py`         | Parametre kombinasyonlarını deneyen öğrenen ajan (bandit)          |
| `main.py`            | Tüm parçaları birleştiren ana döngü                                 |

## Nasıl öğreniyor?

Ajan; zigzag hassasiyeti (`deviation_pct`) ve TP hedefi (`tp_mult`) için
**12 farklı kombinasyon** (4 hassasiyet × 3 TP çarpanı) dener:

- Önce hiç denenmemiş kombinasyonları sırayla dener (keşif).
- Sonra çoğunlukla (**%75 varsayılan**) şimdiye kadar en iyi ortalama getiri
  (R multiple = kazanç / riske edilen tutar) veren kombinasyonu kullanır
  (**exploit**).
- Arada (**%25**, `--epsilon` ile ayarlanabilir) rastgele başka bir
  kombinasyonu dener (**explore**) — böylece piyasa koşulları değişirse
  ajan kör kalmaz.
- Bir kombinasyon sürekli **SL'e** çarpıyorsa ortalama ödülü düşer ve ajan
  onu gitgide daha az seçer — yani **hatalarından ders çıkarır**.

Öğrenilen istatistikler ve işlem geçmişi `./data/` klasöründe
`learner_state.json` ve `account_state.json` olarak saklanır; ajanı
durdurup tekrar başlattığınızda hafızası kaybolmaz.

## Kurulum

```bash
pip install -r requirements.txt
```

## Kullanım

```bash
# Varsayilan: 10 populer coin, 10.000 USDT sanal bakiye, futures
python main.py

# Kendi coin listeni belirle
python main.py --symbols BTC/USDT,ETH/USDT,SOL/USDT

# Tek tarama (debug / test icin)
python main.py --once

# Spot piyasa, farkli risk ve kesif orani, daha az coin
python main.py --symbols BTC/USDT,ETH/USDT --market spot --risk-pct 1.0 --epsilon 0.15
```

### Parametreler

| Parametre        | Açıklama                                                        | Varsayılan               |
|-------------------|---------------------------------------------------------------------|---------------------------|
| `--symbols`       | Virgülle ayrılmış coin listesi                                       | 10 popüler coin (BTC..TON)|
| `--timeframe`      | Mum aralığı (`1m,5m,15m,1h,4h,1d`)                                    | `1h`                       |
| `--market`         | `swap` (futures) veya `spot`                                          | `swap`                     |
| `--limit`          | Çekilecek mum sayısı                                                  | `300`                       |
| `--interval`       | Sürekli modda tam tarama sıklığı (saniye)                             | `60`                       |
| `--balance`        | Başlangıç sanal bakiye (USDT, **tüm coinler için ortak**)             | `10000`                     |
| `--risk-pct`       | İşlem başına riske edilecek bakiye yüzdesi                           | `2.0`                       |
| `--max-open`       | Aynı anda açık olabilecek en fazla pozisyon sayısı                     | `5`                         |
| `--epsilon`        | Öğrenen ajanın keşif (explore) oranı                                   | `0.25`                     |
| `--data-dir`       | Öğrenme/işlem geçmişi kayıt klasörü                                     | `./data`                     |
| `--report-every`   | Kaç taramada bir özet rapor yazdırılsın                                 | `10`                         |
| `--once`           | Tek tarama yap ve çık                                                   | kapalı                       |

### Örnek çıktı

```
[2026-09-08T05:00:00+00:00] >>> SANAL ISLEM ACILDI: BUY BTC/USDT:USDT | entry=64230.500000
tp=65890.120000 sl=63510.800000 | param(dev%=2.5, tp_x=1.618) | dalga2 retrace=%54.2
[2026-09-08T05:00:01+00:00] >>> SANAL ISLEM ACILDI: SELL SOL/USDT:USDT | entry=142.300000
tp=131.800000 sl=145.900000 | param(dev%=3.5, tp_x=2.0) | dalga2 retrace=%61.8
[2026-09-08T05:00:02+00:00] ETH/USDT:USDT 1h | dev%=1.5 tp_x=1.272 -> gecerli kurulum yok.

[2026-09-08T06:00:00+00:00] <<< SANAL ISLEM KAPANDI: KAZANC (TP) | BUY BTC/USDT:USDT
| pnl=324.0 USDT | R=1.62 | yeni bakiye=10324.0 USDT
----------------------------------------------------------------------
SANAL HESAP OZETI  | izlenen_coin=10 acik_pozisyon=1 islem=14 kazanma_orani=%57.1
toplam_pnl=1183.4 bakiye=11183.4 USDT
Acik pozisyonlar: SOL/USDT:USDT(SELL)
Ogrenilen en iyi parametre kombinasyonlari:
  dev%   tp_x    n  winrate    avgR
   2.5  1.618    9    77.8%    1.24
   3.5  2.000    6    50.0%    0.31
   1.5  1.272    4    25.0%   -0.42
----------------------------------------------------------------------
```

## Notlar / Geliştirme fikirleri

- Öğrenen ajan (`learner.py`) şu an **tüm coinler için ortak** — hangi
  parametre kombinasyonunun iyi çalıştığını coin bazında değil genel olarak
  öğrenir. Coin başına ayrı öğrenme istenirse `Learner` nesnesini sembol
  başına ayrı örnekleyip ayrı bir state dosyasına kaydetmek yeterli.
- `--max-open` ile eşzamanlı pozisyon sayısını sınırlayarak toplam riski
  kontrol altında tutabilirsiniz (örn. 10 coin izlerken `--max-open 3` daha
  temkinli bir yaklaşımdır).
- Gerçek emir göndermeye geçmek isterseniz `paper_account.py`'yi referans
  alıp `ccxt`'in `create_order` fonksiyonunu kullanan ayrı bir "live"
  hesap sınıfı yazmanız gerekir — bu repo bilinçli olarak **sadece
  simülasyon** yapacak şekilde tasarlandı.
- Bandit'in ödül fonksiyonu şu an sadece R multiple. İsterseniz Sharpe
  oranı gibi risk-ayarlı bir metrik de eklenebilir.
