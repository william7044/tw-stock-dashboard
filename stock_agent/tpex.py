"""櫃買中心（上櫃）資料。結構與 twse.py 對應：網站 JSON 可指定日期，OpenAPI 抓快照與當備援。"""
from . import mops
from .net import FetchError, fetch_json
from .parse import any_date_to_iso, clean, is_stock, lots, num, r2, roc_to_iso, ymd_to_iso
from .twse import INSTI_FIELDS, MARGIN_FIELDS, QUOTE_FIELDS

OPENAPI = "https://www.tpex.org.tw/openapi/v1"
WWW = "https://www.tpex.org.tw/www/zh-tw"


def _www(path, day, **params):
    query = "".join(f"{k}={v}&" for k, v in params.items())
    data = fetch_json(f"{WWW}/{path}?{query}date={day:%Y/%m/%d}&id=&response=json")
    if str(data.get("stat", "")).lower() != "ok":
        raise FetchError(f"TPEx {path} {day}: stat={data.get('stat')!r}")
    if data.get("date") and data["date"] != day.strftime("%Y%m%d"):
        raise FetchError(f"TPEx {path}: asked for {day}, got {data['date']}")
    tables = data.get("tables") or []
    if not tables or not tables[0].get("data"):
        return None
    return tables[0]


def _cols(fields, *names):
    stripped = [clean(f).replace("<br>", "").replace(" ", "") for f in fields]
    return [stripped.index(n) for n in names]


def _openapi(path):
    data = fetch_json(OPENAPI + path)
    if not isinstance(data, list):
        raise FetchError(f"TPEx OpenAPI {path}: unexpected payload")
    return data


def _yi(value):
    """元 -> 億元"""
    return None if value is None else round(value / 1e8, 2)


def daily_quotes(day):
    table = _www("afterTrading/otc", day, type="EW")
    if table is None:
        return None
    i_code, i_close, i_chg, i_vol, i_val = _cols(table["fields"], "代號", "收盤", "漲跌", "成交股數", "成交金額(元)")
    rows = {}
    for row in table["data"]:
        code = clean(row[i_code])
        if is_stock(code):
            value = num(row[i_val])
            rows[code] = [
                num(row[i_close]),
                r2(num(row[i_chg])),
                lots(row[i_vol]),
                None if value is None else round(value / 1e6, 1),
            ]
    return {"_fields": QUOTE_FIELDS, "rows": rows}


def daily_insti(day):
    table = _www("insti/dailyTrade", day, type="Daily", sect="EW")
    if table is None:
        return None
    if len(table["fields"]) < 24:
        raise FetchError(f"TPEx insti {day}: unexpected columns {table['fields']}")
    rows = {}
    for row in table["data"]:
        code = clean(row[0])
        if is_stock(code):
            # 4: 外資及陸資(不含外資自營商) 13: 投信 22: 自營商合計 23: 三大法人合計
            rows[code] = [lots(row[4]), lots(row[13]), lots(row[22]), lots(row[23])]
    return {"_fields": INSTI_FIELDS, "rows": rows}


def daily_margin(day):
    table = _www("margin/balance", day)
    if table is None:
        return None
    i_code, i_mp, i_mb, i_sp, i_sb = _cols(table["fields"], "代號", "前資餘額(張)", "資餘額", "前券餘額(張)", "券餘額")
    rows = {}
    for row in table["data"]:
        code = clean(row[i_code])
        if is_stock(code):
            m_prev, m_bal, s_prev, s_bal = (num(row[k]) or 0 for k in (i_mp, i_mb, i_sp, i_sb))
            rows[code] = [int(m_bal), int(m_bal - m_prev), int(s_bal), int(s_bal - s_prev)]
    return {"_fields": MARGIN_FIELDS, "rows": rows}


# ---- OpenAPI：最新一天的備援 ----

def latest_quotes_openapi():
    rows, day = {}, None
    for r in _openapi("/tpex_mainboard_daily_close_quotes"):
        code = clean(r.get("SecuritiesCompanyCode"))
        if is_stock(code):
            day = day or roc_to_iso(r["Date"])
            value = num(r.get("TransactionAmount"))
            rows[code] = [
                num(r.get("Close")),
                r2(num(r.get("Change"))),
                lots(r.get("TradingShares")),
                None if value is None else round(value / 1e6, 1),
            ]
    return day, {"_fields": QUOTE_FIELDS, "rows": rows, "source": "openapi"}


def latest_insti_openapi():
    keys = (
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference",
        "SecuritiesInvestmentTrustCompanies-Difference",
        "Dealers-Difference",
        "TotalDifference",
    )
    rows, day = {}, None
    for r in _openapi("/tpex_3insti_daily_trading"):
        code = clean(r.get("SecuritiesCompanyCode"))
        if is_stock(code):
            day = day or roc_to_iso(r["Date"])
            rows[code] = [lots(r.get(k)) for k in keys]
    return day, {"_fields": INSTI_FIELDS, "rows": rows, "source": "openapi"}


def latest_margin_openapi():
    rows, day = {}, None
    for r in _openapi("/tpex_mainboard_margin_balance"):
        code = clean(r.get("SecuritiesCompanyCode"))
        if is_stock(code):
            day = day or roc_to_iso(r["Date"])
            m_prev, m_bal, s_prev, s_bal = (
                num(r.get(k)) or 0
                for k in ("MarginPurchaseBalancePreviousDay", "MarginPurchaseBalance",
                          "ShortSaleBalancePreviousDay", "ShortSaleBalance")
            )
            rows[code] = [int(m_bal), int(m_bal - m_prev), int(s_bal), int(s_bal - s_prev)]
    return day, {"_fields": MARGIN_FIELDS, "rows": rows, "source": "openapi"}


def market_insti_latest():
    """上櫃三大法人買賣金額（億元）。"""
    rows = _openapi("/tpex_3insti_summary")
    if not rows:
        return None, None
    net = {clean(r.get("Investor")).rstrip("*"): num(r.get("Net")) for r in rows}
    return roc_to_iso(rows[0]["Date"]), {
        "foreign": _yi(net.get("外資及陸資(不含自營商)")),
        "trust": _yi(net.get("投信")),
        "dealer": _yi(net.get("自營商合計")),
        "total": _yi(net.get("三大法人合計")),
    }


def index_month():
    """櫃買指數（本月每日收盤）：{日期: [收盤, 漲跌]}。"""
    return {ymd_to_iso(r["Date"]): [num(r.get("Close")), r2(num(r.get("Change")))] for r in _openapi("/tpex_index")}


def index_history(month):
    """櫃買指數某個月（'2026-09'）的每日收盤；OpenAPI 只有本月，回補舊月份用網站的月查詢。"""
    year, mon = int(month[:4]), int(month[5:7])
    data = fetch_json(
        "https://www.tpex.org.tw/web/stock/iNdex_info/inxh/Inx_result.php"
        f"?l=zh-tw&d={year - 1911}/{mon:02d}&o=json"
    )
    tables = data.get("tables") or []
    out = {}
    for row in (tables[0].get("data") if tables else None) or []:
        iso = any_date_to_iso(row[0])
        if iso.startswith(month):
            out[iso] = [num(row[4]), r2(num(row[5]))]
    return out


def valuation():
    rows, day = {}, None
    for r in _openapi("/tpex_mainboard_peratio_analysis"):
        code = clean(r.get("SecuritiesCompanyCode"))
        if is_stock(code):
            day = day or roc_to_iso(r["Date"])
            rows[code] = [num(r.get("PriceEarningRatio")), num(r.get("YieldRatio")), num(r.get("PriceBookRatio"))]
    return day, rows


def exrights(start, end):
    """除權除息計算結果表：{日期: {代號: 除權息參考價}}。"""
    data = fetch_json(f"{WWW}/bulletin/exDailyQ?startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}&id=&response=json")
    if str(data.get("stat", "")).lower() != "ok":
        raise FetchError(f"TPEx exDailyQ: stat={data.get('stat')!r}")
    tables = data.get("tables") or []
    if not tables or not tables[0].get("data"):
        return {}
    i_day, i_code, i_ref = _cols(tables[0]["fields"], "除權息日期", "代號", "除權息參考價")
    out = {}
    for row in tables[0]["data"]:
        ref = num(row[i_ref])
        if ref:
            out.setdefault(roc_to_iso(row[i_day]), {})[clean(row[i_code])] = ref
    return out


def valuation_on(day):
    """指定日期的本益比、殖利率、淨值比（回測用的每月快照）。"""
    table = _www("afterTrading/peQryDate", day)
    if table is None:
        return None
    i_code, i_pe, i_yield, i_pb = _cols(table["fields"], "股票代號", "本益比", "殖利率(%)", "股價淨值比")
    return {
        clean(r[i_code]): [num(r[i_pe]), num(r[i_yield]), num(r[i_pb])]
        for r in table["data"] if is_stock(clean(r[i_code]))
    }


def revenue():
    return mops.parse_revenue(_openapi("/mopsfin_t187ap05_O"))


def insider():
    return mops.parse_insider(_openapi("/mopsfin_t187ap12_O"), "上櫃")


def companies():
    out = {}
    for r in _openapi("/mopsfin_t187ap03_O"):
        code = clean(r.get("SecuritiesCompanyCode"))
        if is_stock(code):
            out[code] = {
                "name": clean(r.get("CompanyAbbreviation")),
                "market": "上櫃",
                "industry_code": clean(r.get("SecuritiesIndustryCode")),
                "shares": int(num(r.get("IssueShares")) or 0),
            }
    return out
