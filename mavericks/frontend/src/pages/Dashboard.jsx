import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import * as api from '../api/migrationApi.js'
import Pipeline from '../components/Pipeline.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import UploadPanel from '../components/UploadPanel.jsx'
import AnalysisPanel from '../components/AnalysisPanel.jsx'
import MigrationPlan from '../components/MigrationPlan.jsx'
import KeywordMapping from '../components/KeywordMapping.jsx'
import GeneratedCode from '../components/GeneratedCode.jsx'
import ValidationPanel from '../components/ValidationPanel.jsx'
import ExecutionResult from '../components/ExecutionResult.jsx'
import RepairPanel from '../components/RepairPanel.jsx'
import Traceability from '../components/Traceability.jsx'
import Metrics from '../components/Metrics.jsx'
import ApiKeyPanel from '../components/ApiKeyPanel.jsx'
import { IN_PROGRESS } from '../lib/status.js'
import { INITIAL, buildStages, reducer } from '../lib/pipeline.js'

const PAGES = [
  { key: 'upload', label: 'Upload', icon: '⇪', component: UploadPanel },
  { key: 'analysis', label: 'Migration Analysis', icon: '◎', component: AnalysisPanel },
  { key: 'plan', label: 'Migration Plan', icon: '☰', component: MigrationPlan },
  { key: 'keywords', label: 'Keyword Mapping', icon: '⇄', component: KeywordMapping },
  { key: 'code', label: 'Generated Code', icon: '</>', component: GeneratedCode },
  { key: 'validation', label: 'Validation', icon: '✓', component: ValidationPanel },
  { key: 'execution', label: 'Execution', icon: '▶', component: ExecutionResult },
  { key: 'repair', label: 'AI Repair', icon: '✦', component: RepairPanel },
  { key: 'qtest', label: 'qTest Traceability', icon: '⛓', component: Traceability },
  { key: 'metrics', label: 'Migration Metrics', icon: '▤', component: Metrics },
]

const PAGE_STAGE = {
  upload: 'upload',
  analysis: 'analyze',
  plan: 'plan',
  keywords: 'plan',
  code: 'generate',
  validation: 'validate',
  execution: 'execute',
  repair: 'repair',
  qtest: 'qtest',
  metrics: 'report',
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function DemoBanner({ state }) {
  const fault = state.project?.demoMode
  if (fault) {
    return (
      <div className="demo-banner is-active" role="status">
        <span className="demo-banner__tag">DEMO MODE</span>
        <span>
          Simulated locator failure on step {fault.stepSequence} ({fault.keyword} <code>{fault.objectName}</code>):
          generated with <code>id={fault.injectedLocator.value}</code> instead of{' '}
          <code>
            {fault.originalLocator.strategy}={fault.originalLocator.value}
          </code>
          . The failure and the AI repair that follow are real.
        </span>
      </div>
    )
  }
  if (state.simulateLocatorFailure) {
    return (
      <div className="demo-banner" role="status">
        <span className="demo-banner__tag">DEMO MODE</span>
        <span>Armed — the next generation will deliberately break one known locator. Turn it off for a normal run.</span>
      </div>
    )
  }
  return null
}

export default function Dashboard() {
  const [health, setHealth] = useState({ state: 'checking' })
  const [state, dispatch] = useReducer(reducer, INITIAL)
  const [page, setPage] = useState('upload')
  const [autopilot, setAutopilot] = useState(false)
  const [hasApiKey, setHasApiKey] = useState(() => Boolean(api.getApiKey()))
  const stateRef = useRef(state)
  stateRef.current = state

  const saveApiKey = (key) => {
    api.setApiKey(key)
    setHasApiKey(Boolean(key))
  }

  useEffect(() => {
    const check = () =>
      api
        .getHealth()
        .then((body) => setHealth({ state: 'online', ...body }))
        .catch(() => setHealth({ state: 'offline' }))
    check()
    const timer = setInterval(check, 15_000)
    return () => clearInterval(timer)
  }, [])

  // Runs one backend step, recording its wall-clock time and any error under the step key.
  const run = useCallback(async (step, work) => {
    dispatch({ type: 'busy', step })
    const started = performance.now()
    try {
      return await work()
    } catch (error) {
      // A rejected or missing key: forget it so the sidebar asks for it again.
      if (error.code === 'UNAUTHORIZED') {
        api.setApiKey(null)
        setHasApiKey(false)
      }
      // The backend store is in-memory, so a restart forgets migrations the page still shows.
      const hint =
        error.code === 'MIGRATION_NOT_FOUND' ? [{ field: null, message: 'The backend was probably restarted — upload the asset again.' }] : []
      dispatch({
        type: 'error',
        step,
        error: { code: error.code ?? 'ERROR', message: error.message, details: [...(error.details ?? []), ...hint] },
      })
      throw error
    } finally {
      dispatch({ type: 'idle', step, seconds: (performance.now() - started) / 1000 })
    }
  }, [])

  const actions = {
    upload: (file) =>
      run('upload', async () => {
        const migration = await api.uploadAsset(file)
        dispatch({ type: 'uploaded', migration })
        return migration
      }),
    analyze: (target = stateRef.current.target) =>
      run('analyze', async () => {
        const id = stateRef.current.migration.migrationId
        const [plan, keywords] = await Promise.all([api.analyzeMigration(id, target), api.getKeywordMappings(id, target)])
        dispatch({ type: 'analyzed', plan, keywords })
        return plan
      }),
    selectTarget: async (target) => {
      if (target === stateRef.current.target) return
      dispatch({ type: 'target', target })
      if (stateRef.current.migration) await actions.analyze(target).catch(() => {})
    },
    generate: () =>
      run('generate', async () => {
        const { migration, target, simulateLocatorFailure } = stateRef.current
        const project = await api.generateCode(migration.migrationId, target, simulateLocatorFailure)
        dispatch({ type: 'generated', project })
        return project
      }),
    validate: () =>
      run('validate', async () => {
        const { migration, target } = stateRef.current
        const validation = await api.validateCode(migration.migrationId, target)
        dispatch({ type: 'validated', validation })
        return validation
      }),
    execute: () =>
      run('execute', async () => {
        const { migration, target } = stateRef.current
        const execution = await api.executeTests(migration.migrationId, target)
        dispatch({ type: 'executed', execution })
        return execution
      }),
    repair: (apply) =>
      run('repair', async () => {
        const { migration, target } = stateRef.current
        const repair = await api.repairFailure(migration.migrationId, target, apply)
        dispatch({ type: 'repaired', repair })
        return repair
      }),
    syncQTest: () =>
      run('qtest', async () => {
        const id = stateRef.current.migration.migrationId
        const links = await api.syncQTest(id)
        const traceability = await api.getTraceability(id)
        dispatch({ type: 'synced', qtest: { links, traceability } })
        return links
      }),
    loadReport: () =>
      run('report', async () => {
        const { migration, target } = stateRef.current
        const report = await api.getReport(migration.migrationId, target)
        dispatch({ type: 'report', report })
        return report
      }),
  }

  // Autopilot walks the remaining flow and follows along page by page; it stops at the first blocking result.
  async function runFullMigration() {
    setAutopilot(true)
    try {
      setPage('analysis')
      const plan = stateRef.current.plan ?? (await actions.analyze())
      setPage('plan')
      if (plan.status !== 'READY') return
      setPage('code')
      await actions.generate()
      setPage('validation')
      const validation = await actions.validate()
      if (validation.status === 'FAIL') return
      setPage('execution')
      const execution = await actions.execute()
      if (execution.status !== 'PASS') {
        // Hold on the failure and on the repair so the audience can follow both.
        await pause(3000)
        setPage('repair')
        await actions.repair(true)
        await pause(6000)
      }
      setPage('qtest')
      await actions.syncQTest()
      setPage('metrics')
      await actions.loadReport()
    } catch {
      // The failing step already shows its error on its own page.
    } finally {
      setAutopilot(false)
    }
  }

  const stages = buildStages(state)
  const stageByKey = Object.fromEntries(stages.map((stage) => [stage.key, stage]))
  const current = PAGES.find((item) => item.key === page)
  const Page = current.component
  const busy = state.busy !== null || autopilot

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand__logo">AM</div>
          <div>
            <div className="brand__name">AutoMigrate AI</div>
            <div className="brand__tag">ICAP → Python automation</div>
          </div>
        </div>
        <nav className="sidebar__nav" aria-label="Sections">
          {PAGES.map((item, index) => {
            const stage = stageByKey[PAGE_STAGE[item.key]]
            return (
              <button
                key={item.key}
                type="button"
                className={`nav-item ${item.key === page ? 'is-active' : ''}`}
                onClick={() => setPage(item.key)}
              >
                <span className="nav-item__icon">{item.icon}</span>
                <span className="nav-item__label">
                  <small>{String(index + 1).padStart(2, '0')}</small> {item.label}
                </span>
                <span className={`nav-item__dot is-${stage.status.toLowerCase().replace(' ', '-')}`} />
              </button>
            )
          })}
        </nav>
        <div className="sidebar__foot">
          <div className={`health is-${health.state}`}>
            <span className="health__dot" /> Backend {health.state}
          </div>
          {health.state === 'online' && (
            <div className="sidebar__meta">
              <span>LLM · {health.llmProvider === 'mock' ? 'mock (rule-based)' : health.llmProvider}</span>
              <span>qTest · {health.qtestMode}</span>
              <span>v{health.version}</span>
            </div>
          )}
          {health.state === 'online' && health.authRequired && (
            <ApiKeyPanel key={String(hasApiKey)} hasKey={hasApiKey} onSave={saveApiKey} />
          )}
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar__title">
            <h1>{current.label}</h1>
            {state.migration ? (
              <div className="topbar__context">
                <span className="chip chip--id">{state.migration.migrationId}</span>
                <span className="chip">{state.migration.testCaseId}</span>
                <span className="chip">{state.migration.name}</span>
                <span className="chip chip--target">{state.target === 'playwright' ? 'Playwright' : 'Selenium'}</span>
              </div>
            ) : (
              <div className="topbar__context muted">No migration loaded — upload an ICAP asset to begin</div>
            )}
          </div>
          <div className="topbar__actions">
            <label
              className={`demo-switch ${state.simulateLocatorFailure ? 'is-on' : ''}`}
              title="DEMO MODE: the next generation deliberately breaks one known locator so the AI repair loop can be shown. Normal runs are never affected."
            >
              <input
                type="checkbox"
                checked={state.simulateLocatorFailure}
                disabled={busy}
                onChange={(event) => dispatch({ type: 'demo', enabled: event.target.checked })}
              />
              <span className="demo-switch__track" aria-hidden="true" />
              <span className="demo-switch__text">
                <strong>DEMO MODE</strong>
                <small>Simulate Locator Failure</small>
              </span>
            </label>
            {autopilot && <StatusBadge status={IN_PROGRESS} label="Autopilot running" />}
            <button
              type="button"
              className="btn btn--primary btn--glow"
              disabled={!state.migration || busy}
              onClick={runFullMigration}
            >
              ⚡ Run full migration
            </button>
          </div>
        </header>

        <Pipeline stages={stages} onSelect={setPage} />

        <DemoBanner state={state} />

        <main className="content" key={page}>
          <Page state={state} actions={actions} health={health} busy={busy} onNavigate={setPage} />
        </main>
      </div>
    </div>
  )
}
