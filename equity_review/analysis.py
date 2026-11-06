"""Build complete-period equity comparisons from local coursework snapshots.

Macro labels describe observation periods ex post. They are not tradable signals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

SERIES = {
    "fred_cpi.csv": ("Inflation_CPI", "CPIAUCSL", "monthly"),
    "fred_fedfunds.csv": ("Fed_Funds_Rate", "FEDFUNDS", "monthly"),
    "fred_gdp.csv": ("GDP", "GDPC1", "quarterly"),
    "fred_recession.csv": ("Recession", "USREC", "monthly"),
    "fred_sp500.csv": ("SP500", "SP500", "daily"),
}


def read_series(path: Path, value: str, apple: bool = False) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame.rename(columns={"Unnamed: 0": "Date", "Adj Close": "Adj_Close"})
    required = ["Date", value] + (["Adj_Close", "Open", "High", "Low", "Volume"] if apple else [])
    if not set(required).issubset(frame):
        raise ValueError(f"{path.name}: missing required columns")
    frame["Date"] = pd.to_datetime(frame["Date"], errors="raise")
    if frame["Date"].isna().any() or frame["Date"].duplicated().any():
        raise ValueError(f"{path.name}: missing or duplicate dates")
    for column in required[1:]:
        original = frame[column]
        frame[column] = pd.to_numeric(original.replace(".", np.nan), errors="raise")
        if np.isinf(frame[column]).any():
            raise ValueError(f"{path.name}: infinite values")
        if apple and (frame[column].isna().any() or (frame[column] < 0).any()):
            raise ValueError(f"{path.name}: invalid equity observation")
    if value in {"Close", "SP500", "Inflation_CPI", "GDP"} and (frame[value].dropna() <= 0).any():
        raise ValueError(f"{path.name}: levels must be positive")
    if value == "Recession" and not frame[value].isin([0, 1]).all():
        raise ValueError("Recession must be binary")
    if apple and ((frame.High < frame[["Open", "Close", "Low"]].max(axis=1) - 1e-6).any()
                  or (frame.Low > frame[["Open", "Close", "High"]].min(axis=1) + 1e-6).any()):
        raise ValueError("Inconsistent OHLC bounds")
    return frame.sort_values("Date").set_index("Date")


def calendar_series(frame: pd.DataFrame, column: str, frequency: str) -> pd.Series:
    """Represent missing calendar periods explicitly, rather than shifting by rows."""
    values = frame[column].copy()
    values.index = values.index.to_period(frequency)
    if values.index.duplicated().any():
        raise ValueError(f"Duplicate {frequency} observations")
    return values.reindex(pd.period_range(values.index.min(), values.index.max(), freq=frequency))


def complete_monthly_levels(daily: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Conservatively exclude the snapshot's terminal calendar month.

    This avoids inferring completeness from a final trading date without an
    exchange calendar. A fully observed terminal month is also excluded.
    """
    terminal = daily.index.max().to_period("M")
    frame = daily[columns].copy()
    frame["observation_date"] = frame.index
    frame["month"] = frame.index.to_period("M")
    frame = frame.loc[frame["month"] < terminal]
    result = frame.groupby("month").last()
    return result.reindex(pd.period_range(result.index.min(), result.index.max(), freq="M"))


def drawdown(values: pd.Series) -> pd.Series:
    if values.empty or values.isna().any() or (values <= 0).any():
        raise ValueError("Drawdown requires nonempty positive levels")
    return values / values.cummax() - 1


def risk_statistics(returns: pd.Series, levels: pd.Series) -> dict:
    returns = returns.dropna()
    if len(returns) < 2:
        raise ValueError("At least two monthly returns required")
    return {
        "months": int(len(returns)),
        "cumulative_price_return": float((1 + returns).prod() - 1),
        "annualized_geometric_price_return": float((1 + returns).prod() ** (12 / len(returns)) - 1),
        "annualized_monthly_volatility": float(returns.std(ddof=1) * np.sqrt(12)),
        "worst_month": float(returns.min()),
        "negative_month_share": float((returns < 0).mean()),
        "maximum_month_end_drawdown": float(drawdown(levels).min()),
    }


def drawdown_episodes(levels: pd.Series, top: int = 5) -> pd.DataFrame:
    """Peak-to-recovery episodes; unrecovered durations are explicitly censored."""
    peak_date, peak = levels.index[0], float(levels.iloc[0])
    trough_date, trough = peak_date, peak
    underwater = False
    episodes = []
    for when, value in levels.iloc[1:].items():
        if value >= peak:
            if underwater:
                episodes.append({"peak_date": peak_date, "trough_date": trough_date,
                                 "recovery_date": when, "depth": trough / peak - 1,
                                 "calendar_days_peak_to_recovery": (when - peak_date).days,
                                 "status": "recovered"})
            peak_date, peak, trough_date, trough = when, float(value), when, float(value)
            underwater = False
        else:
            underwater = True
            if value < trough:
                trough_date, trough = when, float(value)
    if underwater:
        episodes.append({"peak_date": peak_date, "trough_date": trough_date,
                         "recovery_date": pd.NaT, "depth": trough / peak - 1,
                         "calendar_days_peak_to_recovery": None, "status": "not recovered at sample end"})
    return pd.DataFrame(episodes).sort_values("depth").head(top).reset_index(drop=True) if episodes else pd.DataFrame()


def regime_summary(panel: pd.DataFrame) -> pd.DataFrame:
    records = []
    for variable in ["recession", "inflation_regime", "rate_regime"]:
        for label, group in panel.dropna(subset=[variable, "aapl_return"]).groupby(variable):
            r = group["aapl_return"]
            records.append({"classification": variable, "regime": str(label), "months": len(r),
                            "mean_monthly_price_return": r.mean(), "median_monthly_price_return": r.median(),
                            "negative_month_share": (r < 0).mean(), "worst_monthly_price_return": r.min()})
    return pd.DataFrame(records)


def build_analysis(data_dir: Path, output_dir: Path, start: str = "2000-01") -> dict:
    start_period = pd.Period(start, freq="M")
    apple = read_series(data_dir / "apple_stock.csv", "Close", apple=True)
    sources = {name: read_series(data_dir / name, spec[0]) for name, spec in SERIES.items()}
    monthly = complete_monthly_levels(apple, ["Close", "Adj_Close"])
    monthly["aapl_return"] = monthly.Close.pct_change(fill_method=None)
    monthly["aapl_adjusted_return"] = monthly.Adj_Close.pct_change(fill_method=None)
    cpi = calendar_series(sources["fred_cpi.csv"], "Inflation_CPI", "M")
    rate = calendar_series(sources["fred_fedfunds.csv"], "Fed_Funds_Rate", "M")
    recession = calendar_series(sources["fred_recession.csv"], "Recession", "M")
    gdp = calendar_series(sources["fred_gdp.csv"], "GDP", "Q")
    monthly["inflation_yoy_pct"] = cpi.pct_change(12, fill_method=None) * 100
    monthly["funds_rate_pct"] = rate
    monthly["funds_rate_change_12m_pp"] = rate.diff(12)
    monthly["recession"] = recession
    # Quarterly period labels map to their own quarter, never to a prior one.
    growth = gdp.pct_change(4, fill_method=None) * 100
    monthly["real_gdp_yoy_pct"] = growth.reindex(monthly.index.asfreq("Q")).to_numpy()
    monthly["inflation_regime"] = pd.Series(pd.NA, index=monthly.index, dtype="object")
    known = monthly.inflation_yoy_pct.notna()
    monthly.loc[known, "inflation_regime"] = np.where(monthly.loc[known, "inflation_yoy_pct"] >= 4, ">=4% CPI YoY", "<4% CPI YoY")
    monthly["rate_regime"] = pd.Series(pd.NA, index=monthly.index, dtype="object")
    known = monthly.funds_rate_change_12m_pp.notna()
    monthly.loc[known, "rate_regime"] = np.select(
        [monthly.loc[known, "funds_rate_change_12m_pp"] >= 1,
         monthly.loc[known, "funds_rate_change_12m_pp"] <= -1],
        ["Rising >=1pp over 12m", "Falling >=1pp over 12m"], default="Change within +/-1pp")
    panel = monthly.loc[monthly.index >= start_period].copy()
    if len(panel) < 24 or panel.Close.isna().any() or panel.aapl_return.isna().any():
        raise ValueError("Require at least 24 consecutive complete return months")
    # Join actual dates first; then take the last common observation each month.
    common = apple[["Close"]].join(sources["fred_sp500.csv"][["SP500"]], how="inner").dropna()
    overlap = complete_monthly_levels(common, ["Close", "SP500"])
    overlap = overlap.loc[(overlap.index <= panel.index.max()) & (overlap.index >= start_period - 1)]
    if len(overlap) < 25 or overlap[["Close", "SP500"]].isna().any().any():
        raise ValueError("Require at least 24 consecutive benchmark return months")
    overlap["aapl_return"] = overlap.Close.pct_change(fill_method=None)
    overlap["sp500_return"] = overlap.SP500.pct_change(fill_method=None)
    overlap["aapl_index"] = 100 * overlap.Close / overlap.Close.iloc[0]
    overlap["sp500_index"] = 100 * overlap.SP500 / overlap.SP500.iloc[0]
    overlap["relative_wealth"] = overlap.aapl_index / overlap.sp500_index
    returns = overlap.dropna(subset=["aapl_return", "sp500_return"])
    down = returns.loc[returns.sp500_return < 0]
    up = returns.loc[returns.sp500_return > 0]
    # Capture ratios use geometric average monthly returns in the same subset.
    def capture(subset):
        if subset.empty:
            return None
        a = (1 + subset.aapl_return).prod() ** (1 / len(subset)) - 1
        b = (1 + subset.sp500_return).prod() ** (1 / len(subset)) - 1
        return float(a / b) if abs(b) > 1e-12 else None
    result = {
        "title": "Apple Equity Risk and Macroeconomic Context", "author": "Grace Ngo",
        "scope": "Historical, descriptive analysis; observation-period macro labels, not real-time signals.",
        "analysis_start_month": str(panel.index.min()), "analysis_end_month": str(panel.index.max()),
        "apple_snapshot_last_date": str(apple.index.max().date()),
        "excluded_terminal_month": str(apple.index.max().to_period("M")),
        "benchmark_base_date": str(overlap.observation_date.iloc[0].date()),
        "benchmark_end_date": str(overlap.observation_date.iloc[-1].date()),
        "benchmark_return_months": len(returns),
        "aapl_overlap": risk_statistics(returns.aapl_return, overlap.Close),
        "sp500_overlap": risk_statistics(returns.sp500_return, overlap.SP500),
        "relative_wealth_at_end": float(overlap.relative_wealth.iloc[-1]),
        "down_market_months": len(down), "up_market_months": len(up),
        "aapl_mean_return_in_down_market_months": float(down.aapl_return.mean()),
        "sp500_mean_return_in_down_market_months": float(down.sp500_return.mean()),
        "downside_capture_ratio": capture(down), "upside_capture_ratio": capture(up),
        "monthly_return_correlation": float(returns.aapl_return.corr(returns.sp500_return)),
        "limitations": ["Price-only benchmark comparison; dividends, fees and taxes excluded.",
                        "Snapshot values may be revised; macro labels are known ex post.",
                        "One surviving company; no causal attribution, alpha estimate or trading backtest.",
                        "Volatility annualization uses sqrt(12); month-end drawdown misses intramonth troughs.",
                        "Price and adjusted-price vendor provenance and retrieval date were not supplied."],
        "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir.glob("*.csv"))},
        "environment": {"pandas": pd.__version__, "numpy": np.__version__},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "monthly_context.csv", index_label="month", float_format="%.10g")
    overlap.to_csv(output_dir / "benchmark_comparison.csv", index_label="month", float_format="%.10g")
    regime_summary(panel).to_csv(output_dir / "regime_summary.csv", index=False, float_format="%.10g")
    daily_sample = apple.loc[(apple.index.to_period("M") >= start_period) & (apple.index.to_period("M") <= panel.index.max()), "Close"]
    drawdown_episodes(daily_sample).to_csv(output_dir / "drawdown_episodes.csv", index=False, date_format="%Y-%m-%d")
    (output_dir / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    create_figures(panel, overlap, output_dir / "figures")
    return result


def create_figures(panel: pd.DataFrame, overlap: pd.DataFrame, output: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold", "figure.facecolor": "white"})
    dates = overlap.index.to_timestamp("M")
    fig, axes = plt.subplots(2, 1, figsize=(10.8, 7), sharex=True, gridspec_kw={"height_ratios": [1.4, 1]})
    for col, name, color in [("aapl_index", "Apple close", "#772c44"), ("sp500_index", "S&P 500 price index", "#285d70")]:
        axes[0].plot(dates, overlap[col], label=name, color=color, linewidth=2)
        axes[1].plot(dates, drawdown(overlap[col]), color=color, linewidth=1.5)
    axes[0].set(title="Apple's price growth came with deeper drawdowns", ylabel="Price index (base = 100)")
    axes[0].legend(frameon=False)
    axes[1].set(ylabel="Drawdown from in-sample peak", xlabel="Month end")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    for ax in axes: ax.grid(axis="y", alpha=.18)
    fig.text(.07, .015, "Complete periods on common observation dates. Price returns exclude dividends. Source: supplied AAPL and FRED snapshots.", fontsize=8)
    fig.tight_layout(rect=[0, .045, 1, 1]); fig.savefig(output / "benchmark_and_drawdown.png", dpi=180); plt.close(fig)
    regimes = regime_summary(panel)
    selected = regimes.loc[regimes.classification == "rate_regime"].copy()
    fig, ax = plt.subplots(figsize=(10.8, 4.8))
    labels = [f"{r.regime}\n(n={r.months} months)" for r in selected.itertuples()]
    bars = ax.barh(labels, selected.mean_monthly_price_return, color="#285d70", height=.52)
    for bar, val in zip(bars, selected.mean_monthly_price_return):
        ax.annotate(f"{val:.2%}", (val, bar.get_y()+bar.get_height()/2), xytext=(5,0), textcoords="offset points", va="center")
    ax.axvline(0, color="#888", linewidth=.8); ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.set(title="Apple monthly returns across retrospective rate environments", xlabel="Mean monthly price return")
    ax.margins(x=.3); ax.grid(axis="x", alpha=.18)
    fig.text(.03, .015, "Rate regimes use the contemporaneous 12-month change in FEDFUNDS. Descriptive association; no causal or timing claim.", fontsize=8)
    fig.tight_layout(rect=[0,.05,1,1]); fig.savefig(output / "rate_context.png", dpi=180); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--output", type=Path, default=Path("reports"))
    args = parser.parse_args()
    if args.output.resolve() == args.data.resolve() or args.output.resolve().is_relative_to(args.data.resolve()):
        parser.error("Output must be separate from source data")
    result = build_analysis(args.data, args.output)
    print(json.dumps({k: result[k] for k in ["analysis_end_month", "benchmark_return_months", "downside_capture_ratio"]}, indent=2))


if __name__ == "__main__":
    main()
