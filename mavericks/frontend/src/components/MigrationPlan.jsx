import Panel from './Panel.jsx'
import ErrorBanner from './ErrorBanner.jsx'
import EmptyState from './EmptyState.jsx'
import StatusBadge from './StatusBadge.jsx'
import { NoteList } from './AnalysisPanel.jsx'

const FRAMEWORKS = [
  { key: 'playwright', name: 'Python Playwright', blurb: 'pytest-playwright · auto-waiting locators · expect() assertions' },
  { key: 'selenium', name: 'Python Selenium', blurb: 'Selenium 4 WebDriver · explicit waits · page objects' },
]

export default function MigrationPlan({ state, actions, busy, onNavigate }) {
  const { migration, plan, target } = state
  if (!migration) {
    return (
      <EmptyState icon="☰" title="No migration plan yet">
        Upload and analyze an ICAP asset first.
      </EmptyState>
    )
  }
  if (!plan) {
    return (
      <Panel title="Migration Plan">
        <ErrorBanner error={state.errors.analyze} />
        <EmptyState icon="☰" title={state.busy === 'analyze' ? 'Building the plan…' : 'Not analyzed yet'}>
          <button type="button" className="btn btn--primary" disabled={busy} onClick={() => actions.analyze().catch(() => {})}>
            Analyze now
          </button>
        </EmptyState>
      </Panel>
    )
  }

  const ready = plan.status === 'READY'
  const rows = [
    ['Test Steps', plan.stepsDetected],
    ['Keywords Detected', plan.keywordsDetected],
    ['Keywords Mapped', plan.keywordsMapped, plan.keywordsMapped < plan.keywordsDetected],
    ['Locators Resolved', plan.locatorsResolved, plan.missingLocators.length > 0],
    ['Test Data Parameters', plan.dataParameters, plan.unresolvedVariables.length > 0],
    ['Reusable Blocks', plan.reusableBlocks],
  ]

  return (
    <div className="stack">
      <Panel
        title="Migration Plan"
        subtitle={`${plan.applicationType} · Target: Python ${target === 'playwright' ? 'Playwright' : 'Selenium'}`}
        aside={<StatusBadge status={plan.status} size="lg" />}
      >
        <div className="metric-grid">
          {rows.map(([label, value, flagged]) => (
            <div key={label} className={`metric ${flagged ? 'metric--warn' : ''}`}>
              <span className="metric__value">{value}</span>
              <span className="metric__label">{label}</span>
            </div>
          ))}
        </div>
        {(plan.warnings.length > 0 || plan.errors.length > 0) && (
          <div className="split">
            {plan.errors.length > 0 && <NoteList items={plan.errors} tone="fail" />}
            {plan.warnings.length > 0 && <NoteList items={plan.warnings} tone="warning" />}
          </div>
        )}
        {plan.missingLocators.length > 0 && <NoteList items={plan.missingLocators.map((n) => `Missing locator: ${n}`)} tone="fail" />}
        {plan.unsupportedKeywords.length > 0 && (
          <NoteList items={plan.unsupportedKeywords.map((k) => `Unsupported keyword: ${k}`)} tone="fail" />
        )}
      </Panel>

      <Panel title="Select Target Framework" subtitle="Switching re-plans the migration for that framework.">
        <div className="framework-grid">
          {FRAMEWORKS.map((framework) => (
            <button
              key={framework.key}
              type="button"
              className={`framework ${target === framework.key ? 'is-selected' : ''}`}
              disabled={busy}
              onClick={() => actions.selectTarget(framework.key)}
            >
              <span className={`framework__logo framework__logo--${framework.key}`}>{framework.key === 'playwright' ? 'PW' : 'Se'}</span>
              <span className="framework__name">{framework.name}</span>
              <span className="framework__blurb">{framework.blurb}</span>
              {target === framework.key && <span className="framework__check">✓ Selected</span>}
            </button>
          ))}
        </div>
        <ErrorBanner error={state.errors.generate} />
        <div className="actions">
          <button type="button" className="btn" onClick={() => onNavigate('keywords')}>
            Review keyword mapping
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={!ready || busy}
            title={ready ? undefined : 'The plan is blocked; resolve the issues above first'}
            onClick={() =>
              actions
                .generate()
                .then(() => onNavigate('code'))
                .catch(() => {})
            }
          >
            {state.busy === 'generate' ? 'Generating…' : `Generate ${target === 'playwright' ? 'Playwright' : 'Selenium'} project`}
          </button>
        </div>
      </Panel>
    </div>
  )
}
