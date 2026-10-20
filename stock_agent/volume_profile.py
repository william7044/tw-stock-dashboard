"""成交量分布（Volume Profile）：每個價位成交了多少量，以及據此標註的買賣點（B／S）。

資料只有每天的收盤、成交量、成交值，沒有盤中逐筆，所以每天的成交量整筆放在當天的均價
（成交值 ÷ 成交量）上；價格一律用還原股價，除權息前後的量才會落在同一個價位。

- 價格格子：取對數後每 1% 一格。格線固定不動，滾動視窗每天只要加進一天、移出一天。
- POC：成交量最多的那一格。
- 價值區（VAL～VAH）：從 POC 往上下擴張，每次加入量比較多的一側，直到涵蓋 70% 的成交量。
- 每天的判斷只用「前 120 個交易日」的分布（不含當天），不會用到當時還不知道的資料。

只分析 20 日平均成交值 ≥ 1 億元的股票：量太小時，少數幾筆大單就能堆出假的高量區。

買賣點（每檔同時最多一筆部位；B 之後要等到 S，才會出現下一個 B）：
- 區間低接：60 日箱型整理中（價值區也在箱子裡），收盤跌到價值區下緣 → B；
  漲到價值區上緣 → S（到目標），跌破下緣 5% → S（停損），持有滿 60 日 → S（到期）。
- 跌深反彈：距 120 日高點跌超過 20%、POC 與價值區都在上方（套牢區），收盤創 10 日新高、
  離 20 日低點 ≥ 5% → B；漲到價值區下緣（套牢區開始的地方）→ S（到目標），
  跌破 20 日低點 3% → S（停損），持有滿 60 日 → S（到期）。
- 整理突破：60 日高低差 ≤ 25%，收盤帶量（≥ 前 20 日均量 1.5 倍）站上箱頂與價值區上緣，
  而且上方的套牢量 ≤ 15% → B；跌回價值區 → S（突破失敗），從進場後最高收盤回落 10% → S（移動停利）。
"""
import math
from collections import deque

from . import indicators as ind

NAN = float("nan")
WINDOW = 120  # 分布涵蓋的交易日數（約半年）
MIN_DAYS = 100  # 視窗內至少要有這麼多天有成交，分布才算數
STEP = math.log(1.01)  # 價格格子：每格 1%
VALUE_AREA = 0.70
DISPLAY = 120  # 個股圖顯示的交易日數

LIQUID = 100  # 20 日平均成交值 ≥ 1 億元才分析（成交值單位：百萬元）
BIG_RANK, BIG_VALUE = 150, 300  # 量大權值股：市值前 150 大、20 日平均成交值 ≥ 3 億元

BOX = 60  # 箱型整理看前 60 個交易日
RANGE_BOX, RANGE_RET, RANGE_SLACK = 0.25, 0.10, 0.03  # 高低差 ≤ 25%、60 日漲跌 ±10% 內；價值區、收盤的容許誤差 3%
NEAR, RANGE_STOP = 0.01, 0.95  # 收盤在價值區下緣 1% 以內才算碰到；跌破下緣 5% 停損
DEEP, POC_ROOM, VAL_ROOM = 0.20, 1.10, 1.05  # 距 120 日高點跌 ≥ 20%；POC 在 10%、價值區下緣在 5% 以上
BOUNCE, REB_STOP = 0.05, 0.97  # 離 20 日低點 ≥ 5%；跌破 20 日低點 3% 停損
BRK_BOX, BRK_VOL, OVERHEAD, TRAIL = 0.25, 1.5, 0.15, 0.90
MAX_HOLD = 60  # 區間低接、跌深反彈最多持有 60 個交易日
RECENT = 10  # 「整理突破」清單：最近 10 個交易日內突破、還沒出場
KINDS = ("rng", "reb", "brk")  # 區間低接、跌深反彈、整理突破

# 清單用的旗標（可以同時符合多個）
BIG, RANGE, REBOUND, BREAKOUT = 1, 2, 4, 8
COLUMNS = ["mcap", "vp_set", "vp_poc", "vp_val", "vp_vah", "vp_pos", "vp_room", "vp_above",
           "vp_b", "vp_kind", "vp_tgt", "vp_stop"]


def isnan(value):
    return value != value


def bin_of(price):
    return math.floor(math.log(price) / STEP)


def bin_price(k, edge=0.0):
    """格子 k 的價格：edge = 0 是下緣、0.5 是中間、1 是上緣。"""
    return math.exp((k + edge) * STEP)


def adjusted_vwap(close, vol, value, adj):
    """每天的還原均價：成交值 ÷ 成交量，再乘上當天的還原係數（還原收盤 ÷ 收盤）。

    成交值單位是百萬元、成交量是張，所以均價 = 成交值 × 1000 ÷ 成交量。
    沒有成交值，或均價離收盤超過 15%（資料異常）時改用收盤價。
    """
    out = [NAN] * len(close)
    for t, (c, v, val, a) in enumerate(zip(close, vol, value, adj)):
        if isnan(c) or isnan(a) or not (c > 0 and v > 0):
            continue
        p = val * 1000 / v if val > 0 else c
        if not 0.85 < p / c < 1.15:
            p = c
        out[t] = p * a / c
    return out


def value_area(bins, share=VALUE_AREA):
    """bins = {格子: 成交量}，回傳 (POC 格子, 價值區下緣格子, 上緣格子, 總量)。"""
    keys = sorted(k for k, v in bins.items() if v > 0)
    low, high = keys[0], keys[-1]
    total = sum(bins[k] for k in keys)
    middle = (low + high) / 2
    poc = max(keys, key=lambda k: (bins[k], -abs(k - middle)))  # 一樣多時取靠近中間的
    a = b = poc
    acc = bins[poc]
    while acc < share * total:
        up = bins.get(b + 1, 0) if b < high else -1
        down = bins.get(a - 1, 0) if a > low else -1
        if up >= down:
            b, acc = b + 1, acc + up
        else:
            a, acc = a - 1, acc + down
    return poc, a, b, total


def rolling_levels(prices, vols, closes, window=WINDOW, min_days=MIN_DAYS):
    """每天「前 window 個交易日（不含當天）」的分布。

    回傳 (levels, above, last_bins)：levels[t] = (POC, 價值區下緣, 上緣) 的格子或 None；
    above[t] = 分布裡高於當天收盤那一格的成交量比例（上方套牢量）；last_bins = 最後一天用的分布。
    """
    T = len(prices)
    levels, above, last_bins = [None] * T, [NAN] * T, {}
    bins, days = {}, deque()
    for t in range(T):
        if len(days) >= min_days:
            poc, a, b, total = value_area(bins)
            levels[t] = (poc, a, b)
            if not isnan(closes[t]):
                k = bin_of(closes[t])
                above[t] = sum(v for kk, v in bins.items() if kk > k) / total
        if t == T - 1:
            last_bins = dict(bins)
        p, v = prices[t], vols[t]
        if not isnan(p) and v > 0:
            k = bin_of(p)
            bins[k] = bins.get(k, 0) + v
            days.append((t, k, v))
        while days and days[0][0] <= t - window:
            _, k, v = days.popleft()
            bins[k] -= v
            if bins[k] <= 1e-9:
                del bins[k]
    return levels, above, last_bins


def _mean(values, n, today=True):
    """每天最近 n 個交易日（today=False 時為「前 n 天、不含當天」）有資料的日子的平均。"""
    out, window, total = [NAN] * len(values), deque(), 0.0
    for t, v in enumerate(values):
        if not today and window:
            out[t] = total / len(window)
        if not isnan(v):
            window.append((t, v))
            total += v
        while window and window[0][0] <= t - n:
            total -= window.popleft()[1]
        if today and window:
            out[t] = total / len(window)
    return out


def _exit(pos, c, t):
    """持有中：今天收盤是否出場，回傳出場原因或 None。"""
    if pos["kind"] == "brk":
        pos["peak"] = max(pos["peak"], c)
        if c < pos["stop"]:
            return "fail"
        return "trail" if c < pos["peak"] * TRAIL else None
    if c >= pos["target"]:
        return "tgt"
    if c < pos["stop"]:
        return "stop"
    return "time" if t - pos["t"] >= MAX_HOLD else None


def scan(close, vol, value, idx):
    """一檔股票：每天的價值區、B／S 訊號，以及最後一天符合哪些清單。

    close／vol／value 是每天的收盤、成交量（張）、成交值（百萬元），idx 是還原指數（缺值為 NaN）。
    從來沒有達到成交值門檻的股票回傳 None。
    """
    T = len(close)
    value20 = _mean(value, 20)
    last = next((t for t in range(T - 1, -1, -1) if not isnan(close[t])), None)
    if last is None or not max((v for v in value20 if not isnan(v)), default=0) >= LIQUID:
        return None
    scale = close[last] / idx[last]
    px = [idx[t] * scale if not isnan(close[t]) else NAN for t in range(T)]
    levels, above, last_bins = rolling_levels(adjusted_vwap(close, vol, value, px), vol, px)
    hi60, lo60 = ind.prior_extremes(px, BOX, min_days=BOX)
    hi120, _ = ind.prior_extremes(px, WINDOW - 1, min_days=MIN_DAYS)  # 加上當天共 120 天
    _, lo20 = ind.prior_extremes(px, 19, min_days=19)  # 加上當天共 20 天
    hi10, _ = ind.prior_extremes(px, 10, min_days=10)
    vol20 = _mean(vol, 20, today=False)

    marks, trades, pos, near_prev, flags = [], [], None, False, 0
    for t in range(T):
        c, lv = px[t], levels[t]
        if isnan(c):
            continue
        ready = lv is not None and not isnan(hi60[t]) and not isnan(lo20[t])
        near = in_range = rebounding = False
        if ready:
            poc, val, vah = bin_price(lv[0], 0.5), bin_price(lv[1]), bin_price(lv[2], 1)
            box = hi60[t] / lo60[t] - 1
            low = min(lo20[t], c)
            top = max(hi120[t], c) if not isnan(hi120[t]) else NAN
            ret60 = c / px[t - BOX] - 1 if t >= BOX and not isnan(px[t - BOX]) else NAN
            liquid = value20[t] >= LIQUID
            near = c <= val * (1 + NEAR)
            # 箱型整理：60 日箱子夠窄、沒有明顯趨勢，而且前 120 日的價值區就落在這個箱子裡
            # （否則價值區上緣可能是更早的高價區，「漲到上緣」的目標就不是這個箱子）；
            # 收盤在價值區內，下緣下方 3% 以內也算（那是低接的位置），站上上緣就不算整理了
            in_range = (liquid and box <= RANGE_BOX and abs(ret60) <= RANGE_RET
                        and lo60[t] * (1 - RANGE_SLACK) <= val and vah <= hi60[t] * (1 + RANGE_SLACK)
                        and val * (1 - RANGE_SLACK) <= c <= vah)
            rebounding = (liquid and low <= top * (1 - DEEP) and poc >= c * POC_ROOM and val >= c * VAL_ROOM
                          and c >= low * (1 + BOUNCE))
        if pos is not None:  # 持有中只看出場；出場當天也不會再進場
            reason = _exit(pos, c, t)
            if reason:
                marks.append((t, "S", reason))
                trades.append((pos["kind"], pos["t"], t, reason))
                pos = None
        elif ready:
            if (liquid and c > hi60[t] and c > vah and box <= BRK_BOX and vol20[t] > 0
                    and vol[t] >= BRK_VOL * vol20[t] and above[t] <= OVERHEAD):
                pos = {"kind": "brk", "t": t, "target": None, "stop": vah, "peak": c}
            elif rebounding and c > hi10[t]:
                pos = {"kind": "reb", "t": t, "target": val, "stop": low * REB_STOP}
            elif in_range and near and not near_prev:  # 剛跌到下緣那天；一直貼著下緣不重複進場
                pos = {"kind": "rng", "t": t, "target": vah, "stop": val * RANGE_STOP}
            if pos is not None:
                marks.append((t, "B", pos["kind"], pos["target"], pos["stop"]))
        near_prev = near
        if t == T - 1:
            flags = RANGE * in_range | REBOUND * rebounding
    if pos is not None and pos["kind"] == "brk" and pos["t"] >= T - RECENT:
        flags |= BREAKOUT
    return {
        "px": px, "levels": levels, "above": above, "last_bins": last_bins, "value20": value20[T - 1],
        "marks": marks, "trades": trades, "open": pos, "flags": flags,
    }


def events(panel):
    """回測用：各種 B 訊號的 (代號, 隔天進場索引)，以及 B→S 的完整交易 (代號, 種類, 進場, 出場)。"""
    out, trades = {f"vp_{k}": [] for k in KINDS}, []
    for code, s in panel.series.items():
        r = scan(s["close"], s["vol"], s["value"], panel.index[code])
        if r is None:
            continue
        for t, side, kind, *_ in r["marks"]:
            if side == "B" and panel.tradable(code, t + 1):
                out["vp_" + kind].append((code, t + 1))
        for kind, b, x, _ in r["trades"]:
            if panel.tradable(code, b + 1) and panel.tradable(code, x + 1):
                trades.append((code, kind, b + 1, x + 1))
    return out, trades


def _px(value):
    """價格取 4 位有效數字，整數不帶小數點（減少檔案大小）。"""
    if value is None or isnan(value):
        return None
    digits = max(0, 3 - math.floor(math.log10(abs(value)))) if value else 0
    v = round(value, digits)
    return int(v) if v == int(v) else v


def _ret(idx, entry, exit_):
    a, b = idx[entry], idx[exit_]
    return None if isnan(a) or isnan(b) or a <= 0 else round((b / a - 1) * 100, 2)


def payload(panel, shares, universe, display=DISPLAY):
    """儀表板用：每檔的清單欄位（COLUMNS）與成交量分布圖資料。只收 universe 裡、今天成交值達門檻的股票。"""
    T = panel.T
    d0 = max(0, T - display)
    caps = sorted(((shares.get(code) or 0) * panel.series[code]["close"][T - 1], code)
                  for code in universe if code in panel.series and not isnan(panel.series[code]["close"][T - 1]))
    rank = {code: i + 1 for i, (_, code) in enumerate(reversed(caps))}
    cap_of = dict((code, cap) for cap, code in caps)
    rows, charts = {}, {}
    for code in sorted(universe):
        s = panel.series.get(code)
        r = scan(s["close"], s["vol"], s["value"], panel.index[code]) if s else None
        if r is None or not r["value20"] >= LIQUID:
            continue
        idx, px, lv, pos = panel.index[code], r["px"], r["levels"][T - 1], r["open"]
        c = px[T - 1]
        flags = r["flags"]
        if rank.get(code, BIG_RANK + 1) <= BIG_RANK and r["value20"] >= BIG_VALUE:
            flags |= BIG
        poc = val = vah = None
        if lv is not None:
            poc, val, vah = bin_price(lv[0], 0.5), bin_price(lv[1]), bin_price(lv[2], 1)
        stop = None
        if pos is not None:
            stop = max(pos["stop"], pos["peak"] * TRAIL) if pos["kind"] == "brk" else pos["stop"]
        has = lv is not None and not isnan(c)
        rows[code] = [
            round(cap_of[code] / 1e8) if cap_of.get(code) else None, flags,
            _px(poc), _px(val), _px(vah),
            round((c - val) / (vah - val) * 100) if has else None,
            round((val / c - 1) * 100, 1) if has else None,
            round(r["above"][T - 1] * 100) if not isnan(r["above"][T - 1]) else None,
            T - 1 - pos["t"] if pos else None, pos["kind"] if pos else None,
            _px(pos["target"]) if pos else None, _px(stop),
        ]

        # 每天的 POC、價值區下緣、上緣（格子編號），各自只記變動點：[第幾天, 格子, 第幾天, 格子, ...]
        bands = [[], [], []]
        for j, band in enumerate(bands):
            prev = False
            for t in range(d0, T):
                cur = r["levels"][t][j] if r["levels"][t] else None
                if cur != prev:
                    band += [t - d0, cur]
                    prev = cur
        hist = None  # 最後一天用的分布：[最低格子, 各格成交量佔總量的千分比]
        if lv is not None and r["last_bins"]:
            k0, k1 = min(r["last_bins"]), max(r["last_bins"])
            total = sum(r["last_bins"].values())
            hist = [k0, [round(r["last_bins"].get(k, 0) / total * 1000) for k in range(k0, k1 + 1)]]
        marks = []
        for t, side, kind, *levels_at in r["marks"]:
            if t < d0:
                continue
            if side == "B":
                target, stop_ = levels_at
                marks.append([t - d0, "B", kind, _px(target), _px(stop_)])
            else:
                marks.append([t - d0, "S", kind])
        # 交易：訊號隔天收盤進出（含配息）；S 在最後一天、或還沒出場的，先用最新收盤估算（final = 0）
        trades = []
        for kind, b, x, reason in r["trades"]:
            if x >= d0:
                trades.append([panel.dates[b], _px(px[b]), panel.dates[x], _px(px[x]),
                               _ret(idx, b + 1, min(x + 1, T - 1)), kind, reason, int(x + 1 < T)])
        if pos is not None:
            b = pos["t"]
            trades.append([panel.dates[b], _px(px[b]), None, None, _ret(idx, b + 1, T - 1) if b + 1 < T else None,
                           pos["kind"], None, 0])
        charts[code] = {"c": [_px(v) for v in px[d0:]], "b": bands, "h": hist, "m": marks, "tr": trades}
    return {
        "dates": panel.dates[d0:], "window": WINDOW, "step": STEP, "columns": COLUMNS,
        "rows": rows, "charts": charts,
    }
