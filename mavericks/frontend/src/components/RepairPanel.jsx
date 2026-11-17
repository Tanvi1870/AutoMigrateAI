import Panel from './Panel.jsx'
import EmptyState from './EmptyState.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import StatusBadge from './StatusBadge.jsx'
import { formatTime, statusClass, toStatus } from '../lib/status.js'

// Where the repair choice came from. "mock" is the offline rule-based provider: no AI model was involved.
const SOURCE_LABELS = {
  llm: 'LLM analysis',
  mock: 'Mock LLM · rule-based, no AI model',
  heuristic: 'Heuristic analysis · no AI model',
}

export default function RepairPanel({ state, actions, busy, onNavigate, health }) {
  const { executions, repairs } = state
  const last = executions.at(-1)
  const repairing = state.busy === 'repair'
  const needsRepair = last && last.status !== 'PASS'
  const repair = (apply) => actions.repair(apply).catch(() => {})
  const mockLlm = health?.llmProvider === 'mock'

  return (
    <div className="stack">
      <Panel
        title="AI Self-Healing"
        subtitle={
          mockLlm
            ? 'Failure → analysis (mock LLM, rule-based) → minimal fix → validation → re-run. Every repair is shown.'
            : 'Failure → AI analysis → minimal fix → validation → re-run. Every repair is shown, never hidden.'
        }
        aside={
          needsRepair && (
            <div className="row">
              <button type="button" className="btn btn--ghost" disabled={busy} onClick={() => repair(false)}>
                Diagnose only
              </button>
              <button type="button" className="btn btn--accent btn--glow" disabled={busy} onClick={() => repair(true)}>
                {repairing ? 'Repairing…' : '✦ Apply AI repair & re-run'}
              </button>
            </div>
          )
        }
      >
        <ErrorBanner error={state.errors.repair} />
        {repairing && (
          <div className="run-live run-live--ai">
            <span className="spinner spinner--lg" />
            <div>
              <div className="run-live__title">
                {mockLlm ? 'Analysing the failure (mock LLM, rule-based)…' : 'AI agent is analysing the failure…'}
              </div>
              <div className="muted">Reading the exception, the failed step, its locator and the captured DOM.</div>
            </div>
          </div>
        )}
        {!last && !repairing && (
          <EmptyState icon="✦" title="Nothing to repair yet">
            Execute the generated tests first.
          </EmptyState>
        )}
        {last && !needsRepair && repairs.length === 0 && (
          <div className="hero is-pass">
            <StatusBadge status="PASS" size="lg" label="NO REPAIR NEEDED" />
            <span className="hero__text">The latest run passed. The repair agent only acts on real failures.</span>
          </div>
        )}
        {needsRepair && repairs.length === 0 && !repairing && (
          <div className="hero is-warning">
            <StatusBadge status="WARNING" size="lg" label="REPAIR NEEDED" />
            <span className="hero__text">
              Attempt {last.attempt} {last.status}
              {last.failedStep ? ` at step ${last.failedStep}` : ''}
              {last.errorType ? ` · ${last.errorType}` : ''}
            </span>
          </div>
        )}
      </Panel>

      {[...repairs].reverse().map((item) => (
        <RepairCard
          key={item.createdAt}
          repair={item}
          demo={state.project?.demoMode?.injectedLocator}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  )
}

function RepairCard({ repair, demo, onNavigate }) {
  const p = repair.proposal
  const isDemo = demo && p.oldValue === `${demo.strategy}=${demo.value}`
  return (
    <Panel
      title={`AI Repair — step ${p.stepSequence}${p.objectName ? ` (${p.objectName})` : ''}`}
      subtitle={`${repair.outcome} · ${formatTime(repair.createdAt)}`}
      aside={
        <div className="row">
          {isDemo && <span className="demo-banner__tag">DEMO MODE</span>}
          <StatusBadge status={repair.status} label={repair.status.replaceAll('_', ' ')} size="lg" />
        </div>
      }
      className="repair"
    >
      <ol className="timeline">
        {repair.timeline.map((entry) => (
          <li key={entry.stage} className={`timeline__item ${statusClass(toStatus(entry.status))}`}>
            <div className="timeline__dot" />
            <div className="timeline__title">{entry.title}</div>
            <div className="timeline__status">{entry.status}</div>
            {entry.details.map((detail, index) => (
              <div key={index} className="timeline__detail">
                {detail}
              </div>
            ))}
          </li>
        ))}
      </ol>

      <div className="before-after">
        <div className="before-after__side is-fail">
          <span className="before-after__label">Before</span>
          <code>{p.oldValue || '(none)'}</code>
        </div>
        <div className="before-after__arrow">→</div>
        <div className="before-after__side is-pass">
          <span className="before-after__label">After · {p.changeType}</span>
          <code>{p.newValue}</code>
        </div>
      </div>

      <div className="split">
        <div className="ai-card">
          <div className="ai-card__head">
            <span className="ai-card__spark">✦</span> Diagnosis
            <span className="chip chip--soft">{SOURCE_LABELS[p.analysisSource] ?? p.analysisSource}</span>
          </div>
          <p>{p.diagnosis}</p>
          <p>
            <strong>Root cause:</strong> {p.rootCause}
          </p>
          <p>
            <strong>Change:</strong> {p.proposedChange}
          </p>
          <p className="muted">{p.explanation}</p>
        </div>
        <div>
          <div className="confidence">
            <span title="A model's own estimate (capped at 95%) or a DOM-similarity score; not a guarantee.">
              {p.analysisSource === 'llm' ? 'Confidence (model estimate)' : 'Confidence (similarity score)'}
            </span>
            <div className="confidence__bar">
              <div style={{ width: `${Math.round(p.confidence * 100)}%` }} />
            </div>
            <strong>{Math.round(p.confidence * 100)}%</strong>
          </div>
          <dl className="facts">
            {p.affectedSteps.length > 0 && (
              <>
                <dt>Affected steps</dt>
                <dd>{p.affectedSteps.join(', ')}</dd>
              </>
            )}
            {(p.changedFiles.length > 0 || p.file) && (
              <>
                <dt>Changed files</dt>
                <dd className="mono">{(p.changedFiles.length ? p.changedFiles : [p.file]).join(', ')}</dd>
              </>
            )}
            {repair.validationSummary && (
              <>
                <dt>Validation</dt>
                <dd>{repair.validationSummary}</dd>
              </>
            )}
            {repair.message && (
              <>
                <dt>Result</dt>
                <dd>{repair.message}</dd>
              </>
            )}
            {repair.rolledBack && (
              <>
                <dt>Rollback</dt>
                <dd>Original code restored</dd>
              </>
            )}
          </dl>
          {repair.reExecution && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onNavigate('execution')}>
              View re-run (attempt {repair.reExecution.attempt}) →
            </button>
          )}
        </div>
      </div>

      {p.codeDiff && (
        <>
          <h3 className="section-title">Code diff</h3>
          <pre className="diff">
            {p.codeDiff.split('\n').map((line, index) => (
              <span
                key={index}
                className={
                  line.startsWith('+') && !line.startsWith('+++')
                    ? 'diff__add'
                    : line.startsWith('-') && !line.startsWith('---')
                      ? 'diff__del'
                      : line.startsWith('@@')
                        ? 'diff__hunk'
                        : undefined
                }
              >
                {line}
                {'\n'}
              </span>
            ))}
          </pre>
        </>
      )}
    </Panel>
  )
}
