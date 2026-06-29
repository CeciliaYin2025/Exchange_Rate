# ChangeLog

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
