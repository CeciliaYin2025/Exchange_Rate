"""Oracle 查詢腳本 - 透過 oracledb Thick mode 連線並執行 SQL"""
import sys
import oracledb
import yaml


def connect():
    with open('config/db_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)['oracle_db_insert']
    try:
        oracledb.init_oracle_client(lib_dir=config.get('client_lib_dir'))
    except oracledb.ProgrammingError:
        pass
    return oracledb.connect(
        user=config['user'],
        password=config['password'],
        dsn=f"{config['host']}:{config['port']}/{config['service_name']}"
    )


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
        print('Usage: python query.py "<SQL>"')
        sys.exit(1)

    sql = ' '.join(sys.argv[1:])
    conn = connect()
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
