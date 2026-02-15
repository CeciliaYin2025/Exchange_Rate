"""Tests for ImportMonthlyRate.py (deprecated script - minimal coverage)"""

import warnings
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import yaml


# =========================================================================
# Group 1: Deprecation warning
# =========================================================================

class TestDeprecation:
    """Test that importing the module emits a DeprecationWarning."""

    def test_import_emits_deprecation_warning(self):
        # Force re-import to trigger the warning
        import importlib
        import sys

        # Remove from cache if present
        if "ImportMonthlyRate" in sys.modules:
            del sys.modules["ImportMonthlyRate"]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import ImportMonthlyRate
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) >= 1
            assert "deprecated" in str(deprecation_warnings[0].message).lower() or \
                   "棄用" in str(deprecation_warnings[0].message)


# =========================================================================
# Group 2: Date calculation
# =========================================================================

class TestDateCalculation:
    """Tests for date period functions."""

    def test_get_prev_month_period(self):
        import ImportMonthlyRate as imr
        first, last = imr.get_prev_month_period()

        # first should be day 1 of some month
        assert first.day == 1
        # last should be the last day of that same month
        assert last.month == first.month
        assert (last + timedelta(days=1)).day == 1

    def test_get_current_month_period(self):
        import ImportMonthlyRate as imr
        start, end = imr.get_current_month_period()

        today = datetime.today()
        assert start.month == today.month
        assert start.day == 1


# =========================================================================
# Group 3: Config loading
# =========================================================================

class TestLoadDbConfig:
    """Tests for load_db_config()."""

    def test_returns_dev_db(self, tmp_path):
        import ImportMonthlyRate as imr
        cfg = {"dev_db": {"server": "dev-server", "database": "dev_db"}}
        cfg_file = tmp_path / "db_config.yaml"
        cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

        result = imr.load_db_config(str(cfg_file))
        assert result["server"] == "dev-server"
