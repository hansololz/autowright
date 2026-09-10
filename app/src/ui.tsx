// Shared UI primitives — one source of truth for badges, toggles, radios,
// popovers, toasts (prototype Component helpers, §14 tokens). The result
// section and its views live in result.tsx.
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ParamDef, Status, Step } from './types'
import { useStore } from './store'
import { devlogOverlayOpen } from './devlog'
import awMark from '../electron/icon/icon.svg'

export const P = {
  accent: 'var(--accent)', accentBg: 'var(--accent-bg)',
  green: 'var(--green)', greenBg: 'var(--green-bg)',
  cyan: 'var(--cyan)', cyanBg: 'var(--cyan-bg)',
  red: 'var(--red)', redBg: 'var(--red-bg)',
  amber: 'var(--amber)', amberBg: 'var(--amber-bg)',
  orange: 'var(--orange)', orangeBg: 'var(--orange-bg)',
  gray: 'var(--gray)', grayBg: 'var(--gray-bg)',
  magenta: 'var(--magenta)', magentaBg: 'var(--magenta-bg)',
}

export function badgeOf(status: Status | string): { label: string; c: string; bg: string } {
  const map: Record<string, [string, string, string]> = {
    queued: ['Queued', P.gray, P.grayBg],
    executing: ['Executing', P.cyan, P.cyanBg],
    succeeded: ['Succeeded', P.green, P.greenBg],
    failed: ['Failed', P.red, P.redBg],
    cancelled: ['Cancelled', P.gray, P.grayBg],
    skipped: ['Skipped', P.gray, P.grayBg],
    interrupted: ['Interrupted', P.magenta, P.magentaBg],
    none: ['Not executed yet', P.gray, P.grayBg],
  }
  const b = map[status] ?? map.none
  return { label: b[0], c: b[1], bg: b[2] }
}

/** §7 how long a §6 firing has waited in the queue — the executions list's
 * QUEUED FOR column and the queued execution page's metadata line. Whole
 * seconds: both tick once a second, so tenths would only ever flicker. */
export function waitedLabel(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

/** §11 per-step duration stamp — the backend `timefmt.dur_label` shape
 * ("0.4s" / "1.4s" / "2m 13s"), so the thread's step durations read like the
 * §7 execution steps' backend-formatted ones. */
export function durationLabel(ms: number): string {
  const s = Math.max(0, ms) / 1000
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`
}

/** Uppercase mono chip — the one badge geometry. Status `Badge` maps onto it;
 * use directly for ad-hoc labels (Draft, OFF, Ready, NOT SET…). */
export function MiniBadge({ children, c, bg, style }: {
  children: React.ReactNode; c?: string; bg?: string; style?: React.CSSProperties
}) {
  return (
    <span style={{
      fontFamily: 'var(--mono)', fontSize: 10.5, fontWeight: 600, textTransform: 'uppercase',
      letterSpacing: '.06em', color: c ?? 'var(--text-muted)',
      background: bg ?? 'var(--hairline)', padding: '3px 9px',
      borderRadius: 6, whiteSpace: 'nowrap', ...style,
    }}>
      {children}
    </span>
  )
}

export function Badge({ status, style }: { status: Status | string; style?: React.CSSProperties }) {
  const b = badgeOf(status)
  return <MiniBadge c={b.c} bg={b.bg} style={style}>{b.label}</MiniBadge>
}

/** §14 step-row info tag (agent / secret / timeout tags) — one geometry for the
 * create-flow review, detail-page STEPS card, and import preview alike.
 * `title` renders the §14 Tag tooltip — a custom bubble, never the native
 * `title` attribute: Chromium suppresses native tooltips after clicks and
 * scrolls (both constant on the step rows), so they read as broken. */
export function Tag({ icon, children, c, title, style }: {
  icon?: string; children: React.ReactNode; c?: string; title?: string; style?: React.CSSProperties
}) {
  const anchor = useRef<HTMLSpanElement>(null)
  const bubble = useRef<HTMLDivElement>(null)
  const timer = useRef<number | null>(null)
  const [tip, setTip] = useState<{ x: number; y: number; below: boolean } | null>(null)
  const hide = () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    setTip(null)
  }
  const enter = () => {
    if (!title) return
    timer.current = window.setTimeout(() => {
      const r = anchor.current?.getBoundingClientRect()
      if (!r) return
      const below = r.top < 46
      setTip({ x: r.left + r.width / 2, y: below ? r.bottom + 7 : r.top - 7, below })
    }, 200)
  }
  // Clamp the centered bubble 8 px inside the window's left/right edges once
  // its width is measurable.
  useLayoutEffect(() => {
    if (!tip || !bubble.current) return
    const half = bubble.current.offsetWidth / 2
    const x = Math.min(Math.max(tip.x, 8 + half), window.innerWidth - 8 - half)
    if (x !== tip.x) setTip({ ...tip, x })
  }, [tip])
  // Any scroll leaves the fixed-position bubble floating over stale
  // coordinates — hide instead of tracking.
  useEffect(() => {
    if (!tip) return
    const off = () => setTip(null)
    window.addEventListener('scroll', off, true)
    return () => window.removeEventListener('scroll', off, true)
  }, [tip])
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  return (
    <span
      ref={anchor}
      aria-label={title}
      onMouseEnter={enter}
      onMouseLeave={hide}
      onMouseDown={hide}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 500,
        color: c ?? 'var(--text-muted)', background: 'var(--bg-inset)',
        border: '1px solid var(--hairline)', borderRadius: 6, padding: '2px 8px',
        whiteSpace: 'nowrap', ...style,
      }}
    >
      {icon && <i className={`fa-solid ${icon}`} style={{ fontSize: 9 }} />}
      {children}
      {title && tip && createPortal(
        <div style={{
          position: 'fixed', left: tip.x, top: tip.y, zIndex: 120, pointerEvents: 'none',
          transform: `translate(-50%, ${tip.below ? '0' : '-100%'})`,
        }}>
          <div ref={bubble} role="tooltip" style={{
            background: 'var(--bg-menu)', border: '1px solid var(--border-input)',
            borderRadius: 10, boxShadow: 'var(--shadow-menu)', padding: '6px 10px',
            font: '400 11.5px/1.5 var(--sans)', color: 'var(--text-2)',
            maxWidth: 320, width: 'max-content',
            animation: ENTER_UP,
          }}>
            {title}
          </div>
        </div>,
        document.body,
      )}
    </span>
  )
}

export function Logo({ size = 26 }: { size?: number }) {
  // icon.svg's mark spans 824 of its 1024 canvas — oversize and clip so it renders full-bleed.
  const over = size * (1024 / 824)
  return (
    <span style={{
      width: size, height: size, overflow: 'hidden',
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
    }}>
      <img src={awMark} alt="" style={{ width: over, height: over, maxWidth: 'none', flex: 'none', display: 'block' }} />
    </span>
  )
}

/** §14 lowercase mono metadata chip — trigger chips (list + detail lede), tinted
 * result chips ("5 of 6 checked"), execution-page outcome chips. One geometry;
 * `c`/`bg` tint it. Never hand-built at a call site. */
export function MetaChip({ children, c, bg, style }: {
  children: React.ReactNode; c?: string; bg?: string; style?: React.CSSProperties
}) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 500, color: c ?? 'var(--text-muted)',
      background: bg ?? 'var(--hairline-dim)', padding: '3px 8px', borderRadius: 6,
      whiteSpace: 'nowrap', ...style,
    }}>
      {children}
    </span>
  )
}

export function resultChipColors(status: 'changes' | 'ok' | 'attention' | null | undefined): { c: string; bg: string } {
  if (status === 'changes') return { c: P.accent, bg: P.accentBg }
  // §14: attention-flavored result chips use the dedicated chip orange, not status amber.
  if (status === 'attention') return { c: P.orange, bg: P.orangeBg }
  return { c: P.green, bg: P.greenBg }
}

// §7 failure diagnostics — red-tinted notice for a failed execution: the failing
// step, a possible reason when the engine classified one, and the error message.
// Shown on the automation detail page (§9.2) and the execution page (§7).
// `onFix` renders the quiet §7 "Fix with AI" button (failed non-test executions
// whose automation still exists — it opens the §11 editor and starts the analysis).
export function FailureNotice({ error, onView, onFix, style }: {
  error: { step: string | null; message: string; reason: string | null }
  onView?: () => void
  onFix?: () => void
  style?: React.CSSProperties
}) {
  return (
    <Notice tone="red" size="card" className="ad-anim-item" style={style}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <i className="fa-solid fa-circle-exclamation" style={{ color: 'var(--red)', fontSize: 12 }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--red-text)' }}>
          {error.step ? `Failed at step “${error.step}”` : 'Execution failed'}
        </span>
        <div style={{ flex: 1 }} />
        {onFix && (
          <button className="ad-btn-soft" onClick={onFix} title="Opens the editor — your AI analyzes the failure and suggests a fix">
            <i className="fa-solid fa-wand-magic-sparkles" style={{ fontSize: 10 }} /> Fix with AI
          </button>
        )}
        {onView && (
          <button className="ad-btn-text" onClick={onView}>
            View execution <i className="fa-solid fa-chevron-right" style={{ fontSize: 9 }} />
          </button>
        )}
      </div>
      {error.reason && (
        <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-2)', marginTop: 7 }}>
          {error.reason}
        </div>
      )}
      <div style={{
        fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.6, color: 'var(--text-muted)',
        marginTop: error.reason ? 5 : 7, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {error.message}
      </div>
    </Notice>
  )
}

// §7 / §9.2 flagged result — orange notice (the chip orange, §14 — never the amber
// needs-fixing tone) for a SUCCEEDED execution whose step
// set `result.status('attention')`: the chip text (what looks off and why, per
// the §8 build instructions) and one plain sentence. Pure rendering of
// the stored §4.5 chipStatus — the engine never judges a result (§6) — and no
// button: nothing failed, so there is nothing to fix. Shown on the execution
// page in the failure notice's slot and above the §9.2 LATEST RESULT card.
export function FlaggedResultNotice({ chip, onView, style }: {
  chip?: string | null
  onView?: () => void
  style?: React.CSSProperties
}) {
  return (
    <Notice tone="orange" size="card" className="ad-anim-item" style={style} testId="flagged-result-notice">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <i className="fa-solid fa-triangle-exclamation" style={{ color: 'var(--orange)', fontSize: 12 }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--orange)' }}>
          This execution flagged its result
        </span>
        <div style={{ flex: 1 }} />
        {onView && (
          <button className="ad-btn-text" onClick={onView}>
            View execution <i className="fa-solid fa-chevron-right" style={{ fontSize: 9 }} />
          </button>
        )}
      </div>
      {chip && (
        <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text)', fontWeight: 500, marginTop: 7 }}>
          {chip}
        </div>
      )}
      <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-2)', marginTop: chip ? 3 : 7 }}>
        The automation finished, but its own check thinks the result looks off.
      </div>
    </Notice>
  )
}

/** §14 tinted notice banner — the one renderer. `slim` (default): radius 10,
 * `11px 14px`, 7 px leading dot before `children`. `card`: radius 12,
 * `14px 18px`, no dot (FailureNotice, the §9.2 needs-fixing and draft banners).
 * `dashed` swaps the border style (the draft banner). */
export function Notice({ tone, size = 'slim', dashed, className, style, children, testId }: {
  tone: 'red' | 'amber' | 'orange' | 'accent' | 'cyan'; size?: 'slim' | 'card'; dashed?: boolean
  className?: string; style?: React.CSSProperties; children: React.ReactNode; testId?: string
}) {
  const dot = { red: 'var(--red)', amber: 'var(--amber)', orange: 'var(--orange)', accent: 'var(--accent)', cyan: 'var(--cyan)' }[tone]
  const card = size === 'card'
  return (
    <div className={className} data-testid={testId} style={{
      background: `var(--notice-${tone}-bg)`,
      border: `1px ${dashed ? 'dashed' : 'solid'} var(--notice-${tone}-border)`,
      borderRadius: card ? 12 : 10, padding: card ? '14px 18px' : '11px 14px',
      ...(card ? {} : { display: 'flex', alignItems: 'center', gap: 10 }),
      fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-2)',
      ...style,
    }}>
      {!card && <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flex: 'none' }} />}
      {card ? children : <div style={{ flex: 1, minWidth: 0 }}>{children}</div>}
    </div>
  )
}

/** §14 in-card empty line — an empty list inside a card that has content around
 * it (no triggers, no snapshots, no files). 12.5 muted, the card's row inset. */
export function EmptyLine({ children, style, testId }: {
  children: React.ReactNode; style?: React.CSSProperties; testId?: string
}) {
  return (
    <div data-testid={testId} style={{ padding: '14px 18px', fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-muted)', ...style }}>
      {children}
    </div>
  )
}

/** §14 page/modal-body loading well — the one bare-spinner treatment. */
export function PageLoading({ style }: { style?: React.CSSProperties }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0', ...style }}>
      <Spinner size={24} />
    </div>
  )
}

/** §14 one-line status with a leading glyph — success (green check), warning
 * (amber triangle) or failure (red exclamation). Replaces hand-built icon+label
 * rows (Ollama detected, test succeeded / failed). */
export function StatusLine({ tone, label, style }: {
  tone: 'green' | 'amber' | 'red'; label: React.ReactNode; style?: React.CSSProperties
}) {
  const icon = { green: 'fa-check', amber: 'fa-triangle-exclamation', red: 'fa-circle-exclamation' }[tone]
  const color = { green: 'var(--green)', amber: 'var(--amber)', red: 'var(--red-text)' }[tone]
  return (
    <div className="ad-anim-item" style={{ display: 'flex', alignItems: 'center', gap: 8, ...style }}>
      <i className={`fa-solid ${icon}`} style={{ color, fontSize: 13 }} />
      <span style={{ fontWeight: 500, fontSize: 13, color }}>{label}</span>
    </div>
  )
}

/** §14 two-line popover row (version menus, agent picker). `onPick` makes it a
 * pickable row with a 14 px check column; `header` renders the inert current
 * row on the `--wash-hover` ground; `trailing` nests a row action (then the row
 * is the §9 role="button" carve-out). */
export function MenuItemRow({ title, sub, mono, subMono, selected, header, onPick, trailing, testId, style }: {
  title: React.ReactNode; sub?: React.ReactNode; mono?: boolean; subMono?: boolean
  selected?: boolean; header?: boolean; onPick?: () => void; trailing?: React.ReactNode
  testId?: string; style?: React.CSSProperties
}) {
  const body = (
    <>
      {onPick && (
        <span style={{ width: 14, flex: 'none', textAlign: 'center', color: 'var(--accent)' }}>
          {selected ? <i className="fa-solid fa-check" style={{ fontSize: 10 }} /> : ''}
        </span>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ font: `600 12.5px var(${mono ? '--mono' : '--sans'})`, color: header || selected ? 'var(--text)' : 'var(--text-2)' }}>{title}</div>
        {sub && <div style={{ font: `400 11.5px/1.45 var(${subMono ? '--mono' : '--sans'})`, color: 'var(--text-muted)', marginTop: 1 }}>{sub}</div>}
      </div>
      {trailing}
    </>
  )
  const base: React.CSSProperties = {
    display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px',
    borderBottom: '1px solid var(--hairline-dim)',
    ...(header ? { background: 'var(--wash-hover)' } : {}),
    // no inline background when unselected — .ad-hover-row's hover tint must win
    ...(selected ? { background: 'var(--accent-hint-bg)' } : {}),
    ...style,
  }
  if (!onPick) return <div data-testid={testId} style={base}>{body}</div>
  if (trailing) {
    return (
      <div
        className="ad-btn-bare ad-hover-row" role="button" tabIndex={0} data-testid={testId}
        onClick={onPick}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick() } }}
        style={{ ...base, cursor: 'pointer' }}
      >
        {body}
      </div>
    )
  }
  return (
    <button className="ad-btn-bare ad-hover-row" data-testid={testId} onClick={onPick} style={{ ...base, cursor: 'pointer' }}>
      {body}
    </button>
  )
}

export function Eyebrow({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 600, letterSpacing: '.09em',
      textTransform: 'uppercase', color: 'var(--text-faint)', ...style,
    }}>
      {children}
    </div>
  )
}

// §14 motion: the one executing pulse (badges, dots) and the shared
// enter/exit pair for two-way surfaces (menus, toasts; Modal composes its own).
export const PULSE = 'adPulse 1.4s ease-in-out infinite'
export const BLINK = 'adBlink 1s step-end infinite'
const ENTER_UP = 'adFadeUp var(--t-enter) var(--ease-enter) both'
const EXIT_DOWN = 'adFadeOutDown var(--t-exit) var(--ease-exit) both'

export function Spinner({ size = 16, color, style }: {
  size?: number; color?: string; style?: React.CSSProperties
}) {
  return (
    <span style={{
      display: 'inline-block', width: size, height: size, borderRadius: '50%',
      border: '2px solid var(--spinner-track)', borderTopColor: color ?? 'var(--accent)',
      animation: 'adSpin .85s linear infinite', ...style,
    }} />
  )
}

/** §14 the one progress bar — `percent: null` renders the indeterminate sliding bar. */
export function ProgressBar({ percent }: { percent: number | null }) {
  return (
    <div style={{ height: 4, borderRadius: 2, background: 'var(--wash-track)', overflow: 'hidden' }}>
      {percent !== null ? (
        <div style={{ height: '100%', background: 'var(--accent)', width: `${Math.round(percent)}%`, transition: 'width var(--t-enter) var(--ease-enter)' }} />
      ) : (
        <div style={{ height: '100%', width: '30%', background: 'var(--accent)', animation: 'adBarSlide 1.2s ease-in-out infinite' }} />
      )}
    </div>
  )
}

/**
 * §14 the one copy-a-command surface (§4.9 CLI PATH row, §9.4 Homebrew update
 * notice): mono inset box + Copy button that puts the exact command on the
 * clipboard and toasts. The command wraps instead of truncating — it must stay
 * fully readable at any width. Clipboard failures are silent.
 */
export function CommandBlock({ command }: { command: string }) {
  const showToast = useStore((s) => s.showToast)
  return (
    <div style={{
      marginTop: 10, background: 'var(--bg-inset)', border: '1px solid var(--hairline)',
      borderRadius: 8, padding: '5px 6px 5px 11px', font: `400 11.5px var(--mono)`,
      color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 10,
    }}>
      <span style={{ flex: 1, whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{command}</span>
      <button
        className="ad-btn-soft"
        style={{ flex: 'none' }}
        onClick={() => {
          void navigator.clipboard.writeText(command)
            .then(() => showToast('Copied to clipboard.'))
            .catch(() => {})
        }}
      >
        Copy
      </button>
    </div>
  )
}

/** §14 dashed-card empty state with CTA (automations / agents / secrets lists). */
export function EmptyState({ text, cta }: { text: React.ReactNode; cta?: React.ReactNode }) {
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px dashed var(--border-dashed)', borderRadius: 12,
      padding: '36px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center',
      gap: 12, textAlign: 'center',
    }}>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text-muted)', maxWidth: 420 }}>
        {text}
      </p>
      {cta}
    </div>
  )
}

/** §14 dashed-card notice: title 13.5/500 + muted body (empty lists, gone pages). */
export function EmptyNotice({ title, body, style }: {
  title?: React.ReactNode; body: React.ReactNode; style?: React.CSSProperties
}) {
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px dashed var(--border-dashed)',
      borderRadius: 12, padding: 22, textAlign: 'center', ...style,
    }}>
      {title && <div style={{ fontSize: 13.5, fontWeight: 500, marginBottom: 4 }}>{title}</div>}
      <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{body}</div>
    </div>
  )
}

/** §14 inline busy row — the one spinner-beside-label treatment. */
export function LoadingRow({ label, style }: { label: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, ...style }}>
      <Spinner size={13} />
      <span style={{ fontWeight: 500, fontSize: 12.5, color: 'var(--text-muted)' }}>{label}</span>
    </div>
  )
}

/** §9 page back link — chevron + label, above the page title. */
export function BackLink({ label, onClick, style }: {
  label: React.ReactNode; onClick: () => void; style?: React.CSSProperties
}) {
  return (
    <button className="ad-btn-text" onClick={onClick} style={{ display: 'block', marginBottom: 10, ...style }}>
      <i className="fa-solid fa-chevron-left" style={{ fontSize: 10 }} /> {label}
    </button>
  )
}

/** §9/§19 `agentInstall` gating: each provider's own install page — where an
 * OS can't run the install (or the Terminal sign-in), the card's action is
 * replaced by a line linking the provider's name here. */
const VENDOR_INSTALL: Record<string, { name: string; url: string }> = {
  claude: { name: 'Claude Code', url: 'https://claude.com/product/claude-code' },
  gemini: { name: 'Gemini CLI', url: 'https://github.com/google-gemini/gemini-cli' },
  codex: { name: 'Codex', url: 'https://developers.openai.com/codex/cli' },
  opencode: { name: 'OpenCode', url: 'https://opencode.ai' },
  ollama: { name: 'Ollama', url: 'https://ollama.com/download' },
}

/** §9/§10/§12: the one plain line that stands in for a hidden Install /
 * Sign in action while `capabilities.agentInstall` is false. Detection and
 * sign-in state keep working — only the action is gone. `signin` picks the
 * sign-in wording for a provider that is installed but signed out. The link
 * opens in the browser through the §9.4 external-URL policy (window-open
 * handler). */
export function ManualInstallLine({ id, name, signin, style }: {
  id: string; name?: string; signin?: boolean; style?: React.CSSProperties
}) {
  const vendor = VENDOR_INSTALL[id]
  const label = name ?? vendor?.name ?? id
  const linked = vendor
    ? (
      <a href={vendor.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)' }}>
        {label}
      </a>
    )
    : label
  return (
    <div
      data-testid="manual-install-line"
      style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-muted)', ...style }}
    >
      {signin
        ? <>Sign in to {linked} from a terminal, then come back — Autowright detects it automatically.</>
        : <>Install {linked} by hand, then come back — Autowright detects it automatically.</>}
    </div>
  )
}

export function Toggle({ on, onChange, disabled, title }: {
  on: boolean; onChange: (v: boolean) => void; disabled?: boolean; title?: string
}) {
  return (
    <button
      onClick={() => !disabled && onChange(!on)}
      title={title}
      role="switch"
      aria-checked={on}
      aria-label={typeof title === 'string' ? title : undefined}
      disabled={disabled}
      style={{
        width: 36, height: 21, borderRadius: 11, position: 'relative', flex: 'none',
        border: '1px solid var(--wash-track)',
        background: on ? 'var(--accent)' : 'var(--border-dashed)',
        transition: 'background var(--t-hover) var(--ease-enter)', opacity: disabled ? 0.5 : 1,
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: 2, width: 15, height: 15, borderRadius: '50%',
        background: 'var(--text)', transition: 'transform var(--t-hover) var(--ease-enter)',
        transform: on ? 'translateX(15px)' : 'translateX(0)',
      }} />
    </button>
  )
}

/** The §14 checkbox glyph for rows that are themselves the button (`role="checkbox"` +
 *  `aria-checked` on the row); standalone checkboxes are native inputs styled by the same rule. */
export function CheckBox({ on }: { on: boolean }) {
  return <span className="ad-check" data-on={on || undefined} aria-hidden="true" />
}

export function RadioRing({ selected, size = 16 }: { selected: boolean; size?: number }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: '50%', flex: 'none',
      border: `1.5px solid ${selected ? 'var(--accent)' : 'var(--border-hover)'}`,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      transition: 'border-color var(--t-hover) var(--ease-enter)',
    }}>
      <span style={{
        width: size - 8, height: size - 8, borderRadius: '50%', background: selected ? 'var(--accent)' : 'transparent',
        transform: selected ? 'scale(1)' : 'scale(.4)',
        transition: 'transform var(--t-hover) var(--ease-enter), background var(--t-hover) var(--ease-enter)',
      }} />
    </span>
  )
}

export function BtnPrimary({ children, onClick, disabled, title, style, btnRef }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; title?: string
  style?: React.CSSProperties; btnRef?: React.Ref<HTMLButtonElement>
}) {
  return (
    <button ref={btnRef} className="ad-btn-primary" onClick={onClick} disabled={disabled} title={title} style={style}>
      {children}
    </button>
  )
}

export function BtnGhost({ children, onClick, danger, disabled, title, style }: {
  children: React.ReactNode; onClick?: () => void; danger?: boolean; disabled?: boolean
  title?: string; style?: React.CSSProperties
}) {
  return (
    <button className={`ad-btn-ghost${danger ? ' danger' : ''}`} onClick={onClick} disabled={disabled} title={title} style={style}>
      {children}
    </button>
  )
}

/** Popover that closes on outside mousedown (§9). Render children absolutely inside. */
export function usePopover(): [boolean, (v: boolean) => void, React.RefObject<HTMLDivElement | null>] {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown, true)
    return () => document.removeEventListener('mousedown', onDown, true)
  }, [open])
  return [open, setOpen, ref]
}

export const menuStyle: React.CSSProperties = {
  position: 'absolute', zIndex: 60, background: 'var(--bg-menu)',
  border: '1px solid var(--border-input)', borderRadius: 10,
  boxShadow: 'var(--shadow-menu)', padding: 5, minWidth: 200,
}

/** §14 two-way popover menu: pass `show` instead of conditional rendering —
 * the menu plays the exit before unmounting. Position via `style`. */
export function PopMenu({ show, style, children }: {
  show: boolean; style?: React.CSSProperties; children: React.ReactNode
}) {
  const [mounted, setMounted] = useState(show)
  useEffect(() => {
    if (show) { setMounted(true); return }
    // Unmount even if animationend never fires (same fallback as Modal and
    // BlockingOverlay): an interrupted exit would otherwise leave an
    // invisible zIndex-60 node parked over the surface, swallowing clicks.
    const t = setTimeout(() => setMounted(false), 200)
    return () => clearTimeout(t)
  }, [show])
  if (!mounted) return null
  return (
    <div
      onAnimationEnd={(e) => { if (!show && e.target === e.currentTarget) setMounted(false) }}
      style={{ ...menuStyle, animation: show ? ENTER_UP : EXIT_DOWN, ...style }}
    >
      {children}
    </div>
  )
}

/** §14 collapsible body: content stays mounted; height animates via the
 * .ad-collapse grid-rows transition. */
export function Collapse({ open, children }: { open: boolean; children: React.ReactNode }) {
  return (
    <div className={`ad-collapse${open ? ' open' : ''}`}>
      <div>{children}</div>
    </div>
  )
}

/** §14 rotating header caret — a single fa-caret-down glyph; `openDeg`/`closedDeg`
 * give its rotation per state (defaults 0/-90: down when open, right when closed). */
export function Caret({ open, openDeg = 0, closedDeg = -90, style }: {
  open: boolean; openDeg?: number; closedDeg?: number; style?: React.CSSProperties
}) {
  return (
    <i
      className="fa-solid fa-caret-down ad-caret"
      style={{ fontSize: 10, transform: `rotate(${open ? openDeg : closedDeg}deg)`, ...style }}
    />
  )
}

export function MenuRow({ children, onClick, danger, active }: {
  children: React.ReactNode; onClick?: () => void; danger?: boolean; active?: boolean
}) {
  return (
    <button className={`ad-btn-bare ad-menu-row${danger ? ' danger' : ''}${active ? ' active' : ''}`} onClick={onClick}>
      {children}
    </button>
  )
}

/** One-line read-only summary of a parameter's value (§4.2).
 * Works on merged params (server `on`/`lines`/`value`) and on raw definitions
 * (draft payloads in the Review screen) by falling back to the default. */
export function paramSummary(p: ParamDef): string {
  if (p.kind === 'toggle') return (p.on ?? p.default === true) ? 'On' : 'Off'
  if (p.kind === 'list') {
    const lines = p.lines ?? (Array.isArray(p.default) ? (p.default as string[]) : [])
    return p.validate
      ? `${lines.filter((l) => l.trim() && validUrl(l)).length} links`
      : `${lines.filter((l) => l.trim()).length} entries`
  }
  if (p.kind === 'kv') {
    const rows = p.rows ?? (Array.isArray(p.default) ? (p.default as { k: string; v: string }[]) : [])
    return `${rows.length} entries`
  }
  if (p.kind === 'number') return String(p.value ?? p.default ?? p.min ?? 0)
  const text = p.value ?? p.default ?? ''
  return String(text || 'Not set')
}

export function validUrl(s: string): boolean {
  return /^https?:\/\/\S+\.\S+/.test(s.trim())
}

// §7: the 409 no-free-slot toast — identical wherever an execute action can hit
// an automation with every §6 slot busy. The default (maxParallel 1, no queue)
// keeps the original copy; anything else says what actually happens next, since
// "one at a time" and "would be skipped" are both wrong once a queue exists.
const EXECUTING_TOAST = 'Already executing — one execution at a time. A trigger firing now would be skipped.'

export function executingToast(maxParallel: number, maxQueued: number): string {
  if (maxParallel <= 1 && maxQueued <= 0) return EXECUTING_TOAST
  const slots = maxParallel === 1 ? 'The slot is busy' : `All ${maxParallel} slots are busy`
  return maxQueued > 0
    ? `${slots}. A trigger firing now would be queued.`
    : `${slots}. A trigger firing now would be skipped.`
}

// §6 default per-step timeout (seconds) — mirrors the engine's STEP_TIMEOUT.
const STEP_TIMEOUT_DEFAULT = 900

// §9.2 clock-tag label: hours when divisible by 3600, else minutes when
// divisible by 60, else seconds; "no limit" for noTimeout steps.
export function stepTimeoutLabel(s: Step): string {
  if (s.noTimeout) return 'no limit'
  const secs = s.timeout ?? STEP_TIMEOUT_DEFAULT
  if (secs % 3600 === 0) return `${secs / 3600}h`
  if (secs % 60 === 0) return `${secs / 60}m`
  return `${secs}s`
}

export function stepTimeoutTitle(s: Step): string {
  return s.noTimeout
    ? 'No time limit — this step runs until it finishes or you cancel or skip it'
    : `This step is stopped if it runs longer than ${stepTimeoutLabel(s)}`
}

// §9.2 retry-tag label: "1 retry" / "<n> retries", "infinite retries" for
// infiniteRetries steps; null when the step sets neither (the 0 default), so
// no tag renders.
export function stepRetriesLabel(s: Step): string | null {
  if (s.infiniteRetries) return 'infinite retries'
  const n = s.retries ?? 0
  if (n <= 0) return null
  return n === 1 ? '1 retry' : `${n} retries`
}

export function stepRetriesTitle(s: Step): string {
  if (s.infiniteRetries) return 'If this step fails it runs again until it succeeds, or you cancel or skip it'
  const n = s.retries ?? 0
  return `If this step fails it runs again, up to ${n} more ${n === 1 ? 'time' : 'times'}`
}

/** Display label for an agent's model — a null model means the harness's own
 *  configured default (§4.7). */
export function dispModel(ag: { model: string | null }): string {
  return ag.model ?? 'Default model'
}

/** An agent's display name — the user's name, else its harness name (§4.7). */
export function agName(ag: { name: string | null; harness: string }): string {
  return ag.name || ag.harness
}

/** One color per log kind (§7 log views — execution page and §11 test log alike). */
export function logColor(k: string): string {
  if (k === 'sys') return 'var(--text-faint)'
  if (k === 'wrn') return 'var(--amber)'
  if (k === 'err') return 'var(--red)'
  return 'var(--text-2)'
}

/** §4.3 countdown "next in Xd Xh" / "Xh Xm" from the backend-derived `nextAtMs`. */
export function nextIn(a: { nextAtMs: number | null }): string {
  if (a.nextAtMs == null) return ''
  const mTot = Math.max(1, Math.round((a.nextAtMs - Date.now()) / 60000))
  const dd = Math.floor(mTot / 1440)
  const hh = Math.floor((mTot % 1440) / 60)
  const mm = mTot % 60
  return dd > 0 ? `${dd}d ${hh}h` : `${hh}h ${mm}m`
}

export function Toast({ msg }: { msg: string | null }) {
  // Keep the last message mounted while `msg` clears so the exit can play.
  const [shown, setShown] = useState(msg)
  useEffect(() => { if (msg) setShown(msg) }, [msg])
  if (!shown) return null
  const closing = !msg
  return (
    // Centered with left/right+margin, not translateX — adFadeUp animates
    // `transform`, which would override the centering while it plays.
    <div
      key={shown}
      onAnimationEnd={(e) => { if (closing && e.target === e.currentTarget) setShown(null) }}
      style={{
        position: 'fixed', bottom: 26, left: 0, right: 0, margin: '0 auto', width: 'fit-content', zIndex: 100,
        background: 'var(--bg-toast)', border: '1px solid var(--border-dashed)',
        boxShadow: 'var(--shadow-toast)', borderRadius: 9, padding: '10px 18px',
        fontSize: 12.5, fontWeight: 500, color: 'var(--text)', maxWidth: 520,
        animation: closing ? EXIT_DOWN : ENTER_UP,
      }}
    >
      {shown}
    </div>
  )
}

export function CountPill({ n, active }: { n: number; active?: boolean }) {
  if (!n) return null
  return (
    <span style={{
      fontFamily: 'var(--mono)', fontSize: 10.5, fontWeight: 600, padding: '1px 7px', borderRadius: 20,
      background: 'var(--hairline)',
      color: active ? 'var(--text-2)' : 'var(--text-faint)',
    }}>
      {n}
    </span>
  )
}

/** §9 page-header action cluster — every page's top-right actions render in this
 * one flex row (10 px gap). Order left → right by rising prominence: dim text →
 * ghost → danger-ghost → the single primary; only an icon-only overflow ellipsis
 * may follow the primary, at the far right edge. */
export function HeaderActions({ children, style }: {
  children: React.ReactNode; style?: React.CSSProperties
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none', ...style }}>
      {children}
    </div>
  )
}

/** §14 page header — the one title row. `children` is the title text (wrapped in
 * `.ad-h1`); pass `raw` when the left side is a hand-built row of the `.ad-h1`
 * plus inline metadata (rename pencil, version pill). `sub` renders the page
 * subtitle 6 px under; the header ends 20 px above whatever follows. */
export function PageTitle({ children, right, sub, raw, style }: {
  children: React.ReactNode; right?: React.ReactNode; sub?: React.ReactNode
  raw?: boolean; style?: React.CSSProperties
}) {
  return (
    <div style={{ marginBottom: 20, ...style }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 13 }}>
        {raw
          ? <div style={{ display: 'flex', alignItems: 'center', gap: 13, minWidth: 0, flex: 1 }}>{children}</div>
          : <h1 className="ad-h1">{children}</h1>}
        {right}
      </div>
      {sub && <p style={{ margin: '6px 0 0', fontSize: 13, lineHeight: 1.6, color: 'var(--text-muted)' }}>{sub}</p>}
    </div>
  )
}

/** §14 the one document viewer modal (§9.4 About documents, What's new): 680
 * wide, 15/600 title, a 62 vh markdown scroller, Retry on a failed load, quiet
 * Close. `text` null = still loading, `error` = the load failed. */
export function DocModal({ title, text, error, onRetry, onClose, render }: {
  title: React.ReactNode; text: string | null; error: boolean; onRetry: () => void
  onClose: () => void; render: (text: string) => React.ReactNode
}) {
  return (
    <Modal onClose={onClose} width={680}>
      {(close) => (
        <>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>{title}</h2>
          <ScrollArea wrapStyle={{ marginTop: 12 }} style={{ maxHeight: '62vh' }}>
            {text !== null
              ? render(text)
              : error
                ? (
                  <div>
                    <div style={{ fontSize: 12.5, color: 'var(--red-text)' }}>Couldn't load the document.</div>
                    <button className="ad-btn-ghost" onClick={onRetry} style={{ marginTop: 10 }}>Retry</button>
                  </div>
                )
                : <LoadingRow label="Loading…" />}
          </ScrollArea>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
            <BtnGhost onClick={close}>Close</BtnGhost>
          </div>
        </>
      )}
    </Modal>
  )
}

/** Modal shell: dimmed backdrop + centered card, enter (--t-enter fade-up) and
 * exit (--t-exit fade-down). Children get `close`, which plays the exit before
 * firing `onClose` (the caller's unmount) — backdrop click and Escape close the
 * same way, so no dismissal path skips the animation. */
// Stacked modals (e.g. a confirm over an editor modal): Escape must close only
// the top-most one. "Top-most" is what the user sees — highest zIndex wins,
// mount order breaks ties — so this can't drift from the visual stacking.
const modalStack: { id: symbol; z: number }[] = []
const topModal = () =>
  modalStack.reduce((top, m) => (m.z >= top.z ? m : top), modalStack[0])

/** True while any Modal is mounted — non-modal Escape handlers (e.g. the §11
 * Ask panel) must yield to the modal stack instead of closing underneath it. */
export const anyModalOpen = () => modalStack.length > 0

/** True while the top-most mounted Modal is the one at `zIndex` (the Modal
 * default) — a modal-owned shortcut (the §11 doc editor's ⌘S) yields to a card
 * stacked above it, exactly as the Modal's own Escape handler does. */
export const isTopModal = (zIndex = 60) => topModal()?.z === zIndex

/** §14: what Tab may reach inside an open card. Disabled controls and
 * `tabindex="-1"` holders (the card itself) are out; layout-based visibility is
 * not tested (it would force a reflow on every Tab), only the `hidden`
 * attribute, which is what the app uses to withhold a control. */
const FOCUSABLE_IN_CARD =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),'
  + ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({ onClose, width, zIndex = 60, cardStyle, role = 'dialog', ariaLabel, guardClose, children }: {
  onClose: () => void; width: number; zIndex?: number; cardStyle?: React.CSSProperties
  role?: 'dialog' | 'alertdialog'; ariaLabel?: string
  // §14: an escape-path guard — Escape and a backdrop click ask it first, and
  // a `false` keeps the modal open (the caller raises its own confirm, which
  // stacks above this card). Explicit buttons call `close` directly.
  guardClose?: () => boolean
  // `closing` is true through the ~200 ms exit animation, during which the
  // children stay mounted — a child with its own key/pointer handlers must
  // stop acting on the card the user is already dismissing.
  children: (close: () => void, closing: boolean) => React.ReactNode
}) {
  const [closing, setClosing] = useState(false)
  const closed = useRef(false)
  const finish = () => { if (!closed.current) { closed.current = true; onClose() } }
  const guard = useRef(guardClose)
  guard.current = guardClose
  const escape = () => { if (!guard.current || guard.current()) setClosing(true) }
  const card = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const entry = { id: Symbol('modal'), z: zIndex }
    modalStack.push(entry)
    // §14: a card that opens while focus sits outside it takes focus, so the
    // very first Tab is already inside the trap. Focus a child put on itself
    // (a modal that autofocuses its input) is left alone — and re-running this
    // effect (StrictMode mounts twice) then finds focus already inside and
    // does nothing.
    if (card.current && !card.current.contains(document.activeElement)) {
      card.current.focus({ preventScroll: true })
    }
    // §14: Tab wraps within the card — the last focusable element leads back to
    // the first and Shift+Tab back to the last, so no keypress reaches the page
    // underneath. Only the top-most card traps, exactly as Escape does: a
    // stacked confirm owns Tab and the card beneath it yields.
    const trap = (e: KeyboardEvent) => {
      if (!card.current) return
      const items = [...card.current.querySelectorAll<HTMLElement>(FOCUSABLE_IN_CARD)]
        .filter((el) => !el.closest('[hidden]'))
      // A card with nothing to focus keeps the focus it took on open.
      if (!items.length) { e.preventDefault(); card.current.focus({ preventScroll: true }); return }
      const active = document.activeElement
      const inside = card.current.contains(active)
      if (e.shiftKey) {
        // Backwards off the first element — or off the card itself, which sits
        // ahead of them all — wraps to the last.
        if (!inside || active === card.current || active === items[0]) {
          e.preventDefault()
          items[items.length - 1].focus()
        }
      } else if (!inside || active === items[items.length - 1]) {
        e.preventDefault()
        items[0].focus()
      }
    }
    const onKey = (e: KeyboardEvent) => {
      // §9.3: the developer-log overlay owns Escape while it is open — a modal
      // open underneath yields to it, like every other shortcut.
      if (devlogOverlayOpen()) return
      if (topModal()?.id !== entry.id) return
      if (e.key === 'Escape') escape()
      else if (e.key === 'Tab') trap(e)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      const i = modalStack.indexOf(entry)
      if (i >= 0) modalStack.splice(i, 1)
      document.removeEventListener('keydown', onKey)
    }
  }, [])
  useEffect(() => {
    if (!closing) return
    // Unmount even if animationend never fires (e.g. reduced-motion setups).
    const t = setTimeout(finish, 200)
    return () => clearTimeout(t)
  }, [closing])
  // Portal to body: page containers animate `transform` (.ad-anim-page, fill
  // both), which makes them the containing block for position:fixed — in-tree,
  // the modal would anchor to the scrolled page instead of the viewport (§9).
  return createPortal(
    <div
      onMouseDown={(e) => { if (e.target === e.currentTarget) escape() }}
      onAnimationEnd={(e) => { if (closing && e.target === e.currentTarget) finish() }}
      style={{
        position: 'fixed', inset: 0, background: 'var(--backdrop)', zIndex,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: closing
          ? 'adFadeOut var(--t-exit) var(--ease-exit) both'
          : 'adFadeIn var(--t-enter) var(--ease-enter) both',
      }}
    >
      <div ref={card} role={role} aria-modal="true" aria-label={ariaLabel} tabIndex={-1} style={{
        background: 'var(--bg-menu)', border: '1px solid var(--border-input)', borderRadius: 12,
        boxShadow: 'var(--shadow-modal)', width, padding: '22px 24px',
        animation: closing ? EXIT_DOWN : ENTER_UP,
        // §9: modal cards cap at 84vh and scroll inside — footer buttons can
        // never render off-screen on a small window.
        maxHeight: '84vh', display: 'flex', flexDirection: 'column',
        ...cardStyle,
      }}>
        {/* The wrap is itself a shrinkable flex column and the scroller a
          * min-height-0 flex item: a percentage height against the card's
          * auto-height wrap never resolved, so a modal taller than the cap
          * used to paint its footer past the card's edge instead of
          * scrolling (the §7 filter modal on a short window). */}
        <ScrollArea
          wrapStyle={{ minHeight: 0, display: 'flex', flexDirection: 'column' }}
          style={{ height: 'auto', flex: '1 1 auto', minHeight: 0 }}
        >
          {children(() => setClosing(true), closing)}
        </ScrollArea>
      </div>
    </div>,
    document.body,
  )
}

export function ConfirmModal({ title, body, confirmLabel, danger, onConfirm, onCancel }: {
  title: string; body: React.ReactNode; confirmLabel: string; danger?: boolean
  onConfirm: () => void; onCancel: () => void
}) {
  const confirmed = useRef(false)
  return (
    <Modal
      onClose={() => { if (confirmed.current) onConfirm(); else onCancel() }}
      width={400} zIndex={90}
      role="alertdialog" ariaLabel={title}
    >
      {(close) => (
        <>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{title}</div>
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-muted)', marginBottom: 18 }}>{body}</div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <BtnGhost onClick={close}>Cancel</BtnGhost>
            <button
              className={danger ? 'ad-btn-danger-ghost' : 'ad-btn-primary'}
              onClick={() => { confirmed.current = true; close() }}
            >
              {confirmLabel}
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}

// §4.9 reset progress overlay (§14): the Modal backdrop + card with no user
// dismissal path — no Escape, no backdrop click, no buttons. It closes only
// programmatically: flipping `open` false plays the §14 exit before unmount.
// Sits above every other surface (Modal caps at z-index 90).
export function BlockingOverlay({ open, width = 400, ariaLabel, children }: {
  open: boolean; width?: number; ariaLabel: string; children: React.ReactNode
}) {
  const [mounted, setMounted] = useState(open)
  useEffect(() => { if (open) setMounted(true) }, [open])
  const closing = mounted && !open
  useEffect(() => {
    if (!closing) return
    // Unmount even if animationend never fires (e.g. reduced-motion setups).
    const t = setTimeout(() => setMounted(false), 200)
    return () => clearTimeout(t)
  }, [closing])
  if (!mounted) return null
  return createPortal(
    <div
      onAnimationEnd={(e) => { if (closing && e.target === e.currentTarget) setMounted(false) }}
      style={{
        position: 'fixed', inset: 0, background: 'var(--backdrop)', zIndex: 120,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: closing
          ? 'adFadeOut var(--t-exit) var(--ease-exit) both'
          : 'adFadeIn var(--t-enter) var(--ease-enter) both',
      }}
    >
      <div role="alertdialog" aria-modal="true" aria-label={ariaLabel} style={{
        background: 'var(--bg-menu)', border: '1px solid var(--border-input)', borderRadius: 12,
        boxShadow: 'var(--shadow-modal)', width, padding: '22px 24px',
        animation: closing ? EXIT_DOWN : ENTER_UP,
      }}>
        {children}
      </div>
    </div>,
    document.body,
  )
}

// ---------- Python syntax highlighting ----------
// Step scripts are always Python (SPEC §15 — single bundled CPython). Tiny
// zero-dependency tokenizer keeps the bundle lean and Vite-friendly.

const PY_KEYWORDS = new Set([
  'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def',
  'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if',
  'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise',
  'return', 'try', 'while', 'with', 'yield', 'match', 'case',
])
const PY_CONSTS = new Set(['True', 'False', 'None'])
const PY_BUILTINS = new Set([
  'abs', 'all', 'any', 'bool', 'bytes', 'bytearray', 'dict', 'enumerate',
  'filter', 'float', 'format', 'frozenset', 'getattr', 'hasattr', 'hash',
  'input', 'int', 'isinstance', 'issubclass', 'iter', 'len', 'list', 'map',
  'max', 'min', 'next', 'object', 'open', 'ord', 'print', 'range', 'repr',
  'reversed', 'round', 'set', 'setattr', 'sorted', 'str', 'sum', 'super',
  'tuple', 'type', 'zip', 'Exception', 'ValueError', 'KeyError', 'TypeError',
])
const PY_COLOR = {
  keyword: 'var(--syn-keyword)', const: 'var(--syn-const)', string: 'var(--syn-string)',
  number: 'var(--syn-const)', comment: 'var(--syn-comment)', builtin: 'var(--syn-builtin)',
  call: 'var(--syn-builtin)', def: 'var(--syn-def)', decorator: 'var(--syn-def)',
}

// Whitespace, newline, (prefix)string, comment, decorator, number, identifier, symbol.
const PY_TOKEN = /([ \t]+)|(\r?\n)|((?:[rbfuRBFU]{0,2})(?:'''[\s\S]*?'''|"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(#[^\n]*)|(@[A-Za-z_][\w.]*)|(\d[\d_]*\.?[\d_]*(?:[eE][+-]?\d+)?[jJ]?)|([A-Za-z_]\w*)|([\s\S])/g

export function highlightPython(code: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  let m: RegExpExecArray | null
  let prevIdent = ''
  PY_TOKEN.lastIndex = 0
  while ((m = PY_TOKEN.exec(code)) !== null) {
    const [full, ws, nl, str, comment, deco, num, ident] = m
    const key = out.length
    if (ws) { out.push(ws); continue }
    if (nl) { out.push('\n'); prevIdent = ''; continue }
    let color: string | undefined
    if (str !== undefined) color = PY_COLOR.string
    else if (comment !== undefined) color = PY_COLOR.comment
    else if (deco !== undefined) color = PY_COLOR.decorator
    else if (num !== undefined) color = PY_COLOR.number
    else if (ident !== undefined) {
      if (PY_KEYWORDS.has(ident)) color = PY_COLOR.keyword
      else if (PY_CONSTS.has(ident)) color = PY_COLOR.const
      else if (prevIdent === 'def' || prevIdent === 'class') color = PY_COLOR.def
      else if (PY_BUILTINS.has(ident)) color = PY_COLOR.builtin
      else if (code[PY_TOKEN.lastIndex] === '(') color = PY_COLOR.call
    }
    if (ident !== undefined) prevIdent = ident
    else if (str === undefined && comment === undefined) prevIdent = ''
    if (color) {
      const italic = comment !== undefined
      out.push(<span key={key} style={{ color, ...(italic ? { fontStyle: 'italic' } : null) }}>{full}</span>)
    } else {
      out.push(full)
    }
  }
  return out
}

// The highlighted stream split into lines for a line-numbered view: every
// '\n' — the tokenizer's own newline tokens and the newlines inside a
// multi-line token (triple-quoted strings) — starts a new line, a split token
// reopening with the same style on the next one, so per-line rendering never
// loses a docstring's color. Highlighting a script per line instead would
// mis-tokenize every docstring continuation.
export function highlightPythonLines(code: string): React.ReactNode[][] {
  const lines: React.ReactNode[][] = [[]]
  let key = 0
  const push = (text: string, style?: React.CSSProperties) => {
    const parts = text.split('\n')
    parts.forEach((part, i) => {
      if (i > 0) lines.push([])
      if (!part) return
      lines[lines.length - 1].push(style ? <span key={key++} style={style}>{part}</span> : part)
    })
  }
  for (const node of highlightPython(code)) {
    if (typeof node === 'string') push(node)
    else if (React.isValidElement<{ style?: React.CSSProperties; children?: string }>(node)) {
      push(String(node.props.children ?? ''), node.props.style)
    }
  }
  return lines
}

// Highlighted Python <pre>. Pass the same style/className the plain <pre> used;
// per-token colors override the base text color for recognized tokens. The
// tokenizer walks the whole script, so it only reruns when the code changes —
// an unrelated re-render (a poll tick under an open modal) reuses the nodes.
export function PyCode({ code, className, style }: {
  code: string; className?: string; style?: React.CSSProperties
}) {
  const nodes = useMemo(() => highlightPython(code), [code])
  return <pre className={className} style={style}>{nodes}</pre>
}

// §14 overlay scrollbar: the scroller hides its native bar (.ad-scrollhide) and
// the pane draws a thin thumb over the content — no track, no layout space, so
// content never shifts. The caller wraps scroller + thumb in a position:relative
// .ad-scrollwrap element; textareas wire this hook directly (they can't host the
// thumb node), everything else uses ScrollArea below.
export function useOverlayThumb() {
  const elRef = useRef<HTMLElement | null>(null)
  const obsEl = useRef<HTMLElement | null>(null)
  const obs = useRef<{ ro: ResizeObserver; mo: MutationObserver } | null>(null)
  const [thumb, setThumb] = useState<{ top: number; h: number } | null>(null)
  const update = () => {
    const el = elRef.current
    if (!el) return
    const { scrollTop, scrollHeight, clientHeight } = el
    if (scrollHeight <= clientHeight + 1) { setThumb(null); return }
    const h = Math.max(24, (clientHeight / scrollHeight) * clientHeight - 6)
    const top = 3 + (scrollTop / (scrollHeight - clientHeight)) * (clientHeight - h - 6)
    setThumb((p) => (p && Math.abs(p.top - top) < 0.5 && Math.abs(p.h - h) < 0.5 ? p : { top, h }))
  }
  useEffect(update) // own renders (e.g. controlled-textarea value changes)
  useEffect(() => () => { obs.current?.ro.disconnect(); obs.current?.mo.disconnect() }, [])
  return {
    attach: (el: HTMLElement | null) => {
      elRef.current = el
      // content can grow without this pane re-rendering (async loads under the
      // page scroller, streamed logs) — watch the pane's box and its subtree
      if (el && el !== obsEl.current) {
        obs.current?.ro.disconnect()
        obs.current?.mo.disconnect()
        obsEl.current = el
        const ro = new ResizeObserver(update)
        ro.observe(el)
        const mo = new MutationObserver(update)
        mo.observe(el, { childList: true, subtree: true, characterData: true })
        obs.current = { ro, mo }
      }
    },
    onScroll: update,
    node: thumb && <div className="ad-thumb" style={{ top: thumb.top, height: thumb.h }} />,
  }
}

// §14 overlay-scrollbar pane for plain divs: the wrapper carries the layout
// styles (flex/margins), the inner scroller carries padding/overflow and hides
// its native bar.
export function ScrollArea({ wrapStyle, scrollRef, style, className, children, onScroll, testId }: {
  wrapStyle?: React.CSSProperties
  scrollRef?: React.MutableRefObject<HTMLDivElement | null>
  style?: React.CSSProperties
  className?: string
  children?: React.ReactNode
  onScroll?: React.UIEventHandler<HTMLDivElement>
  testId?: string
}) {
  const t = useOverlayThumb()
  return (
    <div className="ad-scrollwrap" style={{ position: 'relative', ...wrapStyle }}>
      <div
        ref={(el) => { t.attach(el); if (scrollRef) scrollRef.current = el }}
        onScroll={(e) => { t.onScroll(); onScroll?.(e) }}
        data-testid={testId}
        className={`ad-scrollhide${className ? ` ${className}` : ''}`}
        style={{ height: '100%', overflowY: 'auto', ...style }}
      >
        {children}
      </div>
      {t.node}
    </div>
  )
}
