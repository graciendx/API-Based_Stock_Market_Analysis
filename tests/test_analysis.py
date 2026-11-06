import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import numpy as np
import pandas as pd

from equity_review.analysis import (build_analysis, calendar_series, complete_monthly_levels,
                                    drawdown, drawdown_episodes, read_series)
from equity_review.fred import fetch_observations

ROOT = Path(__file__).resolve().parents[1]


class EquityTests(unittest.TestCase):
    def test_terminal_partial_month_is_excluded(self):
        values = pd.DataFrame({"price": [100, 110, 112]}, index=pd.to_datetime(["2024-11-29", "2024-12-31", "2025-01-03"]))
        result = complete_monthly_levels(values, ["price"])
        self.assertEqual(list(map(str, result.index)), ["2024-11", "2024-12"])
        self.assertEqual(result.price.iloc[-1], 110)

    def test_missing_month_is_not_filled_or_shifted(self):
        frame = pd.DataFrame({"x": [100, 130]}, index=pd.to_datetime(["2020-01-01", "2020-03-01"]))
        result = calendar_series(frame, "x", "M")
        self.assertTrue(pd.isna(result.loc["2020-02"]))
        self.assertTrue(pd.isna(result.pct_change(fill_method=None).loc["2020-03"]))

    def test_duplicate_period_is_rejected(self):
        frame = pd.DataFrame({"x": [1, 2]}, index=pd.to_datetime(["2020-01-01", "2020-01-02"]))
        with self.assertRaises(ValueError): calendar_series(frame, "x", "M")

    def test_drawdown_uses_running_peak_not_future_peak(self):
        result = drawdown(pd.Series([100, 80, 120, 90]))
        np.testing.assert_allclose(result, [0, -.2, 0, -.25])

    def test_unrecovered_episode_has_no_fictitious_recovery(self):
        values = pd.Series([100, 80, 90], index=pd.date_range("2020-01-01", periods=3))
        row = drawdown_episodes(values).iloc[0]
        self.assertTrue(pd.isna(row.recovery_date))
        self.assertIsNone(row.calendar_days_peak_to_recovery)
        self.assertAlmostEqual(row.depth, -.2)

    def test_recovery_duration_uses_calendar_days(self):
        values = pd.Series([100, 70, 100], index=pd.to_datetime(["2020-01-01", "2020-01-10", "2020-02-01"]))
        row = drawdown_episodes(values).iloc[0]
        self.assertEqual(row.calendar_days_peak_to_recovery, 31)

    def test_source_duplicates_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("Date,SP500\n2020-01-01,100\n2020-01-01,101\n")
            with self.assertRaises(ValueError): read_series(path, "SP500")

    def test_fred_key_required_only_for_refresh(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "FRED_API_KEY"):
                fetch_observations("SP500", "2020-01-01", "2020-02-01")

    def test_request_errors_do_not_disclose_key(self):
        def fail(*args, **kwargs): raise URLError("private-test-value")
        with patch.dict("os.environ", {"FRED_API_KEY": "private-test-value"}):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_observations("SP500", "2020-01-01", "2020-02-01", opener=fail)
        self.assertNotIn("private-test-value", str(ctx.exception))

    def test_full_offline_run_matches_independent_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            report = build_analysis(ROOT / "data/snapshots", Path(directory))
            self.assertEqual(report["analysis_end_month"], "2024-12")
            self.assertEqual(report["benchmark_return_months"], 108)
            apple = pd.read_csv(ROOT / "data/snapshots/apple_stock.csv", index_col=0)
            expected = apple.loc["2024-12-31", "Close"] / apple.loc["2015-12-31", "Close"] - 1
            self.assertAlmostEqual(report["aapl_overlap"]["cumulative_price_return"], expected)
            comparison = pd.read_csv(Path(directory) / "benchmark_comparison.csv")
            self.assertEqual(len(comparison), 109)
            self.assertEqual(comparison.observation_date.iloc[-1], "2024-12-31")
            panel = pd.read_csv(Path(directory) / "monthly_context.csv")
            self.assertEqual(len(panel), 300)
            self.assertEqual(panel.recession.sum(), 28)
            self.assertTrue((Path(directory) / "figures/benchmark_and_drawdown.png").is_file())


if __name__ == "__main__": unittest.main()
