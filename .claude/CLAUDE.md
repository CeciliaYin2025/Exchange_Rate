# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Exchange rate automation system that scrapes currency rates from Taiwanese bank websites, writes them into SQL Server (and optionally Oracle) databases, generates Excel reports, and sends email notifications. Written in Traditional Chinese context for a Taiwanese company (Sercomm).

## Running Scripts

```bash
# Daily rates (CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD vs TWD)
python ImportDailyRate.py

# Monthly rates (INR, MXN, TRY vs TWD) - uses Selenium + Playwright
python ImportWebRate.py

# Portal rates (EUR, JPY, USD, CNY from customs portal JSON API)
python ImportPortalRate.py

```

Python 3.11 managed with `uv`. Virtual environment at `.venv/`.

## Architecture

Each `Import*.py` script is **self-contained** with its own copies of shared utilities (logging setup, DB connection, email sending, config loading). There is no shared module. `main.py` is a placeholder stub.

### Data Flow (per script)

1. **Scrape** rates from bank websites (requests/BeautifulSoup for static pages, Selenium/Playwright for JS-rendered pages)
2. **Calculate** derived rates (e.g., USD-based cross rates from TWD-based rates)
3. **Upsert** into SQL Server using `MERGE` statements → `DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE` (primary), then `SERVICE_TEST` (secondary, bank rates only)
4. **Screenshot** source web pages as proof (saved to `snapshots/`)
5. **Generate** Excel report (saved to `reports/`, deleted after email)
6. **Email** results with inline screenshots and Excel attachment; error emails go to failure recipients

### Key Data Sources

| Script | Source | Currencies |
|--------|--------|-----------|
| `ImportDailyRate.py` | CBC (cbc.gov.tw) + TCB (rate.bot.com.tw) | CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD |
| `ImportWebRate.py` | CBC + CNYES (invest.cnyes.com) via Selenium | INR, MXN, TRY |
| `ImportPortalRate.py` | Customs portal JSON API (portal.sw.nat.gov.tw) | EUR, JPY, USD, CNY |

### Database Target

- **SQL Server**: `NOTES_SIM_GL_DAILY_RATES_INTERFACE` table with MERGE (upsert) pattern
- **Oracle**: `YSC.SGLP0021_TEMP` via oracledb (Thick mode), then `APPS.sglp0021_pkg.main`; SQL Server via mssql-python
- Rate rows are always written in both directions (e.g., USD→TWD and TWD→USD)
- `user_conversion_type` distinguishes rate types: `SC_銀行匯率` (bank rates) vs `SC_三旬匯率` (portal rates)
- `conversion_date` is typically set to **tomorrow** (today + 1 day) in ImportDailyRate.py

## Configuration

- `config/db_config.yaml` — Multiple DB connection profiles (`database`, `dev_db`, `oracle_db_query`, `oracle_db_insert`). Scripts load config by key; `ImportDailyRate.py` and `ImportWebRate.py` try `dev_db` first, then fall back to `database`.
- `config/email_config.yaml` — SMTP server, sender, and separate recipient lists for success vs failure notifications.

## Important Notes

- Hardcoded absolute paths exist throughout (e.g., `D:/Python/DailyExchangeRate/...` for logs, snapshots). These need updating if the deployment location changes.
- `ImportMonthlyRate.py` has dead code after `if __name__` block (lines 669+) that would cause runtime errors.
- Several scripts contain deprecated function copies prefixed with `xx` or `xxx` (e.g., `xxinsert_daily_rate`, `xxxinsert_usd_rate_data`).
- The `run_daily.bat` currently only runs `ImportWebRate.py` (ImportDailyRate.py is commented out).
- Rate fallback: if today's rate is unavailable, scripts look back up to 14 days for the most recent data.
