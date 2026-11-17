/** effortReduction = ((manualEstimate - actualMigrationTime) / manualEstimate) * 100, in seconds; never clamped. */
export function effortReduction(manualMinutes, actualSeconds) {
  const manualSeconds = Number(manualMinutes) * 60
  if (!(manualSeconds > 0) || !(actualSeconds > 0)) return null
  return ((manualSeconds - actualSeconds) / manualSeconds) * 100
}
