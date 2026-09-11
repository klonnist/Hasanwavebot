/* Client-side port of {wave_detector,vwap_detector,learner,paper_account,backtest}.py
 * so the dashboard can run an ad-hoc backtest directly in the visitor's browser
 * (OKX's public market-data API allows cross-origin requests). No API key, no
 * server. Mirrors the Python logic bar-for-bar so results match the CLI tool.
 */
const HB_OKX_BASE = "https://www.okx.com";

const HB_BAR_MS = {
  "1m": 60_000, "5m": 5 * 60_000, "15m": 15 * 60_000, "30m": 30 * 60_000,
  "1h": 3_600_000, "2h": 2 * 3_600_000, "4h": 4 * 3_600_000, "1d": 86_400_000,
};
const HB_OKX_BAR = { "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1D" };

const HB_POPULAR_COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "TON", "ETHFI", "PENGU", "NEAR"];

function hbInstId(base) { return `${base}-USDT-SWAP`; }
function hbSleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function hbRound(x, n) { const f = 10 ** n; return Math.round(x * f) / f; }

async function hbFetchHistoryCandles(instId, okxBar, startMs, endMs, pause = 150) {
  const rows = [];
  const seen = new Set();
  let after = null;
  while (true) {
    const params = new URLSearchParams({ instId, bar: okxBar, limit: "100" });
    if (after !== null) params.set("after", String(after));
    const res = await fetch(`${HB_OKX_BASE}/api/v5/market/history-candles?${params}`);
    const payload = await res.json();
    if (payload.code !== "0") throw new Error(`OKX API hatasi: ${payload.msg || payload.code}`);
    const batch = payload.data;
    if (!batch || !batch.length) break;
    const fresh = batch.filter(r => !seen.has(r[0]));
    if (!fresh.length) break;
    fresh.forEach(r => seen.add(r[0]));
    rows.push(...fresh);
    const oldestTs = Math.min(...batch.map(r => Number(r[0])));
    after = oldestTs;
    if (oldestTs <= startMs) break;
    await hbSleep(pause);
  }
  return rows
    .map(r => ({ timestamp: Number(r[0]), open: Number(r[1]), high: Number(r[2]), low: Number(r[3]), close: Number(r[4]), volume: Number(r[5]) }))
    .filter(c => c.timestamp >= startMs && c.timestamp <= endMs)
    .sort((a, b) => a.timestamp - b.timestamp);
}

// ---------------- wave_detector.py port ----------------

function hbComputeTR(candles) {
  const tr = new Array(candles.length);
  for (let i = 0; i < candles.length; i++) {
    const { high, low } = candles[i];
    if (i === 0) { tr[i] = high - low; continue; }
    const prevClose = candles[i - 1].close;
    tr[i] = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
  }
  return tr;
}

function hbAtrPct(candles, period = 14) {
  const n = candles.length;
  if (n < period) return 0;
  const tr = hbComputeTR(candles);
  const last = tr.slice(n - period, n);
  const atr = last.reduce((a, b) => a + b, 0) / period;
  const lastClose = candles[n - 1].close;
  if (lastClose <= 0) return 0;
  return (atr / lastClose) * 100;
}

function hbZigzagPivots(candles, deviationPct) {
  const highs = candles.map(c => c.high), lows = candles.map(c => c.low), times = candles.map(c => c.timestamp);
  const pivots = [];
  let trend = null;
  const startPrice = (highs[0] + lows[0]) / 2;
  let refHigh = highs[0], refLow = lows[0], refHighIdx = 0, refLowIdx = 0;
  let lastPivotPrice = startPrice, lastPivotIdx = 0, lastPivotKind = "L";

  for (let i = 1; i < candles.length; i++) {
    const h = highs[i], l = lows[i];
    if (trend === null) {
      if (h > refHigh) { refHigh = h; refHighIdx = i; }
      if (l < refLow) { refLow = l; refLowIdx = i; }
      if (refHigh >= startPrice * (1 + deviationPct / 100) && refHighIdx > refLowIdx) {
        trend = "up";
        pivots.push({ idx: refLowIdx, price: refLow, kind: "L", time: times[refLowIdx] });
        lastPivotPrice = refHigh; lastPivotIdx = refHighIdx; lastPivotKind = "H";
      } else if (refLow <= startPrice * (1 - deviationPct / 100) && refLowIdx > refHighIdx) {
        trend = "down";
        pivots.push({ idx: refHighIdx, price: refHigh, kind: "H", time: times[refHighIdx] });
        lastPivotPrice = refLow; lastPivotIdx = refLowIdx; lastPivotKind = "L";
      }
      continue;
    }
    if (trend === "up") {
      if (h > lastPivotPrice) { lastPivotPrice = h; lastPivotIdx = i; }
      else if (l <= lastPivotPrice * (1 - deviationPct / 100)) {
        pivots.push({ idx: lastPivotIdx, price: lastPivotPrice, kind: "H", time: times[lastPivotIdx] });
        trend = "down"; lastPivotPrice = l; lastPivotIdx = i; lastPivotKind = "L";
      }
    } else {
      if (l < lastPivotPrice) { lastPivotPrice = l; lastPivotIdx = i; }
      else if (h >= lastPivotPrice * (1 + deviationPct / 100)) {
        pivots.push({ idx: lastPivotIdx, price: lastPivotPrice, kind: "L", time: times[lastPivotIdx] });
        trend = "up"; lastPivotPrice = h; lastPivotIdx = i; lastPivotKind = "H";
      }
    }
  }
  pivots.push({ idx: lastPivotIdx, price: lastPivotPrice, kind: lastPivotKind, time: times[lastPivotIdx] });
  return pivots;
}

function hbDetectWave3Setup(pivots, params) {
  const confirmed = pivots.slice(0, -1);
  if (confirmed.length < 3) return null;
  const [p0, p1, p2] = confirmed.slice(-3);

  if (p0.kind === "L" && p1.kind === "H" && p2.kind === "L") {
    const wave1Len = p1.price - p0.price;
    if (wave1Len <= 0) return null;
    const retrace = (p1.price - p2.price) / wave1Len;
    if (p2.price <= p0.price) return null;
    if (!(retrace >= params.retraceMin && retrace <= params.retraceMax)) return null;
    return { direction: "BUY", p0, p1, p2, wave1Len };
  }
  if (p0.kind === "H" && p1.kind === "L" && p2.kind === "H") {
    const wave1Len = p0.price - p1.price;
    if (wave1Len <= 0) return null;
    const retrace = (p2.price - p1.price) / wave1Len;
    if (p2.price >= p0.price) return null;
    if (!(retrace >= params.retraceMin && retrace <= params.retraceMax)) return null;
    return { direction: "SELL", p0, p1, p2, wave1Len };
  }
  return null;
}

function hbBuildSignalLevels(setup, params, lastClose) {
  const { p2, wave1Len, direction } = setup;
  const entry = lastClose;
  let tp, sl;
  if (direction === "BUY") { tp = p2.price + wave1Len * params.tpMult; sl = p2.price - wave1Len * params.slMult; }
  else { tp = p2.price - wave1Len * params.tpMult; sl = p2.price + wave1Len * params.slMult; }
  return { entry, tp, sl };
}

// ---------------- vwap_detector.py port ----------------

function hbComputeVwapBands(candles, stdWindow = 20) {
  const n = candles.length;
  const typical = candles.map(c => (c.high + c.low + c.close) / 3);
  const vwap = new Array(n), dist = new Array(n);
  let cumVol = 0, cumTPV = 0;
  for (let i = 0; i < n; i++) {
    cumVol += candles[i].volume;
    cumTPV += typical[i] * candles[i].volume;
    vwap[i] = cumVol !== 0 ? cumTPV / cumVol : NaN;
    dist[i] = typical[i] - vwap[i];
  }
  const std = new Array(n).fill(NaN);
  for (let i = stdWindow - 1; i < n; i++) {
    const slice = dist.slice(i - stdWindow + 1, i + 1);
    const mean = slice.reduce((a, b) => a + b, 0) / stdWindow;
    const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / (stdWindow - 1);
    std[i] = Math.sqrt(variance);
  }
  return { vwap, dist, std };
}

function hbDetectVwapSignal(candles, params) {
  if (candles.length < 25) return null;
  const { vwap, dist, std } = hbComputeVwapBands(candles, 20);
  const n = candles.length;
  const lastStd = std[n - 1];
  if (!(lastStd > 0)) return null;

  const lastClose = candles[n - 1].close, prevClose = candles[n - 2].close;
  const lastVwap = vwap[n - 1];
  const z = dist[n - 1] / lastStd;
  const band = lastStd * params.bandMult;

  if (z <= -params.bandMult && lastClose > prevClose) {
    const entry = lastClose;
    const tp = entry + Math.max(lastVwap - entry, 0) * params.tpMult;
    const sl = entry - band * params.slMult;
    if (!(sl < entry && entry < tp)) return null;
    return { direction: "BUY", entry, tp, sl };
  }
  if (z >= params.bandMult && lastClose < prevClose) {
    const entry = lastClose;
    const tp = entry - Math.max(entry - lastVwap, 0) * params.tpMult;
    const sl = entry + band * params.slMult;
    if (!(tp < entry && entry < sl)) return null;
    return { direction: "SELL", entry, tp, sl };
  }
  return null;
}

// ---------------- donchian_detector.py port ----------------

function hbComputeDonchianBands(candles, channelPeriod = 20) {
  const n = candles.length;
  const upper = new Array(n).fill(NaN), lower = new Array(n).fill(NaN);
  for (let i = channelPeriod; i < n; i++) {
    // SU ANKI mum (i) HARIC, kendisinden onceki channelPeriod mumun high/low'u
    let hi = -Infinity, lo = Infinity;
    for (let j = i - channelPeriod; j < i; j++) {
      if (candles[j].high > hi) hi = candles[j].high;
      if (candles[j].low < lo) lo = candles[j].low;
    }
    upper[i] = hi;
    lower[i] = lo;
  }
  return { upper, lower };
}

function hbDetectDonchianSignal(candles, params) {
  const n = candles.length;
  if (n < params.channelPeriod + 2) return null;
  const { upper, lower } = hbComputeDonchianBands(candles, params.channelPeriod);
  const lastUpper = upper[n - 1], lastLower = lower[n - 1];
  const prevUpper = upper[n - 2], prevLower = lower[n - 2];
  if ([lastUpper, lastLower, prevUpper, prevLower].some(v => Number.isNaN(v))) return null;

  const width = lastUpper - lastLower;
  if (!(width > 0)) return null;

  const lastClose = candles[n - 1].close, prevClose = candles[n - 2].close;

  if (lastClose > lastUpper && prevClose <= prevUpper) {
    const entry = lastClose;
    const tp = entry + width * params.tpMult;
    const sl = entry - width * params.slMult;
    if (!(sl < entry && entry < tp)) return null;
    return { direction: "BUY", entry, tp, sl };
  }
  if (lastClose < lastLower && prevClose >= prevLower) {
    const entry = lastClose;
    const tp = entry - width * params.tpMult;
    const sl = entry + width * params.slMult;
    if (!(tp < entry && entry < sl)) return null;
    return { direction: "SELL", entry, tp, sl };
  }
  return null;
}

// ---------------- learner.py port ----------------

const HB_WAVE_DEVIATIONS = [0.8, 1.2, 1.8, 2.5];
const HB_WAVE_TP_MULTS = [1.272, 1.618, 2.0];
const HB_WAVE_SL_MULT = 0.15;
const HB_VWAP_BAND_MULTS = [1.5, 2.0, 2.5];
const HB_VWAP_TP_MULTS = [0.5, 0.75, 1.0];
const HB_VWAP_SL_MULT = 0.5;
const HB_DONCHIAN_PERIODS = [20, 40, 55];
const HB_DONCHIAN_TP_MULTS = [1.0, 1.5, 2.0];
const HB_DONCHIAN_SL_MULT = 1.0;
const HB_MIN_SYMBOL_SAMPLES = 3;

function hbBuildWaveGrid() {
  return HB_WAVE_DEVIATIONS.flatMap(dev => HB_WAVE_TP_MULTS.map(tp => ({
    deviationPct: dev, tpMult: tp, slMult: HB_WAVE_SL_MULT, retraceMin: 0.236, retraceMax: 0.886,
  })));
}
function hbBuildVwapGrid() {
  return HB_VWAP_BAND_MULTS.flatMap(band => HB_VWAP_TP_MULTS.map(tp => ({
    bandMult: band, tpMult: tp, slMult: HB_VWAP_SL_MULT,
  })));
}
function hbBuildDonchianGrid() {
  return HB_DONCHIAN_PERIODS.flatMap(period => HB_DONCHIAN_TP_MULTS.map(tp => ({
    channelPeriod: period, tpMult: tp, slMult: HB_DONCHIAN_SL_MULT,
  })));
}
function hbParamKey(p, strategy) {
  if (strategy === "wave") return [hbRound(p.deviationPct, 3), hbRound(p.tpMult, 3), hbRound(p.slMult, 3)];
  if (strategy === "donchian") return [hbRound(p.channelPeriod, 3), hbRound(p.tpMult, 3), hbRound(p.slMult, 3)];
  return [hbRound(p.bandMult, 3), hbRound(p.tpMult, 3), hbRound(p.slMult, 3)];
}

class HbLearner {
  constructor(strategy, epsilon = 0.25) {
    this.strategy = strategy;
    this.epsilon = epsilon;
    this.grid = strategy === "wave" ? hbBuildWaveGrid() : strategy === "donchian" ? hbBuildDonchianGrid() : hbBuildVwapGrid();
    this.stats = new Map();
    this.symbolStats = new Map();
  }
  select(symbol) {
    const unexplored = this.grid.filter(p => !this.symbolStats.has(`${symbol}|${JSON.stringify(hbParamKey(p, this.strategy))}`));
    if (unexplored.length) return unexplored[Math.floor(Math.random() * unexplored.length)];
    if (Math.random() < this.epsilon) return this.grid[Math.floor(Math.random() * this.grid.length)];
    const scoreOf = (p) => {
      const key = JSON.stringify(hbParamKey(p, this.strategy));
      const symS = this.symbolStats.get(`${symbol}|${key}`);
      if (symS && symS.n >= HB_MIN_SYMBOL_SAMPLES) return symS.rewardSum / symS.n;
      const gS = this.stats.get(key);
      return gS && gS.n ? gS.rewardSum / gS.n : 0;
    };
    let best = this.grid[0], bestScore = -Infinity;
    for (const p of this.grid) { const s = scoreOf(p); if (s > bestScore) { bestScore = s; best = p; } }
    return best;
  }
  update(symbol, keyArr, reward, win) {
    const key = JSON.stringify(keyArr);
    for (const [map, k] of [[this.stats, key], [this.symbolStats, `${symbol}|${key}`]]) {
      const s = map.get(k) || { n: 0, rewardSum: 0, wins: 0, losses: 0 };
      s.n++; s.rewardSum += reward; win ? s.wins++ : s.losses++;
      map.set(k, s);
    }
  }
  leaderboardRows(topN = 12) {
    const rows = [];
    for (const p of this.grid) {
      const key = JSON.stringify(hbParamKey(p, this.strategy));
      const s = this.stats.get(key);
      if (!s || s.n === 0) continue;
      rows.push({
        first: hbParamKey(p, this.strategy)[0], tp_mult: p.tpMult, n: s.n,
        win_rate: hbRound((100 * s.wins) / s.n, 1), avg_r: hbRound(s.rewardSum / s.n, 3),
      });
    }
    rows.sort((a, b) => b.avg_r - a.avg_r);
    return rows.slice(0, topN);
  }
}

// ---------------- paper_account.py port (funding excluded, matches backtest.py) ----------------

class HbPaperAccount {
  constructor(cfg) {
    Object.assign(this, {
      tradeMargin: 500, maxOpenPositions: 5, leverage: 1, maxPortfolioRiskPct: 8, maxSameDirection: 3,
      breakevenR: 1.0, partialTpR: 1.5, partialTpFraction: 0.5, trailGivebackPct: 0.5,
    }, cfg);
    this.startingBalance = cfg.startingBalance;
    this.balance = cfg.startingBalance;
    this.openPositions = new Map();
    this.history = [];
    this.nowFn = () => new Date();
  }
  nowIso() { return this.nowFn().toISOString(); }
  hasOpenPosition(symbol) { return this.openPositions.has(symbol); }
  canOpenNew() { return this.openPositions.size < this.maxOpenPositions; }
  openRiskTotal() { let s = 0; for (const p of this.openPositions.values()) s += p.riskAmount; return s; }
  usedMargin() { let s = 0; for (const p of this.openPositions.values()) s += p.margin; return s; }
  sameDirectionCount(side) { let c = 0; for (const p of this.openPositions.values()) if (p.side === side) c++; return c; }

  openTrade(symbol, side, entry, tp, sl, paramKeyArr) {
    if (this.openPositions.has(symbol) || !this.canOpenNew()) return null;
    const riskPerUnit = Math.abs(entry - sl);
    if (riskPerUnit <= 0) return null;
    if (this.sameDirectionCount(side) >= this.maxSameDirection) return null;
    const margin = this.tradeMargin;
    const notional = margin * this.leverage;
    const size = notional / entry;
    const riskAmount = size * riskPerUnit;
    if (this.usedMargin() + margin > this.balance) return null;
    if (this.openRiskTotal() + riskAmount > this.balance * (this.maxPortfolioRiskPct / 100)) return null;

    const openTime = this.nowIso();
    const pos = {
      symbol, side, entry, tp, sl, riskAmount, size, paramKey: paramKeyArr, openTime,
      leverage: this.leverage, notional, margin, lastPrice: entry,
      unrealizedPnl: 0, unrealizedR: 0, initialSl: sl, initialRiskAmount: riskAmount,
      breakevenDone: false, partialTpDone: false, partialPnlRealized: 0,
    };
    this.openPositions.set(symbol, pos);
    return pos;
  }

  managePosition(pos, lastPrice) {
    const initialSl = pos.initialSl || pos.sl;
    const initialRisk = pos.initialRiskAmount || pos.riskAmount;
    const riskPerUnit = Math.abs(pos.entry - initialSl);
    if (riskPerUnit <= 0 || initialRisk <= 0) return null;
    const r = ((pos.side === "BUY" ? (lastPrice - pos.entry) : (pos.entry - lastPrice))) / riskPerUnit;

    if (!pos.breakevenDone && r >= this.breakevenR) {
      pos.sl = pos.side === "BUY" ? Math.max(pos.sl, pos.entry) : Math.min(pos.sl, pos.entry);
      pos.breakevenDone = true;
    }
    let partialEvent = null;
    if (!pos.partialTpDone && r >= this.partialTpR) {
      const partialSize = pos.size * this.partialTpFraction;
      const partialPnl = (pos.side === "BUY" ? (lastPrice - pos.entry) : (pos.entry - lastPrice)) * partialSize;
      this.balance += partialPnl;
      pos.partialPnlRealized += partialPnl;
      pos.size -= partialSize;
      pos.riskAmount = pos.size * riskPerUnit;
      pos.notional = pos.entry * pos.size;
      pos.margin = pos.leverage ? pos.notional / pos.leverage : pos.notional;
      pos.partialTpDone = true;
      // SL'i DOGRUDAN kismi kar alinan fiyata cek (paper_account.py ile ayni) --
      // kalan pozisyon en kotu ihtimalle de kismi ile ayni seviyeden kapanir.
      pos.sl = pos.side === "BUY" ? Math.max(pos.sl, lastPrice) : Math.min(pos.sl, lastPrice);
      partialEvent = { price: lastPrice, pnl: partialPnl };
    }
    if (pos.partialTpDone) {
      if (pos.side === "BUY") {
        const candidate = Math.min(pos.entry + (lastPrice - pos.entry) * (1 - this.trailGivebackPct), pos.tp);
        pos.sl = Math.max(pos.sl, candidate);
      } else {
        const candidate = Math.max(pos.entry - (pos.entry - lastPrice) * (1 - this.trailGivebackPct), pos.tp);
        pos.sl = Math.min(pos.sl, candidate);
      }
    }
    return partialEvent;
  }

  checkAndClose(symbol, lastPrice) {
    const pos = this.openPositions.get(symbol);
    if (!pos) return null;

    const partialEvent = this.managePosition(pos, lastPrice);
    if (partialEvent) {
      const record = {
        symbol: pos.symbol, side: pos.side, result: "PARTIAL_TP",
        entry: pos.entry, exit: partialEvent.price,
        pnl: hbRound(partialEvent.pnl, 4), r_multiple: hbRound(this.partialTpR, 3),
        param_key: pos.paramKey, leverage: pos.leverage, funding_paid: 0,
        open_time: pos.openTime, close_time: this.nowIso(), balance_after: hbRound(this.balance, 4),
      };
      this.history.push(record);
      return record;
    }

    const upnl = pos.side === "BUY" ? (lastPrice - pos.entry) * pos.size : (pos.entry - lastPrice) * pos.size;
    pos.lastPrice = lastPrice;
    pos.unrealizedPnl = hbRound(upnl, 4);
    pos.unrealizedR = pos.riskAmount > 0 ? hbRound(upnl / pos.riskAmount, 3) : 0;

    const hitTp = (pos.side === "BUY" && lastPrice >= pos.tp) || (pos.side === "SELL" && lastPrice <= pos.tp);
    const hitSl = (pos.side === "BUY" && lastPrice <= pos.sl) || (pos.side === "SELL" && lastPrice >= pos.sl);
    if (!hitTp && !hitSl) return null;

    const exitPrice = hitTp ? pos.tp : pos.sl;
    const finalLegPnl = pos.side === "BUY" ? (exitPrice - pos.entry) * pos.size : (pos.entry - exitPrice) * pos.size;
    this.balance += finalLegPnl;
    const totalPnl = pos.partialPnlRealized + finalLegPnl;
    const initialRisk = pos.initialRiskAmount || pos.riskAmount;
    const totalR = initialRisk > 0 ? totalPnl / initialRisk : 0;

    const result = {
      symbol: pos.symbol, side: pos.side, entry: pos.entry, exit: exitPrice,
      result: hitTp ? "TP" : "SL", pnl: hbRound(totalPnl, 4), final_leg_pnl: hbRound(finalLegPnl, 4),
      partial_taken: pos.partialTpDone, r_multiple: hbRound(totalR, 3),
      param_key: pos.paramKey, leverage: pos.leverage, funding_paid: 0,
      open_time: pos.openTime, close_time: this.nowIso(), balance_after: hbRound(this.balance, 4),
    };
    this.history.push(result);
    this.openPositions.delete(symbol);
    return result;
  }

  stats() {
    const unrealizedPnl = hbRound([...this.openPositions.values()].reduce((s, p) => s + p.unrealizedPnl, 0), 2);
    const realizedPnl = hbRound(this.balance - this.startingBalance, 2);
    const completed = this.history.filter(t => t.result === "TP" || t.result === "SL");
    if (!completed.length) {
      return { trades: 0, win_rate: 0, total_pnl: realizedPnl, balance: hbRound(this.balance, 2), open_positions: this.openPositions.size, unrealized_pnl: unrealizedPnl };
    }
    const wins = completed.filter(t => t.pnl >= 0).length;
    return {
      trades: completed.length, win_rate: hbRound((100 * wins) / completed.length, 1),
      total_pnl: realizedPnl, balance: hbRound(this.balance, 2),
      open_positions: this.openPositions.size, unrealized_pnl: unrealizedPnl,
    };
  }
}

// ---------------- backtest.py port ----------------

function hbReplaySymbol(candles, symbol, strategy, window, learner, account) {
  for (let i = window; i < candles.length; i++) {
    const windowDf = candles.slice(i - window, i + 1);
    const barClose = candles[i].close;
    const barTs = candles[i].timestamp;
    account.nowFn = () => new Date(barTs);

    if (account.hasOpenPosition(symbol)) {
      const result = account.checkAndClose(symbol, barClose);
      if (result && (result.result === "TP" || result.result === "SL")) {
        learner.update(symbol, result.param_key, result.r_multiple, result.pnl >= 0);
      }
    } else {
      const params = learner.select(symbol);
      let direction = null, entry, tp, sl;
      if (strategy === "wave") {
        const volPct = hbAtrPct(windowDf);
        const effectiveDev = Math.max(params.deviationPct * volPct, 0.05);
        const pivots = hbZigzagPivots(windowDf, effectiveDev);
        const setup = hbDetectWave3Setup(pivots, params);
        if (setup) { const lv = hbBuildSignalLevels(setup, params, barClose); entry = lv.entry; tp = lv.tp; sl = lv.sl; direction = setup.direction; }
      } else if (strategy === "donchian") {
        const sig = hbDetectDonchianSignal(windowDf, params);
        if (sig) { direction = sig.direction; entry = sig.entry; tp = sig.tp; sl = sig.sl; }
      } else {
        const sig = hbDetectVwapSignal(windowDf, params);
        if (sig) { direction = sig.direction; entry = sig.entry; tp = sig.tp; sl = sig.sl; }
      }
      if (direction) {
        const valid = direction === "BUY" ? (sl < entry && entry < tp) : (tp < entry && entry < sl);
        if (valid) account.openTrade(symbol, direction, entry, tp, sl, hbParamKey(params, strategy));
      }
    }
  }
}

function hbComputeMaxDrawdown(history, startingBalance) {
  if (!history.length) return 0;
  const ordered = [...history].sort((a, b) => (a.close_time < b.close_time ? -1 : 1));
  let peak = startingBalance, maxDd = 0;
  for (const t of ordered) {
    const bal = t.balance_after ?? peak;
    peak = Math.max(peak, bal);
    if (peak > 0) maxDd = Math.max(maxDd, (peak - bal) / peak);
  }
  return maxDd * 100;
}

function hbBuildEquityCurve(history, startingBalance, maxPoints = 300) {
  const ordered = [...history].sort((a, b) => (a.close_time < b.close_time ? -1 : 1));
  const points = [{ t: ordered.length ? ordered[0].open_time : null, balance: hbRound(startingBalance, 2) }];
  for (const t of ordered) points.push({ t: t.close_time, balance: hbRound(t.balance_after ?? startingBalance, 2) });
  if (points.length > maxPoints) {
    const step = points.length / maxPoints;
    const thinned = [];
    for (let i = 0; i < maxPoints - 1; i++) thinned.push(points[Math.floor(i * step)]);
    thinned.push(points[points.length - 1]);
    return thinned;
  }
  return points;
}

async function runHbBacktest({
  strategy, timeframe, symbols, sinceMs, untilMs, window = 150,
  balance = 10000, tradeMargin = 500, leverage = 1, maxOpen = 5, maxPortfolioRiskPct = 8,
  maxSameDirection = 3, breakevenR = 1.0, partialTpR = 1.5, partialTpFraction = 0.5,
  trailGivebackPct = 0.5, epsilon = 0.25, onProgress,
}) {
  const account = new HbPaperAccount({
    startingBalance: balance, tradeMargin, maxOpenPositions: maxOpen, leverage,
    maxPortfolioRiskPct, maxSameDirection, breakevenR, partialTpR, partialTpFraction, trailGivebackPct,
  });
  const learner = new HbLearner(strategy, epsilon);
  const okxBar = HB_OKX_BAR[timeframe];
  const warmupMs = HB_BAR_MS[timeframe] * window;

  for (const symbol of symbols) {
    onProgress?.(`${symbol}: OKX'ten veri çekiliyor…`);
    let candles;
    try {
      candles = await hbFetchHistoryCandles(hbInstId(symbol), okxBar, sinceMs - warmupMs, untilMs);
    } catch (e) {
      onProgress?.(`${symbol}: veri alınamadı, atlandı (${e.message})`);
      continue;
    }
    if (candles.length < window + 5) {
      onProgress?.(`${symbol}: yeterli geçmiş veri yok (${candles.length} mum), atlandı`);
      continue;
    }
    onProgress?.(`${symbol}: taranıyor (${candles.length} mum)…`);
    await hbSleep(0);
    hbReplaySymbol(candles, symbol, strategy, window, learner, account);
    await hbSleep(0);
  }

  const s = account.stats();
  const dd = hbComputeMaxDrawdown(account.history, account.startingBalance);
  const now = new Date();
  const dateFrom = new Date(sinceMs).toISOString().slice(0, 10);
  const dateTo = new Date(untilMs).toISOString().slice(0, 10);

  const summary = {
    starting_balance: hbRound(account.startingBalance, 2),
    final_balance: s.balance,
    total_pnl: s.total_pnl,
    total_pnl_pct: account.startingBalance ? hbRound((100 * s.total_pnl) / account.startingBalance, 2) : 0,
    trades: s.trades, win_rate: s.win_rate, max_drawdown_pct: hbRound(dd, 2), open_at_end: s.open_positions,
  };

  return {
    id: `client-${now.getTime()}`,
    created_at: now.toISOString(),
    config: {
      strategy, timeframe, date_from: dateFrom, date_to: dateTo, symbols, window,
      starting_balance: account.startingBalance, trade_margin: account.tradeMargin, leverage: account.leverage,
      max_open: account.maxOpenPositions, max_portfolio_risk_pct: account.maxPortfolioRiskPct,
      max_same_direction: account.maxSameDirection, breakeven_r: account.breakevenR,
      partial_tp_r: account.partialTpR, partial_tp_fraction: account.partialTpFraction,
      trail_giveback_pct: account.trailGivebackPct, epsilon: learner.epsilon,
    },
    summary,
    leaderboard: learner.leaderboardRows(12),
    equity: hbBuildEquityCurve(account.history, account.startingBalance),
    trades: [...account.history].sort((a, b) => (a.close_time < b.close_time ? -1 : 1)).slice(-60),
  };
}
