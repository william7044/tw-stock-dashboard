"""把 data/ 裡的原始資料算成儀表板要用的指標。

每檔股票一列（欄位見 COLUMNS），另外附上個股走勢序列、大盤序列與內部人申報清單。
"""
from datetime import date, timedelta

from .store import Store

MARKETS = ("twse", "tpex")
MARKET_LABEL = {"twse": "上市", "tpex": "上櫃"}
WINDOW = 40  # 個股走勢圖顯示的交易日數
INSTI_WINDOW = 20

COLUMNS = [
    "code", "name", "market", "industry",
    "close", "chg_pct", "ret5", "ret20", "ma20_gap", "vol", "vol5", "value",
    "f1", "f5", "f20", "fs", "t1", "t5", "t20", "ts", "d1", "t5cap", "f5vol",
    "m_bal", "m_chg", "m_chg5", "m_chg5_pct", "s_bal", "s_ratio",
    "pe", "yld", "pb",
    "rev", "rev_yoy", "rev_mom", "rev_cum_yoy", "rev_month",
    "big", "big_chg", "b400", "retail", "holders", "holders_chg",
    "ins_n", "ins_lots",
]


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


def build_payload(store=None):
    store = store or Store()
    docs = [store.load_day(d) for d in store.day_dates()[-max(WINDOW, INSTI_WINDOW) - 5:]]
    docs = [d for d in docs if any("quotes" in d.get(m, {}) for m in MARKETS)][-WINDOW:]
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
