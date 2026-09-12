"""Zaman-agirlikli (recency-weighted) ogrenen ajan -- learner.py'nin ADAPTIF varyanti.

learner.py'deki Learner sinifi, her parametre kombinasyonu icin TUM ZAMANLARIN
kumulatif ortalama odulunu tutar (reward_sum / n): yeni bir islem, o kombinasyon
5 kere mi 500 kere mi denenmis olursa olsun eklendigi anda ayni agirlikta katilir.
Bu, DURAGAN (stationary) bir piyasada dogru yaklasimdir, ama piyasa REJIMI
degistiginde (orn. VWAP backtest'lerini yil yil incelerken 2022'de gorulen desen:
yilin ilk aylarinda iyi giden bir parametre kombinasyonuna kilitlenip, rejim
degisince bu kombinasyonun kotu calismasina gecte fark etme -- eski basarili
donem ortalamayi hala yukarida tuttugu icin) ajanin yeni rejime adapte olmasi
gecikir; erken kazanc kazanilir, sonra donemin geri kalaninda geri verilir.

Standart cozum (bandit literaturunde "non-stationary ortam" icin kullanilan
klasik teknik -- bkz. Sutton & Barto, "Reinforcement Learning: An Introduction",
bolum 2.5 "Tracking a Nonstationary Problem"): kumulatif ortalamanin adim
buyuklugu olan 1/n yerine SABIT bir adim buyuklugu (alpha) kullanarak ussel
azalan agirlikli ortalama (exponentially weighted moving average) tutmak:

    Q_{n+1} = Q_n + alpha * (R_n - Q_n)

Bu guncellemeyi actigimizda Q_{n+1}, gecmis odullerin agirlikli toplamidir ve
her odulun agirligi gecmise gittikce (1-alpha)^k ile ussel azalir -- yani en
son islemler, cok eski islemlerden DAHA FAZLA agirlik tasir. alpha buyudukce
ajan rejim degisikliklerine daha hizli adapte olur ama gurultuye de daha
duyarli hale gelir; alpha kuculdukce learner.py'nin kumulatif ortalamasina
yaklasir. 0.15-0.25 araligindaki sabit bir deger pratikte iyi bir denge sunar.

Bu dosya learner.py'yi DEGISTIRMEZ -- ayri, bagimsiz bir varyanttir. Canli
botun kullandigi learner.py ve onun okudugu/yazdigi state dosyalari bundan
etkilenmez. Grid tanimlari (denenecek parametre kombinasyonlari) duplike
edilmeden learner.py'den import edilir.
"""
import json
import os
import random

from learner import (
    build_wave_grid,
    build_vwap_grid,
    build_donchian_grid,
    MIN_SYMBOL_SAMPLES,
)

DEFAULT_DECAY_ALPHA = 0.2  # sabit adim buyuklugu (alpha) -- EWMA'nin "hafiza uzunlugunu" belirler


class AdaptiveLearner:
    def __init__(self, state_path: str, epsilon: float = 0.25, strategy: str = "vwap",
                 decay_alpha: float = DEFAULT_DECAY_ALPHA):
        self.state_path = state_path
        self.epsilon = epsilon
        self.strategy = strategy
        self.decay_alpha = decay_alpha
        if strategy == "wave":
            self.grid = build_wave_grid()
        elif strategy == "vwap":
            self.grid = build_vwap_grid()
        else:
            self.grid = build_donchian_grid()
        self.stats = {}          # key(tuple) -> {"n","avg_reward","reward_sum","wins","losses"}  (GENEL/global)
        self.symbol_stats = {}   # (symbol, key(tuple)) -> {...}                                  (coin bazli)
        self._load()

    # ---------------- persistence (learner.py ile ayni format) ----------------
    def _symbol_state_path(self) -> str:
        root, ext = os.path.splitext(self.state_path)
        return f"{root}_by_symbol{ext}"

    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r") as f:
                raw = json.load(f)
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
        return s["avg_reward"]

    def select(self, symbol: str):
        unexplored = [p for p in self.grid if (symbol, p.key()) not in self.symbol_stats]
        if unexplored:
            return random.choice(unexplored)

        if random.random() < self.epsilon:
            return random.choice(self.grid)

        def score(p) -> float:
            sym_key = (symbol, p.key())
            s = self.symbol_stats.get(sym_key)
            if s and s["n"] >= MIN_SYMBOL_SAMPLES:
                return self._avg_reward(self.symbol_stats, sym_key)
            return self._avg_reward(self.stats, p.key())

        return max(self.grid, key=score)

    def update(self, symbol: str, key: tuple, reward: float, win: bool):
        for stats_dict, k in ((self.stats, key), (self.symbol_stats, (symbol, key))):
            s = stats_dict.setdefault(k, {"n": 0, "avg_reward": 0.0, "reward_sum": 0.0, "wins": 0, "losses": 0})
            s["n"] += 1
            if s["n"] == 1:
                s["avg_reward"] = reward
            else:
                s["avg_reward"] += self.decay_alpha * (reward - s["avg_reward"])
            # reward_sum'i avg_reward*n olarak turetiyoruz ki learner.py'nin
            # reward_sum/n okuyan genel-amacli tuketici kodlari (backtest
            # raporlama, panel JS'i) degismeden bu learner icin de calissin.
            s["reward_sum"] = s["avg_reward"] * s["n"]
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
            win_rate = 100 * s["wins"] / s["n"]
            rows.append((s["avg_reward"], win_rate, s["n"], p))

        rows.sort(key=lambda r: r[0], reverse=True)
        first_col = {"wave": "atr_x", "vwap": "band_x", "donchian": "period"}[self.strategy]
        lines = [f"{first_col:>6} {'tp_x':>6} {'n':>4} {'winrate':>8} {'avgR':>7}"]
        for avg_r, win_rate, n, p in rows[:top_n]:
            first_val = p.key()[0]
            lines.append(f"{first_val:>6.1f} {p.tp_mult:>6.3f} {n:>4} {win_rate:>7.1f}% {avg_r:>7.2f}")
        return "\n".join(lines) if len(lines) > 1 else "Henuz yeterli veri yok."
