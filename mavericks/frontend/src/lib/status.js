// The dashboard speaks five display states; every backend status maps onto one of them.
export const PASS = 'PASS'
export const WARNING = 'WARNING'
export const FAIL = 'FAIL'
export const IN_PROGRESS = 'IN PROGRESS'
export const PENDING = 'PENDING'
export const NOT_NEEDED = 'NOT NEEDED'

const MAP = {
  PASS,
  READY: PASS,
  PASSED_AFTER_REPAIR: PASS,
  RESOLVED: PASS,
  DONE: PASS,
  WARNING,
  PROPOSED: WARNING,
  APPLIED: WARNING,
  FAIL,
  FAILED: FAIL,
  ERROR: FAIL,
  TIMEOUT: FAIL,
  BLOCKED: FAIL,
  REJECTED: FAIL,
  UNSUPPORTED: FAIL,
  INVALID: FAIL,
  SKIPPED: NOT_NEEDED,
  PENDING,
}

export const toStatus = (value) => MAP[value] ?? PENDING

export const statusClass = (status) => `is-${status.toLowerCase().replace(' ', '-')}`

export function formatSeconds(seconds) {
  if (seconds == null) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`
}

export const formatTime = (iso) => (iso ? new Date(iso).toLocaleString() : '—')

export const fileName = (path) => (path ? path.split('/').pop() : '')
