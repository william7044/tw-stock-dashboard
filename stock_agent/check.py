"""連線檢查：每個資料來源各發一個小請求，確認這台機器（例如 GitHub Actions 的海外機房）抓得到。

    py -m stock_agent.check
輸出是 Markdown 表格；任何一個來源失敗時結束碼為 1。
"""
import sys
import time
from datetime import date, timedelta

from . import tdcc, tpex, twse
from .net import FetchError


def _last_weekday():
    day = date.today() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


CHECKS = [
    ("證交所網站（三大法人金額）", lambda: twse.daily_market_insti(_last_weekday())),
    ("證交所 OpenAPI（休市日）", twse.closed_days),
    ("櫃買網站（櫃買指數月查詢）", lambda: tpex.index_history(_last_weekday().strftime("%Y-%m"))),
    ("櫃買 OpenAPI（櫃買指數）", tpex.index_month),
    ("集保開放資料（股權分散表）", tdcc.shareholding),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("| 資料來源 | 結果 | 耗時 |\n|---|---|---|")
    failed = 0
    for label, check in CHECKS:
        start = time.monotonic()
        try:
            check()
            result = "✅ 可連線"
        except (FetchError, KeyError, ValueError) as e:
            result, failed = f"❌ {str(e)[:120]}", failed + 1
        print(f"| {label} | {result} | {time.monotonic() - start:.1f} 秒 |")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
