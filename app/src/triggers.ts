// §4.3/§19 trigger previews — the renderer keeps NO local trigger-math mirror:
// every trigger label, validity verdict, and next-occurrence string comes from
// the backend's `POST /triggers/preview` (triggers.py — `triggers.cron_display`, the one implementation).
import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { TriggerPreview } from './types'

/** Debounced (§19) preview of a trigger list: one result per entry, in order.
 * Empty until the first response; while a fetch is pending the previous
 * results keep showing (no flicker), and a stale response never lands (each
 * request carries a sequence number — only the newest may commit).
 *
 * The held results are positional, so they only outlive a change that keeps the
 * list's kinds: after a kind switch (the §9.2 trigger editor's tabs) the
 * previous kind's verdict would otherwise gate the new form's Add button and
 * label it, so results from other kinds are not returned at all. */
export function useTriggerPreview(triggers: object[]): TriggerPreview[] {
  const key = JSON.stringify(triggers)
  const kinds = triggers.map((t) => (t as { kind?: unknown }).kind ?? '').join(',')
  const [held, setHeld] = useState<{ kinds: string; entries: TriggerPreview[] }>({ kinds, entries: [] })
  const seq = useRef(0)
  useEffect(() => {
    const mine = ++seq.current // invalidates any in-flight response
    if (triggers.length === 0) {
      setHeld({ kinds, entries: [] })
      return
    }
    const t = setTimeout(() => {
      api.triggersPreview(JSON.parse(key) as object[])
        .then((r) => { if (seq.current === mine) setHeld({ kinds, entries: r.triggers }) })
        .catch(() => {}) // backend unreachable — keep the last results
    }, 300)
    return () => clearTimeout(t)
    // key IS the serialized triggers — the array identity may change per render
  }, [key])
  return held.kinds === kinds ? held.entries : []
}
