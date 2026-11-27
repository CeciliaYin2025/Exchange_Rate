"""Tests for ImportDailyRate.py"""

import logging
import os
import smtplib
from datetime import date, datetime, timedelta
from string import Template
from unittest.mock import MagicMock, call, mock_open, patch

import pytest
import yaml

# Patch heavy third-party imports before importing the module under test.
# This avoids ImportError on CI machines that don't have mssql_python / oracledb
# native drivers installed, and prevents module-level side effects.
import sys

_mssql_python_mock = MagicMock()
_oracledb_mock = MagicMock()
_oracledb_mock.ProgrammingError = type("ProgrammingError", (Exception,), {})

sys.modules.setdefault("mssql_python", _mssql_python_mock)
sys.modules.setdefault("oracledb", _oracledb_mock)

import ImportDailyRate as idr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_logger():
    """Ensure the module-level logger is usable during tests."""
    idr.logger = logging.getLogger("test_idr")
    idr.logger.setLevel(logging.DEBUG)
    idr.RUN_LOGS.clear()


# =========================================================================
# Group 1: Pure calculation functions (no mocks needed)
# =========================================================================

class TestBuildRateRows:
    """Tests for build_rate_rows()."""

    def test_normal_rate(self):
        row = idr.build_rate_rows("USD", "TWD", 31.5, "2026-02-15")
        assert row["from_currency"] == "USD"
        assert row["to_currency"] == "TWD"
        assert row["conversion_rate"] == 31.5
        assert row["inverse_conversion_rate"] == pytest.approx(1 / 31.5, rel=1e-5)
        assert row["from_conversion_date"] == "2026-02-15"
        assert row["to_conversion_date"] == "2026-02-15"

    def test_fixed_fields(self):
        row = idr.build_rate_rows("EUR", "TWD", 37.37, "2026-02-15")
        assert row["user_conversion_type"] == "SC_\u9280\u884c\u532f\u7387"  # SC_銀行匯率
        assert row["user_id"] == "1175"
        assert row["launch_rate_change"] == "Y"
        assert row["batch_number"] is None

    def test_rate_zero_inverse_is_zero(self):
        row = idr.build_rate_rows("JPY", "TWD", 0, "2026-02-15")
        assert row["conversion_rate"] == 0
        assert row["inverse_conversion_rate"] == 0


class TestGetDailyRateWithUsd:
    """Tests for get_daily_rate_with_usd()."""

    def test_usd_cross_rate_calculation(self):
        rates = {"EUR": 37.37, "CAD": 23.13, "AUD": 20.5, "JPY": 0.21, "PHP": 0.55}
        usd_rate = 31.518

        rows, rates_with_usd, conversion_date = idr.get_daily_rate_with_usd(rates, usd_rate)

        # EUR/USD = 37.37 / 31.518
        assert rates_with_usd["EUR"] == pytest.approx(37.37 / 31.518, abs=0.0001)
        assert rates_with_usd["CAD"] == pytest.approx(23.13 / 31.518, abs=0.0001)

    def test_rows_are_bidirectional(self):
        rates = {"EUR": 37.37, "CAD": 23.13, "AUD": 20.5, "JPY": 0.21, "PHP": 0.55}
        usd_rate = 31.518

        rows, _, _ = idr.get_daily_rate_with_usd(rates, usd_rate)

        # Each currency produces 2 rows (USD→XXX, XXX→USD)
        assert len(rows) == len(idr.USD_CURRENCIES) * 2

        from_currencies = [r["from_currency"] for r in rows]
        to_currencies = [r["to_currency"] for r in rows]
        # Every USD_CURRENCY should appear as both from and to
        for cur in idr.USD_CURRENCIES:
            assert cur in from_currencies
            assert cur in to_currencies

    def test_conversion_date_is_tomorrow(self):
        rates = {"EUR": 37.37, "CAD": 23.13, "AUD": 20.5, "JPY": 0.21, "PHP": 0.55}
        usd_rate = 31.518

        _, _, conversion_date = idr.get_daily_rate_with_usd(rates, usd_rate)
        expected = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert conversion_date == expected

    def test_usd_rate_zero(self):
        rates = {"EUR": 37.37, "CAD": 23.13, "AUD": 20.5, "JPY": 0.21, "PHP": 0.55}
        rows, rates_with_usd, _ = idr.get_daily_rate_with_usd(rates, 0)
        for cur in idr.USD_CURRENCIES:
            assert rates_with_usd[cur] == 0


# =========================================================================
# Group 2: Config loading (mock file I/O)
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

        result = idr.load_db_config(str(cfg_file))
        assert result["server"] == "dev-server"

    def test_fallback_to_database(self, tmp_path):
        cfg = {"database": {"server": "prod-server", "database": "prod_db"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = idr.load_db_config(str(cfg_file))
        assert result["server"] == "prod-server"

    def test_missing_both_keys_raises(self, tmp_path):
        cfg = {"other_key": {"server": "x"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        with pytest.raises(KeyError, match="dev_db"):
            idr.load_db_config(str(cfg_file))


class TestLoadOracleConfig:
    """Tests for load_oracle_config()."""

    def test_returns_oracle_section(self, tmp_path):
        cfg = {"oracle_db_insert": {"host": "oracle-host", "port": 1521}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = idr.load_oracle_config(str(cfg_file))
        assert result["host"] == "oracle-host"
        assert result["port"] == 1521


class TestLoadEmailConfig:
    """Tests for load_email_config()."""

    def test_returns_email_section(self, tmp_path):
        cfg = {
            "email": {
                "smtp_server": "smtp.example.com",
                "port": 25,
                "sender_email": "sender@example.com",
                "password": "secret",
                "receiver_emails_success": ["a@b.com"],
                "receiver_emails_failure": ["c@d.com"],
            }
        }
        cfg_file = tmp_path / "email_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = idr.load_email_config(str(cfg_file))
        assert result["smtp_server"] == "smtp.example.com"
        assert result["receiver_emails_success"] == ["a@b.com"]


# =========================================================================
# Group 3: HTML templates
# =========================================================================

class TestLoadTemplate:
    """Tests for _load_template()."""

    def test_load_existing_template(self):
        tmpl = idr._load_template("rate_row.html")
        assert isinstance(tmpl, Template)

    def test_load_nonexistent_template(self):
        with pytest.raises(FileNotFoundError):
            idr._load_template("nonexistent_template.html")


class TestBuildRateTable:
    """Tests for build_rate_table()."""

    def test_html_contains_all_currencies(self):
        rates = {"EUR": 37.37, "JPY": 0.21}
        rates_with_usd = {"EUR": 1.1858, "JPY": 0.0067}
        conversion_date = "2026-02-15"

        html = idr.build_rate_table(rates, rates_with_usd, conversion_date)

        assert "EUR" in html
        assert "JPY" in html
        assert "37.3700" in html
        assert "1.1858" in html
        assert 'id="twd-rates"' in html
        assert 'id="usd-rates"' in html
        assert conversion_date in html


class TestBuildStepStatusHtml:
    """Tests for _build_step_status_html()."""

    def test_all_success(self):
        steps = [
            ("Step 1: Scrape", "OK"),
            ("Step 2: SQL", "Done"),
        ]
        html = idr._build_step_status_html(steps)
        assert "#2e7d32" in html  # green
        assert "&#9989;" in html  # checkmark
        assert "#d32f2f" not in html  # no red

    def test_contains_failure(self):
        steps = [
            ("Step 1: Scrape", "OK"),
            ("Step 2: SQL", "\u5931\u6557 - connection error"),  # 失敗
        ]
        html = idr._build_step_status_html(steps)
        assert "#d32f2f" in html  # red
        assert "&#10060;" in html  # cross mark
        assert "#2e7d32" in html  # green still present for step 1


# =========================================================================
# Group 4: Web scraping (mock requests + BeautifulSoup)
# =========================================================================

def _make_cbc_html(date_str, rate_value):
    """Helper: build a minimal CBC-style HTML page with a rate table."""
    return f"""
    <html><body>
    <table>
        <tr><td>{date_str}</td><td>{rate_value}</td></tr>
    </table>
    </body></html>
    """


class TestGetTargetDate:
    """Tests for get_target_date()."""

    def test_parse_rate_for_today(self):
        today_str = datetime.today().strftime("%Y/%m/%d")
        html = _make_cbc_html(today_str, "31.518")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("ImportDailyRate.requests.get", return_value=mock_resp):
            result = idr.get_target_date("http://example.com")

        assert result is not None
        rate, found_date = result
        assert rate == 31.518
        assert found_date == today_str

    def test_lookback_previous_day(self):
        yesterday_str = (datetime.today() - timedelta(days=1)).strftime("%Y/%m/%d")
        html = _make_cbc_html(yesterday_str, "31.4")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("ImportDailyRate.requests.get", return_value=mock_resp):
            result = idr.get_target_date("http://example.com")

        assert result is not None
        rate, found_date = result
        assert rate == 31.4
        assert found_date == yesterday_str

    def test_no_rate_within_lookback(self):
        old_date = (datetime.today() - timedelta(days=20)).strftime("%Y/%m/%d")
        html = _make_cbc_html(old_date, "30.0")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("ImportDailyRate.requests.get", return_value=mock_resp):
            result = idr.get_target_date("http://example.com")

        assert result is None

    def test_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("ImportDailyRate.requests.get", return_value=mock_resp):
            result = idr.get_target_date("http://example.com")

        assert result is None


def _make_tcb_html(currencies_data):
    """Helper: build minimal TCB-style HTML for rate parsing.

    currencies_data: list of (currency_code, cash_buy, cash_sell, spot_buy, spot_sell)
    """
    rows = ""
    for code, cash_buy, cash_sell, spot_buy, spot_sell in currencies_data:
        rows += f"""
        <tr>
            <td>SomeName ({code})</td>
            <td>{cash_buy}</td>
            <td>{cash_sell}</td>
            <td>{spot_buy}</td>
            <td>{spot_sell}</td>
        </tr>"""
    return f"<html><body><table>{rows}</table></body></html>"


class TestGetDailyRateFromTcb:
    """Tests for get_daily_rate_from_tcb()."""

    def test_parses_currencies_correctly(self):
        today_str = datetime.today().strftime("%Y/%m/%d")
        cbc_html = _make_cbc_html(today_str, "31.518")

        tcb_data = [
            ("CNY", "4.20", "4.50", "4.30", "4.40"),   # avg = 4.35
            ("EUR", "35.0", "36.0", "36.5", "37.5"),    # avg = 37.0
            ("GBP", "40.0", "42.0", "41.0", "42.0"),    # avg = 41.5
            ("CAD", "22.0", "24.0", "22.5", "23.5"),    # avg = 23.0
            ("AUD", "19.0", "21.0", "20.0", "21.0"),    # avg = 20.5
            ("JPY", "0.20", "0.22", "0.20", "0.22"),    # avg = 0.21
            ("PHP", "0.50", "0.56", "", ""),             # PHP uses cash_sell = 0.56
        ]
        tcb_html = _make_tcb_html(tcb_data)

        mock_cbc_resp = MagicMock()
        mock_cbc_resp.status_code = 200
        mock_cbc_resp.text = cbc_html

        mock_tcb_resp = MagicMock()
        mock_tcb_resp.status_code = 200
        mock_tcb_resp.text = tcb_html

        def side_effect(url, **kwargs):
            if "bot.com.tw" in url:
                return mock_tcb_resp
            return mock_cbc_resp

        with (
            patch("ImportDailyRate.requests.get", side_effect=side_effect),
            patch("ImportDailyRate.snapshot_page", return_value="/fake/path.jpg"),
        ):
            result = idr.get_daily_rate_from_tcb()

        assert result is not None
        rates, usd_rate = result
        assert usd_rate == 31.518
        assert len(rates) == 7
        assert rates["EUR"] == pytest.approx(37.0, abs=0.01)
        assert rates["PHP"] == pytest.approx(0.56, abs=0.01)
        assert rates["JPY"] == pytest.approx(0.21, abs=0.01)

    def test_cbc_failure_returns_none(self):
        with patch("ImportDailyRate.get_latest_usdtwd_rate_from_cbc", return_value=None):
            result = idr.get_daily_rate_from_tcb()
        assert result is None


# =========================================================================
# Group 5: Database operations (mock connection + cursor)
# =========================================================================

class TestInsertDailyRate:
    """Tests for insert_daily_rate(): DWNotes（主）先寫，SERVICE_TEST（輔）後寫。"""

    @staticmethod
    def _conn():
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def test_writes_dwnotes_then_service_test_bidirectional(self):
        dw, dw_cur = self._conn()
        st, st_cur = self._conn()
        rates = {"EUR": 37.37, "JPY": 0.21}
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=st):
            assert idr.insert_daily_rate(rates, 31.518) is None
        # rates + USD = 3 currencies, each bidirectional = 6 calls per DB
        assert dw_cur.execute.call_count == 6
        assert st_cur.execute.call_count == 6
        assert "MERGE DWNotes.dbo" in dw_cur.execute.call_args_list[0][0][0]
        assert "user_conversion_type = ?" in dw_cur.execute.call_args_list[0][0][0]
        assert "MERGE SERVICE_TEST.dbo" in st_cur.execute.call_args_list[0][0][0]
        dw.commit.assert_called_once()
        st.commit.assert_called_once()

    def test_dwnotes_connection_failure_raises(self):
        st, st_cur = self._conn()
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=None), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=st):
            with pytest.raises(ConnectionError):
                idr.insert_daily_rate({"EUR": 37.37}, 31.518)
        st_cur.execute.assert_not_called()

    def test_dwnotes_write_failure_rollbacks(self):
        dw, dw_cur = self._conn()
        dw_cur.execute.side_effect = Exception("DB write error")
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=MagicMock()):
            with pytest.raises(Exception, match="DB write error"):
                idr.insert_daily_rate({"EUR": 37.37}, 31.518)
        dw.rollback.assert_called_once()

    def test_service_test_failure_is_reported_not_raised(self):
        dw, _ = self._conn()
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=None):
            err = idr.insert_daily_rate({"EUR": 37.37}, 31.518)
        assert err and "SERVICE_TEST" in err
        dw.commit.assert_called_once()


class TestInsertUsdRates:
    """Tests for insert_usd_rates()."""

    def test_executes_merge_bidirectional_in_both_dbs(self):
        dw, dw_cur = TestInsertDailyRate._conn()
        st, st_cur = TestInsertDailyRate._conn()
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=st):
            assert idr.insert_usd_rates({"EUR": 1.1858, "CAD": 0.7337}, "2026-02-15") is None
        # 2 currencies * 2 directions = 4 execute calls per DB
        assert dw_cur.execute.call_count == 4
        assert st_cur.execute.call_count == 4

    def test_dwnotes_connection_failure_raises(self):
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=None), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=MagicMock()):
            with pytest.raises(ConnectionError):
                idr.insert_usd_rates({"EUR": 1.1858}, "2026-02-15")

    def test_dwnotes_write_failure_rollbacks(self):
        dw, dw_cur = TestInsertDailyRate._conn()
        dw_cur.execute.side_effect = Exception("DB error")
        with patch("ImportDailyRate.connect_to_db_dwnotes", return_value=dw), \
             patch("ImportDailyRate.connect_to_db_sql", return_value=MagicMock()):
            with pytest.raises(Exception, match="DB error"):
                idr.insert_usd_rates({"EUR": 1.1858}, "2026-02-15")
        dw.rollback.assert_called_once()


class TestInsertToOracle:
    """Tests for insert_to_oracle()."""

    def test_executemany_called_with_correct_count(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        rates = {"CNY": 4.35, "EUR": 37.37, "GBP": 41.5, "CAD": 23.0,
                 "AUD": 20.5, "JPY": 0.21, "PHP": 0.56}
        usd_rate = 31.518

        # USD rows: 5 currencies * 2 directions = 10 rows
        usd_rows = []
        for cur in idr.USD_CURRENCIES:
            usd_rows.append({"from_currency": "USD", "to_currency": cur,
                             "conversion_rate": 1.0})
            usd_rows.append({"from_currency": cur, "to_currency": "USD",
                             "conversion_rate": 1.0})

        with patch("ImportDailyRate.connect_to_db_oracle", return_value=mock_conn):
            idr.insert_to_oracle(rates, usd_rate, usd_rows)

        mock_cursor.executemany.assert_called_once()
        actual_rows = mock_cursor.executemany.call_args[0][1]
        # TWD pairs: (7 rates + 1 USD) * 2 directions = 16
        # USD rows: 10
        # Total = 26
        assert len(actual_rows) == 26
        mock_conn.commit.assert_called_once()

    def test_connection_failure_raises(self):
        with patch("ImportDailyRate.connect_to_db_oracle", return_value=None):
            with pytest.raises(ConnectionError):
                idr.insert_to_oracle({"EUR": 37.37}, 31.518, [])

    def test_write_failure_rollbacks(self):
        mock_cursor = MagicMock()
        mock_cursor.executemany.side_effect = Exception("Oracle error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("ImportDailyRate.connect_to_db_oracle", return_value=mock_conn):
            with pytest.raises(Exception, match="Oracle error"):
                idr.insert_to_oracle({"EUR": 37.37}, 31.518, [])

        mock_conn.rollback.assert_called_once()


# =========================================================================
# Group 6: Email sending (mock smtplib + config)
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

    def test_success_email_calls_sendmail(self, tmp_path):
        cfg_file = tmp_path / "email_config.yaml"
        cfg_file.write_text(yaml.dump(_FAKE_EMAIL_CONFIG), encoding="utf-8")

        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportDailyRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportDailyRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportDailyRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = idr.send_email_notification(
                subject="Test", body="Hello", mailtype="success"
            )

        assert result is True
        mock_smtp_instance.sendmail.assert_called_once()
        call_args = mock_smtp_instance.sendmail.call_args
        assert call_args[0][1] == ["success@example.com"]

    def test_error_email_uses_failure_recipients_and_high_priority(self, tmp_path):
        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportDailyRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportDailyRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportDailyRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = idr.send_email_notification(
                subject="Error", body="Oops", mailtype="error"
            )

        assert result is True
        call_args = mock_smtp_instance.sendmail.call_args
        assert call_args[0][1] == ["fail@example.com"]

        # Verify high priority headers are in the raw message
        raw_msg = call_args[0][2]
        assert "X-Priority: 1" in raw_msg
        assert "Importance: High" in raw_msg

    def test_smtp_failure_returns_false(self):
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(
            side_effect=smtplib.SMTPException("SMTP down")
        )
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportDailyRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportDailyRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportDailyRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = idr.send_email_notification(
                subject="Test", body="Hello", mailtype="success"
            )

        assert result is False


# =========================================================================
# Group 7: Report generation (mock DB + email)
# =========================================================================

class TestExportAndEmailRates:
    """Tests for export_and_email_rates()."""

    def _make_sql_rows(self):
        """Fake SQL Server query result rows."""
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        return [
            ("EUR", "TWD", tomorrow, 37.37, round(1 / 37.37, 6)),
            ("JPY", "TWD", tomorrow, 0.21, round(1 / 0.21, 6)),
        ]

    def test_generates_excel_and_sends_email(self, tmp_path):
        mock_sql_cursor = MagicMock()
        mock_sql_cursor.fetchall.return_value = self._make_sql_rows()
        mock_sql_cursor.description = [
            ("from_currency",), ("to_currency",), ("from_conversion_date",),
            ("conversion_rate",), ("inverse_conversion_rate",),
        ]
        mock_sql_conn = MagicMock()
        mock_sql_conn.cursor.return_value = mock_sql_cursor

        web_rates = {"EUR": 37.37, "JPY": 0.21, "CAD": 23.0,
                     "AUD": 20.5, "PHP": 0.56}
        web_usd_rate = 31.518
        web_usd_rows = [
            {"from_currency": "USD", "to_currency": "EUR",
             "from_conversion_date": "2026-02-15", "to_conversion_date": "2026-02-15",
             "conversion_rate": 1.1858, "inverse_conversion_rate": 0.843,
             "user_conversion_type": "SC_\u9280\u884c\u532f\u7387",
             "user_id": "1175", "launch_rate_change": "Y", "batch_number": None},
            {"from_currency": "EUR", "to_currency": "USD",
             "from_conversion_date": "2026-02-15", "to_conversion_date": "2026-02-15",
             "conversion_rate": 0.843, "inverse_conversion_rate": 1.1858,
             "user_conversion_type": "SC_\u9280\u884c\u532f\u7387",
             "user_id": "1175", "launch_rate_change": "Y", "batch_number": None},
        ]
        step_results = [
            ("Step 1: Scrape", "OK"),
            ("Step 2: SQL TWD", "OK"),
            ("Step 3: SQL USD", "OK"),
            ("Step 4: Oracle", "OK"),
        ]

        with (
            patch("ImportDailyRate.connect_to_db_dwnotes", return_value=mock_sql_conn),
            patch("ImportDailyRate.connect_to_db_oracle", return_value=None),
            patch("ImportDailyRate.send_email_notification", return_value=True) as mock_email,
        ):
            idr.export_and_email_rates(
                web_rates=web_rates,
                web_usd_rate=web_usd_rate,
                web_usd_rows=web_usd_rows,
                step_results=step_results,
                excel_attachment_folder=str(tmp_path),
            )

        # Excel file should exist in the temp folder
        xlsx_files = list(tmp_path.glob("*.xlsx"))
        assert len(xlsx_files) == 1

        # Email should have been called once
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args
        assert call_kwargs[1]["file_path"].endswith(".xlsx")

    def test_oracle_failure_does_not_crash(self, tmp_path):
        mock_sql_cursor = MagicMock()
        mock_sql_cursor.fetchall.return_value = self._make_sql_rows()
        mock_sql_cursor.description = [
            ("from_currency",), ("to_currency",), ("from_conversion_date",),
            ("conversion_rate",), ("inverse_conversion_rate",),
        ]
        mock_sql_conn = MagicMock()
        mock_sql_conn.cursor.return_value = mock_sql_cursor

        web_rates = {"EUR": 37.37, "JPY": 0.21, "CAD": 23.0,
                     "AUD": 20.5, "PHP": 0.56}

        with (
            patch("ImportDailyRate.connect_to_db_dwnotes", return_value=mock_sql_conn),
            patch("ImportDailyRate.connect_to_db_oracle", side_effect=Exception("Oracle down")),
            patch("ImportDailyRate.send_email_notification", return_value=True),
        ):
            # Should NOT raise even though Oracle fails
            idr.export_and_email_rates(
                web_rates=web_rates,
                web_usd_rate=31.518,
                web_usd_rows=[],
                step_results=[("Step 1", "OK")],
                excel_attachment_folder=str(tmp_path),
            )

        xlsx_files = list(tmp_path.glob("*.xlsx"))
        assert len(xlsx_files) == 1
