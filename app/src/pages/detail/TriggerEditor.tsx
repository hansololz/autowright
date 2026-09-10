// §9.2 Add-trigger editor (kind picker → cron expression / interval pair / one-shot time)
// plus its widgets: permission checklist, setup guides, segmented time entry,
// timezone and bot-token-secret pickers.
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { usePlatformCopy } from '../../platformCopy'
import { SecretModal } from '../../SecretModal'
import { useStore } from '../../store'
import type { SecretMeta, Trigger, TriggerKindFields } from '../../types'
import { Caret, Collapse, MenuRow, MiniBadge, PopMenu, ScrollArea, usePopover } from '../../ui'
import { useTriggerPreview } from '../../triggers'

const TZ_LIST: string[] = Intl.supportedValuesOf('timeZone')

type AddableKind = 'cron' | 'interval' | 'time' | 'app_start' | 'discord' | 'imessage'

// §9.2: one icon per kind — the picker chips and the trigger rows share it
const KIND_META: Array<{ kind: AddableKind; icon: string; label: string }> = [
  { kind: 'cron', icon: 'fa-solid fa-clock', label: 'Cron' },
  { kind: 'interval', icon: 'fa-solid fa-stopwatch', label: 'Interval' },
  { kind: 'time', icon: 'fa-solid fa-calendar-day', label: 'One time' },
  { kind: 'app_start', icon: 'fa-solid fa-rocket', label: 'App start' },
  { kind: 'discord', icon: 'fa-brands fa-discord', label: 'Discord' },
  { kind: 'imessage', icon: 'fa-solid fa-comment', label: 'iMessage' },
]
export const kindIcon = (k: AddableKind) => KIND_META.find((m) => m.kind === k)!.icon

// §9.2 iMessage permission checklist — FDA can only be detected and guided
// (macOS has no prompt API for it); Automation is promptable via the probe.
function ImsgPermissions() {
  const [perms, setPerms] = useState<{
    fullDisk: boolean; automation: 'granted' | 'denied' | 'unknown'
  } | null>(null)
  const [probing, setProbing] = useState(false)
  useEffect(() => {
    let live = true
    const poll = () => {
      api.imessagePermissions().then((p) => { if (live) setPerms(p) }).catch(() => {})
    }
    poll()
    const iv = setInterval(poll, 3000) // §9.2: flips to granted moments after the user toggles it
    return () => { live = false; clearInterval(iv) }
  }, [])
  const probe = () => {
    setProbing(true)
    api.imessageAutomationProbe()
      .then((r) => setPerms((p) => (p ? { ...p, automation: r.automation } : p)))
      .catch(() => {})
      .finally(() => setProbing(false))
  }
  const row = (icon: React.ReactNode, name: string, note: string, action?: React.ReactNode) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 24 }}>
      <span style={{ width: 14, flex: 'none', textAlign: 'center' }}>{icon}</span>
      <span style={{ font: '500 11.5px var(--sans)', color: 'var(--text-2)', flex: 'none' }}>{name}</span>
      <span style={{
        flex: 1, minWidth: 0, font: '400 11.5px/1.45 var(--sans)', color: 'var(--text-muted)',
      }}>
        {note}
      </span>
      {action}
    </div>
  )
  const check = <i className="fa-solid fa-check" style={{ color: 'var(--green)', fontSize: 11 }} />
  const dot = (c: string) => <i className="fa-solid fa-circle" style={{ color: c, fontSize: 7 }} />
  const btn = (label: string, onClick: () => void, spin = false) => (
    <button className="ad-btn-accent-ghost small" onClick={onClick} disabled={spin} style={{ flex: 'none' }}>
      {spin && <i className="fa-solid fa-spinner fa-spin" style={{ fontSize: 10, marginRight: 5 }} />}
      {label}
    </button>
  )
  return (
    <div style={{
      border: '1px solid var(--hairline-dim)', borderRadius: 8, padding: '8px 10px',
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      {row(
        perms == null ? dot('var(--text-faint)') : perms.fullDisk ? check : dot('var(--amber)'),
        'Full Disk Access',
        perms == null ? 'Checking…'
          : perms.fullDisk ? 'Granted'
          : 'Needed — Autowright reads incoming messages from the Messages database',
        perms != null && !perms.fullDisk
          ? btn('Open System Settings', () =>
              window.open('x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'))
          : undefined,
      )}
      {row(
        perms == null ? dot('var(--text-faint)')
          : perms.automation === 'granted' ? check
          : dot(perms.automation === 'denied' ? 'var(--amber)' : 'var(--text-faint)'),
        'Messages automation',
        perms == null ? 'Checking…'
          : perms.automation === 'granted' ? 'Granted'
          : perms.automation === 'denied'
          ? 'Denied — turn it on in System Settings → Privacy & Security → Automation'
          : 'Not asked yet — Autowright sends replies through Messages',
        perms != null && perms.automation === 'unknown'
          ? btn('Grant', probe, probing)
          : undefined,
      )}
    </div>
  )
}

/** §9.2 setup-guide disclosure — the one pattern both message-trigger kinds use. */
function GuideToggle({ label, open, onToggle, children }: {
  label: string; open: boolean; onToggle: () => void; children: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <button className="ad-btn-text dim small" onClick={onToggle} style={{ alignSelf: 'flex-start' }}>
        <Caret open={open} style={{ marginRight: 5 }} />
        {label}
      </button>
      <Collapse open={open}>
        <ol style={{
          margin: '8px 0 0', paddingLeft: 18, fontSize: 11.5, lineHeight: 1.6, cursor: 'text',
          color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          {children}
        </ol>
      </Collapse>
    </div>
  )
}

/** §9.2 segmented 24-hour time entry — one `ad-input` box, three two-digit
 *  fields (HH:MM:SS). Automation-advance on a completed pair, ↑/↓ stepping with
 *  wrap, Backspace-when-empty jumps back, paste distributes digit pairs. */
const TIME_MAXES = [23, 59, 59]

function TimeParts({ parts, invalid, onChange }: {
  parts: [string, string, string]
  invalid: boolean
  onChange: (p: [string, string, string]) => void
}) {
  const refs = useRef<Array<HTMLInputElement | null>>([])
  const set = (i: number, v: string) => {
    const next = [...parts] as [string, string, string]
    next[i] = v
    onChange(next)
  }
  const type = (i: number, raw: string) => {
    const digits = raw.replace(/\D/g, '')
    if (digits.length <= 2) {
      set(i, digits)
      if (digits.length === 2 && i < 2) refs.current[i + 1]?.select()
      return
    }
    // paste: digit pairs spill into the following segments
    const next = [...parts] as [string, string, string]
    let rest = digits.slice(0, 6)
    let j = i
    while (rest && j < 3) { next[j] = rest.slice(0, 2); rest = rest.slice(2); j++ }
    onChange(next)
    refs.current[Math.min(j, 2)]?.select()
  }
  const key = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault()
      if (parts[i] === '') return set(i, '00')
      const span = TIME_MAXES[i] + 1
      const cur = Math.min(Number(parts[i]), TIME_MAXES[i])
      set(i, String((cur + (e.key === 'ArrowUp' ? 1 : span - 1)) % span).padStart(2, '0'))
    } else if (e.key === 'Backspace' && parts[i] === '' && i > 0) {
      refs.current[i - 1]?.select()
    }
  }
  return (
    <div
      className={`ad-input compact mono${invalid ? ' invalid' : ''}`}
      style={{ display: 'flex', alignItems: 'center', cursor: 'text' }}
    >
      {parts.map((p, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span style={{ color: 'var(--text-faint)' }}>:</span>}
          <input
            ref={(el) => { refs.current[i] = el }}
            value={p}
            onChange={(e) => type(i, e.target.value)}
            onKeyDown={(e) => key(i, e)}
            onFocus={(e) => e.target.select()}
            // pad from the DOM value: auto-advance blurs this segment in the
            // same tick as its onChange, so the closure's `p` is one render old
            onBlur={(e) => { if (e.target.value.length === 1) set(i, `0${e.target.value}`) }}
            placeholder={['HH', 'MM', 'SS'][i]}
            inputMode="numeric"
            spellCheck={false}
            aria-label={['hours', 'minutes', 'seconds'][i]}
            style={{
              width: 20, textAlign: 'center', background: 'none', border: 'none',
              outline: 'none', font: 'inherit', color: 'inherit', padding: 0,
            }}
          />
        </React.Fragment>
      ))}
    </div>
  )
}

/** §9.2 Interval pair — the §4.3 `every` duration entered as an amount and a
 *  unit word. The pair composes the canonical single-component form, and an
 *  edit swap decomposes the stored one back into it. */
const INTERVAL_UNITS = ['seconds', 'minutes', 'hours', 'days'] as const
type IntervalUnit = typeof INTERVAL_UNITS[number]
const UNIT_SECONDS: Record<IntervalUnit, number> = {
  days: 86400, hours: 3600, minutes: 60, seconds: 1,
}
const composeEvery = (amount: string, unit: IntervalUnit): string =>
  unit === 'days' ? `P${amount}D`
    : `PT${amount}${unit === 'hours' ? 'H' : unit === 'minutes' ? 'M' : 'S'}`

// §4.3: the stored form carries exactly one component, so the single non-empty
// group is the pair. A hand-written multi-component duration still decomposes —
// its total goes in the largest unit that divides it exactly, as the backend
// canonicalizes it anyway.
function decomposeEvery(every: string): { amount: string; unit: IntervalUnit } {
  const m = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(every.trim())
  const groups = m
    ? (['days', 'hours', 'minutes', 'seconds'] as IntervalUnit[])
        .map((unit, i) => ({ unit, raw: m[i + 1] }))
        .filter((g) => g.raw != null)
    : []
  if (groups.length === 0) return { amount: '1', unit: 'hours' }
  if (groups.length === 1) return { amount: String(Number(groups[0].raw)), unit: groups[0].unit }
  const total = groups.reduce((sum, g) => sum + Number(g.raw) * UNIT_SECONDS[g.unit], 0)
  const unit = INTERVAL_UNITS.slice().reverse().find((u) => total % UNIT_SECONDS[u] === 0)!
  return { amount: String(total / UNIT_SECONDS[unit]), unit }
}

/** Interval unit picker — the app's standard popover pattern; four fixed units,
 *  so no filter input (§9.2). */
function UnitPick({ unit, onPick }: { unit: IntervalUnit; onPick: (u: IntervalUnit) => void }) {
  const [open, setOpen, ref] = usePopover()
  return (
    <div ref={ref} style={{ position: 'relative', flex: 'none' }}>
      <button
        className="ad-btn-pill"
        onClick={() => setOpen(!open)}
        title="The unit the interval counts in"
      >
        <span>{unit}</span>
        <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 10 }} />
      </button>
      <PopMenu show={open} style={{ top: 'calc(100% + 6px)', left: 0, minWidth: 140 }}>
        {INTERVAL_UNITS.map((u) => (
          <MenuRow key={u} active={u === unit} onClick={() => { setOpen(false); onPick(u) }}>{u}</MenuRow>
        ))}
      </PopMenu>
    </div>
  )
}

/** Timezone picker — the app's standard popover pattern, filterable (§9.2). */
function TzPick({ timezone, onPick }: { timezone: string; onPick: (z: string) => void }) {
  const [open, setOpen, ref] = usePopover()
  const [q, setQ] = useState('')
  const needle = q.trim().toLowerCase()
  const zones = needle ? TZ_LIST.filter((z) => z.toLowerCase().includes(needle)) : TZ_LIST
  return (
    <div ref={ref} style={{ position: 'relative', marginTop: 8 }}>
      <button
        className="ad-btn-pill"
        onClick={() => { setQ(''); setOpen(!open) }}
        title="Timezone the trigger's times read in"
      >
        <i className="fa-solid fa-globe" style={{ color: 'var(--text-faint)', fontSize: 10 }} />
        <span style={timezone ? {} : { fontWeight: 400, color: 'var(--text-muted)' }}>{timezone || 'Local time'}</span>
        <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 10 }} />
      </button>
      <PopMenu show={open} style={{ top: 'calc(100% + 6px)', left: 0, minWidth: 280 }}>
        {open && (
          <input
            className="ad-input compact mono"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter timezones…"
            spellCheck={false}
            autoFocus
            style={{ width: '100%', marginBottom: 4 }}
          />
        )}
        <ScrollArea style={{ maxHeight: 240 }}>
          {!needle && (
            <MenuRow active={!timezone} onClick={() => { setOpen(false); onPick('') }}>Local time</MenuRow>
          )}
          {zones.map((z) => (
            <MenuRow key={z} active={z === timezone} onClick={() => { setOpen(false); onPick(z) }}>{z}</MenuRow>
          ))}
          {needle && zones.length === 0 && (
            <div style={{ padding: '9px 14px', font: '400 11.5px/1.45 var(--sans)', color: 'var(--text-muted)' }}>
              No timezone matches.
            </div>
          )}
        </ScrollArea>
      </PopMenu>
    </div>
  )
}

/** Bot-token secret picker — the app's standard popover pattern (§9.2).
 * `selected` is the secret's §4.8 id (what the trigger stores); the pill
 * renders the live name resolved from it — a dangling id (the secret was
 * deleted since) shows a short id prefix in the deleted-red treatment. */
function SecretPick({ secrets, selected, onPick }: {
  secrets: SecretMeta[]; selected: string; onPick: (id: string) => void
}) {
  const [open, setOpen, ref] = usePopover()
  const live = secrets.find((s) => s.id === selected)
  const dangling = !!selected && !live
  return (
    <div ref={ref} style={{ position: 'relative', flex: '0 1 auto', minWidth: 0 }}>
      <button
        className="ad-btn-pill"
        onClick={() => setOpen(!open)}
        title="The secret holding your Discord bot token"
        style={{ maxWidth: '100%' }}
      >
        <i className="fa-solid fa-key" style={{ color: 'var(--text-faint)', fontSize: 10 }} />
        <span style={{
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          ...(selected ? {} : { fontWeight: 400, color: 'var(--text-muted)' }),
          ...(dangling ? { color: 'var(--red-text)' } : {}),
        }}>
          {live ? live.name : dangling ? `${selected.slice(0, 8)}…` : 'Choose the bot-token secret…'}
        </span>
        <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 10 }} />
      </button>
      <PopMenu show={open} style={{ top: 'calc(100% + 6px)', left: 0, minWidth: 240 }}>
        {secrets.length === 0 ? (
          <div style={{ padding: '9px 14px', font: '400 11.5px/1.45 var(--sans)', color: 'var(--text-muted)' }}>
            No secrets yet — press New secret.
          </div>
        ) : secrets.map((s) => {
          const sel = s.id === selected
          return (
            <button
              className="ad-btn-bare ad-hover-row"
              key={s.id}
              onClick={() => { setOpen(false); onPick(s.id) }}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px', cursor: 'pointer',
                borderBottom: '1px solid var(--hairline-dim)',
                background: sel ? 'var(--accent-hint-bg)' : 'transparent',
              }}
            >
              <span style={{ width: 14, flex: 'none', textAlign: 'center', font: '600 12px var(--mono)', color: 'var(--accent)' }}>
                {sel ? <i className="fa-solid fa-check" style={{ fontSize: 10 }} /> : ''}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ font: `500 12.5px var(--mono)`, color: sel ? 'var(--text)' : 'var(--text-2)' }}>{s.name}</span>
                  {!s.set && <MiniBadge c="var(--amber)" bg="var(--amber-bg)">NOT SET</MiniBadge>}
                </div>
                {s.description && (
                  <div style={{
                    font: '400 11.5px/1.45 var(--sans)', color: 'var(--text-muted)', marginTop: 1,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {s.description}
                  </div>
                )}
              </div>
            </button>
          )
        })}
      </PopMenu>
    </div>
  )
}

// What the editor produces — the §4.3 kind-discriminated field sets; the
// caller adds id/off. A flat field bag exists only inside the editor's state.
export type TriggerDraft = TriggerKindFields
type TriggerFieldBag = {
  kind?: AddableKind; expression?: string; every?: string; at?: string; timezone?: string
  runIfMissed?: boolean
  channel?: string; secret?: string; pattern?: string; mention?: boolean; author?: string[]
  from?: string
}

export function TriggerEditor({ hasAppStart, initial, onSave, onCancel }: {
  hasAppStart: boolean
  initial?: Trigger // §9.2 edit mode: pre-filled, submit reads Save
  onSave: (t: TriggerDraft) => void
  onCancel: () => void
}) {
  const secrets = useStore((s) => s.secrets)
  // §9.2/§2 gating: the iMessage chip renders only while the platform can
  // honor the kind — absent, never disabled, everywhere else. Stored imessage
  // triggers keep displaying: only offering the kind is gated.
  const imessageOn = useStore((s) => s.platformCapabilities.imessage)
  // §9 per-OS copy rule: the secret-store name in the Discord setup guide.
  const copy = usePlatformCopy()
  const init: TriggerFieldBag = initial ?? {}
  const [kind, setKind] = useState<AddableKind>(init.kind ?? 'cron')
  const [expression, setExpr] = useState(init.expression ?? '')
  // §9.2 Interval pair: amount + unit compose the §4.3 `every`; an edit swap
  // decomposes the stored canonical duration back into them
  const initInterval = decomposeEvery(init.every ?? '')
  const [amount, setAmount] = useState(initInterval.amount)
  const [unit, setUnit] = useState<IntervalUnit>(initInterval.unit)
  // §9.2 One time: date and segmented 24-hour time entered apart, combined
  // into `at`; seconds pre-fill 00 so only hour + minute need typing
  const [date, setDate] = useState(init.at ? init.at.slice(0, 10) : '')
  const [tparts, setTparts] = useState<[string, string, string]>(
    init.at
      ? [init.at.slice(11, 13), init.at.slice(14, 16), init.at.slice(17, 19) || '00']
      : ['', '', '00'],
  )
  const [timezone, setTz] = useState(init.timezone ?? '') // '' → local time, no timezone stored (§4.3)
  // §4.3 runIfMissed: checked by default for a new trigger, the stored value on an edit swap
  const [runIfMissed, setRunIfMissed] = useState(initial ? init.runIfMissed !== false : true)
  const [channel, setChannel] = useState(init.channel ?? '')
  const [secret, setSecret] = useState(init.secret ?? '')
  const [pattern, setPattern] = useState(init.pattern ?? '')
  // §9.2: mention-only by default for new triggers — a busy channel shouldn't fire on every message
  const [mention, setMention] = useState(initial ? !!init.mention : true)
  const [author, setAuthor] = useState((init.author ?? []).join(', '))
  const [from, setFrom] = useState(init.from ?? '')
  const [guide, setGuide] = useState(false)
  const [secretModal, setSecretModal] = useState(false)
  const [hh, mm, ss] = tparts
  const timeEntered = hh !== '' || mm !== ''
  const timeOk = hh !== '' && mm !== '' && ss !== '' && +hh <= 23 && +mm <= 59 && +ss <= 59
  const at = date && timeOk
    ? `${date}T${tparts.map((p) => p.padStart(2, '0')).join(':')}`
    : ''
  // §19: the live preview reads from POST /triggers/preview — no local trigger
  // math. Half-typed entries go to the endpoint as-is (an invalid one is a
  // `valid: false` result with a plain-word error, never a 422).
  // §4.3 interval dialect: an integer of at least 1, composed with the unit
  const amountOk = /^[0-9]+$/.test(amount) && Number(amount) >= 1
  const every = amountOk ? composeEvery(amount, unit) : ''
  const previewEntry: object[] = kind === 'cron'
    ? (expression.trim() ? [{ kind, expression, source: 'user', ...(timezone ? { timezone } : {}) }] : [])
    : kind === 'interval'
    ? (every ? [{ kind, every, source: 'user' }] : [])
    : kind === 'time'
    ? (at ? [{ kind, at, ...(timezone ? { timezone } : {}) }] : [])
    : []
  const [pv] = useTriggerPreview(previewEntry)
  const exprOk = kind === 'cron' && !!expression.trim() && !!pv?.valid
  const exprBad = kind === 'cron' && !!expression.trim() && !!pv && !pv.valid
  const everyOk = kind === 'interval' && !!every && !!pv?.valid
  // §9.2: an empty or zero amount reddens the input just like a rejected duration
  const everyBad = kind === 'interval' && (!amountOk || (!!pv && !pv.valid))
  const atOk = kind === 'time' && !!at && !!pv?.valid
  const atBad = kind === 'time' && !!at && !!pv && !pv.valid
  const channelOk = /^[0-9]+$/.test(channel)
  // §4.3: optional sender filter — comma-separated numeric user ids
  const authorIds = author.split(',').map((s) => s.trim()).filter(Boolean)
  const authorOk = author.trim() === ''
    || (authorIds.length > 0 && authorIds.every((a) => /^[0-9]+$/.test(a)))
  // §4.3: email, or E.164 phone after stripping obvious formatting — a
  // number without the country code could never match a stored handle.
  const fromNorm = from.includes('@') ? from.trim() : from.trim().replace(/[\s().-]/g, '')
  const fromOk = from.includes('@')
    ? !!from.trim() && !/\s/.test(from.trim())
    : /^\+[0-9]{3,15}$/.test(fromNorm)
  const canAdd = kind === 'cron' ? exprOk : kind === 'interval' ? everyOk : kind === 'time' ? atOk
    : kind === 'discord' ? channelOk && !!secret && authorOk
    : kind === 'imessage' ? fromOk : true
  const preview = kind === 'cron'
    ? (!expression.trim() || !pv ? ''
      : pv.valid ? `${pv.label}${pv.nextLabel ? ` · next: ${pv.nextLabel}` : ''}`
      : pv.error ?? '')
    : kind === 'interval'
    ? (!every || !pv ? ''
      : pv.valid ? `${pv.label}${pv.nextLabel ? ` · next: ${pv.nextLabel}` : ''}`
      : pv.error ?? '')
    : kind === 'time'
    ? (timeEntered && !timeOk ? 'Hours go 0–23, minutes and seconds 0–59'
      : !at || !pv ? ''
      : pv.valid ? pv.label
      : pv.error ?? '')
    : kind === 'discord'
    ? (channelOk ? `On Discord message in ${channel}` : (channel ? 'The channel id is numbers only' : ''))
    : kind === 'imessage'
    ? (fromOk ? `On iMessage from ${fromNorm}` : (from ? 'Needs a country code (+1…) or an email' : ''))
    : 'On app start — executes when you launch the app'
  return (
    <div style={{ border: '1px dashed var(--border-dashed)', borderRadius: 8, padding: '11px 12px' }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        {KIND_META.filter((m) => m.kind !== 'imessage' || imessageOn).map((m) => {
          const taken = m.kind === 'app_start' && hasAppStart
          return (
            <button
              key={m.kind}
              className="ad-btn-tab"
              aria-pressed={kind === m.kind}
              onClick={() => { if (!taken) setKind(m.kind) }}
              disabled={taken}
              title={taken ? 'Already added' : undefined}
              style={{ flex: 'none' }}
            >
              <i className={m.icon} style={{ fontSize: 10, marginRight: 5 }} />
              {m.label}
            </button>
          )
        })}
      </div>
      {kind === 'imessage' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
          <GuideToggle label="How iMessage triggers work" open={guide} onToggle={() => setGuide(!guide)}>
            <li>This Mac’s Messages account is the identity — no bot, no token. When the sender below texts it, the automation executes.</li>
            <li>Messages this Mac sends never trigger (loop safety) — and iMessage can’t text yourself anyway, since your own messages come from the same Apple ID. The sender must be someone else. To trigger it yourself, either create a new Apple ID, sign Messages on this Mac into it, and text that account — or use a Discord trigger instead: a bot in your own server can receive your own messages.</li>
            <li>Grant the two permissions below.</li>
            <li>Enter the sender below exactly as Messages knows them — phone numbers in international form (<b>+1…</b>), or an email. To see the stored handle, open the conversation in Messages and press the ⓘ info button. Formatting like spaces and dashes is fine — it’s stripped automatically.</li>
          </GuideToggle>
          <ImsgPermissions />
        </div>
      )}
      {kind === 'discord' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
          <GuideToggle label="New to Discord bots? Step-by-step setup" open={guide} onToggle={() => setGuide(!guide)}>
            <li>
              Open{' '}
              <a
                href="https://discord.com/developers/applications"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: 'var(--accent)' }}
              >
                discord.com/developers/applications
              </a>
              {' '}and sign in — if Discord shows an onboarding page first, skip it. Press <b>New Application</b> and give it a name.
            </li>
            <li>On its <b>Bot</b> tab press <b>Reset Token</b> and copy the token — it shows only once.</li>
            <li>Still on the Bot tab, under <b>Privileged Gateway Intents</b> turn on <b>Message Content Intent</b> — without it the bot can’t read messages.</li>
            <li>Press the <b>New secret</b> button below, paste the token as the value and <b>Save to {copy.secretStore}</b> — the secret is selected automatically.</li>
            <li>In the portal’s left sidebar click <b>OAuth2</b> and scroll to the <b>OAuth2 URL Generator</b> section.</li>
            <li>Tick the <b>bot</b> scope — a <b>Bot Permissions</b> grid appears below it. Tick <b>View Channels</b> there. If an Integration Type selector shows, leave it on <b>Guild Install</b>.</li>
            <li>Copy the <b>Generated URL</b> at the bottom and open it in your browser. Pick your server, press Continue, then <b>Authorize</b>. You need Manage Server on that server — and the bot showing offline afterwards is fine.</li>
            <li>In Discord open User Settings (gear icon), scroll the settings sidebar to the bottom and click <b>Developer</b>, then turn on <b>Developer Mode</b>. Close settings and right-click the channel → <b>Copy Channel ID</b>.</li>
            <li>Paste the channel id below, choose the bot-token secret, press <b>Add</b>.</li>
          </GuideToggle>
        </div>
      )}
      {kind === 'cron' ? (
        <input
          className={`ad-input compact mono${exprBad ? ' invalid' : ''}`}
          value={expression}
          onChange={(e) => setExpr(e.target.value)}
          placeholder="0 8 * * *   (minute hour day month weekday, Sun = 0)"
          spellCheck={false}
          style={{ width: '100%' }}
        />
      ) : kind === 'interval' ? (
        // §9.2 Interval pair: the muted "Every" word, the amount, the unit pill
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11.5, color: 'var(--text-muted)', flex: 'none' }}>Every</span>
          <input
            className={`ad-input compact mono${everyBad ? ' invalid' : ''}`}
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^0-9]/g, ''))}
            inputMode="numeric"
            spellCheck={false}
            aria-label="interval amount"
            style={{ width: 64, flex: 'none' }}
          />
          <UnitPick unit={unit} onPick={setUnit} />
        </div>
      ) : kind === 'time' ? (
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            className={`ad-input compact mono${atBad ? ' invalid' : ''}`}
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            style={{ colorScheme: 'dark' }}
          />
          <TimeParts
            parts={tparts}
            invalid={(timeEntered && !timeOk) || atBad}
            onChange={setTparts}
          />
        </div>
      ) : kind === 'discord' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input
            className={`ad-input compact mono${channel && !channelOk ? ' invalid' : ''}`}
            value={channel}
            onChange={(e) => setChannel(e.target.value.trim())}
            placeholder="Channel id (numbers — right-click the channel → Copy Channel ID)"
            spellCheck={false}
            style={{ width: '100%' }}
          />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <SecretPick secrets={secrets} selected={secret} onPick={setSecret} />
            <button className="ad-btn-accent-ghost small" onClick={() => setSecretModal(true)} style={{ flex: 'none' }}>
              <i className="fa-solid fa-plus" style={{ fontSize: 10, marginRight: 5 }} />
              New secret
            </button>
          </div>
          {secretModal && (
            <SecretModal
              modal={{ mode: 'add' }}
              onClose={() => setSecretModal(false)}
              onSaved={(saved) => setSecret(saved.id)}
            />
          )}
          <input
            className="ad-input compact"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder="Message filter — only messages containing… (optional)"
            title="Fires only when the message contains this text — case-insensitive, plain substring"
            spellCheck={false}
            style={{ width: '100%' }}
          />
          <input
            className={`ad-input compact mono${author && !authorOk ? ' invalid' : ''}`}
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            placeholder="Sender filter — only messages from these user ids (optional)"
            spellCheck={false}
            style={{ width: '100%' }}
          />
          <div style={{ fontSize: 11.5, color: 'var(--text-muted)', lineHeight: 1.45, marginTop: -2 }}>
            Fires only on messages from these Discord users — comma-separate several ids.
            A user id is a long number like 234567890123456789 — right-click their name →
            Copy User ID (needs Developer Mode, enabled in step 8).
          </div>
          <label style={{
            display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, width: 'fit-content',
            color: 'var(--text-2)', cursor: 'pointer', userSelect: 'none',
          }}>
            <input type="checkbox" checked={mention} onChange={(e) => setMention(e.target.checked)} />
            Only when the bot is mentioned
          </label>
        </div>
      ) : kind === 'imessage' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input
            className={`ad-input compact mono${from && !fromOk ? ' invalid' : ''}`}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            placeholder="Sender — +15551234567 or an email"
            spellCheck={false}
            style={{ width: '100%' }}
          />
          <input
            className="ad-input compact"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder="Message filter — only messages containing… (optional)"
            title="Fires only when the message contains this text — case-insensitive, plain substring"
            spellCheck={false}
            style={{ width: '100%' }}
          />
        </div>
      ) : null}
      {(kind === 'cron' || kind === 'time') && <TzPick timezone={timezone} onPick={setTz} />}
      {(kind === 'cron' || kind === 'interval' || kind === 'time') && (
        // §9.2 "Catch up if missed": the §4.3 runIfMissed field (§6 wake catch-up)
        <label style={{
          display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, width: 'fit-content',
          color: 'var(--text-2)', cursor: 'pointer', userSelect: 'none', marginTop: 8,
        }}>
          <input type="checkbox" checked={runIfMissed} onChange={(e) => setRunIfMissed(e.target.checked)} />
          Catch up if missed
        </label>
      )}
      {(kind === 'cron' || kind === 'interval' || kind === 'time') && (
        // §9.2 / §3 sleep disclaimer, one note: the first sentence follows the checkbox
        <div style={{ fontSize: 11.5, color: 'var(--text-muted)', lineHeight: 1.45, marginTop: 6 }}>
          If this {copy.machine} sleeps through a scheduled time,{' '}
          {runIfMissed ? 'the automation executes once when it wakes.' : 'that time is skipped.'}
          {' '}{copy.sleepMissNote}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 9 }}>
        <span style={{
          flex: 1, minWidth: 0, fontFamily: 'var(--mono)', fontSize: 11,
          color: canAdd ? 'var(--accent)' : 'var(--red-text)',
        }}>
          {preview}
        </span>
        <button
          className="ad-btn-accent-ghost small"
          onClick={() => {
            onSave(kind === 'app_start' ? { kind }
              : kind === 'discord' ? {
                  kind, channel, secret,
                  ...(pattern.trim() ? { pattern: pattern.trim() } : {}),
                  ...(mention ? { mention: true } : {}),
                  ...(authorIds.length ? { author: [...new Set(authorIds)].sort() } : {}),
                }
              : kind === 'imessage' ? {
                  kind, from: fromNorm,
                  ...(pattern.trim() ? { pattern: pattern.trim() } : {}),
                }
              // §4.3: a hand-set interval is user-sourced too, and carries no
              // timezone — a duration has no wall clock
              : kind === 'interval'
                ? { kind, every, source: 'user' as const, ...(runIfMissed ? {} : { runIfMissed: false }) }
              // §4.3 provenance: a hand-set cron is user-sourced — it
              // survives later syncs' cron-subset replace
              : {
                  ...(kind === 'cron' ? { kind, expression: expression.trim(), source: 'user' as const } : { kind, at }),
                  ...(timezone ? { timezone } : {}),
                  // §4.3: carried only when off; absent reads as true everywhere
                  ...(runIfMissed ? {} : { runIfMissed: false }),
                })
          }}
          disabled={!canAdd}
          style={{ flex: 'none' }}
        >
          {initial ? 'Save' : 'Add'}
        </button>
        <button className="ad-btn-text dim" onClick={onCancel} style={{ flex: 'none' }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

export function AddTrigger({ hasAppStart, onAdd }: { hasAppStart: boolean; onAdd: (t: TriggerDraft) => void }) {
  const [open, setOpen] = useState(false)
  if (!open) {
    return (
      <button className="ad-btn-dashed" onClick={() => setOpen(true)} style={{ marginTop: 9 }}>
        <i className="fa-solid fa-plus" style={{ fontSize: 10 }} /> Add trigger
      </button>
    )
  }
  return (
    <div className="ad-anim-item" style={{ marginTop: 10 }}>
      <TriggerEditor
        hasAppStart={hasAppStart}
        onSave={(t) => { onAdd(t); setOpen(false) }}
        onCancel={() => setOpen(false)}
      />
    </div>
  )
}
