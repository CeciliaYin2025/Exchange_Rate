# ChangeLog

## [2026-11-27]

### 新增：銀行匯率寫入 DWNotes（主）與 SERVICE_TEST（輔）

**需求：** 銀行匯率（`SC_銀行匯率`）以 `DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE`（172.31.3.31）為主、`SERVICE_TEST`（172.31.3.119）為輔，兩邊都寫入 17:30 抓到的匯率，並回補 DWNotes 缺少的期間。

**修正（`ImportDailyRate.py` / `ImportWebRate.py`）：**

- 新增 `connect_to_db_dwnotes()`（讀 `db_config.yaml` 的 `database_notes`）
- `_execute_merge_rate()` 新增 `db` 參數；寫 DWNotes 時比對 key 一律加上 `user_conversion_type`，不會覆蓋同日期的 `SC_三旬匯率`
- 寫入順序：先 DWNotes（主，失敗即中斷，該步驟失敗、寄錯誤信、Airflow 重試），再 SERVICE_TEST（輔，失敗只記 log WARNING「SERVICE_TEST（輔）寫入失敗」，不中斷後續 Oracle 寫入）
- Email 步驟名稱維持原樣，不顯示 DWNotes / SERVICE_TEST 等內部資料庫名稱（User 不需要知道）
- `ImportWebRate.prepare_and_merge_sql_server()` 回傳值改為 `(rows, service_test_error)`
- Excel 報表的 SQL Server 工作表改從 DWNotes 讀取
- 每天寫入筆數：Daily 26 筆（TWD 16 + USD 10）、Web 6 筆，合計 32 筆

**新增（`tools/backfill_dwnotes_bank_rates.py`）：** 回補工具，來源可選 `oracle`（PROD `GL_DAILY_RATES_V`）或 `servicetest`，預設只預覽，加 `--apply` 才寫入。

**測試（`test/`）：** 更新 `TestInsertDailyRate`、`TestInsertUsdRates`、`TestPrepareAndMergeSqlServer`（驗證先寫 DWNotes、SERVICE_TEST 失敗不中斷）及報表測試的連線 mock。全套測試失敗數與修改前相同（27 個既有失敗，與本次無關）。

**回補（2026-11-27 執行）：** DWNotes 原有銀行匯率只到 2025-12-03（由其他系統於每晚 23:5x 寫入，之後停止）。以 Oracle PROD GL 回補 2025-12-04 ~ 2026-11-27，共 359 天 × 32 筆 = 11,488 筆；`SC_三旬匯率` 270 筆不受影響。

- 回補來源選 GL 而非 SERVICE_TEST：SERVICE_TEST 從 2026-02-02 才有資料，且 2~4 月有已知錯誤值（TRY 正反向寫反、假日 INR/MXN）與 8 天缺漏；GL 為 ERP 正式資料
- 已知差異：每日寫入的 `TWD→INR` 與 GL 約差 0.0003（GL 由四捨五入後的 INR→TWD 反算），其他幣別一致

**測試（local，2026-11-27 17:18）：** Oracle 僅寫 TEST2（retcode=0，Daily 13 筆、Web 3 筆），DWNotes 與 SERVICE_TEST 各寫 32 筆（2026-11-28），`SC_三旬匯率` 不受影響，Email 僅寄 `cecilia_yin@sercomm.com`，exit code 0。

---

## [2026-11-11]（2026-11-10 開發測試，2026-11-11 ERP 確認後上版 PROD）

### 新增：`ImportPortalRate.py` 三旬匯率加入 CNY

**需求：** 三旬匯率（`SC_三旬匯率`）新增 CNY→TWD。

**修正（`ImportPortalRate.py`）：**

- 新增常數 `PORTAL_CURRENCIES = ['EUR', 'JPY', 'USD', 'CNY']`，`fetch_json()` 改用此清單篩選幣別（原為寫死 `['EUR', 'JPY', 'USD']`）
- 寫入筆數變化：SQL Server DWNotes 每期 6 → 8 筆（雙向）；Oracle `YSC.SGLP0021_TEMP` 每期 3 → 4 筆（單向，TWD→CNY 由 `sglp0021_pkg` 產生）

### 修正：三旬匯率 Email 表格改為 DISTINCT

**問題：** Email 的 Oracle ERP 匯率表從 `GL_DAILY_RATES_V` 查詢，該 view 每天一筆，每幣別每方向約 10 筆重複資料；新增 CNY 後表格會更長。

**修正：** `query_oracle_portal_rates()` 改為 `SELECT DISTINCT FROM_CURRENCY, TO_CURRENCY, USER_CONVERSION_TYPE, CONVERSION_RATE`（去掉 `CONVERSION_DATE`），每幣別每方向一筆。Excel「Oracle ERP」sheet 仍保留每日明細。

### Bug Fix：`connect_to_db_oracle()` 查詢使用錯誤帳號

**問題：** 在 TEST2 查詢 `APPS.GL_DAILY_RATES_V` 出現 `ORA-00942: table or view does not exist`，導致 Excel「Oracle ERP」sheet 空白、Email 表格退回備援資料。

**根本原因：** `connect_to_db_oracle()` 呼叫 `load_oracle_config()` 未指定 role，預設使用 `insert`（servicegate）帳號；servicegate 在 TEST2 無此 view 權限。

**修正：** 改為 `load_oracle_config(..., role='query')`，使用 ypr 查詢帳號。

### 測試紀錄（local，2026-11-10，期間 20261111~20261120）

- Oracle 僅寫入 TEST2：4 筆，`sglp0021_pkg.main` retcode=0、Processed records: 4；`GL_DAILY_RATES_V` 共 80 筆，DISTINCT 後 8 筆
- SQL Server DWNotes（正式 DW）：已寫入 8 筆，**CNY↔TWD 已存在於 DWNotes**
- Email 僅寄 `cecilia_yin@sercomm.com`
- 測試後 `config/db_config.yaml` 已還原（`oracle_active_env: uat`、`oracle_insert_targets: [uat, test2, prod]`）

**上版：** 2026-11-11 ERP 確認新增 CNY 三旬匯率對 ERP 無影響，上版 PROD。

### 設定變更：PROD 主機查詢環境改為 PROD

**問題：** PROD 主機 `config/db_config.yaml` 的 `oracle_active_env` 為 `uat`，三支程式的 Email/Excel 報表皆查 UAT 的 `GL_DAILY_RATES_V`，而非 PROD 實際資料（UAT 含測試用未來日期資料，與 PROD 不一致）。

**修正（PROD 主機 `config/db_config.yaml`，不在 repo 內）：** `oracle_active_env: "uat"` → `"prod"`。已先驗證 PROD ypr 帳號可讀 `APPS.GL_DAILY_RATES_V`。寫入目標 `oracle_insert_targets` 不變（uat / test2 / prod）。原設定備份於 `/Projects/backup/db_config.yaml.20261111_033615`。

---

## [2026-06-30]

### Bug Fix：`ImportDailyRate.py` TCB 截圖被 Akamai bot 保護攔截

**問題：** `snapshot_page()` 截到 Akamai 挑戰頁（"Processing your request..." 倒數 30 秒），而非實際匯率表。

**根本原因：** TCB 網站（rate.bot.com.tw）部署了 Akamai bot 保護，headless Chromium 預設無 User-Agent 且不等待 JS 挑戰通過，直接截到攔截頁。

**修正（`ImportDailyRate.py`）：**

- `browser.new_context()` 加入真實 Chrome User-Agent（`Mozilla/5.0 ... Chrome/125`）及 `locale="zh-TW"`
- `wait_until` 從 `"networkidle"` 改為 `"domcontentloaded"`（Akamai 挑戰頁 network 永遠不 idle）
- 加入 `page.wait_for_selector("table", timeout=60000)`，等匯率表實際出現後再截圖

---

## [2026-04-30]

### Bug Fix：`ImportDailyRate.py` / `ImportWebRate.py` Oracle INSERT 遺漏 `ROUND(:6, 4)`

**問題：** 2026-04-22 起 Oracle PROD 的 INR / MXN / TRY / 各銀行匯率小數位超過 4 位（例如 `0.3343624346`），不符合精度規範。

**根本原因：** 2026-04-16 重構多環境 Oracle 支援時，`ImportDailyRate.py` 與 `ImportWebRate.py` 的 INSERT SQL 被改寫，`ROUND(:6, 4)` 被不小心移除。`ImportPortalRate.py` 未受影響（仍保有 `ROUND`）。

**修正：**

- `ImportDailyRate.py:772` — `:6` → `ROUND(:6, 4)`
- `ImportWebRate.py:629` — `:6` → `ROUND(:6, 4)`

---

## [2026-04-21]

### 新增：Oracle 寫入目標加入 PROD 環境

**需求：** 匯率寫入 Oracle 時，除現有的 UAT 與 TEST2，同步寫入 PROD 環境。

**修正（`config/db_config.yaml`）：**

- `oracle_insert_targets` 加入 `"prod"`，現為 `[uat, test2, prod]`
- `oracle_insert_prod` 連線設定（servicegate / 172.31.3.210:1521/PROD）先前版本已存在，本次無需新增

三支腳本（`ImportDailyRate.py`、`ImportWebRate.py`、`ImportPortalRate.py`）的 `_get_oracle_insert_connections()` 動態讀取此清單，**不需修改程式碼**。

### 修正：移除 Email 主旨前綴「(測試信)」

**修正（`ImportDailyRate.py` / `ImportWebRate.py` / `ImportPortalRate.py` / `RunAllRates.py`）：**

- 各腳本 Email 主旨前綴 `(測試信)` 全數移除，改為直接以【匯率報告】或【三旬匯率報告】開頭

---

## [2026-04-16]

### 新增：Oracle 多環境設定與同步寫入

**需求：** 查詢帳號（ypr）和寫入帳號（servicegate）各需支援 UAT、TEST2、PROD 三個環境；insert 匯率時需同步寫入 UAT 和 TEST2。

**修正（`config/db_config.yaml`）：**

- 移除舊的 `oracle_db_query` / `oracle_db_insert` / `oracle_db_prod` 區段
- 新增 6 個環境區段：`oracle_query_uat/test2/prod`、`oracle_insert_uat/test2/prod`
- 新增 `oracle_active_env: "uat"` — 查詢環境切換只改此一行
- 新增 `oracle_insert_targets: [uat, test2]` — 寫入目標環境清單，可同時寫入多個環境

**修正（`ImportDailyRate.py` / `ImportWebRate.py` / `ImportPortalRate.py`）：**

- `load_oracle_config(file_path, role='insert')` — 新增 `role` 參數，動態組合 key（`oracle_{role}_{env}`），預設行為不變
- 各腳本新增 `_get_oracle_insert_connections()` — 依 `oracle_insert_targets` 建立所有寫入目標連線清單
- `insert_to_oracle()` / `insert_portal_rates_oracle()` 改為對每個目標連線依序執行：delete → insert → `sglp0021_pkg.main`，任一環境失敗收集錯誤，全部完成後統一 raise

**修正（`RunAllRates.py`）：**

- `_connect_to_db_oracle()` 改為讀取 `oracle_active_env` 動態決定查詢連線

**修正（`.claude/skills/oracle-query/scripts/query.py`）：**

- 改用 `oracle_query_{env}` 連線（ypr 帳號），不再使用 servicegate 帳號做查詢

### 新增：`SKIP_EMAIL` 環境變數（測試用）

**修正（`ImportDailyRate.py` / `ImportPortalRate.py`）：** `send_email_notification()` 加入 `SKIP_EMAIL` env var 判斷，設定時略過發信，方便本機測試。`ImportWebRate.py` 同步加入。正式排程不受影響。

```bash
SKIP_EMAIL=1 uv run python ImportWebRate.py
```

---

## [2026-04-10]

### 修正：Airflow DAG 改為循序執行，避免 Oracle sglp0021_pkg.main 衝突

**問題：** `import_bank_rate` 與 `import_portal_rate` 平行執行，兩者皆會呼叫 `APPS.sglp0021_pkg.main`，同時觸發時 Oracle ERP 鎖定其中一筆並回傳 retcode=1 錯誤。

**Oracle Admin 說明：** 此功能一次僅能處理一筆作業，已設定為單一執行模式，建議拉開執行時間間隔。

**修正（`dags/exchange_rate_dag_job.py`）：**

```python
# 修正前（平行）
[run_bank, run_portal]

# 修正後（循序）
run_bank >> run_portal
```

銀行匯率完成後才啟動三旬匯率，確保 `sglp0021_pkg.main` 不會同時執行。

---

## [2026-04-09]

### Bug Fix：`ImportWebRate.py` chromedriver 失聯時 Selenium 重試 14 次浪費 90 分鐘

**問題：** 2026-04-08 17:30 啟動的 container，chromedriver 在 17:37 失聯後，`fetch_rate_with_fallback` 仍對 MXN/INR/TRY 各重試 15 次（14天回溯），每次等待 120 秒 timeout，共耗時 ~103 分鐘，直到 19:13 才結束並發出錯誤信。

Airflow `cmd_timeout=600s` 超時後已啟動 retry container（17:52 成功寫入），但第一個 zombie container 繼續在背景執行，造成 19:13 的重複錯誤通知。

**修正（`ImportWebRate.py`）：**

- `fetch_currency_rate_selenium`：捕捉到 `HTTPConnectionPool` 錯誤時 re-raise（chromedriver 失聯屬基礎設施問題，非日期缺資料，不應重試）
- `fetch_rate_with_fallback`：加入 try/except，chromedriver 失聯時記錄 log 並立即中止，不繼續日期回溯

---

## [2026-04-07]

### Bug Fix：`ImportWebRate.py` 假日模式 INR / MXN 匯率計算錯誤

**問題：** 假日（週末、連假）執行時，寫入 SQL Server 的 INR→TWD 與 MXN→TWD 匯率異常偏高（約 93–95），正常值應約 0.34。

**受影響日期：**

| CREATION_DATE | CONVERSION_DATE | 異常值 | 說明 |
|---------------|-----------------|--------|------|
| 2026-03-28 | 2026-03-29 | 94.712 | 週六 |
| 2026-04-03 | 2026-04-04 | 93.12 | 清明補假 |
| 2026-04-05 | 2026-04-06 | 93.12 | 清明節（週日） |

**根本原因：** `fetch_holiday_rates_from_oracle()` 查 Oracle `GL_DAILY_RATES_V` 時，`FROM_CURRENCY='INR', TO_CURRENCY='TWD'`，回傳的是 **INR→TWD 直接匯率**（約 0.336）。但 `all_rates` 組裝卻用 `(usd_rate, holiday_rates['INR'])` 傳入 `calculate_monthly_rates`，該函數以 `rate_1 / rate_2` 計算，等同 `32 / 0.336 ≈ 95`，把直接匯率當成 USD/INR 交叉匯率使用，造成單位錯誤。

TRY 因為用 `(holiday_rates['TRY'], 1)` 所以不受影響；INR 和 MXN 用了錯誤的格式。

**修正（`ImportWebRate.py`）：**

```python
# 修正前
all_rates = {
    'INR': (usd_rate, holiday_rates['INR']),
    'MXN': (usd_rate, holiday_rates['MXN']),
    'TRY': (holiday_rates['TRY'], 1),
}

# 修正後
all_rates = {
    'INR': (holiday_rates['INR'], 1),  # Oracle 直接回傳 INR→TWD，同 TRY 模式
    'MXN': (holiday_rates['MXN'], 1),  # Oracle 直接回傳 MXN→TWD，同 TRY 模式
    'TRY': (holiday_rates['TRY'], 1),
}
```

同步移除計算錯誤的中間變數 `inr_rate = usd_rate / holiday_rates['INR']`（該值只用於 log，且算出的是錯誤的 USD/INR 格式）。

---

### 已知問題：`ImportPortalRate.py` 失敗時 Airflow 不重試

**問題：** `sglp0021_pkg.main` 回傳 `retcode != 0` 時，`main()` 會發錯誤 Email，但最終以 exit code 0 正常結束，Airflow 誤判為成功，不觸發重試。

**根本原因：** `except` 區塊捕捉例外後，未呼叫 `sys.exit(1)`。

**修正（`ImportPortalRate.py`）：** 在 `except` 區塊發完錯誤信後加入 `sys.exit(1)`，讓 Airflow 收到非零 exit code 並觸發重試。

---

## [Unreleased] — 2026-03-19

### Bug Fix：Airflow 無法自動重試的根本原因修正

**問題：** `import_bank_rate` task 因 MXN / INR 抓取失敗時，Airflow 不會自動重試，需手動介入。

**根本原因：** `RunAllRates.py` 執行失敗時仍以 exit code 0 結束，Docker container 回傳 0 給 Airflow，Airflow 誤判為成功，不觸發重試。

**修正：**

- **`RunAllRates.py`** — 執行結束時若 `daily_result` 或 `web_result` 任一失敗，呼叫 `sys.exit(1)`，讓 Docker container 以非零 exit code 結束
- **`ImportWebRate.py`** — 獨立執行（`__main__`）時，失敗後加上 `sys.exit(1)`（直接執行時行為一致）

### DAG 調整：`import_bank_rate` 自動重試

- **`dags/exchange_rate_dag_job.py`**
  - `default_args.retries` 從 `1` 改為 `0`（portal task 不需重試）
  - `import_bank_rate` task 明確設定 `retries=1`、`retry_delay=5 minutes`（失敗後 5 分鐘自動重試一次）
  - 修正 `PROJECT_DIR` 路徑：`/home/robert/Projects/Exchange_Rate-main` → `/Projects/Exchange_Rate`

### 文件更新

- **`README.md`** — Airflow DAG 排程說明加入重試設定欄位、背景說明與觸發條件

---

## [2026-03-17] — commit `fba10de`

### 新增：舊檔案自動清除機制

所有三支腳本（`ImportDailyRate.py`、`ImportWebRate.py`、`ImportPortalRate.py`）均加入啟動時自動清除：

- **`_cleanup_old_logs(log_dir, days=14)`** — 刪除 `logs/` 中超過 14 天的 `.log` 檔
- **`_cleanup_old_data(data_dir, days=14)`** — 刪除 `data/` 下超過 14 天的日期子目錄

### 精度統一：匯率小數位從 6 位改為 4 位

- `ImportDailyRate.py`、`ImportWebRate.py`、`ImportPortalRate.py` 所有匯率計算、Excel 輸出、Email 表格一律改為 4 位小數
- SQL Server MERGE 語法加入 `ROUND(?, 4)`，確保寫入資料庫的值也是 4 位精度
- Oracle INSERT 語法加入 `ROUND(:6, 4)`

### Bug Fix：`ImportPortalRate.py` — `oracle_inserted_data` 為 None 時會 crash

- 呼叫 `len(oracle_inserted_data)` 前加入 None 判斷，避免 Oracle 寫入未執行時程式報錯

### ImportWebRate.py：截圖改版

- 截圖只保留匯率**表格**，移除頁面標題、趨勢圖表（`canvas`、`svg`、`highcharts` 等）及廣告
- 新增 `WebDriverWait` + `EC` 引入（Selenium explicit wait 機制）

---

## [2026-03-06] — commit `4e33fa0` / `15d656d` / `7d5c422`

### Docker 整合完成

- `docker-entrypoint.sh` 改為以 `RunAllRates.py` 作為 `bank` 指令的進入點（合併 Daily + Web）
- `Dockerfile` 修正：加入 `libaio` symlink、`.venv` 加入 `PATH`

### Bug Fix：匯率計算錯誤

- 修正 USD 交叉匯率方向錯誤
- 修正 TRY 匯率計算邏輯

### Oracle 整合改善

- Oracle 連線穩定性改善
- Email 與 Excel 報表格式優化

---

## [2026-02-16] — commit `4b64611`

### 初始 Docker 化

- 新增 `Dockerfile`、`docker-compose.yml`、`docker-entrypoint.sh`
- 新增 `README.md`
