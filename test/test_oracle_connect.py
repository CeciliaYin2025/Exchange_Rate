import oracledb
import sqlalchemy
from sqlalchemy import create_engine

oracledb.defaults.thin = True

username = "servicegate"
password = "servicegate"
host = "172.31.3.85"
port = "1531"
service_name = "UAT"

dsn = f"{host}:{port}/{service_name}"

try:
    # 直接用 oracledb 測試
    with oracledb.connect(user=username, password=password, dsn=dsn) as conn:
        print("直接連線成功！")
        print(conn.version)

    # 再測試 SQLAlchemy
    engine = create_engine(f"oracle+oracledb://{username}:{password}@{dsn}")
    with engine.connect() as conn:
        result = conn.execute("SELECT SYSDATE FROM DUAL")
        print("SQLAlchemy 連線成功，SYSDATE:", result.fetchone()[0])

except Exception as e:
    print("連線失敗:", str(e))