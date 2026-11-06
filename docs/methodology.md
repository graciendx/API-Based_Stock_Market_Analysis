# Methodological decisions

The original DATA 200 notebook and report explored Apple prices alongside FRED series. This publication extends that coursework with a reproducible, narrower risk question.

| Original issue | Published treatment |
| --- | --- |
| Apple snapshot ends January 3, 2025, while macro and index files extend later | Exclude the terminal month; Apple monthly results end December 2024 |
| Month-end joins could compare an early-month Apple price with a later index observation | Join actual daily dates first, then select each last common monthly observation |
| Adjusted Apple prices compared with a dividend-excluding S&P index | Compare Close with SP500; retain adjusted prices separately |
| Visual comparison described as evidence that fundamentals dominate macro factors | Report descriptive associations; no earnings data, causal design or attribution model is available |
| Return difference described as unexplained by beta | Remove that inference; no asset-pricing regression or risk-free series is included |
| Quarterly GDP dates treated like already-known monthly data | Retain observation-quarter context explicitly; do not claim release-time availability |
| Missing history and future display windows obscured coverage | Report actual coverage and avoid backfilling unknown periods |
| Hard-coded FRED credential | Use an environment variable and redact request failures; the previously exposed key needs rotation |
| Classroom exercise prompts and global mutable notebook state | Replace with a documented package and concise executable notebook |

## Metrics

Monthly price return is `P_t / P_(t-1) - 1`. Cumulative return compounds consecutive returns. Annualized geometric return compounds that wealth change to twelve months. Annualized volatility is sample monthly standard deviation times the square root of twelve. Drawdown is price divided by the running in-sample peak minus one.

Downside capture divides Apple's geometric mean monthly return by the benchmark's geometric mean monthly return, using only months in which the benchmark return is negative. Upside capture uses benchmark-positive months. These subset calculations are not a trading strategy or a compound investable return sequence.

Daily drawdown episodes use the 2000-2024 price sample, recording peak, trough and first recovery. An unrecovered episode has no assigned recovery date or completed recovery duration. Daily episodes and the month-end benchmark drawdowns have different frequencies and windows and must not be interchanged.

## Remaining uncertainty

The Apple file has no supplied vendor specification or retrieval timestamp. Its historical Close levels appear split-adjusted, but this convention was not independently verified. The original FRED notebook contains December 2025 retrieval metadata; exact export lineage for each supplied CSV is not fully established. Source hashes identify the files used here without inventing a common retrieval date.

The original coursework PDF is retained in Git history rather than presented as the current analysis because its causal and outperformance explanations exceed the evidence. Published results use the provided snapshots, not current market data. No statistical significance or generalization claim is made for the regime comparisons.
