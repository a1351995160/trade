# EVENT RESEARCH STANDARD

## Pipeline

1. `EventDefinition` with `event_time_semantics` / `available_at_semantics`.
2. `EventStore` stores ENTER/STAY/EXIT/REENTER with `available_at`.
3. Event Study uses tradable forward excess (T+1 open entry, T+h close exit, minus same-date all-market mean).
4. Report N/Mean/Median/WinRate/Std/Q05/Q25/Q75/Q95/MAE/MFE.
5. Controls: same-date market-matched + random control; sector-matched where applicable.
6. Extreme period 2024-09-24~2024-10-08 reported separately; Top1/Top3/Top5/Top10 concentration reported.

## M6 conclusion

LHB / LimitUp events had positive mean but win rate generally < 0.50 (right-skewed), not PROMISING. No ROBUST event alpha.
