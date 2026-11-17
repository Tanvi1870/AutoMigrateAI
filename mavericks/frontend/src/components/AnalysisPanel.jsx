import Panel from './Panel.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import EmptyState from './EmptyState.jsx'
import StatusBadge from './StatusBadge.jsx'

export default function AnalysisPanel({ state, actions, health, busy, onNavigate }) {
  const { migration, plan } = state
  if (!migration) {
    return (
      <EmptyState icon="◎" title="Nothing to analyze yet">
        Upload an ICAP asset first.
      </EmptyState>
    )
  }
  const ai = plan?.aiAnalysis
  const analyzing = state.busy === 'analyze'
  const mockLlm = health.llmProvider === 'mock'

  return (
    <div className="stack">
      <Panel
        title={mockLlm ? 'Test Analysis' : 'AI Test Analysis'}
        subtitle={
          mockLlm
            ? `Deterministic facts plus a rule-based summary of ${migration.testCaseId} (no AI model is configured).`
            : `Summarises the intent of ${migration.testCaseId} before any code is written (advisory, not used for code).`
        }
        aside={
          plan ? (
            <StatusBadge status={plan.status} />
          ) : (
            <button type="button" className="btn btn--primary" disabled={busy} onClick={() => actions.analyze().catch(() => {})}>
              {analyzing ? 'Analyzing…' : 'Analyze'}
            </button>
          )
        }
      >
        <ErrorBanner error={state.errors.analyze} />
        {analyzing && (
          <div className="shimmer">
            {mockLlm
              ? 'Analysing steps, objects and test data (mock LLM, rule-based)…'
              : 'AI agent is reading steps, objects and test data…'}
          </div>
        )}
        {plan && (
          <>
            <div className="ai-card">
              <div className="ai-card__head">
                <span className="ai-card__spark">✦</span> {mockLlm ? 'Summary' : 'AI summary'}
                {health.state === 'online' && (
                  <span className="chip chip--soft">
                    {mockLlm ? 'Mock LLM · rule-based, no AI model' : `LLM · ${health.llmProvider}`}
                  </span>
                )}
              </div>
              <p className="ai-card__summary">{ai?.summary ?? plan.summary}</p>
              {ai?.intent && (
                <p className="ai-card__intent">
                  <strong>Intent:</strong> {ai.intent}
                </p>
              )}
            </div>

            <div className="split">
              <div>
                <h3 className="section-title">Risks</h3>
                <NoteList items={ai?.risks} empty="No risks identified" tone="warning" />
              </div>
              <div>
                <h3 className="section-title">Recommendations</h3>
                <NoteList items={ai?.recommendations} empty="No recommendations" tone="info" />
              </div>
            </div>

            <div className="kv-grid">
              <div className="kv">
                <span className="kv__label">Application Type</span>
                <span className="kv__value">{plan.applicationType}</span>
              </div>
              <div className="kv">
                <span className="kv__label">Test Steps</span>
                <span className="kv__value kv__value--big">{plan.stepsDetected}</span>
              </div>
              <div className="kv">
                <span className="kv__label">Distinct Keywords</span>
                <span className="kv__value kv__value--big">{plan.keywordsDetected}</span>
              </div>
              <div className="kv">
                <span className="kv__label">Reusable Blocks</span>
                <span className="kv__value kv__value--big">{plan.reusableBlocks}</span>
              </div>
            </div>

            {(plan.blockingReasons.length > 0 || plan.unsupportedItems.length > 0) && (
              <>
                <h3 className="section-title">Blocking issues</h3>
                <NoteList
                  items={[
                    ...plan.blockingReasons,
                    ...plan.unsupportedItems.map((item) => `${item.kind} ${item.name}: ${item.reason}`),
                  ]}
                  tone="fail"
                />
              </>
            )}
            <div className="actions">
              <button type="button" className="btn" onClick={() => onNavigate('plan')}>
                View migration plan →
              </button>
            </div>
          </>
        )}
      </Panel>
    </div>
  )
}

export function NoteList({ items, empty, tone }) {
  if (!items?.length) return <p className="muted">{empty}</p>
  return (
    <ul className={`notes notes--${tone}`}>
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  )
}
