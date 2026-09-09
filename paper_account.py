"""Sanal (paper trading) hesap yonetimi.
Hicbir gercek emir gondermez -- sadece sanal bakiye uzerinde simulasyon yapar.
Birden fazla coin ayni anda, ORTAK tek bir bakiyeden islem acabilir
(her sembol icin en fazla 1 acik pozisyon).
"""
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict

FUNDING_INTERVAL_HOURS = 8  # OKX perpetual funding periyodu


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
    leverage: float = 1.0    # bu hesabin kullandigi kaldirac
    notional: float = 0.0    # pozisyonun kaldiracli toplam degeri (entry * size)
    margin: float = 0.0      # kaldirac sonrasi baglanan sanal teminat (notional / leverage)
    last_funding_time: str = ""  # son funding kesintisinin uygulandigi zaman
    funding_paid: float = 0.0    # simdiye kadar odenen/alinan toplam funding (USDT, pozitif=maliyet)
    last_price: float = 0.0      # en son gorulen anlik fiyat (mark-to-market)
    unrealized_pnl: float = 0.0  # anlik gerceklesmemis kar/zarar (USDT)
    unrealized_r: float = 0.0    # anlik gerceklesmemis R multiple


class PaperAccount:
    def __init__(self, state_path: str, starting_balance: float = 10000.0,
                 risk_per_trade_pct: float = 2.0, max_open_positions: int = 5,
                 leverage: float = 1.0, max_portfolio_risk_pct: float = 8.0):
        self.state_path = state_path
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.leverage = leverage
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        self.starting_balance = starting_balance
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
            self.starting_balance = data.get("starting_balance", self.starting_balance)
            self.history = data.get("history", [])
            raw_positions = data.get("open_positions", {})
            self.open_positions = {sym: Position(**p) for sym, p in raw_positions.items()}

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        data = {
            "balance": self.balance,
            "starting_balance": self.starting_balance,
            "history": self.history,
            "open_positions": {sym: asdict(p) for sym, p in self.open_positions.items()},
        }
        with open(self.state_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # ---------------- risk helpers ----------------
    def has_open_position(self, symbol: str) -> bool:
        return symbol in self.open_positions

    def can_open_new(self) -> bool:
        return len(self.open_positions) < self.max_open_positions

    def open_risk_total(self) -> float:
        return sum(p.risk_amount for p in self.open_positions.values())

    def used_margin(self) -> float:
        return sum(p.margin for p in self.open_positions.values())

    # ---------------- trading ----------------
    def open_trade(self, symbol: str, side: str, entry: float, tp: float, sl: float, param_key: tuple):
        if symbol in self.open_positions or not self.can_open_new():
            return None
        risk_amount = self.balance * (self.risk_per_trade_pct / 100)
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return None

        # Portfoy bazli risk tavani: korelasyonlu (ayni anda birlikte hareket eden)
        # coinlerde bile toplam risk her zaman bakiyenin belirli bir yuzdesini asamaz.
        if self.open_risk_total() + risk_amount > self.balance * (self.max_portfolio_risk_pct / 100):
            return None

        size = risk_amount / risk_per_unit
        notional = entry * size
        margin = notional / self.leverage

        # Yetersiz teminat kontrolu: gercek bir borsada oldugu gibi, acik pozisyonlarin
        # toplam margin'i mevcut bakiyeyi asamaz.
        if self.used_margin() + margin > self.balance:
            return None

        open_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pos = Position(
            symbol=symbol, side=side, entry=entry, tp=tp, sl=sl,
            risk_amount=risk_amount, size=size, param_key=param_key,
            open_time=open_time, leverage=self.leverage, notional=notional, margin=margin,
            last_funding_time=open_time, last_price=entry,
        )
        self.open_positions[symbol] = pos
        self._save()
        return pos

    def accrue_funding(self, symbol: str, funding_rate: float):
        """OKX perpetual funding maliyetini/gelirini simule eder.

        Gercek borsada funding her FUNDING_INTERVAL_HOURS saatte bir sabit
        saatlerde kesilir; burada basitlik icin son kontrolden bu yana gecen
        sureyi periyotlara bolup oranli uyguluyoruz.
        """
        pos = self.open_positions.get(symbol)
        if pos is None or not funding_rate:
            return
        if not pos.last_funding_time:
            pos.last_funding_time = pos.open_time

        last = datetime.fromisoformat(pos.last_funding_time)
        now = datetime.now(timezone.utc)
        periods = int((now - last).total_seconds() // (FUNDING_INTERVAL_HOURS * 3600))
        if periods <= 0:
            return

        side_sign = 1 if pos.side == "BUY" else -1
        funding_cost = side_sign * pos.notional * funding_rate * periods
        self.balance -= funding_cost
        pos.funding_paid += funding_cost
        pos.last_funding_time = (last + timedelta(hours=FUNDING_INTERVAL_HOURS * periods)).isoformat(timespec="seconds")

    def check_and_close(self, symbol: str, last_price: float) -> Optional[dict]:
        pos = self.open_positions.get(symbol)
        if pos is None:
            return None

        # Mark-to-market: pozisyon kapanmasa bile anlik kar/zarari her taramada guncelle.
        if pos.side == "BUY":
            upnl = (last_price - pos.entry) * pos.size
        else:
            upnl = (pos.entry - last_price) * pos.size
        pos.last_price = last_price
        pos.unrealized_pnl = round(upnl, 4)
        pos.unrealized_r = round(upnl / pos.risk_amount, 3) if pos.risk_amount > 0 else 0.0

        hit_tp = (pos.side == "BUY" and last_price >= pos.tp) or (pos.side == "SELL" and last_price <= pos.tp)
        hit_sl = (pos.side == "BUY" and last_price <= pos.sl) or (pos.side == "SELL" and last_price >= pos.sl)

        if not hit_tp and not hit_sl:
            self._save()
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
            "leverage": pos.leverage,
            "funding_paid": round(pos.funding_paid, 4),
            "open_time": pos.open_time,
            "close_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "balance_after": round(self.balance, 4),
        }
        self.history.append(result)
        del self.open_positions[symbol]
        self._save()
        return result

    def stats(self) -> dict:
        unrealized_pnl = round(sum(p.unrealized_pnl for p in self.open_positions.values()), 2)
        funding_total = round(
            sum(t.get("funding_paid", 0.0) for t in self.history)
            + sum(p.funding_paid for p in self.open_positions.values()), 2
        )
        realized_pnl = round(self.balance - self.starting_balance, 2)
        if not self.history:
            return {
                "trades": 0, "win_rate": 0.0, "total_pnl": realized_pnl,
                "balance": round(self.balance, 2), "open_positions": len(self.open_positions),
                "unrealized_pnl": unrealized_pnl, "funding_total": funding_total,
            }
        wins = sum(1 for t in self.history if t["result"] == "TP")
        return {
            "trades": len(self.history),
            "win_rate": round(100 * wins / len(self.history), 1),
            "total_pnl": realized_pnl,
            "balance": round(self.balance, 2),
            "open_positions": len(self.open_positions),
            "unrealized_pnl": unrealized_pnl,
            "funding_total": funding_total,
        }
