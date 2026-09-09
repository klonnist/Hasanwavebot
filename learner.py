"""Basit ogrenen ajan (multi-armed bandit), coin bazinda + genel fallback.

Fikir: Zigzag hassasiyeti (ATR carpani) ve TP carpani (tp_mult) icin
birden fazla kombinasyon (arm) tanimlariz. Her kapanan islemden sonra o
islemde kullanilan kombinasyonun "skorunu" guncelleriz (gerceklesen R
multiple = pnl / riske edilen tutar). Zamanla ajan, gecmiste daha iyi
sonuc veren kombinasyonlari daha sik secer (exploit), ama arada yeni/az
denenmis kombinasyonlari da dener (explore) -- boylece piyasa degistiginde
tamamen kor kalmaz.

Coin bazinda ogrenme: her sembol icin ayri istatistik tutulur (orn. BTC'de
iyi calisan parametre kombinasyonu PENGU'da hic islemeyebilir). Bir sembol
icin yeterli veri (MIN_SYMBOL_SAMPLES) birikene kadar, o kombinasyonun TUM
semboller genelindeki (global) ortalamasi fallback olarak kullanilir --
boylece her sembol icin ayri ayri kor kesif donemi yasanmaz.
"""
import json
import os
import random
from typing import List

from wave_detector import WaveParams
from vwap_detector import VwapParams

# Denenecek parametre kombinasyonlari (grid).
# WAVE_DEVIATIONS artik ATR carpanidir (mutlak yuzde degil) -- bkz. wave_detector.atr_pct
# ve main.py'deki kullanimi. Boylece ayni katsayi BTC icin de yuksek oynaklikli
# bir memecoin icin de o coinin kendi volatilitesine gore olceklenmis olur.
WAVE_DEVIATIONS = [0.8, 1.2, 1.8, 2.5]
WAVE_TP_MULTS = [1.272, 1.618, 2.0]
WAVE_SL_MULT = 0.15  # sabit tutuyoruz, grid'i sismesin diye

# VWAP (ortalamaya donus) stratejisinin grid'i: bant genisligi (std carpani)
# ve VWAP'a donus hedefinin ne kadari kadar kar alinacagi.
VWAP_BAND_MULTS = [1.5, 2.0, 2.5]
VWAP_TP_MULTS = [0.5, 0.75, 1.0]
VWAP_SL_MULT = 0.5

MIN_SYMBOL_SAMPLES = 3  # bu esikten once sembol bazli istatistik yerine global fallback kullanilir


def build_wave_grid() -> List[WaveParams]:
    return [WaveParams(deviation_pct=dev, tp_mult=tp, sl_mult=WAVE_SL_MULT)
            for dev in WAVE_DEVIATIONS for tp in WAVE_TP_MULTS]


def build_vwap_grid() -> List[VwapParams]:
    return [VwapParams(band_mult=band, tp_mult=tp, sl_mult=VWAP_SL_MULT)
            for band in VWAP_BAND_MULTS for tp in VWAP_TP_MULTS]


class Learner:
    def __init__(self, state_path: str, epsilon: float = 0.25, strategy: str = "wave"):
        self.state_path = state_path
        self.epsilon = epsilon
        self.strategy = strategy
        self.grid = build_wave_grid() if strategy == "wave" else build_vwap_grid()
        self.stats = {}          # key(tuple) -> {"n","reward_sum","wins","losses"}  (GENEL/global)
        self.symbol_stats = {}   # (symbol, key(tuple)) -> {...}                      (coin bazli)
        self._load()

    # ---------------- persistence ----------------
    def _symbol_state_path(self) -> str:
        root, ext = os.path.splitext(self.state_path)
        return f"{root}_by_symbol{ext}"

    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r") as f:
                raw = json.load(f)
            # JSON key'leri string oldugu icin tuple'a geri ceviriyoruz
            self.stats = {tuple(json.loads(k)): v for k, v in raw.items()}

        sym_path = self._symbol_state_path()
        if os.path.exists(sym_path):
            with open(sym_path, "r") as f:
                raw = json.load(f)
            self.symbol_stats = {}
            for k, v in raw.items():
                parsed = json.loads(k)
                symbol, key = parsed[0], tuple(parsed[1:])
                self.symbol_stats[(symbol, key)] = v

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        raw = {json.dumps(list(k)): v for k, v in self.stats.items()}
        with open(self.state_path, "w") as f:
            json.dump(raw, f, indent=2)

        raw_sym = {json.dumps([symbol, *key]): v for (symbol, key), v in self.symbol_stats.items()}
        with open(self._symbol_state_path(), "w") as f:
            json.dump(raw_sym, f, indent=2)

    # ---------------- selection ----------------
    @staticmethod
    def _avg_reward(stats_dict: dict, key) -> float:
        s = stats_dict.get(key)
        if not s or s["n"] == 0:
            return 0.0
        return s["reward_sum"] / s["n"]

    def select(self, symbol: str) -> WaveParams:
        # bu sembol icin hic denenmemis kombinasyon varsa once onu dene
        unexplored = [p for p in self.grid if (symbol, p.key()) not in self.symbol_stats]
        if unexplored:
            return random.choice(unexplored)

        if random.random() < self.epsilon:
            return random.choice(self.grid)  # kesif (explore)

        # somuru (exploit): yeterli sembol-bazli veri varsa onu kullan,
        # yoksa o kombinasyonun tum semboller genelindeki ortalamasina guven.
        def score(p: WaveParams) -> float:
            sym_key = (symbol, p.key())
            s = self.symbol_stats.get(sym_key)
            if s and s["n"] >= MIN_SYMBOL_SAMPLES:
                return self._avg_reward(self.symbol_stats, sym_key)
            return self._avg_reward(self.stats, p.key())

        return max(self.grid, key=score)

    def update(self, symbol: str, key: tuple, reward: float, win: bool):
        for stats_dict, k in ((self.stats, key), (self.symbol_stats, (symbol, key))):
            s = stats_dict.setdefault(k, {"n": 0, "reward_sum": 0.0, "wins": 0, "losses": 0})
            s["n"] += 1
            s["reward_sum"] += reward
            if win:
                s["wins"] += 1
            else:
                s["losses"] += 1
        self._save()

    # ---------------- reporting ----------------
    def leaderboard(self, top_n: int = 5) -> str:
        rows = []
        for p in self.grid:
            key = p.key()
            s = self.stats.get(key)
            if not s or s["n"] == 0:
                continue
            avg_r = s["reward_sum"] / s["n"]
            win_rate = 100 * s["wins"] / s["n"]
            rows.append((avg_r, win_rate, s["n"], p))

        rows.sort(key=lambda r: r[0], reverse=True)
        first_col = "atr_x" if self.strategy == "wave" else "band_x"
        lines = [f"{first_col:>6} {'tp_x':>6} {'n':>4} {'winrate':>8} {'avgR':>7}"]
        for avg_r, win_rate, n, p in rows[:top_n]:
            first_val = p.key()[0]  # deviation_pct (wave) veya band_mult (vwap) -- key() ikisinde de ayni sirada
            lines.append(f"{first_val:>6.1f} {p.tp_mult:>6.3f} {n:>4} {win_rate:>7.1f}% {avg_r:>7.2f}")
        return "\n".join(lines) if len(lines) > 1 else "Henuz yeterli veri yok."
