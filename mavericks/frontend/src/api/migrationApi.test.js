import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './migrationApi.js'

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('migration API client', () => {
  it('posts the documented request bodies to the right endpoints', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }))
    vi.stubGlobal('fetch', fetchMock)

    await api.analyzeMigration('MIG-001', 'selenium')
    await api.generateCode('MIG-001', 'playwright', true)
    await api.validateCode('MIG-001', 'playwright')
    await api.executeTests('MIG-001', 'playwright')
    await api.repairFailure('MIG-001', 'playwright', true)
    await api.syncQTest('MIG-001')

    const calls = fetchMock.mock.calls.map(([url, init]) => [url, init.method, JSON.parse(init.body)])
    expect(calls).toEqual([
      ['/api/migration/analyze', 'POST', { migrationId: 'MIG-001', targetFramework: 'selenium' }],
      ['/api/migration/generate', 'POST', { migrationId: 'MIG-001', targetFramework: 'playwright', simulateLocatorFailure: true }],
      ['/api/migration/validate', 'POST', { migrationId: 'MIG-001', targetFramework: 'playwright' }],
      ['/api/migration/execute', 'POST', { migrationId: 'MIG-001', targetFramework: 'playwright' }],
      ['/api/migration/repair', 'POST', { migrationId: 'MIG-001', targetFramework: 'playwright', apply: true }],
      ['/api/qtest/sync', 'POST', { migrationId: 'MIG-001' }],
    ])
  })

  it('normal generation never asks for a simulated failure', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)
    await api.generateCode('MIG-001', 'playwright')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).simulateLocatorFailure).toBe(false)
  })

  it('uploads the file as multipart form data', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(201, { migrationId: 'MIG-001' }))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['{}'], 'login_test.json', { type: 'application/json' })
    await expect(api.uploadAsset(file)).resolves.toEqual({ migrationId: 'MIG-001' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/migration/upload')
    expect(init.body.get('file').name).toBe('login_test.json')
  })

  it('encodes path and query parameters', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)
    await api.getReport('MIG/../x', 'selenium')
    await api.getKeywordMappings('MIG-001', 'play wright')
    await api.getTraceability('MIG-001')
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/migration/MIG%2F..%2Fx/report?targetFramework=selenium',
      '/api/migration/MIG-001/keywords?targetFramework=play%20wright',
      '/api/qtest/MIG-001/traceability',
    ])
  })

  it('sends the API key from this tab only when one is set', async () => {
    const store = new Map()
    vi.stubGlobal('sessionStorage', {
      getItem: (k) => store.get(k) ?? null,
      setItem: (k, v) => store.set(k, v),
      removeItem: (k) => store.delete(k),
    })
    const fetchMock = vi.fn(async () => jsonResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await api.validateCode('MIG-001', 'playwright')
    api.setApiKey('secret-key-123456')
    await api.validateCode('MIG-001', 'playwright')
    api.setApiKey(null)
    await api.getHealth()

    const keys = fetchMock.mock.calls.map(([, init]) => init.headers['X-API-Key'])
    expect(keys).toEqual([undefined, 'secret-key-123456', undefined])
    expect(fetchMock.mock.calls[1][1].headers['Content-Type']).toBe('application/json')
  })

  it('surfaces 401 responses as UNAUTHORIZED', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, { status: 401, code: 'UNAUTHORIZED', message: 'A valid X-API-Key header is required', details: [] })))
    const error = await api.uploadAsset(new File(['{}'], 'x.json')).catch((e) => e)
    expect([error.status, error.code]).toEqual([401, 'UNAUTHORIZED'])
  })

  it("surfaces the backend's error contract", async () => {
    const details = [{ field: 'migrationId', message: 'Field required' }]
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(422, { status: 422, code: 'VALIDATION_ERROR', message: 'The request is invalid', details })))
    const error = await api.analyzeMigration('MIG-001', 'playwright').catch((e) => e)
    expect(error).toBeInstanceOf(api.ApiError)
    expect([error.status, error.code, error.message, error.details]).toEqual([422, 'VALIDATION_ERROR', 'The request is invalid', details])
  })

  it('reports an unreachable backend for network errors and bare proxy 5xx responses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    expect((await api.getHealth().catch((e) => e)).code).toBe('BACKEND_UNREACHABLE')

    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 500 })))
    expect((await api.getHealth().catch((e) => e)).code).toBe('BACKEND_UNREACHABLE')
  })

  it('keeps real backend 5xx errors instead of calling them unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(502, { status: 502, code: 'QTEST_ERROR', message: 'qTest is down', details: [] })))
    const error = await api.syncQTest('MIG-001').catch((e) => e)
    expect([error.code, error.message]).toEqual(['QTEST_ERROR', 'qTest is down'])
  })

  it('falls back to a generic message when an error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>', { status: 404 })))
    const error = await api.getHealth().catch((e) => e)
    expect([error.code, error.message]).toEqual(['HTTP_404', 'Request failed with HTTP 404'])
  })

  it('times out hung requests with a TIMEOUT error', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
          }),
      ),
    )
    const pending = api.validateCode('MIG-001', 'playwright').catch((e) => e)
    await vi.advanceTimersByTimeAsync(30_000)
    const error = await pending
    expect([error.code, error.message]).toEqual(['TIMEOUT', 'The backend did not respond within 30 s'])
  })

  it('gives slow steps longer than the backend timeouts', async () => {
    vi.useFakeTimers()
    const aborted = []
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (url, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener('abort', () => {
              aborted.push(url)
              reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
            })
          }),
      ),
    )
    const execute = api.executeTests('MIG-001', 'playwright').catch((e) => e)
    const repair = api.repairFailure('MIG-001', 'playwright', true).catch((e) => e)
    await vi.advanceTimersByTimeAsync(179_000)
    expect(aborted).toEqual([])
    await vi.advanceTimersByTimeAsync(1_000)
    expect(aborted).toEqual(['/api/migration/execute'])
    await vi.advanceTimersByTimeAsync(120_000)
    expect(aborted).toEqual(['/api/migration/execute', '/api/migration/repair'])
    expect((await execute).code).toBe('TIMEOUT')
    expect((await repair).code).toBe('TIMEOUT')
  })
})
