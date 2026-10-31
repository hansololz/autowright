// §9.2 RECENT EXECUTIONS card — row per execution, linking into Executions.
import { useStore } from '../../store'
import type { Execution } from '../../types'
import { Badge, Eyebrow } from '../../ui'
import { badgeAnim } from './model'

export function RecentExecutions({ execs }: { execs: Execution[] }) {
  const go = useStore((s) => s.go)
  if (execs.length === 0) return null
  return (
    <div style={{ marginBottom: 26 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 10 }}>
        <Eyebrow>RECENT EXECUTIONS</Eyebrow>
        <button className="ad-btn-text" onClick={() => go('executions')}>
          All executions <i className="fa-solid fa-chevron-right" style={{ fontSize: 10 }} />
        </button>
      </div>
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        {execs.map((e, i) => (
          <button
            className="ad-btn-bare ad-hover-row ad-focus-inset"
            key={e.id}
            onClick={() => go('execution', { executionId: e.id })}
            style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '9px 18px',
              borderBottom: i === execs.length - 1 ? 'none' : '1px solid var(--hairline-dim)',
              cursor: 'pointer',
            }}
          >
            <Badge
              status={e.status}
              style={{ flex: 'none', animation: badgeAnim(e.status) }}
            />
            <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-faint)', flex: 'none' }}>{e.id.slice(0, 8)}</span>
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)', flex: 'none' }}>
              {/* §9.2: message-triggered rows read "Discord · Dave · v3" */}
              {e.trigger}{e.triggerSender ? ` · ${e.triggerSender}` : ''}{e.versionLabel ? ` · ${e.versionLabel}` : ''}
            </span>
            {e.note && <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)' }}>{e.note}</span>}
            <div style={{ flex: 1 }} />
            <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-muted)' }}>{e.duration}</span>
            <span style={{ fontSize: 12, color: 'var(--text-faint)', width: 130, textAlign: 'right', flex: 'none' }}>{e.started}</span>
            <span style={{ color: 'var(--text-deco)' }}><i className="fa-solid fa-chevron-right" style={{ fontSize: 10 }} /></span>
          </button>
        ))}
      </div>
    </div>
  )
}
