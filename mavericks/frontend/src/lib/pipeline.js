import { FAIL, IN_PROGRESS, NOT_NEEDED, PASS, PENDING, WARNING, toStatus } from './status.js'

export const INITIAL = {
  target: 'playwright',
  simulateLocatorFailure: false,
  migration: null,
  plan: null,
  keywords: null,
  project: null,
  validation: null,
  executions: [],
  repairs: [],
  qtest: null,
  report: null,
  timings: {},
  busy: null,
  errors: {},
}

// Each completed step invalidates everything after it, so the UI never shows stale downstream results.
const AFTER_PLAN = { keywords: null, project: null, validation: null, executions: [], repairs: [], qtest: null, report: null }
const AFTER_GENERATE = { validation: null, executions: [], repairs: [], qtest: null, report: null }

export function reducer(state, action) {
  switch (action.type) {
    case 'busy':
      return { ...state, busy: action.step, errors: { ...state.errors, [action.step]: null } }
    case 'idle':
      return { ...state, busy: null, timings: { ...state.timings, [action.step]: action.seconds } }
    case 'error':
      return { ...state, errors: { ...state.errors, [action.step]: action.error } }
    case 'uploaded':
      return {
        ...INITIAL,
        target: state.target,
        simulateLocatorFailure: state.simulateLocatorFailure,
        migration: action.migration,
        busy: state.busy,
      }
    case 'demo':
      return { ...state, simulateLocatorFailure: action.enabled }
    case 'target':
      return { ...state, target: action.target, plan: null, ...AFTER_PLAN }
    case 'analyzed':
      return { ...state, ...AFTER_PLAN, plan: action.plan, keywords: action.keywords }
    case 'generated':
      return { ...state, ...AFTER_GENERATE, project: action.project }
    case 'validated':
      return { ...state, validation: action.validation, executions: [], repairs: [], qtest: null, report: null }
    case 'executed':
      return { ...state, executions: [...state.executions, action.execution], qtest: null, report: null }
    case 'repaired': {
      const rerun = action.repair.reExecution
      return {
        ...state,
        repairs: [...state.repairs, action.repair],
        executions: rerun ? [...state.executions, rerun] : state.executions,
        qtest: rerun ? null : state.qtest,
        report: null,
      }
    }
    case 'synced':
      return { ...state, qtest: action.qtest, report: null }
    case 'report':
      return { ...state, report: action.report }
    default:
      return state
  }
}

function stepStatus(state, step, done, outcome) {
  if (state.busy === step) return IN_PROGRESS
  if (state.errors[step]) return FAIL
  return done ? outcome ?? PASS : PENDING
}

export function buildStages(state) {
  const { plan, validation, executions, repairs, qtest, report } = state
  const lastRun = executions.at(-1)
  const lastRepair = repairs.at(-1)
  let repair = stepStatus(state, 'repair', Boolean(lastRepair), lastRepair && toStatus(lastRepair.status))
  if (repair === PENDING && lastRun?.status === 'PASS') repair = NOT_NEEDED
  if (repair === PENDING && lastRun && lastRun.status !== 'PASS') repair = WARNING
  const planOutcome = plan && (plan.status !== 'READY' ? FAIL : plan.warnings.length ? WARNING : PASS)
  return [
    { key: 'upload', page: 'upload', label: 'Upload ICAP Asset', status: stepStatus(state, 'upload', state.migration) },
    { key: 'analyze', page: 'analysis', label: 'Analyze', status: stepStatus(state, 'analyze', plan) },
    { key: 'plan', page: 'plan', label: 'Migration Plan', status: plan ? planOutcome : stepStatus(state, 'analyze', false), detail: plan?.status },
    {
      key: 'framework',
      page: 'plan',
      label: 'Target Framework',
      status: plan ? PASS : PENDING,
      detail: plan ? (state.target === 'playwright' ? 'Playwright' : 'Selenium') : undefined,
    },
    { key: 'generate', page: 'code', label: 'Generate', status: stepStatus(state, 'generate', state.project) },
    {
      key: 'validate',
      page: 'validation',
      label: 'Validate',
      status: stepStatus(state, 'validate', validation, validation && toStatus(validation.status)),
    },
    {
      key: 'execute',
      page: 'execution',
      label: 'Execute',
      status: stepStatus(state, 'execute', lastRun, lastRun && toStatus(lastRun.status)),
    },
    {
      key: 'repair',
      page: 'repair',
      label: 'AI Repair',
      status: repair,
      detail: repair === WARNING ? 'NEEDED' : undefined,
    },
    { key: 'qtest', page: 'qtest', label: 'qTest Traceability', status: stepStatus(state, 'qtest', qtest) },
    { key: 'report', page: 'metrics', label: 'Report', status: stepStatus(state, 'report', report) },
  ]
}
