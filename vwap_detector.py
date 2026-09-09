"""VWAP (hacim agirlikli ortalama fiyat) etrafinda ortalamaya donus
(mean-reversion) sinyali.

Elliott Wave dedektoru (wave_detector.py) TREND TAKIP eden bir yaklasimdir:
"dalga 2 bitti, dalga 3 baslayacak" diye trend yonunde islem acar. VWAP
stratejisi ise tam tersi karakterde: fiyat VWAP'tan asiri uzaklastiginda
("bant disina cikti") geri donecegine bahis oynar. Gun ici kurumsal
islemcilerin sik kullandigi klasik bir yaklasimdir.

Bu modul de (wave_detector.py gibi) parametriktir -- deviation/TP/SL
carpanlari disaridan (ogrenen ajan tarafindan) verilir.
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class VwapParams:
    band_mult: float = 2.0   # VWAP'tan kac std-sapma uzaklastiginda "asiri" sayilir
    tp_mult: float = 1.0     # hedef: VWAP'a olan mesafenin bu kadari kadar geri donus
    sl_mult: float = 0.5     # stop: bant genisliginin bu kadari, giris fiyatinin otesinde

    def key(self):
        return (round(self.band_mult, 3), round(self.tp_mult, 3), round(self.sl_mult, 3))


def compute_vwap_bands(df: pd.DataFrame, std_window: int = 20):
    """Cekilen mum penceresi uzerinden rolling VWAP ve VWAP'tan sapmanin
    std sapmasini hesaplar. Kripto 7/24 islem gordugu icin net bir "seans
    acilisi" olmadigindan, VWAP tum pencere uzerinden kumulatif hesaplanir
    (gunluk sifirlama yerine kayan/rolling bir referans)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_vol = cum_vol.replace(0, pd.NA)
    vwap = (typical_price * df["volume"]).cumsum() / cum_vol
    dist = typical_price - vwap
    std = dist.rolling(std_window).std()
    return typical_price, vwap, dist, std


def detect_vwap_signal(df: pd.DataFrame, params: VwapParams) -> Optional[dict]:
    """Fiyat VWAP'tan asiri uzaklasip (z-skoru >= band_mult) simdi geri
    donmeye basladiysa (son mum bir onceki mumdan VWAP yonune donuyorsa)
    BUY/SELL sinyali dondurur. Aksi halde None."""
    if len(df) < 25:
        return None

    typical_price, vwap, dist, std = compute_vwap_bands(df)
    last_std = std.iloc[-1]
    if pd.isna(last_std) or last_std <= 0:
        return None

    last_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    last_vwap = float(vwap.iloc[-1])
    z = float(dist.iloc[-1] / last_std)
    band = last_std * params.band_mult

    # Alt banda dokunup simdi yukari donuyor -> asiri satilmis, VWAP'a donus (BUY)
    if z <= -params.band_mult and last_close > prev_close:
        entry = last_close
        target_gap = max(last_vwap - entry, 0) * params.tp_mult
        tp = entry + target_gap
        sl = entry - band * params.sl_mult
        if not (sl < entry < tp):
            return None
        return {"direction": "BUY", "entry": entry, "tp": tp, "sl": sl, "z": z, "vwap": last_vwap}

    # Ust banda dokunup simdi asagi donuyor -> asiri alinmis, VWAP'a donus (SELL)
    if z >= params.band_mult and last_close < prev_close:
        entry = last_close
        target_gap = max(entry - last_vwap, 0) * params.tp_mult
        tp = entry - target_gap
        sl = entry + band * params.sl_mult
        if not (tp < entry < sl):
            return None
        return {"direction": "SELL", "entry": entry, "tp": tp, "sl": sl, "z": z, "vwap": last_vwap}

    return None
