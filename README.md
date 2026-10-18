# 台股籌碼儀表板（免費版）

用證交所、櫃買中心、公開資訊觀測站與集保結算所的**免費公開資料**，每天收盤後自動更新一份台股儀表板。
不呼叫任何 AI API，不需要任何付費帳號或金鑰；排程跑在 GitHub Actions 的免費額度內。

儀表板是單一 HTML 檔（`docs/index.html`），資料都嵌在檔案裡，離線也能開。

## 儀表板內容

- **大盤概況**：加權指數、櫃買指數、上市三大法人買賣超（億元）、上市融資餘額，附近 40 日走勢。
- **選股清單**（可搜尋、依市場／產業／均量篩選、點欄位排序）：

  | 快速篩選 | 條件 |
  |---|---|
  | 外資連買 | 外資連續買超 ≥ 5 天、5 日均量 ≥ 500 張 |
  | 投信連買 | 投信連續買超 ≥ 3 天、5 日均量 ≥ 300 張 |
  | 投信認養 | 投信 5 日買超佔股本 ≥ 0.5% |
  | 營收高成長 | 月營收年增 ≥ 50%、今年累計年增 ≥ 30%、單月營收 ≥ 1 億 |
  | 大戶加碼 | 千張大戶持股比例週增 ≥ 0.5 個百分點且股東人數減少 |
  | 融資大增 | 5 日融資餘額增加 ≥ 10%（風險訊號） |
  | 高殖利率 | 殖利率 ≥ 6%、本益比 0～12 倍 |

  門檻寫在 `templates/dashboard.html` 的 `PRESETS`，可以自己改。
  | 內部人申報 | 近 30 天有董監／大股東申報轉讓持股 |

- **個股細節**（點任一列）：40 日收盤價、外資與投信每日買賣超圖、估值、月營收、融資券、集保大戶分布、內部人申報。
- 依台股習慣，**漲／買超為紅色、跌／賣超為綠色**，數字一律帶正負號（色覺辨識障礙也看得懂）。支援深色模式。

## 資料來源

| 資料 | 來源 | 更新頻率 |
|---|---|---|
| 每日行情、加權指數 | 證交所網站 `afterTrading/MI_INDEX`（備援：OpenAPI `STOCK_DAY_ALL`） | 每交易日 |
| 上櫃行情 | 櫃買中心網站 `afterTrading/otc`（備援：OpenAPI） | 每交易日 |
| 三大法人買賣超 | 證交所 `fund/T86`、`fund/BFI82U`；櫃買 `insti/dailyTrade`（備援：OpenAPI） | 每交易日 |
| 融資融券 | 證交所 `marginTrading/MI_MARGN`；櫃買 `margin/balance`（備援：OpenAPI） | 每交易日（約 21:30） |
| 本益比、殖利率、淨值比 | 證交所／櫃買 OpenAPI | 每交易日 |
| 月營收、公司基本資料、內部人持股轉讓事前申報 | 公開資訊觀測站資料（經由證交所／櫃買 OpenAPI） | 每月／每日 |
| 櫃買指數、上櫃法人金額 | 櫃買 OpenAPI | 每交易日 |
| 集保戶股權分散表 | 集保結算所開放資料 | 每週 |

> 為什麼不全用 OpenAPI？OpenAPI 只提供「最新一天」，沒辦法回補歷史，也沒辦法補抓排程漏跑的日子；
> 證交所 OpenAPI 也沒有個股的三大法人資料。所以每日資料改用同一個機構網站上「可指定日期」的 JSON，
> 網站端點失敗時再用 OpenAPI 補最新一天。

資料依「政府資料開放授權條款」使用。只收普通股（4 碼代號），不含 ETF、權證、特別股。

## 在自己電腦上跑

需要 Python 3.10 以上，**不用安裝任何套件**（只用標準函式庫）。

```bash
py run.py --backfill-days 40
```

第一次會回補 40 個交易日，約 10 分鐘（證交所會封鎖太頻繁的請求，所以每次請求間隔 3.5 秒）。
之後每天跑 `py run.py` 即可，它會自動補抓最近 7 個交易日缺的部分。跑完打開 `docs/index.html`。

其他指令：

```bash
py run.py --skip-update
```

```bash
py -m unittest discover -s tests
```

> **Windows 注意**：如果用的是 MSYS2 / Git Bash 內附的 Python，可能缺 CA 憑證而出現 `CERTIFICATE_VERIFY_FAILED`。
> 請改用 python.org 版本（`py` 指令），或 `pip install certifi`。

## 用 GitHub Actions 每天自動更新

1. 在 GitHub 建一個新 repo，把這個資料夾推上去（`data/` 已經有回補好的資料，可以一起推）。
2. 到 repo 的 **Settings → Pages**，Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/docs`。
   之後網址會是 `https://<你的帳號>.github.io/<repo 名稱>/`。
   - 免費帳號只有 **public repo** 能開 Pages。private repo 也能自動更新，只是要 `git pull` 後在本機開 `docs/index.html`。
3. 到 **Actions** 分頁，選「更新台股資料」→ **Run workflow** 先手動跑一次確認沒問題。
   如果推上去時沒有帶 `data/`，這一次把 `backfill_days` 填 `40`。
   手動執行時，執行結果頁的摘要會列出每個資料來源的連線檢查（也可以在本機跑 `py -m stock_agent.check`）。
4. 之後會自動執行（台灣時間）：
   - 週一～五 17:30：收盤行情、三大法人
   - 週一～五 22:30：融資融券、補抓漏掉的部分
   - 週六 10:00：集保股權分散表

GitHub 的排程常會延遲 10～60 分鐘。若推送失敗，檢查 **Settings → Actions → General → Workflow permissions** 是否允許寫入。

## 資料夾結構

```
stock_agent/        程式
  twse.py tpex.py   上市、上櫃資料（每日 JSON + OpenAPI）
  mops.py           公開資訊觀測站資料解析（月營收、內部人申報）
  tdcc.py           集保股權分散表
  update.py         決定要抓哪些日子、補抓、存檔
  analyze.py        計算指標（連買天數、5/20 日累計、佔股本比、券資比、大戶週變化…）
  dashboard.py      產生 docs/index.html
templates/dashboard.html   儀表板版面（純 HTML + SVG，無外部資源）
data/daily/年/日期.json    每個交易日一個檔（行情、法人、融資券、大盤）
data/revenue/ valuation/ tdcc/ insider/ meta/   快照型資料
docs/index.html     產生出來的儀表板
```

## 限制與風險

- **資料會延遲或缺漏**：官方公布時間不固定，頁面上方會標示每種資料的日期，比資料日期舊的會以黃色提示。
- **端點可能改版**：證交所、櫃買中心改網站時，抓取可能失敗；失敗會寫進 `data/state.json` 並顯示在儀表板警告區，其他資料照常更新。
- **GitHub 的機房在海外**：若證交所網站封鎖或限制海外 IP，上市三大法人（只有網站端點）可能抓不到；其他資料會改用 OpenAPI 備援。這種情況可改在自己電腦上跑。
- **集保要累積兩週**才有「週變化」；內部人申報從開始執行後逐日累積。
- **repo 會慢慢變大**：每個交易日約 130 KB 資料，加上每次更新重新產生的 `docs/index.html`（約 1.3 MB，git 會壓縮），
  一年大約幾十 MB。沒有新資料時程式不會改動任何檔案，所以不會產生空的提交。
- 指標只是整理公開資料，**不是投資建議**，也不保證任何報酬。
