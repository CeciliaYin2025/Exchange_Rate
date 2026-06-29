import os
os.environ['SKIP_EMAIL'] = '1'

import ImportDailyRate as idr

idr.setup_logging('./logs')

result = idr.get_daily_rate_from_tcb()
if result is None:
    print("失敗：抓不到匯率")
else:
    rates, usd_rate = result
    print(f"USD/TWD = {usd_rate}")
    print(rates)
