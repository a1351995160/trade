import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { canCreateBinding, canOperate, canOperateContinuous, continuousResourceRows, lifecycleState, observationDays, qualificationText, sourceText } from './lifecycle.ts'

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

test('continuous scope waits do not imply goal completion or reset unknown consumption', () => {
  assert.match(lifecycleState('WAITING_RESOURCE'), /追加资源/)
  assert.match(lifecycleState('HARD_BUDGET_UNSUPPORTED'), /硬上限/)
  assert.match(lifecycleState('WAITING_MODEL_RECONCILIATION'), /原模型请求/)
  assert.match(lifecycleState('GOAL_NOT_MET'), /尚未/)
  const rows = continuousResourceRows({ scope_budget: { used: { model_calls: 1 },
    reserved: { model_calls: 2 }, remaining: { model_calls: 0 } } })
  assert.deepEqual(rows, [{ name: '模型调用', used: 1, reserved: 2, remaining: 0 }])
  assert.deepEqual(continuousResourceRows({}), [])
})

test('continuous browser controls require the separately registered trusted scope', () => {
  const view = { bindings: {}, binding_catalog: {}, jobs: {}, background_enabled: false, actions_allowed: true,
    operation_permissions: { job_ids: [], create_binding_ids: [], continuous_ids: ['fixed'] } }
  assert.equal(canOperateContinuous(view, 'fixed'), true)
  assert.equal(canOperateContinuous(view, 'caller_chosen'), false)
  assert.equal(canOperateContinuous({ ...view, actions_allowed: false }, 'fixed'), false)
})

async function renderHostHeartbeat(view: Record<string, unknown>) {
  const require = createRequire(import.meta.url)
  const Vue = require('vue')
  const { renderToString } = require('vue/server-renderer')
  const source = readFileSync(new URL('./components/LifecycleWorkbench.vue', import.meta.url), 'utf8')
  const paragraph = source.match(/<p[^>]*aria-label="研究服务心跳"[^>]*>[\s\S]*?<\/p>/)?.[0]
  assert.ok(paragraph, '工作台必须呈现真实研究服务心跳')
  const render = Vue.compile(paragraph, { prefixIdentifiers: true })
  return renderToString(Vue.createSSRApp({ data: () => ({ view }), render }))
}

test('workbench renders the actual server heartbeat even when background is disabled', async () => {
  const heartbeat = '2026-10-10T10:15:30+00:00'
  const html = await renderHostHeartbeat({ background_enabled: false, host: { heartbeat_at: heartbeat } })
  assert.ok(html.includes(`<time datetime="${heartbeat}">${heartbeat}</time>`))
  assert.doesNotMatch(html, /暂无心跳|正在运行|后台自动运行|已启用/)
  assert.match(html, /实际研究进度请以候选、任务阶段和执行回执为准/)
})

test('workbench never substitutes enabled background for a missing heartbeat', async () => {
  for (const host of [undefined, {}, { heartbeat_at: null }, { heartbeat_at: '' }]) {
    const html = await renderHostHeartbeat({ background_enabled: true, host })
    assert.match(html, /暂无心跳/)
    assert.doesNotMatch(html, /<time|正在运行|后台自动运行|已启用/)
  }
})
