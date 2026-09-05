// Agents page (§4.7, §12): agent cards with session-cached connection checks.
// Agent actions (check/make-default/remove) live on the edit form, not the card.
import { useEffect, type MouseEvent } from 'react'
import { useStore, type AgentCheck } from '../store'
import type { Agent } from '../types'
import { BtnPrimary, EmptyState, Eyebrow, HeaderActions, LoadingRow, MiniBadge, P, PageTitle, dispModel } from '../ui'


function AgentCard({ ag, check }: { ag: Agent; check: AgentCheck | undefined }) {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this card on
  // every store write anywhere — every toast, every log line of every execution.
  const automations = useStore((s) => s.automations)
  const go = useStore((s) => s.go)
  const showToast = useStore((s) => s.showToast)
  const runAgentCheck = useStore((s) => s.runAgentCheck)
  const checking = check === 'checking' || check === undefined
  const connecting = check === 'connecting'
  const ready = check === 'ready'
  const busy = checking || connecting

  // §12 per-card check button — Ready runs the timed "Check connection",
  // Needs setup runs the reconnect check; both refresh the cached badge.
  const recheck = (e: MouseEvent) => {
    e.stopPropagation()
    if (ready) {
      const t0 = performance.now()
      void runAgentCheck(ag.id).then((st) => {
        const secs = ((performance.now() - t0) / 1000).toFixed(1)
        showToast(st === 'ready'
          ? `${ag.name || ag.harness} answered in ${secs} s. Ready.`
          : `${ag.name || ag.harness} didn't answer. It needs setup.`)
      })
    } else {
      void runAgentCheck(ag.id, 'connecting').then((st) => {
        showToast(st === 'ready'
          ? 'Connected. Signed in as you.'
          : 'Still signed out. Finish signing in, then try again.', 2600)
      })
    }
  }
  const badge = checking
    ? { label: 'Checking', c: P.cyan, bg: P.cyanBg }
    : connecting
      ? { label: 'Connecting', c: P.cyan, bg: P.cyanBg }
      : ready
        ? { label: 'Ready', c: P.green, bg: P.greenBg }
        : { label: 'Needs setup', c: P.amber, bg: P.amberBg }
  const uses = ag.usedBy ?? []

  return (
    // §12: whole card opens the edit form — needs-setup opens with the reconnect banner.
    <div
      className="ad-card-click"
      data-testid="agent-card"
      role="button"
      tabIndex={0}
      onClick={() => go('agentNew', { agentEditId: ag.id })}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return
        if ((e.target as HTMLElement).closest('button')) return
        e.preventDefault()
        go('agentNew', { agentEditId: ag.id })
      }}
      style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 10 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{ag.name || ag.harness}</span>
        <MiniBadge c={badge.c} bg={badge.bg}>{badge.label}</MiniBadge>
        {/* §12: square icon button like the automations list's Execute now. */}
        <button
          className="ad-btn-exec"
          onClick={recheck}
          disabled={busy}
          title={busy ? 'Checking…' : ready ? 'Check connection' : 'Reconnect'}
          aria-label={busy ? 'Checking…' : ready ? 'Check connection' : 'Reconnect'}
          style={{ marginLeft: 'auto' }}
        >
          <i
            className={busy ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-plug'}
            style={{ fontSize: 11 }}
          />
        </button>
      </div>
      <div style={{ font: `500 11px var(--mono)`, color: 'var(--text-faint)', marginTop: -5 }}>
        {ag.harness} · {dispModel(ag)}
      </div>
      {/* Detail line = the §4.7 description (drafting input) — never generated copy. */}
      <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: ag.description?.trim() ? 'var(--text-muted)' : 'var(--text-faint)' }}>
        {ag.description?.trim() ? ag.description : 'No description yet. Add one to tell the authoring agent what this agent is for.'}
      </p>
      {uses.length > 0 ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <Eyebrow>USED BY</Eyebrow>
          {uses.map((u) => {
            // §4.7/§12: entries are { id, name } — navigate by id (a name
            // lookup would be ambiguous under duplicate automation names).
            const auto = automations.find((a) => a.id === u.id)
            // No matching automation → inert chip: plain span, base chip look, no hover/cursor.
            return auto ? (
              <button
                key={u.id}
                className="ad-chip-btn"
                onClick={(e) => { e.stopPropagation(); go('automation', { automationId: u.id }) }}
              >
                {u.name}
              </button>
            ) : (
              <span key={u.id} className="ad-chip-btn static">{u.name}</span>
            )
          })}
        </div>
      ) : (
        <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Not used by any automation yet.</div>
      )}
      {(checking || connecting) && (
        <LoadingRow
          label={connecting ? 'Reconnecting…' : 'Checking locally…'}
          style={{ marginTop: 'auto' }}
        />
      )}
    </div>
  )
}

export default function AgentsPage() {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this page on
  // every store write anywhere — every toast, every log line of every execution.
  const agents = useStore((s) => s.agents)
  const agentChecks = useStore((s) => s.agentChecks)
  const go = useStore((s) => s.go)

  // §12 session cache: only agents with no cached status get checked, with a
  // small stagger. The cache entry is claimed synchronously (StrictMode
  // re-mount must not double-check), and in-flight checks outlive the page —
  // results land in the store either way.
  useEffect(() => {
    const cur = useStore.getState().agentChecks
    const fresh = agents.filter((ag) => !cur[ag.id])
    if (!fresh.length) return
    useStore.setState({
      agentChecks: { ...cur, ...Object.fromEntries(fresh.map((ag) => [ag.id, 'checking' as const])) },
    })
    fresh.forEach((ag, i) => {
      window.setTimeout(() => { void useStore.getState().runAgentCheck(ag.id) }, i * 100)
    })
  }, [agents])

  return (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '26px 30px 70px' }}>
      <PageTitle
        sub="The AI that writes your automations. It never executes anything. Autowright does that. New automations use your default agent."
        right={<HeaderActions><BtnPrimary onClick={() => go('agentNew')}>Add agent</BtnPrimary></HeaderActions>}
      >
        Agents
      </PageTitle>
      {agents.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(310px,1fr))', gap: 14 }}>
          {agents.map((ag) => (
            <AgentCard key={ag.id} ag={ag} check={agentChecks[ag.id]} />
          ))}
        </div>
      ) : (
        <EmptyState
          text="No agents yet. Existing automations still execute on schedule, but you need an agent to create or edit them."
          cta={<BtnPrimary onClick={() => go('agentNew')}>Add your first agent</BtnPrimary>}
        />
      )}
    </div>
  )
}
