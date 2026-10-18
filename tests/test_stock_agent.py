"""離線單元測試：解析器、指標計算、交易日判斷。不需要網路。

    py -m unittest discover -s tests
"""
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

from stock_agent import analyze, mops, parse, tdcc, tpex, twse, update
from stock_agent.net import FetchError
from stock_agent.store import Store

DAY = date(2026, 10, 16)


class ParseTests(unittest.TestCase):
    def test_num(self):
        self.assertEqual(parse.num("1,234.5"), 1234.5)
        self.assertEqual(parse.num("+0.05"), 0.05)
        self.assertEqual(parse.num("-0.65 "), -0.65)
        self.assertEqual(parse.num("<p>12</p>"), 12.0)
        for blank in ("", "--", "----", "X", None, "除息"):
            self.assertIsNone(parse.num(blank))

    def test_sign_of(self):
        self.assertEqual(parse.sign_of("<p style ='color:red'>+</p>"), 1)
        self.assertEqual(parse.sign_of("<p style= color:green>-</p>"), -1)
        self.assertEqual(parse.sign_of(" "), 0)
        self.assertEqual(parse.sign_of("X"), 0)

    def test_dates(self):
        self.assertEqual(parse.roc_to_iso("1151016"), "2026-10-16")
        self.assertEqual(parse.roc_to_iso("115/10/16"), "2026-10-16")
        self.assertEqual(parse.any_date_to_iso("20261016"), "2026-10-16")
        self.assertEqual(parse.roc_month_to_iso("11509"), "2026-09")

    def test_lots_and_codes(self):
        self.assertEqual(parse.lots("41,260,031"), 41260)
        self.assertEqual(parse.lots("-499"), 0)
        self.assertTrue(parse.is_stock("2330"))
        for code in ("0050", "00919", "00400A", "2881A", "910322", ""):
            self.assertFalse(parse.is_stock(code))


class TwseTests(unittest.TestCase):
    QUOTES = {
        "stat": "OK", "date": "20261016",
        "tables": [
            {"title": "115年10月16日 價格指數(臺灣證券交易所)", "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
             "data": [["發行量加權股價指數", "47,111.26", "<p style ='color:red'>+</p>", "141.39", "0.30", ""]]},
            {"title": "115年10月16日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))",
             "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"],
             "data": [
                 ["2330", "台積電", "43,046,812", "1", "104,583,123,456", "1", "1", "1", "2,420.00", "<p style= color:green>-</p>", "20.00", "", "", "", "", "28.05"],
                 ["0050", "元大台灣50", "1,000", "1", "1,000", "1", "1", "1", "1.00", " ", "0.00", "", "", "", "", ""],
             ]},
        ],
    }

    def test_daily_quotes(self):
        with mock.patch.object(twse, "fetch_json", return_value=self.QUOTES):
            payload = twse.daily_quotes(DAY)
        self.assertEqual(payload["rows"], {"2330": [2420.0, -20.0, 43047, 104583.1]})
        self.assertEqual(payload["index"], [47111.26, 141.39])

    def test_no_data_and_wrong_date(self):
        with mock.patch.object(twse, "fetch_json", return_value={"stat": "很抱歉，沒有符合條件的資料!"}):
            self.assertIsNone(twse.daily_quotes(DAY))
        with mock.patch.object(twse, "fetch_json", return_value={**self.QUOTES, "date": "20261015"}):
            with self.assertRaises(FetchError):
                twse.daily_quotes(DAY)

    def test_daily_insti_uses_field_names(self):
        fields = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)",
                  "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數", "投信買進股數", "投信賣出股數", "投信買賣超股數",
                  "自營商買賣超股數", "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
                  "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]
        row = ["2002", "中鋼            ", "0", "0", "43,728,946", "0", "0", "0", "0", "0", "27,000", "506,725",
               "0", "0", "50,000", "0", "0", "456,725", "44,262,671"]
        with mock.patch.object(twse, "fetch_json", return_value={"stat": "OK", "date": "20261016", "fields": fields, "data": [row]}):
            payload = twse.daily_insti(DAY)
        self.assertEqual(payload["rows"]["2002"], [43729, 27, 507, 44263])


class TpexTests(unittest.TestCase):
    def test_daily_insti_columns(self):
        row = ["6488", "環球晶"] + ["0"] * 22
        row[4], row[13], row[22], row[23] = "-4,456,672", "221,714", "-147,433", "-4,382,391"
        data = {"stat": "ok", "date": "20261016", "tables": [{"fields": ["x"] * 24, "data": [row]}]}
        with mock.patch.object(tpex, "fetch_json", return_value=data):
            payload = tpex.daily_insti(DAY)
        self.assertEqual(payload["rows"]["6488"], [-4457, 222, -147, -4382])

    def test_index_history_keeps_requested_month(self):
        data = {"stat": "ok", "tables": [{"data": [
            ["2026/09/01", "402.12", "412.90", "402.12", "410.77", "9.07"],
            ["2026/08/31", "1", "1", "1", "401.70", "-1.00"],
        ]}]}
        with mock.patch.object(tpex, "fetch_json", return_value=data) as fetch:
            self.assertEqual(tpex.index_history("2026-09"), {"2026-09-01": [410.77, 9.07]})
        self.assertIn("d=115/09", fetch.call_args[0][0])

    def test_empty_table_means_no_data(self):
        data = {"stat": "ok", "date": "20261010", "tables": [{"fields": [], "data": []}]}
        with mock.patch.object(tpex, "fetch_json", return_value=data):
            self.assertIsNone(tpex.daily_quotes(date(2026, 10, 10)))

    def test_margin_by_column_names(self):
        fields = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額",
                  "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]
        row = ["6488", "環球晶", "13,977", "1", "9", "0", "13,969", "0", "1", "1", "285", "1", "1", "0", "272", "0", "0", "1", "0", ""]
        data = {"stat": "ok", "date": "20261016", "tables": [{"fields": fields, "data": [row]}]}
        with mock.patch.object(tpex, "fetch_json", return_value=data):
            self.assertEqual(tpex.daily_margin(DAY)["rows"]["6488"], [13969, -8, 272, -13])


class SnapshotTests(unittest.TestCase):
    def test_tdcc(self):
        lines = ["﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%"]
        lines += [f"20261016,2330,{lv},{lv},{lv * 100},{'84.56' if lv == 15 else '1.00'}" for lv in range(1, 16)]
        lines += ["20261016,2330,16,1,1000,0.00", "20261016,2330,17,3098860,25932370067,100.00", "20261016,00919,1,5,5,1.00"]
        with mock.patch.object(tdcc, "fetch_text", return_value="\n".join(lines)):
            day, rows = tdcc.shareholding()
        self.assertEqual(day, "2026-10-16")
        self.assertEqual(list(rows), ["2330"])
        self.assertEqual(rows["2330"][:2], [3098860, 25932370067])
        self.assertEqual(rows["2330"][16], 84.56)

    def test_revenue_and_insider(self):
        months = mops.parse_revenue([{
            "資料年月": "11509", "公司代號": "2330", "產業別": "半導體業", "營業收入-當月營收": "511857399",
            "營業收入-去年當月營收": "330980920", "營業收入-去年同月增減(%)": "54.65", "營業收入-上月比較增減(%)": "-0.57",
            "累計營業收入-前期比較增減(%)": "41.11", "備註": "-",
        }])
        self.assertEqual(months["2026-09"]["2330"], [511857399, 54.65, -0.57, 41.11, 330980920, "半導體業", ""])
        recs = mops.parse_insider([
            {"出表日期": "1151016", "公司代號": "6534", "公司名稱": "正瀚-創", "申報人身分": "董事本人", "姓名": "某某",
             "預定轉讓方式及股數-轉讓方式": "鉅額逐筆交易", "預定轉讓總股數-自有持股": "2000000",
             "目前持有股數-自有持股": "16736810", "有效轉讓期間": "1151019~1151118"},
            {"Date": "1151016", "SecuritiesCompanyCode": "", "申請人身分": ""},
        ], "上市")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["lots"], 2000)
        self.assertNotIn("姓名", recs[0])


class AnalyzeTests(unittest.TestCase):
    def test_streak(self):
        self.assertEqual(analyze.streak([5, -1, 2, 3, 4]), 3)
        self.assertEqual(analyze.streak([1, -2, -3]), -2)
        self.assertEqual(analyze.streak([1, 2, 0]), 0)
        self.assertEqual(analyze.streak([1, None, 2]), 2)

    def test_build_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            closes = [100, 102, 101, 105, 110, 108]
            for i, close in enumerate(closes):
                iso = f"2026-10-{i + 5:02d}"
                store.save_day({
                    "date": iso,
                    "twse": {
                        "quotes": {"rows": {"2330": [close, close - (closes[i - 1] if i else close), 1000, 50.0]}, "index": [47000 + i, 1]},
                        "insti": {"rows": {"2330": [100 if i >= 3 else -50, 10, 0, 110]}},
                        "margin": {"rows": {"2330": [5000 + i * 100, 100, 50, 0]}, "amount": [6400, 1]},
                        "market_insti": {"foreign": 1, "trust": 1, "dealer": 1, "total": 3},
                    },
                })
            store.write({"rows": {"2330": {"name": "台積電", "market": "上市", "industry": "半導體業", "shares": 1_000_000}}}, "meta", "companies.json")
            payload = analyze.build_payload(store)
        row = dict(zip(payload["columns"], payload["rows"][0]))
        self.assertEqual(row["name"], "台積電")
        self.assertEqual(row["fs"], 3)
        self.assertEqual(row["f5"], 100 * 3 - 50 * 2)
        self.assertEqual(row["ret5"], 8.0)
        self.assertAlmostEqual(row["chg_pct"], -1.82, places=2)
        self.assertEqual(row["t5cap"], 5.0)  # 50 張 / 1,000 張
        self.assertEqual(payload["data_date"], "2026-10-10")


class StoreTests(unittest.TestCase):
    def test_write_only_when_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertTrue(store.write({"a": 1}, "x.json"))
            self.assertFalse(store.write({"a": 1}, "x.json"))
            self.assertTrue(store.write({"a": 2}, "x.json"))


class UpdaterTests(unittest.TestCase):
    def test_candidate_days_skip_weekends_holidays_and_today_before_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = update.Updater(Store(tmp), datetime(2026, 10, 27, 10, 0, tzinfo=update.TAIPEI))
            u.closed = {"2026-10-26": "補假"}
            days = [d.isoformat() for d in u.candidate_days(3)]
        self.assertEqual(days, ["2026-10-21", "2026-10-22", "2026-10-23"])

    def test_closed_day_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp:
            u = update.Updater(Store(tmp), datetime(2026, 10, 18, 22, 0, tzinfo=update.TAIPEI))
            with mock.patch.object(update, "DAILY_PARTS", [("twse", "quotes", lambda d: None)]):
                u.update_day(date(2026, 10, 14), date(2026, 10, 16))
        self.assertIn("2026-10-14", u.closed)


if __name__ == "__main__":
    unittest.main()
