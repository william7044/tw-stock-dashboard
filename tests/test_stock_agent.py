"""離線單元測試：解析器、指標計算、交易日判斷。不需要網路。

    py -m unittest discover -s tests
"""
import tempfile
import unittest
from array import array
from datetime import date, datetime, timedelta
from unittest import mock

from stock_agent import analyze, backtest, indicators, mops, parse, tdcc, tpex, twse, update, volume_profile
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

    def test_stock_day_marks_ex_dividend_change_unknown(self):
        data = {"stat": "OK", "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數", "註記"],
                "data": [["115/07/20", "1", "1", "1", "1", "1", "99.20", "-0.80", "1", ""],
                         ["115/07/21", "1", "1", "1", "1", "1", "102.50", "X0.00", "1", ""]]}
        with mock.patch.object(twse, "fetch_json", return_value=data):
            self.assertEqual(twse.stock_day("0050", "2026-07"), {"2026-07-20": [99.2, -0.8], "2026-07-21": [102.5, None]})

    def test_exrights_reference_prices(self):
        tw = {"stat": "OK", "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價"],
              "data": [["115年07月21日", "0050", "元大台灣50", "99.20", "98.60"]]}
        with mock.patch.object(twse, "fetch_json", return_value=tw):
            self.assertEqual(twse.exrights(date(2026, 7, 1), date(2026, 7, 31)), {"2026-07-21": {"0050": 98.6}})
        tp = {"stat": "ok", "tables": [{"fields": ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價"],
                                         "data": [["114/10/01", "3289", "宜特              ", "120.00", "117.22"]]}]}
        with mock.patch.object(tpex, "fetch_json", return_value=tp):
            self.assertEqual(tpex.exrights(date(2025, 10, 1), date(2025, 10, 31)), {"2025-10-01": {"3289": 117.22}})

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

    def test_mops_revenue_page(self):
        html = ("<tr><th>公司代號</th></tr><tr align=right><td align=center>2330</td><td align=left>台積電</td>"
                "<td nowrap> 511,857,399</td><td nowrap> 514,805,337</td><td nowrap> 330,980,920</td>"
                "<td nowrap> -0.57</td><Td nowrap> 54.64</td><td nowrap> 3,898,726,974</td>"
                "<td nowrap> 2,762,963,851</td><td nowrap> 41.10</td><td align=left>-</td></tr>")
        self.assertEqual(mops.parse_revenue_page(html), {"2330": [511857399, 54.64, -0.57, 41.1, 330980920, "", ""]})

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


class BacktestTests(unittest.TestCase):
    NAN = float("nan")

    def test_adjusted_index_ignores_ex_dividend_drop(self):
        # 第 3 天除息 5 元：收盤 95、參考價 97（漲跌 -2），只算 -2/97 的跌幅
        idx = backtest.adjusted_index(array("d", [100, 102, 95, self.NAN, 96]), array("d", [self.NAN, 2, -2, self.NAN, 1]))
        self.assertAlmostEqual(idx[1], 1.02)
        self.assertAlmostEqual(idx[2], 1.02 * 95 / 97)
        self.assertAlmostEqual(idx[3], idx[2])  # 沒成交的日子沿用
        self.assertAlmostEqual(idx[4], idx[2] * 96 / 95)

    def test_reference_price_gives_true_move_on_ex_dividend_day(self):
        # 交易所在除息日只標「X0.00」（漲跌記成 0）；有參考價 98 時，當天真正是 104/98
        idx = backtest.adjusted_index(array("d", [100, 102, 104]), array("d", [self.NAN, 2, 0.0]),
                                      array("d", [self.NAN, self.NAN, 98]))
        self.assertAlmostEqual(idx[2], 1.02 * 104 / 98)

    def test_benchmark_is_kept_out_of_market_baseline(self):
        docs = _trend_docs(40)
        panel = backtest.Panel(docs)
        before = backtest.baselines(panel)
        rows = {d["date"]: [100 * 1.02 ** i, None if i == 30 else 100 * 1.02 ** i - 100 * 1.02 ** (i - 1)]
                for i, d in enumerate(docs)}
        panel.exrights = {docs[30]["date"]: {"0050": 100 * 1.02 ** 29}}  # 第 30 天除息但參考價 = 前收（沒配息）
        panel.add_benchmark("0050", rows)
        self.assertEqual(backtest.baselines(panel), before)
        self.assertTrue(panel.tradable("0050", 25))
        self.assertAlmostEqual(panel.forward("0050", 25, 10), 1.02 ** 10 - 1)

    def test_streaks_and_transitions(self):
        self.assertEqual(backtest.streaks([1, 2, 0, -1, -3, self.NAN, -2]), [1, 2, 0, -1, -2, -2, -3])
        self.assertEqual(backtest.transitions([False, True, True, False, True], 0), [1, 4])

    def test_signal_enters_next_day_and_is_compared_with_market(self):
        docs = []
        for i in range(40):
            rising, prev = 100 * 1.01 ** i, 100 * 1.01 ** (i - 1)
            docs.append({
                "date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                "twse": {
                    "quotes": {"rows": {"1111": [rising, rising - prev if i else None, 1000, 1.0],
                                        "2222": [50.0, 0.0, 1000, 1.0]}},
                    "insti": {"rows": {"1111": [10 if 22 <= i <= 30 else -10, 0, 0, 0], "2222": [-10, 0, 0, 0]}},
                },
            })
        panel = backtest.Panel(docs)
        events = backtest.daily_events(panel, {})
        self.assertEqual(events["foreign"], [("1111", 27)])  # 第 26 天連買滿 5 天，第 27 天收盤進場
        with mock.patch.object(backtest, "MIN_SAMPLES", 1):
            stats = backtest.summarize(events["foreign"], panel, backtest.baselines(panel))["5"]
        self.assertAlmostEqual(stats["mean"], 5.1, places=1)  # 1.01^5 - 1
        self.assertAlmostEqual(stats["base"], 2.55, places=1)  # 和不漲的 2222 平均
        self.assertAlmostEqual(stats["excess"], 2.55, places=1)
        self.assertAlmostEqual(stats["excess_median"], 2.55, places=1)  # 兩檔的中位數也是 2.55%
        self.assertEqual(stats["beat"], 100.0)
        self.assertEqual(stats["n"], 1)

    def test_random_pick_beats_median_half_the_time(self):
        """右偏分布下，隨機選股贏過「平均」的比例遠低於 50%，但贏過「中位數」剛好一半。"""
        moves = [k / 100 for k in range(9)] + [1.5]  # 第 25 天：九檔各漲 0%～8%，一檔漲 150%
        docs = []
        for i in range(30):
            rows = {}
            for k, move in enumerate(moves):
                close = 100 * (1 + move) if i >= 25 else 100.0
                rows[f"{1000 + k}"] = [close, 100 * move if i == 25 else 0.0, 1000, 1.0]
            docs.append({"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "twse": {"quotes": {"rows": rows}}})
        panel = backtest.Panel(docs)
        events = [(code, 21) for code in panel.series]  # 第 21 天每一檔都「進場」，等於隨機全買
        with mock.patch.object(backtest, "MIN_SAMPLES", 1):
            stats = backtest.summarize(events, panel, backtest.baselines(panel))["5"]
        self.assertEqual(stats["beat"], 50.0)  # 一半贏過同期中位數（若對平均 18.6%，只有 1 檔贏）
        self.assertEqual(stats["excess_median"], 0.0)
        self.assertAlmostEqual(stats["excess"], 0.0, places=6)  # 平均對平均：超額 0


def _trend_docs(days, start=date(2026, 1, 1), spike_day=None):
    """1111 每天漲 1%、2222 每天跌 1%，量都是 1,000 張（spike_day 那天 1111 爆量 3,000 張）。"""
    docs = []
    for i in range(days):
        up, down = 100 * 1.01 ** i, 100 * 0.99 ** i
        docs.append({"date": (start + timedelta(days=i)).isoformat(), "twse": {"quotes": {"rows": {
            "1111": [up, up - 100 * 1.01 ** (i - 1) if i else 0.0, 3000 if i == spike_day else 1000, 1.0],
            "2222": [down, down - 100 * 0.99 ** (i - 1) if i else 0.0, 1000, 1.0],
        }}}})
    return docs


class BacktestSignalTests(unittest.TestCase):
    def test_technical_events_fire_once_and_enter_next_day(self):
        events = backtest.technical_events(backtest.Panel(_trend_docs(140, spike_day=100)))
        self.assertIn(("1111", 60), events["bull"])  # 第 59 天才有 60 日均線
        self.assertIn(("1111", 61), events["rs"])  # 第 60 天才有 60 日漲幅
        self.assertIn(("1111", 101), events["volup"])
        self.assertEqual(events["high52"], [("1111", 121)])  # 至少要 120 天歷史
        self.assertEqual(events["low52"], [("2222", 121)])
        self.assertEqual(events["macd"], [])  # 單調上漲不會出現「交叉」

    def test_revenue_new_high_and_pe_low_events(self):
        panel = backtest.Panel(_trend_docs(90))  # 2026-01-01 ~ 2026-03-31
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            months = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01"]
            for k, month in enumerate(months):
                store.write({"month": month, "rows": {"1111": [100_000 + k, 60.0, 0, 40.0, 0, "", ""]}}, "revenue", f"{month}.json")
            for k, day in enumerate(["2025-08-01", "2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"]):
                store.write({"date": day, "rows": {"1111": [20.0 - k, 3.0, 1.0]}}, "valuation", "history", f"{day}.json")
            revenue = backtest.revenue_events(panel, store)
            valuation = backtest.valuation_events(panel, store)
        # 2025-12 營收在 1 月 11 日進場、2026-01 營收在 2 月 11 日進場；更早的月份進場日在資料期間之前
        expected = [("1111", panel.dates.index("2026-01-11")), ("1111", panel.dates.index("2026-02-11"))]
        self.assertEqual(revenue, {"revenue": expected, "revhigh": expected})
        jan, feb = panel.dates.index("2026-01-01"), panel.dates.index("2026-02-01")
        self.assertEqual(valuation["pelow"], [("1111", jan + 1), ("1111", feb + 1)])
        self.assertEqual(valuation["yield"], [])


class IndicatorTests(unittest.TestCase):
    def test_ema_seeds_with_simple_average(self):
        out = indicators.ema([1, 2, 3, 4, 5], 3)
        self.assertTrue(indicators.isnan(out[1]))
        self.assertEqual(out[2:], [2.0, 3.0, 4.0])

    def test_rsi_and_macd_edge_cases(self):
        self.assertEqual(indicators.rsi(list(range(1, 30)))[-1], 100.0)
        self.assertEqual(indicators.rsi([10.0] * 30)[-1], 50.0)
        dif, dea = indicators.macd([10.0] * 60)
        self.assertEqual((dif[-1], dea[-1]), (0.0, 0.0))

    def test_prior_extremes_excludes_today(self):
        highs, lows = indicators.prior_extremes([5, 3, 8, 1, 9, 2], 3, min_days=1)
        self.assertTrue(indicators.isnan(highs[0]))
        self.assertEqual(highs[1:], [5, 5, 8, 8, 9])
        self.assertEqual(lows[1:], [5, 3, 3, 1, 1])
        self.assertEqual(indicators.percent_rank([1, 2, 3, 4], 3), 50)

    def test_technicals_flags_highs_lows_and_breadth(self):
        docs = []
        for i in range(140):
            up, down = 100 * 1.01 ** i, 100 * 0.99 ** i
            docs.append({"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "twse": {"quotes": {"rows": {
                "1111": [up, up - 100 * 1.01 ** (i - 1) if i else 0.0, 1000, 1.0],
                "2222": [down, down - 100 * 0.99 ** (i - 1) if i else 0.0, 1000, 1.0],
            }}}})
        metrics, breadth = analyze.technicals(backtest.Panel(docs), {"1111", "2222"})
        self.assertEqual((metrics["1111"]["new_hl"], metrics["1111"]["align"], metrics["1111"]["pos52"]), (1, 1, 100.0))
        self.assertEqual((metrics["2222"]["new_hl"], metrics["2222"]["align"], metrics["2222"]["pos52"]), (-1, -1, 0.0))
        self.assertEqual(metrics["1111"]["rs"], 50)  # 兩檔中贏過另一檔
        self.assertEqual(breadth[docs[-1]["date"]], [1, 1, 0, 1, 1])

    def test_revenue_new_high_and_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            for m in range(1, 13):
                store.write({"month": f"2026-{m:02d}", "rows": {"1111": [100 + m, 5.0 if m > 9 else -1.0, 0, 0, 0, "", ""]}},
                            "revenue", f"2026-{m:02d}.json")
            self.assertEqual(analyze.revenue_metrics(store)["1111"], (1, 3))


def _grid(k):
    """價格格子 k 的正中間（每格 1%），測試用的收盤價都放在格子中間，才不會因為浮點誤差掉到隔壁格。"""
    return 1.01 ** (k + 0.5)


A, B, C, D, E = 460, 461, 462, 463, 464  # 約 99.2 ～ 103.2 元的五個相鄰格子
BOX = [C, D, C, E, C, B, D, C, B, A]  # 每 10 天：C 4 天、D 2 天、B 2 天、A 1 天、E 1 天 → 價值區是 B～D


def _vp_series(bins, vols):
    """格子序列 → scan() 的輸入（收盤、成交量張、成交值百萬元、還原指數）。均價 = 收盤。"""
    close = [_grid(k) for k in bins]
    value = [v * c / 1000 for v, c in zip(vols, close)]
    return close, list(vols), value, close


class VolumeProfileTests(unittest.TestCase):
    def test_value_area_adds_the_heavier_side_until_70_percent(self):
        bins = {10: 5, 11: 20, 12: 40, 13: 12, 14: 23}
        # POC 12（40%）→ 下面 20 比上面 12 多，加 11（60%）→ 上面 12 比下面 5 多，加 13（72%）
        self.assertEqual(volume_profile.value_area(bins), (12, 11, 13, 100))
        self.assertEqual(volume_profile.value_area({5: 1, 6: 1, 7: 1})[0], 6)  # 一樣多時取中間

    def test_vwap_from_traded_value_with_dividend_adjustment(self):
        nan = float("nan")
        # 成交值（百萬元）× 1000 ÷ 成交量（張）= 均價；第二天之前除息，還原係數 0.96
        out = volume_profile.adjusted_vwap([100.0, 95.0, nan], [1000, 2000, 0], [101.0, 190.0, nan], [96.0, 95.0, nan])
        self.assertAlmostEqual(out[0], 101 * 0.96)
        self.assertAlmostEqual(out[1], 95.0)
        self.assertTrue(volume_profile.isnan(out[2]))
        self.assertEqual(volume_profile.adjusted_vwap([100.0], [1000], [500.0], [100.0]), [100.0])  # 均價離譜時用收盤

    def test_levels_use_only_the_previous_window(self):
        prices = [_grid(C)] * 3 + [_grid(E)] * 4
        levels, above, last_bins = volume_profile.rolling_levels(prices, [10] * 7, prices, window=3, min_days=3)
        self.assertIsNone(levels[2])  # 前面只有 2 天
        self.assertEqual(levels[3], (C, C, C))  # 當天（E）的量還沒算進去
        self.assertEqual(above[3], 0.0)
        self.assertEqual(levels[6], (E, E, E))  # 3 天前的 C 已經移出視窗
        self.assertEqual(last_bins, {E: 30})

    def test_range_buys_at_value_area_low_and_sells_at_high(self):
        r = volume_profile.scan(*_vp_series(BOX * 13, [3000] * 130))
        marks = [(t, side, kind) for t, side, kind, *_ in r["marks"]]
        # 第 100 天起才有完整分布；第 105 天收在 B（價值區下緣那一格）→ B，第 113 天收在 E（超過上緣）→ S
        self.assertEqual(marks[:4], [(105, "B", "rng"), (113, "S", "tgt"), (115, "B", "rng"), (123, "S", "tgt")])
        _, _, _, target, stop = r["marks"][0]
        self.assertAlmostEqual(target, volume_profile.bin_price(D, 1))
        self.assertAlmostEqual(stop, volume_profile.bin_price(B) * 0.95)
        self.assertEqual(r["flags"] & volume_profile.RANGE, volume_profile.RANGE)

    def test_range_waits_for_a_fresh_touch_when_already_at_the_low(self):
        # 第 128 天起一直收在 B 格（價值區下緣），成交值到第 130 天才達 1 億：整理條件成立時股價早已在下緣，
        # 不是「從上方剛跌到下緣」，所以不買（試過在這種情況買，回測明顯較差）
        bins = BOX * 12 + BOX[:8] + [B] * 8
        r = volume_profile.scan(*_vp_series(bins, [900] * 130 + [4000] * 6))
        self.assertEqual(r["marks"], [])
        self.assertTrue(r["flags"] & volume_profile.RANGE)  # 還是在「區間整理」清單裡，只是沒有 B

    def test_breakout_buys_on_volume_and_sells_when_back_in_value_area(self):
        bins = BOX * 15 + [470, 471, 472, 469, C]  # 第 150 天帶量跳到箱子上方，第 154 天跌回價值區
        vols = [900] * 150 + [5000, 900, 900, 900, 900]  # 整理期間成交值不到 1 億，不會出現區間訊號
        r = volume_profile.scan(*_vp_series(bins, vols))
        self.assertEqual([(t, side, kind) for t, side, kind, *_ in r["marks"]], [(150, "B", "brk"), (154, "S", "fail")])
        self.assertAlmostEqual(r["marks"][0][4], volume_profile.bin_price(D, 1))  # 出場價 = 突破前的價值區上緣

    def test_rebound_buys_after_deep_drop_and_sells_at_trapped_supply(self):
        decline = [452 - 2 * j for j in range(10)]  # 從約 90 元一路跌到約 75 元
        bins = BOX * 12 + decline + [433, 434] * 5 + [441, 446, 452, 458, 461]
        r = volume_profile.scan(*_vp_series(bins, [900] * 120 + [1500] * 25))
        self.assertEqual([(t, side, kind) for t, side, kind, *_ in r["marks"]], [(140, "B", "reb"), (144, "S", "tgt")])
        _, _, _, target, stop = r["marks"][0]
        self.assertAlmostEqual(target, volume_profile.bin_price(A))  # 目標 = 價值區下緣（上方套牢區開始的地方）
        self.assertAlmostEqual(stop, _grid(433) * 0.97)

    def test_events_enter_next_day_and_payload_encodes_bands(self):
        docs, prev = [], None
        for i, k in enumerate(BOX * 13):
            close = _grid(k)
            docs.append({"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "twse": {"quotes": {"rows": {
                "1111": [close, close - prev if prev else 0.0, 3000, 3000 * close / 1000]}}}})
            prev = close
        panel = backtest.Panel(docs)
        events, trades = volume_profile.events(panel)
        self.assertEqual(events["vp_rng"][:2], [("1111", 106), ("1111", 116)])
        self.assertEqual(trades[0], ("1111", "rng", 106, 114))
        out = volume_profile.payload(panel, {"1111": 1_000_000}, {"1111"}, display=30)
        row = dict(zip(out["columns"], out["rows"]["1111"]))
        self.assertEqual(row["vp_set"] & volume_profile.RANGE, volume_profile.RANGE)
        self.assertEqual(out["dates"], panel.dates[-30:])
        chart = out["charts"]["1111"]
        self.assertEqual(chart["b"][1][:2], [0, B])  # 價值區下緣：第 0 天起是 B 格
        self.assertEqual(len(chart["c"]), 30)
        self.assertTrue(all(m[0] >= 0 for m in chart["m"]))


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
