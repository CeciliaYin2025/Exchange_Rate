# === Imports ===
import logging
import os
import re
import smtplib
import subprocess
import warnings
from datetime import date, datetime, timedelta
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from string import Template

import mssql_python
import oracledb
import pandas as pd
import requests
import yaml
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# === Constants ===
MAX_LOOKBACK_DAYS = 14
TWD_CURRENCIES = ['CNY', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'PHP']
USD_CURRENCIES = ['EUR', 'CAD', 'AUD', 'JPY', 'PHP']

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
_SNAPSHOT_DIR = f"./data/daily/{datetime.now().strftime('%y%m%d')}/web_snapshots"
_SNAPSHOT_TIME = datetime.now().strftime('%H%M%S')
TCB_SNAPSHOT_PATH = f"{_SNAPSHOT_DIR}/tcb_rate_snapshot_{_SNAPSHOT_TIME}.jpg"
CBC_SNAPSHOT_PATH = f"{_SNAPSHOT_DIR}/cbc_rate_snapshot_{_SNAPSHOT_TIME}.jpg"

def _get_daily_log_filepath(log_dir, prefix='currency_daily'):
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

    logger = logging.getLogger('CurrencyLogger')
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

# === Email ===
def send_email_notification(subject, body, table_html=None, file_path=None, mailtype=None, image_path=None, additional_image_path=None):
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

    image_html = ""
    if image_path is not None:
        image_html += '<h3 id="web-snapshots">網頁截圖</h3>'
        image_html += '<p>TCB 網頁截圖：</p><img src="cid:snapshot_image" style="max-width:800px;"><br>'
    if additional_image_path is not None:
        image_html += '<p>CBC 網頁截圖：</p><img src="cid:snapshot_image_2" style="max-width:800px;"><br>'

    tmpl = _load_template('email_body.html')
    final_body = tmpl.substitute(
        body=body,
        table_html=table_html or "",
        image_html=image_html,
    )
    msg.attach(MIMEText(final_body, 'html', 'utf-8'))

    if image_path is not None:
        if os.path.exists(image_path):
            filename = os.path.basename(image_path)
            try:
                with open(image_path, 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-ID', '<snapshot_image>')
                    img.add_header('Content-Disposition', 'inline', filename=filename)
                    msg.attach(img)
                    logger.info(f"Successfully attached inline image: {filename}")
            except Exception as e:
                logger.error(f"Failed to read or attach image file {image_path}: {e}")
        else:
            logger.warning(f"Image path is invalid or file does not exist: {os.path.abspath(image_path)}")

    if additional_image_path is not None and os.path.exists(additional_image_path):
        with open(additional_image_path, 'rb') as f:
            img2 = MIMEImage(f.read())
            img2.add_header('Content-ID', '<snapshot_image_2>')
            img2.add_header('Content-Disposition', 'inline', filename=os.path.basename(additional_image_path))
            msg.attach(img2)

    if file_path and os.path.exists(file_path):
        filename = os.path.basename(file_path)

        with open(file_path, 'rb') as f:

            if filename.lower().endswith(('.xlsx')):
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
def snapshot_page(url, output_path):
    """擷取指定網頁為圖片（jpg）"""
    folder = os.path.dirname(output_path)
    filename = os.path.basename(output_path)

    os.makedirs(folder, exist_ok=True)
    filepath = output_path
    logger.info(f"Filename: {filename}")
    logger.info(f"Filepath: {os.path.abspath(filepath)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.screenshot(path=filepath, full_page=True, type="jpeg", quality=90)
        browser.close()

    logger.info(f"Screenshot saved to: {os.path.abspath(filepath)}")

    return filepath

# === Rate Fetching ===
def get_target_date(url):
    response = requests.get(url, timeout=10, verify=False)

    if response.status_code != 200:
        logger.error(f"Failed to fetch data. Status code: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table")
    if not table:
        logger.error("Rate table not found.")
        return None

    rates = {}
    for row in table.find_all("tr"):
        cols = [c.text.strip() for c in row.find_all("td")]
        if len(cols) >= 2:
            date_str = re.sub(r"[-]", "/", cols[0])
            try:
                rate = float(cols[1])
                rates[date_str] = rate
            except ValueError:
                continue

    target_date = (datetime.today()).strftime("%Y/%m/%d")

    current_date = datetime.strptime(target_date, "%Y/%m/%d")
    while True:
        date_key = current_date.strftime("%Y/%m/%d")
        if date_key in rates:
            target_date = date_key
            logger.info(f"get_target_date(): Found rate for {date_key}: {rates[date_key]}")
            return rates[date_key], target_date
        else:
            current_date -= timedelta(days=1)
            if (datetime.today() - current_date).days > MAX_LOOKBACK_DAYS:
                logger.error(f"get_target_date(): No recent rate found within {MAX_LOOKBACK_DAYS} days.")
                return None

            logger.info(f"{date_key} get_target_date(): No data, try previous day.")

def get_latest_usdtwd_rate_from_cbc():
    url = "https://www.cbc.gov.tw/tw/lp-645-1.html"

    result = get_target_date(url)
    if result is None:
        logger.error("get_latest_usdtwd_rate_from_cbc(): Failed to get target date from CBC.")
        return None
    target_date = result[1]

    response = requests.get(url, timeout=10, verify=False)
    if response.status_code != 200:
        logger.error(f"get_latest_usdtwd_rate_from_cbc(): Failed to fetch CBC page. Status code: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    if not table:
        logger.error("get_latest_usdtwd_rate_from_cbc(): Failed to find the rate table on the CBC page.")
        return None

    snapshot_page(url, output_path=CBC_SNAPSHOT_PATH)

    target = re.sub(r"[-]", "/", target_date.strip())

    for row in table.find_all("tr"):
        cols = [c.text.strip() for c in row.find_all("td")]
        if len(cols) < 2:
            continue

        date_str = re.sub(r"[-]", "/", cols[0])
        if date_str == target:
            try:
                rate = float(cols[1])
                log_and_collect(f"get_latest_usdtwd_rate_from_cbc(): {target} USD/TWD rate: {rate}")
                return target, rate
            except ValueError:
                logger.error(f"get_latest_usdtwd_rate_from_cbc(): Error parsing rate value from {cols[1]}")
                return None

    logger.error(f"get_latest_usdtwd_rate_from_cbc(): No rate found for {target}")
    return None

def get_daily_rate_from_tcb():
    """Fetch TWD-based daily rates from TCB. Does not insert into database."""
    cbc_result = get_latest_usdtwd_rate_from_cbc()
    if not cbc_result:
        logger.error("get_daily_rate_from_tcb(): Failed to get USD/TWD rate from CBC.")
        return None

    target_date, usd_rate = cbc_result

    target_date = re.sub(r"[/]", "-", target_date.strip())
    url = f"https://rate.bot.com.tw/xrt/all/{target_date}"
    response = requests.get(url, timeout=10)

    if response.status_code != 200:
        logger.error(f"get_daily_rate_from_tcb(): Failed to fetch daily rates from TCB. Status code: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    if not table:
        logger.error("get_daily_rate_from_tcb(): Failed to find the rate table on the TCB page.")
        return None

    snapshot_page(url, output_path=TCB_SNAPSHOT_PATH)

    rates = {}

    for row in table.find_all("tr"):
        cols = [c.text.strip() for c in row.find_all("td")]
        if len(cols) < 5:
            continue

        match = re.search(r'\((\w+)\)', cols[0])
        if not match:
            continue
        currency = match.group(1)

        if currency not in TWD_CURRENCIES:
            continue
        if currency == 'PHP':
            cash_sell_val = cols[2].strip()
            if cash_sell_val:
                try:
                    php_rate = float(cash_sell_val.replace(',', ''))
                    rates[currency] = php_rate
                    log_and_collect(f"get_daily_rate_from_tcb(): {currency}: cash_sell={php_rate}")
                except ValueError:
                    logger.error(f"get_daily_rate_from_tcb(): Error parsing cash sell value for {currency}")
                    continue
            continue
        buy_val = cols[3].strip()
        sell_val = cols[4].strip()

        if buy_val and sell_val:
            try:
                spot_buy = float(buy_val.replace(',', ''))
                spot_sell = float(sell_val.replace(',', ''))
                avg_rate = round((spot_buy + spot_sell) / 2, 4)
                rates[currency] = avg_rate
                log_and_collect(f"get_daily_rate_from_tcb(): {currency}: buy={spot_buy}, sell={spot_sell}, avg={avg_rate}")
            except ValueError:
                logger.error(f"get_daily_rate_from_tcb(): Error parsing values for {currency}")
                continue

    return rates, usd_rate

# === Rate Calculation ===
def get_daily_rate_with_usd(rates, usd_rate):
    """Calculate USD-based rates from TWD-based rates. Does not insert into database."""
    conversion_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    rows = []
    rates_with_usd = {}
    for currency in USD_CURRENCIES:
        twd_rate = rates.get(currency, 0)
        # XXX/TWD ÷ USD/TWD = XXX/USD（例: EUR 36.77 / USD 31.683 = EUR→USD 1.16）
        combined_rate = round(twd_rate / usd_rate, 4) if usd_rate != 0 else 0
        rates_with_usd[currency] = combined_rate

        rate_val = combined_rate
        rev_rate_val = 1 / rate_val if rate_val != 0 else 0

        row_fwd = build_rate_rows(currency, 'USD', rate_val, conversion_date)
        rows.append(row_fwd)

        row_rev = build_rate_rows('USD', currency, rev_rate_val, conversion_date)
        rows.append(row_rev)

    logger.info(f"get_daily_rate_with_usd(): {rates_with_usd}, {conversion_date}")
    return rows, rates_with_usd, conversion_date

def build_rate_rows(from_currency, to_currency, rate, conversion_date):
    rev_rate = round(1 / rate, 6) if rate != 0 else 0

    return {
        'from_currency': from_currency,
        'to_currency': to_currency,
        'from_conversion_date': conversion_date,
        'to_conversion_date': conversion_date,
        'user_conversion_type': 'SC_銀行匯率',
        'conversion_rate': rate,
        'inverse_conversion_rate': rev_rate,
        'user_id': '1175',
        'launch_rate_change': 'Y',
        'batch_number': None
    }

def build_rate_table(rates, rates_with_usd, conversion_date):
    row_tmpl = _load_template('rate_row.html')

    twd_rows = ""
    for cur, rate in rates.items():
        rev_rate = round(1 / rate, 6) if rate != 0 else 0
        twd_rows += row_tmpl.substitute(
            from_currency=cur, to_currency='TWD',
            conversion_date=conversion_date,
            conversion_rate=f"{rate:.4f}",
            inverse_conversion_rate=f"{rev_rate:.6f}",
        )

    usd_rows = ""
    for cur, usd_rate in rates_with_usd.items():
        rev_rate = round(1 / usd_rate, 6) if usd_rate != 0 else 0
        usd_rows += row_tmpl.substitute(
            from_currency='USD', to_currency=cur,
            conversion_date=conversion_date,
            conversion_rate=f"{usd_rate:.4f}",
            inverse_conversion_rate=f"{rev_rate:.6f}",
        )

    table_tmpl = _load_template('rate_table.html')
    return table_tmpl.substitute(
        conversion_date=conversion_date,
        twd_rows=twd_rows,
        usd_rows=usd_rows,
    )

def query_oracle_bank_rates():
    """從 Oracle GL_DAILY_RATES_V 查詢最新寫入的 SC_銀行匯率 資料，用於 Email 通知表格。"""
    connection = None
    try:
        connection = connect_to_db_oracle()
        if connection is None:
            logger.warning("無法連線 Oracle，無法查詢匯率資料。")
            return None

        cursor = connection.cursor()
        sql = """
            SELECT FROM_CURRENCY, TO_CURRENCY, CONVERSION_DATE,
                   USER_CONVERSION_TYPE, CONVERSION_RATE
            FROM APPS.GL_DAILY_RATES_V
            WHERE TRUNC(CONVERSION_DATE) = TRUNC(SYSDATE + 1)
              AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
            ORDER BY FROM_CURRENCY, TO_CURRENCY
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()

        logger.info(f"Oracle GL_DAILY_RATES_V 查詢到 {len(rows)} 筆 SC_銀行匯率 資料")
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
        conv_date = row.get('CONVERSION_DATE') or row.get('FROM_DATE')
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

# === Database Operations (MERGE/Upsert) ===
def _execute_merge_rate(cursor, from_currency, to_currency, conversion_date,
                        rate, rev_rate, match_conversion_type=False):
    """Execute a MERGE upsert for a single rate pair."""
    user_conversion_type = 'SC_銀行匯率'

    extra_on = "AND target.user_conversion_type = ?" if match_conversion_type else ""

    merge_sql = f"""
        MERGE SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE AS target
        USING (SELECT ? AS from_currency, ? AS to_currency, ? AS from_conversion_date) AS src
        ON (
            target.from_currency = src.from_currency
            AND target.to_currency = src.to_currency
            AND target.from_conversion_date = src.from_conversion_date
            {extra_on}
        )
        WHEN MATCHED THEN
            UPDATE SET
                target.conversion_rate = ?,
                target.inverse_conversion_rate = ?,
                target.insert_time = GETDATE(),
                target.user_id = ?,
                target.launch_rate_change = ?
        WHEN NOT MATCHED THEN
            INSERT (
                from_currency, to_currency, from_conversion_date, to_conversion_date,
                user_conversion_type, conversion_rate, mode_flag, inverse_conversion_rate,
                user_id, launch_rate_change, batch_number, insert_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE());
    """

    base_params = [from_currency, to_currency, conversion_date]
    if match_conversion_type:
        base_params.append(user_conversion_type)

    params = tuple(base_params + [
        rate, rev_rate, '1175', 'Y',
        from_currency, to_currency, conversion_date, conversion_date, user_conversion_type,
        rate, 'I', rev_rate, '1175', 'Y', None
    ])

    cursor.execute(merge_sql, params)

def insert_daily_rate(rates, usd_rate):
    """Insert TWD-based rates into SQL Server."""
    combined_rates = rates.copy()
    combined_rates['USD'] = usd_rate

    conversion_date = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    connection = connect_to_db_sql()
    if connection is None:
        logger.error("無法建立資料庫連線，請檢查 db_config.yaml 與網路連線。")
        raise ConnectionError("Database connection failed.")

    cursor = connection.cursor()

    try:
        logger.info(f"開始寫入 {len(combined_rates)} 筆匯率資料...")

        for currency, rate in combined_rates.items():
            rev_rate = 1 / rate if rate != 0 else 0
            _execute_merge_rate(cursor, currency, 'TWD', conversion_date, rate, rev_rate)
            _execute_merge_rate(cursor, 'TWD', currency, conversion_date, rev_rate, rate)

        connection.commit()
        logger.info(f"insert_daily_rate(): 匯率資料 Upsert 成功 (日期: {conversion_date})")

    except Exception as e:
        logger.error("Error while insert data: %s", e)
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

def insert_usd_rates(rates_with_usd, conversion_date):
    """Insert all USD-based rate pairs in a single transaction."""
    connection = connect_to_db_sql()
    if connection is None:
        logger.error("無法建立資料庫連線，請檢查 db_config.yaml")
        raise ConnectionError("Database connection failed.")

    cursor = connection.cursor()

    try:
        for currency, rate_val in rates_with_usd.items():
            rev_rate_val = 1 / rate_val if rate_val != 0 else 0
            _execute_merge_rate(cursor, currency, 'USD', conversion_date,
                                rate_val, rev_rate_val, match_conversion_type=True)
            _execute_merge_rate(cursor, 'USD', currency, conversion_date,
                                rev_rate_val, rate_val, match_conversion_type=True)

        connection.commit()
        logger.info(f"insert_usd_rates(): USD-based rates upsert completed ({len(rates_with_usd)} currencies)")
    except Exception as e:
        logger.error(f"Error while inserting USD rate data: {e}")
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

def insert_to_oracle(rates, usd_rate, usd_rows, conversion_date):
    """
    將匯率資料寫入 Oracle Table: YSC.SGLP0021_TEMP
    包含 TWD-based 及 USD-based 匯率（正向+反向）
    失敗時拋出例外，由主流程統一處理。
    回傳本次寫入的資料 list[dict]，供 Email 通知使用。

    conversion_date: 匯率適用日期字串 (YYYY-MM-DD)，與 SQL Server 一致。
    """
    connection = None
    try:
        connection = connect_to_db_oracle()
        if connection is None:
            raise ConnectionError("Oracle 連線失敗。")

        oracle_rows = []
        inserted_data = []
        conv_dt = datetime.strptime(conversion_date, '%Y-%m-%d')
        now = datetime.now()

        combined_rates = rates.copy()
        combined_rates['USD'] = usd_rate

        # 只寫入正向匯率（XXX→TWD），不寫入反向（TWD→XXX）
        for currency, rate in combined_rates.items():
            oracle_rows.append((
                'TW_BANK', currency, 'TWD', conv_dt, conv_dt,
                rate, 'I', now, 'N', 'SC_銀行匯率'
            ))
            inserted_data.append({
                'FROM_CURRENCY': currency, 'TO_CURRENCY': 'TWD',
                'FROM_DATE': conv_dt, 'USER_CONVERSION_TYPE': 'SC_銀行匯率',
                'CONVERSION_RATE': rate,
            })

        # USD-based: 只寫入正向（XXX→USD），過濾反向（USD→XXX）
        for row in usd_rows:
            if row['to_currency'] == 'USD':
                oracle_rows.append((
                    'TW_BANK', row['from_currency'], row['to_currency'], conv_dt, conv_dt,
                    row['conversion_rate'], 'I', now, 'N', 'SC_銀行匯率'
                ))
                inserted_data.append({
                    'FROM_CURRENCY': row['from_currency'],
                    'TO_CURRENCY': row['to_currency'],
                    'FROM_DATE': conv_dt, 'USER_CONVERSION_TYPE': 'SC_銀行匯率',
                    'CONVERSION_RATE': row['conversion_rate'],
                })

        # 先刪除同日的 SC_銀行匯率 資料，避免重複
        cursor = connection.cursor()
        delete_sql = """
            DELETE FROM YSC.SGLP0021_TEMP
            WHERE TRUNC(FROM_DATE) = TRUNC(:1)
              AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
        """
        cursor.execute(delete_sql, [conv_dt])
        deleted_count = cursor.rowcount
        logger.info(f"已刪除 Oracle 同日 SC_銀行匯率 {deleted_count} 筆")

        insert_sql = """
            INSERT INTO YSC.SGLP0021_TEMP (
                SOURCE_FROM, FROM_CURRENCY, TO_CURRENCY, FROM_DATE, TO_DATE,
                CONVERSION_RATE, MODE_FLAG, IMPORTED_DATE, IMPORTED_FLAG,
                USER_CONVERSION_TYPE
            ) VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10)
        """

        cursor.executemany(insert_sql, oracle_rows)
        connection.commit()
        logger.info(f"成功寫入 Oracle 資料庫 (YSC.SGLP0021_TEMP)，共 {len(oracle_rows)} 筆")

        # 執行 sglp0021_pkg.main 將 TEMP 資料轉入正式表
        logger.info("開始執行 sglp0021_pkg.main ...")
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
        logger.info(f"sglp0021_pkg.main 執行完成。retcode={retcode_val}, errbuf={errbuf_val}")

        if str(retcode_val) != '0':
            raise RuntimeError(f"sglp0021_pkg.main 執行失敗 (retcode={retcode_val}): {errbuf_val}")

        cursor.close()

        return inserted_data

    except Exception as e:
        logger.error(f"Oracle 寫入失敗: {e}")
        if connection:
            connection.rollback()
        raise
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

# === Report Generation ===
def export_and_email_rates(
    web_rates=None, web_usd_rate=None, web_usd_rows=None,
    step_results=None,
    oracle_inserted_data=None,
    excel_attachment_folder=f"./data/daily/{datetime.now().strftime('%y%m%d')}"
):
    """
    從資料庫查詢今日的匯率，生成 Excel 檔案（三個 Sheet）並以郵件寄送。
    Sheet 1: Web Source（爬取原始資料）
    Sheet 2: SQL Server（資料庫資料）
    Sheet 3: Oracle ERP（Oracle 資料）
    """
    connection = None
    try:
        # === Sheet 1: Web Source ===
        df_web = pd.DataFrame()
        if web_rates and web_usd_rate is not None:
            conversion_date_str = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
            web_rows = []
            for currency, rate in web_rates.items():
                rev_rate = round(1 / rate, 6) if rate != 0 else 0
                web_rows.append({
                    'from_currency': currency,
                    'to_currency': 'TWD',
                    'from_conversion_date': conversion_date_str,
                    'conversion_rate': rate,
                    'inverse_conversion_rate': rev_rate,
                })
            if web_usd_rows:
                for row in web_usd_rows:
                    if row['from_currency'] not in ('TWD', 'USD'):
                        web_rows.append({
                            'from_currency': row['from_currency'],
                            'to_currency': row['to_currency'],
                            'from_conversion_date': row['from_conversion_date'],
                            'conversion_rate': row['conversion_rate'],
                            'inverse_conversion_rate': row['inverse_conversion_rate'],
                        })
            df_web = pd.DataFrame(web_rows)

        # === Sheet 2: SQL Server ===
        connection = connect_to_db_sql()
        sql_query = """
        SELECT
            from_currency,
            to_currency,
            from_conversion_date,
            conversion_rate,
            inverse_conversion_rate
        FROM
            SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
        WHERE
            from_conversion_date = (
                SELECT MAX(from_conversion_date)
                FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
                WHERE user_conversion_type = N'SC_銀行匯率'
            )
            AND user_conversion_type = N'SC_銀行匯率'
        ORDER BY
            from_currency, to_currency;
        """

        cursor = connection.cursor()
        cursor.execute(sql_query)
        sql_rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        connection.close()
        connection = None
        df_sql = pd.DataFrame(sql_rows, columns=columns)
        df_sql = df_sql[~df_sql['from_currency'].isin(['TWD', 'USD'])]

        if df_sql.empty:
            logger.warning("No daily rate data found for today's conversion date.")
            return

        actual_date = df_sql['from_conversion_date'].iloc[0]

        # === Sheet 3: Oracle ERP (GL_DAILY_RATES_V) ===
        df_oracle = pd.DataFrame()
        oracle_conn = None
        try:
            oracle_conn = connect_to_db_oracle()
            if oracle_conn:
                oracle_cursor = oracle_conn.cursor()
                oracle_sql = """
                    SELECT FROM_CURRENCY, TO_CURRENCY, CONVERSION_DATE,
                           USER_CONVERSION_TYPE, CONVERSION_RATE
                    FROM APPS.GL_DAILY_RATES_V
                    WHERE TRUNC(CONVERSION_DATE) = TRUNC(SYSDATE + 1)
                      AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
                    ORDER BY FROM_CURRENCY, TO_CURRENCY
                """
                oracle_cursor.execute(oracle_sql)
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

        # === 產生 Excel 檔案（三個 Sheet）===
        current_time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        excel_file_name = f"Daily_Exchange_Rates_CNY_EUR_GBP_CAD_AUD_JPY_PHP_USD_{current_time_str}.xlsx"

        if not os.path.exists(excel_attachment_folder):
            os.makedirs(excel_attachment_folder)

        excel_file_path = os.path.join(excel_attachment_folder, excel_file_name)

        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            if not df_web.empty:
                df_web.to_excel(writer, index=False, sheet_name='Web Source')
            else:
                pd.DataFrame().to_excel(writer, index=False, sheet_name='Web Source')
            df_sql.to_excel(writer, index=False, sheet_name='SQL Server')
            if not df_oracle.empty:
                df_oracle.to_excel(writer, index=False, sheet_name='Oracle ERP')
            else:
                pd.DataFrame().to_excel(writer, index=False, sheet_name='Oracle ERP')

        logger.info(f"Successfully created Excel file: {excel_file_path}")

        subject = f"(測試信)【匯率報告】每日銀行匯率報告 - {actual_date}"

        # 使用本次執行匯入 Oracle 的資料建構 Email 表格
        if oracle_inserted_data:
            rate_table_html = build_oracle_rate_table(oracle_inserted_data)
        elif web_rates and web_usd_rate is not None:
            _, rates_with_usd, conversion_date = get_daily_rate_with_usd(web_rates, web_usd_rate)
            rate_table_html = build_rate_table(web_rates, rates_with_usd, conversion_date)
        else:
            rates, usd_rate = get_daily_rate_from_tcb() or ({}, 0)
            _, rates_with_usd, conversion_date = get_daily_rate_with_usd(rates, usd_rate)
            rate_table_html = build_rate_table(rates, rates_with_usd, conversion_date)

        step_status_html = ""
        topic_html = ""
        if step_results is not None:
            step_results.append(("Step 5: 報表寄送", "Excel 產生完成，Email 寄送中"))
            step_status_html = _build_step_status_html(step_results)
            step_total = len(step_results)
            step_success = sum(1 for _, r in step_results if not r.startswith("失敗"))
            topic_tmpl = _load_template('topic_daily.html')
            topic_html = topic_tmpl.substitute(
                step_total=step_total,
                step_success=step_success,
            )

        body_text = f"""
            {topic_html}
            <p>此報告使用的匯率基準日為：{actual_date} (最新交易日)。</p>
            {rate_table_html}
            {step_status_html}
        """

        success = send_email_notification(
            subject=subject,
            body=body_text,
            file_path=excel_file_path,
            mailtype="success",
            image_path=TCB_SNAPSHOT_PATH,
            additional_image_path=CBC_SNAPSHOT_PATH
        )
        if success:
            logger.info(f"Excel report kept at: {excel_file_path}")

    except Exception as e:
        logger.error(f"Error during export: {e}")
        raise
    finally:
        if connection:
            connection.close()

# === Main Entry Point ===
logger = logging.getLogger(__name__)

def run():
    """
    執行每日匯率抓取與寫入流程，回傳結果 dict 供主程式彙整。
    回傳格式:
    {
        'success': bool,
        'step_results': list of (step_name, result_text),
        'rates': dict,           # TWD-based rates
        'usd_rate': float,
        'usd_rows': list,        # USD-based rate rows
        'rates_with_usd': dict,
        'conversion_date': str,
        'oracle_inserted_data': list or None,
        'snapshot_paths': dict,   # cid_name -> file_path
        'excel_file_path': str or None,
        'error': str or None,
    }
    """
    setup_logging(log_dir='./logs')
    logger.info("--- Daily Rate Process Started ---")

    current_step = ""
    step_results = []
    result_data = {
        'success': False,
        'step_results': step_results,
        'rates': None,
        'usd_rate': None,
        'usd_rows': None,
        'rates_with_usd': None,
        'conversion_date': None,
        'oracle_inserted_data': None,
        'snapshot_paths': {},
        'excel_file_path': None,
        'error': None,
    }

    try:
        # ===== Step 1: 抓取匯率 =====
        current_step = "Step 1: 抓取匯率（Web Scraping）"
        result = get_daily_rate_from_tcb()
        if result is None:
            raise RuntimeError("無法從銀行網頁抓取相關匯率數據。")

        rates, usd_rate = result
        step_results.append(("Step 1: 抓取匯率", f"取得 {len(rates)} 種幣別匯率，USD/TWD = {usd_rate}"))

        # ===== Step 2: SQL Server - TWD-based 寫入 =====
        current_step = "Step 2: SQL Server TWD-based 匯率寫入"
        insert_daily_rate(rates, usd_rate)
        twd_count = len(rates) + 1  # +1 for USD
        conversion_date_display = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        step_results.append(("Step 2: SQL Server TWD", f"{twd_count} 筆匯率 Upsert 成功（日期: {conversion_date_display}）"))

        # ===== Step 3: SQL Server - USD-based 計算與寫入 =====
        current_step = "Step 3: SQL Server USD-based 匯率寫入"
        logger.info("--- Starting USD Rates Calculation and Update ---")
        rows, rates_with_usd, conversion_date = get_daily_rate_with_usd(rates, usd_rate)
        insert_usd_rates(rates_with_usd, conversion_date)
        step_results.append(("Step 3: SQL Server USD", f"{len(rates_with_usd)} 筆 USD-based 匯率寫入完成"))

        # ===== Step 4: Oracle 寫入 =====
        current_step = "Step 4: Oracle 匯率寫入"
        logger.info("--- Starting Oracle DB Insert ---")
        oracle_inserted_data = insert_to_oracle(rates, usd_rate, rows, conversion_date)
        oracle_count = len(oracle_inserted_data)
        logger.info("Oracle 寫入完成。")
        step_results.append(("Step 4: Oracle", f"{oracle_count} 筆寫入成功"))

        # ===== Step 5: 產生 Excel 報表（不寄送 Email）=====
        current_step = "Step 5: 產生報表"
        excel_file_path = _export_daily_excel(rates, usd_rate, rows, oracle_inserted_data)
        step_results.append(("Step 5: 產生報表", "Excel 產生完成"))

        result_data.update({
            'success': True,
            'rates': rates,
            'usd_rate': usd_rate,
            'usd_rows': rows,
            'rates_with_usd': rates_with_usd,
            'conversion_date': conversion_date,
            'oracle_inserted_data': oracle_inserted_data,
            'snapshot_paths': {
                'tcb_img': TCB_SNAPSHOT_PATH,
                'cbc_img': CBC_SNAPSHOT_PATH,
            },
            'excel_file_path': excel_file_path,
        })

    except Exception as e:
        logger.error(f"工作執行失敗 [{current_step}]: {e}")
        step_results.append((current_step, f"失敗 - {e}"))
        result_data['error'] = str(e)

    logger.info("--- Daily Rate Process Finished ---")
    return result_data


def _export_daily_excel(rates, usd_rate, usd_rows, oracle_inserted_data,
                        excel_attachment_folder=None):
    """產生 Daily Excel 報表（三個 Sheet），回傳檔案路徑。"""
    if excel_attachment_folder is None:
        excel_attachment_folder = f"./data/daily/{datetime.now().strftime('%y%m%d')}"

    connection = None
    try:
        # === Sheet 1: Web Source ===
        conversion_date_str = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        web_rows = []
        for currency, rate in rates.items():
            rev_rate = round(1 / rate, 6) if rate != 0 else 0
            web_rows.append({
                'from_currency': currency,
                'to_currency': 'TWD',
                'from_conversion_date': conversion_date_str,
                'conversion_rate': rate,
                'inverse_conversion_rate': rev_rate,
            })
        if usd_rows:
            for row in usd_rows:
                if row['from_currency'] not in ('TWD', 'USD'):
                    web_rows.append({
                        'from_currency': row['from_currency'],
                        'to_currency': row['to_currency'],
                        'from_conversion_date': row['from_conversion_date'],
                        'conversion_rate': row['conversion_rate'],
                        'inverse_conversion_rate': row['inverse_conversion_rate'],
                    })
        df_web = pd.DataFrame(web_rows)

        # === Sheet 2: SQL Server ===
        connection = connect_to_db_sql()
        sql_query = """
        SELECT
            from_currency, to_currency, from_conversion_date,
            conversion_rate, inverse_conversion_rate
        FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
        WHERE from_conversion_date = (
                SELECT MAX(from_conversion_date)
                FROM SERVICE_TEST.dbo.NOTES_SIM_GL_DAILY_RATES_INTERFACE
                WHERE user_conversion_type = N'SC_銀行匯率'
            )
            AND user_conversion_type = N'SC_銀行匯率'
        ORDER BY from_currency, to_currency;
        """
        cursor = connection.cursor()
        cursor.execute(sql_query)
        sql_rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        connection.close()
        connection = None
        df_sql = pd.DataFrame(sql_rows, columns=columns)
        df_sql = df_sql[~df_sql['from_currency'].isin(['TWD', 'USD'])]

        # === Sheet 3: Oracle ERP (GL_DAILY_RATES_V) ===
        df_oracle = pd.DataFrame()
        oracle_conn = None
        try:
            oracle_conn = connect_to_db_oracle()
            if oracle_conn:
                oracle_cursor = oracle_conn.cursor()
                oracle_sql = """
                    SELECT FROM_CURRENCY, TO_CURRENCY, CONVERSION_DATE,
                           USER_CONVERSION_TYPE, CONVERSION_RATE
                    FROM APPS.GL_DAILY_RATES_V
                    WHERE TRUNC(CONVERSION_DATE) = TRUNC(SYSDATE + 1)
                      AND USER_CONVERSION_TYPE = 'SC_銀行匯率'
                    ORDER BY FROM_CURRENCY, TO_CURRENCY
                """
                oracle_cursor.execute(oracle_sql)
                oracle_rows_db = oracle_cursor.fetchall()
                oracle_columns = [desc[0] for desc in oracle_cursor.description]
                oracle_cursor.close()
                df_oracle = pd.DataFrame(oracle_rows_db, columns=oracle_columns)
        except Exception as e:
            logger.warning(f"Oracle 查詢失敗，Oracle ERP sheet 將為空白: {e}")
        finally:
            if oracle_conn:
                oracle_conn.close()

        # === 產生 Excel 檔案 ===
        os.makedirs(excel_attachment_folder, exist_ok=True)
        current_time_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        excel_file_name = f"Daily_Exchange_Rates_CNY_EUR_GBP_CAD_AUD_JPY_PHP_USD_{current_time_str}.xlsx"
        excel_file_path = os.path.join(excel_attachment_folder, excel_file_name)

        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            if not df_web.empty:
                df_web.to_excel(writer, index=False, sheet_name='Web Source')
            else:
                pd.DataFrame().to_excel(writer, index=False, sheet_name='Web Source')
            df_sql.to_excel(writer, index=False, sheet_name='SQL Server')
            if not df_oracle.empty:
                df_oracle.to_excel(writer, index=False, sheet_name='Oracle ERP')
            else:
                pd.DataFrame().to_excel(writer, index=False, sheet_name='Oracle ERP')

        logger.info(f"Successfully created Excel file: {excel_file_path}")
        return excel_file_path

    except Exception as e:
        logger.error(f"Error during export: {e}")
        raise
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    result = run()
    if not result['success']:
        # 獨立執行時，失敗寄送錯誤通知
        status_html = _build_step_status_html(result['step_results'])
        send_email_notification(
            subject=f"【錯誤報告】每日匯率程式異常 - {datetime.now().strftime('%Y-%m-%d')}",
            body=f"<p>程式執行失敗。</p>"
                 f"<p>錯誤訊息：</p><pre>{result['error']}</pre>"
                 f"<br>{status_html}",
            file_path=None,
            table_html=None,
            mailtype="error"
        )
