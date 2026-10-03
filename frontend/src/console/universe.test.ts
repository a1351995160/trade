import test from 'node:test'
import assert from 'node:assert/strict'
import { isFullUniverseDataset, submissionRequest, universeCoverage, universeDiagnosisAllowsFreeze, universeRunState, universeTaskState, universeScanSummary, universeTaskAllowsResume, universeTaskAllowsPause, universeLongProgress } from './universe.ts'

const dataset = { dataset_id: 'whole', adapter: 'TDX_FULL_UNIVERSE_V1', universe_id: 'historical_main_and_chinext',
  target_count: 4536, completeness: 'UNIVERSE_COMPLETENESS_UNKNOWN', by_board: {
    SZ_MAIN: { target_count: 1478, cached_count: 1478, qualified_count: 1478, identity_unknown_count: 0 },
    SH_MAIN: { target_count: 1681, cached_count: 1681, qualified_count: 1681, identity_unknown_count: 0 },
    CHINEXT: { target_count: 1377, cached_count: 1377, identity_unknown_count: 0 },
  } }

test('qualified mode still checks the registered whole pool and cannot retain stale custom symbols', () => {
  const request = submissionRequest({ dataset_id: 'whole', symbols: ['000001.SZ'] }, dataset, '000001.SZ', 'DATA_QUALIFIED')
  assert.equal(request.version, 'FULL_UNIVERSE_SUBMISSION_V2')
  assert.equal(request.account_scope, 'DATA_QUALIFIED')
  assert.equal('symbols' in request, false)
  const legacy = submissionRequest(request, { dataset_id: 'old' }, '600000.SH', 'DATA_QUALIFIED')
  assert.equal('account_scope' in legacy, false)
  assert.equal('version' in legacy, false)
})

test('qualified freeze requires its matching preview and affirmative scope check', () => {
  const record = { status: 'QUALIFIED_SCOPE_READY', qualified_account_ready: true, scope: { preview_identity: 'bound' }, coverage: { account_data_ready: false } }
  assert.equal(universeDiagnosisAllowsFreeze(record, 'bound'), true)
  assert.equal(universeDiagnosisAllowsFreeze(record, 'different'), false)
  assert.equal(universeDiagnosisAllowsFreeze({ ...record, qualified_account_ready: false }, 'bound'), false)
  assert.equal(universeDiagnosisAllowsFreeze({ ...record, status: 'QUALIFIED_SCOPE_BLOCKED' }, 'bound'), false)
  assert.match(universeRunState('DATA_QUALIFIED'), /是否买入由策略决定/)
  assert.match(universeRunState('EXCLUDED'), /本次账户范围排除/)
})

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

test('explicit long version binds registered descriptor and observations without narrowing whole scope', () => {
  const profile = { profile_id: 'LONG_HORIZON_SEGMENTED_V1', account_sessions: 252,
    registry_hash: 'registered', profile_hash: 'frozen', worker_seconds: 900 }
  const request = submissionRequest({ account_start: '2023-07-20', account_end: '2024-07-31',
    symbols: ['000001.SZ'], rule: { version: 'RESEARCH_RULE_STRATEGY_V4' } }, dataset,
  '000001.SZ', 'FULL_REQUIRED', profile)
  assert.equal(request.version, 'FULL_UNIVERSE_SUBMISSION_V3')
  assert.equal(request.account_scope, 'DATA_QUALIFIED')
  assert.equal('symbols' in request, false)
  assert.deepEqual(request.execution_profile, profile)
  assert.deepEqual(request.observation_plan, { version: 'SIGNAL_OBSERVATION_PLAN_V1', horizons: [5, 10, 20],
    entry: 'NEXT_EXCHANGE_SESSION_RAW_OPEN', exit: 'HORIZON_SESSION_RAW_CLOSE',
    price_policy: 'FRACTIONAL_ONE_SHARE_ENTITLED_ECONOMIC_VALUE_V1',
    comparator: 'QUALIFIED_SCOPE_EQUAL_WEIGHT_SAME_ENTRY_HORIZON', start: 20230720, end: 20240731 })
  const legacy = submissionRequest(request, { dataset_id: 'old' }, '600000.SH')
  assert.equal('execution_profile' in legacy, false)
  assert.equal('observation_plan' in legacy, false)
  assert.equal('version' in legacy, false)
})

test('new pause and resume buttons respect selected task and actual persisted execution state', () => {
  const task = { task_id: 'long', version: 'UNIVERSE_TASK_METADATA_V1',
    execution_profile: { profile_id: 'LONG_HORIZON_SEGMENTED_V1' },
    items: { BASE: { state: 'RUNNING' } } }
  assert.equal(universeTaskAllowsPause(task, 'long'), true)
  assert.equal(universeTaskAllowsPause(task, 'another'), false)
  assert.equal(universeTaskAllowsResume(task, 'long'), false)
  for (const state of ['PAUSED', 'READY_TO_CONTINUE', 'RESUME_REQUIRED']) {
    const paused = { ...task, items: { BASE: { state } } }
    assert.equal(universeTaskAllowsResume(paused, 'long'), true)
    assert.equal(universeTaskAllowsPause(paused, 'long'), false)
  }
  assert.equal(universeTaskAllowsResume({ ...task, items: { BASE: { state: 'FAILED' } } }, 'long'), false)
  assert.equal(universeTaskAllowsPause({ ...task, execution_profile: {} }, 'long'), false)
  const report = { ...task, stage: 'REPORT', items: { BASE: { state: 'COMPLETED' } },
    compute_stages: { REPORT: { state: 'RUNNING' } } }
  assert.equal(universeTaskAllowsPause(report, 'long'), true)
  assert.equal(universeTaskAllowsResume(report, 'long'), false)
  assert.equal(universeTaskAllowsResume({ ...report, paused: true }, 'long'), true)
  assert.equal(universeTaskAllowsResume({ ...report,
    compute_stages: { REPORT: { state: 'RESUME_REQUIRED' } } }, 'long'), true)
  for (const state of ['COMPLETED', 'FAILED']) {
    const terminal = { ...report, paused: true, compute_stages: { REPORT: { state } } }
    assert.equal(universeTaskAllowsPause(terminal, 'long'), false)
    assert.equal(universeTaskAllowsResume(terminal, 'long'), false)
  }
})

test('long progress keeps account sessions and runtime charges distinct and never fills absent evidence with zero', () => {
  const view = universeLongProgress({ execution_profile: { account_sessions: 504, worker_seconds: 900, memory_mib: 2048 },
    items: { BASE: { state: 'PAUSED', phase: 'ACCOUNT', processed_sessions: 120, account_sessions: 504,
      last_day: 20230103, charged_seconds: 850.25, remaining_seconds: 27949.75 }, STRESS: { state: 'NOT_STARTED' } } })!
  assert.equal(view.sessionBound, '504')
  assert.equal(view.rows[0]!.processed, '120')
  assert.equal(view.rows[0]!.chargedSeconds, '850.3')
  assert.equal(view.rows[1]!.processed, '未知')
  assert.equal(view.rows[1]!.chargedSeconds, '未知')
  assert.match(view.rows[0]!.state, /安全边界暂停/)
  assert.equal(universeLongProgress(null), null)
  const reports = universeLongProgress({ stage: 'REPORT',
    execution_profile: { account_sessions: 504 },
    compute_stages: { VERIFICATION: { state: 'COMPLETED', charged_seconds: 25, remaining_seconds: 30 },
      REPORT: { state: 'READY_TO_CONTINUE', charged_seconds: 899.5, remaining_seconds: 2800 } } })!
  assert.equal(reports.stage, '双报告')
  assert.equal(reports.stages[1]!.name, '双报告')
  assert.match(reports.stages[1]!.state, /原用途可继续/)
  assert.equal(reports.stages[1]!.chargedSeconds, '899.5')
  assert.equal(reports.stages[1]!.remainingSeconds, '2800.0')
})

test('legacy completed purposes are displayed without inventing verification or strategy qualification', () => {
  const old = { total: 2, items: { BASE: { state: 'COMPLETED' }, STRESS: { state: 'COMPLETED' } } }
  assert.match(universeTaskState(old), /全部账户用途已完成/)
  assert.match(universeTaskState(old), /分别查看证据/)
  assert.equal(universeTaskState({ ...old, total: 3 }), '尚未提供状态')
  assert.equal(universeTaskState({ ...old, status: 'REPORT_RUNNING' }), '正在生成账户与信号两份报告')
  assert.equal(universeTaskState({ ...old, items: { BASE: { state: 'FAILED' } } }), '尚未提供状态')
})
