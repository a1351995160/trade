import test from 'node:test'
import assert from 'node:assert/strict'
import { candidatePresentation, displayAction, displayClassification, displayFactor, displayReason, displayState, freshnessLabel, humanDecisionRows, humanEvidenceRows, humanReportCategory, humanReportTitle, metricPresentation, objectivePresentation, termHelp, timingLabel } from './presentation.ts'

test('candidate resolver covers registered semantic prefixes', () => {
  const recentParticipation = candidatePresentation({ candidate_id: 'CAND_LIQUIDITY_PREMIUM_RECENT_PARTICIPATION_WITH_MEDI_8AC52656_V1_V2' })
  const intermediateStructure = candidatePresentation({ candidate_id: 'CAND_PRICE_STRUCTURE_INTERMEDIATE_DISTANCE_WITH_VOLUM_5433177A_V1_V2' })

  assert.equal(recentParticipation.covered, true)
  assert.equal(recentParticipation.name, '流动性溢价 + 参与质量')
  assert.equal(intermediateStructure.name, '价格结构 + 中期位置')
})

test('concept mappings keep unknown values explicit and safe', () => {
  assert.equal(displayFactor('MA_DISTANCE_20').label, '20日均线距离')
  assert.equal(displayFactor('limit_down_reversal_signal').label, '跌停反转信号')
  assert.equal(displayFactor('UNREGISTERED_FACTOR').known, false)
  assert.equal(displayState('BUDGET_EXHAUSTED').label, '预测试验预算已耗尽')
  assert.equal(displayReason('RAW_BOOTSTRAP_NOT_SUPPORTED').known, true)
  assert.equal(displayReason('A_UPPER_BOUND_BELOW_MINIMUM').label, '结构样本上界不足')
  assert.match(displayReason('A_UPPER_BOUND_BELOW_MINIMUM').description, /安全样本上界仍低于冻结政策要求/)
  assert.equal(freshnessLabel('CONFLICT').label, '不同来源更新时间不一致')
  assert.equal(displayState('ENGINEERING_BLOCKED').label, '工程问题导致研究暂停')
  assert.equal(displayState('STRUCTURAL_FAILED').label, '结构预检未通过')
  assert.equal(displayState('STRUCTURAL_UNKNOWN').label, '结构预检结果仍无法确认')
  assert.equal(displayReason('FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE').known, true)
  assert.match(displayReason('FROZEN_CONTRACT_PROVIDER_INCOMPATIBLE').description, /预测试验前阻断/)
  assert.equal(displayClassification('ENGINEERING_INVALIDATED').label, '因工程问题作废')
  assert.equal(displayClassification('BLOCKED').label, '预测验证未通过')
  assert.match(displayClassification('BLOCKED').description, /正式预测验证已完成/)
  const aiCandidate = candidatePresentation({ candidate_id: 'CAND_AI_V2_36A5071F_001', mechanism_family: 'event reversal' })
  assert.equal(aiCandidate.mechanism.label, '事件反转')
  assert.equal(aiCandidate.name, '事件反转候选策略')
  assert.match(displayAction('UNREGISTERED_ACTION'), /需要人工确认/)
})

test('canonical timing values have Chinese primary labels', () => {
  assert.equal(timingLabel('NEXT_SESSION_OPEN'), '下一交易日开盘')
  assert.equal(timingLabel('UNKNOWN_TIMING'), '当前合同未提供可读时点')
})

test('human evidence keeps unknown machine fields in technical data only', () => {
  const rows = humanEvidenceRows({ return: 0.12, max_drawdown: -0.03, internal_debug_value: 'hidden' })
  assert.deepEqual(rows.map((row) => row.label), ['累计收益率', '最大回撤'])
  assert.equal(rows.some((row) => row.key === 'internal_debug_value'), false)
})

test('human evidence maps authorized nested trial metrics', () => {
  const rows = humanEvidenceRows({ base_metrics: { net_return: 0.2, closed_trade_count: 12 }, bootstrap: { p_value: 0.04 }, final_decision: { adjusted_p: 0.08 } })
  assert.deepEqual(rows.map((row) => row.label), ['累计收益率', '交易次数', '自助法重采样显著性（Bootstrap）', '多重检验校正后显著性'])
})

test('human decision explains engineering invalidation without changing the machine value', () => {
  const rows = humanDecisionRows({ classification: 'ENGINEERING_INVALIDATED', final_adjudicated: false, final_test_access: { analytical: 0, decision: 0, physical: 0 }, prospective: 0, real_order: 'DISABLED' })
  assert.equal(rows.find((row) => row.key === 'classification')?.value, '因工程问题作废')
  assert.equal(rows.find((row) => row.key === 'final_test_access')?.value, '否')
  assert.equal(rows.find((row) => row.key === 'real_order')?.value, '否')
})

test('report presentation removes unexplained product-code words from primary labels', () => {
  assert.equal(humanReportTitle({ title_zh: 'Research Daemon 状态机' }), '研究守护进程状态机')
  assert.equal(humanReportCategory('Shadow'), '每日研究观察')
})

test('required terminology has a Chinese first presentation', () => {
  assert.equal(objectivePresentation('RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1').name, 'A股短周期策略研究 V1')
  assert.match(termHelp('PIT'), /未来数据泄漏/)
  assert.match(termHelp('Bootstrap'), /重复抽样/)
  assert.match(termHelp('BH'), /多重检验校正/)
  assert.equal(metricPresentation('return').label, '累计收益率')
  assert.equal(metricPresentation('adjusted_p_value').label, '多重检验校正后显著性')
})
