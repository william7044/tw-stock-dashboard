"""抓資料：補齊最近幾個交易日的每日資料，並更新快照型資料（月營收、本益比、集保、內部人申報）。

用法：
    py -m stock_agent.update                    # 一般更新（自動補抓最近 7 個交易日缺的部分）
    py -m stock_agent.update --backfill-days 40 # 第一次執行：回補 40 個交易日
"""
import argparse
from datetime import date, datetime, timedelta, timezone

from . import mops, tdcc, tpex, twse
from .net import FetchError
from .store import Store

TAIPEI = timezone(timedelta(hours=8))
CATCH_UP_DAYS = 7  # 每次都檢查最近 7 個交易日有沒有缺資料（例如前一天排程沒跑成功）
DATA_READY_HOUR = 15  # 台灣時間 15:00 前不抓當天，收盤資料還沒公布

DAILY_PARTS = [
    ("twse", "quotes", twse.daily_quotes),
    ("tpex", "quotes", tpex.daily_quotes),
    ("twse", "insti", twse.daily_insti),
    ("tpex", "insti", tpex.daily_insti),
    ("twse", "margin", twse.daily_margin),
    ("tpex", "margin", tpex.daily_margin),
    ("twse", "market_insti", twse.daily_market_insti),
]
# 網站端點失敗時，如果 OpenAPI 的「最新一天」剛好就是要補的那天，就改用 OpenAPI。
FALLBACKS = {
    ("twse", "quotes"): twse.latest_quotes_openapi,
    ("tpex", "quotes"): tpex.latest_quotes_openapi,
    ("tpex", "insti"): tpex.latest_insti_openapi,
    ("tpex", "margin"): tpex.latest_margin_openapi,
}


class Updater:
    def __init__(self, store, now):
        self.store = store
        self.now = now
        self.warnings = []
        self.closed = store.read("meta", "closed_days.json", default={})
        self.new_days = []
        self.changed = False  # 有任何檔案內容改變才需要重寫 state.json、提交到 git

    def warn(self, message):
        print("  ! " + message)
        self.warnings.append(message)

    def write(self, obj, *parts):
        wrote = self.store.write(obj, *parts)
        self.changed = self.changed or wrote
        return wrote

    def save_day(self, doc):
        wrote = self.store.save_day(doc)
        self.changed = self.changed or wrote
        return wrote

    # ---- 每日資料 ----
    def candidate_days(self, count):
        last = self.now.date() if self.now.hour >= DATA_READY_HOUR else self.now.date() - timedelta(days=1)
        days, day = [], last
        while len(days) < count and day > last - timedelta(days=count * 2 + 21):
            if day.weekday() < 5 and day.isoformat() not in self.closed:
                days.append(day)
            day -= timedelta(days=1)
        return sorted(days)

    def fetch_part(self, market, part, fetch, day, latest):
        """回傳 (payload, failed)。payload 為 None 且 failed 為 False 代表當天沒有資料。"""
        try:
            return fetch(day), False
        except FetchError as e:
            self.warn(f"{day} {market}.{part}: {e}")
        fallback = FALLBACKS.get((market, part))
        if fallback and day == latest:
            try:
                fb_day, payload = fallback()
                if fb_day == day.isoformat():
                    print(f"    {market}.{part} 改用 OpenAPI 備援")
                    return payload, False
            except FetchError as e:
                self.warn(f"{day} {market}.{part} OpenAPI 備援也失敗: {e}")
        return None, True

    def update_day(self, day, latest):
        iso = day.isoformat()
        doc = self.store.load_day(iso)
        fetched = []
        for market, part, fetch in DAILY_PARTS:
            if part in doc.get(market, {}):
                continue
            payload, failed = self.fetch_part(market, part, fetch, day, latest)
            if payload is not None:
                doc.setdefault(market, {})[part] = payload
                fetched.append(f"{market}.{part}")
            elif (market, part) == ("twse", "quotes"):
                # 上市行情是判斷「有沒有開市」的依據：過去的平日查不到行情 = 休市（含颱風假）
                if not failed and day < self.now.date():
                    self.closed[iso] = "查無交易資料"
                break
        if fetched:
            self.save_day(doc)
            self.new_days.append(iso)
            print(f"  {iso}: {', '.join(fetched)}")

    def update_daily(self, backfill_days):
        try:
            self.closed.update(twse.closed_days())
        except FetchError as e:
            self.warn(f"休市日: {e}")
        days = self.candidate_days(max(CATCH_UP_DAYS, backfill_days))
        if not days:
            return
        print(f"檢查 {days[0]} ~ {days[-1]} 共 {len(days)} 個平日")
        for day in days:
            self.update_day(day, days[-1])
        self.write(self.closed, "meta", "closed_days.json")

    # ---- 快照型資料 ----
    def snapshot(self, label, func):
        try:
            func()
        except (FetchError, KeyError, ValueError) as e:
            self.warn(f"{label}: {e}")

    def update_revenue(self):
        months = {}
        for source in (twse.revenue, tpex.revenue):
            for month, rows in source().items():
                months.setdefault(month, {}).update(rows)
        for month, rows in months.items():
            doc = self.store.read("revenue", f"{month}.json", default={"month": month, "rows": {}})
            doc["rows"].update(rows)
            doc["_fields"] = mops.REVENUE_FIELDS
            if self.write(doc, "revenue", f"{month}.json"):
                print(f"  月營收 {month}: {len(doc['rows'])} 家")

    def update_companies(self):
        meta = self.store.read("meta", "companies.json", default={})
        if meta.get("updated") and meta["updated"] >= (self.now.date() - timedelta(days=6)).isoformat():
            return
        industries = {}
        for path in self.store.files("revenue")[-3:]:
            for code, row in self.store.read("revenue", path.name, default={}).get("rows", {}).items():
                if row[5]:
                    industries[code] = row[5]
        rows = {}
        for code, info in {**twse.companies(), **tpex.companies()}.items():
            industry_code = info.pop("industry_code", "")
            info["industry"] = industries.get(code) or mops.INDUSTRY_NAMES.get(industry_code, "其他")
            rows[code] = info
        self.write({"updated": self.now.date().isoformat(), "rows": rows}, "meta", "companies.json")
        print(f"  公司基本資料: {len(rows)} 家")

    def update_valuation(self):
        merged, first_day = {}, None
        for market, source in (("twse", twse.valuation), ("tpex", tpex.valuation)):
            day, rows = source()
            if self.write({"date": day, "_fields": ["pe", "yield", "pb"], "rows": rows}, "valuation", f"{market}.json"):
                print(f"  本益比/殖利率 {market} {day}: {len(rows)} 檔")
            merged.update(rows)
            first_day = first_day or day
        # 每個月留一份快照，給回測用（高殖利率訊號每月檢查一次）
        if first_day and not list(self.store.path("valuation", "history").glob(f"{first_day[:7]}-*.json")):
            self.write({"date": first_day, "_fields": ["pe", "yield", "pb"], "rows": merged}, "valuation", "history", f"{first_day}.json")

    # ---- 回測用的歷史資料（一次性回補） ----
    def update_history(self, months):
        """回補最近 N 個月的月營收（MOPS）與每月第一個交易日的本益比/殖利率快照。"""
        print(f"回補 {months} 個月的月營收與估值快照")
        year, mon = self.now.year, self.now.month
        wanted = []
        for _ in range(months):
            year, mon = (year, mon - 1) if mon > 1 else (year - 1, 12)
            wanted.append(f"{year:04d}-{mon:02d}")
        for month in sorted(wanted):
            if self.store.path("revenue", f"{month}.json").exists():
                continue
            try:
                rows = mops.revenue_history(month)
            except FetchError as e:
                self.warn(f"月營收 {month}: {e}")
                continue
            if rows:
                self.write({"month": month, "_fields": mops.REVENUE_FIELDS, "rows": rows}, "revenue", f"{month}.json")
                print(f"  月營收 {month}: {len(rows)} 家（MOPS）")

        firsts = {}
        for iso in self.store.day_dates():
            firsts.setdefault(iso[:7], iso)
        for month, iso in sorted(firsts.items())[-months:]:
            if list(self.store.path("valuation", "history").glob(f"{month}-*.json")):
                continue
            rows = {}
            for label, source in (("上市", twse.valuation_on), ("上櫃", tpex.valuation_on)):
                try:
                    rows.update(source(date.fromisoformat(iso)) or {})
                except FetchError as e:
                    self.warn(f"本益比 {label} {iso}: {e}")
            if rows:
                self.write({"date": iso, "_fields": ["pe", "yield", "pb"], "rows": rows}, "valuation", "history", f"{iso}.json")
                print(f"  估值快照 {iso}: {len(rows)} 檔")

    def update_insider(self):
        records = twse.insider() + tpex.insider()
        by_month = {}
        for rec in records:
            by_month.setdefault(rec["date"][:7], []).append(rec)
        for month, recs in by_month.items():
            existing = self.store.read("insider", f"{month}.json", default=[])
            seen = {tuple(sorted(r.items())) for r in existing}
            added = [r for r in recs if tuple(sorted(r.items())) not in seen]
            if added:
                self.write(existing + added, "insider", f"{month}.json")
                print(f"  內部人申報 {month}: +{len(added)} 筆")

    def update_tdcc(self):
        files = self.store.files("tdcc")
        if files and files[-1].stem > (self.now.date() - timedelta(days=7)).isoformat():
            return  # 每週一次，最新一週已經有了
        day, rows = tdcc.shareholding()
        if day and not self.store.path("tdcc", f"{day}.json").exists():
            self.write({"date": day, "_fields": tdcc.FIELDS, "rows": rows}, "tdcc", f"{day}.json")
            print(f"  集保股權分散 {day}: {len(rows)} 檔")

    def update_tpex_extras(self):
        """櫃買指數與上櫃三大法人金額，寫進對應日期的每日檔（缺的舊月份用月查詢補）。"""
        index = tpex.index_month()
        insti_day, insti = tpex.market_insti_latest()
        docs = {iso: self.store.load_day(iso) for iso in self.store.day_dates()[-60:]}
        missing = sorted({iso[:7] for iso, doc in docs.items() if iso not in index and "index" not in doc.get("tpex", {})})
        for month in missing:
            try:
                index.update(tpex.index_history(month))
            except FetchError as e:
                self.warn(f"櫃買指數 {month}: {e}")
        for iso, doc in docs.items():
            tp = doc.setdefault("tpex", {})
            changed = False
            if iso in index and "index" not in tp:
                tp["index"], changed = index[iso], True
            if iso == insti_day and insti and "market_insti" not in tp:
                tp["market_insti"], changed = insti, True
            if changed:
                self.save_day(doc)

    def update_snapshots(self):
        print("更新快照型資料")
        self.snapshot("月營收", self.update_revenue)
        self.snapshot("公司基本資料", self.update_companies)
        self.snapshot("本益比/殖利率", self.update_valuation)
        self.snapshot("內部人申報", self.update_insider)
        self.snapshot("集保股權分散", self.update_tdcc)
        self.snapshot("櫃買指數/法人金額", self.update_tpex_extras)


def run(backfill_days=0, history_months=0, store=None, now=None):
    store = store or Store()
    updater = Updater(store, now or datetime.now(TAIPEI))
    updater.update_daily(backfill_days)
    if history_months:
        updater.update_history(history_months)
    updater.update_snapshots()
    warnings = updater.warnings[-30:]
    if updater.changed or warnings != store.read("state.json", default={}).get("warnings", []):
        store.write(
            {"updated_at": updater.now.isoformat(timespec="minutes"), "new_days": updater.new_days, "warnings": warnings},
            "state.json",
        )
    print(f"完成：新增/補齊 {len(updater.new_days)} 天，警告 {len(updater.warnings)} 則"
          + ("" if updater.changed else "（資料沒有變化）"))
    return updater


def main(argv=None):
    parser = argparse.ArgumentParser(description="更新台股免費資料")
    parser.add_argument("--backfill-days", type=int, default=0, help="確保最近 N 個交易日都有資料（第一次建議 40）")
    parser.add_argument("--history-months", type=int, default=0, help="回補最近 N 個月的月營收與估值快照（回測用）")
    args = parser.parse_args(argv)
    run(backfill_days=args.backfill_days, history_months=args.history_months)


if __name__ == "__main__":
    main()
