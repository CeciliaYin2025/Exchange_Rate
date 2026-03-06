# === RunAllRates.py ===
# 主程式：依序執行 ImportDailyRate 和 ImportWebRate，
# 將兩個執行結果合併後，以一封 Email 統一通知。

import logging
import os
import smtplib
from datetime import date, datetime, timedelta
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from string import Template

import oracledb
import yaml

import ImportDailyRate
import ImportWebRate


# === Constants ===
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'config', 'html', 'template')

# === Logging ===
logger = logging.getLogger('RunAllRates')


def _setup_logging(log_dir='./logs'):
    os.makedirs(log_dir, exist_ok=True)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        date_str = datetime.now().strftime('%Y%m%d')
        log_path = os.path.join(log_dir, f"run_all_{date_str}.log")
        fh = logging.FileHandler(log_path, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(module)s - %(message)s'))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(ch)


def _load_template(name):
    path = os.path.join(TEMPLATE_DIR, name)
    with open(path, 'r', encoding='utf-8') as f:
        return Template(f.read())


def _load_email_config():
    with open('config/email_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config['email']


# === Step Status HTML ===
def _build_step_status_html(step_results):
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


# === Oracle Connection ===
def _connect_to_db_oracle():
    with open('config/db_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    db_config = config['oracle_db_insert']
    client_lib_dir = os.environ.get('ORACLE_CLIENT_LIB_DIR') or db_config.get('client_lib_dir')
    try:
        oracledb.init_oracle_client(lib_dir=client_lib_dir)
    except oracledb.ProgrammingError:
        pass
    try:
        dsn = f"{db_config['host']}:{db_config['port']}/{db_config['service_name']}"
        return oracledb.connect(user=db_config['user'], password=db_config['password'], dsn=dsn)
    except Exception as e:
        logger.error(f"Oracle connection error: {e}")
        return None


def _query_gl_daily_rates_v():
    """從 Oracle GL_DAILY_RATES_V 查詢所有 SC_銀行匯率 資料，用於合併 Email 表格。"""
    connection = None
    try:
        connection = _connect_to_db_oracle()
        if connection is None:
            logger.warning("無法連線 Oracle，無法查詢 GL_DAILY_RATES_V。")
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

        logger.info(f"GL_DAILY_RATES_V 查詢到 {len(rows)} 筆 SC_銀行匯率 資料")
        return [dict(zip(columns, row)) for row in rows]

    except Exception as e:
        logger.warning(f"GL_DAILY_RATES_V 查詢失敗: {e}")
        return None
    finally:
        if connection:
            connection.close()


def _build_combined_rate_table(oracle_rows):
    """使用 GL_DAILY_RATES_V 查詢結果建構合併的「每日SC_銀行匯率」HTML 表格。"""
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

    return f"""
    <h3 id="combined-rates">每日SC_銀行匯率</h3>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse;font-family:Consolas, monospace;margin-bottom:20px;">
        <thead>
            <tr style="background-color:#e6f3ff">
                <th>Currency: From</th>
                <th>Currency: To</th>
                <th>Conversion: Date</th>
                <th>Conversion: Type</th>
                <th>Conversion Rate</th>
                <th>Inverse Conversion Rate</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    """


# === Combined Email ===
def _send_combined_email(daily_result, web_result):
    """合併 Daily 和 Web 的執行結果，寄送一封統一通知 Email。"""

    email_config = _load_email_config()
    sender_email = email_config['sender_email']
    smtp_server = email_config['smtp_server']
    port = email_config['port']

    # 判斷整體成敗
    all_success = daily_result['success'] and web_result['success']
    if all_success:
        receiver_email = email_config['receiver_emails_success']
        mailtype = "success"
    else:
        receiver_email = email_config['receiver_emails_failure']
        mailtype = "error"

    # === 合併 step_results（加上分組標題）===
    combined_steps = []
    combined_steps.append(("【每日匯率 Daily - CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD】", "---"))
    combined_steps.extend(daily_result['step_results'])
    combined_steps.append(("【每日匯率 Daily - INR, MXN, TRY】", "---"))
    combined_steps.extend(web_result['step_results'])

    # === 從 GL_DAILY_RATES_V 查詢合併匯率表格 ===
    rate_tables_html = ""
    oracle_rows = _query_gl_daily_rates_v()
    if oracle_rows:
        rate_tables_html = _build_combined_rate_table(oracle_rows)
    else:
        logger.warning("無法從 GL_DAILY_RATES_V 取得資料，使用本地執行結果建構表格。")
        # Fallback: 使用本地 inserted_data 合併
        fallback_rows = []
        if daily_result['success'] and daily_result.get('oracle_inserted_data'):
            fallback_rows.extend(daily_result['oracle_inserted_data'])
        if web_result['success'] and web_result.get('oracle_inserted_data'):
            fallback_rows.extend(web_result['oracle_inserted_data'])
        if fallback_rows:
            rate_tables_html = _build_combined_rate_table(fallback_rows)

    # === Step status ===
    step_status_html = _build_step_status_html(combined_steps)

    # === Topic (目錄) ===
    step_total = len([s for s in combined_steps if s[1] != "---"])
    step_success = sum(1 for _, r in combined_steps if r != "---" and not r.startswith("失敗"))
    topic_tmpl = _load_template('topic_combined.html')
    topic_html = topic_tmpl.substitute(step_total=step_total, step_success=step_success)

    # === 錯誤訊息（如有）===
    error_html = ""
    if not daily_result['success']:
        error_html += f"<p style='color:red;'>Daily 執行失敗：{daily_result['error']}</p>"
    if not web_result['success']:
        error_html += f"<p style='color:red;'>Web 執行失敗：{web_result['error']}</p>"

    # === 組合 Email body ===
    conversion_date_display = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    body_text = f"""
        {topic_html}
        {error_html}
        <p>匯率基準日：{conversion_date_display}</p>
        {rate_tables_html}
        {step_status_html}
    """

    # === 建構 Email ===
    msg = MIMEMultipart('related')
    msg['From'] = sender_email
    receiver_str = ", ".join(receiver_email) if isinstance(receiver_email, list) else receiver_email
    msg['To'] = receiver_str

    if all_success:
        subject = f"(測試信)【匯率報告】每日匯率綜合報告 - {conversion_date_display}"
    else:
        subject = f"【錯誤報告】每日匯率程式異常 - {datetime.now().strftime('%Y-%m-%d')}"
        msg['X-Priority'] = '1'
        msg['Importance'] = 'High'

    msg['Subject'] = Header(subject, 'utf-8')

    # === Inline images ===
    all_snapshots = {}
    image_html = ""

    # Daily snapshots
    daily_snaps = daily_result.get('snapshot_paths', {})
    if daily_snaps:
        image_html += '<h3 id="web-snapshots">網頁截圖</h3>'
        if 'tcb_img' in daily_snaps:
            image_html += '<p>TCB 網頁截圖：</p><img src="cid:tcb_img" style="max-width:800px;"><br>'
            all_snapshots['tcb_img'] = daily_snaps['tcb_img']
        if 'cbc_img' in daily_snaps:
            image_html += '<p>CBC 網頁截圖：</p><img src="cid:cbc_img" style="max-width:800px;"><br>'
            all_snapshots['cbc_img'] = daily_snaps['cbc_img']

    # Web snapshots
    web_snaps = web_result.get('snapshot_paths', {})
    if web_snaps:
        if not image_html:
            image_html += '<h3 id="web-snapshots">網頁截圖</h3>'
        for cid_name, label in [('inr_img', 'INR (USD/INR)'), ('mxn_img', 'MXN (USD/MXN)'), ('try_img', 'TRY (TRY/TWD)')]:
            if cid_name in web_snaps:
                image_html += f'<p>{label}:</p><img src="cid:{cid_name}" style="max-width:600px;"><br>'
                all_snapshots[cid_name] = web_snaps[cid_name]

    # === 組合最終 HTML ===
    tmpl = _load_template('email_body.html')
    final_body = tmpl.substitute(
        body=body_text,
        table_html="",
        image_html=image_html,
    )

    msg_alternative = MIMEMultipart('alternative')
    msg.attach(msg_alternative)
    msg_alternative.attach(MIMEText(final_body, 'html', 'utf-8'))

    # Attach inline images
    for cid_name, path in all_snapshots.items():
        if os.path.exists(path):
            with open(path, 'rb') as f:
                img = MIMEImage(f.read())
                img.add_header('Content-ID', f'<{cid_name}>')
                img.add_header('Content-Disposition', 'inline', filename=os.path.basename(path))
                msg.attach(img)

    # Attach Excel files
    for label, result in [("Daily", daily_result), ("Web", web_result)]:
        excel_path = result.get('excel_file_path')
        if excel_path and os.path.exists(excel_path):
            filename = os.path.basename(excel_path)
            with open(excel_path, 'rb') as f:
                attachment = MIMEApplication(f.read(), _subtype='xlsx')
                attachment.add_header('Content-Disposition', 'attachment', filename=filename)
                msg.attach(attachment)

    # === 寄送 ===
    try:
        with smtplib.SMTP(smtp_server, port) as server:
            server.sendmail(sender_email, receiver_email, msg.as_string())
            logger.info("Combined email sent successfully.")
        return True
    except smtplib.SMTPException as e:
        logger.error(f"Failed to send combined email: {e}")
        return False


# === Main ===
if __name__ == "__main__":
    _setup_logging()
    logger.info("=== RunAllRates Started ===")

    # Step 1: 執行每日匯率
    logger.info("--- Running ImportDailyRate ---")
    daily_result = ImportDailyRate.run()
    logger.info(f"ImportDailyRate finished. Success: {daily_result['success']}")

    # Step 2: 執行月匯率
    logger.info("--- Running ImportWebRate ---")
    web_result = ImportWebRate.run()
    logger.info(f"ImportWebRate finished. Success: {web_result['success']}")

    # Step 3: 合併結果寄送 Email
    logger.info("--- Sending Combined Email ---")
    _send_combined_email(daily_result, web_result)

    logger.info("=== RunAllRates Finished ===")
