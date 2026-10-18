"""集保結算所：集保戶股權分散表（每週一次，只提供最新一週）。

持股分級 1~15 依股數由小到大，第 15 級是 1,000,001 股以上（千張大戶）；17 是合計。
"""
import csv
import io

from .net import fetch_text
from .parse import is_stock, ymd_to_iso

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
# 每檔：[集保總人數, 集保總股數, 第1級%, ..., 第15級%]
FIELDS = ["holders", "shares"] + [f"p{level}" for level in range(1, 16)]


def shareholding():
    reader = csv.reader(io.StringIO(fetch_text(URL)))
    next(reader, None)  # 資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
    day, rows = None, {}
    for rec in reader:
        if len(rec) < 6:
            continue
        code = rec[1].strip()
        if not is_stock(code):
            continue
        day = day or ymd_to_iso(rec[0])
        level = int(rec[2])
        entry = rows.setdefault(code, [0, 0] + [0.0] * 15)
        if 1 <= level <= 15:
            entry[1 + level] = float(rec[5])
        elif level == 17:
            entry[0], entry[1] = int(rec[3]), int(rec[4])
    return day, rows
