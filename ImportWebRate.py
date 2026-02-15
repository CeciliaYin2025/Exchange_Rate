# === Imports ===
import asyncio
import logging
import os
import smtplib
import time
import warnings
from datetime import date, datetime, timedelta
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from string import Template

import mssql_python
import oracledb
import pandas as pd
import requests
import yaml
from bs4 import BeautifulSoup
from openpyxl.drawing.image import Image as XlImage
from playwright.async_api import async_playwright
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


# === Constants ===
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'config', 'html', 'template')

RUN_LOGS = []

# === Template Loading ===
def _load_template(name):
    """從 config/html/template/ 載入 HTML template 檔案，回傳 string.Template 物件。"""
    path = os.path.join(TEMPLATE_DIR, name)
    with open(path, 'r', encoding='utf-8') as f:
        return Template(f.read())

# === Logging Setup ===
warnings.filterwarnings("ignore", module='urllib3')

logger = None

def _get_daily_log_filepath(log_dir, prefix='currency_monthly'):
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

def setup_logging(log_dir='./logs'):
    """
    設定日誌系統，將輸出同時寫入控制台和日誌檔案。
    日誌檔名依日期產生，同日重複執行時自動加流水號。
    """
    global logger

    logger = logging.getLogger('CurrencyMonthlyLogger')
    logger.setLevel(logging.INFO)

    if not logger.handlers:
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

    if "dev_db" in config:
        return config["dev_db"]
    elif "database" in config:
        return config["database"]
    else:
        available_keys = list(config.keys()) if config else "Empty File"
        raise KeyError(f"設定檔中找不到 'dev_db' 或 'database'。目前擁有的標籤為: {available_keys}")

def load_oracle_config(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config['oracle_db_insert']

def load_email_config(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)
    return config['email']

# === Date Utilities ===
def get_current_month_period():
    today = datetime.today()
    current_month_start = today.replace(day=1)
    current_month_last = current_month_start + pd.offsets.MonthEnd(1)
    return current_month_start, current_month_last

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
    db_config = load_oracle_config('config/db_config.yaml')
    client_lib_dir = db_config.get('client_lib_dir')
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

# === Email ===
def send_email_notification(subject, body, table_html=None, file_path=None, mailtype=None, snapshot_paths=None):
    email_config = load_email_config('config/email_config.yaml')
    sender_email = email_config['sender_email']
    password = email_config['password']
    smtp_server = email_config['smtp_server']
    port = email_config['port']
    if mailtype == "error":
        receiver_email = email_config['receiver_emails_failure']
    else:
        receiver_email = email_config['receiver_emails_success']

    msg = MIMEMultipart('related')
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

    # Build image HTML for inline snapshots
    image_html = ""
    if snapshot_paths:
        image_html += '<h3 id="web-snapshots">網頁截圖</h3>'
        for cid_name, label in [('inr_img', 'INR (USD/INR)'), ('mxn_img', 'MXN (USD/MXN)'), ('try_img', 'TRY (TRY/TWD)')]:
            if cid_name in snapshot_paths:
                image_html += f'<p>{label}:</p><img src="cid:{cid_name}" style="max-width:600px;"><br>'

    tmpl = _load_template('email_body.html')
    final_body = tmpl.substitute(
        body=body,
        table_html=table_html or "",
        image_html=image_html,
    )

    msg_alternative = MIMEMultipart('alternative')
    msg.attach(msg_alternative)
    msg_alternative.attach(MIMEText(final_body, 'html', 'utf-8'))

    # Attach inline images
    if snapshot_paths:
        for cid_name, path in snapshot_paths.items():
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-ID', f'<{cid_name}>')
                    img.add_header('Content-Disposition', 'inline', filename=os.path.basename(path))
                    msg.attach(img)

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

# === Web Scraping / Screenshots ===
async def snapshot_page(url, output_path):
    """擷取網頁：保留 Title 與趨勢圖，移除廣告，避免超時"""
    folder = os.path.dirname(output_path)
    os.makedirs(folder, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1280, 'height': 1200})

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            try:
                await page.wait_for_selector("table", timeout=20000)
            except Exception:
                logger.warning("等待表格超時，嘗試繼續截圖...")

            await page.evaluate("""() => {
                const ads = [
                    '.advertisement', '.ad-container', 'iframe',
                    '[id*="ad-"]', '.footer-ads', '#gpt-ad',
                    '.universal-footer', '.sidebar'
                ];
                ads.forEach(s => {
                    document.querySelectorAll(s).forEach(el => el.style.display = 'none');
                });
                document.body.style.backgroundColor = 'white';
            }""")

            await asyncio.sleep(2)

            await page.screenshot(
                path=output_path,
                clip={'x': 0, 'y': 0, 'width': 1200, 'height': 1200}
            )

            logger.info(f"成功擷取去廣告畫面 (含來源標題): {os.path.basename(output_path)}")

        except Exception as e:
            logger.error(f"截圖出錯: {e}")
        finally:
            await browser.close()
    return output_path

# === Rate Fetching ===
def get_latest_usdtwd_rate_from_cbc():
    url = "https://www.cbc.gov.tw/tw/lp-645-1.html"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=10, verify=False, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table')
            if not table:
                return None

            possible_dates = [(date.today() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, 16)]

            rows = table.find_all("tr")
            for row in rows:
                cols = row.find_all(["td", "th"])
                col_texts = [c.text.strip() for c in cols]

                if len(col_texts) < 2:
                    continue

                row_date = col_texts[0].replace("/", "-")
                if row_date in possible_dates:
                    try:
                        rate_val = float(col_texts[1])
                        logger.info(f"成功取得 CBC USD 匯率: {rate_val} (日期: {row_date})")
                        return rate_val
                    except ValueError:
                        continue
    except Exception as e:
        logger.error(f"CBC fetch error: {e}")
    return None

async def fetch_currency_rate_selenium(driver, target_date_obj, currency_pair):
    url = f"https://invest.cnyes.com/forex/detail/{currency_pair}/history#fixed"
    target_date_slash = target_date_obj.strftime('%Y/%m/%d')
    target_date_dash = target_date_obj.strftime('%Y-%m-%d')

    snapshot_dir = f"./data/web/{datetime.now().strftime('%y%m%d')}"
    snapshot_path = f"{snapshot_dir}/{currency_pair}_snapshot.jpg"
    await snapshot_page(url, output_path=snapshot_path)

    try:
        driver.get(url)
        time.sleep(5)
        rows = driver.find_elements(By.CSS_SELECTOR, 'table tr')
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, 'td')
            col_texts = [c.text.strip() for c in cols]
            if len(col_texts) < 5:
                continue

            date_str = col_texts[0].replace('-', '/')
            if date_str == target_date_slash or date_str == target_date_dash:
                rate = float(col_texts[4].replace(',', ''))
                return rate
        return None
    except Exception as e:
        logger.error(f"Selenium 抓取 {currency_pair} 失敗: {e}")
        return None

async def fetch_rate_with_fallback(driver, currency_name, currency_pair):
    """共用 driver 的回溯工具，最多回溯 14 天。"""
    for i in range(1, 15):
        target_date = (date.today() - timedelta(days=i))
        rate = await fetch_currency_rate_selenium(driver, target_date, currency_pair)
        if rate:
            logger.info(f"成功取得 {currency_name} 匯率: {rate} (日期: {target_date})")
            return rate
    logger.error(f"連續 14 天無法取得 {currency_name} 匯率")
    return None

# === Rate Calculation ===
def calculate_monthly_rates(all_currency_rates, target_date):
    """計算月匯率（正向 + 反向），回傳 rows list。"""
    rows = []
    rate_date_str = target_date.strftime("%Y-%m-%d")

    for from_cur, (rate_1, rate_2) in all_currency_rates.items():
        try:
            v_rate = float(rate_1) / float(rate_2) if float(rate_2) != 0 else 0
            rev_rate = float(rate_2) / float(rate_1) if float(rate_1) != 0 else 0

            rows.append({
                "from_cur": from_cur, "to_cur": 'TWD',
                "rate_date": rate_date_str,
                "v_rate": v_rate, "rev_rate": rev_rate,
                "conv_type": "SC_銀行匯率"
            })
            rows.append({
                "from_cur": 'TWD', "to_cur": from_cur,
                "rate_date": rate_date_str,
                "v_rate": rev_rate, "rev_rate": v_rate,
                "conv_type": "SC_銀行匯率"
            })
        except Exception as e:
            logger.error(f"計算 {from_cur} 匯率失敗: {e}")

    return rows

# === Database Operations (MERGE/Upsert) ===
def _execute_merge_rate(cursor, from_currency, to_currency, conversion_date,
                        rate, rev_rate):
    """Execute a MERGE upsert for a single rate pair into SERVICE_TEST."""
    merge_sql = """
        MERGE SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE AS target
        USING (SELECT ? AS from_currency, ? AS to_currency, ? AS from_conversion_date) AS src
        ON (
            target.from_currency = src.from_currency
            AND target.to_currency = src.to_currency
            AND target.from_conversion_date = src.from_conversion_date
        )
        WHEN MATCHED THEN
            UPDATE SET
                conversion_rate = ?,
                inverse_conversion_rate = ?,
                user_conversion_type = ?,
                insert_time = GETDATE()
        WHEN NOT MATCHED THEN
            INSERT (
                from_currency, to_currency, from_conversion_date, to_conversion_date,
                user_conversion_type, conversion_rate, mode_flag, inverse_conversion_rate,
                user_id, launch_rate_change, insert_time
            )
            VALUES (?, ?, ?, ?, ?, ?, 'I', ?, '1175', 'Y', GETDATE());
    """

    conv_type = "SC_銀行匯率"
    params = (
        from_currency, to_currency, conversion_date,
        rate, rev_rate, conv_type,
        from_currency, to_currency, conversion_date, conversion_date, conv_type,
        rate, rev_rate,
    )

    cursor.execute(merge_sql, params)

def prepare_and_merge_sql_server(all_currency_rates, target_date):
    """計算匯率並寫入 SQL Server，回傳 rows list。"""
    rows = calculate_monthly_rates(all_currency_rates, target_date)

    connection = connect_to_db_sql()
    if connection is None:
        logger.error("無法建立資料庫連線，請檢查 db_config.yaml 與網路連線。")
        raise ConnectionError("Database connection failed.")

    cursor = connection.cursor()

    try:
        for param_dict in rows:
            _execute_merge_rate(
                cursor,
                param_dict["from_cur"],
                param_dict["to_cur"],
                param_dict["rate_date"],
                param_dict["v_rate"],
                param_dict["rev_rate"],
            )
        connection.commit()
        logger.info(f"SQL Server 更新成功，共 {len(rows)} 筆")
    except Exception as e:
        logger.error(f"SQL Server 更新失敗: {e}")
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    return rows

def insert_to_oracle(rows_data):
    """
    將資料寫入 Oracle Table: YSC.SGLP0021_TEMP
    回傳本次寫入的資料 list[dict]，供 Email 通知使用。
    """
    connection = None
    try:
        connection = connect_to_db_oracle()
        if connection is None:
            raise ConnectionError("Oracle 連線失敗。")

        oracle_rows = []
        inserted_data = []
        now = datetime.now()

        for r in rows_data:
            rate_date = datetime.strptime(r['rate_date'], '%Y-%m-%d')
            oracle_rows.append((
                'TW_BANK', r['from_cur'], r['to_cur'], rate_date, rate_date,
                r['v_rate'], 'I', now, 'N', 'SC_銀行匯率'
            ))
            inserted_data.append({
                'FROM_CURRENCY': r['from_cur'],
                'TO_CURRENCY': r['to_cur'],
                'FROM_DATE': rate_date,
                'USER_CONVERSION_TYPE': 'SC_銀行匯率',
                'CONVERSION_RATE': r['v_rate'],
            })

        # 先刪除同日的 SC_銀行匯率 (INR/MXN/TRY) 資料，避免重複
        rate_dates = list({r['rate_date'] for r in rows_data})
        cursor = connection.cursor()
        for rd in rate_dates:
            rate_dt = datetime.strptime(rd, '%Y-%m-%d')
            delete_sql = """
                DELETE FROM YSC.SGLP0021_TEMP
                WHERE TRUNC(FROM_DATE) = TRUNC(:1)
                  AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
                  AND (FROM_CURRENCY IN ('INR', 'MXN', 'TRY') OR TO_CURRENCY IN ('INR', 'MXN', 'TRY'))
            """
            cursor.execute(delete_sql, [rate_dt])
            deleted_count = cursor.rowcount
            logger.info(f"已刪除 Oracle 同日 SC_銀行匯率 (INR/MXN/TRY) {deleted_count} 筆 ({rd})")

        insert_sql = """
            INSERT INTO YSC.SGLP0021_TEMP (
                SOURCE_FROM, FROM_CURRENCY, TO_CURRENCY, FROM_DATE, TO_DATE,
                CONVERSION_RATE, MODE_FLAG, IMPORTED_DATE, IMPORTED_FLAG,
                USER_CONVERSION_TYPE
            ) VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10)
        """

        cursor.executemany(insert_sql, oracle_rows)
        connection.commit()
        cursor.close()

        logger.info(f"成功寫入 Oracle 資料庫 (YSC.SGLP0021_TEMP)，共 {len(oracle_rows)} 筆")
        return inserted_data

    except Exception as e:
        logger.error(f"Oracle 寫入失敗: {e}")
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()

# === Report & Email ===
def build_monthly_rate_table(rows_data, conversion_date):
    """使用模板產生月匯率 HTML 表格。"""
    row_tmpl = _load_template('rate_row.html')
    twd_rows = ""
    for r in rows_data:
        if r['to_cur'] == 'TWD':
            twd_rows += row_tmpl.substitute(
                from_currency=r['from_cur'],
                to_currency=r['to_cur'],
                conversion_date=r['rate_date'],
                conversion_rate=f"{r['v_rate']:.4f}",
                inverse_conversion_rate=f"{r['rev_rate']:.6f}",
            )

    table_tmpl = _load_template('monthly_rate_table.html')
    return table_tmpl.substitute(
        conversion_date=conversion_date,
        twd_rows=twd_rows,
    )

def query_oracle_bank_rates():
    """從 Oracle 查詢最新寫入的 SC_銀行匯率 資料（INR, MXN, TRY），用於 Email 通知表格。"""
    connection = None
    try:
        connection = connect_to_db_oracle()
        if connection is None:
            logger.warning("無法連線 Oracle，無法查詢匯率資料。")
            return None

        cursor = connection.cursor()
        sql = """
            SELECT FROM_CURRENCY, TO_CURRENCY, FROM_DATE,
                   USER_CONVERSION_TYPE, CONVERSION_RATE
            FROM YSC.SGLP0021_TEMP
            WHERE TRUNC(IMPORTED_DATE) = TRUNC(SYSDATE)
              AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
              AND (FROM_CURRENCY IN ('INR', 'MXN', 'TRY') OR TO_CURRENCY IN ('INR', 'MXN', 'TRY'))
            ORDER BY FROM_CURRENCY, TO_CURRENCY
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()

        logger.info(f"Oracle 查詢到 {len(rows)} 筆 SC_銀行匯率 資料 (INR/MXN/TRY)")
        return [dict(zip(columns, row)) for row in rows]

    except Exception as e:
        logger.warning(f"Oracle 查詢失敗: {e}")
        return None
    finally:
        if connection:
            connection.close()

def build_oracle_rate_table(oracle_rows):
    """使用 Oracle 查詢結果建構 SC_銀行匯率 HTML 表格（匹配 Excel 格式）。"""
    row_tmpl = _load_template('oracle_rate_row.html')
    rows_html = ""
    for row in oracle_rows:
        rate = float(row['CONVERSION_RATE'])
        inv_rate = round(1 / rate, 6) if rate != 0 else 0
        conv_date = row['FROM_DATE']
        if isinstance(conv_date, datetime):
            conv_date_str = conv_date.strftime('%m-%d-%y')
        else:
            conv_date_str = str(conv_date)

        rows_html += row_tmpl.substitute(
            from_currency=row['FROM_CURRENCY'],
            to_currency=row['TO_CURRENCY'],
            conversion_date=conv_date_str,
            conversion_type=row['USER_CONVERSION_TYPE'],
            conversion_rate=f"{rate:.6f}",
            inverse_conversion_rate=f"{inv_rate:.6f}",
        )

    table_tmpl = _load_template('oracle_rate_table.html')
    return table_tmpl.substitute(oracle_rows=rows_html)

def export_monthly_report_and_email(rows_to_insert, step_results, oracle_inserted_data=None):
    """查詢 DB 最新月匯率，產生 Excel 報表 (3 Sheets) 並寄送 Email。"""
    connection = None
    try:
        # === Sheet 1: SQL Server ===
        connection = connect_to_db_sql()
        if connection is None:
            logger.warning("無法連線資料庫查詢報表資料。")
            return

        sql_query = """
        SELECT from_currency, to_currency, from_conversion_date,
               conversion_rate, inverse_conversion_rate
        FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
        WHERE from_currency IN ('INR', 'MXN', 'TRY')
          AND from_conversion_date = (
              SELECT MAX(from_conversion_date)
              FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
              WHERE from_currency IN ('INR', 'MXN', 'TRY')
          )
        ORDER BY from_currency, to_currency
        """
        cursor = connection.cursor()
        cursor.execute(sql_query)
        sql_rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        connection.close()
        connection = None

        df_sql = pd.DataFrame(sql_rows, columns=columns)

        if df_sql.empty:
            logger.warning("資料庫中查無今日匯率資料，未發送報表。")
            return

        # === Sheet 2: Oracle ERP ===
        df_oracle = pd.DataFrame()
        oracle_conn = None
        try:
            oracle_conn = connect_to_db_oracle()
            if oracle_conn:
                oracle_cursor = oracle_conn.cursor()
                oracle_sql = """
                    SELECT SOURCE_FROM, FROM_CURRENCY, TO_CURRENCY, FROM_DATE, TO_DATE,
                           CONVERSION_RATE, MODE_FLAG, IMPORTED_DATE, IMPORTED_FLAG,
                           USER_CONVERSION_TYPE
                    FROM YSC.SGLP0021_TEMP
                    WHERE TRUNC(FROM_DATE) = TRUNC(:1)
                      AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
                      AND FROM_CURRENCY IN ('INR', 'MXN', 'TRY')
                    ORDER BY FROM_CURRENCY, TO_CURRENCY
                """
                oracle_cursor.execute(oracle_sql, [datetime.now()])
                oracle_rows = oracle_cursor.fetchall()
                oracle_columns = [desc[0] for desc in oracle_cursor.description]
                oracle_cursor.close()
                df_oracle = pd.DataFrame(oracle_rows, columns=oracle_columns)
                logger.info(f"Oracle ERP 查詢到 {len(df_oracle)} 筆資料")
            else:
                logger.warning("無法連線 Oracle，Oracle ERP sheet 將為空白。")
        except Exception as e:
            logger.warning(f"Oracle 查詢失敗，Oracle ERP sheet 將為空白: {e}")
        finally:
            if oracle_conn:
                oracle_conn.close()

        # === 產生 Excel 檔案 (3 Sheets) ===
        output_dir = f"./data/web/{datetime.now().strftime('%y%m%d')}"
        os.makedirs(output_dir, exist_ok=True)
        excel_file_name = f"Monthly_Exchange_Rates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        excel_file_path = os.path.join(output_dir, excel_file_name)

        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            # Sheet 1: SQL Server
            df_sql.to_excel(writer, index=False, sheet_name='SQL Server')

            # Sheet 2: Oracle ERP
            if not df_oracle.empty:
                df_oracle.to_excel(writer, index=False, sheet_name='Oracle ERP')
            else:
                pd.DataFrame().to_excel(writer, index=False, sheet_name='Oracle ERP')

            # Sheet 3: Screenshots
            pd.DataFrame().to_excel(writer, index=False, sheet_name='Screenshots')

        # Embed screenshot images into Sheet 3
        _snap_dir = f"./data/web/{datetime.now().strftime('%y%m%d')}"
        screenshot_info = [
            ('USDINR_snapshot.jpg', 'INR (USD/INR)'),
            ('USDMXN_snapshot.jpg', 'MXN (USD/MXN)'),
            ('TRYTWD_snapshot.jpg', 'TRY (TRY/TWD)'),
        ]

        import openpyxl
        wb = openpyxl.load_workbook(excel_file_path)
        ws = wb['Screenshots']
        current_row = 1
        IMG_HEIGHT_PX = 600
        ROW_HEIGHT_PX = 18.75  # Excel default row height in pixels
        ROWS_PER_IMAGE = int(IMG_HEIGHT_PX / ROW_HEIGHT_PX) + 2  # image rows + margin
        for img_file, label in screenshot_info:
            img_path = os.path.join(_snap_dir, img_file)
            ws[f'A{current_row}'] = label
            current_row += 1
            if os.path.exists(img_path):
                img = XlImage(img_path)
                img.width = 600
                img.height = IMG_HEIGHT_PX
                ws.add_image(img, f'A{current_row}')
            current_row += ROWS_PER_IMAGE
        wb.save(excel_file_path)

        logger.info(f"Excel 報表已產生: {excel_file_path}")

        # === Build email body ===
        # 使用本次執行匯入 Oracle 的資料建構 Email 表格
        if oracle_inserted_data:
            rate_table_html = build_oracle_rate_table(oracle_inserted_data)
        else:
            rate_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            rate_table_html = build_monthly_rate_table(rows_to_insert, rate_date)

        step_results.append(("Step 5: 報表寄送", "Excel 產生完成，Email 寄送中"))
        step_status_html = _build_step_status_html(step_results)

        step_total = len(step_results)
        step_success = sum(1 for _, r in step_results if not r.startswith("失敗"))
        topic_tmpl = _load_template('topic_web.html')
        topic_html = topic_tmpl.substitute(
            step_total=step_total,
            step_success=step_success,
        )

        snapshot_paths = {
            'inr_img': f"{_snap_dir}/USDINR_snapshot.jpg",
            'mxn_img': f"{_snap_dir}/USDMXN_snapshot.jpg",
            'try_img': f"{_snap_dir}/TRYTWD_snapshot.jpg"
        }

        body_text = f"""
            {topic_html}
            <h3>本次匯率寫入清單：</h3>
            {rate_table_html}
            {step_status_html}
        """

        send_email_notification(
            subject=f"(測試信)【匯率報告】月匯率執行結果 - {date.today() + timedelta(days=1)}",
            body=body_text,
            file_path=excel_file_path,
            snapshot_paths=snapshot_paths,
            mailtype="success"
        )

    except Exception as e:
        logger.error(f"匯出報表失敗: {e}")
    finally:
        if connection:
            connection.close()

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

async def async_main():
    setup_logging(log_dir='./logs')
    logger.info("--- Monthly Rate Process Started ---")

    start_time = time.time()
    current_step = ""
    step_results = []

    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        # ===== Step 1: CBC USD/TWD 匯率抓取 =====
        current_step = "Step 1: CBC USD/TWD 匯率抓取"
        usd_rate = get_latest_usdtwd_rate_from_cbc()
        if usd_rate is None:
            raise RuntimeError("無法從 CBC 取得 USD/TWD 匯率。")
        step_results.append(("Step 1: CBC USD/TWD", f"USD/TWD = {usd_rate}"))

        # ===== Step 2: MXN/INR/TRY 匯率抓取 (Selenium) =====
        current_step = "Step 2: MXN/INR/TRY 匯率抓取"
        mxn_rate = await fetch_rate_with_fallback(driver, "MXN", "USDMXN")
        inr_rate = await fetch_rate_with_fallback(driver, "INR", "USDINR")
        try_rate = await fetch_rate_with_fallback(driver, "TRY", "TRYTWD")

        if not all(r is not None for r in [mxn_rate, inr_rate, try_rate]):
            missing = [k for k, v in {"MXN": mxn_rate, "INR": inr_rate, "TRY": try_rate}.items() if v is None]
            raise RuntimeError(f"匯率資料不齊全 (缺失: {missing})")

        step_results.append(("Step 2: Selenium 抓取", f"MXN={mxn_rate}, INR={inr_rate}, TRY={try_rate}"))

        all_rates = {
            'INR': (usd_rate, inr_rate),
            'MXN': (usd_rate, mxn_rate),
            'TRY': (1, try_rate)
        }

        # ===== Step 3: SQL Server 寫入 =====
        current_step = "Step 3: SQL Server 寫入"
        rows = prepare_and_merge_sql_server(all_rates, date.today() + timedelta(days=1))
        step_results.append(("Step 3: SQL Server", f"{len(rows)} 筆匯率 Upsert 成功"))

        # ===== Step 4: Oracle 寫入 =====
        current_step = "Step 4: Oracle 寫入"
        oracle_inserted_data = None
        if rows:
            oracle_inserted_data = insert_to_oracle(rows)
            step_results.append(("Step 4: Oracle", f"{len(oracle_inserted_data)} 筆寫入成功"))
        else:
            step_results.append(("Step 4: Oracle", "無資料需要寫入"))

        # ===== Step 5: 報表寄送 =====
        current_step = "Step 5: 報表寄送"
        export_monthly_report_and_email(rows, step_results, oracle_inserted_data=oracle_inserted_data)
        logger.info("資料庫匯率更新成功。")

    except Exception as e:
        logger.error(f"工作執行失敗 [{current_step}]: {e}")
        step_results.append((current_step, f"失敗 - {e}"))
        status_html = _build_step_status_html(step_results)
        send_email_notification(
            subject=f"【錯誤報告】月匯率程式異常 - {datetime.now().strftime('%Y-%m-%d')}",
            body=f"<p>程式於 <b>{current_step}</b> 階段執行失敗。</p>"
                 f"<p>錯誤訊息：</p><pre>{e}</pre>"
                 f"<br>{status_html}",
            file_path=None,
            mailtype="error"
        )
    finally:
        driver.quit()
        logger.info(f"執行完畢，耗時: {time.time() - start_time:.2f} 秒")

    logger.info("--- Monthly Rate Process Finished ---")


if __name__ == "__main__":
    asyncio.run(async_main())
