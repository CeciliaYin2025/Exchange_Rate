"""
模擬錯誤情境並發送實際的錯誤通知 Email。
情境：Step 1 抓取匯率成功，Step 2 SQL Server 寫入失敗。

用法：
    python test/simulate_error_email.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from ImportDailyRate import (
    _build_step_status_html,
    send_email_notification,
    setup_logging,
)

def main():
    setup_logging(log_dir='./logs')

    # 模擬 Step 1 成功
    step_results = [
        ("Step 1: 抓取匯率", "取得 7 種幣別匯率，USD/TWD = 31.518"),
    ]

    # 模擬 Step 2 失敗
    current_step = "Step 2: SQL Server TWD-based 匯率寫入"
    simulated_error = (
        "[Microsoft][ODBC Driver 17 for SQL Server]"
        "Login failed for user 'sa'. "
        "Reason: Password did not match that for the login provided. "
        "[SQL Server]Error Severity: 14, State: 1"
    )
    step_results.append((current_step, f"失敗 - {simulated_error}"))

    # 組裝 error email 內容（與 main 流程一致）
    status_html = _build_step_status_html(step_results)

    subject = f"(模擬測試)【錯誤報告】每日匯率程式異常 - {datetime.now().strftime('%Y-%m-%d')}"
    body = (
        f"<p>程式於 <b>{current_step}</b> 階段執行失敗。</p>"
        f"<p>錯誤訊息：</p><pre>{simulated_error}</pre>"
        f"<br>{status_html}"
    )

    print(f"Subject: {subject}")
    print(f"Sending error email to failure recipients...")

    result = send_email_notification(
        subject=subject,
        body=body,
        file_path=None,
        table_html=None,
        mailtype="error",
    )

    if result:
        print("Email sent successfully!")
    else:
        print("Email sending failed!")

    return result


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
