// §9.2 version diff modal: what changed between two stored versions of an
// automation, shown before the user restores one. The step-script modal's
// frame put to a second use — the same Modal card, a 280 px file navigator on
// the left and a full-height pane on the code ground on the right — but wider,
// since the pane holds two code columns. The backend diffs (§19
// GET /automations/{id}/diff); this file only renders. The comparison is always
// chronological: the older version left, the newer right.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { StepKeys } from './steps'
import type { DiffFile, DiffRow, VersionDiff } from './types'
import { Eyebrow, MenuItemRow, Modal, Notice, PageLoading, PopMenu, ScrollArea, highlightPythonLines, usePopover } from './ui'

// A stored version as the picker lists it: the current one plus the §4.1
// `versions` history (the pages assemble the list from the automation record).
export interface DiffVersionRef { version: number; when: string; note: string | null }

// §9.2: a run of same rows longer than COLLAPSE_OVER collapses, keeping
// CONTEXT rows on each side (the §20 CLI applies the same numbers).
export const DIFF_CONTEXT = 3
export const DIFF_COLLAPSE_OVER = 6

export type DiffItem =
  | { kind: 'row'; row: DiffRow; index: number }
  | { kind: 'collapsed'; start: number; count: number }

// The rows a file renders: every row, or — in a changed file — long same-runs
// folded to one expandable marker (`expanded` holds the starts of runs the
// user has opened, for the life of the comparison).
export function diffItems(file: DiffFile, expanded: ReadonlySet<number>): DiffItem[] {
  const rows = file.rows
  if (file.status !== 'changed') return rows.map((row, index) => ({ kind: 'row', row, index }))
  const out: DiffItem[] = []
  let i = 0
  while (i < rows.length) {
    if (rows[i].kind !== 'same') { out.push({ kind: 'row', row: rows[i], index: i }); i++; continue }
    let j = i
    while (j < rows.length && rows[j].kind === 'same') j++
    const head = i > 0 ? DIFF_CONTEXT : 0
    const tail = j < rows.length ? DIFF_CONTEXT : 0
    const hidden = j - i - head - tail
    if (j - i > DIFF_COLLAPSE_OVER && hidden > 0 && !expanded.has(i)) {
      for (let k = i; k < i + head; k++) out.push({ kind: 'row', row: rows[k], index: k })
      out.push({ kind: 'collapsed', start: i, count: hidden })
      for (let k = j - tail; k < j; k++) out.push({ kind: 'row', row: rows[k], index: k })
    } else {
      for (let k = i; k < j; k++) out.push({ kind: 'row', row: rows[k], index: k })
    }
    i = j
  }
  return out
}

// §9.2: the frame is sized once per comparison to the LONGEST file's row count
// at the code rhythm — the step-script modal's rule and bounds.
export function diffModalFrame(files: DiffFile[]): string {
  const longest = Math.max(1, ...files.map((f) => f.rows.length))
  return `clamp(440px, ${Math.ceil(44 + 38 + longest * 12 * 1.65)}px, 82vh)`
}

// The first CHANGED file is viewed on open (the first file when nothing changed).
export function firstChanged(files: DiffFile[]): number {
  const i = files.findIndex((f) => f.status !== 'unchanged')
  return i < 0 ? 0 : i
}

function ChangeTag({ file, faint }: { file: DiffFile; faint?: boolean }) {
  const mono = { font: "500 11px var(--mono)", whiteSpace: 'nowrap' as const, flex: 'none' as const }
  if (file.status === 'changed') {
    return (
      <span style={{ ...mono, color: 'var(--text-faint)' }} data-testid="diff-tag">
        +<span style={{ color: 'var(--green)' }}>{file.added}</span>
        {' '}−<span style={{ color: 'var(--red)' }}>{file.removed}</span>
      </span>
    )
  }
  const label = { new: 'New', removed: 'Removed', unchanged: 'Unchanged' }[file.status]
  const color = file.status === 'new' ? 'var(--green)' : file.status === 'removed' ? 'var(--red)' : 'var(--text-faint)'
  return <span style={{ ...mono, color: faint && file.status === 'unchanged' ? 'var(--text-deco)' : color }} data-testid="diff-tag">{label}</span>
}

function FileNavRow({ file, viewed, onNav }: { file: DiffFile; viewed: boolean; onNav: () => void }) {
  // §9.2: the viewed row is a plain, unfocusable block; the others are buttons
  // (the step navigator's rule, so no focus ring lingers after a flip).
  const Row: 'div' | 'button' = viewed ? 'div' : 'button'
  return (
    <Row
      className={viewed ? undefined : 'ad-btn-bare ad-hover-row ad-focus-inset'}
      aria-current={viewed ? 'true' : undefined}
      onClick={viewed ? undefined : onNav}
      data-testid={`diff-file-${file.kind === 'step' ? file.file ?? file.name : file.kind}`}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 16px 9px 18px', width: '100%',
        cursor: viewed ? 'default' : 'pointer',
        ...(viewed
          ? { background: 'var(--bg-active)', boxShadow: 'inset 2px 0 0 var(--accent)', userSelect: 'text' as const, textAlign: 'left' as const }
          : {}),
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          font: `${viewed ? 600 : 500} 13px/18px var(--sans)`, color: viewed ? 'var(--text)' : 'var(--text-muted)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {file.name}
        </div>
        <div style={{ font: "400 11px/16px var(--mono)", color: 'var(--text-deco)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {file.file || 'script'}
        </div>
      </div>
      <span style={{ lineHeight: '18px', display: 'flex' }}><ChangeTag file={file} faint /></span>
    </Row>
  )
}

// One side's texts run through the Python highlighter as the whole file they
// are (a step's old script on the left, its new one on the right), so
// multi-line tokens keep their state; the three documents render plain. The
// result is aligned to row indexes (null where the side is absent).
function useSideLines(file: DiffFile, side: 'left' | 'right'): (React.ReactNode | null)[] {
  return useMemo(() => {
    const present = file.rows.map((r) => r[side])
    const texts = present.filter((t): t is NonNullable<typeof t> => t !== null).map((t) => t.text)
    const nodes: React.ReactNode[] = file.kind === 'step' ? highlightPythonLines(texts.join('\n')) : texts
    let k = 0
    return present.map((t) => (t === null ? null : nodes[k++] ?? t.text))
  }, [file, side])
}

const CELL_GUTTER: React.CSSProperties = { textAlign: 'right', padding: '0 12px 0 14px', color: 'var(--text-deco)', userSelect: 'none' }
const CELL_TEXT: React.CSSProperties = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', paddingRight: 12 }

function DiffRows({ file, expanded, onExpand }: { file: DiffFile; expanded: ReadonlySet<number>; onExpand: (start: number) => void }) {
  const items = useMemo(() => diffItems(file, expanded), [file, expanded])
  const leftLines = useSideLines(file, 'left')
  const rightLines = useSideLines(file, 'right')
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr) auto minmax(0, 1fr)', padding: '14px 28px 24px 0',
      font: "400 12px/1.65 var(--mono)", color: 'var(--code-text)',
    }}>
      {items.map((it) => {
        if (it.kind === 'collapsed') {
          return (
            <button
              key={`c${it.start}`} className="ad-btn-bare ad-hover-row" onClick={() => onExpand(it.start)}
              data-testid="diff-collapsed"
              style={{ gridColumn: '1 / -1', font: "500 11px/28px var(--mono)", color: 'var(--text-faint)', textAlign: 'center', width: '100%', cursor: 'pointer' }}
            >
              ⋯ {it.count} unchanged lines
            </button>
          )
        }
        const { row, index } = it
        // §9.2 grounds: del tints the left cells, add the right, mod both
        const lg = row.kind === 'del' || row.kind === 'mod' ? 'var(--diff-del-bg)' : undefined
        const rg = row.kind === 'add' || row.kind === 'mod' ? 'var(--diff-add-bg)' : undefined
        return (
          <React.Fragment key={index}>
            <span style={{ ...CELL_GUTTER, background: lg }}>{row.left?.number ?? ''}</span>
            {/* an empty line carries a newline so a copied selection keeps its blank lines */}
            <span style={{ ...CELL_TEXT, background: lg, borderRight: '1px solid var(--hairline-dim)' }} data-kind={row.left ? row.kind : undefined}>
              {row.left ? (row.left.text.length ? leftLines[index] : '\n') : ''}
            </span>
            <span style={{ ...CELL_GUTTER, background: rg }}>{row.right?.number ?? ''}</span>
            <span style={{ ...CELL_TEXT, background: rg }} data-kind={row.right ? row.kind : undefined}>
              {row.right ? (row.right.text.length ? rightLines[index] : '\n') : ''}
            </span>
          </React.Fragment>
        )
      })}
    </div>
  )
}

export function VersionDiffModal({ automationId, versions, current, from, other: initialOther, onClose }: {
  automationId: string
  versions: DiffVersionRef[]   // every stored version, the current one included
  current: number
  from: number                 // the clicked version — the fixed side
  other?: number               // the initial other side (default: the current version)
  onClose: () => void
}) {
  const [other, setOther] = useState(initialOther ?? current)
  const x = Math.min(from, other)
  const y = Math.max(from, other)
  const [diff, setDiff] = useState<VersionDiff | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [viewed, setViewed] = useState(0)
  const [expanded, setExpanded] = useState<Map<number, Set<number>>>(new Map())
  const [pickOpen, setPickOpen, pickRef] = usePopover()
  // §9.2: Escape with the picker open closes the picker, not the comparison
  // (a ref, so the Modal's guard reads the latest state without re-binding)
  const pickOpenRef = useRef(pickOpen)
  pickOpenRef.current = pickOpen
  const guardClose = () => {
    if (!pickOpenRef.current) return true
    setPickOpen(false)
    return false
  }

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    api.versionDiff(automationId, x, y).then((d) => {
      if (!live) return
      setDiff(d)
      setViewed(firstChanged(d.files))
      setExpanded(new Map())
      setLoading(false)
    }, (e: unknown) => {
      if (!live) return
      setError(e instanceof Error ? e.message : String(e))
      setLoading(false)
    })
    return () => { live = false }
  }, [automationId, x, y])

  const files = diff?.files ?? []
  const file = files[viewed]
  const frame = diff ? diffModalFrame(files) : 'clamp(440px, 60vh, 82vh)'
  const label = (v: number) => `v${v}${v === current ? ' · current' : ''}`
  const others = versions.filter((v) => v.version !== from).sort((a, b) => b.version - a.version)
  const onNav = (i: number) => setViewed(Math.max(0, Math.min(files.length - 1, i)))

  return (
    <Modal
      onClose={onClose} width={1440} ariaLabel={`Changes from v${x} to v${y}`} guardClose={guardClose}
      cardStyle={{ padding: 0, width: 'min(1440px, 94vw)', overflow: 'hidden' }}
    >
      {(close, closing) => (
        <div className="ad-stepmodal" style={{ height: frame, display: 'flex', minWidth: 0 }}>
          <StepKeys i={viewed} count={files.length} closing={closing} onNav={onNav} />
          {/* file navigator */}
          <div className="ad-stepnav" style={{
            width: 280, flex: 'none', minHeight: 0, display: 'flex', flexDirection: 'column',
            borderRight: '1px solid var(--hairline-dim)',
          }}>
            <div style={{
              height: 44, flex: 'none', display: 'flex', alignItems: 'center',
              padding: '0 16px', borderBottom: '1px solid var(--hairline-dim)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>v{x} → v{y}</Eyebrow>
            </div>
            <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
              <div style={{ paddingBottom: 12 }}>
                {files.map((f, j) => (
                  <FileNavRow key={`${f.kind}:${f.file ?? f.name}:${j}`} file={f} viewed={j === viewed} onNav={() => onNav(j)} />
                ))}
              </div>
            </ScrollArea>
          </div>
          {/* diff pane */}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-code)' }}>
            <div style={{
              height: 44, flex: 'none', display: 'flex', alignItems: 'center', gap: 12,
              padding: '0 10px 0 18px', borderBottom: '1px solid var(--hairline-dim)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>{file ? `FILE ${viewed + 1} OF ${files.length}` : 'FILES'}</Eyebrow>
              <span style={{
                font: "400 11px var(--mono)", color: 'var(--text-deco)', flex: 1, minWidth: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {file?.file || ''}
              </span>
              {file && <ChangeTag file={file} />}
              {/* §9.2 "to" picker: swaps the other side; the sides re-order so the older stays left */}
              <div ref={pickRef} style={{ position: 'relative', flex: 'none' }}>
                <button className="ad-btn-pill" data-testid="diff-picker" onClick={() => setPickOpen(!pickOpen)}>
                  <span>vs v{other}</span>
                  <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 9 }} />
                </button>
                <PopMenu show={pickOpen} style={{ top: 'calc(100% + 6px)', right: 0, minWidth: 300, padding: 0, overflow: 'hidden' }}>
                  <ScrollArea style={{ maxHeight: '50vh' }}>
                    {others.map((v) => (
                      <MenuItemRow
                        key={v.version} mono
                        title={label(v.version)}
                        sub={v.version === current ? 'What triggers and Execute now always use.' : v.when + (v.note ? ' · ' + v.note : '')}
                        selected={v.version === other}
                        testId={`diff-pick-${v.version}`}
                        onPick={() => { setPickOpen(false); setOther(v.version) }}
                      />
                    ))}
                  </ScrollArea>
                </PopMenu>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 2, flex: 'none', marginLeft: 4 }}>
                <button className="ad-btn-icon" aria-label="Previous file" disabled={viewed === 0} onClick={() => onNav(viewed - 1)}>
                  <i className="fa-solid fa-chevron-left" />
                </button>
                <button className="ad-btn-icon" aria-label="Next file" disabled={viewed >= files.length - 1} onClick={() => onNav(viewed + 1)}>
                  <i className="fa-solid fa-chevron-right" />
                </button>
                <button className="ad-btn-icon" aria-label="Close" onClick={close}>
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
            </div>
            {error ? (
              <div style={{ padding: 18 }}>
                <Notice tone="red">{`Could not load the comparison. ${error}`}</Notice>
              </div>
            ) : loading || !file ? (
              <PageLoading />
            ) : (
              <ScrollArea key={`${x}-${y}-${viewed}`} className="ad-anim-fade" wrapStyle={{ flex: 1, minHeight: 0 }}>
                <DiffRows
                  file={file}
                  expanded={expanded.get(viewed) ?? new Set()}
                  onExpand={(start) => setExpanded((m) => {
                    const next = new Map(m)
                    next.set(viewed, new Set([...(m.get(viewed) ?? []), start]))
                    return next
                  })}
                />
              </ScrollArea>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
