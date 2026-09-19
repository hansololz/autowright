// §9.2 MEMORY card: reveal/snapshot/clear actions, the snapshot rows with
// their inline confirm swaps, and the §6.3 automatic-snapshot toggles.
import React, { useEffect, useState } from 'react'
import { api } from '../../api'
import { usePlatformCopy } from '../../platformCopy'
import { useStore } from '../../store'
import type { Automation, SnapshotSettings } from '../../types'
import { EmptyLine, Eyebrow, Toggle } from '../../ui'
import { runAction } from './model'

// §6.3 automatic-snapshot toggles — label + plain-language explanation per reason
const SNAP_SETTINGS: Array<{ key: keyof SnapshotSettings; label: string; help: string }> = [
  {
    key: 'preVersion', label: 'Before a new version executes',
    help: 'Saves a copy of memory right before the first execution of a newly saved version, so you can restore how memory was if the new version mishandles it.',
  },
  {
    key: 'preClear', label: 'Before clearing memory',
    help: 'Saves a copy right before Clear memory empties the directory, so a clear can be undone.',
  },
  {
    key: 'preRestore', label: 'Before restoring a snapshot',
    help: 'Saves a copy of the current memory right before a restore replaces it, so a restore can be undone.',
  },
]

export function MemoryCard({ auto, executing }: { auto: Automation; executing: boolean }) {
  const showToast = useStore((s) => s.showToast)
  // §9 per-OS copy rule: the reveal action's label and file-manager name.
  const copy = usePlatformCopy()
  const [confirmClear, setConfirmClear] = useState(false)
  const [snapAsk, setSnapAsk] = useState(false)
  const [snapName, setSnapName] = useState('')
  const [snapRow, setSnapRow] = useState<{ snapshotId: string; kind: 'restore' | 'rename' | 'delete' } | null>(null)
  const [renameVal, setRenameVal] = useState('')

  // Close any open inline confirm when the page switches automations.
  useEffect(() => { setConfirmClear(false); setSnapAsk(false); setSnapRow(null) }, [auto.id])

  const revealMemory = () => {
    const p = auto.memory?.path
    if (!p) return
    window.autowright?.revealPath(p)
      .then(() => showToast(`Shown in ${copy.fileManager} — Autowright › Memory › ${auto.name}`))
      // a user action never fails silently
      .catch(() => showToast(`Couldn’t open ${copy.fileManager}.`))
  }

  const doClearMemory = () => {
    setConfirmClear(false)
    runAction(auto.id, async () => {
      await api.clearMemory(auto.id)
      return 'Memory cleared — the next execution starts fresh.'
    })
  }

  // §6.3 memory snapshots
  const doSnapshot = () => {
    const name = snapName.trim()
    setSnapAsk(false)
    setSnapName('')
    runAction(auto.id, async () => {
      await api.createSnapshot(auto.id, name || undefined)
      return 'Snapshot saved.'
    })
  }
  const doRestoreSnap = (snapshotId: string) => {
    setSnapRow(null)
    runAction(auto.id, async () => {
      await api.restoreSnapshot(auto.id, snapshotId)
      return 'Memory restored — the next execution continues from the snapshot.'
    })
  }
  const doRenameSnap = (snapshotId: string) => {
    const name = renameVal.trim()
    setSnapRow(null)
    runAction(auto.id, async () => {
      await api.renameSnapshot(auto.id, snapshotId, name || null)
    })
  }
  const doDeleteSnap = (snapshotId: string) => {
    setSnapRow(null)
    runAction(auto.id, async () => {
      await api.deleteSnapshot(auto.id, snapshotId)
      return 'Snapshot deleted.'
    })
  }
  // §6.3 automatic-snapshot toggles — user-owned operational state, applies immediately (§19 PATCH)
  const setSnapSetting = (key: keyof SnapshotSettings, on: boolean) => {
    runAction(auto.id, async () => {
      await api.patchAutomation(auto.id, { snapshotSettings: { [key]: on } })
    })
  }

  if (!auto.memory) return null

  return (
    <div style={{ marginBottom: 26 }}>
      <Eyebrow style={{ marginBottom: 10 }}>MEMORY</Eyebrow>
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <div
          // §14: keyed remount + opacity-only fade — the inline swap never jumps the row
          key={confirmClear ? 'clear' : snapAsk ? 'snap' : 'actions'}
          className="ad-anim-fade"
          style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: '12px 18px' }}
        >
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-muted)' }}>
            {auto.memory.size} · {auto.memory.updated}
          </span>
          <div style={{ flex: 1 }} />
          {confirmClear ? (
            <>
              <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>
                {auto.snapshotSettings.preClear
                  ? 'Next execution starts fresh, like the first time. Current memory is snapshotted first.'
                  : "Next execution starts fresh, like the first time. Automatic snapshots are off — this can't be undone."}
              </span>
              <button className="ad-btn-danger-ghost" onClick={doClearMemory}>
                Clear
              </button>
              <button className="ad-btn-soft" onClick={() => setConfirmClear(false)}>
                Keep
              </button>
            </>
          ) : snapAsk ? (
            <>
              <input
                className="ad-input compact"
                value={snapName}
                onChange={(e) => setSnapName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') doSnapshot() }}
                placeholder="Name — optional"
                autoFocus
                style={{ width: 220 }}
              />
              <button className="ad-btn-accent-ghost" onClick={doSnapshot}>
                Save
              </button>
              <button className="ad-btn-soft" onClick={() => { setSnapAsk(false); setSnapName('') }}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button className="ad-btn-soft" onClick={revealMemory}>
                {copy.reveal}
              </button>
              {auto.memory.size === 'empty' ? (
                <button className="ad-btn-soft" disabled title="Memory is empty">
                  Snapshot
                </button>
              ) : (
                <button className="ad-btn-soft" onClick={() => setSnapAsk(true)}>
                  Snapshot
                </button>
              )}
              <button className="ad-btn-text danger" onClick={() => setConfirmClear(true)}>
                Clear memory
              </button>
            </>
          )}
        </div>
        <div className="ad-anim-item" style={{ borderTop: '1px solid var(--hairline-dim)' }}>
          {(auto.snapshots ?? []).length === 0
            ? <EmptyLine>No snapshots yet.</EmptyLine>
            : (auto.snapshots ?? []).map((s, i) => (
              <div
                // §14: keyed remount + fade on the row's inline action/confirm swaps
                key={`${s.id}:${snapRow?.snapshotId === s.id ? snapRow.kind : 'row'}`}
                className="ad-anim-fade"
                style={{
                  display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                  padding: '12px 18px',
                  ...(i === (auto.snapshots ?? []).length - 1
                    ? {}
                    : { borderBottom: '1px solid var(--hairline-dim)' }),
                }}
              >
                {snapRow?.snapshotId === s.id && snapRow.kind === 'restore' ? (
                  <>
                    <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>
                      {auto.snapshotSettings.preRestore
                        ? 'Replaces current memory — the current state is snapshotted first.'
                        : 'Replaces current memory — automatic snapshots are off, so the current state is lost.'}
                    </span>
                    <div style={{ flex: 1 }} />
                    <button
                      className="ad-btn-accent-ghost"
                      onClick={() => doRestoreSnap(s.id)}
                      disabled={executing}
                      title={executing ? 'Blocked while an execution is live' : undefined}
                    >
                      Restore
                    </button>
                    <button className="ad-btn-soft" onClick={() => setSnapRow(null)}>
                      Keep
                    </button>
                  </>
                ) : snapRow?.snapshotId === s.id && snapRow.kind === 'rename' ? (
                  <>
                    <input
                      className="ad-input compact"
                      value={renameVal}
                      onChange={(e) => setRenameVal(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') doRenameSnap(s.id) }}
                      placeholder="Name — optional"
                      autoFocus
                      style={{ width: 220 }}
                    />
                    <div style={{ flex: 1 }} />
                    <button className="ad-btn-text" onClick={() => doRenameSnap(s.id)}>
                      Save
                    </button>
                    <button className="ad-btn-text" onClick={() => setSnapRow(null)}>
                      Cancel
                    </button>
                  </>
                ) : snapRow?.snapshotId === s.id && snapRow.kind === 'delete' ? (
                  <>
                    <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>Delete this snapshot?</span>
                    <div style={{ flex: 1 }} />
                    <button className="ad-btn-text danger" onClick={() => doDeleteSnap(s.id)}>
                      Delete
                    </button>
                    <button className="ad-btn-text" onClick={() => setSnapRow(null)}>
                      Keep
                    </button>
                  </>
                ) : (
                  <>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                      {s.name ?? 'Snapshot'}
                    </span>
                    <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)' }}>
                      {s.reason} · {s.version} · {s.size} · {s.files} {s.files === 1 ? 'file' : 'files'} · {s.when}
                    </span>
                    <div style={{ flex: 1 }} />
                    <button className="ad-btn-text" onClick={() => setSnapRow({ snapshotId: s.id, kind: 'restore' })}>
                      Restore
                    </button>
                    <button
                      className="ad-btn-text"
                      onClick={() => { setRenameVal(s.name ?? ''); setSnapRow({ snapshotId: s.id, kind: 'rename' }) }}
                    >
                      Rename
                    </button>
                    <button className="ad-btn-text danger" onClick={() => setSnapRow({ snapshotId: s.id, kind: 'delete' })}>
                      Delete
                    </button>
                  </>
                )}
              </div>
            ))}
        </div>
        {/* §6.3 automatic-snapshot toggles */}
        <div style={{ padding: '12px 18px', borderTop: '1px solid var(--hairline-dim)' }}>
          <Eyebrow style={{ marginBottom: 4 }}>AUTOMATIC SNAPSHOTS</Eyebrow>
          {SNAP_SETTINGS.map(({ key, label, help }) => (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '7px 0' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--text-muted)' }}>{label}</div>
                <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>{help}</div>
              </div>
              <Toggle
                on={auto.snapshotSettings[key]}
                onChange={() => setSnapSetting(key, !auto.snapshotSettings[key])}
                title={auto.snapshotSettings[key] ? 'Turn this automatic snapshot off' : 'Turn this automatic snapshot on'}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
