"""一鍵更新資料並產生儀表板。

    py run.py                     # 抓最新資料 + 產生 docs/index.html
    py run.py --backfill-days 40  # 第一次執行：先回補 40 個交易日
    py run.py --skip-update       # 不抓資料，只用現有資料重新產生儀表板
"""
import argparse

from stock_agent import dashboard, update


def main():
    parser = argparse.ArgumentParser(description="台股籌碼儀表板")
    parser.add_argument("--backfill-days", type=int, default=0, help="確保最近 N 個交易日都有資料")
    parser.add_argument("--history-months", type=int, default=0, help="回補最近 N 個月的月營收與估值快照（回測用）")
    parser.add_argument("--skip-update", action="store_true", help="只重新產生儀表板")
    args = parser.parse_args()
    if not args.skip_update:
        update.run(backfill_days=args.backfill_days, history_months=args.history_months)
    print(f"儀表板已產生：{dashboard.build()}")


if __name__ == "__main__":
    main()
