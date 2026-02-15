# ImportWebRate.py 規格書

## 1. 概述

月匯率自動化程式，負責從央行 (CBC) 網站取得 USD/TWD 匯率，再透過 Selenium 從 CNYES 抓取 INR/MXN/TRY 匯率，計算交叉匯率後寫入 SQL Server 與 Oracle 資料庫，最後以 Email 通知並附上匯率表格與網頁截圖證明。

- **檔案位置**: `ImportWebRate.py`
- **Python 版本**: 3.11+
- **套件管理**: uv
- **排程方式**: `run_daily.bat` 或 Windows Task Scheduler
- **架構**: Async (`asyncio.run(async_main())`)

---

## 2. 執行流程

```
┌─────────────────────────────────────────────────┐
│                  主程式啟動                       │
│     setup_logging() + Selenium driver init       │
└──────────────────────┬──────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 1: CBC USD/TWD 抓取   │
         │  get_latest_usdtwd_rate_   │
         │  from_cbc()  (同步)         │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 2: Selenium 抓取      │
         │  MXN (USDMXN)             │
         │  INR (USDINR)             │
         │  TRY (TRYTWD)             │
         │  + async Playwright 截圖    │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 3: SQL Server 寫入    │
         │  prepare_and_merge_sql_    │
         │  server()                  │
         │  (SERVICE_TEST, SC_銀行匯率) │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 4: Oracle 寫入        │
         │  insert_to_oracle()        │
         │  (YSC.SGLP0021_TEMP)      │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 5: 報表寄送           │
         │  export_monthly_report_    │
         │  and_email()               │
         │  (匯率表格 + 截圖)          │
         └────────────────────────────┘
```

---

## 3. Async 架構說明

- 入口點: `asyncio.run(async_main())`
- `snapshot_page()` 使用 async Playwright (headless Chromium)
- `fetch_currency_rate_selenium()` 混合使用：
  - async Playwright: 截圖 (非阻塞)
  - Selenium: 資料抓取 (共用單一 driver 實例)
- `get_latest_usdtwd_rate_from_cbc()` 為同步函式 (requests)

---

## 4. 資料來源

| 來源 | 網址 | 用途 | 抓取方式 |
|------|------|------|----------|
| 中央銀行 (CBC) | `https://www.cbc.gov.tw/tw/lp-645-1.html` | USD/TWD 匯率 | requests + BeautifulSoup |
| CNYES | `https://invest.cnyes.com/forex/detail/{pair}/history` | INR/MXN/TRY 匯率 | Selenium |

### 截圖

- 使用 async Playwright 對 CNYES 頁面進行截圖
- 截圖儲存路徑:
  - `./data/web/{YYMMDD}/USDINR_snapshot.jpg`
  - `./data/web/{YYMMDD}/USDMXN_snapshot.jpg`
  - `./data/web/{YYMMDD}/TRYTWD_snapshot.jpg`

---

## 5. 處理幣別

### 月匯率 (3 種)

| 幣別 | 來源 | 貨幣對 | 換算方式 |
|------|------|--------|----------|
| INR | CNYES (Selenium) | USDINR | `TWD/INR = USD_TWD / USD_INR` |
| MXN | CNYES (Selenium) | USDMXN | `TWD/MXN = USD_TWD / USD_MXN` |
| TRY | CNYES (Selenium) | TRYTWD | `TWD/TRY = 1 / TRY_TWD` |

### 儲存規則

- 每筆匯率皆以**雙向**寫入 (例: INR→TWD 與 TWD→INR)
- `conversion_date` 設定為 `date.today() + timedelta(days=1)`

---

## 6. 14 天回溯邏輯

若目標日期無匯率資料 (假日/國定假日)，程式自動往前回溯最多 **14 天** 取得最近的交易日匯率。

### 6.1 CNYES 匯率回溯 (`fetch_rate_with_fallback`)

```
fetch_rate_with_fallback(driver, currency_name, currency_pair)
  └─ for i in range(1, 15):   ← 從「昨天」開始，逐天往前
       target_date = today - i 天
       rate = fetch_currency_rate_selenium(driver, target_date, currency_pair)
       if rate:
           return rate          ← 找到即停止
  └─ return None                ← 14 天都找不到則回傳 None
```

**範例**：程式於 2026-02-15 (日) 執行

| 嘗試 | target_date | 星期 | CNYES 表格是否有資料 | 結果 |
|------|-------------|------|---------------------|------|
| i=1 | 2026-02-14 | 六 | 無 (假日無交易) | 繼續 |
| i=2 | 2026-02-13 | 五 | 有 (收盤價存在) | 回傳匯率 |

### 6.2 匹配方式 (`fetch_currency_rate_selenium`)

1. Selenium 載入 CNYES 歷史價格頁面
2. 遍歷 `<table>` 每一行 `<tr>`
3. 比對第一欄日期 `col_texts[0]` 是否等於 `target_date`
4. 匹配成功 → 取第五欄收盤價 `col_texts[4]` 回傳
5. 整張表格都不匹配 → 回傳 `None`，由外層迴圈嘗試前一天

### 6.3 CBC USD/TWD 回溯 (`get_latest_usdtwd_rate_from_cbc`)

CBC 採用類似策略，但在單次 HTTP 請求內完成：
1. 產生最近 **15 天** 的日期清單 (`possible_dates`)
2. 解析 CBC 網頁表格，逐行比對日期
3. 第一筆匹配的日期即回傳其 USD/TWD 匯率

---

## 7. 資料庫

### 7.1 SQL Server

| 項目 | 值 |
|------|-----|
| 連線方式 | `mssql-python` (DB API 2.0, 直連) |
| 目標資料庫 | `SERVICE_TEST` |
| 目標資料表 | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` |
| 寫入方式 | `MERGE` (Upsert) |
| 參數佔位符 | `?` (qmark style) |
| Config 優先順序 | `dev_db` > `database` |

#### MERGE 匹配條件

- `from_currency` + `to_currency` + `from_conversion_date`

#### 寫入欄位

| 欄位名稱 | 值 |
|----------|-----|
| `from_currency` | 來源幣別 |
| `to_currency` | 目標幣別 |
| `from_conversion_date` | 匯率日期 (today+1, 明天) |
| `to_conversion_date` | 匯率日期 (today+1, 明天) |
| `user_conversion_type` | `SC_銀行匯率` |
| `conversion_rate` | 匯率 |
| `mode_flag` | `I` |
| `inverse_conversion_rate` | 反向匯率 |
| `user_id` | `1175` |
| `launch_rate_change` | `Y` |
| `insert_time` | `GETDATE()` |

### 7.2 Oracle

| 項目 | 值 |
|------|-----|
| 連線方式 | `oracledb` Thick mode (需 Oracle Instant Client) |
| Instant Client 路徑 | `config/db_config.yaml` 中 `client_lib_dir` 設定 |
| 目標 Schema | `YSC` |
| 目標資料表 | `SGLP0021_TEMP` |
| 寫入方式 | `INSERT` (executemany 批次寫入) |
| 參數佔位符 | `:1, :2, ...` (positional bind) |
| Config 區段 | `oracle_db_insert` |

#### 寫入欄位

| 欄位名稱 | 值 |
|----------|-----|
| `SOURCE_FROM` | `TW_BANK` |
| `FROM_CURRENCY` | 來源幣別 |
| `TO_CURRENCY` | 目標幣別 |
| `FROM_DATE` | 匯率日期 (today+1, 明天) |
| `TO_DATE` | 匯率日期 (today+1, 明天) |
| `CONVERSION_RATE` | 匯率 |
| `MODE_FLAG` | `I` |
| `IMPORTED_DATE` | 當下時間 (`datetime.now()`) |
| `IMPORTED_FLAG` | `N` |
| `USER_CONVERSION_TYPE` | `SC_銀行匯率` |

---

## 8. 設定檔

### 8.1 `config/db_config.yaml`

```yaml
dev_db:                          # SQL Server (優先使用)
  server: "172.31.x.x"
  database: "SERVICE_TEST"
  username: "xxx"
  password: "xxx"

database:                        # SQL Server (備用)
  server: "172.31.x.x"
  database: "SERVICE_TEST"
  username: "xxx"
  password: "xxx"

oracle_db_insert:                # Oracle 寫入
  user: "xxx"
  password: "xxx"
  host: "172.31.x.x"
  port: "1531"
  service_name: "UAT"
  client_lib_dir: "C:\\Oracle_instantclient_11_2"
```

### 8.2 `config/email_config.yaml`

```yaml
email:
  smtp_server: "172.31.x.x"
  port: 25
  sender_email: "xxx@sercomm.com"
  password: ""
  receiver_emails_success: "xxx@sercomm.com"
  receiver_emails_failure:
    - "xxx@sercomm.com"
```

### 8.3 HTML 模板 (`config/html/template/`)

| 模板檔案 | 用途 | 主要佔位符 |
|----------|------|-----------|
| `email_body.html` | Email 外框 | `${body}`, `${table_html}`, `${image_html}` |
| `monthly_rate_table.html` | 月匯率表格 | `${conversion_date}`, `${twd_rows}` |
| `rate_row.html` | 單筆匯率列 | `${from_currency}`, `${to_currency}`, `${conversion_date}`, `${conversion_rate}`, `${inverse_conversion_rate}` |
| `step_status.html` | 執行狀態表格 | `${step_rows}` |
| `step_row.html` | 單筆 Step 狀態列 | `${step_name}`, `${color}`, `${icon}`, `${result_text}` |

---

## 9. 郵件通知

### 9.1 錯誤處理策略

- **統一發信**：所有錯誤皆由 `async_main()` 統一捕獲並發送**一封**錯誤通知信
- **步驟追蹤**：透過 `current_step` 變數記錄當前執行階段
- **立即停止**：任一步驟發生錯誤即停止後續步驟

### 9.2 成功通知

| 項目 | 內容 |
|------|------|
| 主旨 | `(測試信)【匯率報告】月匯率執行結果 - {日期}` |
| 收件人 | `receiver_emails_success` |
| 信件內容 | 匯率表格 + 截圖 (INR/MXN/TRY) + 執行狀態 |
| Excel 附件 | `Monthly_Exchange_Rates_{YYYYMMDD_HHMMSS}.xlsx` (3 Sheets) |

### 9.3 失敗通知

| 項目 | 內容 |
|------|------|
| 主旨 | `【錯誤報告】月匯率程式異常 - {日期}` |
| 收件人 | `receiver_emails_failure` |
| 信件優先級 | **最高** (`X-Priority: 1`, `Importance: High`) |

---

## 10. 日誌

| 項目 | 值 |
|------|-----|
| 目錄 | `./logs/` |
| 檔名格式 | `currency_monthly_{YYYYMMDD}.log` |
| 同日重複執行 | 自動加流水號: `_01`, `_02`, ... |
| 輸出 | 同時寫入檔案與 Console |
| 格式 | `{時間} - {等級} - {模組} - {訊息}` |

---

## 11. Excel 報表

`export_monthly_report_and_email()` 會產生一份 Excel 報表，包含 3 個 Sheet：

| Sheet | 名稱 | 內容 | 資料來源 |
|-------|------|------|----------|
| 1 | SQL Server | 最新月匯率 (INR/MXN/TRY) | `SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE` |
| 2 | Oracle ERP | 當日月匯率 (INR/MXN/TRY) | `YSC.SGLP0021_TEMP` |
| 3 | Screenshots | 嵌入 3 張網頁截圖 (USDINR, USDMXN, TRYTWD) | `./data/web/{YYMMDD}/` |

- 檔案路徑: `./data/web/{YYMMDD}/Monthly_Exchange_Rates_{YYYYMMDD_HHMMSS}.xlsx`
- 作為 Email 附件寄出

---

## 12. 輸出檔案

| 類型 | 路徑 | 說明 |
|------|------|------|
| 截圖 | `./data/web/{YYMMDD}/USDINR_snapshot.jpg` | INR 匯率頁面截圖 |
| 截圖 | `./data/web/{YYMMDD}/USDMXN_snapshot.jpg` | MXN 匯率頁面截圖 |
| 截圖 | `./data/web/{YYMMDD}/TRYTWD_snapshot.jpg` | TRY 匯率頁面截圖 |
| Excel | `./data/web/{YYMMDD}/Monthly_Exchange_Rates_{YYYYMMDD_HHMMSS}.xlsx` | 月匯率報表 (3 Sheets) |
| 日誌 | `./logs/currency_monthly_{date}.log` | 執行日誌 |

---

## 13. 函式清單

| 函式 | 說明 | 回傳值 |
|------|------|--------|
| `_load_template()` | 從 `config/html/template/` 載入 HTML 模板 | `string.Template` |
| `setup_logging()` | 初始化日誌系統 | `logger` |
| `log_and_collect()` | 記錄日誌並收集至 `RUN_LOGS` | - |
| `load_db_config()` | 讀取 SQL Server 連線設定 (dev_db > database) | `dict` |
| `load_oracle_config()` | 讀取 Oracle 連線設定 (oracle_db_insert) | `dict` |
| `load_email_config()` | 讀取 Email 設定 | `dict` |
| `get_current_month_period()` | 計算當月起迄日 | `(start, end)` |
| `connect_to_db_sql()` | 建立 SQL Server 連線 (mssql_python) | `connection` 或 `None` |
| `connect_to_db_oracle()` | 建立 Oracle 連線 (Thick mode) | `connection` 或 `None` |
| `send_email_notification()` | 發送 HTML 郵件 (含截圖/附件)，錯誤信設定最高優先級 | `True` / `False` |
| `snapshot_page()` | async Playwright 擷取網頁截圖 (去廣告) | 檔案路徑 |
| `get_latest_usdtwd_rate_from_cbc()` | 取得 USD/TWD 匯率 (15 天回溯) | `float` 或 `None` |
| `fetch_currency_rate_selenium()` | Selenium 抓取特定幣別匯率 + 截圖 | `float` 或 `None` |
| `fetch_rate_with_fallback()` | 14 天回溯抓取匯率 | `float` 或 `None` |
| `calculate_monthly_rates()` | 計算月匯率（正向+反向） | `list[dict]` |
| `_execute_merge_rate()` | 執行單筆 MERGE SQL | - |
| `prepare_and_merge_sql_server()` | 計算匯率並批次寫入 SQL Server | `list[dict]` |
| `insert_to_oracle()` | 寫入匯率至 Oracle (executemany) | `True` |
| `build_monthly_rate_table()` | 使用模板產生月匯率 HTML 表格 | `html_str` |
| `export_monthly_report_and_email()` | 查詢 DB、產生 Excel 報表 (3 Sheets)、寄送 Email | - |
| `_build_step_status_html()` | 使用模板產生執行狀態 HTML 表格 | `html_str` |
| `async_main()` | 非同步主流程進入點 | - |

---

## 14. 相依套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `mssql-python` | >= 1.3.0 | SQL Server 連線 |
| `oracledb` | >= 3.4.2 | Oracle 連線 (Thick mode) |
| `requests` | >= 2.32.5 | HTTP 請求 (CBC 匯率) |
| `beautifulsoup4` | >= 4.14.3 | HTML 解析 |
| `playwright` | >= 1.58.0 | 網頁截圖 (async headless browser) |
| `selenium` | >= 4.40.0 | JS 渲染頁面抓取 |
| `webdriver-manager` | >= 4.0.2 | ChromeDriver 自動管理 |
| `pandas` | >= 3.0.0 | DataFrame 處理 |
| `openpyxl` | >= 3.1.0 | Excel 報表產生 |
| `pillow` | >= 12.1.1 | openpyxl 圖片嵌入所需 |
| `pyyaml` | >= 6.0.3 | YAML 設定檔解析 |

---

## 15. 錯誤處理

| 情境 | 處理方式 |
|------|----------|
| CBC 無法存取 | 回傳 `None`，主程式拋出 `RuntimeError` |
| 當日無匯率資料 | 自動回溯最多 14 天 |
| Selenium 抓取失敗 | 記錄 error log，回傳 `None` |
| 匯率資料不齊全 | 拋出 `RuntimeError`，由 `async_main()` 發送失敗郵件 |
| SQL Server 連線失敗 | 拋出 `ConnectionError`，由 `async_main()` 統一發送失敗郵件 |
| SQL Server 寫入失敗 | Rollback + 拋出例外 |
| Oracle 連線/寫入失敗 | Rollback + 拋出例外 |
| 郵件發送失敗 | 記錄 error log，回傳 `False` |
| 任何未預期例外 | `async_main()` catch-all，發送失敗郵件 (含步驟名稱 + 執行狀態) |

### 執行狀態表格

| Step | 說明 | 範例結果 |
|------|------|----------|
| Step 1: CBC USD/TWD | CBC 匯率抓取 | USD/TWD = 31.518 |
| Step 2: Selenium 抓取 | MXN/INR/TRY 匯率 | MXN=20.5, INR=83.0, TRY=0.85 |
| Step 3: SQL Server | SQL Server 寫入 | 6 筆匯率 Upsert 成功 |
| Step 4: Oracle | Oracle 寫入 | 6 筆寫入成功 |
| Step 5: 報表寄送 | Excel + Email | Excel 產生完成，Email 寄送中 |
