// §6 concurrency settings + the live queue (§9.2).
import React, { useState } from 'react'
import { api } from '../../api'
import { useStore } from '../../store'
import type { Automation } from '../../types'
import { ConfirmModal, Eyebrow, Notice } from '../../ui'
import { runAction } from './model'

/** One `number` row per setting, using the same §9.2 compact-row layout as
 *  ParamRow, and PATCHing on the same no-version/no-AI path. */
export function ConcurrencyCard({ auto, showToast }: { auto: Automation; showToast: (m: string, ms?: number) => void }) {
  const loadAuto = useStore((s) => s.loadAuto)
  const executions = useStore((s) => s.executions)
  const [confirmClear, setConfirmClear] = useState(false)

  // §9.2: the waiting count is the automation's own `queued` records — the same
  // source the §7 Waiting section lists. A count carried on the automation would
  // ride a /state snapshot, and two refreshes racing (the finish and the
  // promotion it drains into) can land out of order and leave a promoted entry
  // counted as waiting; a record's status flips with `exec.started` and can't.
  const waiting = executions.filter((e) => e.automationId === auto.id && e.status === 'queued' && !e.test).length

  const patch = async (key: 'maxParallel' | 'maxQueued', v: number) => {
    try {
      await api.patchAutomation(auto.id, { [key]: v })
      // awaited so the row's draft outlives the refresh — clearing it sooner
      // would flash the pre-PATCH store value before the new one lands
      await loadAuto(auto.id)
    } catch (err) {
      showToast((err as Error).message)
    }
  }

  // §6/§9.2: the caution is specific or it isn't shown — an automation whose
  // steps never touch memory has nothing to warn about.
  // Names are deduped: two steps may legally carry the same name, and naming
  // one twice in the caution reads as a mistake.
  const memSteps = auto.maxParallel > 1
    ? [...new Set((auto.steps ?? []).filter(s => /\bmemory\b/.test(s.code ?? '')).map(s => s.name))]
    : []

  return (
    <div style={{ marginBottom: 26 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
        <Eyebrow>CONCURRENCY</Eyebrow>
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
          Changes apply immediately.
        </span>
      </div>
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <NumberSettingRow
          label="Max parallel executions"
          help="How many executions of this automation may run at the same time."
          value={auto.maxParallel} min={1}
          onCommit={(v) => patch('maxParallel', v)}
        />
        {memSteps.length > 0 && (
          <Notice tone="amber" className="ad-anim-item" style={{ margin: '12px 18px' }}>
            {memSteps.map((n, i) => <code key={`${i}:${n}`} style={{ fontFamily: 'var(--mono)' }}>{n}</code>)
              .reduce<React.ReactNode[]>((acc, el, i) => i === 0 ? [el] : [...acc, ', ', el], [])}
            {memSteps.length === 1 ? ' writes' : ' write'} to memory. Parallel executions share one
            memory directory, so two runs updating the same value can lose one of the updates.
          </Notice>
        )}
        <NumberSettingRow
          label="Max queued executions"
          help="How many executions wait for a free slot. Incoming messages beyond this are answered with a busy notice instead."
          value={auto.maxQueued} min={0} last={waiting === 0}
          onCommit={(v) => patch('maxQueued', v)}
        />
        {waiting > 0 && (
          <div className="ad-anim-item" style={{
            padding: '12px 18px', display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', gap: 12,
          }}>
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
              <i className="fa-solid fa-hourglass-half" style={{ marginRight: 7, color: 'var(--cyan)' }} />
              {waiting} waiting
            </span>
            <button className="ad-btn-text" onClick={() => setConfirmClear(true)}>Clear queue</button>
          </div>
        )}
      </div>
      {confirmClear && (
        <ConfirmModal
          title="Clear queue"
          body={`Cancel ${waiting} waiting message${waiting === 1 ? '' : 's'}? Each sender is told. Executions already running are not affected.`}
          confirmLabel="Clear queue"
          danger
          onCancel={() => setConfirmClear(false)}
          onConfirm={() => {
            setConfirmClear(false)
            runAction(auto.id, async () => {
              const { cancelled } = await api.clearQueue(auto.id)
              return `${cancelled} waiting message${cancelled === 1 ? '' : 's'} cancelled.`
            })
          }}
        />
      )}
    </div>
  )
}

function NumberSettingRow(
  { label, help, value, min, last, onCommit }:
  { label: string; help: string; value: number; min: number; last?: boolean; onCommit: (v: number) => Promise<void> | void },
) {
  const [draft, setDraft] = useState<string | null>(null)

  return (
    <div style={{
      padding: '15px 18px', borderBottom: last ? 'none' : '1px solid var(--hairline-dim)',
      display: 'flex', gap: 18, alignItems: 'center',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600 }}>{label}</div>
        <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-muted)', marginTop: 3 }}>{help}</div>
      </div>
      <input
        value={draft ?? String(value)}
        inputMode="numeric"
        onChange={(e) => setDraft(e.target.value.replace(/[^0-9]/g, ''))}
        onBlur={() => {
          // Commit on blur rather than per keystroke: an intermediate "" or "0"
          // would otherwise PATCH a value the user never meant to set.
          const v = draft === null || draft === '' ? value : Math.max(min, parseInt(draft, 10))
          if (v === value) { setDraft(null); return }
          // Hold the committed number until the PATCH (and store refresh) settles;
          // clearing now would show the old value for the length of the round-trip.
          // Success finds the store already refreshed; failure reverts to the prop.
          const committed = String(v)
          setDraft(committed)
          void Promise.resolve(onCommit(v)).finally(() =>
            setDraft((d) => (d === committed ? null : d)))
        }}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
        className="ad-input compact mono"
        style={{ width: 70, textAlign: 'center' }}
      />
    </div>
  )
}
