# DailyExchangeRate

匯率自動化系統：從台灣銀行網站與關務署 API 擷取外幣匯率，寫入 SQL Server / Oracle 資料庫，產生 Excel 報表並以 Email 通知。

## 功能概覽

| 腳本 | 資料來源 | 幣別 | 擷取方式 |
|------|----------|------|----------|
| `ImportDailyRate.py` | 央行 (cbc.gov.tw) + 台銀 (rate.bot.com.tw) | CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD | requests + BeautifulSoup；Playwright 截圖 |
| `ImportWebRate.py` | 央行 + 鉅亨網 (invest.cnyes.com) | INR, MXN, TRY | Selenium 抓取；async Playwright 截圖 |
| `ImportPortalRate.py` | 關務署 JSON API (portal.sw.nat.gov.tw) | EUR, JPY, USD | requests (JSON API) |

## 執行流程

每支腳本皆為獨立執行，流程如下：

1. **擷取匯率** — 從銀行網站或 API 抓取最新匯率
2. **計算衍生匯率** — 例如以 USD/TWD 為基準計算 USD-based 交叉匯率
3. **寫入資料庫** — 透過 MERGE (Upsert) 寫入 SQL Server，並寫入 Oracle ERP
4. **網頁截圖** — 保存來源網頁截圖作為佐證 (存於 `data/`)
5. **產生 Excel 報表** — 含多個 Sheet (Web Source / SQL Server / Oracle ERP)
6. **Email 通知** — 附帶截圖與 Excel 附件；異常時發送錯誤通知信

## 環境需求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 套件管理器
- SQL Server 資料庫
- Oracle 資料庫 (選用) + Oracle Instant Client
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
# 每日匯率 (CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD vs TWD)
uv run python ImportDailyRate.py

# 月匯率 (INR, MXN, TRY vs TWD) — 需要 Chrome + Playwright
uv run python ImportWebRate.py

# 三旬匯率 (EUR, JPY, USD — 關務署 API)
uv run python ImportPortalRate.py
```

### Docker 執行

```bash
# 建置映像檔
docker compose build

# 執行個別腳本
docker compose run --rm exchange-rate daily     # ImportDailyRate.py
docker compose run --rm exchange-rate web       # ImportWebRate.py
docker compose run --rm exchange-rate portal    # ImportPortalRate.py

# 依序執行全部
docker compose run --rm exchange-rate all

# 進入容器 shell (除錯用)
docker compose run --rm exchange-rate shell
```

Docker 容器基於 `mcr.microsoft.com/playwright/python:v1.50.0-noble`，已包含：

- Chromium (Playwright) + Google Chrome (Selenium)
- Oracle Instant Client 23.7
- CJK 字型 (Noto Sans CJK)
- 時區設定為 `Asia/Taipei`

## 專案結構

```
DailyExchangeRate/
├── ImportDailyRate.py          # 每日銀行匯率 (CBC + TCB)
├── ImportWebRate.py            # 月匯率 (CBC + CNYES, Selenium)
├── ImportPortalRate.py         # 三旬匯率 (關務署 API)
├── main.py                    # Placeholder stub
├── config/
│   ├── db_config.yaml.sample  # 資料庫連線範本
│   ├── email_config.yaml.sample # Email 設定範本
│   └── html/template/         # Email HTML 模板
├── test/                      # 測試腳本
├── docs/                      # 各腳本規格文件
├── data/                      # 執行產出 (截圖、Excel、JSON)
├── logs/                      # 日誌檔案
├── Dockerfile                 # Docker 映像檔定義
├── docker-compose.yml         # Docker Compose 設定
├── docker-entrypoint.sh       # Docker 進入點腳本
├── pyproject.toml              # Python 專案與依賴定義
└── uv.lock                    # 依賴鎖定檔
```

## 資料庫目標表格

| 資料庫 | 表格 | 匯率類型 |
|--------|------|----------|
| SQL Server (`SERVICE_TEST`) | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` | `SC_銀行匯率` (每日 / 月) |
| SQL Server (`DWNotes`) | `NOTES_SIM_GL_DAILY_RATES_INTERFACE` | `SC_三旬匯率` (關務署) |
| Oracle | `YSC.SGLP0021_TEMP` | `SC_銀行匯率` / `SC_三旬匯率` |

匯率一律以**雙向**寫入 (例如 USD→TWD 與 TWD→USD)。`conversion_date` 預設為**明日** (today + 1)。

## 測試

```bash
uv run pytest test/
```

## 日誌

日誌檔存放於 `logs/` 目錄，命名規則為 `{prefix}_{YYYYMMDD}.log`，同日重複執行時自動加流水號 (`_01`, `_02`, ...)。

同時輸出至控制台與檔案。
