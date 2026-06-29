from datetime import datetime, timedelta
import pendulum
from airflow import DAG
from airflow.providers.ssh.operators.ssh import SSHOperator

LOCAL_TZ = pendulum.timezone('Asia/Taipei')

PROJECT_DIR = '/Projects/Exchange_Rate'
SSH_CONN_ID = 'Exchange_Rate_Server'

default_args = {
    'owner': 'airflow',
    'retries': 0,
    'retry_delay': timedelta(minutes=15),
}

with DAG(
    dag_id='exchange_rate_daily',
    default_args=default_args,
    description='每天 17:30 執行匯率擷取 (銀行匯率 + 三旬匯率)',
    schedule_interval='30 17 * * *',
    start_date=datetime(2025, 1, 1, tzinfo=LOCAL_TZ),
    catchup=False,
    tags=['exchange_rate'],
) as dag:

    run_bank = SSHOperator(
        task_id='import_bank_rate',
        ssh_conn_id=SSH_CONN_ID,
        command=f'cd {PROJECT_DIR} && docker compose run --rm exchange-rate bank',
        cmd_timeout=600,
        conn_timeout=30,
        retries=1,
        retry_delay=timedelta(minutes=5),
    )

    run_portal = SSHOperator(
        task_id='import_portal_rate',
        ssh_conn_id=SSH_CONN_ID,
        command=f'cd {PROJECT_DIR} && docker compose run --rm exchange-rate portal',
        cmd_timeout=600,
        conn_timeout=30,
    )

    # 循序執行：銀行匯率完成後再執行三旬匯率，避免 sglp0021_pkg.main 同時執行衝突
    run_bank >> run_portal
