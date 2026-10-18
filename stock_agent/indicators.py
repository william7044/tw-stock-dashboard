"""技術指標（純 Python）：移動平均、EMA、RSI、MACD、前 N 日高低點、百分位。

輸入是還原股價序列（list 或 array，缺值為 NaN，只會出現在序列開頭）。
"""
from bisect import bisect_left
from collections import deque

NAN = float("nan")


def isnan(value):
    return value != value


def last_mean(values, n):
    """最後 n 個值的平均；不足 n 個有效值就回傳 NaN。"""
    window = [v for v in values[-n:] if not isnan(v)]
    return sum(window) / n if len(window) == n else NAN


def sma(values, n):
    """每天的 n 日簡單移動平均（不足 n 個有效值為 NaN）。"""
    out, window, total = [NAN] * len(values), deque(), 0.0
    for i, v in enumerate(values):
        if isnan(v):
            continue
        window.append(v)
        total += v
        if len(window) > n:
            total -= window.popleft()
        if len(window) == n:
            out[i] = total / n
    return out


def ema(values, n):
    """指數移動平均，前 n 個有效值用簡單平均起算。"""
    out, k, prev, seed = [NAN] * len(values), 2 / (n + 1), NAN, []
    for i, v in enumerate(values):
        if isnan(v):
            continue
        if isnan(prev):
            seed.append(v)
            if len(seed) == n:
                prev = out[i] = sum(seed) / n
            continue
        prev = out[i] = v * k + prev * (1 - k)
    return out


def rsi(values, n=14):
    """Wilder RSI。"""
    out, prev, count, gains, losses = [NAN] * len(values), NAN, 0, 0.0, 0.0
    avg_gain = avg_loss = NAN
    for i, v in enumerate(values):
        if isnan(v):
            continue
        if isnan(prev):
            prev = v
            continue
        change, prev = v - prev, v
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if count < n:
            gains, losses, count = gains + gain, losses + loss, count + 1
            if count < n:
                continue
            avg_gain, avg_loss = gains / n, losses / n
        else:
            avg_gain = (avg_gain * (n - 1) + gain) / n
            avg_loss = (avg_loss * (n - 1) + loss) / n
        if avg_loss == 0:
            out[i] = 50.0 if avg_gain == 0 else 100.0
        else:
            out[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values, fast=12, slow=26, signal=9):
    """回傳 (DIF, DEA)：DIF = EMA12 − EMA26，DEA = DIF 的 9 日 EMA。"""
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    dif = [a - b if not (isnan(a) or isnan(b)) else NAN for a, b in zip(fast_ema, slow_ema)]
    return dif, ema(dif, signal)


def prior_extremes(values, n, min_days):
    """每天「之前 n 天（不含當天）」的最高與最低值；歷史少於 min_days 天時為 NaN。"""
    highs, lows = [NAN] * len(values), [NAN] * len(values)
    first = next((i for i, v in enumerate(values) if not isnan(v)), None)
    max_q, min_q = deque(), deque()
    for i, v in enumerate(values):
        while max_q and max_q[0][0] < i - n:
            max_q.popleft()
        while min_q and min_q[0][0] < i - n:
            min_q.popleft()
        if first is not None and i - first >= min_days and max_q:
            highs[i], lows[i] = max_q[0][1], min_q[0][1]
        if not isnan(v):
            while max_q and max_q[-1][1] <= v:
                max_q.pop()
            max_q.append((i, v))
            while min_q and min_q[-1][1] >= v:
                min_q.pop()
            min_q.append((i, v))
    return highs, lows


def percent_rank(sorted_population, value):
    """value 贏過母體中百分之幾的值（0～100）。sorted_population 必須已排序。"""
    if not sorted_population:
        return None
    return round(bisect_left(sorted_population, value) / len(sorted_population) * 100)
