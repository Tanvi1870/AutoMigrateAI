import Panel from './Panel.jsx'
import EmptyState from './EmptyState.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import StatusBadge from './StatusBadge.jsx'
import { fileName, formatTime } from '../lib/status.js'

export default function Traceability({ state, actions, busy, onNavigate }) {
  const { qtest, executions, migration } = state
  const sync = () => actions.syncQTest().catch(() => {})

  if (!qtest) {
    return (
      <Panel title="qTest Traceability">
        <ErrorBanner error={state.errors.qtest} />
        <EmptyState icon="⛓" title={state.busy === 'qtest' ? 'Publishing to qTest…' : 'Not linked to qTest yet'}>
          {executions.length > 0 ? (
            <button type="button" className="btn btn--primary" disabled={busy} onClick={sync}>
              Sync to qTest
            </button>
          ) : (
            'Execute the generated tests first, so the result can be published.'
          )}
        </EmptyState>
      </Panel>
    )
  }

  const { links, traceability } = qtest
  const stepsByCase = traceability.steps.reduce((groups, step) => {
    ;(groups[step.sourceTestCaseId] ??= []).push(step)
    return groups
  }, {})

  return (
    <div className="stack">
      <Panel
        title="qTest Links"
        subtitle={`Published to ${traceability.qtestSystem}`}
        aside={
          <div className="row">
            <button type="button" className="btn btn--ghost" disabled={busy} onClick={sync}>
              Re-sync
            </button>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => {
                onNavigate('metrics')
                actions.loadReport().catch(() => {})
              }}
            >
              View report →
            </button>
          </div>
        }
      >
        <ErrorBanner error={state.errors.qtest} />
        <div className="link-grid">
          {links.map((link) => (
            <div key={`${link.qTestCaseId}-${link.targetFramework}`} className="qlink">
              <div className="qlink__ids">
                <span className="qlink__id">{link.sourceTestCaseId}</span>
                <span className="qlink__arrow">⇄</span>
                <span className="qlink__id qlink__id--qtest">{link.qTestCaseId}</span>
                {link.mock && <span className="chip chip--soft">mock</span>}
              </div>
              <dl className="facts">
                <dt>Script</dt>
                <dd className="mono">{link.generatedScript}</dd>
                <dt>Run</dt>
                <dd className="mono">
                  {link.qTestRunId ?? '—'} · attempt {link.attempt ?? '—'}
                </dd>
                <dt>Executed</dt>
                <dd>{formatTime(link.executedAt)}</dd>
                <dt>Published</dt>
                <dd>{link.published ? 'Yes' : 'No'}</dd>
              </dl>
              <StatusBadge status={link.executionStatus} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="End-to-end Traceability" subtitle="ICAP test case ↕ canonical step ↕ generated line ↕ execution ↕ qTest">
        {Object.entries(stepsByCase).map(([caseId, steps]) => (
          <div key={caseId} className="tree">
            <div className="tree__root">
              <span className="tree__badge">ICAP</span> {caseId}
              {migration?.name && <span className="muted"> · {migration.name}</span>}
            </div>
            <ul>
              {steps.map((step) => (
                <li key={step.stepSequence} className="tree__node">
                  <div className="tree__step">
                    <span className="tree__seq">Step {step.stepSequence}</span>
                    <code className="kw">{step.keyword}</code>
                    {step.blockId && <span className="chip chip--soft">block {step.blockId}</span>}
                    <span className="muted mono">ref {step.sourceStepRef}</span>
                  </div>
                  <ul>
                    <li className="tree__leaf">
                      <span className="tree__leaf-label">code</span>
                      <button type="button" className="link mono" onClick={() => onNavigate('code')}>
                        {fileName(step.generatedFile)}:{step.generatedLine}
                      </button>
                    </li>
                    <li className="tree__leaf">
                      <span className="tree__leaf-label">result</span>
                      {step.executionStatus ? <StatusBadge status={step.executionStatus} size="sm" /> : <span className="muted">not run</span>}
                    </li>
                    <li className="tree__leaf">
                      <span className="tree__leaf-label">qTest</span>
                      <span className="mono">{step.qTestCaseId ?? '—'}</span>
                    </li>
                  </ul>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Panel>
    </div>
  )
}
