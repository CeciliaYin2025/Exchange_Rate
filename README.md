# DailyExchangeRate

匯率自動化系統：從台灣銀行網站與關務署 API 擷取外幣匯率，寫入 SQL Server / Oracle 資料庫，產生 Excel 報表並以 Email 通知。

## 功能概覽

| 腳本 | 資料來源 | 幣別 | 擷取方式 |
|------|----------|------|----------|
| `ImportDailyRate.py` | 央行 (cbc.gov.tw) + 台銀 (rate.bot.com.tw) | CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD | requests + BeautifulSoup；Playwright 截圖 |
| `ImportWebRate.py` | 央行 + 鉅亨網 (invest.cnyes.com) | INR, MXN, TRY | Selenium 抓取；async Playwright 截圖 |
| `ImportPortalRate.py` | 關務署 JSON API (portal.sw.nat.gov.tw) | EUR, JPY, USD | requests (JSON API) |
| `RunAllRates.py` | 合併執行 Daily + Web | 全部銀行匯率幣別 | 統一 Email 通知 |

## 執行流程

### RunAllRates.py（主程式，建議使用）

合併執行 `ImportDailyRate` 和 `ImportWebRate`，統一發送一封 Email 通知：

1. **執行 ImportDailyRate** — 擷取 CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD 匯率
2. **執行 ImportWebRate** — 擷取 INR, MXN, TRY 匯率
3. **查詢 Oracle ERP** — 從 `APPS.GL_DAILY_RATES_V` 取得正式匯率資料
4. **合併 Email 通知** — 包含所有步驟狀態、匯率表格、截圖與 Excel 附件

### 各腳本獨立流程

每支腳本皆可獨立執行，流程如下：

1. **擷取匯率** — 從銀行網站或 API 抓取最新匯率（優先取當天，找不到往前回溯最多 14 天）
2. **計算衍生匯率** — 例如以 USD/TWD 為基準計算 XXX→USD 交叉匯率
3. **寫入 SQL Server** — 透過 MERGE (Upsert) 寫入，雙向（正向 + 反向）
4. **寫入 Oracle TEMP** — 只寫入正向匯率至 `YSC.SGLP0021_TEMP`
5. **執行 SGLP0021_PKG.MAIN** — 將 TEMP 資料轉入 Oracle ERP 正式表（retcode != 0 視為錯誤）
6. **產生 Excel 報表** — 含多個 Sheet (Web Source / SQL Server / Oracle ERP)
7. **網頁截圖** — 保存來源網頁截圖作為佐證 (存於 `data/`)
8. **Email 通知** — 附帶截圖與 Excel 附件；異常時發送錯誤通知信

## 環境需求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 套件管理器
- SQL Server 資料庫
- Oracle 資料庫 + Oracle Instant Client
- Google Chrome (ImportWebRate.py 的 Selenium 需要)
- SMTP 伺服器 (Email 通知)

## 安裝與設定

### 1. 安裝依賴

```bash
uv sync
```

### 2. 安裝 Playwright 瀏覽器

```bash
uv run playwright install chromium
```

### 3. 設定組態檔

複製範本並填入實際連線資訊：

```bash
cp config/db_config.yaml.sample config/db_config.yaml
cp config/email_config.yaml.sample config/email_config.yaml
```

**db_config.yaml** — 資料庫連線設定：

| 區段 | 用途 | 使用腳本 |
|------|------|----------|
| `dev_db` | SQL Server 開發環境 (優先) | ImportDailyRate, ImportWebRate |
| `database` | SQL Server (fallback) | ImportDailyRate, ImportWebRate |
| `database_notes` | SQL Server DWNotes 資料庫 | ImportPortalRate |
| `oracle_db_insert` | Oracle ERP 寫入 | 全部 |
| `oracle_db_query` | Oracle ERP 查詢 | — |

**email_config.yaml** — SMTP 與收件人設定：

| 欄位 | 說明 |
|------|------|
| `smtp_server` / `port` | SMTP 伺服器位址與端口 |
| `sender_email` | 寄件人 Email |
| `receiver_emails_success` | 成功通知收件人 |
| `receiver_emails_failure` | 錯誤通知收件人 (支援多人) |

## 使用方式

### 本機執行

```bash
# 每日銀行匯率 (CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD, INR, MXN, TRY)
uv run python RunAllRates.py

# 三旬匯率 (EUR, JPY, USD — 關務署 API)
uv run python ImportPortalRate.py
```

### Docker 執行

```bash
# 建置映像檔
docker compose build

# 每日銀行匯率（預設）
docker compose run --rm exchange-rate bank

# 三旬匯率
docker compose run --rm exchange-rate portal

# 進入容器 shell (除錯用)
docker compose run --rm exchange-rate shell
```

Docker 容器基於 `mcr.microsoft.com/playwright/python:v1.50.0-noble`，已包含：

- Chromium (Playwright) + Google Chrome (Selenium)
- Oracle Instant Client 23.7
- CJK 字型 (Noto Sans CJK)
- 時區設定為 `Asia/Taipei`

### Airflow DAG 排程

DAG 檔案位於 `dags/exchange_rate_dag_job.py`，每天 17:30 (Asia/Taipei) 執行：

| Task | 命令 | 說明 |
|------|------|------|
| `import_bank_rate` | `docker compose run --rm exchange-rate bank` | RunAllRates.py (每日銀行匯率) |
| `import_portal_rate` | `docker compose run --rm exchange-rate portal` | ImportPortalRate.py (三旬匯率) |

兩個 task 平行執行。

## 專案結構

```
DailyExchangeRate/
├── RunAllRates.py             # 主程式：合併 Daily + Web，統一 Email 通知
├── ImportDailyRate.py         # 每日銀行匯率 (CBC + TCB)
├── ImportWebRate.py           # 每日匯率 (CBC + CNYES, Selenium)
├── ImportPortalRate.py        # 三旬匯率 (關務署 API)
├── main.py                    # Placeholder stub
├── config/
│   ├── db_config.yaml.sample  # 資料庫連線範本
│   ├── email_config.yaml.sample # Email 設定範本
│   └── html/template/         # Email HTML 模板
├── dags/
│   └── exchange_rate_dag_job.py # Airflow DAG 排程定義
├── test/                      # 測試腳本
├── docs/                      # 各腳本規格文件
├── data/                      # 執行產出 (截圖、Excel、JSON)
├── logs/                      # 日誌檔案
├── Dockerfile                 # Docker 映像檔定義
├── docker-compose.yml         # Docker Compose 設定
├── docker-entrypoint.sh       # Docker 進入點腳本
├── pyproject.toml             # Python 專案與依賴定義
└── uv.lock                    # 依賴鎖定檔
```

## 資料庫目標表格

| 資料庫 | 表格 | 匯率類型 |
|--------|------|----------|
| SQL Server (`SERVICE_TEST`) | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` | `SC_銀行匯率` (每日) |
| SQL Server (`DWNotes`) | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` | `SC_三旬匯率` (關務署) |
| Oracle TEMP | `YSC.SGLP0021_TEMP` | `SC_銀行匯率` / `SC_三旬匯率`（只寫正向） |
| Oracle ERP | `APPS.GL_DAILY_RATES_V` | 由 `APPS.SGLP0021_PKG.MAIN` 轉入正式表 |

- SQL Server 匯率以**雙向**寫入 (例如 USD→TWD 與 TWD→USD)
- Oracle TEMP 只寫**正向**匯率，反向由 `SGLP0021_PKG.MAIN` 自動產生
- `conversion_date` 預設為**明日** (today + 1)

## 測試

```bash
uv run pytest test/
```

## 日誌

日誌檔存放於 `logs/` 目錄，命名規則為 `{prefix}_{YYYYMMDD}.log`，同日重複執行時自動加流水號 (`_01`, `_02`, ...)。

同時輸出至控制台與檔案。
