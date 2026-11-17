import { useEffect, useRef, useState } from 'react'
import Panel from './Panel.jsx'
import EmptyState from './EmptyState.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import StatusBadge from './StatusBadge.jsx'
import { formatSeconds, formatTime } from '../lib/status.js'
import { effortReduction } from '../lib/metrics.js'

const STEP_LABELS = {
  upload: 'Upload & parse',
  analyze: 'Analysis',
  generate: 'Code generation',
  validate: 'Validation',
  execute: 'Execution',
  repair: 'AI repair',
  qtest: 'qTest sync',
}
const ESTIMATE_KEY = 'automigrate.manualEstimateMinutes'

export default function Metrics({ state, actions, busy }) {
  const { report, migration, timings } = state
  const [estimate, setEstimate] = useState(() => localStorage.getItem(ESTIMATE_KEY) ?? '')
  const requested = useRef(null)

  // Metrics come from the backend report, so fetch it once for every new state of the migration.
  const key =
    migration &&
    [
      migration.migrationId,
      state.target,
      state.project?.generatedAt,
      state.validation?.status,
      state.executions.length,
      state.repairs.length,
      Boolean(state.qtest),
    ].join('|')
  useEffect(() => {
    if (key && !report && !busy && requested.current !== key) {
      requested.current = key
      actions.loadReport().catch(() => {})
    }
  }, [key, report, busy, actions])

  if (!migration) {
    return (
      <EmptyState icon="▤" title="No metrics yet">
        Metrics are measured from a real migration run — upload an asset to start.
      </EmptyState>
    )
  }

  const measured = Object.entries(STEP_LABELS)
    .filter(([step]) => timings[step] != null)
    .map(([step, label]) => ({ step, label, seconds: timings[step] }))
  const actualSeconds = measured.reduce((sum, item) => sum + item.seconds, 0)
  const longest = Math.max(...measured.map((item) => item.seconds), 0.001)
  const manualMinutes = Number(estimate)
  const manualSeconds = manualMinutes > 0 ? manualMinutes * 60 : null
  const reduction = effortReduction(manualMinutes, actualSeconds)

  function updateEstimate(value) {
    setEstimate(value)
    localStorage.setItem(ESTIMATE_KEY, value)
  }

  function download() {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${report.migrationId}-${report.targetFramework ?? 'report'}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const m = report?.metrics
  const finalRun = report?.executions.at(-1) ?? state.executions.at(-1)
  const loading = state.busy === 'report' && !report

  return (
    <div className="stack">
      <Panel
        title="Migration Metrics"
        subtitle={`Calculated from ${migration.migrationId} (${state.target}) — no fixed or assumed percentages.`}
        aside={loading && <StatusBadge status="IN PROGRESS" label="Calculating" />}
      >
        <div className="metric-grid metric-grid--6">
          <Kpi label="Total Steps" value={m?.totalSteps ?? migration.stepsDetected} />
          <Kpi label="Keywords Detected" value={m?.keywordsDetected ?? '—'} />
          <Kpi label="Keywords Mapped" value={m?.keywordsMapped ?? '—'} hint={m?.keywordsMapped == null ? 'after analysis' : undefined} />
          <Kpi label="Locators Resolved" value={m?.locatorsResolved ?? '—'} />
          <Kpi label="Locators Unresolved" value={m?.locatorsUnresolved ?? '—'} warn={m?.locatorsUnresolved > 0} />
          <Kpi
            label="Code Generated"
            value={m && m.generatedFiles > 0 ? `${m.generatedLines} lines` : '—'}
            hint={m && m.generatedFiles > 0 ? `${m.generatedFiles} files` : 'not generated yet'}
          />
          <StatusKpi label="Validation Status" status={m?.validationStatus} />
          <StatusKpi label="Execution Status" status={m?.executionStatus} hint={m?.executionCount ? `${m.executionCount} run(s)` : undefined} />
          <Kpi
            label="Repair Count"
            value={m?.repairCount ?? '—'}
            hint={m?.repairCount ? `${m.passedAfterRepairCount} passed re-run` : undefined}
          />
        </div>

        <div className="split">
          <div>
            <h3 className="section-title">Actual migration time (measured)</h3>
            {measured.length === 0 ? (
              <p className="muted">Run the pipeline in this browser session to measure it.</p>
            ) : (
              <div className="bars">
                {measured.map((item) => (
                  <div key={item.step} className="bar">
                    <span className="bar__label">{item.label}</span>
                    <span className="bar__track">
                      <span className="bar__fill" style={{ width: `${Math.max(2, (item.seconds / longest) * 100)}%` }} />
                    </span>
                    <span className="bar__value">{formatSeconds(item.seconds)}</span>
                  </div>
                ))}
                <div className="bar bar--total">
                  <span className="bar__label">Actual migration time</span>
                  <span className="bar__track" />
                  <span className="bar__value">{formatSeconds(actualSeconds)}</span>
                </div>
              </div>
            )}
            {m?.aiMigrationSeconds != null && (
              <p className="muted">Backend AI processing alone (analyze + generate): {formatSeconds(m.aiMigrationSeconds)}</p>
            )}
          </div>

          <div className={`saving ${reduction != null && reduction < 0 ? 'saving--negative' : ''}`}>
            <label className="saving__input">
              <span>Manual estimated effort for this test (minutes)</span>
              <input
                type="number"
                min="1"
                step="1"
                placeholder="Enter your team's estimate"
                value={estimate}
                onChange={(event) => updateEstimate(event.target.value)}
              />
            </label>
            <div className="saving__rows">
              <div>
                <span>Manual estimated effort</span>
                <strong>{manualSeconds ? `${manualMinutes} min` : '—'}</strong>
              </div>
              <div>
                <span>Actual migration time</span>
                <strong>{actualSeconds > 0 ? formatSeconds(actualSeconds) : '—'}</strong>
              </div>
            </div>
            <div className="saving__result">
              <span className="saving__value">{reduction == null ? '—' : `${reduction.toFixed(1)}%`}</span>
              <span className="saving__label">Observed Effort Reduction</span>
              <code className="saving__formula">
                {reduction == null
                  ? '((manualEstimate − actualMigrationTime) / manualEstimate) × 100'
                  : `((${manualSeconds} s − ${actualSeconds.toFixed(1)} s) / ${manualSeconds} s) × 100`}
              </code>
              {reduction == null && (
                <span className="saving__hint">
                  {manualSeconds ? 'Run the pipeline to measure the actual time.' : 'Enter a manual estimate to calculate it.'}
                </span>
              )}
            </div>
          </div>
        </div>
      </Panel>

      <Panel
        title="Migration Report"
        subtitle={report ? `Generated ${formatTime(report.generatedAt)}` : 'Consolidated plan, validation, runs, repairs and qTest links.'}
        aside={
          <div className="row">
            <button type="button" className="btn btn--ghost" disabled={busy} onClick={() => actions.loadReport().catch(() => {})}>
              {state.busy === 'report' ? 'Loading…' : report ? 'Refresh' : 'Build report'}
            </button>
            <button type="button" className="btn btn--primary" disabled={!report} onClick={download}>
              ⤓ Download JSON
            </button>
          </div>
        }
      >
        <ErrorBanner error={state.errors.report} />
        {report ? (
          <div className="report">
            <ReportRow label="Migration" value={`${report.migrationId} · ${report.testCaseId} · ${report.targetFramework ?? '—'}`} />
            {report.demoFault && <ReportRow label="DEMO MODE" value={report.demoFault.description} status="WARNING" />}
            <ReportRow label="Plan" status={report.plan?.status ?? 'PENDING'} />
            <ReportRow
              label="Locators"
              value={`${report.locatorResolutions.filter((l) => l.status === 'RESOLVED').length}/${report.locatorResolutions.length} resolved`}
              status={report.unresolvedLocators.length ? 'WARNING' : 'PASS'}
            />
            <ReportRow label="Validation" value={report.validation?.summary} status={report.validation?.status ?? 'PENDING'} />
            <ReportRow
              label="Execution"
              value={report.executions.map((e) => `#${e.attempt} ${e.status}`).join(' → ') || 'not run'}
              status={finalRun?.status ?? 'PENDING'}
            />
            <ReportRow
              label="AI Repairs"
              value={report.repairs.map((r) => `${r.status} (${r.outcome})`).join(', ') || 'none needed'}
              status={report.repairs.at(-1)?.status ?? (report.executions.length ? 'SKIPPED' : undefined)}
            />
            <ReportRow
              label="qTest"
              value={report.qtestLinks.map((l) => `${l.sourceTestCaseId} ⇄ ${l.qTestCaseId}`).join(', ') || 'not linked'}
              status={report.qtestLinks.length ? report.qtestLinks.at(-1).executionStatus : undefined}
            />
            <ReportRow label="Traced steps" value={`${report.traceability.length}`} />
          </div>
        ) : (
          <p className="muted">Build the report once the flow is complete — or at any point to snapshot progress.</p>
        )}
      </Panel>
    </div>
  )
}

function Kpi({ label, value, hint, warn }) {
  return (
    <div className={`metric ${warn ? 'metric--warn' : ''}`}>
      <span className="metric__value">{value}</span>
      <span className="metric__label">{label}</span>
      {hint && <span className="metric__hint">{hint}</span>}
    </div>
  )
}

function StatusKpi({ label, status, hint }) {
  return (
    <div className="metric">
      <span className="metric__status">{status ? <StatusBadge status={status} size="lg" /> : <StatusBadge status="PENDING" size="lg" />}</span>
      <span className="metric__label">{label}</span>
      {hint && <span className="metric__hint">{hint}</span>}
    </div>
  )
}

function ReportRow({ label, value, status }) {
  return (
    <div className="report__row">
      <span className="report__label">{label}</span>
      <span className="report__value">{value ?? ''}</span>
      <span>{status && <StatusBadge status={status} size="sm" />}</span>
    </div>
  )
}
