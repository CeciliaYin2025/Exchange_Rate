# ImportDailyRate.py 規格書

## 1. 概述

每日銀行匯率自動化程式，負責從台灣央行 (CBC) 及台灣銀行 (TCB) 網站抓取匯率資料，計算交叉匯率後寫入 SQL Server 與 Oracle 資料庫，最後產生 Excel 報表（三個 Sheet）並以 Email 通知，信件含目錄導覽、匯率表格、網頁截圖及執行狀態摘要。

- **檔案位置**: `ImportDailyRate.py`
- **Python 版本**: 3.11+
- **套件管理**: uv
- **排程方式**: `run_daily.bat` 或 Windows Task Scheduler

---

## 2. 執行流程

```
┌─────────────────────────────────────────────────────┐
│                    主程式啟動                         │
│                 setup_logging()                      │
└──────────────────────┬──────────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 1: 抓取匯率           │
         │  get_daily_rate_from_tcb()  │
         │    ├─ CBC: USD/TWD 匯率     │
         │    ├─ CBC 網頁截圖           │
         │    ├─ TCB: 7 種 TWD 匯率    │
         │    └─ TCB 網頁截圖           │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 2: SQL Server 寫入    │
         │  insert_daily_rate()       │
         │  (TWD-based, 8 幣別雙向)    │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 3: USD 交叉匯率       │
         │  get_daily_rate_with_usd() │
         │  insert_usd_rates()        │
         │  (USD-based, 5 幣別雙向)    │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 4: Oracle 寫入        │
         │  insert_to_oracle()        │
         │  (TWD + USD, 共 26 筆)      │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 5: 報表與郵件通知      │
         │  export_and_email_rates()  │
         │  (Excel 3 Sheets + 截圖)    │
         └────────────────────────────┘
```

---

## 3. 資料來源

| 來源 | 網址 | 用途 | 抓取方式 |
|------|------|------|----------|
| 中央銀行 (CBC) | `https://www.cbc.gov.tw/tw/lp-645-1.html` | USD/TWD 收盤匯率 | requests + BeautifulSoup |
| 台灣銀行 (TCB) | `https://rate.bot.com.tw/xrt/all/{date}` | 7 種幣別即期匯率 | requests + BeautifulSoup |

### 截圖
- 使用 Playwright (headless Chromium) 對兩個來源網頁進行全頁截圖
- 截圖儲存路徑:
  - `./data/daily/{YYMMDD}/web_snapshots/tcb_rate_snapshot.jpg`
  - `./data/daily/{YYMMDD}/web_snapshots/cbc_rate_snapshot.jpg`

---

## 4. 處理幣別

### TWD 基準匯率 (8 種)

| 幣別 | 來源 | 取值方式 |
|------|------|----------|
| USD | CBC | 收盤價直接取用 |
| CNY | TCB | 即期買入 + 即期賣出 平均值 |
| EUR | TCB | 即期買入 + 即期賣出 平均值 |
| GBP | TCB | 即期買入 + 即期賣出 平均值 |
| CAD | TCB | 即期買入 + 即期賣出 平均值 |
| AUD | TCB | 即期買入 + 即期賣出 平均值 |
| JPY | TCB | 即期買入 + 即期賣出 平均值 |
| PHP | TCB | 現金賣出價 (無即期匯率) |

### USD 基準交叉匯率 (5 種)

由 TWD 匯率換算: `USD/XXX = (XXX/TWD) / (USD/TWD)`

- EUR, CAD, AUD, JPY, PHP

### 儲存規則
- 每筆匯率皆以**雙向**寫入 (例: USD->TWD 與 TWD->USD)
- 反向匯率 = `1 / 正向匯率`
- `conversion_date` 設定為**明天** (`today + 1`)

---

## 5. 日期回溯邏輯

若當天 (today) 無匯率資料 (假日/國定假日)，程式自動往前回溯最多 **14 天** (`MAX_LOOKBACK_DAYS`) 取得最近的交易日匯率。

---

## 6. 資料庫

### 6.1 SQL Server

| 項目 | 值 |
|------|-----|
| 連線方式 | `mssql-python` (DB API 2.0, 直連) |
| 目標資料庫 | `SERVICE_TEST` |
| 目標資料表 | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` |
| 寫入方式 | `MERGE` (Upsert) |
| 參數佔位符 | `?` (qmark style) |
| Config 優先順序 | `dev_db` > `database` |

#### MERGE 匹配條件

- TWD 基準匯率: `from_currency` + `to_currency` + `from_conversion_date`
- USD 基準匯率: 上述三欄位 + `user_conversion_type`

#### 寫入欄位

| 欄位名稱 | 值 |
|----------|-----|
| `from_currency` | 來源幣別 |
| `to_currency` | 目標幣別 |
| `from_conversion_date` | 匯率日期 (明天) |
| `to_conversion_date` | 匯率日期 (明天) |
| `user_conversion_type` | `SC_銀行匯率` |
| `conversion_rate` | 匯率 |
| `mode_flag` | `I` |
| `inverse_conversion_rate` | 反向匯率 |
| `user_id` | `1175` |
| `launch_rate_change` | `Y` |
| `batch_number` | `NULL` |
| `insert_time` | `GETDATE()` |

### 6.2 Oracle

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
| `FROM_DATE` | 當下時間 |
| `TO_DATE` | 當下時間 |
| `CONVERSION_RATE` | 匯率 |
| `MODE_FLAG` | `I` |
| `IMPORTED_DATE` | 當下時間 |
| `IMPORTED_FLAG` | `N` |
| `USER_CONVERSION_TYPE` | `SC_銀行匯率` |

#### 資料筆數

每次執行寫入 Oracle 共 **26 筆**:
- TWD 基準: 8 幣別 x 2 方向 = 16 筆
- USD 基準: 5 幣別 x 2 方向 = 10 筆

#### Oracle ERP 查詢 (報表用)

報表產生時，會查詢 Oracle `YSC.SGLP0021_TEMP` 以取得 ERP 端資料。查詢條件使用 `TRUNC(FROM_DATE) = TRUNC(:1)` 進行日期比對（忽略時分秒），避免因寫入與查詢時間差異導致查無資料。

---

## 7. 設定檔

### 7.1 `config/db_config.yaml`

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

### 7.2 `config/email_config.yaml`

```yaml
email:
  smtp_server: "172.31.x.x"
  port: 25
  sender_email: "xxx@sercomm.com"
  password: ""
  receiver_emails_success: "xxx@sercomm.com"   # 成功通知
  receiver_emails_failure:                      # 失敗通知 (可多人)
    - "xxx@sercomm.com"
    - "xxx@sercomm.com"
```

### 7.3 HTML 模板 (`config/html/template/`)

郵件中所有 HTML 區塊皆使用 `string.Template` 從外部檔案載入，提升程式碼可讀性與維護性。

| 模板檔案 | 用途 | 主要佔位符 |
|----------|------|-----------|
| `email_body.html` | Email 外框 (body + table + 圖片 + footer) | `${body}`, `${table_html}`, `${image_html}` |
| `rate_table.html` | TWD/USD 匯率表格外框 (含錨點) | `${conversion_date}`, `${twd_rows}`, `${usd_rows}` |
| `rate_row.html` | 單筆匯率列 | `${from_currency}`, `${to_currency}`, `${conversion_date}`, `${conversion_rate}`, `${inverse_conversion_rate}` |
| `step_status.html` | 執行狀態表格外框 (含錨點) | `${step_rows}` |
| `step_row.html` | 單筆 Step 狀態列 | `${step_name}`, `${color}`, `${icon}`, `${result_text}` |
| `topic.html` | 目錄導覽區塊 (含錨點連結) | `${step_total}`, `${step_success}` |

---

## 8. 郵件通知

### 8.1 錯誤處理策略

- **統一發信**：所有錯誤皆由 `__main__` 區塊統一捕獲並發送**一封**錯誤通知信，避免重複發信
- **步驟追蹤**：透過 `current_step` 變數記錄當前執行階段，錯誤信中會標示失敗的步驟名稱
- **立即停止**：任一步驟發生錯誤即停止後續步驟執行

### 8.2 成功通知

| 項目 | 內容 |
|------|------|
| 主旨 | `(測試信)【匯率報告】每日銀行匯率報告 - {日期}` |
| 收件人 | `receiver_emails_success` |
| 信件優先級 | 一般 |

#### 信件內容結構

```
┌──────────────────────────────────────────────┐
│ 目錄                                          │
│  1. TWD-based Exchange Rates    ← 錨點連結    │
│  2. USD-based Exchange Rates    ← 錨點連結    │
│  3. 網頁截圖                     ← 錨點連結    │
│  4. 執行狀態 (5/5 成功)          ← 錨點連結    │
├──────────────────────────────────────────────┤
│ 匯率基準日說明                                 │
├──────────────────────────────────────────────┤
│ TWD-based Exchange Rates 表格   id=twd-rates │
│  欄位: from_currency, to_currency,           │
│        from_conversion_date,                 │
│        conversion_rate, inverse_rate         │
├──────────────────────────────────────────────┤
│ USD-based Exchange Rates 表格   id=usd-rates │
│  欄位: (同上)                                 │
├──────────────────────────────────────────────┤
│ 網頁截圖                     id=web-snapshots │
│  TCB 截圖 (inline image)                      │
│  CBC 截圖 (inline image)                      │
├──────────────────────────────────────────────┤
│ 執行狀態                      id=step-status │
│  Step 1~5 各步驟結果 (綠勾/紅叉)              │
├──────────────────────────────────────────────┤
│ 此郵件由系統自動發送，請勿直接回覆。            │
└──────────────────────────────────────────────┘
  附件: Daily_Exchange_Rates_{timestamp}.xlsx
```

#### 匯率表格欄位 (與 Excel 一致)

| 欄位 | 說明 |
|------|------|
| `from_currency` | 來源幣別 |
| `to_currency` | 目標幣別 |
| `from_conversion_date` | 匯率日期 |
| `conversion_rate` | 匯率 |
| `inverse_conversion_rate` | 反向匯率 |

#### 執行狀態表格

| Step | 說明 | 範例結果 |
|------|------|----------|
| Step 1: 抓取匯率 | Web Scraping | 取得 7 種幣別匯率，USD/TWD = 31.518 |
| Step 2: SQL Server TWD | TWD-based 寫入 | 8 筆匯率 Upsert 成功（日期: 2026-02-15） |
| Step 3: SQL Server USD | USD-based 寫入 | 5 筆 USD-based 匯率寫入完成 |
| Step 4: Oracle | Oracle 寫入 | 26 筆寫入成功 |
| Step 5: 報表寄送 | Excel + Email | Excel 產生完成，Email 寄送中 |

### 8.3 失敗通知

| 項目 | 內容 |
|------|------|
| 主旨 | `【錯誤報告】每日匯率程式異常 - {日期}` |
| 收件人 | `receiver_emails_failure` |
| 信件優先級 | **最高** (`X-Priority: 1`, `Importance: High`) |
| 內文 | 失敗步驟名稱 + 錯誤訊息 + 執行狀態表格 (已完成步驟綠勾、失敗步驟紅叉) |

---

## 9. Excel 報表

產生的 Excel 檔包含三個 Sheet：

| Sheet 名稱 | 資料來源 | 說明 |
|------------|----------|------|
| `Web Source` | 傳入的 `web_rates` + `web_usd_rows` | 網頁爬取的原始匯率資料 |
| `SQL Server` | SQL Server 查詢 | 資料庫中最新銀行匯率 (排除 TWD/USD 反向列) |
| `Oracle ERP` | Oracle `YSC.SGLP0021_TEMP` 查詢 | ERP 端匯率資料 (以 `TRUNC` 比對當日) |

### Web Source 欄位

`from_currency`, `to_currency`, `from_conversion_date`, `conversion_rate`, `inverse_conversion_rate`

### SQL Server 欄位

`from_currency`, `to_currency`, `from_conversion_date`, `conversion_rate`, `inverse_conversion_rate`

### Oracle ERP 欄位

`SOURCE_FROM`, `FROM_CURRENCY`, `TO_CURRENCY`, `FROM_DATE`, `TO_DATE`, `CONVERSION_RATE`, `MODE_FLAG`, `IMPORTED_DATE`, `IMPORTED_FLAG`, `USER_CONVERSION_TYPE`

---

## 10. 日誌

| 項目 | 值 |
|------|-----|
| 目錄 | `./logs/` |
| 檔名格式 | `currency_daily_{YYYYMMDD}.log` |
| 同日重複執行 | 自動加流水號: `_01`, `_02`, ... |
| 輸出 | 同時寫入檔案與 Console |
| 格式 | `{時間} - {等級} - {模組} - {訊息}` |

---

## 11. 輸出檔案

| 類型 | 路徑 | 說明 |
|------|------|------|
| 截圖 | `./data/daily/{YYMMDD}/web_snapshots/tcb_rate_snapshot.jpg` | TCB 匯率頁面截圖 |
| 截圖 | `./data/daily/{YYMMDD}/web_snapshots/cbc_rate_snapshot.jpg` | CBC 匯率頁面截圖 |
| 報表 | `./data/daily/{YYMMDD}/Daily_Exchange_Rates_{timestamp}.xlsx` | 匯率 Excel (三個 Sheet) |
| 日誌 | `./logs/currency_daily_{date}.log` | 執行日誌 |

---

## 12. 函式清單

| 函式 | 說明 | 回傳值 |
|------|------|--------|
| `_load_template()` | 從 `config/html/template/` 載入 HTML 模板 | `string.Template` |
| `setup_logging()` | 初始化日誌系統 | `logger` |
| `log_and_collect()` | 記錄日誌並收集至 `RUN_LOGS` | - |
| `load_db_config()` | 讀取 SQL Server 連線設定 | `dict` |
| `load_oracle_config()` | 讀取 Oracle 連線設定 | `dict` |
| `load_email_config()` | 讀取 Email 設定 | `dict` |
| `connect_to_db_sql()` | 建立 SQL Server 連線 | `connection` 或 `None` |
| `connect_to_db_oracle()` | 建立 Oracle 連線 (Thick mode) | `connection` 或 `None` |
| `send_email_notification()` | 發送 HTML 郵件 (含附件/截圖)，錯誤信自動設定最高優先級 | `True` / `False` |
| `snapshot_page()` | Playwright 擷取網頁截圖 | 檔案路徑 |
| `get_target_date()` | 從 CBC 取得最近有資料的交易日 | `(rate, date_str)` 或 `None` |
| `get_latest_usdtwd_rate_from_cbc()` | 取得 USD/TWD 收盤匯率 | `(date_str, rate)` 或 `None` |
| `get_daily_rate_from_tcb()` | 取得 7 種 TWD 基準匯率 | `(rates_dict, usd_rate)` 或 `None` |
| `get_daily_rate_with_usd()` | 計算 USD 基準交叉匯率 | `(rows, rates_with_usd, date)` |
| `build_rate_rows()` | 組裝單筆匯率 dict | `dict` |
| `build_rate_table()` | 使用模板產生 HTML 匯率表格 | `html_str` |
| `_build_step_status_html()` | 使用模板產生執行狀態 HTML 表格 | `html_str` |
| `_execute_merge_rate()` | 執行單筆 MERGE SQL (內部函式) | - |
| `insert_daily_rate()` | 寫入 TWD 基準匯率至 SQL Server | - |
| `insert_usd_rates()` | 寫入 USD 基準匯率至 SQL Server | - |
| `insert_to_oracle()` | 寫入所有匯率至 Oracle | `True` |
| `export_and_email_rates()` | 查詢匯率、產生 Excel (3 Sheets)、組裝目錄與執行狀態、寄送郵件 | - |

---

## 13. 相依套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `mssql-python` | >= 1.3.0 | SQL Server 連線 (Microsoft 官方驅動) |
| `oracledb` | >= 3.4.2 | Oracle 連線 (Thick mode) |
| `requests` | >= 2.32.5 | HTTP 請求 (抓取匯率網頁) |
| `beautifulsoup4` | >= 4.14.3 | HTML 解析 |
| `playwright` | >= 1.58.0 | 網頁截圖 (headless browser) |
| `pandas` | >= 3.0.0 | DataFrame 處理 / Excel 匯出 |
| `openpyxl` | >= 3.1.5 | Excel 寫入引擎 |
| `pyyaml` | >= 6.0.3 | YAML 設定檔解析 |

---

## 14. 錯誤處理

| 情境 | 處理方式 |
|------|----------|
| 網頁無法存取 | 記錄 error log，回傳 `None`，主程式拋出 `RuntimeError` |
| 當日無匯率資料 | 自動回溯最多 14 天 |
| SQL Server 連線失敗 | 拋出 `ConnectionError`，由 `__main__` 統一發送失敗郵件 |
| SQL Server 寫入失敗 | Rollback + 拋出例外，由 `__main__` 統一發送失敗郵件 |
| Oracle 連線/寫入失敗 | Rollback + 拋出例外，由 `__main__` 統一發送失敗郵件 |
| Oracle ERP 查詢失敗 (報表用) | 記錄 warning，Oracle ERP Sheet 為空白，不中斷流程 |
| 郵件發送失敗 | 記錄 error log，回傳 `False` |
| 任何未預期例外 | `__main__` catch-all，發送失敗郵件 (含失敗步驟名稱 + 執行狀態表格) |

### 錯誤通知信特性

- **統一發信**：任何步驟失敗只發送**一封**錯誤通知信，避免重複
- **最高優先級**：錯誤信設定 `X-Priority: 1` + `Importance: High`
- **步驟追蹤**：信中標示失敗步驟名稱及已完成步驟清單
