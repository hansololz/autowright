// §22.3 archive viewer: every text file inside a marketplace entry's archive,
// so the user can read what an Install would land before landing it. The
// step-script modal's frame put to a third use (beside the version diff and the
// §22.7 catalog editor) — the same Modal card, a 280 px file navigator on the
// left and a full-height pane on the code ground on the right. The backend
// opens the zip (§19 GET …/entries/{index}/archive); this file only renders,
// and says nothing about what it shows — the §5.1 validation happens at Install.
import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { StepKeys } from '../steps'
import type { MarketplaceEntry, MarketplaceSource } from '../types'
import { EmptyLine, Eyebrow, Modal, Notice, PageLoading, ScrollArea, highlightPythonLines } from '../ui'

/** One file as the §22.4 route serves it: `text` is null when the member isn't
 * UTF-8 text or is over the 1 MB cap. */
export interface ArchiveFile { path: string; text: string | null }

// §22.3: the fixed titles the navigator shows over an archive path. Anything
// else (a file the §5.1 import would not know either) has no title of its own —
// the row shows its path alone.
const FIXED_TITLES: Record<string, string> = {
  'manifest.yaml': 'Manifest',
  'automation/automation.yaml': 'Automation',
  'automation/spec.md': 'Spec',
  'automation/notes.md': 'Notes',
  'agents.yaml': 'Agents',
  'secrets.yaml': 'Secrets',
}

/** The row's title, or null when the path is its own title: a step script is
 * named by its file name without the §4.1 `NN-` prefix and the `.py`. */
export function archiveFileTitle(path: string): string | null {
  const fixed = FIXED_TITLES[path]
  if (fixed) return fixed
  const step = /^automation\/([^/]+)\.py$/.exec(path)
  if (step) return step[1].replace(/^\d+-/, '')
  return null
}

// A file's rendered lines: a single trailing final newline is neither rendered
// nor counted (§22.3, the step-script modal's rule), `.py` runs through the §11
// Python highlighter and every other file renders plain.
function fileLines(file: ArchiveFile): (React.ReactNode[] | string)[] {
  const text = (file.text ?? '').replace(/\n$/, '')
  if (text === '') return []
  return file.path.endsWith('.py') ? highlightPythonLines(text) : text.split('\n')
}

/** §22.3: the frame is sized once per open to the LONGEST file's line count at
 * the code rhythm — the step-script modal's rule and bounds. */
export function archiveModalFrame(files: ArchiveFile[]): string {
  const longest = Math.max(1, ...files.map((f) => (f.text ?? '').replace(/\n$/, '').split('\n').length))
  return `clamp(440px, ${Math.ceil(44 + 38 + longest * 12 * 1.65)}px, 82vh)`
}

function ArchiveNavRow({ file, viewed, onNav }: { file: ArchiveFile; viewed: boolean; onNav: () => void }) {
  // §22.3: the viewed row is a plain, unfocusable block; the others are buttons
  // (the step navigator's rule, so no focus ring lingers after a flip).
  const Row: 'div' | 'button' = viewed ? 'div' : 'button'
  const title = archiveFileTitle(file.path)
  return (
    <Row
      className={viewed ? undefined : 'ad-btn-bare ad-hover-row ad-focus-inset'}
      aria-current={viewed ? 'true' : undefined}
      onClick={viewed ? undefined : onNav}
      data-testid="archive-file"
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
          {title ?? file.path}
        </div>
        {title && (
          <div style={{ font: "400 11px/16px var(--mono)", color: 'var(--text-deco)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {file.path}
          </div>
        )}
      </div>
    </Row>
  )
}

const ArchiveRows = React.memo(function ArchiveRows({ file }: { file: ArchiveFile }) {
  const lines = useMemo(() => fileLines(file), [file])
  if (file.text === null) return <EmptyLine>{"This file can't be shown as text."}</EmptyLine>
  return (
    <div style={{
      // 28px right padding keeps the longest line clear of the overlay thumb
      display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', padding: '14px 28px 24px 0',
      font: "400 12px/1.65 var(--mono)", color: 'var(--code-text)',
    }}>
      {lines.map((ln, n) => (
        <React.Fragment key={n}>
          <span style={{ textAlign: 'right', padding: '0 16px 0 18px', color: 'var(--text-deco)', userSelect: 'none' }}>{n + 1}</span>
          {/* an empty line carries a newline so a copied selection keeps its blank lines */}
          <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{ln.length ? ln : '\n'}</span>
        </React.Fragment>
      ))}
    </div>
  )
})

export default function ArchiveViewer({ source, entry, onClose }: {
  source: MarketplaceSource
  entry: MarketplaceEntry
  onClose: () => void
}) {
  const [files, setFiles] = useState<ArchiveFile[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewed, setViewed] = useState(0)

  // §22.3: the fetch happens on this open and only on this open — nothing is
  // cached, so an answer that lands after the viewer is gone is dropped.
  useEffect(() => {
    let live = true
    api.marketplaceEntryArchive(source.id, entry.index).then((r) => {
      if (!live) return
      setFiles(r.files)
      setViewed(0)
    }, (e: unknown) => {
      if (!live) return
      setError(e instanceof Error ? e.message : String(e))
    })
    return () => { live = false }
  }, [source.id, entry.index])

  const list = files ?? []
  const file = list[viewed]
  // §22.3: fixed for the life of the open viewer — the floor while the fetch is
  // in flight and after a failed one.
  const frame = files ? archiveModalFrame(files) : '440px'
  const onNav = (i: number) => setViewed(Math.max(0, Math.min(list.length - 1, i)))

  return (
    <Modal
      onClose={onClose} width={1120} ariaLabel="Archive viewer"
      cardStyle={{ padding: 0, width: 'min(1120px, 92vw)', overflow: 'hidden' }}
    >
      {(close, closing) => (
        <div className="ad-stepmodal" style={{ height: frame, display: 'flex', minWidth: 0 }}>
          <StepKeys i={viewed} count={list.length} closing={closing} onNav={onNav} />
          {/* file navigator */}
          <div className="ad-stepnav" style={{
            width: 280, flex: 'none', minHeight: 0, display: 'flex', flexDirection: 'column',
            borderRight: '1px solid var(--hairline-dim)',
          }}>
            <div style={{
              height: 44, flex: 'none', display: 'flex', alignItems: 'center',
              padding: '0 16px', borderBottom: '1px solid var(--hairline-dim)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>ARCHIVE</Eyebrow>
            </div>
            <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
              <div style={{ paddingBottom: 12 }}>
                {list.map((f, j) => (
                  <ArchiveNavRow key={j} file={f} viewed={j === viewed} onNav={() => onNav(j)} />
                ))}
              </div>
            </ScrollArea>
          </div>
          {/* file pane */}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-code)' }}>
            <div style={{
              height: 44, flex: 'none', display: 'flex', alignItems: 'center', gap: 12,
              padding: '0 10px 0 18px', borderBottom: '1px solid var(--hairline-dim)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>{file ? `FILE ${viewed + 1} OF ${list.length}` : 'FILES'}</Eyebrow>
              <span style={{
                font: "400 11px var(--mono)", color: 'var(--text-deco)', flex: 1, minWidth: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {file?.path || ''}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 2, flex: 'none', marginLeft: 4 }}>
                <button className="ad-btn-icon" aria-label="Previous file" disabled={viewed === 0} onClick={() => onNav(viewed - 1)}>
                  <i className="fa-solid fa-chevron-left" />
                </button>
                <button className="ad-btn-icon" aria-label="Next file" disabled={viewed >= list.length - 1} onClick={() => onNav(viewed + 1)}>
                  <i className="fa-solid fa-chevron-right" />
                </button>
                <button className="ad-btn-icon" aria-label="Close" onClick={close}>
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
            </div>
            {error ? (
              // §22.3: the viewer stays open on a failure, so the reason can be read.
              <div style={{ padding: 18 }}>
                <Notice tone="red">{error}</Notice>
              </div>
            ) : files && list.length === 0 ? (
              <EmptyLine>This archive holds no files.</EmptyLine>
            ) : !file ? (
              <PageLoading />
            ) : (
              <ScrollArea key={viewed} className="ad-anim-fade" wrapStyle={{ flex: 1, minHeight: 0 }}>
                <ArchiveRows file={file} />
              </ScrollArea>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
