import os
os.environ['SKIP_EMAIL'] = '1'

from datetime import date, timedelta
import ImportDailyRate as idr

idr.setup_logging('./logs')

# 先從 CBC 取得有效日期
cbc_result = idr.get_latest_usdtwd_rate_from_cbc()
if cbc_result is None:
    print("無法取得 CBC 日期，改用昨天")
    target_date = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
else:
    target_date, usd_rate = cbc_result
    target_date = target_date.replace('/', '-')
    print(f"CBC 日期: {target_date}, USD/TWD = {usd_rate}")

url = f"https://rate.bot.com.tw/xrt/all/{target_date}"
output_path = f"./data/daily/test/tcb_snapshot_test.jpg"

print(f"截圖 URL: {url}")
print(f"輸出路徑: {output_path}")

result = idr.snapshot_page(url, output_path)
if result and os.path.exists(result):
    size_kb = os.path.getsize(result) // 1024
    print(f"截圖成功：{result}（{size_kb} KB）")
else:
    print("截圖失敗")
