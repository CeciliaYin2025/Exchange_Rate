# ImportWebRate.py 規格書

## 1. 概述

每日匯率自動化程式，負責從央行 (CBC) 網站取得 USD/TWD 匯率，再透過 Selenium 從 CNYES 抓取 INR/MXN/TRY 匯率，計算交叉匯率後寫入 SQL Server 與 Oracle 資料庫，最後產生 Excel 報表並回傳結果供 `RunAllRates.py` 統一寄送 Email。

- **檔案位置**: `ImportWebRate.py`
- **Python 版本**: 3.11+
- **套件管理**: uv
- **排程方式**: 由 `RunAllRates.py` 呼叫，或直接執行
- **架構**: Async (`asyncio.run(_async_run())`)

---

## 2. 執行流程

```
┌─────────────────────────────────────────────────┐
│                  主程式啟動                       │
│  setup_logging() + Selenium driver init          │
└──────────────────────┬──────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 1: CBC USD/TWD 抓取   │
         │  get_latest_usdtwd_rate_   │
         │  from_cbc() → (rate, date) │
         └──────┬────────────┬────────┘
                │            │
          row_date        row_date
        == 系統日期     != 系統日期
          (一般)          (假日)
                │            │
   ┌────────────▼──┐  ┌──────▼───────────────────┐
   │  Step 2:       │  │  Step 2B: 假日模式        │
   │  Selenium 抓取 │  │  fetch_holiday_rates_     │
   │  MXN / INR /  │  │  from_oracle(row_date)    │
   │  TRY + 截圖    │  │  從 Oracle GL_DAILY_RATES_V│
   │  (CNYES)      │  │  取得前一交易日 INR/MXN/TRY │
   └────────────┬──┘  └──────────────┬────────────┘
                │                    │
                └───────────┬────────┘
                            │
         ┌──────────────────▼──────────┐
         │  Step 3: SQL Server 寫入     │
         │  prepare_and_merge_sql_     │
         │  server() (MERGE Upsert)    │
         └──────────────────┬──────────┘
                            │
         ┌──────────────────▼──────────┐
         │  Step 4: Oracle 寫入        │
         │  insert_to_oracle()         │
         │  (YSC.SGLP0021_TEMP)       │
         │  + sglp0021_pkg.main()      │
         └──────────────────┬──────────┘
                            │
         ┌──────────────────▼──────────┐
         │  Step 5: 產生 Excel 報表     │
         │  _export_web_excel()        │
         │  (3 Sheets: SS / Oracle /  │
         │   Screenshots)             │
         └─────────────────────────────┘
```

---

## 3. Async 架構說明

- 入口點: `run()` → `asyncio.run(_async_run())`
- `_async_run()` 為核心非同步主流程，回傳結果 `dict`
- `fetch_currency_rate_selenium()` 使用 Selenium 抓取並截圖 (同步截圖)
- `get_latest_usdtwd_rate_from_cbc()` 為同步函式 (requests)
- Email 由 `RunAllRates.py` 統一寄送，`ImportWebRate` 不再獨立發信（獨立執行例外）

---

## 4. 資料來源

| 來源 | 網址 | 用途 | 抓取方式 |
|------|------|------|----------|
| 中央銀行 (CBC) | `https://www.cbc.gov.tw/tw/lp-645-1.html` | USD/TWD 匯率 + 假日判斷 | requests + BeautifulSoup |
| CNYES | `https://invest.cnyes.com/forex/detail/{pair}/history` | INR/MXN/TRY 匯率 | Selenium |
| Oracle GL_DAILY_RATES_V | ERP Oracle DB | 假日模式：前一交易日匯率 | oracledb |

### 截圖

- 使用 Selenium 對 CNYES 歷史價格表格進行截圖（直接截取 `<table>` 元素）
- 截圖前隱藏廣告、導覽列、圖表等非表格元素
- 截圖儲存路徑:
  - `./data/web/{YYMMDD}/USDINR_snapshot.png`
  - `./data/web/{YYMMDD}/USDMXN_snapshot.png`
  - `./data/web/{YYMMDD}/TRYTWD_snapshot.png`

---

## 5. 處理幣別

### 每日匯率 (3 種)

| 幣別 | 來源 | 貨幣對 | 換算方式 |
|------|------|--------|----------|
| INR | CNYES (Selenium) | USDINR | `INR/TWD = USD_TWD / USD_INR` |
| MXN | CNYES (Selenium) | USDMXN | `MXN/TWD = USD_TWD / USD_MXN` |
| TRY | CNYES (Selenium) | TRYTWD | `TRY/TWD` 直接使用 (已是 TWD 計價) |

### 儲存規則

- 每筆匯率皆以**雙向**寫入 SQL Server (例: INR→TWD 與 TWD→INR)
- Oracle TEMP 只寫入**正向**匯率 (INR/MXN/TRY → TWD)
- `conversion_date` 設定為 `date.today() + timedelta(days=1)`（**明日**）

---

## 6. 假日判斷與回溯邏輯

### 6.1 假日判斷 (`_async_run`)

Step 1 取得 CBC USD/TWD 匯率後，會比對 `row_date`（CBC 最新匯率日期）與系統日期：

```python
system_date = date.today().strftime('%Y-%m-%d')
is_holiday = (row_date != system_date)
```

- **一般工作日**：`row_date == system_date` → 執行 Step 2（Selenium 抓取）
- **假日**：`row_date != system_date` → 執行 Step 2B（Oracle 假日查詢）

### 6.2 假日模式 (`fetch_holiday_rates_from_oracle`)

假日時，INR/MXN/TRY 不透過 Selenium 抓取，改從 Oracle `APPS.GL_DAILY_RATES_V` 查詢前一交易日的匯率：

```sql
SELECT FROM_CURRENCY, CONVERSION_RATE
FROM (
    SELECT FROM_CURRENCY, CONVERSION_RATE,
           ROW_NUMBER() OVER (PARTITION BY FROM_CURRENCY ORDER BY CONVERSION_DATE DESC) AS rn
    FROM APPS.GL_DAILY_RATES_V
    WHERE TRUNC(CONVERSION_DATE) >= TRUNC(:from_date)   -- row_date + 1
      AND TRUNC(CONVERSION_DATE) <= TRUNC(:to_date)     -- today + 1
      AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
      AND FROM_CURRENCY IN ('INR', 'MXN', 'TRY')
      AND TO_CURRENCY = 'TWD'
)
WHERE rn = 1
```

- **搜尋區間**：`[row_date + 1 天, 今天 + 1 天]`
- **處理長假**：使用 `ROW_NUMBER()` 取各幣別最新一筆，可應對多天連假
- **匯率換算**（假日模式）：
  - INR、MXN：Oracle 中為 `INR/TWD`、`MXN/TWD` 格式，需搭配當日 `usd_rate` 重新換算：
    ```
    all_rates['INR'] = (usd_rate, oracle_INR_rate)  → INR/TWD = usd_rate / oracle_INR_rate
    all_rates['MXN'] = (usd_rate, oracle_MXN_rate)
    ```
  - TRY：Oracle 中為 `TRY/TWD` 格式，直接使用：
    ```
    all_rates['TRY'] = (oracle_TRY_rate, 1)
    ```
- **假日截圖路徑**：指向前一個交易日的目錄 `./data/web/{row_date_YYMMDD}/`

### 6.3 CNYES 匯率回溯 (`fetch_rate_with_fallback`)

```
fetch_rate_with_fallback(driver, currency_name, currency_pair)
  └─ for i in range(0, 15):   ← 從「今天」開始，逐天往前
       target_date = today - i 天
       rate = fetch_currency_rate_selenium(driver, target_date, currency_pair)
       if rate:
           return rate          ← 找到即停止
  └─ return None                ← 14 天都找不到則回傳 None
```

**範例**：程式於 2026-02-15 (日) 執行（非假日模式下）

| 嘗試 | target_date | 星期 | CNYES 表格是否有資料 | 結果 |
|------|-------------|------|---------------------|------|
| i=0 | 2026-02-15 | 日 | 無 (假日無交易) | 繼續 |
| i=1 | 2026-02-14 | 六 | 無 (假日無交易) | 繼續 |
| i=2 | 2026-02-13 | 五 | 有 (收盤價存在) | 回傳匯率 |

### 6.4 匹配方式 (`fetch_currency_rate_selenium`)

1. Selenium 載入 CNYES 歷史價格頁面
2. 遍歷 `<table>` 每一行 `<tr>`
3. 比對第一欄日期 `col_texts[0]` 是否等於 `target_date`
4. 匹配成功 → 取第五欄收盤價 `col_texts[4]` 回傳
5. 整張表格都不匹配 → 回傳 `None`，由外層迴圈嘗試前一天

### 6.5 CBC USD/TWD 回溯 (`get_latest_usdtwd_rate_from_cbc`)

CBC 採用類似策略，但在單次 HTTP 請求內完成：
1. 產生最近 **15 天**日期清單 (`possible_dates`)
2. 解析 CBC 網頁表格，逐行比對日期
3. 第一筆匹配的日期即回傳其 USD/TWD 匯率 **和** 日期 (`rate, row_date`)

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
| `from_conversion_date` | 匯率日期 (today+1) |
| `to_conversion_date` | 匯率日期 (today+1) |
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
| 刪除舊資料 | 寫入前先刪除同日 INR/MXN/TRY 的 `SC_銀行匯率` 資料（避免重複） |
| 參數佔位符 | `:1, :2, ...` (positional bind) |
| Config 區段 | `oracle_db_insert` |
| 後處理 | 呼叫 `APPS.sglp0021_pkg.main()` 將 TEMP 轉入正式表，retcode != '0' 視為錯誤 |

#### 寫入欄位

| 欄位名稱 | 值 |
|----------|-----|
| `SOURCE_FROM` | `TW_BANK` |
| `FROM_CURRENCY` | 來源幣別 |
| `TO_CURRENCY` | 目標幣別 |
| `FROM_DATE` | 匯率日期 (today+1) |
| `TO_DATE` | 匯率日期 (today+1) |
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
| `oracle_rate_table.html` | Oracle 匯率表格 | `${oracle_rows}` |
| `oracle_rate_row.html` | Oracle 單筆匯率列 | `${from_currency}`, `${to_currency}`, `${conversion_date}`, `${conversion_type}`, `${conversion_rate}`, `${inverse_conversion_rate}` |
| `step_status.html` | 執行狀態表格 | `${step_rows}` |
| `step_row.html` | 單筆 Step 狀態列 | `${step_name}`, `${color}`, `${icon}`, `${result_text}` |
| `topic_web.html` | Email 目錄 | `${step_total}`, `${step_success}` |

---

## 9. `run()` 回傳格式

`ImportWebRate.run()` 回傳 dict，供 `RunAllRates.py` 彙整 Email：

```python
{
    'success': bool,                    # 整體是否成功
    'step_results': [                   # 各步驟狀態 list
        (step_name: str, result_text: str), ...
    ],
    'rows': list or None,               # SQL Server 寫入的匯率資料
    'oracle_inserted_data': list or None, # Oracle 寫入的資料
    'snapshot_paths': {                 # inline 截圖 CID → 檔案路徑
        'inr_img': str,
        'mxn_img': str,
        'try_img': str,
    },
    'excel_file_path': str or None,     # Excel 報表完整路徑
    'error': str or None,               # 錯誤訊息 (成功時為 None)
    'is_holiday': bool,                 # 是否為假日模式
}
```

---

## 10. 郵件通知

### 10.1 整合模式（由 RunAllRates.py 寄送）

`ImportWebRate.run()` 被 `RunAllRates.py` 呼叫時，**不自行寄送 Email**，僅回傳結果 dict，由主程式統一整合後寄送一封 Email。

### 10.2 獨立執行模式

直接執行 `python ImportWebRate.py` 時：
- 成功：不另行寄信（Email 已整合至 RunAllRates）
- 失敗：寄送錯誤通知信（收件人 `receiver_emails_failure`）

### 10.3 錯誤處理策略

- **步驟追蹤**：透過 `current_step` 變數記錄當前執行階段
- **立即停止**：任一步驟發生錯誤即停止後續步驟
- **失敗信主旨**：`【錯誤報告】月匯率程式異常 - {日期}`

---

## 11. 日誌

| 項目 | 值 |
|------|-----|
| 目錄 | `./logs/` |
| 檔名格式 | `currency_monthly_{YYYYMMDD}.log` |
| 同日重複執行 | 自動加流水號: `_01`, `_02`, ... |
| 輸出 | 同時寫入檔案與 Console |
| 格式 | `{時間} - {等級} - {模組} - {訊息}` |

---

## 12. Excel 報表

`_export_web_excel()` 會產生一份 Excel 報表，包含 3 個 Sheet：

| Sheet | 名稱 | 內容 | 資料來源 |
|-------|------|------|----------|
| 1 | SQL Server | 最新匯率 (INR/MXN/TRY) | `SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE` |
| 2 | Oracle ERP | 當日匯率 (INR/MXN/TRY) | `APPS.GL_DAILY_RATES_V` |
| 3 | Screenshots | 嵌入 3 張網頁截圖 (USDINR, USDMXN, TRYTWD) | `./data/web/{YYMMDD}/` |

- 檔案路徑: `./data/web/{YYMMDD}/Daily_Exchange_Rates_INR_MXN_TRY_{YYYYMMDD_HHMMSS}.xlsx`
- 作為 Email 附件寄出（由 RunAllRates.py 附加）

---

## 13. 輸出檔案

| 類型 | 路徑 | 說明 |
|------|------|------|
| 截圖 | `./data/web/{YYMMDD}/USDINR_snapshot.png` | INR 匯率頁面截圖 |
| 截圖 | `./data/web/{YYMMDD}/USDMXN_snapshot.png` | MXN 匯率頁面截圖 |
| 截圖 | `./data/web/{YYMMDD}/TRYTWD_snapshot.png` | TRY 匯率頁面截圖 |
| Excel | `./data/web/{YYMMDD}/Daily_Exchange_Rates_INR_MXN_TRY_{YYYYMMDD_HHMMSS}.xlsx` | 匯率報表 (3 Sheets) |
| 日誌 | `./logs/currency_monthly_{date}.log` | 執行日誌 |

---

## 14. 函式清單

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
| `snapshot_page()` | async Playwright 擷取網頁截圖 (去廣告，備用) | 檔案路徑 |
| `get_latest_usdtwd_rate_from_cbc()` | 取得 USD/TWD 匯率 (15 天回溯)，回傳匯率與日期 | `(float, str)` 或 `(None, None)` |
| `fetch_currency_rate_selenium()` | Selenium 抓取特定幣別匯率 + 截圖表格 | `float` 或 `None` |
| `fetch_rate_with_fallback()` | 14 天回溯抓取匯率 | `float` 或 `None` |
| `fetch_holiday_rates_from_oracle()` | 假日模式：從 Oracle GL_DAILY_RATES_V 取得前一交易日 INR/MXN/TRY 匯率 | `dict` 或 `None` |
| `calculate_monthly_rates()` | 計算匯率（正向+反向） | `list[dict]` |
| `_execute_merge_rate()` | 執行單筆 MERGE SQL | - |
| `prepare_and_merge_sql_server()` | 計算匯率並批次寫入 SQL Server | `list[dict]` |
| `insert_to_oracle()` | 寫入匯率至 Oracle (executemany) + 執行 sglp0021_pkg.main | `list[dict]` |
| `build_monthly_rate_table()` | 使用模板產生月匯率 HTML 表格 | `html_str` |
| `query_oracle_bank_rates()` | 從 Oracle GL_DAILY_RATES_V 查詢最新 INR/MXN/TRY 匯率 | `list[dict]` 或 `None` |
| `build_oracle_rate_table()` | 使用 Oracle 查詢結果建構 SC_銀行匯率 HTML 表格 | `html_str` |
| `_build_step_status_html()` | 使用模板產生執行狀態 HTML 表格 | `html_str` |
| `_export_web_excel()` | 產生 Excel 報表 (3 Sheets)，回傳檔案路徑 | `str` 或 `None` |
| `_async_run()` | 內部 async 主流程，回傳結果 dict | `dict` |
| `run()` | 同步入口，呼叫 `asyncio.run(_async_run())` | `dict` |

---

## 15. 相依套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `mssql-python` | >= 1.3.0 | SQL Server 連線 |
| `oracledb` | >= 3.4.2 | Oracle 連線 (Thick mode) |
| `requests` | >= 2.32.5 | HTTP 請求 (CBC 匯率) |
| `beautifulsoup4` | >= 4.14.3 | HTML 解析 |
| `playwright` | >= 1.58.0 | 網頁截圖 (async headless browser，備用) |
| `selenium` | >= 4.40.0 | JS 渲染頁面抓取 + 截圖 |
| `webdriver-manager` | >= 4.0.2 | ChromeDriver 自動管理 |
| `pandas` | >= 3.0.0 | DataFrame 處理 |
| `openpyxl` | >= 3.1.0 | Excel 報表產生 |
| `pillow` | >= 12.1.1 | openpyxl 圖片嵌入所需 |
| `pyyaml` | >= 6.0.3 | YAML 設定檔解析 |

---

## 16. 錯誤處理

| 情境 | 處理方式 |
|------|----------|
| CBC 無法存取 | 回傳 `(None, None)`，主程式拋出 `RuntimeError` |
| 當日無匯率資料 | 自動回溯最多 14 天 |
| CBC 日期與系統日期不符 | 切換假日模式（Step 2B），從 Oracle 查前一交易日匯率 |
| 假日模式 Oracle 無資料 | 拋出 `RuntimeError`，結束流程 |
| Selenium 抓取失敗 | 記錄 error log，回傳 `None` |
| 匯率資料不齊全 | 拋出 `RuntimeError`，由 `_async_run()` 記錄錯誤 |
| SQL Server 連線失敗 | 拋出 `ConnectionError` |
| SQL Server 寫入失敗 | Rollback + 拋出例外 |
| Oracle 連線/寫入失敗 | Rollback + 拋出例外 |
| sglp0021_pkg.main retcode != '0' | 拋出 `RuntimeError` |
| 郵件發送失敗 | 記錄 error log，回傳 `False` |
| 任何未預期例外 | `_async_run()` catch-all，記錄錯誤至 `result_data['error']` |

### 執行狀態表格

| Step | 說明 | 範例結果（一般模式） |
|------|------|---------------------|
| Step 1: CBC USD/TWD | CBC 匯率抓取 | `USD/TWD = 31.5180 (日期: 2026-03-14)` |
| Step 2: Selenium 抓取 | MXN/INR/TRY 匯率 | `MXN=20.5, INR=83.0, TRY=0.85` |
| Step 3: SQL Server | SQL Server 寫入 | `6 筆匯率 Upsert 成功` |
| Step 4: Oracle | Oracle 寫入 | `3 筆寫入成功` |
| Step 5: 產生報表 | Excel 產生 | `Excel 產生完成` |

| Step | 說明 | 範例結果（假日模式） |
|------|------|---------------------|
| Step 1: CBC USD/TWD | CBC 匯率抓取 | `USD/TWD = 31.5180 (日期: 2026-03-13)` |
| Step 2B: 假日 Oracle 匯率 | 從 Oracle 查詢前一交易日 | `INR=1.2345, MXN=1.5678, TRY=0.9012` |
| Step 3: SQL Server | SQL Server 寫入 | `6 筆匯率 Upsert 成功` |
| Step 4: Oracle | Oracle 寫入 | `3 筆寫入成功` |
| Step 5: 產生報表 | Excel 產生 | `Excel 產生完成` |
