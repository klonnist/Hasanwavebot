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
    initial_sl: float = 0.0            # acilistaki ORIJINAL stop (R hesaplari icin sabit referans)
    initial_risk_amount: float = 0.0   # acilistaki ORIJINAL risk tutari (blended R icin sabit referans)
    breakeven_done: bool = False       # SL basabasa cekildi mi
    partial_tp_done: bool = False      # kismi kar alindi mi
    partial_pnl_realized: float = 0.0  # kismi kar alimindan simdiye kadar gerceklesen toplam USDT


class PaperAccount:
    def __init__(self, state_path: str, starting_balance: float = 10000.0,
                 trade_margin: float = 500.0, max_open_positions: int = 5,
                 leverage: float = 1.0, max_portfolio_risk_pct: float = 8.0,
                 max_same_direction: int = 3,
                 breakeven_r: float = 1.0, partial_tp_r: float = 1.5,
                 partial_tp_fraction: float = 0.5, trail_giveback_pct: float = 0.5):
        self.state_path = state_path
        self.trade_margin = trade_margin
        self.max_open_positions = max_open_positions
        self.leverage = leverage
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        # Ayni anda ayni yonde (hepsi BUY veya hepsi SELL) acik olabilecek en
        # fazla pozisyon sayisi -- portfoy risk tavani TOPLAM riski sinirlar
        # ama yon korelasyonunu sinirlamaz: piyasa geneli tek yonde hareket
        # ettiginde (ozellikle VWAP gibi coklu coin'in ayni anda tetiklendigi
        # stratejilerde) tum acik pozisyonlar ayni yonde birikip piyasa ters
        # gittiginde hep birlikte vurabilir.
        self.max_same_direction = max_same_direction
        # Kademeli kar alma / trailing stop ayarlari:
        self.breakeven_r = breakeven_r            # bu R'a ulasinca SL basabasa cekilir
        self.partial_tp_r = partial_tp_r           # bu R'a ulasinca pozisyonun bir kismi kapatilir
        self.partial_tp_fraction = partial_tp_fraction  # kapatilacak kisim (0.5 = yarisi)
        self.trail_giveback_pct = trail_giveback_pct     # kismi sonrasi, gelecek kazancin en fazla bu oranini SL'e "geri verir"
        self.starting_balance = starting_balance
        self.balance: float = starting_balance
        self.open_positions: Dict[str, Position] = {}
        self.history: List[Dict] = []
        # Zaman kaynagi: canli modda gercek saat. Backtest bunu simule edilen
        # mum zamaniyla degistirir, boylece islem zaman damgalari gercek saati
        # degil test edilen tarihi gosterir.
        self.now_fn = lambda: datetime.now(timezone.utc)
        self._load()

    def _now_iso(self) -> str:
        return self.now_fn().isoformat(timespec="seconds")

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

    def same_direction_count(self, side: str) -> int:
        return sum(1 for p in self.open_positions.values() if p.side == side)

    # ---------------- trading ----------------
    def open_trade(self, symbol: str, side: str, entry: float, tp: float, sl: float, param_key: tuple):
        if symbol in self.open_positions or not self.can_open_new():
            return None
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return None

        # Yon konsantrasyonu tavani: acik pozisyonlarin hepsi ayni yonde
        # (hepsi BUY veya hepsi SELL) birikemez -- piyasa genelinin tek yonlu
        # hareketinde (ozellikle korelasyonlu coinlerde) toplu vurulma riskini
        # sinirlar. Portfoy risk tavanindan BAGIMSIZ bir kontroldur.
        if self.same_direction_count(side) >= self.max_same_direction:
            return None

        # Pozisyon buyuklugu artik bakiyenin yuzdesi degil, SABIT bir teminat
        # (trade_margin) ile kaldiracin carpimindan geliyor -- her islem ayni
        # miktarda "sermaye" kullanir, riske edilen tutar ise stop mesafesine
        # gore degisir (asagida hesaplanan risk_amount, sadece bilgi/rapor
        # ve portfoy risk tavani icin kullanilir).
        margin = self.trade_margin
        notional = margin * self.leverage
        size = notional / entry
        risk_amount = size * risk_per_unit

        # Yetersiz teminat kontrolu: gercek bir borsada oldugu gibi, acik pozisyonlarin
        # toplam margin'i mevcut bakiyeyi asamaz.
        if self.used_margin() + margin > self.balance:
            return None

        # Portfoy bazli risk tavani: korelasyonlu (ayni anda birlikte hareket eden)
        # coinlerde bile toplam risk her zaman bakiyenin belirli bir yuzdesini asamaz.
        if self.open_risk_total() + risk_amount > self.balance * (self.max_portfolio_risk_pct / 100):
            return None

        open_time = self._now_iso()
        pos = Position(
            symbol=symbol, side=side, entry=entry, tp=tp, sl=sl,
            risk_amount=risk_amount, size=size, param_key=param_key,
            open_time=open_time, leverage=self.leverage, notional=notional, margin=margin,
            last_funding_time=open_time, last_price=entry,
            initial_sl=sl, initial_risk_amount=risk_amount,
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
        now = self.now_fn()
        periods = int((now - last).total_seconds() // (FUNDING_INTERVAL_HOURS * 3600))
        if periods <= 0:
            return

        side_sign = 1 if pos.side == "BUY" else -1
        funding_cost = side_sign * pos.notional * funding_rate * periods
        self.balance -= funding_cost
        pos.funding_paid += funding_cost
        pos.last_funding_time = (last + timedelta(hours=FUNDING_INTERVAL_HOURS * periods)).isoformat(timespec="seconds")

    def _manage_position(self, pos: Position, last_price: float) -> Optional[dict]:
        """Basabasa cekme, kismi kar alma ve trailing stop -- pozisyon acikken
        her taramada calisir, pos.sl/size'i yerinde gunceller. Kismi kar
        alindiysa bilgi dondurur (check_and_close bunu history'e ekler)."""
        initial_sl = pos.initial_sl if pos.initial_sl else pos.sl
        initial_risk = pos.initial_risk_amount if pos.initial_risk_amount else pos.risk_amount
        risk_per_unit = abs(pos.entry - initial_sl)
        if risk_per_unit <= 0 or initial_risk <= 0:
            return None

        r = ((last_price - pos.entry) if pos.side == "BUY" else (pos.entry - last_price)) / risk_per_unit

        # 1) Basabasa (breakeven): belirli bir R'a ulasinca SL'i giris fiyatina cek --
        # boylece pozisyon en kotu ihtimalle "notr" kapanir, tekrar zarara donmez.
        if not pos.breakeven_done and r >= self.breakeven_r:
            pos.sl = max(pos.sl, pos.entry) if pos.side == "BUY" else min(pos.sl, pos.entry)
            pos.breakeven_done = True

        partial_event = None
        # 2) Kismi kar alma: belirli bir R'a ulasinca pozisyonun bir kismini
        # hemen nakde cevir, kalanini kosturmaya devam et.
        if not pos.partial_tp_done and r >= self.partial_tp_r:
            partial_size = pos.size * self.partial_tp_fraction
            partial_pnl = ((last_price - pos.entry) if pos.side == "BUY" else (pos.entry - last_price)) * partial_size
            self.balance += partial_pnl
            pos.partial_pnl_realized += partial_pnl
            pos.size -= partial_size
            pos.risk_amount = pos.size * risk_per_unit
            pos.notional = pos.entry * pos.size
            pos.margin = pos.notional / pos.leverage if pos.leverage else pos.notional
            pos.partial_tp_done = True
            partial_event = {"price": last_price, "pnl": partial_pnl}

        # 3) Trailing stop: kismi alindiktan sonra, fiyat ilerledikce SL'i
        # kazancin en fazla trail_giveback_pct kadarini geri verecek sekilde
        # yukari (BUY) / asagi (SELL) cek -- orijinal TP'yi hic asmaz (sinir/cati).
        if pos.partial_tp_done:
            if pos.side == "BUY":
                candidate = min(pos.entry + (last_price - pos.entry) * (1 - self.trail_giveback_pct), pos.tp)
                pos.sl = max(pos.sl, candidate)
            else:
                candidate = max(pos.entry - (pos.entry - last_price) * (1 - self.trail_giveback_pct), pos.tp)
                pos.sl = min(pos.sl, candidate)

        return partial_event

    def check_and_close(self, symbol: str, last_price: float) -> Optional[dict]:
        pos = self.open_positions.get(symbol)
        if pos is None:
            return None

        partial_event = self._manage_position(pos, last_price)
        if partial_event:
            record = {
                "symbol": pos.symbol, "side": pos.side, "result": "PARTIAL_TP",
                "entry": pos.entry, "exit": partial_event["price"],
                "pnl": round(partial_event["pnl"], 4), "r_multiple": round(self.partial_tp_r, 3),
                "param_key": pos.param_key, "leverage": pos.leverage, "funding_paid": 0.0,
                "open_time": pos.open_time, "close_time": self._now_iso(),
                "balance_after": round(self.balance, 4),
            }
            self.history.append(record)
            self._save()
            return record  # bu tur sadece kismi kar alindi -- TP/SL kontrolu bir sonraki taramada

        # Mark-to-market: pozisyon kapanmasa bile anlik kar/zarari her taramada guncelle
        # (kismi alindiysa KALAN boyut uzerinden).
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
            final_leg_pnl = (exit_price - pos.entry) * pos.size
        else:
            final_leg_pnl = (pos.entry - exit_price) * pos.size

        self.balance += final_leg_pnl
        # pnl/r_multiple, kismi kar alimi + son bacagi birlikte yansitir --
        # "bu islem toplamda ne kazandirdi" sorusunun cevabi budur.
        total_pnl = pos.partial_pnl_realized + final_leg_pnl
        initial_risk = pos.initial_risk_amount if pos.initial_risk_amount else pos.risk_amount
        total_r = total_pnl / initial_risk if initial_risk > 0 else 0.0

        result = {
            "symbol": pos.symbol,
            "side": pos.side,
            "entry": pos.entry,
            "exit": exit_price,
            "result": "TP" if hit_tp else "SL",
            "pnl": round(total_pnl, 4),
            "final_leg_pnl": round(final_leg_pnl, 4),
            "partial_taken": pos.partial_tp_done,
            "r_multiple": round(total_r, 3),
            "param_key": pos.param_key,
            "leverage": pos.leverage,
            "funding_paid": round(pos.funding_paid, 4),
            "open_time": pos.open_time,
            "close_time": self._now_iso(),
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
        # PARTIAL_TP kayitlari tamamlanmis bir islem degil (pozisyon hala acik),
        # bu yuzden islem sayisi / kazanma oranindan haric tutuluyor.
        completed = [t for t in self.history if t["result"] in ("TP", "SL")]
        if not completed:
            return {
                "trades": 0, "win_rate": 0.0, "total_pnl": realized_pnl,
                "balance": round(self.balance, 2), "open_positions": len(self.open_positions),
                "unrealized_pnl": unrealized_pnl, "funding_total": funding_total,
            }
        # Kazanma/kayip GERCEK pnl isaretine gore belirlenir -- trailing stop
        # kar durumundayken tetiklenirse (sonuc alani "SL" olsa bile) bu hala
        # bir kazancdir, "SL" etiketi sadece hangi fiyat seviyesine carptigini gosterir.
        wins = sum(1 for t in completed if t["pnl"] >= 0)
        return {
            "trades": len(completed),
            "win_rate": round(100 * wins / len(completed), 1),
            "total_pnl": realized_pnl,
            "balance": round(self.balance, 2),
            "open_positions": len(self.open_positions),
            "unrealized_pnl": unrealized_pnl,
            "funding_total": funding_total,
        }
