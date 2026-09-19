// §4.9 shared quit flow (§3 quit-entirely): the quit overlay and the
// force-confirm modal, mounted once on every main-window surface. Driven by
// the store (`quitStage`, `quitAll`) so the Settings QUIT card and an OS quit
// — Cmd+Q, the application menu's Quit, the dock's Quit, pushed by main as
// `quit-requested` — run the very same flow.
import React, { useEffect } from 'react'
import { useStore } from '../store'
import { BlockingOverlay, ConfirmModal, Spinner } from '../ui'

export default function QuitFlow() {
  const quitStage = useStore((s) => s.quitStage)
  const quitAll = useStore((s) => s.quitAll)
  const cancelQuit = useStore((s) => s.cancelQuit)

  // §3: main asks on an OS quit; the flow answers with the unforced quit-all
  // (main's native fallback stands down once that IPC arrives).
  useEffect(() => {
    const off = window.autowright?.onQuitRequested?.(() => { void useStore.getState().quitAll() })
    return () => { off?.() }
  }, [])

  return (
    <>
      {/* §4.9 force-confirm modal: the quit-all IPC answered busy — a live
          execution. Confirming retries with force, which skips the gate. */}
      {quitStage === 'force' && (
        <ConfirmModal
          title="An automation is executing"
          body="Shut down everything and quit? The running automation will be killed."
          confirmLabel="Shut down and quit"
          danger
          onConfirm={() => { void quitAll(true) }}
          onCancel={cancelQuit}
        />
      )}
      {/* §4.9 quit overlay: non-dismissable while §3 quit-all runs — on
          success it stays up until the app exits. */}
      <BlockingOverlay open={quitStage === 'stopping'} ariaLabel="Quitting Autowright">
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, textAlign: 'center' }}>
          <Spinner size={22} />
          <div style={{ fontSize: 15, fontWeight: 600, marginTop: 4 }}>Quitting Autowright…</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>Stopping everything…</div>
        </div>
      </BlockingOverlay>
    </>
  )
}
