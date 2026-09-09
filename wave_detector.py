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
    deviation_pct: float = 2.5
    retrace_min: float = 0.236
    retrace_max: float = 0.886
    tp_mult: float = 1.618
    sl_mult: float = 0.15

    def key(self):
        return (round(self.deviation_pct, 3), round(self.tp_mult, 3), round(self.sl_mult, 3))


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
