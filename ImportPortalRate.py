# === Imports ===
import json
import logging
import os
import smtplib
import sys
import warnings
from datetime import datetime, timedelta
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from string import Template

import mssql_python
import oracledb
import pandas as pd
import requests
import yaml


# === Constants ===
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'config', 'html', 'template')

RUN_LOGS = []

# 三旬匯率要處理的幣別（對 TWD）
PORTAL_CURRENCIES = ['EUR', 'JPY', 'USD', 'CNY']

# === Template Loading ===
def _load_template(name):
    """從 config/html/template/ 載入 HTML template 檔案，回傳 string.Template 物件。"""
    path = os.path.join(TEMPLATE_DIR, name)
    with open(path, 'r', encoding='utf-8') as f:
        return Template(f.read())

# === Logging Setup ===
warnings.filterwarnings("ignore", module='urllib3')

logger = None

def _get_daily_log_filepath(log_dir, prefix='currency_portal'):
    """
    產生日期命名的 log 檔路徑。
    規則：{prefix}_{YYYYMMDD}.log，若已存在則加流水號 _01, _02 ...
    """
    os.makedirs(log_dir, exist_ok=True)
    date_str = datetime.now().strftime('%Y%m%d')
    base_path = os.path.join(log_dir, f"{prefix}_{date_str}.log")

    if not os.path.exists(base_path):
        return base_path

    seq = 1
    while True:
        seq_path = os.path.join(log_dir, f"{prefix}_{date_str}_{seq:02d}.log")
        if not os.path.exists(seq_path):
            return seq_path
        seq += 1

def _cleanup_old_logs(log_dir, days=14):
    """刪除 log_dir 中超過 days 天的 .log 檔案。"""
    cutoff = datetime.now().timestamp() - days * 86400
    for fname in os.listdir(log_dir):
        if not fname.endswith('.log'):
            continue
        fpath = os.path.join(log_dir, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            try:
                os.remove(fpath)
            except OSError:
                pass

def _cleanup_old_data(data_dir, days=14):
    """刪除 data_dir 中超過 days 天的日期子目錄（含其內容）。"""
    if not os.path.isdir(data_dir):
        return
    cutoff = datetime.now().timestamp() - days * 86400
    for dname in os.listdir(data_dir):
        dpath = os.path.join(data_dir, dname)
        if os.path.isdir(dpath) and os.path.getmtime(dpath) < cutoff:
            try:
                import shutil
                shutil.rmtree(dpath)
            except OSError:
                pass

def setup_logging(log_dir='./logs'):
    """
    設定日誌系統，將輸出同時寫入控制台和日誌檔案。
    日誌檔名依日期產生，同日重複執行時自動加流水號。
    啟動時自動清除超過 14 天的舊 log 檔。
    """
    global logger

    logger = logging.getLogger('CurrencyPortalLogger')
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        _cleanup_old_logs(log_dir)
        _cleanup_old_data('./data/portal')
        log_filepath = _get_daily_log_filepath(log_dir)
        file_handler = logging.FileHandler(log_filepath, encoding='utf-8')
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(module)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger

def log_and_collect(message, level="info"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"{timestamp} - {message}"

    RUN_LOGS.append(log_line)

    if level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)

# === Config Loading ===
def load_db_config(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if "database_notes" in config:
        return config["database_notes"]
    elif "database" in config:
        return config["database"]
    else:
        available_keys = list(config.keys()) if config else "Empty File"
        raise KeyError(f"設定檔中找不到 'database_notes' 或 'database'。目前擁有的標籤為: {available_keys}")

def load_email_config(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)
    return config['email']

def load_oracle_config(file_path, role='insert'):
    """讀取 Oracle 連線設定。role='insert'(servicegate) 或 'query'(ypr)。
    環境由 oracle_active_env 決定：uat | test2 | prod。"""
    with open(file_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    env = config.get('oracle_active_env', 'uat')
    key = f'oracle_{role}_{env}'
    if key not in config:
        raise KeyError(f"Oracle config key '{key}' not found (oracle_active_env={env})")
    return config[key]

# === Database Connections ===
def connect_to_db_sql():
    db_config = load_db_config('config/db_config.yaml')
    try:
        conn = mssql_python.connect(
            f"SERVER={db_config['server']};"
            f"DATABASE={db_config['database']};"
            f"UID={db_config['username']};"
            f"PWD={db_config['password']};"
            f"Encrypt=no;"
        )
        return conn
    except Exception as e:
        logger.error("Database connection error: %s", e)
        return None

def connect_to_db_oracle():
    # 查詢 GL_DAILY_RATES_V 需使用 ypr 查詢帳號（servicegate 在部分環境無此 view 權限）
    db_config = load_oracle_config('config/db_config.yaml', role='query')
    client_lib_dir = os.environ.get('ORACLE_CLIENT_LIB_DIR') or db_config.get('client_lib_dir')
    try:
        oracledb.init_oracle_client(lib_dir=client_lib_dir)
    except oracledb.ProgrammingError:
        pass
    try:
        dsn = f"{db_config['host']}:{db_config['port']}/{db_config['service_name']}"
        conn = oracledb.connect(
            user=db_config['user'],
            password=db_config['password'],
            dsn=dsn
        )
        return conn
    except Exception as e:
        logger.error("Oracle connection error: %s", e)
        return None

def _get_oracle_insert_connections():
    """依 oracle_insert_targets 建立並回傳所有寫入目標的連線清單 [(connection, env_label)]。"""
    with open('config/db_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    targets = config.get('oracle_insert_targets', [config.get('oracle_active_env', 'uat')])
    client_lib_dir = os.environ.get('ORACLE_CLIENT_LIB_DIR')
    connections = []
    for env in targets:
        db_config = config.get(f'oracle_insert_{env}')
        if db_config is None:
            logger.warning(f"Oracle insert config for env '{env}' not found, skipping")
            continue
        lib_dir = client_lib_dir or db_config.get('client_lib_dir')
        try:
            oracledb.init_oracle_client(lib_dir=lib_dir)
        except oracledb.ProgrammingError:
            pass
        conn = oracledb.connect(
            user=db_config['user'],
            password=db_config['password'],
            dsn=f"{db_config['host']}:{db_config['port']}/{db_config['service_name']}"
        )
        connections.append((conn, env.upper()))
    return connections

# === Email ===
def send_email_notification(subject, body, table_html=None, file_path=None, mailtype=None):
    if os.environ.get('SKIP_EMAIL'):
        logger.info(f"SKIP_EMAIL 設定，略過 Email 發送（subject: {subject}）")
        return True
    email_config = load_email_config('config/email_config.yaml')
    sender_email = email_config['sender_email']
    password = email_config['password']
    smtp_server = email_config['smtp_server']
    port = email_config['port']
    if mailtype == "error":
        receiver_email = email_config['receiver_emails_failure']
    else:
        receiver_email = email_config['receiver_emails_success']

    msg = MIMEMultipart()
    msg['From'] = sender_email
    if isinstance(receiver_email, list):
        receiver_str = ", ".join(receiver_email)
    else:
        receiver_str = receiver_email

    msg['To'] = receiver_str
    msg['Subject'] = Header(subject, 'utf-8')

    if mailtype == "error":
        msg['X-Priority'] = '1'
        msg['Importance'] = 'High'

    tmpl = _load_template('email_body.html')
    final_body = tmpl.substitute(
        body=body,
        table_html=table_html or "",
        image_html="",
    )
    msg.attach(MIMEText(final_body, 'html', 'utf-8'))

    if file_path and os.path.exists(file_path):
        filename = os.path.basename(file_path)
        with open(file_path, 'rb') as f:
            if filename.lower().endswith('.xlsx'):
                attachment = MIMEApplication(f.read(), _subtype='xlsx')
                attachment.add_header('Content-Disposition', 'attachment', filename=filename)
            else:
                attachment = MIMEApplication(f.read())
                attachment.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(attachment)

    try:
        with smtplib.SMTP(smtp_server, port) as server:
            server.sendmail(sender_email, receiver_email, msg.as_string())
            logger.info("Email sent successfully.")
        return True
    except smtplib.SMTPException as e:
        logger.error("Failed to send email: %s", e)
        return False

# === JSON Fetching ===
def fetch_json():
    """從關務署 API 抓取最新三旬匯率 JSON，回傳 (filtered_items, start, end, raw_data)。"""
    url = "https://portal.sw.nat.gov.tw/APGQ/GC331!downLoad?formBean.downLoadFile=CURRENT_JSON"
    response = requests.get(url, timeout=10, verify=False)
    if response.status_code != 200:
        logger.error(f"API 請求失敗，狀態碼: {response.status_code}")
        return None

    data = response.json()
    json_start = data['start']
    json_end = data['end']

    json_filtered_items = [item for item in data['items'] if item['code'] in PORTAL_CURRENCIES]

    for item in json_filtered_items:
        try:
            conversion_rate = 1 / float(item['buyValue'])
            item['conversion_rate'] = conversion_rate
            log_and_collect(
                f"Code: {item['code']}, Buy Value: {item['buyValue']}, "
                f"Start: {json_start}, End: {json_end}, Conversion Rate: {conversion_rate:.4f}"
            )
        except (ValueError, ZeroDivisionError) as e:
            logger.error(f"Error calculating rate for {item['code']}: {e}")

    return json_filtered_items, json_start, json_end, data

# === Rate Table HTML ===
def build_portal_rate_table(filtered_items, start, end):
    """使用模板產生三旬匯率 HTML 表格。"""
    row_tmpl = _load_template('portal_rate_row.html')
    portal_rows = ""
    for item in filtered_items:
        portal_rows += row_tmpl.substitute(
            code=item['code'],
            buy_value=item['buyValue'],
            conversion_rate=f"{item.get('conversion_rate', 0):.4f}",
        )

    table_tmpl = _load_template('portal_rate_table.html')
    return table_tmpl.substitute(
        period_start=start,
        period_end=end,
        portal_rows=portal_rows,
    )

def query_oracle_portal_rates(start, end):
    """從 Oracle GL_DAILY_RATES_V 查詢最新寫入的 SC_三旬匯率 資料，用於 Email 通知表格。"""
    connection = None
    try:
        connection = connect_to_db_oracle()
        if connection is None:
            logger.warning("無法連線 Oracle，無法查詢三旬匯率資料。")
            return None

        start_date = datetime.strptime(start, '%Y%m%d')
        end_date = datetime.strptime(end, '%Y%m%d')

        cursor = connection.cursor()
        # GL_DAILY_RATES_V 每天一筆；Email 只顯示期間，故去掉日期後 DISTINCT，每幣別一筆
        sql = """
            SELECT DISTINCT FROM_CURRENCY, TO_CURRENCY,
                   USER_CONVERSION_TYPE, CONVERSION_RATE
            FROM APPS.GL_DAILY_RATES_V
            WHERE CONVERSION_DATE BETWEEN :1 AND :2
              AND USER_CONVERSION_TYPE = 'SC_三旬匯率'
            ORDER BY FROM_CURRENCY, TO_CURRENCY
        """
        cursor.execute(sql, [start_date, end_date])
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()

        logger.info(f"Oracle GL_DAILY_RATES_V 查詢到 {len(rows)} 筆 SC_三旬匯率 資料 (DISTINCT)")
        return [dict(zip(columns, row)) for row in rows]

    except Exception as e:
        logger.warning(f"Oracle 查詢失敗: {e}")
        return None
    finally:
        if connection:
            connection.close()

def build_oracle_portal_rate_table(oracle_rows, start, end):
    """使用 Oracle 寫入資料建構 SC_三旬匯率 HTML 表格（匹配 Excel 格式）。

    Conversion Date 顯示 API 起迄日期 (start ~ end)。
    """
    row_tmpl = _load_template('oracle_rate_row.html')
    rows_html = ""

    period_date_str = f"{start} ~ {end}"
    for row in oracle_rows:
        rate = float(row['CONVERSION_RATE'])
        inv_rate = round(1 / rate, 4) if rate != 0 else 0

        rows_html += row_tmpl.substitute(
            from_currency=row['FROM_CURRENCY'],
            to_currency=row['TO_CURRENCY'],
            conversion_date=period_date_str,
            conversion_type=row['USER_CONVERSION_TYPE'],
            conversion_rate=f"{rate:.4f}",
            inverse_conversion_rate=f"{inv_rate:.4f}",
        )

    table_tmpl = _load_template('oracle_portal_rate_table.html')
    return table_tmpl.substitute(
        period_start=start,
        period_end=end,
        oracle_rows=rows_html,
    )

# === Database Operations (MERGE/Upsert) ===
def _execute_merge_rate(cursor, from_currency, to_currency, from_date, to_date,
                        rate, rev_rate):
    """Execute a MERGE upsert for a single rate pair into DWNotes."""
    user_conversion_type = 'SC_三旬匯率'

    merge_sql = """
        MERGE DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE AS target
        USING (SELECT ? AS from_currency,
                      ? AS to_currency,
                      ? AS from_conversion_date,
                      ? AS to_conversion_date,
                      ? AS user_conversion_type) AS source
        ON target.from_currency = source.from_currency
           AND target.to_currency = source.to_currency
           AND target.from_conversion_date = source.from_conversion_date
           AND target.user_conversion_type = source.user_conversion_type

        WHEN MATCHED THEN
            UPDATE SET
                to_conversion_date = source.to_conversion_date,
                conversion_rate = ?,
                inverse_conversion_rate = ?,
                user_id = '1175',
                launch_rate_change = 'Y',
                insert_time = GETDATE()

        WHEN NOT MATCHED THEN
            INSERT (from_currency, to_currency, from_conversion_date, to_conversion_date,
                    user_conversion_type, conversion_rate, mode_flag,
                    inverse_conversion_rate, user_id, launch_rate_change,
                    batch_number, insert_time)
            VALUES (source.from_currency, source.to_currency,
                    source.from_conversion_date, source.to_conversion_date,
                    source.user_conversion_type, ROUND(?,4), 'I',
                    ROUND(?,4), '1175', 'Y',
                    NULL, GETDATE());
    """

    params = (
        from_currency, to_currency, from_date, to_date, user_conversion_type,
        rate, rev_rate,
        rate, rev_rate,
    )

    cursor.execute(merge_sql, params)

def insert_portal_rates(filtered_items, start, end):
    """將三旬匯率寫入 DWNotes SQL Server，使用 API 起迄日期 (不展開每日)。"""
    connection = connect_to_db_sql()
    if connection is None:
        logger.error("無法建立資料庫連線，請檢查 db_config.yaml 與網路連線。")
        raise ConnectionError("Database connection failed.")

    cursor = connection.cursor()

    try:
        start_date = datetime.strptime(start, '%Y%m%d').date()
        end_date = datetime.strptime(end, '%Y%m%d').date()

        # 先刪除同期間的舊資料（含先前每日展開的資料）
        delete_sql = """
            DELETE FROM DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
            WHERE from_conversion_date BETWEEN ? AND ?
              AND user_conversion_type = N'SC_三旬匯率'
        """
        cursor.execute(delete_sql, (start_date, end_date))
        deleted_count = cursor.rowcount
        logger.info(f"已刪除 SQL Server 同期間 SC_三旬匯率 {deleted_count} 筆 ({start}~{end})")

        # 以 from_conversion_date=start, to_conversion_date=end 寫入
        insert_sql = """
            INSERT INTO DWNotes.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
                (from_currency, to_currency, from_conversion_date, to_conversion_date,
                 user_conversion_type, conversion_rate, mode_flag,
                 inverse_conversion_rate, user_id, launch_rate_change,
                 batch_number, insert_time)
            VALUES (?, ?, ?, ?, N'SC_三旬匯率', ROUND(?, 4), 'I', ROUND(?, 4), '1175', 'Y', NULL, GETDATE())
        """

        insert_count = 0
        for item in filtered_items:
            base_rate: float = float(f"{float(item['buyValue']):.4f}")
            inverse_rate: float = float(f"{1 / base_rate:.4f}") if base_rate != 0 else 0.0

            cursor.execute(insert_sql, (item["code"], "TWD", start_date, end_date, base_rate, inverse_rate))
            cursor.execute(insert_sql, ("TWD", item["code"], start_date, end_date, inverse_rate, base_rate))
            insert_count += 2

        connection.commit()
        logger.info(f"三旬匯率寫入成功，共 {insert_count} 筆 (日期: {start}~{end})")
        return insert_count

    except Exception as e:
        logger.error("Error while inserting portal rate data: %s", e)
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

# === Oracle Database Operations ===
def insert_portal_rates_oracle(filtered_items, start, end):
    """
    將三旬匯率寫入所有 oracle_insert_targets 的 YSC.SGLP0021_TEMP。
    回傳本次寫入的資料 list[dict]，供 Email 通知使用。
    """
    start_dt = datetime.strptime(start, '%Y%m%d')
    end_dt = datetime.strptime(end, '%Y%m%d')

    # 組裝資料（與目標環境無關，只需計算一次）
    oracle_rows = []
    inserted_data = []
    for item in filtered_items:
        base_rate: float = float(f"{float(item['buyValue']):.4f}")
        oracle_rows.append((
            'TW_BANK', item["code"], 'TWD', start_dt, end_dt,
            base_rate, 'I', start_dt, 'N', 'SC_三旬匯率'
        ))
        inserted_data.append({
            'FROM_CURRENCY': item["code"], 'TO_CURRENCY': 'TWD',
            'FROM_DATE': start_dt, 'TO_DATE': end_dt,
            'USER_CONVERSION_TYPE': 'SC_三旬匯率',
            'CONVERSION_RATE': base_rate,
        })

    delete_sql = """
        DELETE FROM YSC.SGLP0021_TEMP
        WHERE FROM_DATE BETWEEN :1 AND :2
          AND USER_CONVERSION_TYPE = 'SC_三旬匯率'
    """
    insert_sql = """
        INSERT INTO YSC.SGLP0021_TEMP (
            SOURCE_FROM, FROM_CURRENCY, TO_CURRENCY, FROM_DATE, TO_DATE,
            CONVERSION_RATE, MODE_FLAG, IMPORTED_DATE, IMPORTED_FLAG,
            USER_CONVERSION_TYPE
        ) VALUES (:1, :2, :3, :4, :5, ROUND(:6, 4), :7, :8, :9, :10)
    """

    connections = _get_oracle_insert_connections()
    if not connections:
        raise ConnectionError("Oracle 無可用的 insert 目標環境")

    errors = []
    for connection, env_label in connections:
        try:
            cursor = connection.cursor()
            cursor.execute(delete_sql, [start_dt, end_dt])
            logger.info(f"[{env_label}] 已刪除 Oracle 同期間 SC_三旬匯率 {cursor.rowcount} 筆 ({start}~{end})")

            cursor.executemany(insert_sql, oracle_rows)
            connection.commit()
            logger.info(f"[{env_label}] 成功寫入 Oracle (YSC.SGLP0021_TEMP)，共 {len(oracle_rows)} 筆三旬匯率")

            logger.info(f"[{env_label}] 開始執行 sglp0021_pkg.main ...")
            cursor2 = connection.cursor()
            l_errbuf = cursor2.var(oracledb.STRING, 2000)
            l_retcode = cursor2.var(oracledb.STRING, 10)
            cursor2.execute("""
                BEGIN
                    APPS.sglp0021_pkg.main(:errbuf, :retcode);
                END;
            """, {'errbuf': l_errbuf, 'retcode': l_retcode})
            connection.commit()
            errbuf_val = l_errbuf.getvalue()
            retcode_val = l_retcode.getvalue()
            cursor2.close()
            logger.info(f"[{env_label}] sglp0021_pkg.main 完成。retcode={retcode_val}, errbuf={errbuf_val}")

            if str(retcode_val) != '0':
                raise RuntimeError(f"sglp0021_pkg.main 執行失敗 (retcode={retcode_val}): {errbuf_val}")

            cursor.close()
        except Exception as e:
            logger.error(f"[{env_label}] Oracle 寫入失敗: {e}")
            connection.rollback()
            errors.append(f"[{env_label}] {e}")
        finally:
            connection.close()

    if errors:
        raise RuntimeError(f"Oracle 寫入失敗: {'; '.join(errors)}")

    return inserted_data

# === Step Status HTML ===
def _build_step_status_html(step_results):
    """將 step_results list 組成 HTML 表格，用於 email 通知。"""
    row_tmpl = _load_template('step_row.html')
    rows_html = ""
    for step_name, result_text in step_results:
        is_fail = result_text.startswith("失敗")
        rows_html += row_tmpl.substitute(
            step_name=step_name,
            color="#d32f2f" if is_fail else "#2e7d32",
            icon="&#10060;" if is_fail else "&#9989;",
            result_text=result_text,
        )
    table_tmpl = _load_template('step_status.html')
    return table_tmpl.substitute(step_rows=rows_html)

# === Main Entry Point ===
logger = logging.getLogger(__name__)

def _build_excel_sheets(filtered_items, start, end, raw_data):
    """建構 Excel 4 sheets 的 DataFrame dict。"""
    start_dt = datetime.strptime(start, '%Y%m%d')
    end_dt = datetime.strptime(end, '%Y%m%d')

    # Sheet 1: Oracle ERP (GL_DAILY_RATES_V)
    df_oracle_erp = pd.DataFrame()
    oracle_conn = None
    try:
        oracle_conn = connect_to_db_oracle()
        if oracle_conn:
            oracle_cursor = oracle_conn.cursor()
            oracle_sql = """
                SELECT FROM_CURRENCY, TO_CURRENCY, CONVERSION_DATE,
                       USER_CONVERSION_TYPE, CONVERSION_RATE
                FROM APPS.GL_DAILY_RATES_V
                WHERE CONVERSION_DATE BETWEEN :1 AND :2
                  AND USER_CONVERSION_TYPE = 'SC_三旬匯率'
                ORDER BY FROM_CURRENCY, TO_CURRENCY, CONVERSION_DATE
            """
            oracle_cursor.execute(oracle_sql, [start_dt, end_dt])
            oracle_rows_db = oracle_cursor.fetchall()
            oracle_columns = [desc[0] for desc in oracle_cursor.description]
            oracle_cursor.close()
            df_oracle_erp = pd.DataFrame(oracle_rows_db, columns=oracle_columns)
            logger.info(f"Oracle ERP 查詢到 {len(df_oracle_erp)} 筆三旬匯率資料")
    except Exception as e:
        logger.warning(f"Oracle 查詢失敗，Oracle ERP sheet 將為空白: {e}")
    finally:
        if oracle_conn:
            oracle_conn.close()

    # Sheet 2: DWNotes_DAILY_RATES
    dwnotes_rows = []
    for item in filtered_items:
        base_rate: float = float(f"{float(item['buyValue']):.4f}")
        inverse_rate: float = float(f"{1 / base_rate:.4f}") if base_rate != 0 else 0.0
        dwnotes_rows.append({
            'from_currency': item["code"], 'to_currency': 'TWD',
            'from_conversion_date': start_dt.date(), 'to_conversion_date': end_dt.date(),
            'user_conversion_type': 'SC_三旬匯率',
            'conversion_rate': base_rate, 'inverse_conversion_rate': inverse_rate,
            'mode_flag': 'I', 'user_id': '1175', 'launch_rate_change': 'Y',
        })
        dwnotes_rows.append({
            'from_currency': 'TWD', 'to_currency': item["code"],
            'from_conversion_date': start_dt.date(), 'to_conversion_date': end_dt.date(),
            'user_conversion_type': 'SC_三旬匯率',
            'conversion_rate': inverse_rate, 'inverse_conversion_rate': base_rate,
            'mode_flag': 'I', 'user_id': '1175', 'launch_rate_change': 'Y',
        })
    df_dwnotes = pd.DataFrame(dwnotes_rows)

    # Sheet 3: API_Raw_Data
    df_api = pd.DataFrame(raw_data.get('items', []))

    return {
        'Oracle ERP': df_oracle_erp,
        'DWNotes_DAILY_RATES': df_dwnotes,
        'API_Raw_Data': df_api,
    }


def main():
    setup_logging(log_dir='./logs')
    logger.info("--- Portal Rate Process Started ---")

    current_step = ""
    step_results = []
    try:
        # ===== Step 1: API 資料抓取 =====
        current_step = "Step 1: API 資料抓取"
        result = fetch_json()
        if result is None:
            raise RuntimeError("無法從關務署 API 取得匯率數據。")

        filtered_items, start, end, raw_data = result
        step_results.append(("Step 1: API 抓取", f"取得 {len(filtered_items)} 種幣別匯率 ({start}~{end})"))

        # 儲存原始 JSON
        data_folder = f"./data/portal/{datetime.now().strftime('%y%m%d')}"
        os.makedirs(data_folder, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        raw_json_path = os.path.join(data_folder, f"portal_raw_{timestamp}.json")
        with open(raw_json_path, 'w', encoding='utf-8') as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Raw JSON saved to: {raw_json_path}")

        # ===== Step 2: SQL Server 寫入 (DWNotes) =====
        current_step = "Step 2: SQL Server 寫入 (DWNotes)"
        merge_count = insert_portal_rates(filtered_items, start, end)
        step_results.append(("Step 2: SQL Server (DWNotes)", f"{merge_count} 筆三旬匯率 Upsert 成功"))

        # ===== Step 3: Oracle 寫入 (YSC.SGLP0021_TEMP) =====
        current_step = "Step 3: Oracle 寫入 (YSC.SGLP0021_TEMP)"
        oracle_inserted_data = insert_portal_rates_oracle(filtered_items, start, end)
        oracle_inserted_len = len(oracle_inserted_data) if oracle_inserted_data is not None else 0
        step_results.append(("Step 3: Oracle (YSC.SGLP0021_TEMP)", f"{oracle_inserted_len} 筆三旬匯率寫入成功"))

        # ===== Step 4: Excel 報表產生 (3 sheets) =====
        current_step = "Step 4: Excel 報表產生"
        excel_file_name = f"Portal_Exchange_Rates_{timestamp}.xlsx"
        excel_file_path = os.path.join(data_folder, excel_file_name)

        sheets = _build_excel_sheets(filtered_items, start, end, raw_data)
        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, index=False, sheet_name=sheet_name)
        logger.info(f"Excel report saved to: {excel_file_path}")
        step_results.append(("Step 4: Excel 報表", f"檔案產生完成: {excel_file_name}"))

        # ===== Step 5: 報表寄送 =====
        current_step = "Step 5: 報表寄送"
        # 從 GL_DAILY_RATES_V 查詢匯率資料建構 Email 表格
        gl_rates = query_oracle_portal_rates(start, end)
        if gl_rates:
            rate_table_html = build_oracle_portal_rate_table(gl_rates, start, end)
        elif oracle_inserted_data:
            rate_table_html = build_oracle_portal_rate_table(oracle_inserted_data, start, end)
        else:
            rate_table_html = build_portal_rate_table(filtered_items, start, end)
        step_results.append(("Step 5: 報表寄送", "Email 寄送中"))
        step_status_html = _build_step_status_html(step_results)

        step_total = len(step_results)
        step_success = sum(1 for _, r in step_results if not r.startswith("失敗"))
        topic_tmpl = _load_template('topic_portal.html')
        topic_html = topic_tmpl.substitute(
            step_total=step_total,
            step_success=step_success,
        )

        body_text = f"""
            {topic_html}
            <p>此報告為三旬匯率資料，期間：{start} ~ {end}。</p>
            {rate_table_html}
            {step_status_html}
        """

        success = send_email_notification(
            subject=f"【三旬匯率報告】關務署匯率報告 - {start}~{end}",
            body=body_text,
            file_path=excel_file_path,
            mailtype="success"
        )
        if success:
            logger.info(f"Excel report kept at: {excel_file_path}")

    except Exception as e:
        logger.error(f"工作執行失敗 [{current_step}]: {e}")
        step_results.append((current_step, f"失敗 - {e}"))
        status_html = _build_step_status_html(step_results)
        send_email_notification(
            subject=f"【錯誤報告】三旬匯率程式異常 - {datetime.now().strftime('%Y-%m-%d')}",
            body=f"<p>程式於 <b>{current_step}</b> 階段執行失敗。</p>"
                 f"<p>錯誤訊息：</p><pre>{e}</pre>"
                 f"<br>{status_html}",
            file_path=None,
            table_html=None,
            mailtype="error"
        )
        sys.exit(1)

    logger.info("--- Portal Rate Process Finished ---")


if __name__ == "__main__":
    main()
