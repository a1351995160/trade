import test from 'node:test'
import assert from 'node:assert/strict'
import { canCreateBinding, canOperate, lifecycleState, observationDays, qualificationText, sourceText } from './lifecycle.ts'

test('completion never becomes effectiveness or real observation', () => {
  assert.equal(lifecycleState('COMPLETED'), '指定阶段已完成')
  assert.equal(observationDays({ status: 'COMPLETED' }), '尚无观察日证据')
  assert.match(qualificationText({ status: 'COMPLETED' }), /尚未取得/)
  assert.match(qualificationText({ profile: 'SYNTHETIC', strategy_qualified: true }), /不授予真实资格/)
})
test('real records and unknown states remain explicit', () => {
  assert.equal(observationDays({ real_observation_days: 0 }), '0 天')
  assert.equal(observationDays({ real_observation_days: -1 }), '尚无观察日证据')
  assert.match(lifecycleState('NEW_STATUS'), /NEW_STATUS/)
  assert.match(sourceText({ profile: 'HISTORICAL_MODELED' }), /采用模型/)
})
test('only server registered jobs and bindings gain browser actions', () => {
  const view = { bindings: {}, binding_catalog: {}, jobs: {}, background_enabled: false, actions_allowed: true,
    operation_permissions: { job_ids: ['registered'], create_binding_ids: ['trusted'] } }
  assert.equal(canOperate(view, { profile: 'REAL', status: 'READY' }, 'registered'), true)
  assert.equal(canOperate(view, { profile: 'REAL', status: 'READY' }, 'other'), false)
  assert.equal(canOperate(view, { profile: 'SYNTHETIC', status: 'READY' }, 'other'), false)
  assert.equal(canOperate({ ...view, actions_allowed: false }, { status: 'READY' }, 'registered'), false)
  assert.equal(canOperate(view, { status: 'RUNNING' }, 'registered'), false)
  assert.equal(canOperate({ ...view, operation_permissions: undefined }, { profile: 'SYNTHETIC' }, 'registered'), false)
  assert.equal(canCreateBinding(view, 'trusted'), true)
  assert.equal(canCreateBinding(view, 'other'), false)
  assert.equal(canCreateBinding({ ...view, actions_allowed: false }, 'trusted'), false)
})

test('exhausted research attempts have an explicit Chinese status', () => {
  assert.equal(lifecycleState('ATTEMPT_BUDGET_EXHAUSTED'), '研究尝试次数已用完')
})
