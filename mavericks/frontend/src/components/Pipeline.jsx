import { IN_PROGRESS, statusClass } from '../lib/status.js'

/** The end-to-end flow, Upload -> ... -> Report, as a clickable progress rail. */
export default function Pipeline({ stages, onSelect }) {
  const done = stages.filter((stage) => ['PASS', 'WARNING', 'NOT NEEDED'].includes(stage.status)).length
  return (
    <nav className="pipeline" aria-label="Migration flow">
      <div className="pipeline__progress" style={{ '--done': `${(done / stages.length) * 100}%` }} />
      <ol>
        {stages.map((stage, index) => (
          <li key={stage.key} className={`pipeline__stage ${statusClass(stage.status)}`}>
            <button type="button" onClick={() => onSelect(stage.page)} title={`${stage.label}: ${stage.status}`}>
              <span className="pipeline__dot">
                {stage.status === IN_PROGRESS ? <span className="spinner" /> : index + 1}
              </span>
              <span className="pipeline__label">{stage.label}</span>
              <span className="pipeline__status">{stage.detail ?? stage.status}</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  )
}
