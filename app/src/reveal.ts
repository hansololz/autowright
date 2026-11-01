// §9 reveal (third sweep, 2026-10-31): every "Show in <file manager>" caller
// rides this. Resolves true only when the reveal happened; a refused or failed
// one toasts "Couldn't open <file manager> — <reason>" (a user action never
// fails silently) and resolves false. No bridge (a plain browser) does nothing.
export async function revealPath(path: string, fileManager: string, showToast: (message: string) => void): Promise<boolean> {
  const bridge = window.autowright
  if (!bridge) return false
  try {
    const reply = await bridge.revealPath(path)
    if (reply && reply.ok === false) {
      showToast(`Couldn’t open ${fileManager} — ${reply.error}`)
      return false
    }
    return true
  } catch {
    showToast(`Couldn’t open ${fileManager}.`)
    return false
  }
}
