---
name: sqlserver-query
description: 查詢 SQL Server 資料庫表格內容
user-invocable: true
allowed-tools: Bash(python *)
---

# SQL Server Query Skill

連線至 SQL Server 資料庫 (mssql_python) 並執行 SQL 查詢，以表格格式顯示結果。

## 使用者輸入

$ARGUMENTS

## 可用資料庫

| db_key | Server | Database | 說明 |
|--------|--------|----------|------|
| `dev_db` (預設) | 172.31.3.119 | SERVICE_TEST | 開發/測試用 |
| `database` | 172.31.3.119 | SERVICE_TEST | 正式環境 |
| `database_notes` | 172.31.3.31 | DWNotes | 三旬匯率 (Portal) |
| `database_bi` | 172.31.3.31 | S_ODS | BI 資料庫 |

## 判斷邏輯

根據使用者輸入決定要執行的 SQL：

1. **完整 SQL** — 輸入以 `SELECT`、`WITH`、`INSERT`、`UPDATE`、`DELETE` 開頭，直接執行該 SQL
2. **表格名稱** — 輸入為表格名（如 `NOTES_SIM_GL_DAILY_RATES_INTERFACE`），自動執行：
   ```sql
   SELECT TOP 20 * FROM {表格名}
   ```
3. **匯率查詢捷徑** — 輸入包含「匯率」、「today」、「latest」、「最新」等關鍵字時：
   - **銀行匯率（Daily/Web）**：
     ```sql
     SELECT TOP 50 from_currency, to_currency, from_conversion_date, user_conversion_type, conversion_rate, inverse_conversion_rate, insert_time
     FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
     WHERE from_conversion_date = (SELECT MAX(from_conversion_date) FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE WHERE user_conversion_type = N'SC_銀行匯率')
       AND user_conversion_type = N'SC_銀行匯率'
     ORDER BY from_currency, to_currency
     ```
   - **三旬匯率（Portal）**：指定 db_key 為 `database_notes`，查詢：
     ```sql
     SELECT TOP 50 from_currency, to_currency, from_conversion_date, user_conversion_type, conversion_rate, inverse_conversion_rate, insert_time
     FROM DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
     WHERE from_conversion_date = (SELECT MAX(from_conversion_date) FROM DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE WHERE user_conversion_type = N'SC_三旬匯率')
       AND user_conversion_type = N'SC_三旬匯率'
     ORDER BY from_currency, to_currency
     ```
4. **DESCRIBE / 結構查詢** — 輸入為 `DESC {TABLE}` 或 `DESCRIBE {TABLE}`，自動執行：
   ```sql
   SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
   FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_NAME = '{TABLE}'
   ORDER BY ORDINAL_POSITION
   ```

## 執行方式

使用 Bash 工具執行查詢腳本：

```bash
# 預設連線 (dev_db / SERVICE_TEST)
python .Codex/skills/sqlserver-query/scripts/query.py "SQL_HERE"

# 指定資料庫
python .Codex/skills/sqlserver-query/scripts/query.py "SQL_HERE" database_notes
```

將 `SQL_HERE` 替換為上方判斷後的實際 SQL 語句。第二個參數為可選的 db_key。

## 範例

```bash
# 查詢最新銀行匯率（SERVICE_TEST）
python .Codex/skills/sqlserver-query/scripts/query.py "SELECT TOP 20 * FROM NOTES_SIM_GL_DAILY_RATES_INTERFACE ORDER BY insert_time DESC"

# 查詢三旬匯率（DWNotes）
python .Codex/skills/sqlserver-query/scripts/query.py "SELECT TOP 20 * FROM NOTES_SIM_GL_DAILY_RATES_INTERFACE WHERE user_conversion_type = N'SC_三旬匯率' ORDER BY insert_time DESC" database_notes

# 查詢表格結構
python .Codex/skills/sqlserver-query/scripts/query.py "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'NOTES_SIM_GL_DAILY_RATES_INTERFACE' ORDER BY ORDINAL_POSITION"
```
