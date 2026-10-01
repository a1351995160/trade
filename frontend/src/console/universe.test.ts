import test from 'node:test'
import assert from 'node:assert/strict'
import { isFullUniverseDataset, submissionRequest, universeCoverage, universeDiagnosisAllowsFreeze, universeRunState, universeScanSummary, universeTaskAllowsResume } from './universe.ts'

const dataset = { dataset_id: 'whole', adapter: 'TDX_FULL_UNIVERSE_V1', universe_id: 'historical_main_and_chinext',
  target_count: 4536, completeness: 'UNIVERSE_COMPLETENESS_UNKNOWN', by_board: {
    SZ_MAIN: { target_count: 1478, cached_count: 1478, qualified_count: 1478, identity_unknown_count: 0 },
    SH_MAIN: { target_count: 1681, cached_count: 1681, qualified_count: 1681, identity_unknown_count: 0 },
    CHINEXT: { target_count: 1377, cached_count: 1377, identity_unknown_count: 0 },
  } }

test('full universe form sends a registered reference and never the typed eight-stock subset', () => {
  const request = submissionRequest({ strategy_id: 'RULE', dataset_id: 'whole', initial_cash: 50000,
    authorization_ref: 'FROZEN_AUTHORITY', symbols: ['000001.SZ'] }, dataset,
  '000001.SZ,600000.SH')
  assert.equal(request.version, 'FULL_UNIVERSE_SUBMISSION_V1')
  assert.equal(request.universe_id, dataset.universe_id)
  assert.equal(request.benchmark, 'CASH_AND_PRICE_REFERENCE')
  assert.equal('symbols' in request, false)
  assert.equal(request.initial_cash, 50000)
  assert.equal(request.authorization_ref, 'FROZEN_AUTHORITY')
})

test('legacy dataset uses its old explicit-symbol request without an automatic upgrade', () => {
  const legacy = { dataset_id: 'old', adapter: 'BAOSTOCK_RESPONSE_JSON_V1' }
  assert.equal(isFullUniverseDataset(legacy), false)
  const request = submissionRequest({ dataset_id: 'old', version: 'FULL_UNIVERSE_SUBMISSION_V1',
    universe_id: 'stale' }, legacy, '000001.SZ，600000.SH')
  assert.deepEqual(request.symbols, ['000001.SZ', '600000.SH'])
  assert.equal('version' in request, false)
  assert.equal('universe_id' in request, false)
  assert.equal(request.benchmark, 'FULL_POOL_BUY_HOLD')
})

test('4536 cached and 3159 qualified keeps the ChiNext gap visible', () => {
  const view = universeCoverage(dataset)
  assert.equal(view.target, '4,536')
  assert.match(view.completeness, /尚未证明/)
  assert.match(view.account, /尚未核验/)
  assert.equal(view.boards.length, 3)
  assert.deepEqual(view.boards.map(row => row.qualified), ['1,478', '1,681', '未知'])
  assert.equal(view.boards[2]!.name, '创业板')
  assert.equal(view.boards[2]!.target, '1,377')
})

test('input-only scan reports gaps without displaying a profit or a qualification', () => {
  const view = universeCoverage({ coverage: { target_symbol_count: 4536, account_data_ready: false,
    global_gaps: ['UNIVERSE_CORPORATE_COVERAGE_MISSING'], gaps: [{ symbol: '300001.SZ',
      reason: 'UNIVERSE_STATE_MISSING', count: 60 }] } })
  assert.match(view.account, /不能给出完整收益/)
  assert.equal(view.boards[2]!.target, '未知')
  assert.equal(view.gaps.length, 2)
  assert.match(universeRunState('PARTIAL_COMPLETED'), /尚未覆盖全部/)
  assert.match(universeRunState('RECONCILED_DIAGNOSTIC'), /不代表策略已证明有效/)
})

test('old publication is never a new-version acceptance label', () => {
  assert.match(universeRunState('ENGINEERING_NOT_ACCEPTED'), /尚无新版本完整工程/)
  assert.match(universeRunState('REAL_NOT_ACCEPTED'), /尚无真实全范围/)
  assert.equal(universeRunState('UNRECOGNIZED'), 'UNRECOGNIZED')
})

test('full-universe freeze requires a ready diagnostic bound to the current preview', () => {
  const diagnosis = { status: 'ACCOUNT_INPUTS_READY', scope: { preview_identity: 'same-preview' },
    coverage: { account_data_ready: true } }
  assert.equal(universeDiagnosisAllowsFreeze(diagnosis, 'same-preview'), true)
  assert.equal(universeDiagnosisAllowsFreeze(diagnosis, 'changed-preview'), false)
  assert.equal(universeDiagnosisAllowsFreeze(null, 'same-preview'), false)
  assert.equal(universeDiagnosisAllowsFreeze({ ...diagnosis, status: 'DATA_GAPS' }, 'same-preview'), false)
  assert.equal(universeDiagnosisAllowsFreeze({ ...diagnosis, coverage: { account_data_ready: false } }, 'same-preview'), false)
  assert.match(universeRunState('ACCOUNT_INPUTS_READY'), /账户执行仍需已有授权/)
})

test('all-unknown scan distinguishes processed stocks from evaluated conditions and zero hits', () => {
  const view = universeScanSummary({ target_count: 4607, processed_target_count: 4607,
    signals_evaluated_target_count: 0, signals_evaluated_session_count: 0, unknown_target_count: 4607,
    per_symbol: [{ symbol: '300001.SZ', status: 'UNKNOWN', evaluated_sessions: 0,
      unknown_sessions: 486, condition_counts: null }] })
  assert.equal(view.target, '4,607')
  assert.equal(view.processed, '4,607')
  assert.equal(view.evaluated, '0')
  assert.equal(view.anyEvaluated, false)
  assert.equal(view.rows[0]!.buy, '未知')
  assert.equal(view.rows[0]!.sell, '未知')
  assert.match(view.rows[0]!.status, /未知/)
  assert.match(universeRunState('SCAN_DATA_GAPS'), /保持未知/)
})

test('legitimately evaluated false conditions keep zero counts without an account-profit label', () => {
  const view = universeScanSummary({ target_count: 3, processed_target_count: 3,
    signals_evaluated_target_count: 1, signals_evaluated_session_count: 20, unknown_target_count: 2,
    per_symbol: [{ symbol: '000001.SZ', status: 'CONDITIONS_EVALUATED', evaluated_sessions: 20,
      unknown_sessions: 0, condition_counts: { buy: 0, sell: 0 } }] })
  assert.equal(view.anyEvaluated, true)
  assert.equal(view.rows[0]!.buy, '0')
  assert.equal(view.rows[0]!.unknown, '0')
  assert.match(view.rows[0]!.status, /未运行交易账户/)
})

test('manual resume appears only for the selected new-version unsettled account task', () => {
  const task = { task_id: 'new-task', version: 'UNIVERSE_TASK_METADATA_V1',
    items: { BASE: { state: 'UNSETTLED_CHECK_WORKER' }, STRESS: { state: 'NOT_STARTED' } } }
  assert.equal(universeTaskAllowsResume(task, 'new-task'), true)
  assert.equal(universeTaskAllowsResume(task, 'another-task'), false)
  assert.equal(universeTaskAllowsResume({ ...task, version: 'STRATEGY_SUBMISSION_V1' }, 'new-task'), false)
  assert.equal(universeTaskAllowsResume({ ...task, items: { BASE: { state: 'COMPLETED' } } }, 'new-task'), false)
  assert.equal(universeTaskAllowsResume(null, 'new-task'), false)
})
