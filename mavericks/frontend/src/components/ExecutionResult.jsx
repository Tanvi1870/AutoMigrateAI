import { useState } from 'react'
import Panel from './Panel.jsx'
import EmptyState from './EmptyState.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import StatusBadge from './StatusBadge.jsx'
import { formatSeconds, formatTime, statusClass, toStatus } from '../lib/status.js'

export default function ExecutionResult({ state, actions, busy, onNavigate }) {
  const { executions, validation } = state
  const [selected, setSelected] = useState(null)
  const [tab, setTab] = useState('logs')
  const running = state.busy === 'execute' || (state.busy === 'repair' && executions.length > 0)
  const canRun = validation && validation.status !== 'FAIL'
  const execute = () => actions.execute().catch(() => {})

  if (executions.length === 0) {
    return (
      <Panel title="Execution">
        <ErrorBanner error={state.errors.execute} />
        {running ? (
          <div className="run-live">
            <span className="spinner spinner--lg" />
            <div>
              <div className="run-live__title">Running pytest against the application under test…</div>
              <div className="muted">Real browser, real assertions — this takes a few seconds.</div>
            </div>
          </div>
        ) : (
          <EmptyState icon="▶" title="Not executed yet">
            {canRun ? (
              <button type="button" className="btn btn--primary" disabled={busy} onClick={execute}>
                Run tests
              </button>
            ) : (
              'Validate the generated code first.'
            )}
          </EmptyState>
        )}
      </Panel>
    )
  }

  const run = executions.find((execution) => execution.attempt === selected) ?? executions.at(-1)
  const passed = run.status === 'PASS'
  const latest = executions.at(-1)
  const output = { logs: run.logs, stdout: run.stdout, stderr: run.stderr }

  return (
    <div className="stack">
      <Panel
        title="Execution"
        subtitle="pytest run of the generated project; every attempt is kept."
        aside={
          <div className="row">
            {latest.status !== 'PASS' && (
              <button type="button" className="btn btn--accent" onClick={() => onNavigate('repair')}>
                ✦ Repair with AI
              </button>
            )}
            <button type="button" className="btn btn--ghost" disabled={!canRun || busy} onClick={execute}>
              {running ? 'Running…' : 'Re-run'}
            </button>
          </div>
        }
      >
        <ErrorBanner error={state.errors.execute} />
        {executions.length > 1 && (
          <div className="tabs">
            {executions.map((execution) => (
              <button
                key={execution.attempt}
                type="button"
                className={`tab ${execution.attempt === run.attempt ? 'is-active' : ''}`}
                onClick={() => setSelected(execution.attempt)}
              >
                Attempt {execution.attempt} <StatusBadge status={execution.status} size="sm" />
              </button>
            ))}
          </div>
        )}

        <div className={`hero hero--big ${statusClass(toStatus(run.status))}`}>
          <div className="hero__icon">{passed ? '✓' : '✕'}</div>
          <div>
            <div className="hero__title">{passed ? 'All tests passed' : `Execution ${run.status}`}</div>
            <div className="hero__text">
              {run.failedStep ? `Failed at ICAP step ${run.failedStep}` : `${run.testsTotal ?? 0} test(s) run`} · attempt {run.attempt} ·{' '}
              {formatTime(run.startedAt)}
            </div>
          </div>
        </div>

        <div className="metric-grid metric-grid--4">
          <Metric label="Duration" value={formatSeconds(run.durationSeconds)} />
          <Metric label="Tests" value={run.testsTotal ?? '—'} />
          <Metric label="Failed" value={run.testsFailed ?? '—'} warn={run.testsFailed > 0} />
          <Metric label="Exit code" value={run.exitCode ?? '—'} warn={run.exitCode !== 0} />
        </div>

        {(run.errorType || run.errorMessage) && (
          <div className="failure">
            <div className="failure__type">{run.errorType ?? 'Error'}</div>
            <pre className="failure__message">{run.errorMessage}</pre>
          </div>
        )}

        {(run.screenshots.length > 0 || run.domSnapshots.length > 0) && (
          <div className="artifacts">
            <span className="muted">Evidence captured in {run.artifactsDir}:</span>
            {[...run.screenshots, ...run.domSnapshots].map((path) => (
              <span key={path} className="chip chip--soft mono">
                {path.split('/').pop()}
              </span>
            ))}
          </div>
        )}

        <div className="tabs tabs--terminal">
          {Object.keys(output).map((key) => (
            <button key={key} type="button" className={`tab ${tab === key ? 'is-active' : ''}`} onClick={() => setTab(key)}>
              {key}
            </button>
          ))}
        </div>
        <pre className="terminal">{output[tab] || `(no ${tab})`}</pre>
      </Panel>
    </div>
  )
}

function Metric({ label, value, warn }) {
  return (
    <div className={`metric ${warn ? 'metric--warn' : ''}`}>
      <span className="metric__value">{value}</span>
      <span className="metric__label">{label}</span>
    </div>
  )
}
