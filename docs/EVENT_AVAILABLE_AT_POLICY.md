# EVENT AVAILABLE_AT POLICY

- YYYYMMDD+1 integer arithmetic is forbidden. Use `TradingCalendar.next_session()` or equivalent calendar logic.
- available_at is a timestamp, not just a date. Example: limit-up families `T 15:00:01 +08:00`; LHB families `T+1 08:00 +08:00` until vendor publish time is confirmed.
- `available_at` must be strictly before `earliest_signal_at`, which must be strictly before `earliest_execution`.
- Limit-up/consec/failed/seal state is final only after T close (15:00:00). Never set available_at earlier than T 15:00:00 for these families.
- LHB families with unconfirmed publish time get AVAILABLE_AT_CONFIDENCE=LOW and PROMOTION_CEILING=PROMISING.
- Event population must be explicit: ALL_EVENTS or FIRST_EVENT_PER_SYMBOL. Evidence population must match the strategy population.
