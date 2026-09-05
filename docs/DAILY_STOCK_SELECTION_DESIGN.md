# DAILY STOCK SELECTION DESIGN

## Flow

```text
Trading Date -> PIT Universe -> Market Regime -> Factor Snapshot -> Approved Strategy Signals
-> Candidate Merge -> Risk Filter -> Ranking -> Top N -> Immutable snapshot
```

## Strategy policy

- ROBUST strategies -> PRODUCTION_CANDIDATES (currently 0).
- PROMISING factors/strategies -> EXPERIMENTAL_WATCHLIST, separated from production.
- No qualifying candidates -> NO TRADE.

## Ranking

First version: equal-weight average of approved factors' cross-sectional percentile rank. No historical weight search.

## Output fields

symbol, rank, score, candidate_type, triggered_factors, supporting_events, negative_factors, market_regime, suggested_horizon, confidence_tier, risk_flags, data_timestamp.
