"""Tests for ImportPortalRate.py"""

import logging
import smtplib
from datetime import datetime
from string import Template
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml

# Patch heavy third-party imports before importing the module under test.
import sys

_mssql_python_mock = MagicMock()
sys.modules.setdefault("mssql_python", _mssql_python_mock)

_oracledb_mock = MagicMock()
_oracledb_mock.ProgrammingError = type("ProgrammingError", (Exception,), {})
sys.modules.setdefault("oracledb", _oracledb_mock)

import ImportPortalRate as ipr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_logger():
    """Ensure the module-level logger is usable during tests."""
    ipr.logger = logging.getLogger("test_ipr")
    ipr.logger.setLevel(logging.DEBUG)
    ipr.RUN_LOGS.clear()


# =========================================================================
# Group 1: Config loading
# =========================================================================

class TestLoadDbConfig:
    """Tests for load_db_config()."""

    def test_database_notes_preferred(self, tmp_path):
        cfg = {
            "database_notes": {"server": "notes-server", "database": "DWNotes"},
            "database": {"server": "prod-server", "database": "prod_db"},
        }
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = ipr.load_db_config(str(cfg_file))
        assert result["server"] == "notes-server"

    def test_fallback_to_database(self, tmp_path):
        cfg = {"database": {"server": "prod-server", "database": "prod_db"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = ipr.load_db_config(str(cfg_file))
        assert result["server"] == "prod-server"

    def test_missing_both_keys_raises(self, tmp_path):
        cfg = {"other_key": {"server": "x"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        with pytest.raises(KeyError, match="database_notes"):
            ipr.load_db_config(str(cfg_file))


# =========================================================================
# Group 2: JSON fetching
# =========================================================================

_SAMPLE_JSON = {
    "start": "20260201",
    "end": "20260210",
    "items": [
        {"code": "EUR", "buyValue": "34.5"},
        {"code": "JPY", "buyValue": "0.22"},
        {"code": "USD", "buyValue": "31.5"},
        {"code": "CNY", "buyValue": "4.3"},  # should be filtered out
    ]
}


class TestFetchJson:
    """Tests for fetch_json()."""

    def test_successful_parse(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _SAMPLE_JSON

        with patch("ImportPortalRate.requests.get", return_value=mock_resp):
            result = ipr.fetch_json()

        assert result is not None
        items, start, end, raw_data = result
        assert len(items) == 3  # EUR, JPY, USD only
        assert start == "20260201"
        assert end == "20260210"
        # raw_data contains all items including filtered-out ones
        assert len(raw_data['items']) == 4
        assert raw_data['start'] == "20260201"

    def test_filters_by_currency(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _SAMPLE_JSON

        with patch("ImportPortalRate.requests.get", return_value=mock_resp):
            items, _, _, raw_data = ipr.fetch_json()

        codes = [item['code'] for item in items]
        assert "CNY" not in codes
        assert "EUR" in codes
        assert "JPY" in codes
        assert "USD" in codes
        # raw_data still has CNY
        raw_codes = [item['code'] for item in raw_data['items']]
        assert "CNY" in raw_codes

    def test_http_error_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("ImportPortalRate.requests.get", return_value=mock_resp):
            result = ipr.fetch_json()

        assert result is None

    def test_invalid_buy_value_handled(self):
        bad_json = {
            "start": "20260201",
            "end": "20260210",
            "items": [{"code": "EUR", "buyValue": "invalid"}]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = bad_json

        with patch("ImportPortalRate.requests.get", return_value=mock_resp):
            result = ipr.fetch_json()

        items, _, _, _ = result
        assert "conversion_rate" not in items[0]


# =========================================================================
# Group 3: Database operations
# =========================================================================

class TestInsertPortalRates:
    """Tests for insert_portal_rates()."""

    def test_merge_call_count(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        items = [
            {"code": "EUR", "buyValue": "34.5"},
            {"code": "JPY", "buyValue": "0.22"},
        ]

        with patch("ImportPortalRate.connect_to_db_sql", return_value=mock_conn):
            count = ipr.insert_portal_rates(items, "20260201", "20260203")

        # 2 items * 3 days * 2 directions = 12
        assert count == 12
        assert mock_cursor.execute.call_count == 12
        mock_conn.commit.assert_called_once()

    def test_connection_failure_raises(self):
        with patch("ImportPortalRate.connect_to_db_sql", return_value=None):
            with pytest.raises(ConnectionError):
                ipr.insert_portal_rates([{"code": "EUR", "buyValue": "34.5"}], "20260201", "20260201")

    def test_write_failure_rollbacks(self):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB write error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("ImportPortalRate.connect_to_db_sql", return_value=mock_conn):
            with pytest.raises(Exception, match="DB write error"):
                ipr.insert_portal_rates([{"code": "EUR", "buyValue": "34.5"}], "20260201", "20260201")

        mock_conn.rollback.assert_called_once()

    def test_uses_sc_三旬匯率(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        items = [{"code": "EUR", "buyValue": "34.5"}]

        with patch("ImportPortalRate.connect_to_db_sql", return_value=mock_conn):
            ipr.insert_portal_rates(items, "20260201", "20260201")

        # Check the params passed to execute contain SC_三旬匯率
        call_args = mock_cursor.execute.call_args_list[0]
        params = call_args[0][1]
        assert "SC_三旬匯率" in params


# =========================================================================
# Group 3b: Oracle config loading
# =========================================================================

class TestLoadOracleConfig:
    """Tests for load_oracle_config()."""

    def test_returns_oracle_section(self, tmp_path):
        cfg = {
            "oracle_db_insert": {
                "user": "testuser",
                "password": "testpw",
                "host": "172.31.0.1",
                "port": "1521",
                "service_name": "ORCL",
                "client_lib_dir": "C:\\Oracle",
            }
        }
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = ipr.load_oracle_config(str(cfg_file))
        assert result["user"] == "testuser"
        assert result["service_name"] == "ORCL"


# =========================================================================
# Group 3c: Oracle database operations
# =========================================================================

class TestInsertPortalRatesOracle:
    """Tests for insert_portal_rates_oracle()."""

    def test_executemany_called(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        items = [
            {"code": "EUR", "buyValue": "34.5"},
            {"code": "JPY", "buyValue": "0.22"},
        ]

        with patch("ImportPortalRate.connect_to_db_oracle", return_value=mock_conn):
            count = ipr.insert_portal_rates_oracle(items, "20260201", "20260203")

        # 2 items * 3 days * 2 directions = 12
        assert count == 12
        mock_cursor.executemany.assert_called_once()
        rows = mock_cursor.executemany.call_args[0][1]
        assert len(rows) == 12
        mock_conn.commit.assert_called_once()

    def test_connection_failure_raises(self):
        with patch("ImportPortalRate.connect_to_db_oracle", return_value=None):
            with pytest.raises(ConnectionError):
                ipr.insert_portal_rates_oracle(
                    [{"code": "EUR", "buyValue": "34.5"}], "20260201", "20260201"
                )

    def test_write_failure_rollbacks(self):
        mock_cursor = MagicMock()
        mock_cursor.executemany.side_effect = Exception("Oracle write error")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("ImportPortalRate.connect_to_db_oracle", return_value=mock_conn):
            with pytest.raises(Exception, match="Oracle write error"):
                ipr.insert_portal_rates_oracle(
                    [{"code": "EUR", "buyValue": "34.5"}], "20260201", "20260201"
                )

        mock_conn.rollback.assert_called_once()

    def test_uses_sc_三旬匯率_in_oracle(self):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        items = [{"code": "EUR", "buyValue": "34.5"}]

        with patch("ImportPortalRate.connect_to_db_oracle", return_value=mock_conn):
            ipr.insert_portal_rates_oracle(items, "20260201", "20260201")

        rows = mock_cursor.executemany.call_args[0][1]
        for row in rows:
            assert row[-1] == "SC_三旬匯率"


# =========================================================================
# Group 4: Email
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
            patch("ImportPortalRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportPortalRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportPortalRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = ipr.send_email_notification(
                subject="Test", body="Hello", mailtype="success"
            )

        assert result is True
        mock_smtp_instance.sendmail.assert_called_once()

    def test_error_email_high_priority(self):
        mock_smtp_instance = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportPortalRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportPortalRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportPortalRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = ipr.send_email_notification(
                subject="Error", body="Oops", mailtype="error"
            )

        assert result is True
        raw_msg = mock_smtp_instance.sendmail.call_args[0][2]
        assert "X-Priority: 1" in raw_msg

    def test_smtp_failure_returns_false(self):
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(
            side_effect=smtplib.SMTPException("SMTP down")
        )
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("ImportPortalRate.load_email_config", return_value=_FAKE_EMAIL_CONFIG["email"]),
            patch("ImportPortalRate.smtplib.SMTP", mock_smtp_cls),
            patch("ImportPortalRate._load_template", return_value=Template("$body $table_html $image_html")),
        ):
            result = ipr.send_email_notification(
                subject="Test", body="Hello", mailtype="success"
            )

        assert result is False


# =========================================================================
# Group 5: Step Status HTML
# =========================================================================

class TestBuildStepStatusHtml:
    """Tests for _build_step_status_html()."""

    def test_all_success(self):
        steps = [
            ("Step 1: API", "OK"),
            ("Step 2: Excel", "Done"),
        ]
        html = ipr._build_step_status_html(steps)
        assert "#2e7d32" in html  # green
        assert "&#9989;" in html  # checkmark
        assert "#d32f2f" not in html  # no red

    def test_contains_failure(self):
        steps = [
            ("Step 1: API", "OK"),
            ("Step 2: SQL", "失敗 - connection error"),
        ]
        html = ipr._build_step_status_html(steps)
        assert "#d32f2f" in html  # red
        assert "&#10060;" in html  # cross mark


# =========================================================================
# Group 6: Portal Rate Table HTML
# =========================================================================

class TestBuildPortalRateTable:
    """Tests for build_portal_rate_table()."""

    def test_html_contains_currencies(self):
        items = [
            {"code": "EUR", "buyValue": "34.5", "conversion_rate": 1 / 34.5},
            {"code": "JPY", "buyValue": "0.22", "conversion_rate": 1 / 0.22},
        ]
        html = ipr.build_portal_rate_table(items, "20260201", "20260210")
        assert "EUR" in html
        assert "JPY" in html
        assert "34.5" in html
        assert 'id="portal-rates"' in html
        assert "20260201" in html
        assert "20260210" in html


# =========================================================================
# Group 7: Main flow
# =========================================================================

class TestMain:
    """Tests for main()."""

    def test_happy_path(self, tmp_path):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_oracle_cursor = MagicMock()
        mock_oracle_conn = MagicMock()
        mock_oracle_conn.cursor.return_value = mock_oracle_cursor

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _SAMPLE_JSON

        mock_writer = MagicMock()
        mock_writer.__enter__ = MagicMock(return_value=mock_writer)
        mock_writer.__exit__ = MagicMock(return_value=False)

        # Selective open mock: intercept JSON write but allow template reads
        real_open = open
        def selective_open(path, *args, **kwargs):
            if isinstance(path, str) and path.endswith('.json') and 'w' in (args[0] if args else kwargs.get('mode', 'r')):
                return MagicMock()
            return real_open(path, *args, **kwargs)

        with (
            patch("ImportPortalRate.requests.get", return_value=mock_resp),
            patch("ImportPortalRate.connect_to_db_sql", return_value=mock_conn),
            patch("ImportPortalRate.connect_to_db_oracle", return_value=mock_oracle_conn),
            patch("ImportPortalRate.send_email_notification", return_value=True),
            patch("ImportPortalRate.pd.ExcelWriter", return_value=mock_writer),
            patch("builtins.open", side_effect=selective_open),
            patch("ImportPortalRate.os.makedirs"),
        ):
            ipr.main()

        mock_conn.commit.assert_called_once()
        mock_oracle_conn.commit.assert_called_once()

    def test_fetch_failure_sends_error_email(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with (
            patch("ImportPortalRate.requests.get", return_value=mock_resp),
            patch("ImportPortalRate.send_email_notification", return_value=True) as mock_email,
        ):
            ipr.main()

        # Error email should have been sent
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args
        assert call_kwargs[1]["mailtype"] == "error"
