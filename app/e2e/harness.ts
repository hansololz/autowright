// §15 e2e harness: one real backend subprocess (isolated tmp AUTOWRIGHT_HOME,
// fake `claude` CLI on PATH) plus one real Electron app per test. Mirrors
// tests/integration/it_harness.py on the Node side.
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { createConnection } from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron, type ElectronApplication, type Page } from 'playwright-core'
import { platformCopy } from '../src/platformCopyTable'

// §9 per-OS copy for the HOST platform — e2e drives the real app, whose
// backend reports this machine's os token, so assertions read the same table
// the renderer does instead of hardcoding one OS's strings.
export const COPY = platformCopy(
  process.platform === 'win32' ? 'windows' : process.platform === 'linux' ? 'linux' : 'macos')

const HERE = path.dirname(fileURLToPath(import.meta.url))
const APP_DIR = path.resolve(HERE, '..')
const REPO = path.resolve(HERE, '..', '..')
const ARTIFACTS = path.join(HERE, 'artifacts')

const PYTHON = process.platform === 'win32'
  ? path.join(REPO, '.venv', 'Scripts', 'python.exe')
  : path.join(REPO, '.venv', 'bin', 'python')
const FAKE_BIN = path.join(REPO, 'tests', 'bin')

// ---------- generic polling (no fixed sleeps) ----------

export async function waitFor<T>(
  fn: () => Promise<T | null | false | undefined>,
  timeoutMs: number,
  what: string,
): Promise<T> {
  const deadline = Date.now() + timeoutMs
  let lastErr: unknown = null
  while (Date.now() < deadline) {
    try {
      const v = await fn()
      if (v) return v
    } catch (e) { lastErr = e }
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error(`timed out waiting for ${what}${lastErr ? ` (last error: ${String(lastErr)})` : ''}`)
}

function tcpOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = createConnection({ host: '127.0.0.1', port }, () => {
      sock.destroy()
      resolve(true)
    })
    sock.setTimeout(1000, () => { sock.destroy(); resolve(false) })
    sock.on('error', () => resolve(false))
  })
}

// ---------- backend ----------

export class Backend {
  home = ''
  port = 0
  token = ''
  private proc: ChildProcess | null = null
  private out = ''

  /** Fresh tmp home + real `python -m autowright.main`, ready to answer. */
  async start(): Promise<this> {
    this.home = await mkdtemp(path.join(os.tmpdir(), 'aw-e2e-'))
    return this.spawnAndWait()
  }

  /** §3 restart: SIGKILL the process (a crash simulation — no lifespan
   * cleanup runs, which is the point), then boot a NEW backend on the SAME
   * home — it binds a new port/token and rewrites backend.json. */
  async restart(): Promise<this> {
    await this.endProcess('SIGKILL')
    await rm(path.join(this.home, 'backend.json'), { force: true })
    return this.spawnAndWait()
  }

  /** Signal the backend and wait (bounded) for it to exit; SIGTERM falls back
   * to SIGKILL when the wait runs out. Mirrors it_harness.stop/kill. */
  private async endProcess(signal: 'SIGTERM' | 'SIGKILL'): Promise<void> {
    const proc = this.proc
    if (!proc || proc.exitCode !== null) return
    const gone = new Promise<void>((r) => proc.once('exit', () => r()))
    proc.kill(signal)
    let timer: NodeJS.Timeout | undefined
    const waited = await Promise.race([
      gone.then(() => true),
      new Promise<boolean>((r) => { timer = setTimeout(() => r(false), 5000) }),
    ])
    clearTimeout(timer)
    if (!waited && signal === 'SIGTERM' && proc.exitCode === null) {
      proc.kill('SIGKILL')
      await Promise.race([gone, new Promise((r) => setTimeout(r, 5000))])
    }
  }

  private async spawnAndWait(): Promise<this> {
    this.out = ''
    const proc = spawn(PYTHON, ['-m', 'autowright.main'], {
      env: {
        ...process.env,
        AUTOWRIGHT_HOME: this.home,
        PATH: `${FAKE_BIN}${path.delimiter}${process.env.PATH ?? ''}`,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    this.proc = proc
    proc.stdout?.on('data', (d: Buffer) => { this.out += d.toString() })
    proc.stderr?.on('data', (d: Buffer) => { this.out += d.toString() })

    let info: { port: number; token: string; pid: number }
    try {
      info = await waitFor(async () => {
        const raw = await readFile(path.join(this.home, 'backend.json'), 'utf-8').catch(() => null)
        if (!raw) return null
        const j = JSON.parse(raw) as { port: number; token: string; pid: number }
        return (await tcpOpen(j.port)) ? j : null
      }, 20_000, `backend.json + open port (output so far:\n${this.out.slice(-2000)})`)
    } catch (e) {
      // A startup timeout throws before `new Backend().start()` ever returns,
      // so the caller holds no handle and afterEach's stop() never runs — tear
      // the half-started python down here or it outlives the whole suite (§15).
      await this.stop()
      throw e
    }
    this.port = info.port
    this.token = info.token
    return this
  }

  async api(method: string, route: string, body?: unknown): Promise<unknown> {
    const res = await fetch(`http://127.0.0.1:${this.port}${route}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    })
    if (!res.ok) throw new Error(`${method} ${route} -> ${res.status}: ${await res.text()}`)
    return res.json()
  }

  /** Seed one automation over the real HTTP API (it_harness.make_draft shape).
   * `steps` overrides the default two-step draft; `opts` adds params, the
   * allowed-secrets grant list, or a authoring agent. */
  async createAutomation(
    name: string,
    steps?: Array<{ file: string; name: string; description: string; code: string }>,
    opts?: { params?: unknown[]; allowedSecrets?: string[]; agentId?: string },
  ): Promise<{ id: string }> {
    const draft = {
      description: 'Runs end to end through the real stack.',
      note: 'Created',
      params: opts?.params ?? [],
      steps: steps ?? [
        {
          file: '01-say.py', name: 'Say', description: 'prints',
          code: 'from autowright import log\nlog("e2e says hi")\n',
        },
        {
          file: '02-finish.py', name: 'Finish', description: 'result',
          code: 'from autowright import result\nresult.status("ok")\nresult.chip("All good")\n',
        },
      ],
      spec: [{ kind: 'h1', text: name }, { kind: 'p', text: 'It runs end to end.' }],
      instructions: null,
    }
    return await this.api('POST', '/automations', {
      draft, name,
      ...(opts?.allowedSecrets ? { allowedSecrets: opts.allowedSecrets } : {}),
      ...(opts?.agentId ? { agentId: opts.agentId } : {}),
    }) as { id: string }
  }

  /** §4.8 placeholder secret — blank value, so NOTHING touches the Keychain.
   * Returns the entity so callers get the minted id (§4.1: steps and grants
   * reference secrets by id; §19: creation is the POST route). */
  async putSecretPlaceholder(name: string, description: string): Promise<{ id: string; name: string }> {
    return await this.api('POST', '/secrets', { name, value: '', description }) as { id: string; name: string }
  }

  /** Fire an execution and return its id without waiting. */
  async execute(automationId: string): Promise<string> {
    const { executionId } = await this.api('POST', `/automations/${automationId}/execute`, {}) as { executionId: string }
    return executionId
  }

  /** Execute over HTTP and poll until the execution settles; returns the record. */
  async executeAndWait(automationId: string, timeoutMs = 60_000): Promise<{ id: string; status: string }> {
    const { executionId } = await this.api('POST', `/automations/${automationId}/execute`, {}) as { executionId: string }
    return waitFor(async () => {
      const e = await this.api('GET', `/executions/${executionId}`) as { id: string; status: string }
      return e.status !== 'queued' && e.status !== 'executing' ? e : null
    }, timeoutMs, `execution ${executionId} to settle`)
  }

  /** Seed one config-only agent (fake `claude` on the backend's PATH answers
   * detection and readiness — no install, no login). */
  async createAgent(name: string): Promise<{ id: string }> {
    return await this.api('POST', '/agents', {
      name, harness: 'Claude Code', mode: 'default', model: null, description: '',
    }) as { id: string }
  }

  /** §15 teardown: stop the backend GRACEFULLY (SIGTERM, bounded wait, SIGKILL
   * only as a fallback) so its lifespan cleanup kills the live step and
   * drafting process groups. Those children run in their own sessions, so a
   * bare SIGKILL here would orphan them — and the home this deletes next is
   * exactly what a successor's startup recovery would have used to reap them. */
  async stop(): Promise<void> {
    await this.endProcess('SIGTERM')
    this.proc = null
    if (this.home) await rm(this.home, { recursive: true, force: true }).catch(() => {})
  }
}

// ---------- electron ----------

export interface AppHandle {
  app: ElectronApplication
  page: Page
}

/** Launch the built app against `home`'s backend.json and pin the renderer's
 * `ad-onboarded` flag. The profile lives inside the tmp home (§15), so a fresh
 * home starts clean — pinning just keeps each test's precondition explicit. */
export async function launchApp(home: string, onboarded: boolean): Promise<AppHandle> {
  const require = createRequire(import.meta.url)
  const app = await _electron.launch({
    // The throttling switches keep renderer timers full-speed even when the
    // window is occluded (screen lock/overlap) — long suite runs otherwise
    // crawl through the onboarding self-check's setTimeout chain.
    args: [
      '.',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
    ],
    cwd: APP_DIR,
    executablePath: require('electron') as unknown as string,
    env: {
      ...process.env,
      AUTOWRIGHT_HOME: home,
      // §15: cli-status must read a tmp path, not the machine's real
      // ~/.local/bin — keeps the Settings COMMAND LINE card deterministic.
      AUTOWRIGHT_SHIM: `${home}/bin/autowright`,
    } as Record<string, string>,
  })
  const page = await app.firstWindow()
  await page.waitForLoadState('domcontentloaded')
  // §9.4: the renderer silently pins ad-last-seen-version at boot; seed it to
  // the app's own version so the tracked localStorage state is deterministic.
  const version = (await readFile(path.join(REPO, 'VERSION'), 'utf-8')).trim()
  await page.evaluate(({ flag, version: v }: { flag: boolean; version: string }) => {
    localStorage.clear()
    if (flag) {
      localStorage.setItem('ad-onboarded', '1')
      localStorage.setItem('ad-last-seen-version', v)
    }
  }, { flag: onboarded, version })
  await page.reload()
  await page.waitForLoadState('domcontentloaded')
  return { app, page }
}

export async function closeApp(h: AppHandle | null): Promise<void> {
  if (!h) return
  try {
    await Promise.race([h.app.close(), new Promise((r) => setTimeout(r, 5000))])
  } finally {
    try { h.app.process().kill('SIGKILL') } catch { /* already gone */ }
  }
}

/** Click a §9 nav-rail row, then park the mouse over the content area and wait
 * for the hover-expanded rail to collapse — otherwise the 212px overlay keeps
 * covering later click targets near the left edge. */
export async function clickNav(page: Page, label: string): Promise<void> {
  // Scoped to the rail: page text can legitimately repeat a rail label (the
  // editor's back button says "Automations" too).
  await page.getByTestId('nav-rail').getByText(label, { exact: true }).click()
  await page.mouse.move(640, 300)
  await waitFor(async () => {
    const w = await page.getByTestId('nav-rail').evaluate((el) => el.getBoundingClientRect().width)
    return w < 60
  }, 5_000, 'nav rail to collapse')
}

export async function shot(page: Page, name: string): Promise<void> {
  await mkdir(ARTIFACTS, { recursive: true })
  await page.screenshot({ path: path.join(ARTIFACTS, name) }).catch(() => {})
}
