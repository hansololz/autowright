// Backend client (§19). Discovers port+token via preload (backend.json).
import type { DraftJob, Health, StateSnapshot, WsEvent } from './types'

declare global {
  interface Window {
    autowright?: {
      backendInfo(): Promise<{ port: number; token: string } | null>
      backendStatus(): Promise<{ state: 'idle' | 'installing' | 'ok' | 'failed'; detail: string }>
      // §3 CLI on PATH (§10 step 3, §4.9 COMMAND LINE card)
      cliStatus(): Promise<{ state: 'installed' | 'missing' | 'foreign'; path: string; onPath: boolean }>
      cliInstall(): Promise<{ ok: true } | { ok: false; error: string }>
      cliUninstall(): Promise<{ ok: true } | { ok: false; hint: string }>
      openApp(hash: string): Promise<void>
      pickFolder(defaultPath?: string): Promise<string | null>
      resizePanel(h: number): Promise<void>
      saveFile(defaultName: string, data: ArrayBuffer): Promise<string | null>
      openArchive(): Promise<{ name: string; data: Uint8Array } | null>
      revealPath(p: string): Promise<void>
      // §9.5 report modal: OS details + bundle version for the info block
      platformInfo(): Promise<{ platform: string; osName?: string; release: string; arch: string; version: string; trayPanel?: boolean }>
      applySettings(s: { login?: boolean; menuBarIcon?: boolean; automaticUpdateCheck?: boolean }): Promise<void>
      tailLogs(): Promise<{ name: string; text: string }[]>
      listRequestLogs(): Promise<string[]>
      readRequestLog(name: string): Promise<string | null>
      trayAlert(on: boolean): Promise<void>
      // §9.4 in-app updates (§3). The error state may carry a detail line —
      // a platform with no update feed answers the plain no-updates sentence,
      // which the §9.4 page renders instead of the generic network copy.
      updateCheck(): Promise<{ state: 'uptodate' } | { state: 'error'; error?: string } | { state: 'available'; version: string }>
      updateDownload(): Promise<{ ok: true } | { error: string }>
      updateInstall(): Promise<{ ok: true } | { busy: true } | { error: string }>
      updateBrewManaged(): Promise<boolean>
      // §4.9 QUIT card (§3 explicit-quit exception)
      quitAll(force?: boolean): Promise<{ ok: true } | { busy: true } | { error: string }>
      // §4.9 RESET card (§3 reset flow): erases every §5 root and every secret,
      // then the app quits; the next launch runs onboarding as a fresh
      // install. The service registration, the CLI shim, and the app survive.
      resetAll(): Promise<{ ok: true } | { busy: true } | { error: string }>
      // §3 reset-progress stage tokens ('secrets' | 'service' | 'data' |
      // 'quit') for the §4.9 reset progress overlay's stage line.
      // Each on* returns an unsubscribe for effect cleanup (void-typed
      // stubs in tests are fine — callers optional-chain the return).
      onResetProgress(cb: (stage: string) => void): (() => void) | void
      onUpdateProgress(cb: (percent: number | null) => void): (() => void) | void
      updateAvailable(): Promise<string | null>
      onUpdateAvailable(cb: (version: string | null) => void): (() => void) | void
      onOpenTarget(cb: (hash: string) => void): (() => void) | void
    }
  }
}

let base = ''
let token = ''

export async function connectInfo(): Promise<boolean> {
  const info = await window.autowright?.backendInfo()
  if (!info) return false
  base = `http://127.0.0.1:${info.port}`
  token = info.token
  return true
}

// §5.1/§5.2 archives ride as raw zip bytes (§19: no multipart)
async function rawPost<T>(path: string, data: Uint8Array): Promise<T> {
  const r = await fetch(base + path, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/octet-stream' },
    body: data as unknown as BodyInit,
  })
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail } catch { /* ignore */ }
    throw Object.assign(new Error(detail || r.statusText), { status: r.status })
  }
  return r.json()
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(base + path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let detail = ''
    try { detail = (await r.json()).detail } catch { /* ignore */ }
    throw Object.assign(new Error(detail || r.statusText), { status: r.status })
  }
  return r.json()
}

export const api = {
  // §19 GET /health — the one unauthenticated route: version/app plus the §5.1
  // `os` token and the §2 capability flags every OS-coupled surface gates on
  // (§9). Sent without the bearer header, exactly as the route is defined.
  health: async (): Promise<Health> => {
    const r = await fetch(base + '/health')
    if (!r.ok) throw new Error(r.statusText)
    return r.json() as Promise<Health>
  },
  state: () => req<StateSnapshot>('GET', '/state'),
  instructions: () => req<{ framework: string; build: string }>('GET', '/instructions'),
  // §4.5/§19: the machine kind — the API serializes the display label
  // §6/§19 `queue`: the §9.2 capacity popup's Queue action — at capacity the
  // start joins the firing queue instead of answering 409.
  executeNow: (automationId: string, version?: string, trigger: 'manual' | 'menubar' = 'manual', queue?: boolean) =>
    req<{ executionId: string; queued: boolean }>('POST', `/automations/${automationId}/execute`,
      { version, trigger, queue: queue || undefined }),
  cancelExecution: (executionId: string) => req('POST', `/executions/${executionId}/cancel`),
  // §7 in-place retry: same execution record, from the failed step
  retryExecution: (executionId: string) => req<{ executionId: string }>('POST', `/executions/${executionId}/retry`),
  // §7 skip: index must be the currently executing step (409 otherwise)
  skipStep: (executionId: string, index: number) =>
    req('POST', `/executions/${executionId}/skip-step`, { index }),
  // §19 executions query — §7 paging: status filter (a §4.6 status or
  // 'finished' = any terminal one), keyset cursor, envelope with the match
  // total (what sizes the pager readout).
  // §19: `automation` and `status` repeat once per value (the §7 filter
  // modal's multi-selects); `startedFromMs`/`startedToMs` are the inclusive
  // §7 time range.
  listExecutions: (opts: {
    automation?: string | string[]; status?: string | string[]; limit?: number
    startedFromMs?: number; startedToMs?: number
    before?: { startedMs: number; id: string }
  } = {}) => {
    const many = (v: string | string[] | undefined) => v === undefined ? [] : Array.isArray(v) ? v : [v]
    const q = [
      ...many(opts.automation).map((id) => `automation=${encodeURIComponent(id)}`),
      ...many(opts.status).map((s) => `status=${s}`),
      ...(opts.startedFromMs !== undefined ? [`startedFromMs=${opts.startedFromMs}`] : []),
      ...(opts.startedToMs !== undefined ? [`startedToMs=${opts.startedToMs}`] : []),
      ...(opts.limit !== undefined ? [`limit=${opts.limit}`] : []),
      ...(opts.before ? [`beforeStartedMs=${opts.before.startedMs}`, `beforeId=${opts.before.id}`] : []),
    ]
    return req<{ executions: import('./types').Execution[]; total: number }>(
      'GET', `/executions${q.length ? `?${q.join('&')}` : ''}`)
  },
  getExecution: (executionId: string) => req<import('./types').Execution>('GET', `/executions/${executionId}`),
  // §19 lazy logs: both params → that step attempt's file; neither → the execution log.
  // `tail` caps the response at the last N lines of that log (§7: the pane is
  // capped, so a chatty run never ships or renders an unbounded array).
  getExecutionLogs: (executionId: string, step?: number, attempt?: number, tail?: number) => {
    const q = [
      ...(step !== undefined ? [`step=${step}`, `attempt=${attempt ?? 1}`] : []),
      ...(tail !== undefined ? [`tail=${tail}`] : []),
    ]
    return req<{ lines: import('./types').LogLine[] }>(
      'GET', `/executions/${executionId}/logs${q.length ? `?${q.join('&')}` : ''}`)
  },
  getAutomation: (automationId: string) => req<import('./types').Automation>('GET', `/automations/${automationId}`),
  patchAutomation: (automationId: string, patch: Record<string, unknown>) =>
    req<import('./types').Automation>('PATCH', `/automations/${automationId}`, patch),
  // §19 pure function endpoint: validate + label §4.3-shaped trigger dicts —
  // the renderer's only source of trigger display strings and next occurrences
  triggersPreview: (triggers: object[]) =>
    req<{ triggers: import('./types').TriggerPreview[] }>('POST', '/triggers/preview', { triggers }),
  // §19 iMessage permission checklist (§9.2): status probe + Automation prompt
  imessagePermissions: () =>
    req<{ fullDisk: boolean; automation: 'granted' | 'denied' | 'unknown' }>(
      'GET', '/imessage/permissions'),
  imessageAutomationProbe: () =>
    req<{ automation: 'granted' | 'denied' }>('POST', '/imessage/permissions/automation-probe'),
  deleteAutomation: (automationId: string) => req('DELETE', `/automations/${automationId}`),
  clearMemory: (automationId: string) => req('POST', `/automations/${automationId}/memory/clear`),
  // §6 firing queue — cancels every waiting entry; running executions are untouched
  clearQueue: (automationId: string) =>
    req<{ cancelled: number }>('POST', `/automations/${automationId}/queue/clear`),
  // §6.3 memory snapshots
  createSnapshot: (automationId: string, name?: string) =>
    req<{ snapshot: import('./types').MemorySnapshot }>('POST', `/automations/${automationId}/memory/snapshots`, { name }),
  renameSnapshot: (automationId: string, snapshotId: string, name: string | null) =>
    req<{ snapshot: import('./types').MemorySnapshot }>('PATCH', `/automations/${automationId}/memory/snapshots/${snapshotId}`, { name }),
  restoreSnapshot: (automationId: string, snapshotId: string) =>
    req('POST', `/automations/${automationId}/memory/snapshots/${snapshotId}/restore`),
  deleteSnapshot: (automationId: string, snapshotId: string) =>
    req('DELETE', `/automations/${automationId}/memory/snapshots/${snapshotId}`),
  createAutomation: (body: Record<string, unknown>) => req<import('./types').Automation>('POST', '/automations', body),
  saveVersion: (automationId: string, body: Record<string, unknown>) =>
    req<{ version: number }>('POST', `/automations/${automationId}/versions`, body),
  // §19 the one draft-container surface: owner = automation id | 'pending'
  // (the §4.4 create-mode slot <root>/draft/). agentId rides beside the
  // pending payload only — the identity no automation record exists to hold.
  getDraft: (owner: string) =>
    req<{ draft: import('./types').DraftPayload | null; agentId: string | null
      // §19 background continuation: the owner's building job or held outcome
      job?: import('./types').DraftJobRef }>('GET', `/draft/${owner}`),
  putDraft: (owner: string, draft: unknown, agentId?: string | null) =>
    req('PUT', `/draft/${owner}`, { draft, ...(agentId !== undefined ? { agentId } : {}) }),
  openDraft: (owner: string) => req('POST', `/draft/${owner}/open`),
  deleteDraft: (owner: string) => req('DELETE', `/draft/${owner}`),
  // §19/§4.4 the chat-thread surface — the thread lives at the container root
  // and outlives the draft; an empty list unlinks it (§11 Clear chat).
  getChat: (owner: string) => req<{ chat: import('./types').ChatEntry[] }>('GET', `/chat/${owner}`),
  putChat: (owner: string, chat: import('./types').ChatEntry[]) => req('PUT', `/chat/${owner}`, { chat }),
  restore: (automationId: string, v: number) => req<{ version: number }>('POST', `/automations/${automationId}/restore`, { version: v }),
  // §4.4/§19 delete an old version — never the current one (the UI hides the affordance)
  deleteVersion: (automationId: string, v: number) =>
    req<{ automation: import('./types').Automation }>('DELETE', `/automations/${automationId}/versions/${v}`),
  // §19 test: starts a §4.5 test execution record of the sent draft's steps;
  // progress via the ordinary exec.* events, cancel via POST /executions/{id}/cancel
  postTest: (body: Record<string, unknown>) => req<{ executionId: string }>('POST', '/tests', body),
  // §6.2 declared packages: fast installed-check / blocking ensure (§19)
  checkPackages: (packages: { pip: string; import: string }[]) =>
    req<{ packages: import('./types').PackageDep[] }>('POST', '/packages/check', { packages }),
  installPackages: (packages: { pip: string; import: string }[]) =>
    req<{ packages: import('./types').PackageDep[] }>('POST', '/packages/install', { packages }),
  // §6.2 updates: read-only PyPI check / pip install --upgrade, no manifest writes (§19)
  outdatedPackages: (packages: { pip: string; import: string }[]) =>
    req<{ packages: import('./types').PackageDep[] }>('POST', '/packages/outdated', { packages }),
  updatePackages: (packages: { pip: string; import: string }[]) =>
    req<{ packages: import('./types').PackageDep[] }>('POST', '/packages/update', { packages }),
  postDraftJob: (body: Record<string, unknown>) => req<{ jobId: string }>('POST', '/drafts', body),
  getDraftJob: (jobId: string) => req<DraftJob>('GET', `/drafts/${jobId}`),
  cancelDraftJob: (jobId: string) => req('DELETE', `/drafts/${jobId}`),
  // §19 background continuation: the editor consumed a settled job's outcome
  ackDraftJob: (jobId: string) => req('POST', `/drafts/${jobId}/ack`),
  listAgents: () => req<import('./types').Agent[]>('GET', '/agents'),
  addAgent: (body: Record<string, unknown>) => req<import('./types').Agent>('POST', '/agents', body),
  patchAgent: (id: string, body: Record<string, unknown>) => req('PATCH', `/agents/${id}`, body),
  deleteAgent: (id: string) => req('DELETE', `/agents/${id}`),
  checkAgent: (id: string) => req<{ status: string }>('POST', `/agents/${id}/check`),
  // §19 §4.7 readiness check before an agent record exists (§10 found cards)
  checkHarness: (harness: string, model?: string | null, mode: string = 'default') =>
    req<{ status: string }>('POST', '/agents/check-harness', { harness, mode, model }),
  detectAgents: () =>
    req<{ id: string; name: string; installed: boolean; signedIn: boolean | null; detail: string }[]>(
      'GET', '/agents/detect'),
  // §19 real installs + sign-in help (§10 step 2)
  installHarness: (id: string) => req('POST', '/agents/install', { id }),
  installStatus: (id: string) =>
    req<{ state: 'idle' | 'running' | 'done' | 'failed'; percent?: number; line?: string; error?: string }>(
      'GET', `/agents/install/${id}`),
  loginHarness: (id: string) => req<{ ok: boolean; method: 'browser' | 'terminal' }>('POST', '/agents/login', { id }),
  signinStatus: (id: string) => req<{ installed: boolean; signedIn: boolean | null }>('GET', `/agents/signin/${id}`),
  ollamaStatus: () => req<{ ready: boolean; installed: boolean; models: string[] }>('GET', '/ollama/status'),
  ollamaPull: (model: string) => req('POST', '/ollama/pull', { model }),
  // §19: both writes return the serialized secret entity — the creating
  // client learns the minted §4.8 id without a second fetch. Routes are
  // id-keyed; only create carries a name (§4.8: names are immutable).
  createSecret: (name: string, value: string, description?: string) =>
    req<import('./types').SecretMeta>('POST', '/secrets',
      description === undefined ? { name, value } : { name, value, description }),
  putSecret: (id: string, value: string, description?: string) =>
    req<import('./types').SecretMeta>('PUT', `/secrets/${id}`,
      description === undefined ? { value } : { value, description }),
  deleteSecret: (id: string) => req('DELETE', `/secrets/${id}`),
  patchSettings: (patch: Record<string, unknown>) =>
    req<import('./types').Settings>('PATCH', '/settings', patch),
  setDataPath: (path: string) => req<import('./types').Settings>('POST', '/settings/data-path', { path }),
  // §5.1 transfer archives — raw zip bytes both ways (§19: no multipart)
  exportAutomation: async (automationId: string, values: boolean): Promise<ArrayBuffer> => {
    const r = await fetch(`${base}/automations/${automationId}/export?values=${values ? 1 : 0}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!r.ok) {
      // §19 error convention (same as req/rawPost): surface the API's `detail`,
      // so the §9.2 export toast says WHY — e.g. the §5.1 422 naming the step
      // whose secret or agent reference has no record to travel with.
      let detail = ''
      try { detail = (await r.json()).detail } catch { /* ignore */ }
      throw Object.assign(new Error(detail || r.statusText), { status: r.status })
    }
    return r.arrayBuffer()
  },
  importAutomation: (data: Uint8Array) =>
    rawPost<{ automation: import('./types').Automation; summary: import('./types').ImportSummary }>(
      '/automations/import', data),
  // §5.2 two-phase import — preview (URL or file bytes), then confirm by token
  importPreview: (data: Uint8Array) =>
    rawPost<{ token: string; preview: import('./types').ImportPreview }>(
      '/automations/import/preview', data),
  importFromUrl: (url: string) =>
    req<{ token: string; preview: import('./types').ImportPreview }>(
      'POST', '/automations/import/url', { url }),
  importConfirm: (tok: string) =>
    req<{ automation: import('./types').Automation; summary: import('./types').ImportSummary }>(
      'POST', '/automations/import/confirm', { token: tok }),
  // Raw result-dir file (§4.5) — Response, not JSON: callers .text() or .blob() it.
  resultFile: async (executionId: string, name: string): Promise<Response> => {
    const r = await fetch(`${base}/executions/${executionId}/result/${encodeURIComponent(name)}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!r.ok) throw new Error(r.statusText)
    return r
  },
}

export function openWs(onEvent: (msg: WsEvent) => void): () => void {
  let sock: WebSocket | null = null
  let closed = false
  const connect = () => {
    if (closed) return
    sock = new WebSocket(`${base.replace('http', 'ws')}/ws?token=${token}`)
    sock.onmessage = (e) => {
      // A malformed frame (or a handler that throws on one) must never kill the
      // socket's message loop — log it and drop that frame alone.
      try {
        onEvent(JSON.parse(e.data))
      } catch (err) {
        console.warn('Dropped a WebSocket frame:', err)
      }
    }
    // Errors always arrive with (or just before) a close — let onclose own the
    // reconnect; this handler only keeps the failure from surfacing unhandled.
    sock.onerror = () => { console.warn('WebSocket error — reconnecting.') }
    sock.onclose = () => {
      if (closed) return
      // A backend restart binds a NEW port and token — re-read backend.json
      // before each reconnect attempt or the loop retries a dead address forever.
      setTimeout(() => { void connectInfo().finally(connect) }, 1500)
    }
    sock.onopen = () => onEvent({ event: 'ws.open' })
  }
  connect()
  return () => { closed = true; sock?.close() }
}
