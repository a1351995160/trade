# RESEARCH INTEGRITY POLICY

1. Every research result must carry PRE_FIX/POST_FIX lineage and the data-store hash used.
2. Acceptance status strings must be recomputed by IndependentAcceptanceValidator from raw results.
3. Required evidence completeness is checked by RequiredEvidenceCompletenessValidator before any Gate Complete.
4. Reports may never claim PASS for a condition that was NOT_APPLICABLE or DATA_UNAVAILABLE.
5. Mechanism claims must match implementation; proxy claims are labelled PROXY and capped at PROMISING.
6. Event evidence must report ALL_EVENTS and FIRST_EVENT_PER_SYMBOL; population must match strategy population.
7. Final Test status cannot return to SEALED-as-if-untouched after an access incident; use FINAL_TEST_PHYSICAL_ACCESS / FINAL_TEST_DECISION_EXPOSURE / FINAL_TEST_PROMOTION_ELIGIBLE.
