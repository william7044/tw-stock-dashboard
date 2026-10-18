"""訊號回測：統計各訊號出現後 5、20、60 個交易日的報酬，並和同期全部股票的平均比較。

計算方式（避免用到當時還不知道的資料）：
- 法人、融資資料收盤後才公布，訊號日 t 的進場價用隔天（t+1）的收盤價。
- 月營收每月 10 日前公布完，進場日是次月 11 日（含）之後第一個交易日的收盤。
- 報酬用每天「漲跌價差」串成的還原指數計算，除權息、減資造成的參考價調整不算漲跌。
- 每日型訊號只在「條件剛成立」那天算一次，避免同一段連買被重複計入。
- 不計手續費、證交稅與滑價；股本用最新一期資料（近似）。

    py -m stock_agent.backtest
"""
from array import array
from bisect import bisect_left, bisect_right
from datetime import date
from statistics import mean, median

from . import indicators as ind
from .store import Store

HORIZONS = (5, 20, 60)
MAX_DAYS = 500  # 最多用最近兩年的資料
WARMUP = 20  # 前 20 天用來累積連續天數、移動平均，不產生訊號
MIN_SAMPLES = 20  # 樣本少於這個數字就不顯示平均值
MARKETS = ("twse", "tpex")
NAN = float("nan")

NEXT_DAY = "條件剛成立的隔天收盤進場"
MONTHLY_REVENUE = "次月 11 日後第一個交易日收盤進場，每月計一次"
MONTHLY_CHECK = "每月第一個交易日檢查，隔天收盤進場"
SIGNALS = [
    # 對照組：不看任何訊號，每天、每一檔都「買」。訊號至少要贏過同樣流動性的對照組才算有用。
    {"id": "ref_all", "group": "對照組", "label": "隨機選股（全部）", "rule": "每天每一檔都買，代表市場的一般情況", "timing": "每天收盤進場"},
    {"id": "ref_liquid", "group": "對照組", "label": "隨機選股（均量 ≥ 300 張）", "rule": "每天每一檔 5 日均量 ≥ 300 張的股票都買", "timing": "每天收盤進場"},
    {"id": "ref_0050", "group": "對照組", "label": "0050 每天定期定額", "rule": "每個交易日收盤買進固定金額的元大台灣 50（含配息），代表「直接買大盤」", "timing": "每天收盤進場"},
    {"id": "foreign", "group": "籌碼面", "label": "外資連買", "rule": "外資連續買超 ≥ 5 天、5 日均量 ≥ 500 張", "timing": NEXT_DAY},
    {"id": "trust", "group": "籌碼面", "label": "投信連買", "rule": "投信連續買超 ≥ 3 天、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
    {"id": "trustcap", "group": "籌碼面", "label": "投信認養", "rule": "投信 5 日買超佔股本 ≥ 0.5%", "timing": NEXT_DAY},
    {"id": "big", "group": "籌碼面", "label": "大戶加碼", "rule": "千張大戶比例週增 ≥ 0.5 個百分點且股東人數減少", "timing": "集保資料日後第一個交易日收盤進場，每週計一次"},
    {"id": "margin", "group": "籌碼面", "label": "融資大增", "rule": "5 日融資餘額增加 ≥ 10%、餘額 ≥ 500 張", "timing": NEXT_DAY},
    {"id": "insider", "group": "籌碼面", "label": "內部人申報轉讓", "rule": "董監、經理人或大股東申報轉讓持股", "timing": "申報日後第一個交易日收盤進場"},
    {"id": "revenue", "group": "基本面", "label": "營收高成長", "rule": "月營收年增 ≥ 50%、累計年增 ≥ 30%、單月 ≥ 1 億", "timing": MONTHLY_REVENUE},
    {"id": "revhigh", "group": "基本面", "label": "營收創新高", "rule": "月營收為近 12 個月最高、單月 ≥ 1 億", "timing": MONTHLY_REVENUE},
    {"id": "yield", "group": "基本面", "label": "高殖利率", "rule": "殖利率 ≥ 6%、本益比 0～12 倍", "timing": MONTHLY_CHECK},
    {"id": "pelow", "group": "基本面", "label": "本益比低檔", "rule": "本益比落在自己過去一年（每月取樣）最低 10%、5 日均量 ≥ 100 張", "timing": MONTHLY_CHECK},
    {"id": "high52", "group": "技術面", "label": "創 52 週新高", "rule": "還原股價創一年新高、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
    {"id": "low52", "group": "技術面", "label": "創 52 週新低", "rule": "還原股價創一年新低、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
    {"id": "bull", "group": "技術面", "label": "多頭排列", "rule": "股價 > 5 日 > 20 日 > 60 日均線、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
    {"id": "rs", "group": "技術面", "label": "相對強勢", "rule": "近 60 日漲幅贏過 95% 的股票、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
    {"id": "volup", "group": "技術面", "label": "爆量上漲", "rule": "成交量 ≥ 前 20 日均量 2 倍、≥ 500 張且收漲", "timing": NEXT_DAY},
    {"id": "macd", "group": "技術面", "label": "MACD 黃金交叉", "rule": "DIF 由下往上穿過 DEA、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
    {"id": "rsilow", "group": "技術面", "label": "RSI 超賣", "rule": "14 日 RSI ≤ 30、5 日均量 ≥ 300 張", "timing": NEXT_DAY},
]


def isnan(value):
    return value != value


def nan_array(length):
    return array("d", [NAN]) * length


def adjusted_index(close, change, reference=None):
    """用每天的漲跌串成還原指數（含配息的報酬）。

    平常日子：漲跌價差是相對前一天收盤。除權息日：交易所的漲跌價差只標「X」，
    改用除權除息計算結果表的「除權息參考價」（reference）算當天真正的漲跌。
    """
    out = nan_array(len(close))
    level = prev = NAN
    for i, c in enumerate(close):
        if not isnan(c):
            if isnan(level):
                level = 1.0
            else:
                ref = reference[i] if reference is not None else NAN
                if not ref > 0:
                    ch = change[i]
                    ref = c - ch if not isnan(ch) else NAN
                ratio = c / ref if ref > 0 else (c / prev if prev > 0 else 1.0)
                if 1 / 3 < ratio < 3:  # 停牌很久等資料異常的跳動不計入
                    level *= ratio
            prev = c
        out[i] = level
    return out


def streaks(values):
    """每天的連續買超（正）或賣超（負）天數；缺資料的日子沿用前一天。"""
    out, s = [0] * len(values), 0
    for i, v in enumerate(values):
        if not isnan(v):
            s = (s + 1 if s > 0 else 1) if v > 0 else (s - 1 if s < 0 else -1) if v < 0 else 0
        out[i] = s
    return out


def rolling(values, n, how):
    out = nan_array(len(values))
    for i in range(len(values)):
        window = [v for v in values[max(0, i - n + 1): i + 1] if not isnan(v)]
        if window:
            out[i] = sum(window) / len(window) if how == "mean" else sum(window)
    return out


def transitions(condition, start):
    """條件「剛成立」的那幾天（前一天不成立、今天成立）。"""
    return [i for i in range(max(start, 1), len(condition)) if condition[i] and not condition[i - 1]]


def load_exrights(store):
    """除權息參考價：{日期: {代號: 參考價}}。"""
    out = {}
    for path in store.files("exrights"):
        out.update(store.read("exrights", path.name, default={}))
    return out


class Panel:
    """每檔股票每天的收盤、漲跌、成交量、外資、投信、融資餘額（array，缺值為 NaN）。

    對照用的 ETF（例如 0050）放在 bench，不會混進全市場平均或任何訊號。
    """

    FIELDS = ("close", "chg", "vol", "foreign", "trust", "margin", "ref")

    def __init__(self, docs, exrights=None):
        self.dates = [d["date"] for d in docs]
        self.T = T = len(docs)
        self.series, self.bench, self.bench_index = {}, {}, {}
        self.exrights = exrights or {}
        for i, doc in enumerate(docs):
            day_refs = self.exrights.get(doc["date"], {})
            for market in MARKETS:
                part = doc.get(market, {})
                quotes = (part.get("quotes") or {}).get("rows", {})
                insti = (part.get("insti") or {}).get("rows")
                margin = (part.get("margin") or {}).get("rows")
                for code, q in quotes.items():
                    s = self.series.get(code)
                    if s is None:
                        s = self.series[code] = {k: nan_array(T) for k in self.FIELDS}
                    for key, value in zip(("close", "chg", "vol"), q):
                        if value is not None:
                            s[key][i] = value
                    if insti is not None:  # 當天有法人資料：沒出現在名單上 = 0
                        row = insti.get(code) or (0, 0)
                        s["foreign"][i], s["trust"][i] = row[0] or 0, row[1] or 0
                    if margin is not None and code in margin:
                        s["margin"][i] = margin[code][0]
                    if code in day_refs:
                        s["ref"][i] = day_refs[code]
        self.index = {code: adjusted_index(s["close"], s["chg"], s["ref"]) for code, s in self.series.items()}

    def add_benchmark(self, code, rows):
        """加入對照用的 ETF：rows = {日期: [收盤, 漲跌]}，依同樣方式還原配息。"""
        s = {k: nan_array(self.T) for k in self.FIELDS}
        for i, iso in enumerate(self.dates):
            row = rows.get(iso)
            if row and row[0] is not None:
                s["close"][i] = row[0]
                if row[1] is not None:
                    s["chg"][i] = row[1]
            ref = self.exrights.get(iso, {}).get(code)
            if ref:
                s["ref"][i] = ref
        self.bench[code] = s
        self.bench_index[code] = adjusted_index(s["close"], s["chg"], s["ref"])

    def first_after(self, iso, inclusive=False):
        """iso 之後（或當天）第一個交易日的索引；沒有就回傳 None。"""
        i = (bisect_left if inclusive else bisect_right)(self.dates, iso)
        return i if i < self.T else None

    def forward(self, code, entry, horizon):
        idx = self.index.get(code)
        if idx is None:
            idx = self.bench_index.get(code)
        if idx is None or entry + horizon >= self.T:
            return None
        a, b = idx[entry], idx[entry + horizon]
        if isnan(a) or isnan(b) or a <= 0:
            return None
        return b / a - 1

    def tradable(self, code, i):
        s = self.series.get(code)
        if s is None:
            s = self.bench.get(code)
        return s is not None and i is not None and i < self.T and not isnan(s["close"][i])


def daily_events(panel, shares):
    """法人、融資類訊號：條件剛成立那天 t，進場日 t+1。"""
    events = {"foreign": [], "trust": [], "trustcap": [], "margin": []}
    for code, s in panel.series.items():
        fs, ts = streaks(s["foreign"]), streaks(s["trust"])
        vol5 = rolling(s["vol"], 5, "mean")
        t5 = rolling(s["trust"], 5, "sum")
        lots = (shares.get(code) or 0) / 1000
        m = s["margin"]
        conditions = {
            "foreign": [fs[i] >= 5 and vol5[i] >= 500 for i in range(panel.T)],
            "trust": [ts[i] >= 3 and vol5[i] >= 300 for i in range(panel.T)],
            "trustcap": [lots > 0 and t5[i] / lots * 100 >= 0.5 for i in range(panel.T)],
            "margin": [
                i >= 5 and m[i - 5] > 0 and m[i] >= 500 and (m[i] - m[i - 5]) / m[i - 5] * 100 >= 10
                for i in range(panel.T)
            ],
        }
        for key, cond in conditions.items():
            for i in transitions(cond, WARMUP):
                if panel.tradable(code, i + 1):
                    events[key].append((code, i + 1))
    return events


def technical_events(panel):
    """技術面訊號（還原股價）：52 週新高／新低、多頭排列、相對強勢、爆量上漲、MACD 黃金交叉、RSI 超賣。"""
    keys = ("high52", "low52", "bull", "rs", "volup", "macd", "rsilow")
    events = {key: [] for key in keys}
    T, conditions, ret60, liquid_of = panel.T, {}, {}, {}
    for code, s in panel.series.items():
        idx, close, vol, chg = panel.index[code], s["close"], s["vol"], s["chg"]
        vol5 = rolling(vol, 5, "mean")
        prev_vol = rolling(vol, 20, "mean")  # prev_vol[t - 1] = 前 20 日均量
        hi, lo = ind.prior_extremes(idx, 250, min_days=120)
        ma5, ma20, ma60 = ind.sma(idx, 5), ind.sma(idx, 20), ind.sma(idx, 60)
        rsi = ind.rsi(idx)
        dif, dea = ind.macd(idx)
        traded = [not isnan(c) for c in close]
        liquid = [vol5[t] >= 300 for t in range(T)]
        liquid_of[code] = liquid
        conditions[code] = {
            "high52": [traded[t] and liquid[t] and idx[t] >= hi[t] for t in range(T)],
            "low52": [traded[t] and liquid[t] and idx[t] <= lo[t] for t in range(T)],
            "bull": [liquid[t] and idx[t] > ma5[t] > ma20[t] > ma60[t] for t in range(T)],
            "volup": [
                t > 0 and traded[t] and vol[t] >= 500 and chg[t] > 0 and prev_vol[t - 1] > 0 and vol[t] >= 2 * prev_vol[t - 1]
                for t in range(T)
            ],
            "macd": [t > 0 and liquid[t] and dif[t] > dea[t] and dif[t - 1] <= dea[t - 1] for t in range(T)],
            "rsilow": [liquid[t] and rsi[t] <= 30 for t in range(T)],
        }
        ret60[code] = [idx[t] / idx[t - 60] - 1 if t >= 60 and idx[t - 60] > 0 else NAN for t in range(T)]
    # 相對強勢：每天把所有股票的 60 日漲幅排名，前 5%
    strong = {code: [False] * T for code in panel.series}
    for t in range(60, T):
        ranked = sorted((r[t], code) for code, r in ret60.items() if not isnan(r[t]))
        cutoff = int(len(ranked) * 0.95)
        for _, code in ranked[cutoff:]:
            strong[code][t] = liquid_of[code][t]
    for code, cond in conditions.items():
        cond["rs"] = strong[code]
        for key in keys:
            for i in transitions(cond[key], WARMUP):
                if panel.tradable(code, i + 1):
                    events[key].append((code, i + 1))
    return events


def _months_before(month, n):
    """'2026-08' 往前 n 個月（不含自己），由舊到新。"""
    year, mon = int(month[:4]), int(month[5:7])
    out = []
    for _ in range(n):
        year, mon = (year, mon - 1) if mon > 1 else (year - 1, 12)
        out.append(f"{year:04d}-{mon:02d}")
    return out[::-1]


def _revenue_entry(panel, month):
    """月營收的進場日：次月 11 日（含）之後第一個交易日；資料期間開始前就該進場的月份回傳 None。"""
    year, mon = int(month[:4]), int(month[5:7])
    nxt = f"{year + mon // 12:04d}-{mon % 12 + 1:02d}-11"
    entry = panel.first_after(nxt, inclusive=True)
    if entry is None or (date.fromisoformat(panel.dates[entry]) - date.fromisoformat(nxt)).days > 10:
        return None
    return entry


def revenue_events(panel, store):
    events = {"revenue": [], "revhigh": []}
    docs = {doc["month"]: doc for doc in (store.read("revenue", p.name, default={}) for p in store.files("revenue"))}
    for month, doc in sorted(docs.items()):
        entry = _revenue_entry(panel, month)
        if entry is None:
            continue
        previous = [docs.get(m, {}).get("rows", {}) for m in _months_before(month, 11)]
        for code, row in doc.get("rows", {}).items():
            revenue, yoy, cum_yoy = row[0], row[1], row[3]
            if not panel.tradable(code, entry) or (revenue or 0) < 100_000:
                continue
            if (yoy or 0) >= 50 and (cum_yoy or 0) >= 30:
                events["revenue"].append((code, entry))
            past = [rows.get(code, [None])[0] for rows in previous]
            if all(p is not None for p in past) and revenue >= max(past):
                events["revhigh"].append((code, entry))
    return events


def valuation_events(panel, store):
    """高殖利率、本益比低檔：每月第一個交易日的估值快照，隔天收盤進場。"""
    events = {"yield": [], "pelow": []}
    snaps = [store.read("valuation", "history", p.name, default={})
             for p in sorted(store.path("valuation", "history").glob("*.json"))]
    vol5_cache = {}
    for k, doc in enumerate(snaps):
        i = panel.first_after(doc.get("date", ""), inclusive=True)
        if i is None or panel.dates[i] != doc["date"]:
            continue  # 每日股價資料開始之前的快照只當作本益比的歷史比較
        window = snaps[max(0, k - 12): k + 1]
        for code, (pe, dividend_yield, _pb) in doc.get("rows", {}).items():
            if not panel.tradable(code, i + 1):
                continue
            if (dividend_yield or 0) >= 6 and pe and 0 < pe <= 12:
                events["yield"].append((code, i + 1))
            if not pe or pe <= 0:
                continue
            history = sorted(s["rows"][code][0] for s in window
                             if s.get("rows", {}).get(code) and (s["rows"][code][0] or 0) > 0)
            if len(history) < 6 or bisect_left(history, pe) / len(history) * 100 > 10:
                continue
            if code not in vol5_cache:
                vol5_cache[code] = rolling(panel.series[code]["vol"], 5, "mean")
            if vol5_cache[code][i] >= 100:
                events["pelow"].append((code, i + 1))
    return events


def tdcc_events(panel, store):
    events = []
    docs = [store.read("tdcc", p.name, default={}) for p in store.files("tdcc")]
    for prev, now in zip(docs, docs[1:]):
        entry = panel.first_after(now["date"])
        for code, row in now.get("rows", {}).items():
            old = prev.get("rows", {}).get(code)
            if not old or not old[0]:
                continue
            if row[16] - old[16] >= 0.5 and row[0] < old[0] and panel.tradable(code, entry):
                events.append((code, entry))
    return events


def insider_events(panel, store):
    seen = set()
    for path in store.files("insider"):
        for rec in store.read("insider", path.name, default=[]):
            seen.add((rec["code"], rec["date"]))
    events = []
    for code, day in sorted(seen):
        entry = panel.first_after(day)
        if panel.tradable(code, entry):
            events.append((code, entry))
    return events


def baselines(panel):
    """每個進場日、每個天期：全部股票的 (平均報酬, 上漲比例, 中位數報酬)。

    報酬分布右偏（少數股票漲好幾倍），平均會明顯高於中位數，所以「中位數型」的比較要對中位數，
    否則隨機選股也會看起來「大多輸給大盤」。
    """
    out = {}
    for h in HORIZONS:
        out[h] = {}
        for e in range(panel.T - h):
            rets = [r for r in (panel.forward(code, e, h) for code in panel.index) if r is not None]
            if rets:
                out[h][e] = (mean(rets), sum(r > 0 for r in rets) / len(rets), median(rets))
    return out


def pct(value):
    return round(value * 100, 2)


def summarize(events, panel, base):
    stats = {}
    for h in HORIZONS:
        rets, excess, vs_median, base_means, base_wins, entries = [], [], [], [], [], set()
        for code, entry in events:
            r, b = panel.forward(code, entry, h), base[h].get(entry)
            if r is None or b is None:
                continue
            rets.append(r)
            excess.append(r - b[0])  # 對同期平均（平均對平均）
            vs_median.append(r - b[2])  # 對同期中位數股票（隨機選股 = 0、贏過比例 = 50%）
            base_means.append(b[0])
            base_wins.append(b[1])
            entries.add(entry)
        n = len(rets)
        if n < MIN_SAMPLES:
            stats[str(h)] = {"n": n}
            continue
        stats[str(h)] = {
            "n": n,
            "dates": len(entries),
            "mean": pct(mean(rets)),
            "median": pct(median(rets)),
            "win": pct(sum(r > 0 for r in rets) / n),
            "base": pct(mean(base_means)),
            "base_win": pct(mean(base_wins)),
            "excess": pct(mean(excess)),
            "excess_median": pct(median(vs_median)),  # 一般情況：訊號股票報酬的中位數 − 同期中位數股票
            "beat": pct(sum(x > 0 for x in vs_median) / n),  # 贏過同期一半股票的比例，隨機選股約 50%
        }
    return stats


def run(store=None, docs=None):
    store = store or Store()
    if docs is None:
        docs = [store.load_day(d) for d in store.day_dates()[-MAX_DAYS:]]
    docs = [d for d in docs[-MAX_DAYS:] if (d.get("twse", {}).get("quotes") or {}).get("rows")]
    if len(docs) < WARMUP + max(HORIZONS) // 2:
        return {"days": len(docs), "signals": [], "message": "歷史資料不足，請先執行 py run.py --backfill-days 260 --history-months 25"}
    panel = Panel(docs, load_exrights(store))
    panel.add_benchmark("0050", store.read("benchmarks", "0050.json", default={}).get("rows", {}))
    shares = {code: info.get("shares") for code, info in store.read("meta", "companies.json", default={}).get("rows", {}).items()}
    base = baselines(panel)

    events = daily_events(panel, shares)
    events.update(technical_events(panel))
    events.update(revenue_events(panel, store))
    events.update(valuation_events(panel, store))
    events["big"] = tdcc_events(panel, store)
    events["insider"] = insider_events(panel, store)
    events["ref_all"], events["ref_liquid"] = [], []
    for code, s in panel.series.items():
        vol5 = rolling(s["vol"], 5, "mean")
        for e in range(WARMUP + 1, panel.T):
            if panel.tradable(code, e):
                events["ref_all"].append((code, e))
                if vol5[e - 1] >= 300:
                    events["ref_liquid"].append((code, e))
    events["ref_0050"] = [("0050", e) for e in range(WARMUP + 1, panel.T) if panel.tradable("0050", e)]

    all_entries = [(None, e) for e in range(WARMUP + 1, panel.T)]
    overall = {}
    for h in HORIZONS:
        values = [base[h][e] for _, e in all_entries if e in base[h]]
        if values:
            overall[str(h)] = {"mean": pct(mean(v[0] for v in values)), "win": pct(mean(v[1] for v in values)), "dates": len(values)}

    taiex = [((d.get("twse", {}).get("quotes") or {}).get("index") or [None])[0] for d in docs]
    taiex = [v for v in taiex if v]
    return {
        "period": [panel.dates[0], panel.dates[-1]],
        "days": panel.T,
        "taiex": [taiex[0], taiex[-1]] if taiex else None,
        "horizons": list(HORIZONS),
        "min_samples": MIN_SAMPLES,
        "baseline": overall,
        "signals": [
            {**sig, "events": len(events[sig["id"]]), "stats": summarize(events[sig["id"]], panel, base)}
            for sig in SIGNALS
        ],
    }


def main():
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = run()
    if not result["signals"]:
        print(result["message"])
        return
    print(f"期間 {result['period'][0]} ~ {result['period'][1]}（{result['days']} 個交易日）")
    print("全部股票平均（基準）：" + "，".join(
        f"{h} 日 {v['mean']:+.2f}%（上漲 {v['win']:.0f}%）" for h, v in result["baseline"].items()))
    for sig in result["signals"]:
        print(f"\n{sig['label']}｜{sig['rule']}｜訊號 {sig['events']} 次")
        for h, st in sig["stats"].items():
            if "mean" not in st:
                print(f"  {h:>2} 日：樣本不足（{st['n']} 筆）")
            else:
                print(f"  {h:>2} 日：平均 {st['mean']:+.2f}%  中位數 {st['median']:+.2f}%  上漲 {st['win']:.0f}%  "
                      f"超額平均 {st['excess']:+.2f}%  超額中位數 {st['excess_median']:+.2f}%  "
                      f"贏過全市場 {st['beat']:.0f}%  (n={st['n']}, {st['dates']} 個進場日)")


if __name__ == "__main__":
    main()
