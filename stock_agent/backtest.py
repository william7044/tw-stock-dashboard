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

from .store import Store

HORIZONS = (5, 20, 60)
MAX_DAYS = 500  # 最多用最近兩年的資料
WARMUP = 20  # 前 20 天用來累積連續天數、移動平均，不產生訊號
MIN_SAMPLES = 20  # 樣本少於這個數字就不顯示平均值
MARKETS = ("twse", "tpex")
NAN = float("nan")

SIGNALS = [
    {"id": "foreign", "label": "外資連買", "rule": "外資連續買超 ≥ 5 天、5 日均量 ≥ 500 張", "timing": "條件剛成立的隔天收盤進場"},
    {"id": "trust", "label": "投信連買", "rule": "投信連續買超 ≥ 3 天、5 日均量 ≥ 300 張", "timing": "條件剛成立的隔天收盤進場"},
    {"id": "trustcap", "label": "投信認養", "rule": "投信 5 日買超佔股本 ≥ 0.5%", "timing": "條件剛成立的隔天收盤進場"},
    {"id": "revenue", "label": "營收高成長", "rule": "月營收年增 ≥ 50%、累計年增 ≥ 30%、單月 ≥ 1 億", "timing": "次月 11 日後第一個交易日收盤進場，每月計一次"},
    {"id": "big", "label": "大戶加碼", "rule": "千張大戶比例週增 ≥ 0.5 個百分點且股東人數減少", "timing": "集保資料日後第一個交易日收盤進場，每週計一次"},
    {"id": "margin", "label": "融資大增", "rule": "5 日融資餘額增加 ≥ 10%、餘額 ≥ 500 張", "timing": "條件剛成立的隔天收盤進場"},
    {"id": "yield", "label": "高殖利率", "rule": "殖利率 ≥ 6%、本益比 0～12 倍", "timing": "每月第一個交易日檢查，隔天收盤進場"},
    {"id": "insider", "label": "內部人申報轉讓", "rule": "董監、經理人或大股東申報轉讓持股", "timing": "申報日後第一個交易日收盤進場"},
]


def isnan(value):
    return value != value


def nan_array(length):
    return array("d", [NAN]) * length


def adjusted_index(close, change):
    """用每天的漲跌價差串成還原指數。漲跌價差是相對「參考價」，除權息日的參考價已扣掉股利。"""
    out = nan_array(len(close))
    level = prev = NAN
    for i, c in enumerate(close):
        if not isnan(c):
            if isnan(level):
                level = 1.0
            else:
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


class Panel:
    """每檔股票每天的收盤、漲跌、成交量、外資、投信、融資餘額（array，缺值為 NaN）。"""

    FIELDS = ("close", "chg", "vol", "foreign", "trust", "margin")

    def __init__(self, docs):
        self.dates = [d["date"] for d in docs]
        self.T = T = len(docs)
        self.series = {}
        for i, doc in enumerate(docs):
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
        self.index = {code: adjusted_index(s["close"], s["chg"]) for code, s in self.series.items()}

    def first_after(self, iso, inclusive=False):
        """iso 之後（或當天）第一個交易日的索引；沒有就回傳 None。"""
        i = (bisect_left if inclusive else bisect_right)(self.dates, iso)
        return i if i < self.T else None

    def forward(self, code, entry, horizon):
        idx = self.index.get(code)
        if idx is None or entry + horizon >= self.T:
            return None
        a, b = idx[entry], idx[entry + horizon]
        if isnan(a) or isnan(b) or a <= 0:
            return None
        return b / a - 1

    def tradable(self, code, i):
        s = self.series.get(code)
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


def revenue_events(panel, store):
    events = []
    for path in store.files("revenue"):
        doc = store.read("revenue", path.name, default={})
        year, month = int(doc["month"][:4]), int(doc["month"][5:7])
        nxt = f"{year + month // 12:04d}-{month % 12 + 1:02d}-11"
        entry = panel.first_after(nxt, inclusive=True)
        # 資料期間開始之前就該進場的月份跳過（否則會被算成期間第一天才進場）
        if entry is None or (date.fromisoformat(panel.dates[entry]) - date.fromisoformat(nxt)).days > 10:
            continue
        for code, row in doc.get("rows", {}).items():
            revenue, yoy, cum_yoy = row[0], row[1], row[3]
            if (revenue or 0) >= 100_000 and (yoy or 0) >= 50 and (cum_yoy or 0) >= 30 and panel.tradable(code, entry):
                events.append((code, entry))
    return events


def yield_events(panel, store):
    events = []
    for path in sorted(store.path("valuation", "history").glob("*.json")):
        doc = store.read("valuation", "history", path.name, default={})
        i = panel.first_after(doc.get("date", ""), inclusive=True)
        if i is None or panel.dates[i] != doc["date"]:
            continue
        for code, (pe, dividend_yield, _pb) in doc.get("rows", {}).items():
            if (dividend_yield or 0) >= 6 and pe and 0 < pe <= 12 and panel.tradable(code, i + 1):
                events.append((code, i + 1))
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
    """每個進場日、每個天期：全部股票的平均報酬與上漲比例。"""
    out = {}
    for h in HORIZONS:
        out[h] = {}
        for e in range(panel.T - h):
            rets = [r for r in (panel.forward(code, e, h) for code in panel.index) if r is not None]
            if rets:
                out[h][e] = (mean(rets), sum(r > 0 for r in rets) / len(rets))
    return out


def pct(value):
    return round(value * 100, 2)


def summarize(events, panel, base):
    stats = {}
    for h in HORIZONS:
        rets, excess, base_means, base_wins, entries = [], [], [], [], set()
        for code, entry in events:
            r, b = panel.forward(code, entry, h), base[h].get(entry)
            if r is None or b is None:
                continue
            rets.append(r)
            excess.append(r - b[0])
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
            "excess_median": pct(median(excess)),  # 平均容易被少數暴漲股拉高，中位數較能代表一般情況
            "beat": pct(sum(x > 0 for x in excess) / n),
        }
    return stats


def run(store=None, docs=None):
    store = store or Store()
    if docs is None:
        docs = [store.load_day(d) for d in store.day_dates()[-MAX_DAYS:]]
    docs = [d for d in docs[-MAX_DAYS:] if (d.get("twse", {}).get("quotes") or {}).get("rows")]
    if len(docs) < WARMUP + max(HORIZONS) // 2:
        return {"days": len(docs), "signals": [], "message": "歷史資料不足，請先執行 py run.py --backfill-days 260 --history-months 13"}
    panel = Panel(docs)
    shares = {code: info.get("shares") for code, info in store.read("meta", "companies.json", default={}).get("rows", {}).items()}
    base = baselines(panel)

    events = daily_events(panel, shares)
    events["revenue"] = revenue_events(panel, store)
    events["yield"] = yield_events(panel, store)
    events["big"] = tdcc_events(panel, store)
    events["insider"] = insider_events(panel, store)

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
