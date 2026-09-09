"""Basit ogrenen ajan (multi-armed bandit).

Fikir: Zigzag hassasiyeti (deviation_pct) ve TP carpani (tp_mult) icin
birden fazla kombinasyon (arm) tanimlariz. Her kapanan islemden sonra o
islemde kullanilan kombinasyonun "skorunu" guncelleriz (gerceklesen R
multiple = pnl / riske edilen tutar). Zamanla ajan, gecmiste daha iyi
sonuc veren kombinasyonlari daha sik secer (exploit), ama arada yeni/az
denenmis kombinasyonlari da dener (explore) -- boylece piyasa degistiginde
tamamen kor kalmaz.

Bu, "hatalarindan ders cikarma" davranisini uygulayan minimal ama gercek
bir ogrenme mekanizmasidir: kaybettiren parametreler zamanla daha az
secilir, kazandiranlar daha sik secilir.
"""
import json
import os
import random
from typing import List

from wave_detector import WaveParams

# Denenecek parametre kombinasyonlari (grid).
DEVIATIONS = [1.5, 2.5, 3.5, 5.0]
TP_MULTS = [1.272, 1.618, 2.0]
SL_MULT = 0.15  # sabit tutuyoruz, grid'i sismesin diye


def build_grid() -> List[WaveParams]:
    grid = []
    for dev in DEVIATIONS:
        for tp in TP_MULTS:
            grid.append(WaveParams(deviation_pct=dev, tp_mult=tp, sl_mult=SL_MULT))
    return grid


class Learner:
    def __init__(self, state_path: str, epsilon: float = 0.25):
        self.state_path = state_path
        self.epsilon = epsilon
        self.grid = build_grid()
        self.stats = {}  # key(tuple) -> {"n": int, "reward_sum": float, "wins": int, "losses": int}
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r") as f:
                raw = json.load(f)
            # JSON key'leri string oldugu icin tuple'a geri ceviriyoruz
            self.stats = {tuple(json.loads(k)): v for k, v in raw.items()}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        raw = {json.dumps(list(k)): v for k, v in self.stats.items()}
        with open(self.state_path, "w") as f:
            json.dump(raw, f, indent=2)

    # ---------------- selection ----------------
    def _avg_reward(self, key) -> float:
        s = self.stats.get(key)
        if not s or s["n"] == 0:
            return 0.0
        return s["reward_sum"] / s["n"]

    def select(self) -> WaveParams:
        # hic denenmemis kombinasyon varsa once onu dene
        unexplored = [p for p in self.grid if p.key() not in self.stats]
        if unexplored:
            return random.choice(unexplored)

        if random.random() < self.epsilon:
            return random.choice(self.grid)  # kesif (explore)

        # sonuc: en iyi ortalama odule sahip kombinasyonu sec (somuru / exploit)
        best = max(self.grid, key=lambda p: self._avg_reward(p.key()))
        return best

    def update(self, key: tuple, reward: float, win: bool):
        s = self.stats.setdefault(key, {"n": 0, "reward_sum": 0.0, "wins": 0, "losses": 0})
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
        lines = [f"{'dev%':>6} {'tp_x':>6} {'n':>4} {'winrate':>8} {'avgR':>7}"]
        for avg_r, win_rate, n, p in rows[:top_n]:
            lines.append(f"{p.deviation_pct:>6.1f} {p.tp_mult:>6.3f} {n:>4} {win_rate:>7.1f}% {avg_r:>7.2f}")
        return "\n".join(lines) if len(lines) > 1 else "Henuz yeterli veri yok."
