# RESEARCH PLATFORM V1 FREEZE

## Freeze id

`RESEARCH_PLATFORM_V1_FREEZE`

## Git

- repo: `E:/llmwiki/chanlun-trading-system`
- tag: `quant-research-platform-v1`
- commit: `20ebec1`

## Frozen objects

- 18 PROMISING Factor definitions (factor_definition_hash computed, definitions unchanged)
- 58 previous hypotheses (`data/research/hypothesis_ledger.jsonl`)
- ExperimentLedger (`data/research/experiment_ledger/experiment_ledger.parquet`)
- FailureLibrary (`data/research/failure_library.parquet`)
- FactorLibrary (`data/research/factor_library/library.json`)
- StrategyLibrary (`data/research/strategy_library/library.json`)
- PromotionRecords (`data/research/promotion_records`)
- Daily Selector v1 (`data/research/daily_selection`)
- M9 Acceptance (`reports/M9_ACCEPTANCE.json`, `reports/QUANT_RESEARCH_PLATFORM_FINAL_ACCEPTANCE.md`)
- BT_ENGINE_V2 version + Data version recorded in freeze manifest

## Factor definition hashes (first 16 chars)

- `F_GAP` `8b4b1a490a72a6d8` status=PROMISING
- `F_IND_DISP_LOW` `4f158ce6acf74335` status=PROMISING
- `F_LOWAMT` `60dfebc079008706` status=PROMISING
- `F_LOWVOLBURST` `b81e232479bf2d3a` status=PROMISING
- `F_REV20` `2a7abd6f89de8e5d` status=PROMISING
- `F_REV5` `1e3bd9958dce1a9e` status=PROMISING
- `F_UP5_INV` `c4343cf31393f4cb` status=PROMISING
- `F_VOLCOMP_LOW` `b36ece500e12a80a` status=PROMISING
- `R2_GAP_NOTUP5` `e1dd597f778b5748` status=PROMISING
- `R2_LOWAMT_HIGHPRICE` `8ff32fc942752240` status=PROMISING
- `R2_LOWAMT_TOPLIQ` `9c7d80022acbf608` status=PROMISING
- `R2_LOWVOL_TOPLIQ` `7b0c861d81d0c149` status=PROMISING
- `R2_REV10` `6938a8dd692b168f` status=PROMISING
- `R2_REV20_INDUP` `575bafd31004264c` status=PROMISING
- `R2_REV20_NOLIMIT` `3f052df601795dbd` status=PROMISING
- `R2_REV20_TOPLIQ` `4e7f60480237137f` status=PROMISING
- `R2_REV30` `c00e2a1e5113db63` status=PROMISING
- `R2_REV_COMPOSITE` `9e1b2fa058fdc64e` status=PROMISING

## Rule

上一阶段结果只能 reference / reclassify / supersede，禁止 overwrite / delete / silently modify。
