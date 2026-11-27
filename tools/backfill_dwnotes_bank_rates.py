"""回補 DWNotes 的銀行匯率（SC_銀行匯率）。

來源：
  oracle      — Oracle PROD APPS.GL_DAILY_RATES_V（ERP 正式資料，建議）
  servicetest — SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE

預設只預覽（不寫入），加 --apply 才寫入 DWNotes。寫入用 MERGE，重跑不會產生重複資料，
比對 key 含 user_conversion_type，不會覆蓋 SC_三旬匯率。

用法（在專案根目錄執行）：
  python tools/backfill_dwnotes_bank_rates.py --source oracle --from 2025-12-04 --to 2026-11-27
  python tools/backfill_dwnotes_bank_rates.py --source oracle --from 2025-12-04 --to 2026-11-27 --apply
"""
import argparse
import os
from collections import Counter
from datetime import date

import mssql_python
import oracledb
import yaml

DWNOTES_MERGE_SQL = """
    MERGE DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE AS t
    USING (SELECT ? AS from_currency, ? AS to_currency,
                  ? AS from_conversion_date, ? AS to_conversion_date,
                  ? AS user_conversion_type,
                  ? AS conversion_rate, ? AS inverse_conversion_rate) AS s
    ON  t.from_currency = s.from_currency
    AND t.to_currency = s.to_currency
    AND t.from_conversion_date = s.from_conversion_date
    AND t.user_conversion_type = s.user_conversion_type
    WHEN MATCHED THEN
        UPDATE SET to_conversion_date = s.to_conversion_date,
                   conversion_rate = ROUND(s.conversion_rate, 4),
                   inverse_conversion_rate = ROUND(s.inverse_conversion_rate, 4),
                   user_id = '1175',
                   launch_rate_change = 'Y',
                   insert_time = GETDATE()
    WHEN NOT MATCHED THEN
        INSERT (from_currency, to_currency, from_conversion_date, to_conversion_date,
                user_conversion_type, conversion_rate, mode_flag,
                inverse_conversion_rate, user_id, launch_rate_change,
                batch_number, insert_time)
        VALUES (s.from_currency, s.to_currency, s.from_conversion_date, s.to_conversion_date,
                s.user_conversion_type, ROUND(s.conversion_rate, 4), 'I',
                ROUND(s.inverse_conversion_rate, 4), '1175', 'Y',
                NULL, GETDATE());
"""


def _config():
    with open('config/db_config.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _connect_sql(key):
    cfg = _config()[key]
    return mssql_python.connect(
        f"SERVER={cfg['server']};DATABASE={cfg['database']};"
        f"UID={cfg['username']};PWD={cfg['password']};Encrypt=no;"
    )


def read_service_test_bank_rates(date_from, date_to):
    conn = _connect_sql('dev_db')
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT from_currency, to_currency, from_conversion_date, to_conversion_date,
                   conversion_rate, inverse_conversion_rate
            FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
            WHERE user_conversion_type = N'SC_銀行匯率'
              AND from_conversion_date BETWEEN ? AND ?
            ORDER BY from_conversion_date, from_currency, to_currency
        """, (date_from, date_to))
        return [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()


def read_oracle_prod_bank_rates(date_from, date_to):
    cfg = _config()['oracle_query_prod']
    lib_dir = os.environ.get('ORACLE_CLIENT_LIB_DIR') or cfg.get('client_lib_dir')
    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
    except oracledb.ProgrammingError:
        pass
    conn = oracledb.connect(user=cfg['user'], password=cfg['password'],
                            dsn=f"{cfg['host']}:{cfg['port']}/{cfg['service_name']}")
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT FROM_CURRENCY, TO_CURRENCY, TRUNC(CONVERSION_DATE), CONVERSION_RATE
            FROM APPS.GL_DAILY_RATES_V
            WHERE USER_CONVERSION_TYPE = 'SC_銀行匯率'
              AND CONVERSION_DATE BETWEEN :1 AND :2
            ORDER BY 3, 1, 2
        """, [date_from, date_to])
        rows = []
        for f_cur, t_cur, conv_dt, rate in cur:
            d = conv_dt.date()
            rate = float(rate)
            rows.append((f_cur, t_cur, d, d, rate, 1 / rate if rate else 0))
        return rows
    finally:
        conn.close()


def merge_rows_to_dwnotes(rows):
    """rows: list of (from_currency, to_currency, from_date, to_date, rate, inverse_rate)。"""
    conn = _connect_sql('database_notes')
    cur = conn.cursor()
    try:
        for f_cur, t_cur, f_date, t_date, rate, inv in rows:
            cur.execute(DWNOTES_MERGE_SQL, (f_cur, t_cur, f_date, t_date, 'SC_銀行匯率', rate, inv))
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--source', choices=['oracle', 'servicetest'], required=True)
    p.add_argument('--from', dest='date_from', type=date.fromisoformat, required=True)
    p.add_argument('--to', dest='date_to', type=date.fromisoformat, required=True)
    p.add_argument('--apply', action='store_true', help='實際寫入 DWNotes（預設只預覽）')
    args = p.parse_args()

    if args.source == 'servicetest':
        rows = read_service_test_bank_rates(args.date_from, args.date_to)
    else:
        rows = read_oracle_prod_bank_rates(args.date_from, args.date_to)

    per_day = Counter(str(r[2]) for r in rows)
    print(f"來源 {args.source}，{args.date_from} ~ {args.date_to}：{len(rows)} 筆，{len(per_day)} 天")
    if per_day:
        print("每天筆數分布（筆數: 天數）:", dict(sorted(Counter(per_day.values()).items())))
        print("前 3 筆:", rows[:3])

    if not args.apply:
        print("預覽模式，未寫入。加 --apply 才會寫入 DWNotes。")
        return
    print(f"已 MERGE {merge_rows_to_dwnotes(rows)} 筆到 DWNotes。")


if __name__ == '__main__':
    main()
