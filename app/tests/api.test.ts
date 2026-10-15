// Tests for src/api.ts request plumbing. `req` is module-private, so it is
// exercised through the thin api.* wrappers (api.state, api.executeNow) with
// global fetch stubbed. Discovery reads window.autowright.backendInfo() —
// stubbed before connectInfo() is called, which fills the module-level
// base/token used by every request.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, connectInfo, IMAGE_REQUEST_TIMEOUT_MS, LONG_REQUEST_TIMEOUT_MS, openWs, REQUEST_TIMEOUT_MS, requestTimeoutMs, saveFile } from '../src/api'

const setBackendInfo = (info: { port: number; token: string } | null) => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    backendInfo: async () => info,
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('connectInfo', () => {
  it('returns false when the preload has no backend info', async () => {
    setBackendInfo(null)
    expect(await connectInfo()).toBe(false)
  })
  it('returns true and stores port + token', async () => {
    setBackendInfo({ port: 4242, token: 'tok' })
    expect(await connectInfo()).toBe(true)
  })
})

describe('req (via api.state / api.executeNow)', () => {
  beforeEach(async () => {
    setBackendInfo({ port: 4242, token: 'tok' })
    await connectInfo()
  })

  it('ok path: GET hits base+path with the bearer token and returns the JSON', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ version: '1.0' }),
    }) as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
    const s = await api.state()
    expect(s).toEqual({ version: '1.0' })
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:4242/state', {
      method: 'GET',
      headers: { Authorization: 'Bearer tok' },
      body: undefined,
      signal: expect.any(AbortSignal),
    })
  })

  it('a body adds Content-Type and JSON-serializes', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ executionId: 'e1' }),
    }) as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
    await api.executeNow('a1')
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:4242/automations/a1/execute', {
      method: 'POST',
      headers: { Authorization: 'Bearer tok', 'Content-Type': 'application/json' },
      body: JSON.stringify({ version: undefined, trigger: 'manual' }),
      signal: expect.any(AbortSignal),
    })
  })

  it('error with a JSON {detail} body → Error message is the detail, .status attached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 404, statusText: 'Not Found',
      json: async () => ({ detail: 'automation not found' }),
    }) as unknown as Response))
    const err = await api.state().then(() => null, (e: Error & { status?: number }) => e)
    expect(err).toBeInstanceOf(Error)
    expect(err!.message).toBe('automation not found')
    expect(err!.status).toBe(404)
  })

  it('error with a non-JSON body falls back to statusText', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 500, statusText: 'Internal Server Error',
      json: async () => { throw new Error('not json') },
    }) as unknown as Response))
    const err = await api.state().then(() => null, (e: Error & { status?: number }) => e)
    expect(err!.message).toBe('Internal Server Error')
    expect(err!.status).toBe(500)
  })

  it('error with JSON but empty detail also falls back to statusText', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 409, statusText: 'Conflict',
      json: async () => ({}),
    }) as unknown as Response))
    const err = await api.state().then(() => null, (e: Error & { status?: number }) => e)
    expect(err!.message).toBe('Conflict')
    expect(err!.status).toBe(409)
  })
})

// §19 client timeout: a wedged-but-listening backend fails requests instead of
// hanging every surface — 30 s, and 660 s for the long routes like the §20 CLI.
describe('client request timeout (§19)', () => {
  beforeEach(async () => {
    setBackendInfo({ port: 4242, token: 'tok' })
    await connectInfo()
  })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  it('a never-answering backend fails the request at 30 s instead of hanging', async () => {
    vi.useFakeTimers()
    // AbortSignal.timeout runs on the platform clock — route it through the
    // fake timers so the test can walk the 30 s.
    vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms: number) => {
      const controller = new AbortController()
      setTimeout(() => controller.abort(new DOMException('The operation timed out.', 'TimeoutError')), ms)
      return controller.signal
    })
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(init.signal!.reason))
    })))
    let settled: unknown = 'pending'
    const pending = api.state().then(() => 'answered', (e: Error) => e.message)
    void pending.then((v) => { settled = v })
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1)
    expect(settled).toBe('pending')
    await vi.advanceTimersByTimeAsync(1)
    // §19 plain words, never the browser's "signal timed out"
    expect(await pending).toBe("The backend didn't answer in time.")
  })

  it('a connection failure surfaces as plain words, never "Failed to fetch"', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    await expect(api.state()).rejects.toThrow("The backend isn't reachable.")
    await expect(api.importPreview(new Uint8Array([1]))).rejects.toThrow("The backend isn't reachable.")
  })

  it('a timed-out raw upload surfaces as plain words too', async () => {
    vi.spyOn(AbortSignal, 'timeout').mockImplementation(() => {
      const controller = new AbortController()
      controller.abort(new DOMException('signal timed out', 'TimeoutError'))
      return controller.signal
    })
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => { throw init.signal!.reason }))
    await expect(api.importPreview(new Uint8Array([1]))).rejects.toThrow("The backend didn't answer in time.")
  })

  it('HTTP-detail errors pass through unchanged', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 409, statusText: 'Conflict', json: async () => ({ detail: 'Busy right now.' }),
    }) as unknown as Response))
    await expect(api.state()).rejects.toThrow('Busy right now.')
  })

  it('every request builds its signal from the route\u2019s timeout — /health included', async () => {
    const timeout = vi.spyOn(AbortSignal, 'timeout')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({}) }) as unknown as Response))
    await api.health()
    await api.state()
    await api.installPackages([])
    expect(timeout.mock.calls.map((c) => c[0])).toEqual([REQUEST_TIMEOUT_MS, REQUEST_TIMEOUT_MS, LONG_REQUEST_TIMEOUT_MS])
  })

  it('gives the install/import/delete/memory-copy/slow marketplace routes 660 s, images 90 s, and everything else 30 s', () => {
    const long = [
      ['POST', '/packages/install'], ['POST', '/packages/update'],
      ['POST', '/automations/import'], ['POST', '/automations/import/preview'],
      ['POST', '/automations/import/url'], ['POST', '/automations/import/confirm'],
      ['POST', '/marketplace/sources/s1/entries/3/preview'],
      ['POST', '/marketplace/sources'], ['POST', '/marketplace/sources/s1/refresh'],
      ['POST', '/marketplace/refresh'], ['GET', '/marketplace/sources/s1/entries/3/archive'],
      ['POST', '/marketplace/catalogs'], ['PUT', '/marketplace/sources/s1/catalog'],
      ['DELETE', '/automations/a1'],
      ['POST', '/automations/a1/memory/clear'], ['POST', '/automations/a1/memory/snapshots'],
      ['POST', '/automations/a1/memory/snapshots/s1/restore'],
    ]
    for (const [method, path] of long) expect(requestTimeoutMs(method, path)).toBe(LONG_REQUEST_TIMEOUT_MS)
    const short = [
      ['GET', '/state'], ['GET', '/automations/a1'], ['PATCH', '/automations/a1'],
      ['POST', '/automations/a1/execute'], ['POST', '/packages/check'],
      ['DELETE', '/automations/a1/memory/snapshots/s1'], ['DELETE', '/marketplace/sources/s1'],
      ['POST', '/triggers/preview'],
      ['GET', '/marketplace'], ['GET', '/marketplace/sources/s1/catalog'],
      ['PATCH', '/marketplace/sources/s1'],
    ]
    for (const [method, path] of short) expect(requestTimeoutMs(method, path)).toBe(REQUEST_TIMEOUT_MS)
    // §22.3 marketplace images: the backend slot wait plus the fetch
    expect(requestTimeoutMs('GET', '/marketplace/sources/s1/entries/3/image')).toBe(IMAGE_REQUEST_TIMEOUT_MS)
    expect(IMAGE_REQUEST_TIMEOUT_MS).toBe(90_000)
  })
})

// §19: the raw-bytes routes (export, catalog file, preview image, result file)
// go through the same timed wrapper and the same error convention as req.
describe('raw-bytes routes (§19)', () => {
  beforeEach(async () => {
    setBackendInfo({ port: 4242, token: 'tok' })
    await connectInfo()
  })
  afterEach(() => { vi.restoreAllMocks() })

  const rawCalls = () => [
    () => api.exportAutomation('a1', false),
    () => api.marketplaceCatalogFile('s1'),
    () => api.marketplaceImage('s1', 3),
    () => api.resultFile('e1', 'result.md'),
  ]

  it('a connection failure surfaces as plain words, never "Failed to fetch"', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    for (const call of rawCalls()) await expect(call()).rejects.toThrow("The backend isn't reachable.")
  })

  it('an error answer surfaces the API detail and status', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 422, statusText: 'Unprocessable Entity',
      json: async () => ({ detail: 'Step 2 names a secret with no record.' }),
    }) as unknown as Response))
    for (const call of rawCalls()) {
      const err = await call().then(() => null, (e: Error & { status?: number }) => e)
      expect(err!.message).toBe('Step 2 names a secret with no record.')
      expect(err!.status).toBe(422)
    }
  })

  it('sends the bearer header and the route timeout, merged with the caller signal', async () => {
    const timeout = vi.spyOn(AbortSignal, 'timeout')
    const fetchMock = vi.fn(async () => ({ ok: true, blob: async () => new Blob() }) as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
    const caller = new AbortController()
    await api.marketplaceImage('s1', 3, caller.signal)
    expect(timeout).toHaveBeenCalledWith(IMAGE_REQUEST_TIMEOUT_MS)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://127.0.0.1:4242/marketplace/sources/s1/entries/3/image')
    expect(init.method).toBe('GET')
    expect(init.headers).toEqual({ Authorization: 'Bearer tok' })
    caller.abort()
    expect(init.signal!.aborted).toBe(true)
  })
})

// ---- §10/§12/§19 request shapes: harness install, sign-in, Ollama, drafting ----
// Every wrapper is asserted down to the exact URL, method, and JSON body the
// backend contract (§19) expects.
describe('agent/harness/ollama/draft request shapes', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(async () => {
    setBackendInfo({ port: 4242, token: 'tok' })
    await connectInfo()
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }) as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
  })
  const call = () => fetchMock.mock.calls[0] as unknown as [string, RequestInit]

  it('detectAgents GETs /agents/detect', async () => {
    await api.detectAgents()
    expect(call()[0]).toBe('http://127.0.0.1:4242/agents/detect')
    expect(call()[1].method).toBe('GET')
  })

  it('installHarness POSTs the provider id', async () => {
    await api.installHarness('opencode')
    expect(call()[0]).toBe('http://127.0.0.1:4242/agents/install')
    expect(call()[1].body).toBe(JSON.stringify({ id: 'opencode' }))
  })

  it('installStatus GETs the per-provider snapshot (remount reattach)', async () => {
    await api.installStatus('claude')
    expect(call()[0]).toBe('http://127.0.0.1:4242/agents/install/claude')
    expect(call()[1].method).toBe('GET')
  })

  it('loginHarness POSTs the id; signinStatus polls GET', async () => {
    await api.loginHarness('codex')
    expect(call()[0]).toBe('http://127.0.0.1:4242/agents/login')
    expect(call()[1].body).toBe(JSON.stringify({ id: 'codex' }))
    await api.signinStatus('codex')
    expect(fetchMock.mock.calls[1][0]).toBe('http://127.0.0.1:4242/agents/signin/codex')
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('GET')
  })

  it('checkHarness POSTs harness + mode + model (§10 found-card auto-check)', async () => {
    await api.checkHarness('OpenCode', 'qwen3:8b', 'ollama')
    expect(call()[0]).toBe('http://127.0.0.1:4242/agents/check-harness')
    expect(call()[1].body).toBe(JSON.stringify({ harness: 'OpenCode', mode: 'ollama', model: 'qwen3:8b' }))
  })

  it('checkHarness defaults mode to `default`', async () => {
    await api.checkHarness('Claude Code', null)
    expect(call()[1].body).toBe(JSON.stringify({ harness: 'Claude Code', mode: 'default', model: null }))
  })

  it('ollamaStatus GETs /ollama/status; ollamaPull POSTs the model', async () => {
    await api.ollamaStatus()
    expect(call()[0]).toBe('http://127.0.0.1:4242/ollama/status')
    await api.ollamaPull('qwen3:8b')
    expect(fetchMock.mock.calls[1][0]).toBe('http://127.0.0.1:4242/ollama/pull')
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBe(JSON.stringify({ model: 'qwen3:8b' }))
  })

  it('postDraftJob passes grant arrays through verbatim — empty stays empty', async () => {
    // §19: an explicit [] means "unchecked" and must reach the backend as [],
    // never dropped from the JSON (absent would fall back to all-on defaults).
    await api.postDraftJob({
      mode: 'sync', automationId: 'a1', agentId: 'g1',
      enabledAgents: [], allowedSecrets: [],
    })
    expect(call()[0]).toBe('http://127.0.0.1:4242/drafts')
    expect(call()[1].body).toBe(JSON.stringify({
      mode: 'sync', automationId: 'a1', agentId: 'g1',
      enabledAgents: [], allowedSecrets: [],
    }))
  })

  it('getDraftJob / cancelDraftJob address the job id', async () => {
    await api.getDraftJob('j1')
    expect(call()[0]).toBe('http://127.0.0.1:4242/drafts/j1')
    await api.cancelDraftJob('j1')
    expect(fetchMock.mock.calls[1][0]).toBe('http://127.0.0.1:4242/drafts/j1')
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('DELETE')
  })

  it('putSecret sends description only when given (§4.8: absent description edits nothing)', async () => {
    // §19: the edit route is id-keyed — the name is immutable and never a field
    const secretId = '9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34'
    await api.putSecret(secretId, 'v')
    expect(call()[0]).toBe(`http://127.0.0.1:4242/secrets/${secretId}`)
    expect(call()[1].body).toBe(JSON.stringify({ value: 'v' }))
    await api.putSecret(secretId, 'v', 'what it is for')
    expect((fetchMock.mock.calls[1][1] as RequestInit).body)
      .toBe(JSON.stringify({ value: 'v', description: 'what it is for' }))
  })

  it('createSecret POSTs the name + value; deleteSecret hits the id route (§4.8/§19)', async () => {
    await api.createSecret('MY_TOKEN', 'v')
    expect(call()[0]).toBe('http://127.0.0.1:4242/secrets')
    expect(call()[1].method).toBe('POST')
    expect(call()[1].body).toBe(JSON.stringify({ name: 'MY_TOKEN', value: 'v' }))
    const secretId = '9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34'
    await api.deleteSecret(secretId)
    expect(fetchMock.mock.calls[1][0]).toBe(`http://127.0.0.1:4242/secrets/${secretId}`)
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('DELETE')
  })
})

// §19 reconnect: a backend that stays down must not be asked once a second for
// as long as the app is open — the wait doubles to a ceiling and starts over
// only when a connection actually came back.
describe('openWs reconnect backoff (§19)', () => {
  class FakeSocket {
    static made: FakeSocket[] = []
    onmessage: ((e: { data: string }) => void) | null = null
    onerror: (() => void) | null = null
    onclose: (() => void) | null = null
    onopen: (() => void) | null = null
    close = vi.fn()
    constructor(public url: string) { FakeSocket.made.push(this) }
  }
  let infoCalls = 0

  beforeEach(async () => {
    FakeSocket.made = []
    infoCalls = 0
    ;(window as unknown as Record<string, unknown>).autowright = {
      backendInfo: async () => { infoCalls++; return { port: 4242, token: 'tok' } },
    }
    await connectInfo()
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  // Each close is followed to the millisecond: nothing reconnects a tick early,
  // and exactly one socket opens a tick later.
  const expectRetryAfter = async (index: number, wait: number) => {
    FakeSocket.made[index].onclose!()
    await vi.advanceTimersByTimeAsync(wait - 1)
    expect(FakeSocket.made.length).toBe(index + 1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.made.length).toBe(index + 2)
  }

  it('doubles 1.5 s up to the 15 s ceiling, and starts over after a successful open', async () => {
    const close = openWs(() => {})
    expect(FakeSocket.made.length).toBe(1)

    // four consecutive failures, then the ceiling holds
    for (const [i, wait] of [1500, 3000, 6000, 12000, 15000].entries()) {
      await expectRetryAfter(i, wait)
    }
    // every attempt re-reads backend.json first — a restart binds a new port
    expect(infoCalls).toBeGreaterThanOrEqual(5)

    // the connection came back: the next outage starts at the quick retry again
    FakeSocket.made[5].onopen!()
    await expectRetryAfter(5, 1500)

    close()
  })

  // A throw out of connect() — the constructor refusing a malformed address
  // after a failed backend.json read — used to take the whole retry chain with
  // it, leaving the app permanently disconnected.
  it('a throwing WebSocket constructor still schedules the next backoff tick', async () => {
    let first = true
    class ThrowsOnce extends FakeSocket {
      constructor(url: string) {
        super(url)
        if (first) { first = false; throw new Error('bad address') }
      }
    }
    vi.stubGlobal('WebSocket', ThrowsOnce)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    const close = openWs(() => {})
    expect(FakeSocket.made.length).toBe(1)
    await vi.advanceTimersByTimeAsync(1499)
    expect(FakeSocket.made.length).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.made.length).toBe(2)

    warn.mockRestore()
    close()
  })

  it('the closer cancels the pending retry — a closed socket never reconnects', async () => {
    const close = openWs(() => {})
    FakeSocket.made[0].onclose!()
    close()
    const before = infoCalls
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeSocket.made.length).toBe(1)
    expect(infoCalls).toBe(before)
  })
})

describe('saveFile (§9.2 native save dialog seam)', () => {
  // 2026-10-14 audit: Electron wraps a rejected invoke as "Error invoking
  // remote method 'save-file': Error: …" — the toast gets plain words.
  const w = window as unknown as { autowright?: { saveFile: (n: string, d: ArrayBuffer) => Promise<string | null> } }
  afterEach(() => { delete w.autowright })

  it('passes a saved path and a cancelled dialog through', async () => {
    w.autowright = { saveFile: vi.fn(async () => '/Users/x/out.yaml') }
    expect(await saveFile('out.yaml', new ArrayBuffer(1))).toBe('/Users/x/out.yaml')
    w.autowright = { saveFile: vi.fn(async () => null) }
    expect(await saveFile('out.yaml', new ArrayBuffer(1))).toBeNull()
  })

  it('strips the IPC wrapper and keeps the main process plain words', async () => {
    w.autowright = { saveFile: vi.fn(async () => {
      throw new Error("Error invoking remote method 'save-file': Error: Couldn't save the file: that folder no longer exists")
    }) }
    await expect(saveFile('out.yaml', new ArrayBuffer(1)))
      .rejects.toThrow("Couldn't save the file: that folder no longer exists")
    w.autowright = { saveFile: vi.fn(async () => {
      throw new Error("Error invoking remote method 'save-file': Error: ENOENT: no such file or directory, open '/x/y'")
    }) }
    await expect(saveFile('out.yaml', new ArrayBuffer(1)))
      .rejects.toThrow("Couldn't save the file: ENOENT: no such file or directory, open '/x/y'")
  })

  it('answers null outside Electron', async () => {
    expect(await saveFile('out.yaml', new ArrayBuffer(1))).toBeNull()
  })
})
