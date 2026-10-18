"""把 data/ 裡的原始資料算成儀表板要用的指標。

每檔股票一列（欄位見 COLUMNS），另外附上個股走勢序列、大盤序列與內部人申報清單。
"""
from datetime import date, timedelta

from . import indicators as ind
from .backtest import Panel
from .store import Store

MARKETS = ("twse", "tpex")
MARKET_LABEL = {"twse": "上市", "tpex": "上櫃"}
WINDOW = 40  # 個股走勢圖顯示的交易日數
INSTI_WINDOW = 20
LONG_WINDOW = 260  # 技術指標（52 週高低點、季線）需要約一年的股價

COLUMNS = [
    "code", "name", "market", "industry",
    "close", "chg_pct", "ret5", "ret20", "ma20_gap", "vol", "vol5", "value",
    "f1", "f5", "f20", "fs", "t1", "t5", "t20", "ts", "d1", "t5cap", "f5vol",
    "m_bal", "m_chg", "m_chg5", "m_chg5_pct", "s_bal", "s_ratio",
    "pe", "yld", "pb",
    "rev", "rev_yoy", "rev_mom", "rev_cum_yoy", "rev_month",
    "big", "big_chg", "b400", "retail", "holders", "holders_chg",
    "ins_n", "ins_lots",
    # 技術面（還原股價）
    "ret60", "ma5", "ma20", "ma60", "align", "hi52", "lo52", "pos52", "dist_hi", "new_hl",
    "vol_ratio", "rs", "rsi", "dif", "dea", "macd_state",
    # 基本面延伸
    "rev_high", "rev_streak", "pe_pct",
]
TECH_KEYS = ["ret60", "ma5", "ma20", "ma60", "align", "hi52", "lo52", "pos52", "dist_hi", "new_hl",
             "vol_ratio", "rs", "rsi", "dif", "dea", "macd_state"]


def load_docs(store, count=LONG_WINDOW):
    docs = [store.load_day(d) for d in store.day_dates()[-count:]]
    return [d for d in docs if any("quotes" in d.get(m, {}) for m in MARKETS)]


def _num(value, digits=2):
    return None if value is None or ind.isnan(value) else round(value, digits)


def technicals(panel, universe):
    """每檔的技術指標，以及每天的漲跌家數、創 52 週新高／新低家數。

    全部用還原股價（漲跌價差串成的指數 × 最新收盤），除權息不會造成假的跌破。
    """
    T, nan = panel.T, ind.NAN
    adv, dec, flat, highs, lows = ([0] * T for _ in range(5))
    metrics, ret60 = {}, {}
    for code, s in panel.series.items():
        closes, idx = s["close"], panel.index[code]
        last = next((i for i in range(T - 1, -1, -1) if not ind.isnan(closes[i])), None)
        if last is None:
            continue
        scale = closes[last] / idx[last]
        adj = [v * scale if not ind.isnan(v) else nan for v in idx]
        prior_hi, prior_lo = ind.prior_extremes(adj, 250, min_days=120)
        for i in range(T):
            if ind.isnan(closes[i]):
                continue
            chg = s["chg"][i]
            if not ind.isnan(chg):
                adv[i] += chg > 0
                dec[i] += chg < 0
                flat[i] += chg == 0
            if not ind.isnan(prior_hi[i]):
                highs[i] += adj[i] >= prior_hi[i]
                lows[i] += adj[i] <= prior_lo[i]
        if code not in universe:
            continue

        t, price = T - 1, adj[T - 1]
        traded = not ind.isnan(closes[t])
        ma5, ma20, ma60 = (ind.last_mean(adj, n) for n in (5, 20, 60))
        align = None
        if not ind.isnan(ma60):
            align = 1 if price > ma5 > ma20 > ma60 else -1 if price < ma5 < ma20 < ma60 else 0
        hi52 = lo52 = pos52 = dist_hi = new_hl = None
        if not ind.isnan(prior_hi[t]):
            hi52, lo52 = max(prior_hi[t], price), min(prior_lo[t], price)
            pos52 = (price - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None
            dist_hi = (price / hi52 - 1) * 100
            new_hl = (1 if price >= prior_hi[t] else -1 if price <= prior_lo[t] else 0) if traded else 0
        prev_vols = [v for v in s["vol"][max(0, t - 20): t] if not ind.isnan(v)]
        avg_vol = sum(prev_vols) / len(prev_vols) if len(prev_vols) >= 10 else 0
        vol_ratio = s["vol"][t] / avg_vol if avg_vol > 0 and traded else None
        r60 = price / adj[t - 60] - 1 if t >= 60 and not ind.isnan(adj[t - 60]) else None
        if r60 is not None:
            ret60[code] = r60
        dif, dea = ind.macd(adj)
        state = None
        if not (ind.isnan(dif[t]) or ind.isnan(dea[t]) or ind.isnan(dif[t - 1]) or ind.isnan(dea[t - 1])):
            above, was_above = dif[t] > dea[t], dif[t - 1] > dea[t - 1]
            state = (2 if not was_above else 1) if above else (-2 if was_above else -1)
        metrics[code] = {
            "ret60": None if r60 is None else round(r60 * 100, 2),
            "ma5": _num(ma5), "ma20": _num(ma20), "ma60": _num(ma60), "align": align,
            "hi52": _num(hi52), "lo52": _num(lo52), "pos52": _num(pos52, 1), "dist_hi": _num(dist_hi), "new_hl": new_hl,
            "vol_ratio": _num(vol_ratio), "rsi": _num(ind.rsi(adj)[t], 1),
            "dif": _num(dif[t], 3), "dea": _num(dea[t], 3), "macd_state": state,
        }
    population = sorted(ret60.values())
    for code, value in ret60.items():
        metrics[code]["rs"] = ind.percent_rank(population, value)
    breadth = {panel.dates[i]: [adv[i], dec[i], flat[i], highs[i], lows[i]] for i in range(T)}
    return metrics, breadth


def revenue_metrics(store):
    """營收是否為 12 個月新高、年增率連續為正的月數。"""
    series = {}
    for path in store.files("revenue")[-13:]:
        doc = store.read("revenue", path.name, default={})
        for code, row in doc.get("rows", {}).items():
            series.setdefault(code, []).append((doc["month"], row[0], row[1]))
    out = {}
    for code, months in series.items():
        months.sort()
        _, revenue, _ = months[-1]
        previous = [r for _, r, _ in months[-12:-1] if r is not None]
        high = (1 if revenue >= max(previous) else 0) if revenue is not None and len(previous) >= 11 else None
        streak_months = 0
        for _, _, yoy in reversed(months):
            if yoy is None or yoy <= 0:
                break
            streak_months += 1
        out[code] = (high, streak_months)
    return out


def pe_position(store, valuation):
    """目前本益比在自己過去一年（每月快照）中的位置：0 = 最便宜、100 = 最貴。"""
    snaps = [store.read("valuation", "history", p.name, default={})
             for p in sorted(store.path("valuation", "history").glob("*.json"))][-13:]
    out = {}
    for code, row in valuation.items():
        pe = row[0] if row else None
        if not pe or pe <= 0:
            continue
        history = sorted(s["rows"][code][0] for s in snaps
                         if s.get("rows", {}).get(code) and s["rows"][code][0] and s["rows"][code][0] > 0)
        if len(history) >= 6:
            out[code] = ind.percent_rank(history, pe)
    return out


def _round(value, digits=2):
    return None if value is None else round(value, digits)


def _pct(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator * 100, 2)


def _merged(doc, part):
    """合併上市、上櫃同一種資料；回傳 (rows, 有資料的市場)。"""
    rows, available = {}, set()
    for market in MARKETS:
        payload = doc.get(market, {}).get(part)
        if payload and isinstance(payload, dict) and "rows" in payload:
            rows.update(payload["rows"])
            available.add(market)
    return rows, available


def streak(values):
    """最近連續買超（正數）或賣超（負數）天數；略過缺資料的日子。"""
    seq = [v for v in values if v is not None]
    if not seq or seq[-1] == 0:
        return 0
    sign = 1 if seq[-1] > 0 else -1
    count = 0
    for v in reversed(seq):
        if v * sign > 0:
            count += 1
        else:
            break
    return sign * count


def _sum(values):
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _series_value(rows, available, market, code, idx, zero_if_absent):
    if market not in available:
        return None
    row = rows.get(code)
    if row is None:
        return 0 if zero_if_absent else None
    return row[idx]


def _latest_files(store, folder, count):
    return [store.read(folder, p.name, default={}) for p in store.files(folder)[-count:]]


def build_payload(store=None, all_docs=None):
    store = store or Store()
    all_docs = all_docs if all_docs is not None else load_docs(store)
    docs = all_docs[-WINDOW:]
    if not docs:
        raise SystemExit("data/daily 沒有資料，請先執行 py -m stock_agent.update --backfill-days 40")
    dates = [d["date"] for d in docs]
    quotes = [_merged(d, "quotes") for d in docs]
    insti = [_merged(d, "insti") for d in docs]
    margin = [_merged(d, "margin") for d in docs]

    latest_quotes, _ = quotes[-1]
    market_of = {}
    for market in MARKETS:
        for code in docs[-1].get(market, {}).get("quotes", {}).get("rows", {}):
            market_of[code] = market

    companies = store.read("meta", "companies.json", default={}).get("rows", {})
    valuation = {}
    for market in MARKETS:
        valuation.update(store.read("valuation", f"{market}.json", default={}).get("rows", {}))

    # 月營收：每檔用最新一個有申報的月份（每月 10 號前會有部分公司還沒公布）
    revenue, rev_notes = {}, {}
    for doc in _latest_files(store, "revenue", 3):
        for code, row in doc.get("rows", {}).items():
            revenue[code] = (doc["month"], row)

    # 集保：最新一週與前一週
    tdcc_docs = _latest_files(store, "tdcc", 2)
    tdcc_now = tdcc_docs[-1] if tdcc_docs else {"rows": {}}
    tdcc_prev = tdcc_docs[-2] if len(tdcc_docs) > 1 else {"rows": {}}

    # 內部人申報：最近 30 天
    since = (date.fromisoformat(dates[-1]) - timedelta(days=30)).isoformat()
    insider = []
    for path in store.files("insider")[-2:]:
        insider += [r for r in store.read("insider", path.name, default=[]) if r["date"] >= since]
    insider.sort(key=lambda r: (r["date"], r["lots"] or 0), reverse=True)
    ins_by_code = {}
    for rec in insider:
        n, lots_sum = ins_by_code.get(rec["code"], (0, 0))
        ins_by_code[rec["code"]] = (n + 1, lots_sum + (rec["lots"] or 0))

    tech, breadth = technicals(Panel(all_docs[-LONG_WINDOW:]), set(market_of))
    rev_extra = revenue_metrics(store)
    pe_pct = pe_position(store, valuation)

    rows, series_close, series_foreign, series_trust = [], {}, {}, {}
    for code, market in sorted(market_of.items()):
        info = companies.get(code, {})
        close_s = [_series_value(r, a, market, code, 0, False) for r, a in quotes]
        vol_s = [_series_value(r, a, market, code, 2, False) for r, a in quotes]
        f_s = [_series_value(r, a, market, code, 0, True) for r, a in insti]
        t_s = [_series_value(r, a, market, code, 1, True) for r, a in insti]
        d_s = [_series_value(r, a, market, code, 2, True) for r, a in insti]
        m_rows = [r.get(code) if market in a else None for r, a in margin]
        q = latest_quotes.get(code)
        close, change, vol, value = q

        def ret(n):
            if len(close_s) > n and close and close_s[-1 - n]:
                return _round((close / close_s[-1 - n] - 1) * 100)
            return None

        recent_closes = [c for c in close_s[-20:] if c]
        ma20 = sum(recent_closes) / len(recent_closes) if len(recent_closes) >= 15 else None
        vol5 = [v for v in vol_s[-5:] if v is not None]
        f5, t5 = _sum(f_s[-5:]), _sum(t_s[-5:])
        shares_lots = (info.get("shares") or 0) / 1000

        m_available = [r for r in m_rows if r is not None]
        m_latest = m_available[-1] if m_available else None
        m_chg5 = _sum([r[1] if r else None for r in m_rows[-5:]])

        rev = revenue.get(code)
        rev_row = rev[1] if rev else [None] * 7
        if rev and rev_row[6]:
            rev_notes[code] = rev_row[6]

        t_now, t_prev = tdcc_now["rows"].get(code), tdcc_prev["rows"].get(code)
        big = t_now[16] if t_now else None
        n_ins, lots_ins = ins_by_code.get(code, (0, 0))

        rows.append([
            code, info.get("name") or code, MARKET_LABEL[market], info.get("industry") or "其他",
            close,
            _pct(change, close - change) if close is not None and change is not None and close != change else None,
            ret(5), ret(20),
            _round((close / ma20 - 1) * 100) if ma20 and close else None,
            vol, _round(sum(vol5) / len(vol5), 0) if vol5 else None, value,
            f_s[-1], f5, _sum(f_s[-INSTI_WINDOW:]), streak(f_s),
            t_s[-1], t5, _sum(t_s[-INSTI_WINDOW:]), streak(t_s), d_s[-1],
            _round(t5 / shares_lots * 100, 3) if t5 is not None and shares_lots else None,
            _pct(f5, sum(vol5)) if vol5 else None,
            m_latest[0] if m_latest else None,
            m_latest[1] if m_latest else None,
            m_chg5,
            _pct(m_chg5, m_latest[0] - m_chg5) if m_latest and m_chg5 is not None and m_latest[0] - m_chg5 > 0 else None,
            m_latest[2] if m_latest else None,
            _pct(m_latest[2], m_latest[0]) if m_latest and m_latest[0] else None,
            *(valuation.get(code) or [None, None, None]),
            _round(rev_row[0] / 1e5) if rev_row[0] is not None else None,  # 千元 -> 億元
            rev_row[1], rev_row[2], rev_row[3], rev[0] if rev else None,
            big,
            _round(big - t_prev[16]) if t_now and t_prev else None,
            _round(sum(t_now[13:17])) if t_now else None,  # 400 張以上
            _round(sum(t_now[2:10])) if t_now else None,  # 50 張以下
            t_now[0] if t_now else None,
            _pct(t_now[0] - t_prev[0], t_prev[0]) if t_now and t_prev and t_prev[0] else None,
            n_ins, lots_ins,
            *[tech.get(code, {}).get(k) for k in TECH_KEYS],
            *(rev_extra.get(code) or (None, None)),
            pe_pct.get(code),
        ])
        series_close[code] = close_s
        series_foreign[code] = f_s[-INSTI_WINDOW:]
        series_trust[code] = t_s[-INSTI_WINDOW:]

    def market_series(market, key):
        return [doc.get(market, {}).get(key) for doc in docs]

    def asof(part):
        for doc in reversed(docs):
            if any(part in doc.get(m, {}) for m in MARKETS):
                return doc["date"]
        return None

    state = store.read("state.json", default={})
    return {
        "data_date": dates[-1],
        "updated_at": state.get("updated_at"),
        "warnings": state.get("warnings", []),
        "status": {
            "quotes": asof("quotes"),
            "insti": asof("insti"),
            "margin": asof("margin"),
            "revenue": max((m for m, _ in revenue.values()), default=None),
            "valuation": store.read("valuation", "twse.json", default={}).get("date"),
            "tdcc": tdcc_now.get("date"),
            "tdcc_prev": tdcc_prev.get("date"),
        },
        "market": {
            "dates": dates,
            "taiex": [(doc.get("twse", {}).get("quotes") or {}).get("index") for doc in docs],
            "otc": market_series("tpex", "index"),
            "insti_twse": market_series("twse", "market_insti"),
            "insti_tpex": market_series("tpex", "market_insti"),
            "margin_amount": [(doc.get("twse", {}).get("margin") or {}).get("amount") for doc in docs],
            "breadth": [breadth.get(d) for d in dates],  # [上漲, 下跌, 平盤, 創52週新高, 創52週新低]
        },
        "columns": COLUMNS,
        "rows": rows,
        "series": {
            "dates": dates,
            "insti_dates": dates[-INSTI_WINDOW:],
            "close": series_close,
            "foreign": series_foreign,
            "trust": series_trust,
        },
        "rev_notes": rev_notes,
        "insider": insider[:300],
    }
