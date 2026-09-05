// Shared step-list / param-editor module (§17): one StepList renders the
// read-only step rows and the §9.2 step-script modal on the §11 create/edit
// flow ('editor' variant — agent warning colors, package facts) and the §9.2
// automation detail page ('detail' variant); both draw the same §14 list
// row. One presentational ParamValueEditor renders
// the five §4.2 value kinds (toggle/list/kv/number/text) for both the
// editor's test-value card and the detail page's debounced ParamRow.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { usePlatformCopy } from './platformCopy'
import type { Agent, PackageDep, ParamDef, SecretMeta, Step, UnresolvedRefs } from './types'
import { Eyebrow, MiniBadge, Modal, ScrollArea, Tag, Toggle, agName, dispModel, highlightPythonLines, stepRetriesLabel, stepRetriesTitle, stepTimeoutLabel, stepTimeoutTitle, validUrl } from './ui'

// §4.1/§6.1 code-reference scan: literal quoted uuid subscripts only.
const SECRET_REF_RE = /\bsecrets\[\s*["']([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})["']\s*\]/g
export const shortId = (id: string) => `${id.slice(0, 8)}…`

// A step's secrets are its declared `secrets` entry ids unioned with the
// literal secrets["<id>"] references in its code (§4.1). Tags carry the
// declared entry's per-use `why`; a code-referenced id with no entry has none.
// Display resolves ids to LIVE names; a dangling id renders the red deleted
// state (missing: true) — under the archive record's NAME when the
// automation's §4.1 unresolvedReferences carries the id (imported: true),
// else under its short id prefix.
export function stepSecretTags(s: Step, secrets: SecretMeta[], unresolved?: UnresolvedRefs):
    { id: string; name: string; missing: boolean; imported?: boolean; why?: string }[] {
  const resolve = (id: string, why?: string) => {
    const sec = secrets.find((z) => z.id === id)
    const un = !sec && unresolved?.[id]?.kind === 'secret' ? unresolved[id] : null
    return {
      id, name: sec ? sec.name : un ? un.name : shortId(id), missing: !sec,
      ...(un ? { imported: true } : {}), ...(why ? { why } : {}),
    }
  }
  const tags = (s.secrets ?? []).map((e) => resolve(e.id, e.why))
  for (const m of (s.code || '').matchAll(SECRET_REF_RE)) {
    if (!tags.some((t) => t.id === m[1])) tags.push(resolve(m[1]))
  }
  return tags
}
// The step's referenced secret ids — declared entries plus code references.
export function stepSecretIds(s: Step): string[] {
  const ids = (s.secrets ?? []).map((e) => e.id)
  for (const m of (s.code || '').matchAll(SECRET_REF_RE)) {
    if (!ids.includes(m[1])) ids.push(m[1])
  }
  return ids
}

// A step's packages are its declared `packages` entries unioned with the §6.2
// declared imports appearing in its code (§4.1). Tags carry the declared
// entry's per-step `why`, falling back to the package declaration's general
// `why` (§4.1); with neither, the tooltip drops its why clause.
export function stepPackageTags(s: Step, packages: PackageDep[]):
    { import: string; why?: string; version?: string }[] {
  const version = (imp: string) => packages.find((p) => p.import === imp)?.version
  const general = (imp: string) => packages.find((p) => p.import === imp)?.why
  const tags = (s.packages ?? []).map((e) => ({ ...e, why: e.why || general(e.import), version: version(e.import) }))
  for (const p of packages) {
    if (tags.some((t) => t.import === p.import)) continue
    if (new RegExp(`\\b(?:import|from)\\s+${p.import}\\b`).test(s.code || '')) {
      tags.push({ import: p.import, why: p.why, version: p.version })
    }
  }
  return tags
}

// ---------- §9.2 navigator facts (literal-only scans) ----------

const STR_SRC = String.raw`(?:[rbfuRBFU]{0,2})(?:'''[\s\S]*?'''|"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')`
const STR_RE = new RegExp(STR_SRC, 'g')
// A string token's contents: prefix and quotes stripped.
const unquote = (tok: string) => {
  const q = tok.match(/^[rbfuRBFU]{0,2}('''|"""|"|')/)
  if (!q) return tok
  return tok.slice(q[0].length, tok.length - q[1].length)
}
// A literal workspace-relative file name; absolute, home, parent-relative
// and interpolated (f-string) names are skipped.
const relFile = (tok: string) => {
  if (/^[rbuRBU]*[fF]/.test(tok)) return null
  const v = unquote(tok)
  return v && !/^[/~]|\.\./.test(v) && !/[{}]/.test(v) ? v : null
}

// Every distinct http(s) host literal, in order of appearance.
export function stepHosts(code: string): string[] {
  const out: string[] = []
  for (const m of code.matchAll(/https?:\/\/([A-Za-z0-9.-]+(?::\d+)?)/g)) {
    if (!out.includes(m[1])) out.push(m[1])
  }
  return out
}

// agent / agents["…"] .ask / .read / .write call sites: the first string
// literal inside each call's balanced parentheses is its prompt.
export function stepAgentPrompts(code: string): { count: number; prompts: string[] } {
  const prompts: string[] = []
  let count = 0
  for (const m of code.matchAll(/\bagents?(?:\[[^\]]*\])?\.(?:ask|read|write)\(/g)) {
    count++
    let depth = 1
    let i = m.index! + m[0].length
    const start = i
    while (i < code.length && depth > 0) {
      const ch = code[i]
      if (ch === '"' || ch === "'" || /[rbfuRBFU]/.test(ch)) {
        STR_RE.lastIndex = i
        const sm = STR_RE.exec(code)
        if (sm && sm.index === i) { i += sm[0].length; continue }
      }
      if (ch === '(' || ch === '[' || ch === '{') depth++
      else if (ch === ')' || ch === ']' || ch === '}') depth--
      i++
    }
    const inner = code.slice(start, depth === 0 ? i - 1 : i)
    const lit = inner.match(new RegExp(STR_SRC))
    if (lit) {
      // Adjacent literals concatenate, as in Python — a prompt split across
      // lines is quoted whole, never from its first piece alone.
      let text = unquote(lit[0])
      const next = new RegExp(String.raw`\s*(${STR_SRC})`, 'y')
      next.lastIndex = lit.index! + lit[0].length
      for (let nm = next.exec(inner); nm; nm = next.exec(inner)) text += unquote(nm[1])
      text = text.replace(/\s+/g, ' ').trim()
      if (!text) continue
      if (text.length <= 72) { prompts.push(text); continue }
      // cut at the last word boundary inside the limit, never mid-word
      const head = text.slice(0, 71)
      const cut = head.lastIndexOf(' ')
      prompts.push(`${(cut > 40 ? head.slice(0, cut) : head).trimEnd()}…`)
    }
  }
  return { count, prompts }
}

// Workspace-relative files the script opens, split by direction.
export function stepFiles(code: string): { reads: string[]; writes: string[] } {
  const reads: string[] = []
  const writes: string[] = []
  const add = (list: string[], f: string) => { if (!list.includes(f)) list.push(f) }
  for (const m of code.matchAll(new RegExp(String.raw`\bopen\(\s*(${STR_SRC})\s*(?:,\s*(?:mode\s*=\s*)?(${STR_SRC}))?`, 'g'))) {
    const f = relFile(m[1])
    if (!f) continue
    add(m[2] && /[wax]/.test(unquote(m[2])) ? writes : reads, f)
  }
  for (const m of code.matchAll(new RegExp(String.raw`\bPath\(\s*(${STR_SRC})\s*\)\s*\.\s*(write_text|write_bytes|read_text|read_bytes|open)\(`, 'g'))) {
    const f = relFile(m[1])
    if (!f) continue
    add(m[2].startsWith('write') ? writes : reads, f)
  }
  return { reads, writes }
}

// §6.1 memory.load / memory.save key literals.
export function stepMemory(code: string): { loads: string[]; saves: string[] } {
  const loads: string[] = []
  const saves: string[] = []
  for (const m of code.matchAll(new RegExp(String.raw`\bmemory\.(load|save)\(\s*(${STR_SRC})`, 'g'))) {
    const list = m[1] === 'load' ? loads : saves
    const k = unquote(m[2])
    if (k && !list.includes(k)) list.push(k)
  }
  return { loads, saves }
}

// §4.2 param name literals the script reads: params["<name>"] / params.get("<name>").
export function stepParams(code: string): string[] {
  const out: string[] = []
  for (const m of code.matchAll(new RegExp(String.raw`\bparams(?:\[\s*|\.get\(\s*)(${STR_SRC})`, 'g'))) {
    const k = unquote(m[1])
    if (k && !/[{}]/.test(k) && !out.includes(k)) out.push(k)
  }
  return out
}

// One stored revision's steps, for the change badge.
export type StepHistory = { version: number; steps: Step[] }

// §9.2 change badge: the step compared by NAME across the stored versions.
// `viewing` is the revision the step belongs to — a version number, or
// 'draft' for the §11 editor's unsaved draft over the newest stored version.
export function stepChange(step: Step, viewing: number | 'draft', history: StepHistory[]): string | null {
  if (!history.length) return null
  const byVersion = [...history].sort((a, b) => b.version - a.version)
  const find = (v: number) => byVersion.find((h) => h.version === v)
  const code = (s: Step) => (s.code || '').replace(/\n$/, '')
  const same = (a: Step, b: Step) => code(a) === code(b)
  let base: number
  if (viewing === 'draft') {
    base = byVersion[0].version
    const prev = find(base)!.steps.find((s) => s.name === step.name)
    if (!prev) return 'New in this draft'
    if (!same(prev, step)) return 'Changed in this draft'
  } else {
    base = viewing
    if (!find(base)) return null
  }
  // Walk back through identical predecessors; `earliest` is the oldest
  // version in the unbroken run of identical scripts.
  let earliest = base
  for (;;) {
    const prevVersion = byVersion.find((h) => h.version < earliest)?.version
    const prev = prevVersion === undefined ? undefined : find(prevVersion)!.steps.find((s) => s.name === step.name)
    if (prev && same(prev, step)) { earliest = prevVersion!; continue }
    if (earliest !== base) return `Unchanged since v${earliest}`
    return prev ? `Changed in v${base}` : `New in v${base}`
  }
}

// One §9.2 fact section: an eyebrow label over one bullet per item.
export type StepFactSection = { key: string; label: string; items: string[] }

// The ordered fact sections for one step (§9.2), with the file hand-offs
// resolved against the other steps; empty sections are dropped.
export function stepFacts(steps: Step[], i: number, viewing: number | 'draft' | undefined, history: StepHistory[] | undefined, params: ParamDef[] = []): StepFactSection[] {
  const step = steps[i]
  const code = step.code || ''
  const sections: StepFactSection[] = []
  const listWord = (xs: string[]) => xs.length === 1 ? xs[0] : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`
  sections.push({ key: 'params', label: 'PARAMETERS', items: stepParams(code).map((k) => params.find((p) => p.name === k)?.label || k) })
  sections.push({ key: 'hosts', label: 'WEBSITES', items: stepHosts(code) })
  const ag = stepAgentPrompts(code)
  const agentItems = ag.prompts.map((p) => `“${p}”`)
  const rest = ag.count - ag.prompts.length
  if (rest > 0) agentItems.push(`${rest} ${ag.prompts.length ? 'more ' : ''}call${rest === 1 ? '' : 's'}`)
  sections.push({ key: 'agent', label: 'ASKS THE AGENT', items: agentItems })
  const files = steps.map((s) => stepFiles(s.code || ''))
  const stepsWord = (ns: number[]) => `step${ns.length === 1 ? '' : 's'} ${listWord(ns.map(String))}`
  const fileItems: string[] = []
  for (const f of files[i].reads) {
    let producer: number | null = null
    for (let j = i - 1; j >= 0; j--) if (files[j].writes.includes(f)) { producer = j + 1; break }
    fileItems.push(producer ? `Reads ${f} from step ${producer}` : `Reads ${f}`)
  }
  for (const f of files[i].writes) {
    const consumers: number[] = []
    for (let j = i + 1; j < steps.length; j++) if (files[j].reads.includes(f)) consumers.push(j + 1)
    fileItems.push(consumers.length ? `Hands ${f} to ${stepsWord(consumers)}` : `Writes ${f}`)
  }
  sections.push({ key: 'files', label: 'FILES', items: fileItems })
  const mem = stepMemory(code)
  sections.push({ key: 'memory', label: 'MEMORY', items: [...mem.loads.map((k) => `Reads ${k}`), ...mem.saves.map((k) => `Saves ${k}`)] })
  const change = viewing !== undefined && history ? stepChange(step, viewing, history) : null
  sections.push({ key: 'version', label: 'VERSION', items: change ? [change] : [] })
  return sections.filter((sec) => sec.items.length > 0)
}

// ---------- §9.2 find in script ----------

const lineTextOf = (nodes: React.ReactNode[]) => nodes.map((n) =>
  typeof n === 'string' ? n : React.isValidElement<{ children?: string }>(n) ? String(n.props.children ?? '') : '').join('')

export type CodeMatch = { line: number; start: number; end: number }

// Case-insensitive substring matches over the rendered lines, in document order.
export function findInLines(lines: React.ReactNode[][], query: string): CodeMatch[] {
  const q = query.toLowerCase()
  if (!q) return []
  const out: CodeMatch[] = []
  lines.forEach((ln, line) => {
    const t = lineTextOf(ln).toLowerCase()
    for (let at = t.indexOf(q); at !== -1; at = t.indexOf(q, at + q.length)) out.push({ line, start: at, end: at + q.length })
  })
  return out
}

const MARK: React.CSSProperties = { background: 'var(--find-bg)', color: 'inherit', borderRadius: 2 }
const MARK_CURRENT: React.CSSProperties = { ...MARK, background: 'var(--find-active-bg)' }

// Wraps one line's matched ranges in <mark>s, keeping the token colors: a
// styled token is split around the match and re-wrapped in its own span.
function markLine(nodes: React.ReactNode[], ranges: { start: number; end: number; current: boolean }[]): React.ReactNode[] {
  if (!ranges.length) return nodes
  const out: React.ReactNode[] = []
  let offset = 0
  let key = 0
  for (const node of nodes) {
    const el = React.isValidElement<{ style?: React.CSSProperties; children?: string }>(node) ? node : null
    const text = typeof node === 'string' ? node : el ? String(el.props.children ?? '') : ''
    const parts: React.ReactNode[] = []
    let pos = 0
    for (const r of ranges) {
      const s = Math.max(r.start - offset, 0)
      const e = Math.min(r.end - offset, text.length)
      if (e <= 0 || s >= text.length || s >= e || s < pos) continue
      if (s > pos) parts.push(text.slice(pos, s))
      parts.push(
        <mark key={key++} data-match={r.current ? 'current' : 'hit'} style={r.current ? MARK_CURRENT : MARK}>{text.slice(s, e)}</mark>,
      )
      pos = e
    }
    if (pos === 0) out.push(node)
    else {
      if (pos < text.length) parts.push(text.slice(pos))
      out.push(el ? <span key={key++} style={el.props.style}>{parts}</span> : <React.Fragment key={key++}>{parts}</React.Fragment>)
    }
    offset += text.length
  }
  return out
}

// ---------- step rows + step-script modal ----------

// One descriptor per step fact, shared by the row's Tag chips and the
// step-script modal's tag row — both render the same chips with the same
// tooltip sentences, so row and modal can never drift. `rowHidden` marks
// modal-only facts (§9.2: the detail rows carry no package chips).
type StepTagDesc = {
  key: string
  icon: string
  label: string
  title: string
  tone: 'accent' | 'plain' | 'red'
  rowHidden?: boolean
}

// Per-variant Tag visuals for a tone; only the editor's plain chips differ
// (they pin the muted text color; detail leaves the Tag's default).
const tagVisual = (tone: StepTagDesc['tone'], editor: boolean): { c?: string; style: React.CSSProperties } =>
  tone === 'red'
    ? { c: 'var(--red-text)', style: { background: 'var(--red-bg)', border: '1px solid var(--notice-red-border)' } }
    : tone === 'accent'
      ? { c: 'var(--accent)', style: { background: 'var(--accent-chip-bg)', border: '1px solid var(--border-card-hover)' } }
      : { ...(editor ? { c: 'var(--text-muted)' } : {}), style: { background: 'var(--hairline-dim)' } }

// The full ordered fact list for one step (§9.2/§11): agents, secrets,
// packages (rowHidden on the detail variant), time limit, retries.
function stepTagDescs(props: StepListProps, step: Step, copy: ReturnType<typeof usePlatformCopy>): StepTagDesc[] {
  const unres = props.unresolvedReferences
  // §5.1: a dangling agent id carried by unresolvedReferences shows the
  // archive record's name with the imported-file sentence.
  const unresAgent = (id: string) => (unres?.[id]?.kind === 'agent' ? unres[id] : null)
  const descs: StepTagDesc[] = []
  if (props.variant === 'editor' && step.agent) {
    // §4.1: one tag per entry in the step's `agents` list, resolved BY ID to
    // the live agent (a rename updates the tag); an empty list falls
    // back to the automation's first enabled agent ("no agent" when none is).
    // enabled=false + ag → exists but not enabled; ag=null → deleted agent.
    // §11: why = the entry's role note, falling back to the step's own why.
    const entries: { nm: string | null; why?: string; ag: Agent | null; enabled: boolean; imported?: boolean }[] =
      (step.agents ?? []).length
        ? (step.agents ?? []).map((e) => {
          const ag = props.allAgents.find((g) => g.id === e.id) ?? null
          const un = ag ? null : unresAgent(e.id)
          return {
            nm: ag ? agName(ag) : un ? un.name : shortId(e.id), why: e.why, ag,
            enabled: props.availAgents.some((g) => g.id === e.id), ...(un ? { imported: true } : {}),
          }
        })
        : [{ nm: props.availAgents[0] ? agName(props.availAgents[0]) : null, ag: props.availAgents[0] ?? null, enabled: !!props.availAgents[0] }]
    entries.forEach(({ nm, why, ag, enabled, imported }, j) => descs.push({
      key: `agent-${j}`, icon: 'fa-microchip', label: nm ?? 'no agent',
      tone: ag && enabled ? 'accent' : 'red',
      title: ag && enabled
        ? `This step calls ${agName(ag)} · ${dispModel(ag)} mid-execution${(why || step.why) ? ` — ${why || step.why}` : ''}`
        : ag
          ? `${agName(ag)} isn’t enabled for steps — this step would fail`
          : imported
            ? `This step calls ${nm} from the imported file. No agent on this ${copy.machine} matched it, so this step would fail.`
            : nm
              ? 'This step calls an agent that no longer exists — this step would fail'
              : 'No agent is enabled for steps — this step would fail',
    }))
  }
  if (props.variant === 'detail' && step.agent) {
    // §9.2: accent-only agent tags; entry ids resolve to LIVE names (a rename
    // updates the tag); a dangling id renders the red deleted state, under the
    // archive record's name when unresolvedReferences carries it, else the
    // short id prefix.
    const entries: { name: string; why?: string; missing: boolean; imported?: boolean }[] = step.agents?.length
      ? step.agents.map((e) => {
        const ag = props.agents.find((g) => g.id === e.id)
        const un = ag ? null : unresAgent(e.id)
        return {
          name: ag ? agName(ag) : un ? un.name : shortId(e.id), why: e.why, missing: !ag,
          ...(un ? { imported: true } : {}),
        }
      })
      : [{ name: props.fallbackAgent, missing: false }]
    entries.forEach((t, j) => descs.push({
      key: `agent-${j}`, icon: 'fa-microchip', label: t.name, tone: t.missing ? 'red' : 'accent',
      title: t.missing
        ? t.imported
          ? `This step calls ${t.name} from the imported file. No agent on this ${copy.machine} matched it, so this step would fail.`
          : 'This step calls an agent that no longer exists — this step would fail'
        : `This step calls the ${t.name} AI agent${(t.why || step.why) ? ` — ${t.why || step.why}` : ''}`,
    }))
  }
  for (const t of stepSecretTags(step, props.secrets, unres)) {
    descs.push({
      key: `secret-${t.id}`, icon: 'fa-key', label: t.name, tone: t.missing ? 'red' : 'plain',
      title: t.missing
        ? t.imported
          ? `This step uses ${t.name} from the imported file. No secret on this ${copy.machine} matched it, so this step would fail.`
          : 'This step uses a secret that no longer exists — this step would fail'
        : `This step uses the ${t.name} secret from your ${copy.secretStore}${t.why ? ` — ${t.why}` : ''}`,
    })
  }
  // §9.2: package facts feed both variants' modals — the editor from the
  // draft's declared packages, the detail modal from the automation record's
  // §6.2 list — but the detail ROWS carry no package chips (rowHidden).
  for (const p of stepPackageTags(step, props.packages)) {
    descs.push({
      key: `pkg-${p.import}`, icon: 'fa-cube', label: p.import, tone: 'plain',
      title: `This step uses the ${p.import} Python package${p.version ? `, version ${p.version}` : ''}${p.why ? ` — ${p.why}` : ''}`,
      ...(props.variant === 'detail' ? { rowHidden: true } : {}),
    })
  }
  descs.push({ key: 'timeout', icon: 'fa-clock', label: stepTimeoutLabel(step), title: stepTimeoutTitle(step), tone: 'plain' })
  const retries = stepRetriesLabel(step)
  if (retries) {
    descs.push({ key: 'retries', icon: 'fa-rotate-right', label: retries, title: stepRetriesTitle(step), tone: 'plain' })
  }
  return descs
}

// §9.2/§11 step row: the whole row is a click target opening the step-script
// modal. The detail page's only right-edge affordance is the expand glyph
// ("View script" tooltip, no text label, so narrow windows don't crush the
// middle column); the §11 editor's rows carry no glyph at all and keep the
// tooltip on the row.
function StepRow({ step, i, last, editor, tags, onOpen }: {
  step: Step; i: number; last: boolean; editor: boolean; tags: StepTagDesc[]; onOpen: () => void
}) {
  const tagNodes = tags.filter((t) => !t.rowHidden).map((t) => {
    const v = tagVisual(t.tone, editor)
    return <Tag key={t.key} icon={t.icon} c={v.c} title={t.title} style={v.style}>{t.label}</Tag>
  })
  const glyph = editor ? null : (
    <span title="View script" style={{ color: 'var(--text-deco)', flex: 'none' }}>
      <i className="fa-solid fa-expand" style={{ fontSize: 12 }} />
    </span>
  )
  // §14 list row — one geometry for both variants: gutter step number,
  // 13/600 name, 11.5 sub-line, divider suppressed on the last row.
  return (
    <div style={{ borderBottom: last ? 'none' : '1px solid var(--hairline-dim)' }}>
      <button className="ad-btn-bare ad-hover-row ad-focus-inset" onClick={onOpen} title={editor ? 'View script' : undefined} style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '12px 18px', cursor: 'pointer' }}>
        <span style={{ fontFamily: 'var(--mono)', fontWeight: 500, fontSize: 11, color: 'var(--text-faint)', width: 14, flex: 'none' }}>{i + 1}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{step.name}</span>
            {tagNodes}
          </div>
          <div style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--text-muted)', marginTop: 1 }}>{step.description}</div>
        </div>
        {glyph}
      </button>
    </div>
  )
}

// Left / right arrow keys flip the viewed step; ⌘F / Ctrl+F opens the find
// bar. Rendered inside the Modal so it can see `closing`: the children stay
// mounted through the ~200 ms exit animation, and a key press then would act
// on the fading card — same guard shape as the Modal's own Escape handler.
// The flip keys ignore editable targets, so typing in the find field never
// flips the step.
function StepKeys({ i, count, closing, onNav, onFind }: {
  i: number; count: number; closing: boolean; onNav: (i: number) => void; onFind: () => void
}) {
  useEffect(() => {
    if (closing) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); onFind(); return }
      if (e.target instanceof HTMLElement && e.target.closest('input, textarea, [contenteditable="true"]')) return
      const flip = e.key === 'ArrowLeft' ? (i > 0 ? i - 1 : null) : e.key === 'ArrowRight' ? (i < count - 1 ? i + 1 : null) : null
      if (flip === null) return
      e.preventDefault()
      // §9.2: a key flip drops focus from whatever holds it — a chevron, or
      // the page's step row that opened the modal and still holds focus
      // behind it (wider than the card, its ring would peek past the edge) —
      // so keyboard mode never draws a ring; the accent bar alone marks the
      // step.
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
      onNav(flip)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [i, count, closing]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

// §9.2 step-script modal: a two-column viewer on one large Modal card, 82vh
// tall so flipping between steps never resizes the frame. Left, the step
// navigator: every step as a row, the viewed one expanded to hold its
// description and tag row (the same §14 chips the step row carries, tooltips
// holding the detail, plus the detail variant's modal-only package chips).
// Right, the code pane at full height on the code ground: a fixed toolbar
// (STEP N OF M eyebrow, the §4.1 version-folder filename — its one appearance
// in the UI — the line count, prev / next / close) over the line-numbered
// script in its own scroll pane. Only the code pane remounts through the §14
// keyed fade (resetting its scroll) when the step switches; the navigator and
// the toolbar stay put, so nothing under the pointer flashes.
function StepNavRow({ step, j, viewed, editor, tags, facts, onNav }: {
  step: Step; j: number; viewed: boolean; editor: boolean; tags: StepTagDesc[]; facts: StepFactSection[]; onNav: () => void
}) {
  // §9.2: the viewed row is a plain, text-selectable, unfocusable block — a
  // button would stop the user from dragging over its description, chips and
  // facts to copy them, and clicking the viewed row does nothing anyway. A
  // clicked button row unmounts as it becomes this block, so no focus ring
  // can linger on a step no longer viewed.
  const Row: 'div' | 'button' = viewed ? 'div' : 'button'
  return (
    <Row
      className={viewed ? undefined : 'ad-btn-bare ad-hover-row ad-focus-inset'}
      aria-current={viewed ? 'step' : undefined}
      onClick={viewed ? undefined : onNav}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 18px',
        cursor: viewed ? 'text' : 'pointer',
        // §14 selected row: the --bg-active wash plus an inset accent bar —
        // never a border-left with asymmetric padding
        ...(viewed
          ? {
            background: 'var(--bg-active)', boxShadow: 'inset 2px 0 0 var(--accent)',
            userSelect: 'text' as const, textAlign: 'left' as const,
          }
          : {}),
      }}
    >
      <span style={{ font: "500 11px/18px var(--mono)", color: 'var(--text-faint)', width: 16, flex: 'none' }}>{j + 1}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ font: `${viewed ? 600 : 500} 13px/18px var(--sans)`, color: viewed ? 'var(--text)' : 'var(--text-muted)' }}>
          {step.name}
        </div>
        {viewed && (
          <>
            {step.description && (
              <div style={{ font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)', marginTop: 4 }}>{step.description}</div>
            )}
            {/* §9.2 tag row: the same chips the step row carries (tooltips
                hold the detail), plus package chips in the detail modal. */}
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 8 }}>
              {tags.map((t) => {
                const v = tagVisual(t.tone, editor)
                return <Tag key={t.key} icon={t.icon} c={v.c} title={t.title} style={v.style}>{t.label}</Tag>
              })}
            </div>
            {/* §9.2 fact list: what the script reaches and touches, from a
                literal-only scan — labeled sections of bullets, not chips
                (hosts, prompts and file names are too long for a chip). */}
            {facts.length > 0 && (
              <div data-testid="step-facts" style={{ display: 'flex', flexDirection: 'column', gap: 9, marginTop: 12 }}>
                {facts.map((sec) => (
                  <div key={sec.key} data-testid={`step-facts-${sec.key}`}>
                    <Eyebrow style={{ marginBottom: 3 }}>{sec.label}</Eyebrow>
                    {sec.items.map((text, k) => (
                      <div key={k} style={{ display: 'flex', alignItems: 'flex-start', gap: 7, font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)' }}>
                        <span aria-hidden style={{ color: 'var(--text-deco)', flex: 'none', width: 8, textAlign: 'center', userSelect: 'none' }}>•</span>
                        <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>{text}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </Row>
  )
}

// §9.2: the frame is sized once per open to the automation's LONGEST script
// (toolbar + lines at the code pane's 12px/1.65 rhythm + its padding),
// floored at 440px for the navigator and capped at 82vh — stable across
// flips, yet an automation of short steps gets a card that fits them
// instead of a mostly empty one.
export function stepModalFrame(steps: Step[]): string {
  const longest = Math.max(1, ...steps.map((s) => (s.code || '').replace(/\n$/, '').split('\n').length))
  return `clamp(440px, ${Math.ceil(44 + 38 + longest * 12 * 1.65)}px, 82vh)`
}

function StepModal({ steps, i, editor, tagsByStep, factsByStep, onNav, onClose }: {
  steps: Step[]; i: number; editor: boolean; tagsByStep: StepTagDesc[][]; factsByStep: StepFactSection[][]
  onNav: (i: number) => void; onClose: () => void
}) {
  const step = steps[i]
  // A script's single trailing final newline is neither rendered nor counted —
  // it would show as a blank last line and count one line too many (§9.2).
  const code = (editor ? (step.code || '# script not written yet') : step.code || '').replace(/\n$/, '')
  const lines = useMemo(() => highlightPythonLines(code), [code])
  const lineCount = lines.length
  const frame = stepModalFrame(steps)
  // §9.2 find in script: the bar and its query survive step flips; the
  // current match resets to the first on a new query or a new step.
  const [findOpen, setFindOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [cur, setCur] = useState(0)
  const findInput = useRef<HTMLInputElement>(null)
  const scroller = useRef<HTMLDivElement | null>(null)
  const matches = useMemo(() => findInLines(lines, query), [lines, query])
  useEffect(() => { setCur(0) }, [query, i])
  const openFind = () => {
    setFindOpen(true)
    // an open bar refocuses and selects; a fresh one focuses once mounted
    requestAnimationFrame(() => { findInput.current?.focus(); findInput.current?.select() })
  }
  const closeFind = () => { setFindOpen(false); setQuery('') }
  const step_ = (d: 1 | -1) => { if (matches.length) setCur((c) => (c + d + matches.length) % matches.length) }
  // keep the current match mid-pane — scrolling the pane's own scroller, never the page
  useEffect(() => {
    const sc = scroller.current
    const mark = sc?.querySelector<HTMLElement>('mark[data-match="current"]')
    if (!sc || !mark) return
    const r = mark.getBoundingClientRect()
    const box = sc.getBoundingClientRect()
    sc.scrollTop += r.top - box.top - sc.clientHeight / 2 + r.height / 2
  }, [cur, matches, i])
  const marked = useMemo(() => {
    if (!matches.length) return lines
    return lines.map((ln, n) => markLine(ln, matches.map((m, k) => ({ ...m, current: k === cur })).filter((m) => m.line === n)))
  }, [lines, matches, cur])
  const counter = matches.length ? `${cur + 1} of ${matches.length}` : query ? 'No matches' : ''
  return (
    <Modal
      onClose={onClose} width={1120} ariaLabel={`Step ${i + 1} of ${steps.length}: ${step.name}`}
      cardStyle={{ padding: 0, width: 'min(1120px, 92vw)', overflow: 'hidden' }}
    >
      {(close, closing) => (
        <div className="ad-stepmodal" style={{ height: frame, display: 'flex', minWidth: 0 }}>
          <StepKeys i={i} count={steps.length} closing={closing} onNav={onNav} onFind={openFind} />
          {/* step navigator */}
          <div className="ad-stepnav" style={{
            width: 280, flex: 'none', minHeight: 0, display: 'flex', flexDirection: 'column',
            borderRight: '1px solid var(--hairline-dim)',
          }}>
            <div style={{
              height: 44, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '0 18px 0 16px', borderBottom: '1px solid var(--hairline-dim)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>STEPS</Eyebrow>
              <span style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)' }}>{steps.length}</span>
            </div>
            <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
              <div style={{ paddingBottom: 12 }}>
                {steps.map((s, j) => (
                  <StepNavRow key={j} step={s} j={j} viewed={j === i} editor={editor} tags={tagsByStep[j]} facts={factsByStep[j]} onNav={() => onNav(j)} />
                ))}
              </div>
            </ScrollArea>
          </div>
          {/* code pane */}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-code)' }}>
            <div style={{
              height: 44, flex: 'none', display: 'flex', alignItems: 'center', gap: 12,
              padding: '0 10px 0 18px', borderBottom: '1px solid var(--hairline-dim)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>STEP {i + 1} OF {steps.length}</Eyebrow>
              <span style={{
                font: "400 11px var(--mono)", color: 'var(--text-deco)', flex: 1, minWidth: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {step.file || 'script'}
              </span>
              <span style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)', flex: 'none' }}>
                {lineCount} {lineCount === 1 ? 'line' : 'lines'}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 2, flex: 'none', marginLeft: 4 }}>
                <button className="ad-btn-icon" aria-label="Find in script" aria-pressed={findOpen} onClick={openFind}>
                  <i className="fa-solid fa-magnifying-glass" />
                </button>
                <button className="ad-btn-icon" aria-label="Previous step" disabled={i === 0} onClick={() => onNav(i - 1)}>
                  <i className="fa-solid fa-chevron-left" />
                </button>
                <button className="ad-btn-icon" aria-label="Next step" disabled={i === steps.length - 1} onClick={() => onNav(i + 1)}>
                  <i className="fa-solid fa-chevron-right" />
                </button>
                <button className="ad-btn-icon" aria-label="Close" onClick={close}>
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
            </div>
            {findOpen && (
              <div data-testid="find-bar" style={{
                height: 36, flex: 'none', display: 'flex', alignItems: 'center', gap: 8,
                padding: '0 10px 0 18px', borderBottom: '1px solid var(--hairline-dim)',
              }}>
                <input
                  ref={findInput}
                  className="ad-input compact"
                  placeholder="Find in script"
                  aria-label="Find in script"
                  value={query}
                  autoFocus
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); step_(e.shiftKey ? -1 : 1) }
                    else if (e.key === 'Escape') { e.stopPropagation(); closeFind() }
                    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') e.stopPropagation()
                  }}
                  style={{ width: 280, flex: '0 1 auto', minWidth: 0 }}
                />
                <span data-testid="find-counter" style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)', flex: 'none', width: 72, whiteSpace: 'nowrap' }}>
                  {counter}
                </span>
                <button className="ad-btn-icon" aria-label="Previous match" disabled={!matches.length} onClick={() => step_(-1)}>
                  <i className="fa-solid fa-chevron-up" />
                </button>
                <button className="ad-btn-icon" aria-label="Next match" disabled={!matches.length} onClick={() => step_(1)}>
                  <i className="fa-solid fa-chevron-down" />
                </button>
                <button className="ad-btn-icon" aria-label="Close find" onClick={closeFind} style={{ marginLeft: 'auto' }}>
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
            )}
            <ScrollArea key={i} className="ad-anim-fade" wrapStyle={{ flex: 1, minHeight: 0 }} scrollRef={scroller}>
              <div style={{
                // 28px right padding keeps the longest line clear of the overlay thumb
                display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', padding: '14px 28px 24px 0',
                font: "400 12px/1.65 var(--mono)", color: 'var(--code-text)',
              }}>
                {marked.map((ln, n) => (
                  <React.Fragment key={n}>
                    <span style={{ textAlign: 'right', padding: '0 16px 0 18px', color: 'var(--text-deco)', userSelect: 'none' }}>{n + 1}</span>
                    {/* an empty line carries a newline so a copied selection keeps its blank lines */}
                    <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{ln.length ? ln : '\n'}</span>
                  </React.Fragment>
                ))}
              </div>
            </ScrollArea>
          </div>
        </div>
      )}
    </Modal>
  )
}

// Holds the viewed-step index locally so opening the modal re-renders only
// this list. One step shows at a time; prev / next flips inside the modal.
// `history` / `viewing` feed the §9.2 change badge: the stored revisions
// (current version + `versions`) and which revision these steps belong to.
export type StepListProps = {
  steps: Step[]; secrets: SecretMeta[]; packages: PackageDep[]; unresolvedReferences?: UnresolvedRefs
  history?: StepHistory[]; viewing?: number | 'draft'
  params?: ParamDef[] // §4.2 definitions, labeling the "Uses the … parameter" fact
} & (
  | { variant: 'editor'; availAgents: Agent[]; allAgents: Agent[] }
  | { variant: 'detail'; agents: Agent[]; fallbackAgent: string }
)

export function StepList(props: StepListProps) {
  const { steps } = props
  // §9 per-OS copy rule: the secret-store name in the secret fact sentences.
  const copy = usePlatformCopy()
  const [viewing, setViewing] = useState<number | null>(null)
  // §11: a sync/undo that swaps the steps closes the editor's modal — the
  // index would no longer name the same step. The detail page's modal stays
  // open across store refreshes.
  const editor = props.variant === 'editor'
  useEffect(() => { if (editor) setViewing(null) }, [steps]) // eslint-disable-line react-hooks/exhaustive-deps
  const current = viewing !== null && viewing < steps.length ? viewing : null
  // Deriving the facts scans every script for secret references and package
  // imports, so it only reruns when the steps or the records they resolve
  // against change — an unrelated re-render (the §11 job poll ticks the editor
  // twice a second with the modal open) reuses the descriptors. Rows and modal
  // read the same entry, so they still can never drift.
  const { secrets, packages, unresolvedReferences } = props
  const tagsByStep = useMemo(
    () => steps.map((s) => stepTagDescs(props, s, copy)),
    // The last two are the per-variant agent inputs: the editor resolves entry
    // ids against allAgents and checks them against availAgents, the detail
    // variant against agents with fallbackAgent for an empty list.
    [steps, secrets, packages, unresolvedReferences, editor, copy,
      editor ? props.allAgents : props.agents, editor ? props.availAgents : props.fallbackAgent],
  ) // eslint-disable-line react-hooks/exhaustive-deps
  // §9.2 facts: the literal scans rerun only when the scripts or the stored
  // history change.
  const { history, viewing: revision, params } = props
  const factsByStep = useMemo(
    () => steps.map((_, i) => stepFacts(steps, i, revision, history, params)),
    [steps, history, revision, params],
  )
  return (
    <>
      {steps.map((s, i) => (
        <StepRow
          key={i} step={s} i={i}
          last={i === steps.length - 1}
          editor={editor}
          tags={tagsByStep[i]}
          onOpen={() => setViewing(i)}
        />
      ))}
      {current !== null && (
        <StepModal
          steps={steps} i={current} editor={editor}
          tagsByStep={tagsByStep}
          factsByStep={factsByStep}
          onNav={setViewing}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  )
}

// ---------- param value editor (§4.2 kinds) ----------

// Presentational value controls for the five param kinds. The wrappers own
// layout (label/help rows), state, and commit semantics: the §11 test-value
// card ('draft' variant) writes value + default immediately into the draft;
// the §9.2 ParamRow ('detail' variant) keeps its local drafts and
// debounce/PATCH plumbing and passes them through here.
// §14 the list editor's entry/link count — one mono metadata line in both variants.
const countStyle: React.CSSProperties = { font: "500 11px var(--mono)", color: 'var(--text-faint)' }

export function ParamValueEditor({ p, variant, on, lines, rows, value, setOn, setLines, setRows, setText, setNumber, onFocus, onBlur }: {
  p: ParamDef
  variant: 'draft' | 'detail'
  on: boolean
  lines: string[]
  rows: { key: string; value: string }[]
  value: string // the rendered text/number input value
  setOn: (v: boolean) => void
  setLines: (next: string[], removal?: boolean) => void // removal → the detail variant commits at once
  setRows: (next: { key: string; value: string }[], removal?: boolean) => void
  setText: (v: string) => void
  setNumber: (digits: string) => void
  onFocus?: () => void // detail: text/number focus tracking
  onBlur?: () => void // draft: number clamp; detail: flush (list/kv) or flush+reset (text/number)
}) {
  const detail = variant === 'detail'
  // 'draft' base input layout (the create/edit flow's test-value editors) —
  // §14: .ad-input.compact owns the geometry, call sites only lay out
  const inputStyle: React.CSSProperties = { flex: 1, minWidth: 0 }
  if (p.kind === 'toggle') {
    return <Toggle on={on} onChange={setOn} />
  }
  if (p.kind === 'number') {
    return (
      <input
        className="ad-input compact mono"
        value={value}
        {...(detail ? { inputMode: 'numeric' as const } : {})}
        onChange={(e) => setNumber(e.target.value.replace(/[^0-9]/g, ''))}
        onFocus={onFocus}
        onBlur={onBlur}
        style={detail
          ? { width: 70, textAlign: 'center' }
          : { width: 84, textAlign: 'right' }}
      />
    )
  }
  if (p.kind === 'text') {
    return (
      <input
        className="ad-input compact"
        value={value} placeholder={detail ? p.placeholder ?? '' : p.placeholder}
        onChange={(e) => setText(e.target.value)}
        onFocus={onFocus}
        onBlur={onBlur}
        style={detail
          ? { width: '100%', maxWidth: 520 }
          : { ...inputStyle, width: '100%' }}
      />
    )
  }
  if (p.kind === 'list') {
    const good = lines.filter((l) => l.trim() && validUrl(l)).length
    const bad = lines.filter((l) => l.trim() && !validUrl(l)).length
    return (
      <div style={detail
        ? { width: '100%', display: 'flex', flexDirection: 'column', gap: 6 }
        : { display: 'flex', flexDirection: 'column', gap: 6 }}>
        {lines.map((ln, li) => {
          const invalid = !!p.validate && ln.trim() !== '' && !validUrl(ln)
          return (
            <div key={li} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                className={`ad-input compact mono${invalid ? ' invalid' : ''}`}
                value={ln}
                onChange={(e) => setLines(lines.map((z, j) => (j === li ? e.target.value : z)))}
                onBlur={detail ? onBlur : undefined}
                style={{ ...inputStyle, ...(invalid ? { color: 'var(--red-text)' } : {}) }}
              />
              {invalid && (
                <MiniBadge c="var(--red-text)" bg="var(--red-bg)" style={detail ? { flex: 'none' } : undefined}>NOT A VALID LINK</MiniBadge>
              )}
              <button className="ad-btn-x" onClick={() => setLines(lines.filter((_, j) => j !== li), true)} aria-label="Remove line">
                <i className="fa-solid fa-xmark" />
              </button>
            </div>
          )
        })}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <button className="ad-btn-dashed" onClick={() => setLines([...lines, ''])}>
            + Add line
          </button>
          {detail ? (
            <span style={countStyle}>
              {lines.length}{p.validate ? ` lines · ${good} valid links${bad ? ` · ${bad} needs attention` : ''}` : ' entries'}
            </span>
          ) : p.validate ? (
            <span style={countStyle}>
              {lines.length} lines · {good} valid links{bad > 0 ? ` · ${bad} needs attention` : ''}
            </span>
          ) : null}
        </div>
      </div>
    )
  }
  // kv
  return (
    <div style={detail
      ? { width: '100%', display: 'flex', flexDirection: 'column', gap: 6 }
      : { display: 'flex', flexDirection: 'column', gap: 6 }}>
      {rows.map((r, ri) => (
        <div key={ri} style={detail ? { display: 'flex', gap: 6 } : { display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            className="ad-input compact mono"
            value={r.key} placeholder={detail ? undefined : 'Key'}
            onChange={(e) => setRows(rows.map((z, j) => (j === ri ? { ...z, key: e.target.value } : z)))}
            onBlur={detail ? onBlur : undefined}
            style={detail
              ? { flex: 1.3, minWidth: 0 }
              : { ...inputStyle, flex: '0 1 38%' }}
          />
          <input
            className="ad-input compact mono"
            value={r.value} placeholder={detail ? undefined : 'Value'}
            onChange={(e) => setRows(rows.map((z, j) => (j === ri ? { ...z, value: e.target.value } : z)))}
            onBlur={detail ? onBlur : undefined}
            style={inputStyle}
          />
          <button className="ad-btn-x" onClick={() => setRows(rows.filter((_, j) => j !== ri), true)} aria-label={r.key.trim() ? `Remove ${r.key}` : 'Remove row'}>
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
      ))}
      <button className="ad-btn-dashed" onClick={() => setRows([...rows, { key: '', value: '' }])}>
        {detail ? '+ Add row' : '+ Add pair'}
      </button>
    </div>
  )
}
