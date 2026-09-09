"""Sanal (paper trading) hesap yonetimi.
Hicbir gercek emir gondermez -- sadece sanal bakiye uzerinde simulasyon yapar.
Birden fazla coin ayni anda, ORTAK tek bir bakiyeden islem acabilir
(her sembol icin en fazla 1 acik pozisyon).
"""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict


@dataclass
class Position:
    symbol: str
    side: str          # BUY / SELL
    entry: float
    tp: float
    sl: float
    risk_amount: float  # bu islemde riske edilen sanal USDT
    size: float          # pozisyon buyuklugu (birim)
    param_key: tuple      # ogrenen ajanin hangi parametreyi kullandigi
    open_time: str


class PaperAccount:
    def __init__(self, state_path: str, starting_balance: float = 10000.0,
                 risk_per_trade_pct: float = 2.0, max_open_positions: int = 5):
        self.state_path = state_path
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.balance: float = starting_balance
        self.open_positions: Dict[str, Position] = {}
        self.history: List[Dict] = []
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r") as f:
                data = json.load(f)
            self.balance = data.get("balance", self.balance)
            self.history = data.get("history", [])
            raw_positions = data.get("open_positions", {})
            self.open_positions = {sym: Position(**p) for sym, p in raw_positions.items()}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        data = {
            "balance": self.balance,
            "history": self.history,
            "open_positions": {sym: asdict(p) for sym, p in self.open_positions.items()},
        }
        with open(self.state_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # ---------------- trading ----------------
    def has_open_position(self, symbol: str) -> bool:
        return symbol in self.open_positions

    def can_open_new(self) -> bool:
        return len(self.open_positions) < self.max_open_positions

    def open_trade(self, symbol: str, side: str, entry: float, tp: float, sl: float, param_key: tuple):
        if symbol in self.open_positions or not self.can_open_new():
            return None
        risk_amount = self.balance * (self.risk_per_trade_pct / 100)
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return None
        size = risk_amount / risk_per_unit
        pos = Position(
            symbol=symbol, side=side, entry=entry, tp=tp, sl=sl,
            risk_amount=risk_amount, size=size, param_key=param_key,
            open_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.open_positions[symbol] = pos
        self._save()
        return pos

    def check_and_close(self, symbol: str, last_price: float) -> Optional[dict]:
        pos = self.open_positions.get(symbol)
        if pos is None:
            return None

        hit_tp = (pos.side == "BUY" and last_price >= pos.tp) or (pos.side == "SELL" and last_price <= pos.tp)
        hit_sl = (pos.side == "BUY" and last_price <= pos.sl) or (pos.side == "SELL" and last_price >= pos.sl)

        if not hit_tp and not hit_sl:
            return None

        exit_price = pos.tp if hit_tp else pos.sl
        if pos.side == "BUY":
            pnl = (exit_price - pos.entry) * pos.size
        else:
            pnl = (pos.entry - exit_price) * pos.size

        r_multiple = pnl / pos.risk_amount if pos.risk_amount > 0 else 0.0
        self.balance += pnl

        result = {
            "symbol": pos.symbol,
            "side": pos.side,
            "entry": pos.entry,
            "exit": exit_price,
            "result": "TP" if hit_tp else "SL",
            "pnl": round(pnl, 4),
            "r_multiple": round(r_multiple, 3),
            "param_key": pos.param_key,
            "open_time": pos.open_time,
            "close_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "balance_after": round(self.balance, 4),
        }
        self.history.append(result)
        del self.open_positions[symbol]
        self._save()
        return result

    def stats(self) -> dict:
        if not self.history:
            return {"trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "balance": round(self.balance, 2),
                     "open_positions": len(self.open_positions)}
        wins = sum(1 for t in self.history if t["result"] == "TP")
        return {
            "trades": len(self.history),
            "win_rate": round(100 * wins / len(self.history), 1),
            "total_pnl": round(sum(t["pnl"] for t in self.history), 2),
            "balance": round(self.balance, 2),
            "open_positions": len(self.open_positions),
        }
