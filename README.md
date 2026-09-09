# Elliott Wave Dalga-3 Öğrenen Ajan (OKX, Çoklu-Coin)

### 🔴 [Canlı Panel: klonnist.github.io/Hasanwavebot](https://klonnist.github.io/Hasanwavebot/)

OKX'ten (varsayılan: **USDT-M Perpetual Futures / swap**) canlı veri okuyan,
**birden fazla popüler coini aynı anda tarayan**, her birinde bir kurulum
(Elliott Wave Dalga 1-2-3 veya VWAP'a dönüş — bkz. aşağı) arayan ve
bulduğunda **sanal (paper trading) bir hesap üzerinden** BUY/SELL
pozisyonu açıp kademeli kâr alma/trailing stop ile yöneten bir ajan. Her
kapanan işlemden sonra hangi parametre kombinasyonunun daha iyi sonuç
verdiğini öğrenir ve zamanla kazandıran kombinasyonları daha sık,
kaybettirenleri daha az kullanır.

## Varsayılan izlenen coinler

BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, TON, ETHFI, PENGU, NEAR
(`main.py` içinde `POPULAR_COINS` listesi, `--symbols` ile değiştirilebilir).

> Not: XAUT/USDT (Tether Gold) OKX'te sadece spot piyasada işlem görüyor,
> USDT-M perpetual futures (swap) karşılığı yok — bu yüzden listeye
> eklenmedi.

## GitHub Actions ile 7/24 çoklu profil taraması

[`.github/workflows/run-bot.yml`](.github/workflows/run-bot.yml) her 15
dakikada bir **dört ayrı profili birbirinden bağımsız** tarar; her biri
kendi ayrı sanal 10.000 USDT hesabına ve kendi öğrenen ajanına sahiptir,
böylece sonuçları birbirinden bağımsız karşılaştırabilirsiniz:

| Profil  | Strateji | Zaman dilimi | Klasör       | Kaldıraç |
|---------|----------|--------------|--------------|----------|
| `15m`   | Elliott Wave (`--strategy wave`) | 15dk | `data/15m/`  | 10x |
| `4h`    | Elliott Wave (`--strategy wave`) | 4 saat | `data/4h/`   | 5x  |
| `1d`    | Elliott Wave (`--strategy wave`) | 1 gün | `data/1d/`   | 3x  |
| `vwap`  | VWAP ortalamaya dönüş (`--strategy vwap`) | 15dk | `data/vwap/` | 10x |

Sonuçlar [klonnist.github.io/Hasanwavebot](https://klonnist.github.io/Hasanwavebot/)
adresindeki panelde sekmeler halinde canlı gösterilir.

## İki farklı strateji

`--strategy` bayrağıyla seçilir; ikisi de aynı altyapıyı (veri çekme,
sanal hesap, risk yönetimi, öğrenen ajan, kademeli kâr alma) paylaşır,
sadece "ne zaman işlem açılır" mantığı farklıdır:

- **`wave` (varsayılan)** — Elliott Wave Dalga-3: **trend takip eden**
  bir yaklaşım. Dalga-2 düzeltmesi bitince Dalga-3'ün geleceğine bahis
  oynar (`wave_detector.py`).
- **`vwap`** — VWAP'a dönüş: **ortalamaya dönüş (mean-reversion)**
  yaklaşımı, gün içi kurumsal işlemcilerin sık kullandığı bir yöntem.
  Fiyat VWAP'tan (hacim ağırlıklı ortalama fiyat) istatistiksel olarak
  anlamlı şekilde uzaklaşıp (`band_mult` × standart sapma) geri dönmeye
  başladığında, VWAP'a doğru bir hareket bekleyerek işlem açar
  (`vwap_detector.py`). Karakteri `wave`'in tam tersi: `wave` "trend
  devam edecek" der, `vwap` "aşırı hareket geri çekilecek" der — ikisini
  aynı anda çalıştırmak, botun tek bir piyasa görüşüne bağımlı kalmasını
  önler.

**Pozisyon büyüklüğü — sabit teminat × kaldıraç:** Her işlem, bakiyenin
yüzdesi yerine **sabit `--trade-margin` (varsayılan 500 USDT)** teminat
kullanır; pozisyon büyüklüğü (`notional`) bu teminat çarpı kaldıraçtır
(`notional = trade_margin × leverage`, `size = notional / entry`). Yani
15m profilinde her işlem 500 USDT × 10x = 5.000 USDT'lik pozisyon açar,
1d profilinde 500 USDT × 3x = 1.500 USDT'lik. Riske edilen tutar
(`risk_amount`) artık sabit değil, stop mesafesine göre değişir — bu da
R multiple (kazanç/risk) hesaplamasında ve öğrenen ajanın ödül
fonksiyonunda kullanılıyor.

Ajan her taramada listedeki **her coin için ayrı ayrı** kurulum arar; aynı
anda birden fazla coinde pozisyon açık olabilir (varsayılan sınır: **5**,
`--max-open` ile ayarlanır). Tüm pozisyonlar **tek ortak 10.000 USDT'lik
sanal bakiyeden** risk alır.

**Portföy bazlı risk tavanı:** Kripto piyasasında çoğu coin birlikte hareket
ettiği için (korelasyon), "5 pozisyon = %10 risk" varsayımı gerçekte
yanıltıcı olabilir — piyasa geneli düşerken 5 pozisyon aynı anda vurabilir.
Bunu sınırlamak için `--max-portfolio-risk-pct` (varsayılan **%8**) tüm açık
pozisyonların **toplam** riskine üst sınır koyar; pozisyon sayısı sınırına
ulaşılmasa bile bu tavan aşılacaksa yeni işlem açılmaz.

**Teminat (margin) kontrolü:** Gerçek bir borsada olduğu gibi, açık
pozisyonların toplam margin'i (`notional / kaldıraç`) mevcut bakiyeyi
aşacaksa yeni işlem reddedilir — yani simülasyon, gerçekte imkansız
olacak büyüklükte pozisyon açamaz.

**Funding ücreti:** Perpetual futures'ta pozisyon her 8 saatte bir funding
ücreti öder/alır. Ajan her taramada OKX'in güncel funding oranını çekip
(`ccxt.fetch_funding_rate`) açık pozisyonlara oranlı olarak işler ve
bakiyeden düşer/ekler — özellikle **1 Gün** profili gibi günlerce açık
kalan pozisyonlarda bu maliyet artık gerçekçi şekilde hesaba katılıyor.

**Anlık kâr/zarar (mark-to-market):** Bir pozisyon henüz TP/SL'e çarpmasa
bile, her taramada güncel fiyata göre gerçekleşmemiş kâr/zararı hesaplanıp
`account_state.json`'a kaydediliyor (`unrealized_pnl`, `unrealized_r`).
Panelde açık pozisyonlar tablosunda ve kart özetinde canlı olarak görünür.

**Kademeli kâr alma + trailing stop:** Sabit tek bir TP hedefi yerine,
pozisyon kâra geçtikçe üç aşamalı bir yönetim uygulanır:

1. **Başabaş (breakeven)** — `--breakeven-r` R'a ulaşınca (varsayılan **1R**)
   SL, giriş fiyatına çekilir. Pozisyon artık en kötü ihtimalle nötr kapanır.
2. **Kısmi kâr alma** — `--partial-tp-r` R'a ulaşınca (varsayılan **1.5R**)
   pozisyonun `--partial-tp-fraction` kadarı (varsayılan **%50**) hemen
   nakde çevrilir; kalan kısım açık kalmaya devam eder.
3. **Trailing stop** — kısmi alındıktan sonra SL, fiyat ilerledikçe
   kazancın en fazla `--trail-giveback-pct` kadarını (varsayılan **%50**)
   geri verecek şekilde takip eder — orijinal TP seviyesini asla aşmaz
   (bir tavan/çatı olarak kalır).

Böylece bir pozisyon "SL'e çarptı" görünse bile, trailing stop kâr
durumundayken tetiklenmişse **gerçekte kazançlı** kapanmış olabilir —
panelde bu durum "KAZANÇ (trailing)" olarak, kazanma oranı da gerçek
kâr/zarar işaretine göre (sadece TP/SL etiketine göre değil) hesaplanır.
Kısmi kâr alma anları, işlem geçmişinde ayrı "KISMİ AL" satırı olarak
görünür (istatistiklere dahil edilmez, tamamlanmış bir işlem sayılmaz).

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
| `data_feed.py`       | OKX'ten `ccxt` ile mum/fiyat/funding verisi çeker                  |
| `wave_detector.py`   | Zigzag pivot tespiti + Elliott Wave Dalga 1-2-3 kurulum tespiti (strateji: `wave`) |
| `vwap_detector.py`   | VWAP hesabı + ortalamaya dönüş kurulum tespiti (strateji: `vwap`)  |
| `paper_account.py`   | Sanal bakiye, pozisyon açma/kapama, kademeli kâr alma, işlem geçmişi (JSON'a kaydeder) |
| `learner.py`         | Parametre kombinasyonlarını deneyen öğrenen ajan (bandit)          |
| `main.py`            | Canlı tarama: tüm parçaları birleştiren ana döngü                   |
| `backtest.py`        | Geçmiş veride "bu strateji ne kazandırırdı?" testi                  |

## Nasıl öğreniyor?

Ajan; zigzag hassasiyeti (ATR çarpanı) ve TP hedefi (`tp_mult`) için
**12 farklı kombinasyon** (4 ATR çarpanı × 3 TP çarpanı) dener:

- Önce hiç denenmemiş kombinasyonları sırayla dener (keşif).
- Sonra çoğunlukla (**%75 varsayılan**) şimdiye kadar en iyi ortalama getiri
  (R multiple = kazanç / riske edilen tutar) veren kombinasyonu kullanır
  (**exploit**).
- Arada (**%25**, `--epsilon` ile ayarlanabilir) rastgele başka bir
  kombinasyonu dener (**explore**) — böylece piyasa koşulları değişirse
  ajan kör kalmaz.
- Bir kombinasyon sürekli **SL'e** çarpıyorsa ortalama ödülü düşer ve ajan
  onu gitgide daha az seçer — yani **hatalarından ders çıkarır**.

**ATR bazlı (volatiliteye duyarlı) hassasiyet:** Zigzag hassasiyeti artık
sabit bir yüzde değil, o coinin son 14 mumdaki ATR%'sinin bir katsayısı
(`wave_detector.atr_pct`). Böylece aynı katsayı, BTC gibi düşük oynaklıklı
bir coin için de PENGU gibi çok oynak bir coin için de o coinin kendi
hareketine göre adil bir eşik üretir — sabit yüzdeyle BTC'de hiç sinyal
üretmeyecek bir ayar, PENGU'da sadece gürültüden sinyal üretmiyor.

**Coin bazlı öğrenme (global fallback'li):** Öğrenen ajan artık her sembol
için ayrı istatistik tutuyor — BTC'de iyi çalışan kombinasyon PENGU'yu
etkilemiyor. Bir sembol için yeterli veri (`MIN_SYMBOL_SAMPLES = 3`)
birikene kadar, o kombinasyonun **tüm semboller genelindeki** ortalaması
fallback olarak kullanılır, böylece 13 coin × 12 kombinasyon için ayrı ayrı
sıfırdan keşif dönemi yaşanmaz.

Öğrenilen istatistikler ve işlem geçmişi her zaman dilimi klasöründe
(`data/<tf>/`) `learner_state.json` (genel), `learner_state_by_symbol.json`
(coin bazlı) ve `account_state.json` olarak saklanır; ajanı durdurup
tekrar başlattığınızda hafızası kaybolmaz.

## Backtest -- "bu tarih aralığında ne kazandırırdı?"

[`backtest.py`](backtest.py) aynı tespit mantığını, aynı sanal hesabı
(margin/portföy risk kontrolleri, kademeli kâr alma/trailing stop dahil)
ve aynı öğrenen ajanı kullanarak **geçmiş veride** çalıştırır:

```bash
# BTC ve ETH'de, Elliott Wave stratejisiyle, 4 saatlik mumlarla,
# 2026-05-01 ile 2026-09-01 arasinda ne kazandirirdi?
python backtest.py --symbols BTC/USDT,ETH/USDT --strategy wave \
    --timeframe 4h --from 2026-05-01 --to 2026-09-01

# VWAP stratejisiyle, sonuclari bir klasore de kaydet
python backtest.py --strategy vwap --timeframe 1h \
    --from 2026-06-01 --to 2026-09-01 --out-dir ./backtest_out
```

Çıktı olarak toplam kâr/zarar, kazanma oranı, **maksimum drawdown** ve
öğrenen ajanın hangi parametre kombinasyonunu en iyi bulduğunu gösteren
bir özet basar.

**Python kurmadan, GitHub üzerinden çalıştırmak:** Yerelde Python
kurulu olmasa bile [Actions → "Backtest calistir" → "Run workflow"](../../actions/workflows/backtest.yml)
üzerinden bir form doldurup (strateji, zaman dilimi, tarih aralığı...)
tetikleyebilirsiniz.

**Sonuçları panelde görmek:** Her çalıştırma, sonucu
`data/backtests/<id>.json` olarak repoya kaydeder ve `index.json`'a
ekler; [canlı panelin](https://klonnist.github.io/Hasanwavebot/)
**Backtest** sekmesinde geçmiş tüm çalıştırmalar listelenir —
"Detay" ile bakiye eğrisi, öğrenilen parametreler, işlem listesi ve
kullanılan ayarlar görülebilir. (`--report-dir` bayrağı bunu yerelde
de üretir.)

**Sınırlamalar** (canlı bottan farkı):
- Mum **kapanış fiyatına** göre karar verir — bir mum içinde fiyatın
  TP/SL'e değip geri dönmesi (intrabar iğne) yakalanmaz. Bu, canlı
  botun zaten periyodik (15 dakikada bir) kontrol etme davranışına
  yakındır, ama gerçek sonuçlar biraz farklı olabilir.
- **Funding ücreti simüle edilmez** (sadece fiyat bazlı kâr/zarar).
- `--window` (varsayılan 150 mum), her karar anında dedektöre verilen
  pencere boyutudur — canlı moddaki `--limit` ile aynı role sahiptir.

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

# Spot piyasa, farkli islem basina teminat ve kesif orani, daha az coin
python main.py --symbols BTC/USDT,ETH/USDT --market spot --trade-margin 250 --epsilon 0.15

# VWAP (ortalamaya donus) stratejisiyle calistir
python main.py --strategy vwap --timeframe 15m
```

### Parametreler

| Parametre        | Açıklama                                                        | Varsayılan               |
|-------------------|---------------------------------------------------------------------|---------------------------|
| `--symbols`       | Virgülle ayrılmış coin listesi                                       | 10 popüler coin (BTC..TON)|
| `--strategy`      | `wave` (Elliott Wave, trend takip) veya `vwap` (VWAP'a dönüş, mean-reversion) | `wave`      |
| `--timeframe`      | Mum aralığı (`1m,5m,15m,1h,4h,1d`)                                    | `1h`                       |
| `--market`         | `swap` (futures) veya `spot`                                          | `swap`                     |
| `--limit`          | Çekilecek mum sayısı                                                  | `300`                       |
| `--interval`       | Sürekli modda tam tarama sıklığı (saniye)                             | `60`                       |
| `--balance`        | Başlangıç sanal bakiye (USDT, **tüm coinler için ortak**)             | `10000`                     |
| `--trade-margin`   | İşlem başına kullanılacak SABİT teminat (USDT); `notional = bu × kaldıraç` | `500.0`                 |
| `--leverage`       | Pozisyonlarda kullanılacak kaldıraç                                    | `1.0`                       |
| `--max-open`       | Aynı anda açık olabilecek en fazla pozisyon sayısı                     | `5`                         |
| `--max-portfolio-risk-pct` | Tüm açık pozisyonların toplam riskinin bakiyeye oranı üst sınırı | `8.0`                 |
| `--breakeven-r`    | Bu R'a ulaşınca SL başabaşa (giriş fiyatına) çekilir                    | `1.0`                       |
| `--partial-tp-r`   | Bu R'a ulaşınca pozisyonun bir kısmı kapatılır (kısmi kâr alma)          | `1.5`                       |
| `--partial-tp-fraction` | Kısmi kâr alırken kapatılacak oran (0.5 = pozisyonun yarısı)        | `0.5`                       |
| `--trail-giveback-pct` | Kısmi sonrası SL'in kazancın en fazla ne kadarını geri vereceği       | `0.5`                       |
| `--epsilon`        | Öğrenen ajanın keşif (explore) oranı                                   | `0.25`                     |
| `--data-dir`       | Öğrenme/işlem geçmişi kayıt klasörü                                     | `./data`                     |
| `--report-every`   | Kaç taramada bir özet rapor yazdırılsın                                 | `10`                         |
| `--once`           | Tek tarama yap ve çık                                                   | kapalı                       |

### Örnek çıktı

```
[2026-09-08T05:00:00+00:00] >>> SANAL ISLEM ACILDI: BUY BTC/USDT:USDT | entry=64230.500000
tp=65890.120000 sl=63510.800000 | kaldirac=10.0x margin=500.00 | param(atr_x=2.5, tp_x=1.618) | dalga2 retrace=%54.2
[2026-09-08T05:00:01+00:00] SOL/USDT:USDT 15m | atr_x=1.8 (atr%=0.64 -> esik%=1.14) tp_x=2.0 -> gecerli kurulum yok.
[2026-09-08T05:15:00+00:00] Pozisyon acik: BUY BTC/USDT:USDT | entry=64230.500000 guncel=64890.000000
| anlik_kz=+412.50 USDT (R=2.06) | tp=65890.120000 sl=63510.800000

[2026-09-08T06:00:00+00:00] <<< SANAL ISLEM KAPANDI: KAZANC (TP) | BUY BTC/USDT:USDT
| pnl=324.0 USDT | funding=-1.2 USDT | R=1.62 | yeni bakiye=10322.8 USDT
----------------------------------------------------------------------
SANAL HESAP OZETI  | izlenen_coin=13 acik_pozisyon=1 islem=14 kazanma_orani=%57.1
toplam_pnl=1183.4 anlik_kz=45.2 funding_maliyeti=8.6 bakiye=11183.4 USDT
Acik pozisyonlar: SOL/USDT:USDT(SELL)
Ogrenilen en iyi parametre kombinasyonlari:
 atr_x   tp_x    n  winrate    avgR
   1.8  1.618    9    77.8%    1.24
   2.5  2.000    6    50.0%    0.31
   0.8  1.272    4    25.0%   -0.42
----------------------------------------------------------------------
```

## Notlar / Geliştirme fikirleri

- `--max-open` ve `--max-portfolio-risk-pct` ile eşzamanlı pozisyon
  sayısını ve toplam riski sınırlayabilirsiniz (örn. 13 coin izlerken
  `--max-open 3 --max-portfolio-risk-pct 5` daha temkinli bir yaklaşımdır).
- Gerçek emir göndermeye geçmek isterseniz `paper_account.py`'yi referans
  alıp `ccxt`'in `create_order` fonksiyonunu kullanan ayrı bir "live"
  hesap sınıfı yazmanız gerekir — bu repo bilinçli olarak **sadece
  simülasyon** yapacak şekilde tasarlandı.
- Bandit'in ödül fonksiyonu şu an sadece R multiple. İsterseniz Sharpe
  oranı / maksimum drawdown gibi risk-ayarlı metrikler de eklenebilir.
- İşlem açılıp kapandığında Telegram/Discord bildirimi göndermek isterseniz
  `main.py`'deki `log()` çağrılarının yanına bir webhook isteği eklemek
  yeterli.
