"""臺灣證券交易所（上市）資料。

每日行情、三大法人、融資融券用證交所網站「可指定日期」的 JSON（能回補歷史、補抓漏掉的日子）；
OpenAPI 只提供最新一天，所以拿來抓快照型資料，以及在網站端點失敗時當備援。
"""
from . import mops
from .net import FetchError, fetch_json
from .parse import clean, is_stock, lots, num, r2, roc_to_iso, sign_of

OPENAPI = "https://openapi.twse.com.tw/v1"
WWW = "https://www.twse.com.tw/rwd/zh"
NO_DATA = "沒有符合條件的資料"

QUOTE_FIELDS = ["close", "change", "volume", "value"]  # 收盤、漲跌、成交量(張)、成交值(百萬元)
INSTI_FIELDS = ["foreign", "trust", "dealer", "total"]  # 買賣超(張)
MARGIN_FIELDS = ["margin", "margin_chg", "short", "short_chg"]  # 融資餘額、增減、融券餘額、增減(張)


def _www(path, day, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    data = fetch_json(f"{WWW}/{path}?{query}&response=json")
    stat = str(data.get("stat", ""))
    if NO_DATA in stat:
        return None
    if stat.upper() != "OK":
        raise FetchError(f"TWSE {path} {day}: stat={stat!r}")
    if data.get("date") and data["date"] != day.strftime("%Y%m%d"):
        raise FetchError(f"TWSE {path}: asked for {day}, got {data['date']}")
    return data


def _table(data, keyword):
    for table in data.get("tables") or []:
        if keyword in (table.get("title") or ""):
            return table
    return None


def _openapi(path):
    data = fetch_json(OPENAPI + path)
    if not isinstance(data, list):
        raise FetchError(f"TWSE OpenAPI {path}: unexpected payload")
    return data


def _yi(value):
    """元 -> 億元"""
    return None if value is None else round(value / 1e8, 2)


def daily_quotes(day):
    data = _www("afterTrading/MI_INDEX", day, date=day.strftime("%Y%m%d"), type="ALLBUT0999")
    table = data and _table(data, "每日收盤行情")
    if not table or not table.get("data"):
        return None
    f = table["fields"]
    i_code, i_close, i_sign, i_diff, i_vol, i_val = (
        f.index(n) for n in ("證券代號", "收盤價", "漲跌(+/-)", "漲跌價差", "成交股數", "成交金額")
    )
    rows = {}
    for row in table["data"]:
        code = clean(row[i_code])
        if not is_stock(code):
            continue
        diff, value = num(row[i_diff]), num(row[i_val])
        rows[code] = [
            num(row[i_close]),
            None if diff is None else r2(sign_of(row[i_sign]) * diff),
            lots(row[i_vol]),
            None if value is None else round(value / 1e6, 1),
        ]
    payload = {"_fields": QUOTE_FIELDS, "rows": rows}
    for row in (_table(data, "價格指數(臺灣證券交易所)") or {}).get("data", []):
        if clean(row[0]) == "發行量加權股價指數":
            diff = num(row[3])
            payload["index"] = [num(row[1]), None if diff is None else r2(sign_of(row[2]) * diff)]
    return payload


def daily_insti(day):
    data = _www("fund/T86", day, date=day.strftime("%Y%m%d"), selectType="ALLBUT0999")
    if not data or not data.get("data"):
        return None
    f = data["fields"]
    idx = [f.index(n) for n in ("外陸資買賣超股數(不含外資自營商)", "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數")]
    rows = {}
    for row in data["data"]:
        code = clean(row[0])
        if is_stock(code):
            rows[code] = [lots(row[k]) for k in idx]
    return {"_fields": INSTI_FIELDS, "rows": rows}


def daily_margin(day):
    data = _www("marginTrading/MI_MARGN", day, date=day.strftime("%Y%m%d"), selectType="ALL")
    detail = data and _table(data, "融資融券彙總")
    if not detail or not detail.get("data"):
        return None
    f = detail["fields"]
    if len(f) < 13 or f[6] != "今日餘額" or f[12] != "今日餘額":
        raise FetchError(f"TWSE MI_MARGN {day}: unexpected columns {f}")
    rows = {}
    for row in detail["data"]:
        code = clean(row[0])
        if not is_stock(code):
            continue
        m_prev, m_bal, s_prev, s_bal = (num(row[k]) or 0 for k in (5, 6, 11, 12))
        rows[code] = [int(m_bal), int(m_bal - m_prev), int(s_bal), int(s_bal - s_prev)]
    payload = {"_fields": MARGIN_FIELDS, "rows": rows}
    for row in (_table(data, "信用交易統計") or {}).get("data", []):
        if clean(row[0]).startswith("融資金額"):
            today, prev = num(row[5]), num(row[4])  # 仟元
            if today is not None and prev is not None:
                payload["amount"] = [round(today / 1e5, 2), round((today - prev) / 1e5, 2)]  # 億元
    return payload


def daily_market_insti(day):
    data = _www("fund/BFI82U", day, type="day", dayDate=day.strftime("%Y%m%d"))
    if not data or not data.get("data"):
        return None
    net = {clean(row[0]): num(row[3]) for row in data["data"]}
    return {
        "foreign": _yi(net.get("外資及陸資(不含外資自營商)")),
        "trust": _yi(net.get("投信")),
        "dealer": _yi((net.get("自營商(自行買賣)") or 0) + (net.get("自營商(避險)") or 0)),
        "total": _yi(net.get("合計")),
    }


def latest_quotes_openapi():
    """備援：OpenAPI 只有最新一天。回傳 (日期, payload)。"""
    rows, day = {}, None
    for r in _openapi("/exchangeReport/STOCK_DAY_ALL"):
        code = clean(r.get("Code"))
        if not is_stock(code):
            continue
        day = day or roc_to_iso(r["Date"])
        value = num(r.get("TradeValue"))
        rows[code] = [
            num(r.get("ClosingPrice")),
            r2(num(r.get("Change"))),
            lots(r.get("TradeVolume")),
            None if value is None else round(value / 1e6, 1),
        ]
    payload = {"_fields": QUOTE_FIELDS, "rows": rows, "source": "openapi"}
    for r in _openapi("/exchangeReport/MI_INDEX"):
        if clean(r.get("指數")) == "發行量加權股價指數":
            diff = num(r.get("漲跌點數"))
            payload["index"] = [num(r.get("收盤指數")), None if diff is None else r2(sign_of(r.get("漲跌")) * diff)]
    return day, payload


def valuation():
    rows, day = {}, None
    for r in _openapi("/exchangeReport/BWIBBU_ALL"):
        code = clean(r.get("Code"))
        if is_stock(code):
            day = day or roc_to_iso(r["Date"])
            rows[code] = [num(r.get("PEratio")), num(r.get("DividendYield")), num(r.get("PBratio"))]
    return day, rows


def revenue():
    return mops.parse_revenue(_openapi("/opendata/t187ap05_L"))


def insider():
    return mops.parse_insider(_openapi("/opendata/t187ap12_L"), "上市")


def companies():
    out = {}
    for r in _openapi("/opendata/t187ap03_L"):
        code = clean(r.get("公司代號"))
        if is_stock(code):
            out[code] = {
                "name": clean(r.get("公司簡稱")),
                "market": "上市",
                "industry_code": clean(r.get("產業別")),
                "shares": int(num(r.get("已發行普通股數或TDR原股發行股數")) or 0),
            }
    return out


def closed_days():
    """休市日（不含「開始交易日」「最後交易日」這類其實有開市的條目）。"""
    out = {}
    for r in _openapi("/holidaySchedule/holidaySchedule"):
        name = clean(r.get("Name"))
        if "開始交易" not in name and "最後交易" not in name:
            out[roc_to_iso(r["Date"])] = name
    return out
