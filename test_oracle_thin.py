#!/usr/bin/env python3
"""Test Oracle Thin mode connection."""
import oracledb
import yaml

with open('config/db_config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['oracle_db_insert']
dsn = f"{db_config['host']}:{db_config['port']}/{db_config['service_name']}"
print(f'Testing connection to: {dsn}')

try:
    conn = oracledb.connect(
        user=db_config['user'],
        password=db_config['password'],
        dsn=dsn
    )
    print('✓ Oracle Thin mode connection successful!')
    conn.close()
except Exception as e:
    print(f'✗ Connection failed: {e}')
