import { IN_PROGRESS, statusClass, toStatus } from '../lib/status.js'

const ICONS = { PASS: '✓', WARNING: '!', FAIL: '✕', 'IN PROGRESS': '', PENDING: '•', 'NOT NEEDED': '–' }

// `status` may be a raw backend value (READY, PASSED_AFTER_REPAIR, ...) or a display status.
export default function StatusBadge({ status, label, size = 'md' }) {
  const display = status in ICONS ? status : toStatus(status)
  return (
    <span className={`badge badge--${size} ${statusClass(display)}`}>
      {display === IN_PROGRESS ? <span className="spinner" aria-hidden="true" /> : <span aria-hidden="true">{ICONS[display]}</span>}
      {label ?? display}
    </span>
  )
}
