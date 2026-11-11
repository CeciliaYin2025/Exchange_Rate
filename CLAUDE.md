# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Exchange rate automation system that scrapes currency rates from Taiwanese bank websites, upserts them into SQL Server (and optionally Oracle), generates Excel reports, and sends email notifications. Written in Traditional Chinese context for a Taiwanese company (Sercomm).

## Setup & Running

```bash
# Install dependencies (Python 3.11, managed with uv)
uv sync

# Daily rates (CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD vs TWD)
python ImportDailyRate.py

# Monthly rates (INR, MXN, TRY vs TWD) - uses Selenium + Playwright
python ImportWebRate.py

# Portal rates (EUR, JPY, USD, CNY from customs portal JSON API)
python ImportPortalRate.py

# Legacy monthly rates (older version of ImportWebRate.py, not actively used)
python ImportMonthlyRate.py

# Scheduled execution via batch file (currently only runs ImportWebRate.py)
run_daily.bat
```

There are no formal tests. `test/test_oracle_connect.py` is a manual Oracle connection check (`python test/test_oracle_connect.py`).

## Architecture

Each `Import*.py` script is **self-contained** — logging setup, DB connection, email sending, config loading, and MERGE SQL are all duplicated per script. There is no shared module. `main.py` is a placeholder stub.

### Data Flow (per script)

1. **Scrape** rates from bank websites
2. **Calculate** derived rates (e.g., USD-based cross rates from TWD-based rates)
3. **Upsert** into SQL Server via `MERGE` statements
4. **Screenshot** source web pages as proof (saved to `snapshots/`)
5. **Generate** Excel report (saved to `reports/`, deleted after email)
6. **Email** results with inline screenshots and Excel attachment; error emails go to failure recipients

### Data Sources

| Script | Source | Currencies | Scraping Method |
|--------|--------|-----------|-----------------|
| `ImportDailyRate.py` | CBC (cbc.gov.tw) + TCB (rate.bot.com.tw) | CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD | requests + CSV API; Playwright for screenshots（需繞過 Akamai bot 保護） |
| `ImportWebRate.py` | CBC + CNYES (invest.cnyes.com) | INR, MXN, TRY | Selenium for scraping; async Playwright for screenshots |
| `ImportPortalRate.py` | Customs portal JSON API (portal.sw.nat.gov.tw) | EUR, JPY, USD, CNY | requests (JSON API) |

### Database Patterns

- **SQL Server target table**: `NOTES_SIM_GL_DAILY_RATES_INTERFACE` (in `SERVICE_TEST` or `DWNotes` database depending on script)
- **Oracle** (optional, ImportDailyRate/ImportWebRate): `YSC.SGLP0021_TEMP`
- Rates are always stored **bidirectionally** (e.g., USD→TWD and TWD→USD with reciprocal rate)
- `user_conversion_type` distinguishes: `SC_銀行匯率` (bank rates) vs `SC_三旬匯率` (portal rates)
- `conversion_date` is typically set to **tomorrow** (today + 1 day) in ImportDailyRate.py
- Rate fallback: if today's rate is unavailable, scripts look back up to 14 days

**DB connection libraries differ by script:**
- `ImportDailyRate.py` — SQLAlchemy + **pymssql** for SQL Server; oracledb **Thick mode** for Oracle
- `ImportWebRate.py` — **pyodbc** for SQL Server; oracledb **Thin mode** for Oracle
- `ImportPortalRate.py` — **mssql_python** for SQL Server (DWNotes); oracledb **Thick mode** for Oracle (insert: servicegate, query GL_DAILY_RATES_V: ypr)
- Config priority: ImportDailyRate and ImportWebRate try `dev_db` first, fall back to `database`; ImportPortalRate uses `database` directly

### MERGE SQL Variants

Two patterns in the codebase:
1. **Positional placeholders** (`?`) with tuple params — used by ImportDailyRate.py, ImportPortalRate.py
2. **Named parameters** (`:param_name`) via SQLAlchemy `text()` — used by ImportWebRate.py

## Configuration

- `config/db_config.yaml` — DB connection profiles: `database`, `dev_db`, `database_bi`, `database_notes`, `oracle_db_query`, `oracle_db_insert`
- `config/email_config.yaml` — SMTP server, sender, and separate recipient lists for success vs failure notifications

## Known Issues & Technical Debt

- **`ImportDailyRate_bk.py` 的 TCB 抓取已失效**：舊版使用 `https://rate.bot.com.tw/xrt/all/{date}` HTML 頁面解析 `<table>`，但 TCB 網站已改版，該頁面不再有 `<table>` 標籤。現版 `ImportDailyRate.py` 已改用 CSV API（`xrt/flcsv/0/{date}`），不受影響。`_bk.py` 僅供參考，勿直接使用。
- **TCB 截圖需繞過 Akamai bot 保護**：`snapshot_page()` 對 `rate.bot.com.tw` 截圖時，headless Chromium 會被 Akamai 攔截並顯示 30 秒挑戰頁。已透過設定真實 Chrome User-Agent、`wait_until="domcontentloaded"` 及 `wait_for_selector("table", timeout=60000)` 解決。若截圖再次出現 Akamai 攔截頁，表示 bot 偵測規則更新，可考慮改用 `playwright-stealth`。
- **Hardcoded absolute paths** in ImportWebRate.py, ImportMonthlyRate.py, and run_daily.bat reference `D:/Python/DailyExchangeRate/`. ImportDailyRate.py and ImportPortalRate.py use relative paths correctly.
- **Deprecated functions** prefixed with `xx` or `xxx` (e.g., `xxinsert_daily_rate`) remain in ImportDailyRate.py.
- **ImportMonthlyRate.py** has dead code after the `if __name__` block (~line 669+) that would cause runtime errors. This is a legacy script; ImportWebRate.py is its replacement.
- **Oracle connection in ImportWebRate.py** has hardcoded credentials in `get_oracle_engine()` that may be stale.
- **No .gitignore entries** for `config/` (credentials in YAML), `logs/`, `snapshots/`, or `reports/`.
- `run_daily.bat` points to a separate venv at `D:\Python\venv\` rather than the project `.venv/`.
