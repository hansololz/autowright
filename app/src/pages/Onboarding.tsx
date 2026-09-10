// Onboarding surface (SPEC §10): step 1 (welcome + live self-check) and step 2
// (connect your AI). A card's Continue commits the agents and lands in the app
// shell — the CLI shim install lives only on the §4.9 COMMAND LINE card.
//
// Step 2 is fully real (§10/§19): detection reports installed + sign-in state
// for the four harnesses, installs run in the backend (`harness.install` WS
// stream), and sign-in help opens only when the provider actually needs it.
// Ollama is never a card of its own — the Free local AI card sets up OpenCode
// driving Qwen3 8B through Ollama (§4.7).
import { useEffect, useReducer, useRef } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import { Caret, Eyebrow, LoadingRow, Logo, ManualInstallLine, MiniBadge, ProgressBar, PULSE, ScrollArea, Spinner } from '../ui'

interface Det { id: string; name: string; installed: boolean; signedIn: boolean | null; detail: string }

type CardPhase = 'idle' | 'installing' | 'pulling' | 'signin' | 'checking' | 'connected' | 'failed'
// The Free local AI card's three real pieces (§10), installed in this order.
type LocalPiece = 'opencode' | 'ollama' | 'model'
interface Card {
  phase: CardPhase
  percent: number | null            // install percent, when the stream carries one
  line: string                  // latest install line
  pullPct: number               // §10 Qwen3 8B model download percent
  method: 'browser' | 'terminal' | null
  error: string | null          // failed-install first error line
  notReady: string | null       // failed connection check, shown on the idle card
  queue: LocalPiece[]           // local card: missing pieces being installed
  qi: number                    // local card: index into queue
}

const LOCAL_MODEL = 'qwen3:8b'
// §10: every body variant of the Free local AI card ends with this fit sentence.
const LOCAL_FIT = 'Best for simple steps — for authoring automations, a cloud option gives stronger results.'
const LOCAL_ID = 'local'
const SUG_ORDER = ['claude', 'codex', 'gemini', 'opencode']
// §9 per-OS copy rule: the two bodies that name the machine take the noun from
// the store's platform token, so the map is built per render, not at import.
const suggestions = (machine: string): Record<string, { title: string; body: string; btn: string; primary: boolean }> => ({
  claude: {
    title: 'Claude', primary: true, btn: 'Set up Claude Code',
    body: 'You’ll need a Claude account on Pro or higher. The most capable option — nothing extra to pay.',
  },
  codex: {
    title: 'Codex', primary: false, btn: 'Set up Codex',
    body: 'Signs in with your ChatGPT account.',
  },
  gemini: {
    title: 'Gemini', primary: false, btn: 'Set up Gemini CLI',
    body: `Signs in with your Google account. Generous free tier. Needs Node.js on this ${machine}.`,
  },
  opencode: {
    title: 'OpenCode', primary: false, btn: 'Set up OpenCode',
    body: 'Open-source — works with any provider you’ve already set up.',
  },
  [LOCAL_ID]: {
    title: 'Free local AI', primary: false, btn: 'Download and install · 5.2 GB',
    body: `Sets up OpenCode with Ollama and Qwen3 8B. Local to this ${machine}, works offline. ${LOCAL_FIT}`,
  },
})
// §10: one uniform pick label on every step-2 card — the card names the provider.
const CONTINUE_LABEL = 'Use as default →'

interface Ob {
  phase: 'welcome' | 'connect'
  smStarted: boolean
  smSteps: { name: string; status: 'pending' | 'executing' | 'done'; duration: string }[]
  smShowResult: boolean
  smDone: boolean
  det: 'searching' | 'cards'
  detStarted: boolean
  provs: Det[]
  // Free local AI card pieces (§10): OpenCode installed, Ollama serving,
  // a model installed — null until detection lands.
  localSt: { opencode: boolean; ollama: boolean; model: boolean } | null
  // §10: first model from /ollama/status — the card's model when set;
  // qwen3:8b is only the download fallback when nothing is installed.
  localModel: string | null
  // §10: all three pieces present at detection → the card renders in the
  // found section. Snapshotted once so the card never moves mid-flow.
  localFound: boolean
  cards: Record<string, Card>
  chosen: string | null
  committing: boolean
  sugOpen: boolean
}

const freshCard = (): Card => ({
  phase: 'idle', percent: null, line: '', pullPct: 0, method: null, error: null, notReady: null,
  queue: [], qi: 0,
})

function freshOb(): Ob {
  return {
    phase: 'welcome',
    smStarted: false,
    smSteps: [
      { name: 'Checking your settings', status: 'pending', duration: '' },
      { name: 'Loading your automations', status: 'pending', duration: '' },
      { name: 'Starting the execution engine', status: 'pending', duration: '' },
    ],
    smShowResult: false,
    smDone: false,
    det: 'searching',
    detStarted: false,
    provs: [],
    localSt: null,
    localModel: null,
    localFound: false,
    cards: {},
    chosen: null,
    committing: false,
    sugOpen: false,
  }
}

// ---------- page ----------

export default function Onboarding() {
  const agents = useStore((s) => s.agents)
  const automations = useStore((s) => s.automations)
  const showToast = useStore((s) => s.showToast)
  const setSurface = useStore((s) => s.setSurface)
  const harnessInstall = useStore((s) => s.harnessInstall)
  const ollamaPull = useStore((s) => s.ollamaPull)
  // §2/§9 gating: with `agentInstall` false the §19 install and sign-in-help
  // endpoints answer 409, so the cards hide those actions and show the
  // manual-install line instead. Detection and sign-in state are untouched.
  const agentInstallOn = useStore((s) => s.platformCapabilities.agentInstall)
  const platformOs = useStore((s) => s.platformOs)
  // §9 Linux nuance on §2 `agentInstall`: installs work there, but the
  // Terminal-window sign-in method is macOS-only — only codex's browser
  // method works, so the TUI providers keep the manual sign-in line.
  const signinHelpOn = (id: string) =>
    agentInstallOn && (platformOs !== 'linux' || id === 'codex')
  // §9 per-OS copy rule: the machine noun and the §1 secret-store promise.
  const copy = usePlatformCopy()
  const SUG = suggestions(copy.machine)

  const [, bump] = useReducer((n: number) => n + 1, 0)
  const obRef = useRef<Ob | null>(null)
  if (!obRef.current) obRef.current = freshOb()
  const ob = obRef.current

  const timers = useRef<number[]>([])
  const ivals = useRef<number[]>([])
  // §10 sign-in poll, one interval per provider (see pollSignin below)
  const signinPolls = useRef<Record<string, number>>({})
  const t = (fn: () => void, ms: number) => { const id = window.setTimeout(fn, ms); timers.current.push(id); return id }
  const iv = (fn: () => void, ms: number) => { const id = window.setInterval(fn, ms); ivals.current.push(id); return id }
  const up = (fn: (o: Ob) => void) => { fn(ob); bump() }

  useEffect(() => () => {
    timers.current.forEach((id) => clearTimeout(id))
    ivals.current.forEach((id) => clearInterval(id))
    signinPolls.current = {}
  }, [])

  // ----- step 1: live self-check (prototype runSample timings) -----
  useEffect(() => {
    if (ob.phase !== 'welcome' || ob.smStarted) return
    ob.smStarted = true
    t(() => up((o) => { o.smSteps[0].status = 'executing' }), 500)
    t(() => up((o) => { o.smSteps[0].status = 'done'; o.smSteps[0].duration = '1.1s'; o.smSteps[1].status = 'executing' }), 1700)
    t(() => up((o) => { o.smSteps[1].status = 'done'; o.smSteps[1].duration = '1.4s'; o.smSteps[2].status = 'executing' }), 3100)
    const finish = () => {
      // Real verification: the store booted against the backend before this
      // surface rendered — only report "ready" once that connection is live.
      if (useStore.getState().connected === true) {
        up((o) => { o.smSteps[2].status = 'done'; o.smSteps[2].duration = '0.8s'; o.smShowResult = true })
        t(() => up((o) => { o.smDone = true }), 550)
      } else {
        t(finish, 300)
      }
    }
    t(finish, 3950)
    bump()
    // Re-arm after StrictMode's dev remount (the timer-clearing cleanup above
    // fires between the two effect passes, so the guard must reset with it).
    return () => { ob.smStarted = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ----- step 2: detection (real api.detectAgents, ≥1.9 s spinner as designed) -----
  // Detection covers the four harnesses; the Free local AI card's pieces come
  // from /ollama/status alongside (§10).
  const startDetect = () => {
    if (ob.detStarted) return
    up((o) => { o.detStarted = true; o.det = 'searching' })
    const started = Date.now()
    void Promise.all([
      api.detectAgents().catch(() => null),
      api.ollamaStatus().catch(() => null),
    ]).then(([provs, ost]) => {
      if (provs === null) {
        // §10: a failed detect (backend restarting) must not render as
        // "No AI app was found" with the suggestion cards missing — stay
        // on the searching state and retry until the backend answers.
        t(() => { ob.detStarted = false; startDetect() }, 1500)
        return
      }
      // §10: any installed model counts — the first one becomes the card's
      // model; qwen3:8b downloads only when none is installed.
      const localModel = ost?.models[0] ?? null
      const localSt = {
        opencode: provs.some((p) => p.id === 'opencode' && p.installed),
        ollama: ost?.ready ?? false,
        model: localModel !== null,
      }
      const wait = Math.max(0, 1900 - (Date.now() - started))
      t(() => {
        up((o) => {
          o.det = 'cards'; o.provs = provs; o.localSt = localSt; o.localModel = localModel
          o.localFound = localSt.opencode && localSt.ollama && localSt.model
        })
        // §10: connection checks run on their own — signed-out providers
        // skip it (the check would fail) and show Sign in directly.
        provs.filter((p) => p.installed && p.signedIn !== false)
          .forEach((p) => startCheck(p))
        // Local card: every piece already present → straight to the check.
        if (localSt.opencode && localSt.ollama && localSt.model) startLocalCheck()
      }, wait)
    })
  }

  useEffect(() => {
    if (ob.phase === 'connect') startDetect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ob.phase])

  // ----- per-provider card machine (§10) -----
  const card = (id: string): Card => ob.cards[id] ?? freshCard()
  const setCard = (id: string, patch: Partial<Card>) =>
    up((o) => { o.cards = { ...o.cards, [id]: { ...card(id), ...patch } } })

  // Found-card "Check connection": the real §4.7 readiness check (§19
  // /agents/check-harness), padded to ≥900 ms so the spinner reads.
  const startCheck = (p: Det) => {
    setCard(p.id, { phase: 'checking', notReady: null })
    const t0 = Date.now()
    void api.checkHarness(p.name, null)
      .then((r) => r.status === 'ready')
      .catch(() => false)
      .then((ready) => {
        t(() => {
          if (card(p.id).phase !== 'checking') return
          if (ready) setCard(p.id, { phase: 'connected' })
          else setCard(p.id, { phase: 'idle', notReady: 'Not ready — it didn’t answer the readiness check.' })
        }, Math.max(0, 900 - (Date.now() - t0)))
      })
  }

  // Free local AI card check (§10): OpenCode with the card's model — the §19
  // check needs no sign-in, only the binary + Ollama serving + the model.
  const startLocalCheck = () => {
    const model = ob.localModel ?? LOCAL_MODEL
    setCard(LOCAL_ID, { phase: 'checking', notReady: null })
    const t0 = Date.now()
    void api.checkHarness('OpenCode', model, 'ollama')
      .then((r) => r.status === 'ready')
      .catch(() => false)
      .then(async (ready) => {
        let reason = 'it didn’t answer the readiness check'
        if (!ready) {
          const st = await api.ollamaStatus().catch(() => null)
          if (st && !st.ready) reason = 'the local server isn’t answering'
          else if (st && !st.models.includes(model)) reason = `${model} isn’t installed yet`
        }
        t(() => {
          if (card(LOCAL_ID).phase !== 'checking') return
          if (ready) setCard(LOCAL_ID, { phase: 'connected' })
          else setCard(LOCAL_ID, { phase: 'idle', notReady: `Not ready — ${reason}.` })
        }, Math.max(0, 900 - (Date.now() - t0)))
      })
  }

  // Sign-in help, only when necessary (§10): the backend opens the browser
  // (Codex) or Terminal (the rest); we poll until the sign-in rule flips.
  const pollSignin = (p: Det) => {
    // One poll per provider: a second sign-in attempt replaces the first, and
    // a phase that leaves sign-in stops it — otherwise every retry would leave
    // another 2 s fetch loop running for the life of the page.
    clearInterval(signinPolls.current[p.id])
    signinPolls.current[p.id] = iv(() => {
      if (card(p.id).phase !== 'signin') {
        clearInterval(signinPolls.current[p.id])
        delete signinPolls.current[p.id]
        return
      }
      void api.signinStatus(p.id).then((s) => {
        if (s.signedIn === true && card(p.id).phase === 'signin') startCheck(p)
      }).catch(() => { /* backend hiccup — keep polling */ })
    }, 2000)
  }
  const startSignin = (p: Det) => {
    void api.loginHarness(p.id)
      .then((r) => { setCard(p.id, { phase: 'signin', method: r.method }); pollSignin(p) })
      .catch((e: Error) => {
        if (e.message.includes('already signed in')) startCheck(p)
        else showToast(e.message)
      })
  }

  // Suggestion-card install: real backend install (§19 POST /agents/install);
  // progress arrives via the harness.install effect below.
  const startInstall = (p: Det) => {
    setCard(p.id, { phase: 'installing', percent: null, line: '', error: null })
    // A previous attempt's terminal harness.install event may still sit in the
    // store — clear it or the effect below would instantly fail this retry.
    useStore.setState((s) => ({ harnessInstall: Object.fromEntries(Object.entries(s.harnessInstall).filter(([k]) => k !== p.id)) }))
    api.installHarness(p.id).catch((e: Error & { status?: number }) => {
      // 409 = already running (a resumed machine) — the stream keeps feeding us.
      if (e.status !== 409) setCard(p.id, { phase: 'failed', error: e.message })
    })
  }

  // §10 model download completion: the model appearing in the installed list
  // is the source of truth (percent comes from the ollama.pull effect below).
  const pollPull = () => {
    iv(() => {
      if (card(LOCAL_ID).phase !== 'pulling') return
      void api.ollamaStatus().then((s) => {
        if (s.models.includes(LOCAL_MODEL) && card(LOCAL_ID).phase === 'pulling') {
          markLocalPiece('model')
          startLocalCheck()
        }
      }).catch(() => { /* keep polling */ })
    }, 2000)
  }

  // After a finished harness install, sign-in help only if it's needed (§10).
  const afterInstall = (id: string) => {
    const p = ob.provs.find((x) => x.id === id)
    if (!p || card(id).phase !== 'installing') return
    setCard(id, { phase: 'checking' })
    void api.signinStatus(id)
      // No sign-in help for this provider here (§9 Linux nuance): the check
      // reports the signed-out state and the card offers the manual line.
      .then((s) => { if (s.signedIn === false && signinHelpOn(id)) startSignin(p); else startCheck(p) })
      .catch(() => startCheck(p))
  }

  // ----- Free local AI card machine (§10): install the missing pieces in
  // order — OpenCode, Ollama, the model — then the connection check.
  const markLocalPiece = (piece: LocalPiece) =>
    up((o) => { if (o.localSt) o.localSt = { ...o.localSt, [piece]: true } })

  const localMissing = (): LocalPiece[] => {
    const l = ob.localSt
    const out: LocalPiece[] = []
    if (!l?.opencode) out.push('opencode')
    if (!l?.ollama) out.push('ollama')
    if (!l?.model) out.push('model')
    return out
  }

  const runLocalPiece = (queue: LocalPiece[], qi: number) => {
    const piece = queue[qi]
    if (!piece) { startLocalCheck(); return }
    if (piece === 'model') {
      setCard(LOCAL_ID, { phase: 'pulling', pullPct: 0, line: '', queue, qi })
      // A previous attempt's terminal ollama.pull event may still sit in the
      // store — clear it or the failure effect would instantly kill this pull.
      useStore.setState({ ollamaPull: null })
      void api.ollamaPull(LOCAL_MODEL).catch((e: Error) => setCard(LOCAL_ID, { phase: 'failed', error: e.message }))
      pollPull()
      return
    }
    setCard(LOCAL_ID, { phase: 'installing', percent: null, line: '', error: null, queue, qi })
    // Same stale-terminal-event guard as startInstall.
    useStore.setState((s) => ({ harnessInstall: Object.fromEntries(Object.entries(s.harnessInstall).filter(([k]) => k !== piece)) }))
    api.installHarness(piece).catch((e: Error & { status?: number }) => {
      // 409 = already running (a resumed machine) — the stream keeps feeding us.
      if (e.status !== 409) setCard(LOCAL_ID, { phase: 'failed', error: e.message })
    })
  }

  // "Try again" resumes here too: only the still-missing pieces re-run (§10).
  const startLocalSetup = () => {
    const missing = localMissing()
    if (missing.length === 0) { startLocalCheck(); return }
    runLocalPiece(missing, 0)
  }

  // §10 recovery: a found model that fails the check (e.g. embedding-only)
  // gets discarded, and qwen3:8b downloads in its place.
  const startQwenFallback = () => {
    up((o) => { o.localModel = null; if (o.localSt) o.localSt = { ...o.localSt, model: false } })
    runLocalPiece(['model'], 0)
  }

  // Live install progress from the §19 harness.install WS stream — feeds both
  // the harness suggestion cards and the local card's current piece.
  useEffect(() => {
    for (const [id, evt] of Object.entries(harnessInstall)) {
      const c = ob.cards[id]
      if (c && c.phase === 'installing') {
        if (!evt.done) {
          if (evt.line !== undefined || evt.percent !== undefined) {
            setCard(id, { line: evt.line ?? c.line, percent: evt.percent ?? c.percent })
          }
        } else if (evt.ok) {
          afterInstall(id)
        } else {
          setCard(id, { phase: 'failed', error: evt.error ?? 'install failed' })
        }
      }
      const lc = ob.cards[LOCAL_ID]
      if (lc && lc.phase === 'installing' && lc.queue[lc.qi] === id) {
        if (!evt.done) {
          if (evt.line !== undefined || evt.percent !== undefined) {
            setCard(LOCAL_ID, { line: evt.line ?? lc.line, percent: evt.percent ?? lc.percent })
          }
        } else if (evt.ok) {
          markLocalPiece(id as LocalPiece)
          runLocalPiece(lc.queue, lc.qi + 1)
        } else {
          setCard(LOCAL_ID, { phase: 'failed', error: evt.error ?? 'install failed' })
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [harnessInstall])

  // Live model-download percent / failure from the ollama.pull WS stream.
  useEffect(() => {
    const c = ob.cards[LOCAL_ID]
    if (!c || c.phase !== 'pulling' || !ollamaPull || ollamaPull.model !== LOCAL_MODEL) return
    if (ollamaPull.done && ollamaPull.ok === false) {
      setCard(LOCAL_ID, { phase: 'failed', error: ollamaPull.line || `couldn't pull ${LOCAL_MODEL}` })
      return
    }
    // §19: percent is the backend's single overall pull percent (byte-weighted
    // across layers, monotonic) — never parsed out of the raw line, whose own
    // numbers reset 0–100 per layer.
    setCard(LOCAL_ID, {
      line: ollamaPull.line || card(LOCAL_ID).line,
      ...(ollamaPull.percent !== undefined ? { pullPct: Math.min(100, ollamaPull.percent) } : {}),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ollamaPull])

  // ----- derived (prototype obVals) -----
  const agentPre = agents.length > 0
  const autoPre = automations.length > 0
  // With prior data (agents or automations), step 1 still shows but Continue
  // goes straight to the app instead of step 2.
  const pre = agentPre || autoPre

  // ----- commit connected cards as real agent records -----
  // `pick` is the card whose in-card Continue was clicked (null on skip);
  // it becomes the default agent. All connected cards are committed (§10):
  // a harness card as a default-mode agent, the local card as OpenCode
  // driving Qwen3 8B through Ollama.
  const commitOnboardAgents = async (pick: string | null): Promise<void> => {
    const connection = ob.provs
      .filter((p) => card(p.id).phase === 'connected')
      .map((p) => ({ id: p.id, body: { name: null as string | null, harness: p.name, mode: 'default', model: null as string | null } }))
    if (card(LOCAL_ID).phase === 'connected') {
      // Null name → display falls back to the harness, so the agent reads
      // "OpenCode · <model>" (§10), never the model name twice.
      connection.push({ id: LOCAL_ID, body: { name: null, harness: 'OpenCode', mode: 'ollama', model: ob.localModel ?? LOCAL_MODEL } })
    }
    if (connection.length === 0) return
    const defPid = pick ?? connection[0].id
    // §10 idempotent commit: each card resolves by its §4.7 effective grant
    // name (name, else harness — case-insensitive) against the backend's LIVE
    // agents — a partially-landed earlier commit leaves agents the store
    // snapshot may not carry yet, and re-posting one 422s. The picked card
    // goes first so a within-commit clash (the plain OpenCode card and the
    // local card are both grant-named "OpenCode") resolves to the pick.
    const grantKey = (a: { name: string | null; harness: string }) =>
      (a.name ?? a.harness).trim().toLowerCase()
    const byGrant = new Map((await api.listAgents()).map((a) => [grantKey(a), a.id]))
    const ordered = connection.filter((c) => c.id === defPid)
      .concat(connection.filter((c) => c.id !== defPid))
    let defaultId: string | null = null
    for (const { id: cid, body } of ordered) {
      let id = byGrant.get(grantKey(body))
      if (!id) {
        try {
          id = (await api.addAgent(body)).id
        } catch (e) {
          // §4.7 already-exists 422 (an agent of this name landed mid-commit)
          // means reuse, never failure (§10).
          const err = e as Error & { status?: number }
          if (err.status !== 422 || !err.message.includes('already exists')) throw e
          const landed = (await api.listAgents()).find((a) => grantKey(a) === grantKey(body))
          if (!landed) throw e
          id = landed.id
        }
        byGrant.set(grantKey(body), id)
      }
      if (cid === defPid) defaultId = id
    }
    if (defaultId) {
      await api.patchAgent(defaultId, { default: true })
      // §10: every seed automation gets the chosen default agent.
      const allAutos = useStore.getState().automations
      await Promise.all(
        allAutos.filter((a) => a.agentId !== defaultId)
          .map((a) => api.patchAutomation(a.id, { agentId: defaultId })),
      )
    }
  }

  // ----- navigation -----
  const obToConnect = () => {
    if (pre) { setSurface('app'); return }
    up((o) => { o.phase = 'connect' })
  }
  const obContinue = (pick: string) => {
    if (ob.committing) return
    up((o) => { o.chosen = pick; o.committing = true })
    void (async () => {
      try {
        await commitOnboardAgents(pick)
      } catch (e) {
        showToast((e as Error).message)
        up((o) => { o.committing = false })
        return
      }
      // The store only hears about the new agents via the async agents.changed
      // refresh — pull state now so the app mounts seeing the picked default.
      await useStore.getState().refresh().catch(() => { /* WS refresh still lands */ })
      setSurface('app')
    })()
  }
  const obSkip = () => {
    if (ob.committing) return
    up((o) => { o.committing = true })
    void (async () => {
      try {
        await commitOnboardAgents(null)
      } catch (e) {
        showToast((e as Error).message)
      }
      await useStore.getState().refresh().catch(() => { /* WS refresh still lands */ })
      setSurface('app')
    })()
  }

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      background: 'var(--bg-content)',
    }}>
      {/* §9: on Windows this header doubles as the shell's title bar — same
          --bg-titlebar/hairline treatment and height as the app shell's bar,
          so the OS titleBarOverlay cluster blends into it. */}
      <div className="ad-drag" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 18, flex: 'none',
        ...(platformOs === 'windows'
          // 41 = 40px bar + the hairline below the titleBarOverlay (§9).
          ? { height: 41, padding: '0 28px', background: 'var(--bg-titlebar)', borderBottom: '1px solid var(--hairline)' }
          : { padding: '13px 28px' }),
      }}>
        {/* §10: with prior data step 1 is the only screen — no counter. */}
        {!pre && (
          <div style={{ fontFamily: 'var(--mono)', fontWeight: 500, fontSize: 11, color: 'var(--text-faint)' }}>
            {ob.phase === 'welcome' ? 'Step 1 of 2' : 'Step 2 of 2'}
          </div>
        )}
      </div>

      <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
        {ob.phase === 'welcome' ? renderWelcome() : renderConnect()}
      </ScrollArea>

      <div style={{ flex: 'none', borderTop: '1px solid var(--hairline)', padding: '13px 28px', display: 'flex', justifyContent: 'center', gap: 26, flexWrap: 'wrap' }}>
        {[`Your automations execute only on this ${copy.machine}`, `Secrets live in your ${copy.secretStore}`].map((p) => (
          <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--green)' }} />
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{p}</span>
          </div>
        ))}
      </div>
    </div>
  )

  // ---------- step 1 ----------
  function renderWelcome() {
    const stepDot = (s: Ob['smSteps'][number]) =>
      s.status === 'executing' ? { dot: 'var(--cyan)', anim: PULSE, c: 'var(--text)' }
      : s.status === 'done' ? { dot: 'var(--green)', anim: 'none', c: 'var(--text-2)' }
      : { dot: 'var(--text-deco)', anim: 'none', c: 'var(--text-faint)' }
    const chips = ['Settings created', 'Folders in place']
    if (agentPre) chips.push('Agent found')
    if (autoPre) chips.push('Automations found')
    const para = autoPre
      ? 'Autowright created fresh settings and folders, and found your existing automations. You’re ready to go.'
      : agentPre
      ? 'Autowright created fresh settings and folders, and found an AI already connected. You’re ready to go.'
      : 'Autowright created fresh settings and folders, and everything is loaded. You’re ready to go.'
    const nextPara = autoPre
      ? 'Setup only happens once. Your automations are already here, so you can go straight to them.'
      : agentPre
      ? 'Setup only happens once. Your AI is already connected, so you can go straight to creating automations.'
      : 'Setup only happens once. Next, connect your AI so you can create your own automations.'
    const nextLabel = pre ? 'Continue →' : 'Connect your AI →'

    return (
      <div className="ad-anim-page" style={{ maxWidth: 720, margin: '0 auto', padding: '30px 32px 60px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
          <Logo size={32} />
          <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: '-.01em' }}>Autowright</span>
        </div>
        <h1 style={{ fontWeight: 600, fontSize: 26, lineHeight: 1.25, letterSpacing: '-.02em', margin: '0 0 12px' }}>
          Recurring jobs, done exactly the same way every time.
        </h1>
        <p style={{ fontSize: 15, lineHeight: 1.6, color: 'var(--text-muted)', margin: '0 0 28px' }}>
          Describe a job in plain words. Your AI writes it as scripts you can read. Autowright executes them on your schedule and shows you the result.
        </p>

        <div className="ad-card" style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid var(--hairline)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontWeight: 600, fontSize: 15 }}>Getting Autowright ready</span>
            </div>
          </div>
          <div style={{ padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {ob.smSteps.map((s) => {
              const d = stepDot(s)
              return (
                <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: d.dot, animation: d.anim, flex: 'none' }} />
                  <span style={{ flex: 1, fontSize: 13, color: d.c }}>{s.name}</span>
                  <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-faint)' }}>{s.duration}</span>
                </div>
              )
            })}
          </div>
          {ob.smShowResult && (
            <div className="ad-anim-item" style={{ borderTop: '1px solid var(--hairline-dim)', background: 'var(--bg-inset)', padding: '16px 18px' }}>
              <Eyebrow style={{ marginBottom: 10 }}>READY</Eyebrow>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                <MiniBadge c="var(--green)" bg="var(--green-bg)">All set</MiniBadge>
                {chips.map((ch) => (
                  <MiniBadge key={ch}>{ch}</MiniBadge>
                ))}
              </div>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: 'var(--text-2)' }}>{para}</p>
            </div>
          )}
        </div>

        <p style={{ fontSize: 13.5, lineHeight: 1.5, color: 'var(--text-muted)', margin: '20px 0 16px' }}>{nextPara}</p>
        {ob.smDone
          ? <button className="ad-btn-primary ad-anim-item" onClick={obToConnect}>{nextLabel}</button>
          : <span style={{ fontFamily: 'var(--mono)', fontWeight: 500, fontSize: 11.5, color: 'var(--text-faint)' }}>Setting things up…</span>}
      </div>
    )
  }

  // ---------- step 2 ----------
  function renderConnect() {
    const foundList = ob.provs.filter((p) => p.installed)
    const localDet: Det = { id: LOCAL_ID, name: 'Free local AI', installed: ob.localFound, signedIn: null, detail: '' }
    // Suggestions: every missing harness, then the Free local AI card —
    // unless every local piece was found, which moves it to the found
    // section (§10). localFound implies OpenCode installed, so the found
    // section always exists when the card lands there.
    const sugList = SUG_ORDER
      .map((id) => ob.provs.find((p) => p.id === id && !p.installed))
      .filter((p): p is Det => !!p)
      .concat(ob.localFound ? [] : [localDet])
    // Found-section status line covers the local card too when it sits there.
    const foundPhases = foundList.map((f) => card(f.id).phase)
      .concat(ob.localFound ? [card(LOCAL_ID).phase] : [])

    return (
      <div className="ad-anim-page" style={{ maxWidth: 720, margin: '0 auto', padding: '30px 32px 60px' }}>
        <h1 style={{ fontWeight: 600, fontSize: 26, lineHeight: 1.25, letterSpacing: '-.02em', margin: '0 0 10px' }}>Connect your AI</h1>
        <p style={{ fontSize: 15, lineHeight: 1.6, color: 'var(--text-muted)', margin: '0 0 26px' }}>
          The AI only writes the scripts — Autowright executes them.
        </p>

        {ob.det === 'searching' && (
          <div className="ad-card">
            <LoadingRow
              label={`Looking for an AI already on this ${copy.machine}…`}
              style={{ padding: '16px 18px' }}
            />
          </div>
        )}

        {ob.det === 'cards' && (
          <>
            {foundList.length > 0 && (
              <div className="ad-anim-item" style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 26 }}>
                <Eyebrow style={{ color: 'var(--accent)' }}>FOUND ON THIS {copy.machine.toUpperCase()}</Eyebrow>
                {foundList.map((f) => renderFoundCard(f))}
                {ob.localFound && renderSuggestionCard(localDet)}
                {foundPhases.every((ph) => ph !== 'checking') && (
                  foundPhases.some((ph) => ph === 'connected') ? (
                    <div className="ad-anim-item" style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                      You’re ready — pick a connected AI as your default, or set up another below.
                    </div>
                  ) : (
                    <div className="ad-anim-item" style={{ fontSize: 13, color: 'var(--amber)' }}>
                      More setup needed — finish the steps above before continuing.
                    </div>
                  )
                )}
              </div>
            )}

            {foundList.length === 0 && (
              <div className="ad-anim-item ad-card" style={{
                padding: '12px 18px',
                marginBottom: 18, display: 'flex', alignItems: 'center', gap: 10,
              }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--text-faint)', flex: 'none' }} />
                <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>No AI app was found on this {copy.machine} — here are some suggestions for moving forward.</span>
              </div>
            )}

            {sugList.length > 0 && (
              <div className="ad-anim-item">
                {foundList.length > 0 && (
                  <button
                    className="ad-btn-text small"
                    onClick={() => up((o) => { o.sugOpen = !o.sugOpen })}
                    aria-expanded={ob.sugOpen}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 7, marginBottom: ob.sugOpen ? 12 : 0,
                    }}
                  >
                    <Eyebrow>OR TRY SOMETHING NEW</Eyebrow>
                    <Caret open={ob.sugOpen} openDeg={180} closedDeg={0} style={{ color: 'var(--text-faint)' }} />
                  </button>
                )}
                {(foundList.length === 0 || ob.sugOpen) && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {sugList.map((p) => renderSuggestionCard(p))}
                  </div>
                )}
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 18, marginTop: 24 }}>
              <button
                className="ad-btn-text dim"
                onClick={obSkip}
                disabled={ob.committing}
              >
                Skip for now
              </button>
            </div>
          </>
        )}
      </div>
    )
  }

  // Amber "waiting for you to sign in" block, shared by found and suggestion
  // cards; copy follows the §19 login method the backend reported.
  function renderSigninWait(p: Det) {
    const c = card(p.id)
    const where = c.method === 'browser'
      ? 'We opened your browser — sign in there and come back. We’ll notice on our own.'
      : 'We opened Terminal — finish signing in there and come back. We’ll notice on our own.'
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%', background: 'var(--amber)',
            animation: PULSE, flex: 'none', marginTop: 5,
          }} />
          <div>
            <div style={{ fontWeight: 500, fontSize: 13 }}>Waiting for you to sign in…</div>
            <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)', marginTop: 2 }}>{where}</div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, paddingLeft: 16 }}>
          <button className="ad-btn-text dim" onClick={() => setCard(p.id, { phase: 'idle', method: null })}>Cancel</button>
        </div>
      </div>
    )
  }

  function renderFoundCard(f: Det) {
    const c = card(f.id)
    const connection = c.phase === 'connected'
    return (
      <div
        key={f.id}
        data-testid="onboard-agent-card"
        className="ad-card"
        style={{ padding: 18, display: 'flex', alignItems: 'center', gap: 16 }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>{f.name}</div>
          <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)', marginTop: 3 }}>{f.detail}</div>
          {c.phase === 'idle' && c.notReady && (
            <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--amber)', marginTop: 4 }}>{c.notReady}</div>
          )}
        </div>
        {c.phase === 'idle' && (f.signedIn === false ? (
          // §2 `agentInstall`: the sign-in help opens Terminal, so where the
          // flag is false — or the §9 Linux nuance rules this provider out —
          // the action is replaced by the plain line (§9).
          signinHelpOn(f.id) ? (
            <button className="ad-btn-amber" onClick={() => startSignin(f)} style={{ flex: 'none' }}>
              Sign in
            </button>
          ) : (
            <ManualInstallLine id={f.id} name={f.name} signin style={{ flex: 'none', maxWidth: 340 }} />
          )
        ) : (
          <button className="ad-btn-ghost" onClick={() => startCheck(f)} style={{ flex: 'none' }}>
            Check again
          </button>
        ))}
        {c.phase === 'checking' && (
          <LoadingRow label="Checking connection…" style={{ flex: 'none' }} />
        )}
        {c.phase === 'signin' && (
          <div style={{ flex: 'none', maxWidth: 340 }}>{renderSigninWait(f)}</div>
        )}
        {connection && (
          <button
            className="ad-btn-primary ad-anim-item"
            onClick={() => obContinue(f.id)}
            disabled={ob.committing}
            style={{ flex: 'none' }}
          >
            {ob.committing && ob.chosen === f.id ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <Spinner size={13} /> Setting up…
              </span>
            ) : CONTINUE_LABEL}
          </button>
        )}
      </div>
    )
  }

  function renderSuggestionCard(p: Det) {
    const c = card(p.id)
    const s = SUG[p.id]
    const isLocal = p.id === LOCAL_ID
    // §10: a found model replaces the qwen3:8b download pitch — nothing to
    // download beyond the missing pieces. In the found section (every piece
    // present) the body drops the setup framing entirely.
    const found = isLocal ? ob.localModel : null
    const body = isLocal && ob.localFound
      ? `OpenCode with Ollama and ${found ?? LOCAL_MODEL} — local to this ${copy.machine}, works offline. ${LOCAL_FIT}`
      : found ? `Sets up OpenCode with Ollama and ${found}, already on this ${copy.machine}. Works offline. ${LOCAL_FIT}` : s.body
    const btn = found ? 'Set up local AI' : s.btn
    const connection = c.phase === 'connected'
    const busy = c.phase === 'installing' || c.phase === 'pulling' || c.phase === 'signin' || c.phase === 'failed'
    const start = () => { if (isLocal) startLocalSetup(); else startInstall(p) }
    // §2 `agentInstall`: the card's setup button runs §19 /agents/install (and
    // the sign-in help behind it), so it is hidden where the flag is false —
    // the manual-install line names the provider it would have installed. The
    // local card only needs it while a piece is a real install: a missing
    // model alone is an /ollama/pull, which this capability doesn't gate.
    const manualId = isLocal ? localMissing().find((piece) => piece !== 'model') ?? null : p.id
    const manual = !agentInstallOn && manualId !== null
    return (
      <div
        key={p.id}
        className="ad-card"
        style={{ padding: 18 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 15 }}>{s.title}</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)', marginTop: 3 }}>{body}</div>
            {c.phase === 'idle' && c.notReady && (
              <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--amber)', marginTop: 4 }}>{c.notReady}</div>
            )}
          </div>
          {c.phase === 'idle' && (isLocal && c.notReady ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 'none' }}>
              <button className="ad-btn-ghost" onClick={() => startLocalCheck()}>
                Check again
              </button>
              {found && (
                // §10 recovery: the found model failed the check — offer the
                // qwen3:8b download instead.
                <button className="ad-btn-ghost" onClick={startQwenFallback}>
                  Download Qwen3 8B · 5.2 GB
                </button>
              )}
            </div>
          ) : manual ? (
            <ManualInstallLine id={manualId as string} style={{ flex: 'none', maxWidth: 340 }} />
          ) : (
            <button
              className={s.primary ? 'ad-btn-primary' : 'ad-btn-ghost'}
              onClick={start}
              style={{ flex: 'none' }}
            >
              {btn}
            </button>
          ))}
          {c.phase === 'checking' && (
            <LoadingRow label="Checking connection…" style={{ flex: 'none' }} />
          )}
          {connection && (
            <button
              className="ad-btn-primary ad-anim-item"
              onClick={() => obContinue(p.id)}
              disabled={ob.committing}
              style={{ flex: 'none' }}
            >
              {ob.committing && ob.chosen === p.id ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                  <Spinner size={13} /> Setting up…
                </span>
              ) : CONTINUE_LABEL}
            </button>
          )}
        </div>
        {busy && (
          <div style={{ marginTop: 14 }}>
            {c.phase === 'installing' && (
              <div>
                <div style={{ fontWeight: 500, fontSize: 12.5, color: 'var(--text-2)', marginBottom: 8 }}>
                  {isLocal
                    ? `Step ${c.qi + 1} of ${c.queue.length} — Installing ${c.queue[c.qi] === 'opencode' ? 'OpenCode' : 'Ollama'}…`
                    : `Installing ${p.name}…`}{' '}
                  {c.percent !== null && (
                    <span style={{ fontFamily: 'var(--mono)', fontWeight: 500, fontSize: 12, color: 'var(--text-muted)' }}>{Math.round(c.percent)}%</span>
                  )}
                </div>
                <ProgressBar percent={c.percent} />
                {c.line && (
                  <div style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-faint)', marginTop: 7, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {c.line}
                  </div>
                )}
              </div>
            )}
            {c.phase === 'pulling' && (
              <div>
                <div style={{ fontWeight: 500, fontSize: 12.5, color: 'var(--text-2)', marginBottom: 8 }}>
                  Step {c.qi + 1} of {c.queue.length} — Downloading Qwen3 8B…{' '}
                  <span style={{ fontFamily: 'var(--mono)', fontWeight: 500, fontSize: 12, color: 'var(--text-muted)' }}>
                    {(c.pullPct / 100 * 5.2).toFixed(1)} GB of 5.2 GB
                  </span>
                </div>
                <ProgressBar percent={c.pullPct} />
                {c.line && (
                  <div style={{ fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-faint)', marginTop: 7, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {c.line}
                  </div>
                )}
                <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)', marginTop: 8 }}>
                  Ollama is installed. You can keep using your {copy.machine} — this finishes in the background.
                </div>
              </div>
            )}
            {c.phase === 'signin' && renderSigninWait(p)}
            {c.phase === 'failed' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--red-text)' }}>
                  Install failed — {c.error ?? 'something went wrong'}
                </div>
                <button className="ad-btn-ghost" onClick={start} style={{ alignSelf: 'flex-start' }}>
                  Try again
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }
}
