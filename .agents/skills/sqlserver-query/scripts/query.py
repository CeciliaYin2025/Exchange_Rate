"""SQL Server 查詢腳本 - 透過 mssql_python 連線並執行 SQL"""
import sys
import mssql_python
import yaml


def connect(db_key=None):
    with open('config/db_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 依指定 key 或預設順序取得連線設定
    if db_key and db_key in config:
        db_config = config[db_key]
    elif 'dev_db' in config:
        db_config = config['dev_db']
    elif 'database' in config:
        db_config = config['database']
    else:
        raise KeyError("找不到可用的資料庫連線設定")

    return mssql_python.connect(
        f"SERVER={db_config['server']};"
        f"DATABASE={db_config['database']};"
        f"UID={db_config['username']};"
        f"PWD={db_config['password']};"
        f"Encrypt=no;"
    ), db_config['server'], db_config['database']


def print_table(cursor):
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    if not rows:
        print(' | '.join(cols))
        print('(0 rows)')
        return
    widths = [
        max(len(str(col)), max(len(str(r[i])) for r in rows))
        for i, col in enumerate(cols)
    ]
    header = ' | '.join(c.ljust(w) for c, w in zip(cols, widths))
    print(header)
    print('-+-'.join('-' * w for w in widths))
    for row in rows:
        print(' | '.join(str(v).ljust(w) for v, w in zip(row, widths)))
    print(f'\n({len(rows)} rows)')


def main():
    if len(sys.argv) < 2:
        print('Usage: python query.py "<SQL>" [db_key]')
        print('  db_key: database | dev_db | database_notes | database_bi')
        sys.exit(1)

    sql = sys.argv[1]
    db_key = sys.argv[2] if len(sys.argv) > 2 else None

    conn, server, database = connect(db_key)
    print(f'Connected: {server} / {database}')
    print()

    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        if cursor.description:
            print_table(cursor)
        else:
            print(f'Rows affected: {cursor.rowcount}')
            conn.commit()
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
