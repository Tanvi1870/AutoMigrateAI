const BASE_URL = '/api'
const API_KEY_HEADER = 'X-API-Key'
const API_KEY_STORAGE = 'automigrate.apiKey'

// The API key lives only in this tab's sessionStorage: it is never bundled, logged or sent anywhere else.
function keyStorage() {
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

export function getApiKey() {
  return keyStorage()?.getItem(API_KEY_STORAGE) || null
}

export function setApiKey(key) {
  const storage = keyStorage()
  if (!storage) return
  if (key) storage.setItem(API_KEY_STORAGE, key)
  else storage.removeItem(API_KEY_STORAGE)
}

// Client-side limits sit above the backend's own (LLM 30 s, pytest run 120 s) so the server reports first.
const TIMEOUT_MS = {
  default: 30_000,
  llm: 75_000,
  execute: 180_000,
  repair: 300_000,
}

// Mirrors the backend's {status, code, message, details} error contract.
export class ApiError extends Error {
  constructor(status, code, message, details = []) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

async function request(path, options = {}, timeoutMs = TIMEOUT_MS.default) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let response
  let body
  try {
    const apiKey = getApiKey()
    const headers = { ...(options.headers ?? {}), ...(apiKey ? { [API_KEY_HEADER]: apiKey } : {}) }
    response = await fetch(`${BASE_URL}${path}`, { ...options, headers, signal: controller.signal })
    body = await response.json().catch(() => ({}))
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new ApiError(0, 'TIMEOUT', `The backend did not respond within ${Math.round(timeoutMs / 1000)} s`)
    }
    throw new ApiError(0, 'BACKEND_UNREACHABLE', 'The AutoMigrate AI backend is not reachable')
  } finally {
    clearTimeout(timer)
  }
  if (!response.ok) {
    // Our backend always sends a code; a bare 5xx comes from the dev proxy when the backend is down.
    if (response.status >= 500 && typeof body.code !== 'string') {
      throw new ApiError(response.status, 'BACKEND_UNREACHABLE', 'The AutoMigrate AI backend is not reachable')
    }
    throw new ApiError(
      response.status,
      body.code ?? `HTTP_${response.status}`,
      typeof body.message === 'string' ? body.message : `Request failed with HTTP ${response.status}`,
      Array.isArray(body.details) ? body.details : [],
    )
  }
  return body
}

function postJson(path, payload, timeoutMs) {
  return request(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    timeoutMs,
  )
}

export const getHealth = () => request('/health', {}, 5_000)

export function uploadAsset(file) {
  const form = new FormData()
  form.append('file', file)
  return request('/migration/upload', { method: 'POST', body: form })
}

export const analyzeMigration = (migrationId, targetFramework) =>
  postJson('/migration/analyze', { migrationId, targetFramework }, TIMEOUT_MS.llm)

export const generateCode = (migrationId, targetFramework, simulateLocatorFailure = false) =>
  postJson('/migration/generate', { migrationId, targetFramework, simulateLocatorFailure })

export const validateCode = (migrationId, targetFramework) =>
  postJson('/migration/validate', { migrationId, targetFramework })

export const executeTests = (migrationId, targetFramework) =>
  postJson('/migration/execute', { migrationId, targetFramework }, TIMEOUT_MS.execute)

export const repairFailure = (migrationId, targetFramework, apply = false) =>
  postJson('/migration/repair', { migrationId, targetFramework, apply }, apply ? TIMEOUT_MS.repair : TIMEOUT_MS.llm)

export const getReport = (migrationId, targetFramework) =>
  request(`/migration/${encodeURIComponent(migrationId)}/report?targetFramework=${encodeURIComponent(targetFramework)}`)

export const getKeywordMappings = (migrationId, targetFramework) =>
  request(`/migration/${encodeURIComponent(migrationId)}/keywords?targetFramework=${encodeURIComponent(targetFramework)}`)

export const syncQTest = (migrationId) => postJson('/qtest/sync', { migrationId })

export const getTraceability = (migrationId) => request(`/qtest/${encodeURIComponent(migrationId)}/traceability`)
