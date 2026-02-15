# ImportPortalRate.py 規格書

## 1. 概述

三旬匯率自動化程式，負責從關務署 (portal.sw.nat.gov.tw) JSON API 抓取關稅匯率資料，計算換算率後寫入 DWNotes SQL Server 資料庫，產生 Excel 報表並以 Email 通知，信件含匯率表格及執行狀態摘要。

- **檔案位置**: `ImportPortalRate.py`
- **Python 版本**: 3.11+
- **套件管理**: uv
- **排程方式**: Windows Task Scheduler 或手動執行

---

## 2. 執行流程

```
┌─────────────────────────────────────────────────┐
│                  主程式啟動                       │
│               setup_logging()                    │
└──────────────────────┬──────────────────────────┘
                       │
         ┌─────────────▼──────────────────────────┐
         │  Step 1: API 資料抓取                    │
         │  fetch_json()                           │
         │  (EUR, JPY, USD from API)               │
         │  + 儲存原始 JSON 到 ./data/portal/{yymmdd}/ │
         └─────────────┬──────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 2: SQL Server 寫入    │
         │  insert_portal_rates()     │
         │  (DWNotes, SC_三旬匯率)     │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼──────────────────────┐
         │  Step 3: Oracle 寫入               │
         │  insert_portal_rates_oracle()      │
         │  (YSC.SGLP0021_TEMP, SC_三旬匯率)  │
         └─────────────┬──────────────────────┘
                       │
         ┌─────────────▼──────────────────────────┐
         │  Step 4: Excel 報表產生 (3 sheets)       │
         │  _build_excel_sheets() + ExcelWriter    │
         │  存到 ./data/portal/{yymmdd}/            │
         └─────────────┬──────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Step 5: 報表寄送           │
         │  send_email_notification() │
         │  (Excel 附件 + 匯率表格)    │
         └────────────────────────────┘
```

---

## 3. 資料來源

| 來源 | 網址 | 用途 | 抓取方式 |
|------|------|------|----------|
| 關務署 | `https://portal.sw.nat.gov.tw/APGQ/GC331!downLoad?formBean.downLoadFile=CURRENT_JSON` | EUR/JPY/USD 三旬匯率 | requests (JSON API) |

### API 回傳結構

```json
{
  "start": "20260201",
  "end": "20260210",
  "items": [
    {"code": "EUR", "buyValue": "34.5", ...},
    {"code": "JPY", "buyValue": "0.22", ...},
    {"code": "USD", "buyValue": "31.5", ...}
  ]
}
```

---

## 4. 處理幣別

### 三旬匯率 (3 種)

| 幣別 | 來源 | 換算方式 |
|------|------|----------|
| EUR | 關務署 API | `conversion_rate = 1 / buyValue` |
| JPY | 關務署 API | `conversion_rate = 1 / buyValue` |
| USD | 關務署 API | `conversion_rate = 1 / buyValue` |

### 儲存規則

- 每筆匯率皆以**雙向**寫入 (例: EUR→TWD 與 TWD→EUR)
- `buyValue` 作為正向匯率 (XXX→TWD)
- `1/buyValue` 作為反向匯率 (TWD→XXX)
- 日期範圍：API 回傳的 `start`~`end` 展開為每日

---

## 5. 日期範圍處理

API 回傳 `start` 和 `end` (格式 `YYYYMMDD`)，程式會展開為每日日期列表，為每個幣別在每天都執行一次 MERGE。

---

## 6. 資料庫

### SQL Server

| 項目 | 值 |
|------|-----|
| 連線方式 | `mssql-python` (DB API 2.0, 直連) |
| 目標資料庫 | `DWNotes` |
| 目標資料表 | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` |
| 寫入方式 | `MERGE` (Upsert) |
| 參數佔位符 | `?` (qmark style) |
| Config 優先順序 | `database_notes` > `database` |

#### MERGE 匹配條件

- `from_currency` + `to_currency` + `from_conversion_date` + `user_conversion_type`

#### 寫入欄位

| 欄位名稱 | 值 |
|----------|-----|
| `from_currency` | 來源幣別 |
| `to_currency` | 目標幣別 |
| `from_conversion_date` | 匯率日期 (展開每日) |
| `to_conversion_date` | 匯率日期 (展開每日) |
| `user_conversion_type` | `SC_三旬匯率` |
| `conversion_rate` | 匯率 |
| `mode_flag` | `I` |
| `inverse_conversion_rate` | 反向匯率 |
| `user_id` | `1175` |
| `launch_rate_change` | `Y` |
| `batch_number` | `NULL` |
| `insert_time` | `GETDATE()` |

### Oracle

| 項目 | 值 |
|------|-----|
| 連線方式 | `oracledb` Thick mode (`init_oracle_client`) |
| 目標資料表 | `YSC.SGLP0021_TEMP` |
| 寫入方式 | `INSERT` via `cursor.executemany()` |
| 參數佔位符 | `:1, :2, ...` (positional) |
| Config key | `oracle_db_insert` |

#### 寫入欄位

| 欄位名稱 | 值 |
|----------|-----|
| `SOURCE_FROM` | `TW_BANK` |
| `FROM_CURRENCY` | 來源幣別 |
| `TO_CURRENCY` | 目標幣別 |
| `FROM_DATE` | 匯率日期 (展開每日) |
| `TO_DATE` | 匯率日期 (展開每日) |
| `CONVERSION_RATE` | 匯率 |
| `MODE_FLAG` | `I` |
| `IMPORTED_DATE` | 匯率日期 (展開每日) |
| `IMPORTED_FLAG` | `N` |
| `USER_CONVERSION_TYPE` | `SC_三旬匯率` |

---

## 7. 設定檔

### 7.1 `config/db_config.yaml`

```yaml
database_notes:                  # DWNotes (優先使用)
  server: "172.31.x.x"
  database: "DWNotes"
  username: "xxx"
  password: "xxx"

database:                        # 備用
  server: "172.31.x.x"
  database: "SERVICE_TEST"
  username: "xxx"
  password: "xxx"
```

### 7.2 `config/email_config.yaml`

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

### 7.3 HTML 模板 (`config/html/template/`)

| 模板檔案 | 用途 | 主要佔位符 |
|----------|------|-----------|
| `email_body.html` | Email 外框 | `${body}`, `${table_html}`, `${image_html}` |
| `portal_rate_table.html` | 三旬匯率表格 | `${period_start}`, `${period_end}`, `${portal_rows}` |
| `portal_rate_row.html` | 三旬匯率列 | `${code}`, `${buy_value}`, `${conversion_rate}` |
| `step_status.html` | 執行狀態表格 | `${step_rows}` |
| `step_row.html` | 單筆 Step 狀態列 | `${step_name}`, `${color}`, `${icon}`, `${result_text}` |

---

## 8. 郵件通知

### 8.1 錯誤處理策略

- **統一發信**：所有錯誤皆由 `main()` 統一捕獲並發送**一封**錯誤通知信
- **步驟追蹤**：透過 `current_step` 變數記錄當前執行階段
- **立即停止**：任一步驟發生錯誤即停止後續步驟執行

### 8.2 成功通知

| 項目 | 內容 |
|------|------|
| 主旨 | `(測試信)【三旬匯率報告】關務署匯率報告 - {start}~{end}` |
| 收件人 | `receiver_emails_success` |
| 附件 | `Portal_Exchange_Rates_{timestamp}.xlsx` |

### 8.3 失敗通知

| 項目 | 內容 |
|------|------|
| 主旨 | `【錯誤報告】三旬匯率程式異常 - {日期}` |
| 收件人 | `receiver_emails_failure` |
| 信件優先級 | **最高** (`X-Priority: 1`, `Importance: High`) |

---

## 9. 資料儲存目錄

所有 Portal Rate 相關資料 (原始 JSON + Excel 報表) 統一儲存至：

```
./data/portal/{YYMMDD}/
├── portal_raw_{timestamp}.json     # API 原始 JSON (完整回傳，含所有幣別)
└── Portal_Exchange_Rates_{timestamp}.xlsx  # Excel 報表 (3 sheets)
```

### 9.1 原始 JSON 儲存

- `fetch_json()` 回傳 4-tuple: `(filtered_items, start, end, raw_data)`
- `raw_data` 為 API 完整回傳 dict (含 start, end, items)，包含所有幣別 (非僅 EUR/JPY/USD)
- 儲存格式：UTF-8, indent=2

### 9.2 Excel 報表 (3 Sheets)

| Sheet 名稱 | 資料來源 | 欄位 |
|------------|----------|------|
| `Oracle_SGLP0021_TEMP` | filtered_items + day_list 展開，雙向寫入 | SOURCE_FROM, FROM_CURRENCY, TO_CURRENCY, FROM_DATE, TO_DATE, CONVERSION_RATE, MODE_FLAG, IMPORTED_DATE, IMPORTED_FLAG, USER_CONVERSION_TYPE |
| `DWNotes_DAILY_RATES` | filtered_items + day_list 展開，雙向寫入 | from_currency, to_currency, from_conversion_date, to_conversion_date, user_conversion_type, conversion_rate, inverse_conversion_rate, mode_flag, user_id, launch_rate_change |
| `API_Raw_Data` | raw_data['items'] (全部幣別，未過濾) | API 原始欄位 |

Excel 產生時機：在 DB 寫入 (SQL Server + Oracle) 完成後，以確保 Oracle/DWNotes sheets 呈現的資料與實際寫入一致。

---

## 10. 日誌

| 項目 | 值 |
|------|-----|
| 目錄 | `./logs/` |
| 檔名格式 | `currency_portal_{YYYYMMDD}.log` |
| 同日重複執行 | 自動加流水號: `_01`, `_02`, ... |
| 輸出 | 同時寫入檔案與 Console |
| 格式 | `{時間} - {等級} - {模組} - {訊息}` |

---

## 11. 函式清單

| 函式 | 說明 | 回傳值 |
|------|------|--------|
| `_load_template()` | 從 `config/html/template/` 載入 HTML 模板 | `string.Template` |
| `setup_logging()` | 初始化日誌系統 | `logger` |
| `log_and_collect()` | 記錄日誌並收集至 `RUN_LOGS` | - |
| `load_db_config()` | 讀取 SQL Server 連線設定 (database_notes > database) | `dict` |
| `load_email_config()` | 讀取 Email 設定 | `dict` |
| `load_oracle_config()` | 讀取 Oracle 連線設定 (oracle_db_insert) | `dict` |
| `connect_to_db_sql()` | 建立 SQL Server 連線 (mssql_python) | `connection` 或 `None` |
| `connect_to_db_oracle()` | 建立 Oracle 連線 (oracledb Thick mode) | `connection` 或 `None` |
| `send_email_notification()` | 發送 HTML 郵件，錯誤信設定最高優先級 | `True` / `False` |
| `fetch_json()` | 從關務署 API 抓取 JSON 匯率資料 | `(items, start, end, raw_data)` 或 `None` |
| `build_portal_rate_table()` | 使用模板產生三旬匯率 HTML 表格 | `html_str` |
| `_build_excel_sheets()` | 建構 Excel 3 sheets 的 DataFrame dict | `dict[str, DataFrame]` |
| `_execute_merge_rate()` | 執行單筆 MERGE SQL (SC_三旬匯率) | - |
| `insert_portal_rates()` | 批次寫入三旬匯率至 DWNotes | `int` (寫入筆數) |
| `insert_portal_rates_oracle()` | 批次寫入三旬匯率至 Oracle (YSC.SGLP0021_TEMP) | `int` (寫入筆數) |
| `_build_step_status_html()` | 使用模板產生執行狀態 HTML 表格 | `html_str` |
| `main()` | 主流程進入點 | - |

---

## 12. 相依套件

| 套件 | 版本 | 用途 |
|------|------|------|
| `mssql-python` | >= 1.3.0 | SQL Server 連線 |
| `requests` | >= 2.32.5 | HTTP 請求 (JSON API) |
| `pandas` | >= 3.0.0 | DataFrame 處理 / Excel 匯出 |
| `openpyxl` | >= 3.1.5 | Excel 寫入引擎 |
| `pyyaml` | >= 6.0.3 | YAML 設定檔解析 |
| `oracledb` | >= 3.4.2 | Oracle 資料庫連線 (Thick mode) |

---

## 13. 錯誤處理

| 情境 | 處理方式 |
|------|----------|
| API 無法存取 | 記錄 error log，回傳 `None`，主程式拋出 `RuntimeError` |
| JSON 無效匯率值 | 記錄 error log，該筆略過 |
| SQL Server 連線失敗 | 拋出 `ConnectionError`，由 `main()` 統一發送失敗郵件 |
| SQL Server 寫入失敗 | Rollback + 拋出例外，由 `main()` 統一發送失敗郵件 |
| Oracle 連線失敗 | 拋出 `ConnectionError`，由 `main()` 統一發送失敗郵件 |
| Oracle 寫入失敗 | Rollback + 拋出例外，由 `main()` 統一發送失敗郵件 |
| 郵件發送失敗 | 記錄 error log，回傳 `False` |
| 任何未預期例外 | `main()` catch-all，發送失敗郵件 (含失敗步驟 + 執行狀態) |

### 執行狀態表格

| Step | 說明 | 範例結果 |
|------|------|----------|
| Step 1: API 抓取 | 關務署 JSON API + 原始 JSON 儲存 | 取得 3 種幣別匯率 (20260201~20260210) |
| Step 2: SQL Server (DWNotes) | 三旬匯率寫入 | 60 筆三旬匯率 Upsert 成功 |
| Step 3: Oracle (YSC.SGLP0021_TEMP) | 三旬匯率寫入 | 60 筆三旬匯率寫入成功 |
| Step 4: Excel 報表 | 3 sheets Excel 產生 | 檔案產生完成 |
| Step 5: 報表寄送 | Email 寄送 | Email 寄送中 |
