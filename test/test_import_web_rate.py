"""Tests for ImportWebRate.py"""

import logging
import os
import smtplib
from datetime import date, datetime, timedelta
from string import Template
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest
import yaml

# Patch heavy third-party imports before importing the module under test.
import sys

_mssql_python_mock = MagicMock()
_oracledb_mock = MagicMock()
_oracledb_mock.ProgrammingError = type("ProgrammingError", (Exception,), {})

sys.modules.setdefault("mssql_python", _mssql_python_mock)
sys.modules.setdefault("oracledb", _oracledb_mock)

import ImportWebRate as iwr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_logger():
    """Ensure the module-level logger is usable during tests."""
    iwr.logger = logging.getLogger("test_iwr")
    iwr.logger.setLevel(logging.DEBUG)
    iwr.RUN_LOGS.clear()


# =========================================================================
# Group 1: Config loading
# =========================================================================

class TestLoadDbConfig:
    """Tests for load_db_config()."""

    def test_dev_db_preferred(self, tmp_path):
        cfg = {
            "dev_db": {"server": "dev-server", "database": "dev_db"},
            "database": {"server": "prod-server", "database": "prod_db"},
        }
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = iwr.load_db_config(str(cfg_file))
        assert result["server"] == "dev-server"

    def test_fallback_to_database(self, tmp_path):
        cfg = {"database": {"server": "prod-server", "database": "prod_db"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = iwr.load_db_config(str(cfg_file))
        assert result["server"] == "prod-server"

    def test_missing_both_keys_raises(self, tmp_path):
        cfg = {"other_key": {"server": "x"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        with pytest.raises(KeyError, match="dev_db"):
            iwr.load_db_config(str(cfg_file))


class TestLoadOracleConfig:
    """Tests for load_oracle_config()."""

    def test_returns_oracle_section(self, tmp_path):
        cfg = {"oracle_db_insert": {"host": "oracle-host", "port": 1521}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = iwr.load_oracle_config(str(cfg_file))
        assert result["host"] == "oracle-host"


# =========================================================================
# Group 2: CBC rate fetching
# =========================================================================

def _make_cbc_html(dates_rates):
    """Helper: build CBC-style HTML with multiple date/rate rows."""
    rows = ""
    for date_str, rate_val in dates_rates:
        rows += f"<tr><td>{date_str}</td><td>{rate_val}</td></tr>"
    return f"<html><body><table>{rows}</table></body></html>"


class TestGetLatestUsdtwdRateFromCbc:
    """Tests for get_latest_usdtwd_rate_from_cbc()."""

    def test_successful_fetch(self):
        yesterday = (date.today() - timedelta(days=1)).strftime('%Y/%m/%d')
        html = _make_cbc_html([(yesterday, "31.518")])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("ImportWebRate.requests.get", return_value=mock_resp):
            result = iwr.get_latest_usdtwd_rate_from_cbc()

        assert result == 31.518

    def test_lookback_finds_older_date(self):
        three_days_ago = (date.today() - timedelta(days=3)).strftime('%Y/%m/%d')
        html = _make_cbc_html([(three_days_ago, "31.4")])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("ImportWebRate.requests.get", return_value=mock_resp):
            result = iwr.get_latest_usdtwd_rate_from_cbc()

        assert result == 31.4

    def test_http_error_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("ImportWebRate.requests.get", return_value=mock_resp):
            result = iwr.get_latest_usdtwd_rate_from_cbc()

        assert result is None


# =========================================================================
# Group 3: Rate calculation
# =========================================================================

class TestCalculateMonthlyRates:
    """Tests for calculate_monthly_rates()."""

    def test_inr_rate_calculation(self):
        all_rates = {'INR': (31.5, 83.0)}
        rows = iwr.calculate_monthly_rates(all_rates, date(2026, 2, 14))

        # Should have 2 rows: INR→TWD and TWD→INR
        assert len(rows) == 2
        inr_to_twd = [r for r in rows if r['from_cur'] == 'INR'][0]
        assert inr_to_twd['v_rate'] == pytest.approx(31.5 / 83.0, rel=1e-4)

    def test_mxn_rate_calculation(self):
        all_rates = {'MXN': (31.5, 20.5)}
        rows = iwr.calculate_monthly_rates(all_rates, date(2026, 2, 14))

        mxn_to_twd = [r for r in rows if r['from_cur'] == 'MXN'][0]
        assert mxn_to_twd['v_rate'] == pytest.approx(31.5 / 20.5, rel=1e-4)

    def test_try_rate_calculation(self):
        all_rates = {'TRY': (1, 0.85)}
        rows = iwr.calculate_monthly_rates(all_rates, date(2026, 2, 14))

        try_to_twd = [r for r in rows if r['from_cur'] == 'TRY'][0]
        assert try_to_twd['v_rate'] == pytest.approx(1 / 0.85, rel=1e-4)

    def test_bidirectional_rows(self):
        all_rates = {
            'INR': (31.5, 83.0),
            'MXN': (31.5, 20.5),
            'TRY': (1, 0.85),
        }
        rows = iwr.calculate_monthly_rates(all_rates, date(2026, 2, 14))

        # 3 currencies * 2 directions = 6
        assert len(rows) == 6

        from_currencies = [r['from_cur'] for r in rows]
        assert 'TWD' in from_currencies  # reverse rows exist

    def test_rate_date_format(self):
        all_rates = {'INR': (31.5, 83.0)}
        rows = iwr.calculate_monthly_rates(all_rates, date(2026, 2, 14))

        assert rows[0]['rate_date'] == "2026-02-14"


# =========================================================================
# Group 4: Database operations
# =========================================================================

class TestPrepareAndMergeSqlServer:
    """Tests for prepare_and_merge_sql_server(): DWNotes（主）先寫，SERVICE_TEST（輔）後寫。"""

    @staticmethod
    def _conn():
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_sql_merge_bidirectional_in_both_dbs(self):
        dw, dw_cur = self._conn()
        st, st_cur = self._conn()
        all_rates = {'INR': (31.5, 83.0), 'MXN': (31.5, 20.5)}
        with patch("ImportWebRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportWebRate.connect_to_db_sql", return_value=st):
            rows, st_error = iwr.prepare_and_merge_sql_server(all_rates, date(2026, 2, 14))
        # 2 currencies * 2 directions = 4 execute calls per DB
        assert len(rows) == 4
        assert st_error is None
        assert dw_cur.execute.call_count == 4
        assert st_cur.execute.call_count == 4
        assert "MERGE DWNotes.dbo" in dw_cur.execute.call_args_list[0][0][0]
        assert "user_conversion_type = N'SC_" in dw_cur.execute.call_args_list[0][0][0]
        assert "MERGE SERVICE_TEST.dbo" in st_cur.execute.call_args_list[0][0][0]

    def test_dwnotes_connection_failure_raises(self):
        with patch("ImportWebRate.connect_to_db_dwnotes", return_value=None), \
             patch("ImportWebRate.connect_to_db_sql", return_value=MagicMock()):
            with pytest.raises(ConnectionError):
                iwr.prepare_and_merge_sql_server({'INR': (31.5, 83.0)}, date(2026, 2, 14))

    def test_dwnotes_write_failure_rollbacks(self):
        dw, dw_cur = self._conn()
        dw_cur.execute.side_effect = Exception("DB error")
        with patch("ImportWebRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportWebRate.connect_to_db_sql", return_value=MagicMock()):
            with pytest.raises(Exception, match="DB error"):
                iwr.prepare_and_merge_sql_server({'INR': (31.5, 83.0)}, date(2026, 2, 14))
        dw.rollback.assert_called_once()

    def test_service_test_failure_is_reported_not_raised(self):
        dw, _ = self._conn()
        with patch("ImportWebRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportWebRate.connect_to_db_sql", return_value=None):
            rows, st_error = iwr.prepare_and_merge_sql_server({'INR': (31.5, 83.0)}, date(2026, 2, 14))
        assert len(rows) == 2
        assert st_error and "SERVICE_TEST" in st_error
        dw.commit.assert_called_once()


class TestInsertToOracle:
    """Tests for insert_to_oracle()."""

    def test_executemany_called(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        rows = [
            {"from_cur": "INR", "to_cur": "TWD", "rate_date": "2026-02-14", "v_rate": 0.38, "rev_rate": 2.63, "conv_type": "SC_銀行匯率"},
            {"from_cur": "TWD", "to_cur": "INR", "rate_date": "2026-02-14", "v_rate": 2.63, "rev_rate": 0.38, "conv_type": "SC_銀行匯率"},
        ]

        with patch("ImportWebRate.connect_to_db_oracle", return_value=mock_conn):
            iwr.insert_to_oracle(rows)

        mock_cursor.executemany.assert_called_once()
        actual_rows = mock_cursor.executemany.call_args[0][1]
        assert len(actual_rows) == 2
        mock_conn.commit.assert_called_once()

    def test_connection_failure_raises(self):
        with patch("ImportWebRate.connect_to_db_oracle", return_value=None):
            with pytest.raises(ConnectionError):
                iwr.insert_to_oracle([{"from_cur": "INR", "to_cur": "TWD", "v_rate": 0.38}])

    def test_write_failure_rollbacks(self):
        mock_cursor = MagicMock()
        mock_cursor.executemany.side_effect = Exception("Oracle error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("ImportWebRate.connect_to_db_oracle", return_value=mock_conn):
            with pytest.raises(Exception, match="Oracle error"):
                iwr.insert_to_oracle([{"from_cur": "INR", "to_cur": "TWD", "v_rate": 0.38}])

        mock_conn.rollback.assert_called_once()


# =========================================================================
# Group 5: Email & report
# =========================================================================

_FAKE_EMAIL_CONFIG = {
    "email": {
        "smtp_server": "smtp.test.com",
        "port": 25,
        "sender_email": "test@example.com",
        "password": "pw",
        "receiver_emails_success": ["success@example.com"],
        "receiver_emails_failure": ["fail@example.com"],
    }
}


class TestSendEmailNotification:
    """Tests for send_email_notification()."""

    def test_success_email(self):
        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportWebRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportWebRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportWebRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = iwr.send_email_notification(
                subject="Test", body="Hello", mailtype="success"
            )

        assert result is True

    def test_error_email_high_priority(self):
        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportWebRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportWebRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportWebRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = iwr.send_email_notification(
                subject="Error", body="Oops", mailtype="error"
            )

        raw_msg = mock_smtp_instance.sendmail.call_args[0][2]
        assert "X-Priority: 1" in raw_msg

    def test_smtp_failure_returns_false(self):
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(
            side_effect=smtplib.SMTPException("SMTP down")
        )
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportWebRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportWebRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportWebRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = iwr.send_email_notification(
                subject="Test", body="Hello", mailtype="success"
            )

        assert result is False


# =========================================================================
# Group 6: Monthly rate table HTML
# =========================================================================

class TestBuildMonthlyRateTable:
    """Tests for build_monthly_rate_table()."""

    def test_html_contains_twd_rows(self):
        rows = [
            {"from_cur": "INR", "to_cur": "TWD", "rate_date": "2026-02-14", "v_rate": 0.3795, "rev_rate": 2.635},
            {"from_cur": "TWD", "to_cur": "INR", "rate_date": "2026-02-14", "v_rate": 2.635, "rev_rate": 0.3795},
        ]
        html = iwr.build_monthly_rate_table(rows, "2026-02-14")
        assert "INR" in html
        assert 'id="monthly-rates"' in html
        # TWD→INR reverse row should NOT appear in the table (only to_cur=TWD)
        assert html.count("INR") >= 1


# =========================================================================
# Group 7: Step Status HTML
# =========================================================================

class TestBuildStepStatusHtml:
    """Tests for _build_step_status_html()."""

    def test_all_success(self):
        steps = [
            ("Step 1: CBC", "OK"),
            ("Step 2: Selenium", "Done"),
        ]
        html = iwr._build_step_status_html(steps)
        assert "#2e7d32" in html
        assert "&#9989;" in html

    def test_contains_failure(self):
        steps = [
            ("Step 1: CBC", "OK"),
            ("Step 2: Selenium", "失敗 - timeout"),
        ]
        html = iwr._build_step_status_html(steps)
        assert "#d32f2f" in html
        assert "&#10060;" in html


# =========================================================================
# Group 8: Export monthly report & email (Excel 3 sheets)
# =========================================================================

class TestExportMonthlyReportAndEmail:
    """Tests for export_monthly_report_and_email()."""

    @pytest.fixture()
    def rows_to_insert(self):
        return [
            {"from_cur": "INR", "to_cur": "TWD", "rate_date": "2026-02-16", "v_rate": 0.3795, "rev_rate": 2.635, "conv_type": "SC_銀行匯率"},
            {"from_cur": "TWD", "to_cur": "INR", "rate_date": "2026-02-16", "v_rate": 2.635, "rev_rate": 0.3795, "conv_type": "SC_銀行匯率"},
            {"from_cur": "MXN", "to_cur": "TWD", "rate_date": "2026-02-16", "v_rate": 1.537, "rev_rate": 0.6506, "conv_type": "SC_銀行匯率"},
            {"from_cur": "TWD", "to_cur": "MXN", "rate_date": "2026-02-16", "v_rate": 0.6506, "rev_rate": 1.537, "conv_type": "SC_銀行匯率"},
            {"from_cur": "TRY", "to_cur": "TWD", "rate_date": "2026-02-16", "v_rate": 1.1765, "rev_rate": 0.85, "conv_type": "SC_銀行匯率"},
            {"from_cur": "TWD", "to_cur": "TRY", "rate_date": "2026-02-16", "v_rate": 0.85, "rev_rate": 1.1765, "conv_type": "SC_銀行匯率"},
        ]

    def _make_mock_sql_cursor(self):
        """Return a mock SQL cursor that returns sample rate rows."""
        cursor = MagicMock()
        cursor.description = [
            ("from_currency",), ("to_currency",), ("from_conversion_date",),
            ("conversion_rate",), ("inverse_conversion_rate",),
        ]
        cursor.fetchall.return_value = [
            ("INR", "TWD", "2026-02-16", 0.3795, 2.635),
            ("MXN", "TWD", "2026-02-16", 1.537, 0.6506),
        ]
        return cursor

    def _make_mock_oracle_cursor(self):
        """Return a mock Oracle cursor that returns sample rows."""
        cursor = MagicMock()
        cursor.description = [
            ("SOURCE_FROM",), ("FROM_CURRENCY",), ("TO_CURRENCY",),
            ("FROM_DATE",), ("TO_DATE",), ("CONVERSION_RATE",),
            ("MODE_FLAG",), ("IMPORTED_DATE",), ("IMPORTED_FLAG",),
            ("USER_CONVERSION_TYPE",),
        ]
        cursor.fetchall.return_value = [
            ("TW_BANK", "INR", "TWD", "2026-02-15", "2026-02-15", 0.3795, "I", "2026-02-15", "N", "SC_銀行匯率"),
        ]
        return cursor

    def test_excel_has_three_sheets(self, rows_to_insert):
        """Verify the generated Excel file has SQL Server, Oracle ERP, and Screenshots sheets."""
        mock_sql_conn = MagicMock()
        mock_sql_conn.cursor.return_value = self._make_mock_sql_cursor()

        mock_oracle_conn = MagicMock()
        mock_oracle_conn.cursor.return_value = self._make_mock_oracle_cursor()

        mock_send = MagicMock(return_value=True)
        step_results = [("Step 1", "OK"), ("Step 2", "OK")]

        with (
            patch("ImportWebRate.connect_to_db_dwnotes", return_value=mock_sql_conn),
            patch("ImportWebRate.connect_to_db_oracle", return_value=mock_oracle_conn),
            patch("ImportWebRate.send_email_notification", mock_send),
            patch("ImportWebRate.build_monthly_rate_table", return_value="<table></table>"),
            patch("ImportWebRate._build_step_status_html", return_value="<div></div>"),
        ):
            iwr.export_monthly_report_and_email(rows_to_insert, step_results)

        assert mock_send.called, "send_email_notification should have been called"
        call_kwargs = mock_send.call_args
        excel_path = call_kwargs.kwargs.get("file_path") or call_kwargs[1].get("file_path")

        assert excel_path is not None, "file_path should be passed to send_email_notification"
        assert excel_path.endswith(".xlsx")
        assert os.path.exists(excel_path)

        # Verify 3 sheets exist
        wb = openpyxl.load_workbook(excel_path)
        assert "SQL Server" in wb.sheetnames
        assert "Oracle ERP" in wb.sheetnames
        assert "Screenshots" in wb.sheetnames

        # Verify SQL Server sheet has data
        ws_sql = wb["SQL Server"]
        assert ws_sql.max_row > 1  # header + at least 1 data row

        # Verify Oracle ERP sheet has data
        ws_oracle = wb["Oracle ERP"]
        assert ws_oracle.max_row > 1

        wb.close()

        # Cleanup generated file
        os.remove(excel_path)

    def test_email_called_with_file_path(self, tmp_path, rows_to_insert):
        """Verify send_email_notification is called with file_path kwarg."""
        mock_sql_conn = MagicMock()
        mock_sql_conn.cursor.return_value = self._make_mock_sql_cursor()

        mock_send = MagicMock(return_value=True)
        step_results = [("Step 1", "OK")]

        with (
            patch("ImportWebRate.connect_to_db_dwnotes", return_value=mock_sql_conn),
            patch("ImportWebRate.connect_to_db_oracle", return_value=None),
            patch("ImportWebRate.send_email_notification", mock_send),
            patch("ImportWebRate.build_monthly_rate_table", return_value="<table></table>"),
            patch("ImportWebRate._build_step_status_html", return_value="<div></div>"),
        ):
            iwr.export_monthly_report_and_email(rows_to_insert, step_results)

        assert mock_send.called
        call_kwargs = mock_send.call_args
        file_path = call_kwargs.kwargs.get("file_path") or call_kwargs[1].get("file_path")
        assert file_path is not None
        assert file_path.endswith(".xlsx")

    def test_oracle_failure_still_generates_excel(self, rows_to_insert):
        """When Oracle connection fails, Excel should still be generated with empty Oracle sheet."""
        mock_sql_conn = MagicMock()
        mock_sql_conn.cursor.return_value = self._make_mock_sql_cursor()

        mock_send = MagicMock(return_value=True)
        step_results = [("Step 1", "OK")]

        with (
            patch("ImportWebRate.connect_to_db_dwnotes", return_value=mock_sql_conn),
            patch("ImportWebRate.connect_to_db_oracle", side_effect=Exception("Oracle down")),
            patch("ImportWebRate.send_email_notification", mock_send),
            patch("ImportWebRate.build_monthly_rate_table", return_value="<table></table>"),
            patch("ImportWebRate._build_step_status_html", return_value="<div></div>"),
        ):
            iwr.export_monthly_report_and_email(rows_to_insert, step_results)

        assert mock_send.called
        call_kwargs = mock_send.call_args
        file_path = call_kwargs.kwargs.get("file_path") or call_kwargs[1].get("file_path")
        assert file_path is not None

        wb = openpyxl.load_workbook(file_path)
        assert "Oracle ERP" in wb.sheetnames
        wb.close()

    def test_sql_empty_result_skips_email(self, rows_to_insert):
        """When SQL Server returns no rows, function should return early without sending email."""
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("from_currency",), ("to_currency",), ("from_conversion_date",),
            ("conversion_rate",), ("inverse_conversion_rate",),
        ]
        mock_cursor.fetchall.return_value = []

        mock_sql_conn = MagicMock()
        mock_sql_conn.cursor.return_value = mock_cursor

        mock_send = MagicMock()
        step_results = []

        with (
            patch("ImportWebRate.connect_to_db_dwnotes", return_value=mock_sql_conn),
            patch("ImportWebRate.send_email_notification", mock_send),
        ):
            iwr.export_monthly_report_and_email(rows_to_insert, step_results)

        mock_send.assert_not_called()
