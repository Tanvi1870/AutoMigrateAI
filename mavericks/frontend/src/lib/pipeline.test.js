import { describe, expect, it } from 'vitest'
import { INITIAL, buildStages, reducer } from './pipeline.js'
import { effortReduction } from './metrics.js'
import { FAIL, IN_PROGRESS, NOT_NEEDED, PASS, PENDING, WARNING, fileName, formatSeconds, statusClass, toStatus } from './status.js'

const plan = { status: 'READY', warnings: [] }
const passRun = { attempt: 1, status: 'PASS' }
const failRun = { attempt: 1, status: 'FAIL' }

function run(...actions) {
  return actions.reduce(reducer, INITIAL)
}

const statusOf = (state, key) => buildStages(state).find((stage) => stage.key === key).status

describe('status mapping', () => {
  it.each([
    ['PASS', PASS],
    ['READY', PASS],
    ['PASSED_AFTER_REPAIR', PASS],
    ['WARNING', WARNING],
    ['PROPOSED', WARNING],
    ['FAIL', FAIL],
    ['ERROR', FAIL],
    ['TIMEOUT', FAIL],
    ['BLOCKED', FAIL],
    ['REJECTED', FAIL],
    ['FAILED', FAIL],
    ['SKIPPED', NOT_NEEDED],
    [undefined, PENDING],
    ['SOMETHING_NEW', PENDING],
  ])('%s -> %s', (raw, expected) => {
    expect(toStatus(raw)).toBe(expected)
  })

  it('formats helpers', () => {
    expect(statusClass(IN_PROGRESS)).toBe('is-in-progress')
    expect(formatSeconds(null)).toBe('—')
    expect(formatSeconds(0.25)).toBe('250 ms')
    expect(formatSeconds(4.25)).toBe('4.3 s')
    expect(formatSeconds(125)).toBe('2 min 5 s')
    expect(fileName('playwright/MIG-001/tests/login_test.py')).toBe('login_test.py')
  })
})

describe('observed effort reduction', () => {
  it('uses ((manual - actual) / manual) * 100 in seconds', () => {
    expect(effortReduction(45, 5.2)).toBeCloseTo(((2700 - 5.2) / 2700) * 100, 10)
    expect(effortReduction('45', 300)).toBeCloseTo(88.888, 2)
  })

  it('is not clamped when the migration took longer than the estimate', () => {
    expect(effortReduction(0.05, 5.2)).toBeCloseTo(-73.33, 2)
  })

  it.each([
    ['', 10],
    [0, 10],
    [-5, 10],
    ['abc', 10],
    [45, 0],
    [45, null],
  ])('needs a positive estimate and a measured time (%s, %s)', (manual, actual) => {
    expect(effortReduction(manual, actual)).toBeNull()
  })
})

describe('pipeline reducer', () => {
  it('a new upload resets everything but keeps the target and DEMO MODE switch', () => {
    const before = run(
      { type: 'demo', enabled: true },
      { type: 'target', target: 'selenium' },
      { type: 'uploaded', migration: { migrationId: 'MIG-001' } },
      { type: 'analyzed', plan, keywords: [] },
      { type: 'error', step: 'generate', error: { code: 'X' } },
    )
    const after = reducer(before, { type: 'uploaded', migration: { migrationId: 'MIG-002' } })
    expect(after).toMatchObject({ target: 'selenium', simulateLocatorFailure: true, plan: null, errors: {} })
    expect(after.migration.migrationId).toBe('MIG-002')
  })

  it('each step invalidates everything after it', () => {
    const full = run(
      { type: 'uploaded', migration: { migrationId: 'MIG-001' } },
      { type: 'analyzed', plan, keywords: [] },
      { type: 'generated', project: { artifacts: [] } },
      { type: 'validated', validation: { status: 'PASS' } },
      { type: 'executed', execution: passRun },
      { type: 'synced', qtest: { links: [] } },
      { type: 'report', report: {} },
    )
    expect(reducer(full, { type: 'generated', project: {} })).toMatchObject({ validation: null, executions: [], qtest: null, report: null })
    expect(reducer(full, { type: 'executed', execution: passRun })).toMatchObject({ qtest: null, report: null })
    expect(reducer(full, { type: 'target', target: 'selenium' })).toMatchObject({ plan: null, project: null, keywords: null })
  })

  it('a verified repair appends its re-run to the executions', () => {
    const state = run(
      { type: 'uploaded', migration: {} },
      { type: 'executed', execution: failRun },
      { type: 'repaired', repair: { status: 'PASSED_AFTER_REPAIR', reExecution: { attempt: 2, status: 'PASS' } } },
    )
    expect(state.executions.map((e) => e.status)).toEqual(['FAIL', 'PASS'])
    const proposalOnly = reducer(state, { type: 'repaired', repair: { status: 'PROPOSED', reExecution: null } })
    expect(proposalOnly.executions).toHaveLength(2)
  })

  it('busy clears the previous error of that step and idle records its time', () => {
    const state = run({ type: 'error', step: 'execute', error: { code: 'X' } }, { type: 'busy', step: 'execute' })
    expect(state.errors.execute).toBeNull()
    expect(reducer(state, { type: 'idle', step: 'execute', seconds: 4.2 })).toMatchObject({ busy: null, timings: { execute: 4.2 } })
  })
})

describe('pipeline stages', () => {
  it('start pending, in flow order', () => {
    const stages = buildStages(INITIAL)
    expect(stages.map((s) => s.key)).toEqual(['upload', 'analyze', 'plan', 'framework', 'generate', 'validate', 'execute', 'repair', 'qtest', 'report'])
    expect(new Set(stages.map((s) => s.status))).toEqual(new Set([PENDING]))
  })

  it('show IN PROGRESS and FAIL for the step that is running or failed', () => {
    expect(statusOf(run({ type: 'busy', step: 'execute' }), 'execute')).toBe(IN_PROGRESS)
    expect(statusOf(run({ type: 'error', step: 'generate', error: {} }), 'generate')).toBe(FAIL)
  })

  it('map plan, validation and execution outcomes', () => {
    expect(statusOf(run({ type: 'analyzed', plan: { status: 'BLOCKED', warnings: [] } }), 'plan')).toBe(FAIL)
    expect(statusOf(run({ type: 'analyzed', plan: { status: 'READY', warnings: ['w'] } }), 'plan')).toBe(WARNING)
    expect(statusOf(run({ type: 'validated', validation: { status: 'WARNING' } }), 'validate')).toBe(WARNING)
    expect(statusOf(run({ type: 'executed', execution: { status: 'TIMEOUT' } }), 'execute')).toBe(FAIL)
  })

  it('repair is NOT NEEDED after a pass, WARNING (needed) after a failure, PASS once verified', () => {
    expect(statusOf(run({ type: 'executed', execution: passRun }), 'repair')).toBe(NOT_NEEDED)
    const failed = run({ type: 'executed', execution: failRun })
    expect(statusOf(failed, 'repair')).toBe(WARNING)
    expect(buildStages(failed).find((s) => s.key === 'repair').detail).toBe('NEEDED')
    const repaired = reducer(failed, { type: 'repaired', repair: { status: 'PASSED_AFTER_REPAIR', reExecution: { status: 'PASS' } } })
    expect([statusOf(repaired, 'repair'), statusOf(repaired, 'execute')]).toEqual([PASS, PASS])
  })

  it('shows the selected framework once planned', () => {
    const state = run({ type: 'target', target: 'selenium' }, { type: 'analyzed', plan, keywords: [] })
    expect(buildStages(state).find((s) => s.key === 'framework')).toMatchObject({ status: PASS, detail: 'Selenium' })
  })
})
