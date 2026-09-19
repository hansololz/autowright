// §9 render-failure containment: much of what the renderer shows is AI-authored
// or leniently loaded (§4, §8), so one bad page must never blank the window.
// The shell wraps its content region in this boundary (and main.tsx the root as
// a backstop): the throwing subtree is replaced by the §14 dashed notice with a
// way back, while the rail, toasts, and the rest of the shell keep working.
import React from 'react'
import { useStore } from './store'
import { EmptyNotice } from './ui'

interface Props { children: React.ReactNode }
// `attempt` keys the children: clearing the error alone re-renders the same
// element tree, and the page that threw would be handed straight back its own
// state — the button reads as inert. A new key is a genuine remount.
interface State { error: Error | null; attempt: number }

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, attempt: 0 }

  static getDerivedStateFromError(error: Error): Pick<State, 'error'> {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // The §9.3 developer log overlay reads the console — keep the stack there.
    console.error('Render failed:', error, info.componentStack)
  }

  // Navigate first, then clear: both land in one React batch, so the children
  // re-mount on the Automations list rather than on the page that just threw.
  private back = () => {
    useStore.getState().go('automations', { automationId: null, executionId: null })
    this.setState((s) => ({ error: null, attempt: s.attempt + 1 }))
  }

  render() {
    const { error, attempt } = this.state
    if (!error) return <React.Fragment key={attempt}>{this.props.children}</React.Fragment>
    return (
      <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '26px 30px 70px' }}>
        <EmptyNotice
          title="Something went wrong on this page"
          body={(
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.6, wordBreak: 'break-word' }}>
                {error.message || String(error)}
              </span>
              <button className="ad-btn-ghost" onClick={this.back}>
                Back to Automations
              </button>
            </div>
          )}
          style={{ marginTop: 20 }}
        />
      </div>
    )
  }
}

export default ErrorBoundary
