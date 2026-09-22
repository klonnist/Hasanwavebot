"""Zigzag pivot tespiti ve Elliott Wave Dalga-1/2/3 kurulum tespiti.

Bu modul parametriktir: deviation_pct, retrace araligi ve TP/SL carpanlari
disaridan (ogrenen ajan tarafindan) verilir, boylece farkli parametre
kombinasyonlari denenip performanslari karsilastirilabilir.
"""
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class Pivot:
    idx: int
    price: float
    kind: str  # "H" veya "L"
    time: pd.Timestamp


@dataclass
class WaveParams:
    # deviation_pct burada dogrudan bir yuzde degil, ATR%%'nin katsayisidir
    # (main.py bunu atr_pct(df) ile carpip zigzag_pivots'a gercek yuzdeyi verir).
    deviation_pct: float = 2.5
    # DEGISTI (eskiden 0.236): %23.6 gibi sig bir geri cekilme klasik Elliott
    # kaynaklarinda (Nature's Law / EWP / Handbook) tipik bir DALGA 4 ozelligi
    # sayilir, dalga 2 degil -- dalga 2 genelde keskin/derin olur (%50-88.7).
    # 0.236 alt sinir pratikte neredeyse her pullback'i kabul ediyordu, gercek
    # bir filtre gorevi gormuyordu. 0.5-0.886 araligi EWP/Handbook'un "tipik
    # dalga 2" tanimina cok daha yakin.
    retrace_min: float = 0.5
    retrace_max: float = 0.886
    tp_mult: float = 1.618
    # DEGISTI (eskiden 0.15): stop, dalga-1 uzunlugunun sadece %15'i kadar bir
    # mesafedeydi -- bu, normal piyasa gurultusune neredeyse hic nefes payi
    # birakmiyordu ve TP genis (1.272-2.0x) oldugunda matematiksel olarak
    # zor bir R:R yaratiyordu (bkz. ogrenen ajanin ATR1.2/TP2.0 kombinasyonunda
    # gozlemlenen %16.7 kazanma orani). 0.382 hem Fibonacci-hizali hem de
    # gercekci bir nefes payi veriyor.
    sl_mult: float = 0.382

    def key(self):
        return (round(self.deviation_pct, 3), round(self.tp_mult, 3), round(self.sl_mult, 3))


def atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """Son kapanisa gore ATR'yi yuzde olarak dondurur (volatilite normalizasyonu icin).

    Farkli coinlerin dogal oynakligi cok farkli oldugundan (orn. BTC ile bir
    memecoin), sabit bir yuzdelik zigzag hassasiyeti coinden coine adaletsiz
    calisir. ATR%%, o coinin kendi son hareketine gore olceklenmis bir
    referans verir; zigzag hassasiyeti bunun bir katsayisi olarak kurulur.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.rolling(period).mean().iloc[-1]
    last_close = float(close.iloc[-1])
    if last_close <= 0 or pd.isna(atr):
        return 0.0
    return float(atr / last_close * 100)


def zigzag_pivots(df: pd.DataFrame, deviation_pct: float = 2.5) -> List[Pivot]:
    highs = df["high"].values
    lows = df["low"].values
    times = df["timestamp"].values

    pivots: List[Pivot] = []
    trend = None
    start_price = (highs[0] + lows[0]) / 2
    ref_high, ref_low = highs[0], lows[0]
    ref_high_idx = ref_low_idx = 0
    last_pivot_price = start_price
    last_pivot_idx = 0
    last_pivot_kind = "L"

    for i in range(1, len(df)):
        h, l = highs[i], lows[i]

        if trend is None:
            if h > ref_high:
                ref_high, ref_high_idx = h, i
            if l < ref_low:
                ref_low, ref_low_idx = l, i

            if ref_high >= start_price * (1 + deviation_pct / 100) and ref_high_idx > ref_low_idx:
                trend = "up"
                pivots.append(Pivot(ref_low_idx, ref_low, "L", pd.Timestamp(times[ref_low_idx])))
                last_pivot_price, last_pivot_idx, last_pivot_kind = ref_high, ref_high_idx, "H"
            elif ref_low <= start_price * (1 - deviation_pct / 100) and ref_low_idx > ref_high_idx:
                trend = "down"
                pivots.append(Pivot(ref_high_idx, ref_high, "H", pd.Timestamp(times[ref_high_idx])))
                last_pivot_price, last_pivot_idx, last_pivot_kind = ref_low, ref_low_idx, "L"
            continue

        if trend == "up":
            if h > last_pivot_price:
                last_pivot_price, last_pivot_idx = h, i
            elif l <= last_pivot_price * (1 - deviation_pct / 100):
                pivots.append(Pivot(last_pivot_idx, last_pivot_price, "H", pd.Timestamp(times[last_pivot_idx])))
                trend = "down"
                last_pivot_price, last_pivot_idx, last_pivot_kind = l, i, "L"
        else:
            if l < last_pivot_price:
                last_pivot_price, last_pivot_idx = l, i
            elif h >= last_pivot_price * (1 + deviation_pct / 100):
                pivots.append(Pivot(last_pivot_idx, last_pivot_price, "L", pd.Timestamp(times[last_pivot_idx])))
                trend = "up"
                last_pivot_price, last_pivot_idx, last_pivot_kind = h, i, "H"

    pivots.append(Pivot(last_pivot_idx, last_pivot_price, last_pivot_kind, pd.Timestamp(times[last_pivot_idx])))
    return pivots


def wave1_has_internal_structure(df: pd.DataFrame, p0: Pivot, p1: Pivot, effective_dev_pct: float,
                                  min_sub_pivots: int = 4) -> bool:
    """Dalga 1 adayinin (p0->p1) gercekten itici (impulsif, coklu-bacakli) mi,
    yoksa tek yonlu duz bir sicrayis mi oldugunu kontrol eder.

    Onceki mantik yalnizca 2 fiyat noktasina (p0, p1) bakiyordu ve aralarinda
    kac alt dalga oldugunu hic bilmiyordu -- knowledge_base.md'deki temel
    kurala gore ("1. Dalga impulse veya diagonal olmalidir", EW Patterns.pdf
    / Rules.docx) bir dalga 1 adayinin kendi icinde alt yapisi olmasi
    beklenir, rastgele tek bacakli bir hareket olmamalidir.

    Bunu tam bir alt-dalga sayimi yapmadan, ucuz bir vekil (proxy) ile
    kontrol ediyoruz: p0-p1 araligina, ana zigzag'dan daha ince bir esikle
    (yarisi) ikinci bir zigzag uygulayip kac pivot ciktigina bakiyoruz. Saf
    tek bacakli bir sicrayista sadece 2 pivot (baslangic+bitis) cikar; en
    az `min_sub_pivots` (varsayilan 4) pivot cikmasi, aralarinda en az 2
    gercek yon degisikligi (ic yapi) oldugu anlamina gelir -- bu da tek bir
    duz mumun/gurultunun "dalga 1" sanilmasina karsi ucuz ama gercek bir
    filtredir.
    """
    lo, hi = min(p0.idx, p1.idx), max(p0.idx, p1.idx)
    if hi - lo < 3:
        # cok kisa bir aralikta ic yapi aramak anlamsiz (yeterli mum yok)
        return False
    segment = df.iloc[lo:hi + 1].reset_index(drop=True)
    fine_dev_pct = max(effective_dev_pct / 2, 0.02)
    sub_pivots = zigzag_pivots(segment, deviation_pct=fine_dev_pct)
    return len(sub_pivots) >= min_sub_pivots


def higher_timeframe_trend(df_htf: pd.DataFrame, sma_period: int = 50) -> Optional[str]:
    """Ust zaman diliminde basit bir trend yonu tahmini dondurur: 'up' / 'down' / None.

    knowledge_base.md'de defalarca vurgulanan "once buyuk resim, sonra kucuk
    resim" ilkesinin (coklu zaman dilimi/derece tutarliligi) kod karsiligi.
    Eskiden bot SADECE islem yaptigi tek zaman dilimine bakiyordu, hicbir
    ust-derece trend teyidi yoktu. Yeterli veri yoksa None doner (filtre
    devre disi kalir, islem engellenmez -- "fail-open").
    """
    if df_htf is None or len(df_htf) < sma_period:
        return None
    sma = df_htf["close"].rolling(sma_period).mean().iloc[-1]
    last_close = float(df_htf["close"].iloc[-1])
    if pd.isna(sma):
        return None
    return "up" if last_close > sma else "down"


def detect_wave3_setup(pivots: List[Pivot], params: WaveParams) -> Optional[dict]:
    confirmed = pivots[:-1]
    if len(confirmed) < 3:
        return None

    p0, p1, p2 = confirmed[-3], confirmed[-2], confirmed[-1]

    if p0.kind == "L" and p1.kind == "H" and p2.kind == "L":
        wave1_len = p1.price - p0.price
        if wave1_len <= 0:
            return None
        retrace = (p1.price - p2.price) / wave1_len
        if p2.price <= p0.price:
            return None
        if not (params.retrace_min <= retrace <= params.retrace_max):
            return None
        return {"direction": "BUY", "p0": p0, "p1": p1, "p2": p2,
                "wave1_len": wave1_len, "retrace_pct": retrace * 100}

    if p0.kind == "H" and p1.kind == "L" and p2.kind == "H":
        wave1_len = p0.price - p1.price
        if wave1_len <= 0:
            return None
        retrace = (p2.price - p1.price) / wave1_len
        if p2.price >= p0.price:
            return None
        if not (params.retrace_min <= retrace <= params.retrace_max):
            return None
        return {"direction": "SELL", "p0": p0, "p1": p1, "p2": p2,
                "wave1_len": wave1_len, "retrace_pct": retrace * 100}

    return None


def build_signal_levels(setup: dict, params: WaveParams, last_close: float):
    """Entry/TP/SL seviyelerini hesaplar. (entry, tp, sl) dondurur."""
    p2 = setup["p2"]
    wave1_len = setup["wave1_len"]
    direction = setup["direction"]

    if direction == "BUY":
        entry = last_close
        tp = p2.price + wave1_len * params.tp_mult
        sl = p2.price - wave1_len * params.sl_mult
    else:
        entry = last_close
        tp = p2.price - wave1_len * params.tp_mult
        sl = p2.price + wave1_len * params.sl_mult

    return entry, tp, sl
