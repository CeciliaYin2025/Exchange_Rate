---
name: oracle-query
description: 查詢 Oracle 資料庫表格內容
user-invocable: true
allowed-tools: Bash(python *)
---

# Oracle Query Skill

連線至 Oracle 資料庫 (oracledb Thick mode) 並執行 SQL 查詢，以表格格式顯示結果。

## 使用者輸入

$ARGUMENTS

## 判斷邏輯

根據使用者輸入決定要執行的 SQL：

1. **完整 SQL** — 輸入以 `SELECT`、`WITH`、`INSERT`、`UPDATE`、`DELETE` 開頭，直接執行該 SQL
2. **表格名稱** — 輸入為表格名（如 `YSC.SGLP0021_TEMP`），自動執行：
   ```sql
   SELECT * FROM {表格名} WHERE ROWNUM <= 20
   ```
3. **DESCRIBE** — 輸入為 `DESC {SCHEMA.TABLE}` 或 `DESCRIBE {SCHEMA.TABLE}`，將 SCHEMA 與 TABLE 拆開後執行：
   ```sql
   SELECT column_name, data_type, data_length, nullable
   FROM all_tab_columns
   WHERE owner = '{SCHEMA}' AND table_name = '{TABLE}'
   ORDER BY column_id
   ```

## 執行方式

使用 Bash 工具執行查詢腳本：

```bash
python .Codex/skills/oracle-query/scripts/query.py "SQL_HERE"
```

將 `SQL_HERE` 替換為上方判斷後的實際 SQL 語句。

## 範例

```bash
# 查詢表格前 20 筆
python .Codex/skills/oracle-query/scripts/query.py "SELECT * FROM YSC.SGLP0021_TEMP WHERE ROWNUM <= 20"

# 查詢表格結構
python .Codex/skills/oracle-query/scripts/query.py "SELECT column_name, data_type, data_length, nullable FROM all_tab_columns WHERE owner='YSC' AND table_name='SGLP0021_TEMP' ORDER BY column_id"

# 自訂查詢
python .Codex/skills/oracle-query/scripts/query.py "SELECT FROM_CURRENCY, TO_CURRENCY, CONVERSION_RATE FROM YSC.SGLP0021_TEMP WHERE ROWNUM <= 5"
```
