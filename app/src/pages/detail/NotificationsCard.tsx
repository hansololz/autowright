// §4.1 per-automation notification override (§9.2 NOTIFICATIONS card).
import React, { useRef, useState } from 'react'
import { api } from '../../api'
import { useStore } from '../../store'
import type { Automation, AutomationNotifications } from '../../types'
import { Eyebrow, MenuRow, PopMenu, settingsRow, settingsRowSub, settingsRowTitle, usePopover } from '../../ui'

// The §4.9 app-wide values, labelled as the Settings page labels them.
const GLOBAL_LABELS: Record<'attention' | 'all' | 'never', string> = {
  attention: 'Only when something needs attention',
  all: 'After every execution',
  never: 'Never',
}

const OVERRIDE_LABELS: Record<AutomationNotifications, string> = {
  attention: GLOBAL_LABELS.attention,
  all: GLOBAL_LABELS.all,
  never: 'Never for this automation',
}

const INHERIT_LABEL = 'Use the app setting'

/** One "Notify me" row whose value button opens the four choices; picking one
 *  PATCHes `notifications` on the same no-version/no-AI path as the
 *  concurrency rows. Renders only where the OS notification API exists — the
 *  same gate as the §4.9 Settings row. */
export function NotificationsCard({ auto, showToast }: { auto: Automation; showToast: (m: string, ms?: number) => void }) {
  const notificationsOn = useStore((s) => s.platformCapabilities.notifications)
  const globalSetting = useStore((s) => s.settings?.notifications)
  const loadAuto = useStore((s) => s.loadAuto)
  const [open, setOpen, ref] = usePopover()
  // The committed choice while its PATCH (and the store refresh) is in flight;
  // `undefined` means nothing is pending and the record's value shows.
  const [pending, setPending] = useState<AutomationNotifications | null | undefined>(undefined)
  // §9.2: picks are request-counted - only the newest pick settles `pending`,
  // so an older PATCH landing never flips the button back while a newer one
  // is still in flight.
  const pickSequence = useRef(0)

  if (!notificationsOn) return null

  const value = pending !== undefined ? pending : (auto.notifications ?? null)
  const globalLabel = globalSetting ? GLOBAL_LABELS[globalSetting] : null

  const pick = async (next: AutomationNotifications | null) => {
    setOpen(false)
    if (next === value) return
    const sequence = ++pickSequence.current
    setPending(next)
    try {
      await api.patchAutomation(auto.id, { notifications: next })
      // awaited so the button's committed label outlives the refresh —
      // clearing it sooner would flash the pre-PATCH value
      await loadAuto(auto.id)
    } catch (err) {
      showToast((err as Error).message)
    } finally {
      if (sequence === pickSequence.current) setPending(undefined)
    }
  }

  const inheritLabel = (
    <>
      {INHERIT_LABEL}
      {globalLabel && <span style={{ color: 'var(--text-faint)' }}> · {globalLabel}</span>}
    </>
  )

  const row = (choice: AutomationNotifications | null, label: React.ReactNode) => {
    const selected = value === choice
    return (
      <MenuRow key={choice ?? 'inherit'} active={selected} onClick={() => { void pick(choice) }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ width: 14, flex: 'none', textAlign: 'center', color: 'var(--accent)' }}>
            {selected ? <i className="fa-solid fa-check" style={{ fontSize: 10 }} /> : ''}
          </span>
          <span>{label}</span>
        </span>
      </MenuRow>
    )
  }

  return (
    <div style={{ marginBottom: 26 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
        <Eyebrow>NOTIFICATIONS</Eyebrow>
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
          Changes apply immediately.
        </span>
      </div>
      <div className="ad-card">
        <div style={settingsRow}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={settingsRowTitle}>Notify me</div>
            {value === 'never' && (
              <div style={settingsRowSub}>Failures still show on the Automations list and the menu bar.</div>
            )}
          </div>
          <div ref={ref} style={{ position: 'relative', flex: 'none' }}>
            <button
              className="ad-btn-pill"
              onClick={() => setOpen(!open)}
              aria-label="Notify me"
            >
              <span>{value === null ? INHERIT_LABEL : OVERRIDE_LABELS[value]}</span>
              <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 10 }} />
            </button>
            <PopMenu show={open} style={{ top: 'calc(100% + 6px)', right: 0, minWidth: 320 }}>
              {row(null, inheritLabel)}
              {row('attention', OVERRIDE_LABELS.attention)}
              {row('all', OVERRIDE_LABELS.all)}
              {row('never', OVERRIDE_LABELS.never)}
            </PopMenu>
          </div>
        </div>
      </div>
    </div>
  )
}
