"""Donchian Channel breakout sinyali.

Elliott Wave dedektoru (wave_detector.py) ve VWAP dedektoru (vwap_detector.py)
gibi bu modul de parametriktir -- kanal periyodu/TP/SL carpanlari disaridan
(ogrenen ajan tarafindan) verilir.

VWAP stratejisi ortalamaya donus (mean-reversion) karakterindeydi: fiyat
bandin disina cikinca GERI DONECEGINE bahis oynuyordu. Donchian Channel
breakout ise tam tersi: fiyat son N mumun en yuksegini/en dususunu KIRARSA
("breakout") hareketin o yonde DEVAM EDECEGINE bahis oynar (trend takip).
Turtle Trading sisteminin temelini olusturan klasik bir yaklasimdir.
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class DonchianParams:
    channel_period: int = 20  # ust/alt bandi hesaplamak icin kac mum geriye bakilir
    tp_mult: float = 2.0      # hedef: kanal genisliginin bu kadari, kirilma yonunde
    sl_mult: float = 1.0      # stop: kanal genisliginin bu kadari, kirilma yonunun tersine

    def key(self):
        return (round(self.channel_period, 3), round(self.tp_mult, 3), round(self.sl_mult, 3))


def compute_donchian_bands(df: pd.DataFrame, channel_period: int = 20):
    """Son N mumun (SU ANKI mum HARIC -- look-ahead onlemek icin shift(1))
    en yuksek high'i (ust bant) ve en dusuk low'unu (alt bant) hesaplar."""
    upper = df["high"].rolling(channel_period).max().shift(1)
    lower = df["low"].rolling(channel_period).min().shift(1)
    return upper, lower


def detect_donchian_signal(df: pd.DataFrame, params: DonchianParams) -> Optional[dict]:
    """Son kapanis, kendinden onceki N mumun olusturdugu kanalin ustune/altina
    KAPANIS ile kirdiysa (breakout) hareket yonunde BUY/SELL sinyali dondurur.
    Aksi halde None."""
    if len(df) < params.channel_period + 2:
        return None

    upper, lower = compute_donchian_bands(df, params.channel_period)
    last_upper = upper.iloc[-1]
    last_lower = lower.iloc[-1]
    prev_upper = upper.iloc[-2]
    prev_lower = lower.iloc[-2]
    if pd.isna(last_upper) or pd.isna(last_lower) or pd.isna(prev_upper) or pd.isna(prev_lower):
        return None

    width = last_upper - last_lower
    if width <= 0:
        return None

    last_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])

    # Ust bandi YUKARI kirdi (onceki mum bandin icindeydi/altindaydi, simdi ustunde) -> BUY
    if last_close > last_upper and prev_close <= prev_upper:
        entry = last_close
        tp = entry + width * params.tp_mult
        sl = entry - width * params.sl_mult
        if not (sl < entry < tp):
            return None
        return {"direction": "BUY", "entry": entry, "tp": tp, "sl": sl,
                "upper": last_upper, "lower": last_lower, "width": width}

    # Alt bandi ASAGI kirdi (onceki mum bandin icindeydi/ustundeydi, simdi altinda) -> SELL
    if last_close < last_lower and prev_close >= prev_lower:
        entry = last_close
        tp = entry - width * params.tp_mult
        sl = entry + width * params.sl_mult
        if not (tp < entry < sl):
            return None
        return {"direction": "SELL", "entry": entry, "tp": tp, "sl": sl,
                "upper": last_upper, "lower": last_lower, "width": width}

    return None
