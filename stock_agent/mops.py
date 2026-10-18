"""公開資訊觀測站（MOPS）資料的共用解析：月營收、內部人持股轉讓事前申報、產業別名稱。

證交所與櫃買中心的 OpenAPI 都轉載了這些 MOPS 資料表，欄位名稱幾乎相同。
"""
from .parse import clean, is_stock, lots, num, r2, roc_month_to_iso, roc_to_iso

REVENUE_FIELDS = ["revenue", "yoy", "mom", "cum_yoy", "revenue_last_year", "industry", "note"]

# 產業別代碼（月營收資料沒有涵蓋的公司才會用到）
INDUSTRY_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "14": "建材營造業", "15": "航運業", "16": "觀光餐旅", "17": "金融保險業",
    "18": "貿易百貨業", "19": "綜合", "20": "其他業", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證",
}


def parse_revenue(records):
    """Return {'2026-09': {code: [revenue(千元), yoy%, mom%, cum_yoy%, revenue_last_year, industry, note]}}."""
    months = {}
    for r in records:
        code = clean(r.get("公司代號"))
        if not is_stock(code) or not r.get("資料年月"):
            continue
        month = roc_month_to_iso(r["資料年月"])
        note = clean(r.get("備註"))
        revenue, last_year = num(r.get("營業收入-當月營收")), num(r.get("營業收入-去年當月營收"))
        months.setdefault(month, {})[code] = [
            None if revenue is None else int(revenue),
            r2(num(r.get("營業收入-去年同月增減(%)"))),
            r2(num(r.get("營業收入-上月比較增減(%)"))),
            r2(num(r.get("累計營業收入-前期比較增減(%)"))),
            None if last_year is None else int(last_year),
            clean(r.get("產業別")),
            "" if note in ("-", "－") else note[:120],
        ]
    return months


def parse_insider(records, market):
    """內部人持股轉讓事前申報。只留下身分與股數，不保存申報人姓名。"""
    out = []
    for r in records:
        code = clean(r.get("公司代號") or r.get("SecuritiesCompanyCode"))
        if not is_stock(code):
            continue
        own = num(r.get("預定轉讓總股數-自有持股")) or 0
        trust = num(r.get("預定轉讓總股數-保留運用決定權信託股數")) or 0
        held = (num(r.get("目前持有股數-自有持股")) or 0) + (num(r.get("目前持有股數-保留運用決定權信託股數")) or 0)
        out.append({
            "date": roc_to_iso(r.get("出表日期") or r.get("Date")),
            "code": code,
            "name": clean(r.get("公司名稱") or r.get("CompanyName")),
            "market": market,
            "role": clean(r.get("申報人身分") or r.get("申請人身分")),
            "method": clean(r.get("預定轉讓方式及股數-轉讓方式")),
            "lots": lots(own + trust),
            "held_lots": lots(held),
            "period": clean(r.get("有效轉讓期間")),
        })
    return out
