import Panel from './Panel.jsx'
import EmptyState from './EmptyState.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import StatusBadge from './StatusBadge.jsx'
import { statusClass, toStatus } from '../lib/status.js'

export default function ValidationPanel({ state, actions, busy, onNavigate }) {
  const { validation, project } = state
  const validate = () => actions.validate().catch(() => {})

  if (!validation) {
    return (
      <Panel title="Static Validation">
        <ErrorBanner error={state.errors.validate} />
        <EmptyState icon="✓" title={state.busy === 'validate' ? 'Running static checks…' : 'Not validated yet'}>
          {project ? (
            <button type="button" className="btn btn--primary" disabled={busy} onClick={validate}>
              Validate generated code
            </button>
          ) : (
            'Generate the project first.'
          )}
        </EmptyState>
      </Panel>
    )
  }

  const runnable = validation.status !== 'FAIL'
  return (
    <div className="stack">
      <Panel
        title="Static Validation"
        subtitle="Checked before anything runs: syntax, imports, keywords, locators, variables, assertions, duplicates, framework and coding standards."
        aside={
          <div className="row">
            <button type="button" className="btn btn--ghost" disabled={busy} onClick={validate}>
              Re-validate
            </button>
            <button
              type="button"
              className="btn btn--primary"
              disabled={!runnable || busy}
              onClick={() => {
                onNavigate('execution')
                actions.execute().catch(() => {})
              }}
            >
              Execute →
            </button>
          </div>
        }
      >
        <div className={`hero ${statusClass(toStatus(validation.status))}`}>
          <StatusBadge status={validation.status} size="lg" />
          <span className="hero__text">{validation.summary}</span>
        </div>
        <div className="check-grid">
          {validation.checks.map((check) => (
            <div key={check.check} className={`check ${statusClass(toStatus(check.status))}`}>
              <div className="check__head">
                <span className="check__label">{check.label}</span>
                <StatusBadge status={check.status} size="sm" />
              </div>
              {check.reasons.length > 0 && (
                <ul className="check__reasons">
                  {check.reasons.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
        <ErrorBanner error={state.errors.execute} />
      </Panel>

      {validation.issues.length > 0 && (
        <Panel title="Issues">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Check</th>
                  <th>Location</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {validation.issues.map((issue, index) => (
                  <tr key={index}>
                    <td>
                      <StatusBadge status={issue.severity === 'ERROR' ? 'FAIL' : 'WARNING'} label={issue.severity} size="sm" />
                    </td>
                    <td>{issue.check}</td>
                    <td className="mono">
                      {[issue.file && (issue.line ? `${issue.file}:${issue.line}` : issue.file), issue.stepSequence && `step ${issue.stepSequence}`]
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </td>
                    <td>{issue.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  )
}
