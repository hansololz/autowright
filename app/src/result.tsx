// Result section (§7): a collapsible stack of result views — one view per
// renderable file (.md markdown, .html in a sandboxed iframe, images inline),
// and a collapsible FILES footer with a "Show in Finder" button. Collapse
// state is component state only (§7: per session, never persisted).
import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'
import { usePlatformCopy } from './platformCopy'
import { Caret, Collapse, EmptyLine, EmptyNotice, Eyebrow, LoadingRow, MetaChip, resultChipColors, Tag } from './ui'
import type { ResultFile, ExecutionResult, SpecBlock } from './types'

const MD_EXT = ['md', 'markdown']
const HTML_EXT = ['html', 'htm']
const IMG_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg']
const TEXT_EXT = ['csv', 'json', 'txt', 'yaml', 'yml', 'log', 'tsv', 'xml']

// §7 text preview caps — a multi-MB CSV must not freeze the renderer.
const TEXT_MAX_BYTES = 200_000
const TEXT_MAX_LINES = 2000

// The name §6.1 tells steps to write, and the only file the §9.2 compact card
// promotes into its single top view slot.
const PRIMARY_FILE = 'result.md'

export function ext(name: string): string {
  return name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
}

// Renderable kinds get a §7 file view of their own; `text` is preview-only —
// it never earns a top-level view, just an expandable FILES row.
export function fileKind(name: string): 'md' | 'html' | 'img' | null {
  const e = ext(name)
  if (MD_EXT.includes(e)) return 'md'
  if (HTML_EXT.includes(e)) return 'html'
  if (IMG_EXT.includes(e)) return 'img'
  return null
}

function previewKind(name: string): 'md' | 'html' | 'img' | 'text' | null {
  return fileKind(name) ?? (TEXT_EXT.includes(ext(name)) ? 'text' : null)
}

const KIND_LABEL = { md: 'markdown', html: 'web page', img: 'image', text: 'text' } as const

// ---------- collapsible view card ----------

// Also the §7 execution page's PARAMETERS and WORKSPACE cards (collapsed by
// default there), so every collapsible card on that page shares one header.
export function ViewCard({ title, kind, meta, mono = true, defaultOpen = true, testId, children }: {
  title: string; kind?: string; meta?: string; mono?: boolean; defaultOpen?: boolean; testId?: string
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="ad-card" style={{ overflow: 'hidden' }} data-testid={testId}>
      <button
        className="ad-btn-bare ad-hover-row ad-focus-inset"
        onClick={() => setOpen(!open)}
        style={{
          display: 'flex', alignItems: 'center', gap: 9, padding: '12px 18px', cursor: 'pointer',
        }}
      >
        <Caret open={open} style={{ fontSize: 10, width: 10, flex: 'none' }} />
        <span style={{
          fontFamily: mono ? 'var(--mono)' : undefined, fontSize: 12.5,
          fontWeight: 500, color: 'var(--text)',
        }}>
          {title}
        </span>
        {kind && <Tag>{kind}</Tag>}
        <span style={{ flex: 1 }} />
        {meta && <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)' }}>{meta}</span>}
      </button>
      <Collapse open={open}>{children}</Collapse>
    </div>
  )
}

// ---------- markdown (§4.5 shared renderer) ----------

export const Markdown = React.memo(function Markdown({ text, small }: { text: string; small?: boolean }) {
  // GFM via react-markdown + remark-gfm; output is React elements (never
  // injected HTML). Styling lives in tokens.css under .ad-md; `small` is the
  // §4.5 compact variant for the chat thread (.ad-md-sm).
  // Memoized: the chat thread re-renders once a second while a job runs (the
  // live-duration tick) — without memo every settled entry re-parses its
  // markdown on each tick.
  return (
    <div className={small ? 'ad-md ad-md-sm' : 'ad-md'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          table: ({ node: _, ...props }) => <div className="ad-md-tablewrap"><table {...props} /></div>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})

// Spec cards (create flow + automation page): SpecBlock[] → markdown, rendered
// by the same shared component as every other markdown surface.
export function SpecMarkdown({ blocks }: { blocks: SpecBlock[] }) {
  const md = blocks.map((b, i) => {
    const line = b.kind === 'h1' ? '# ' + b.text : b.kind === 'h2' ? '## ' + b.text : b.kind === 'li' ? '- ' + b.text : b.text
    // adjacent li stay one list; everything else separates into its own block
    return (i === 0 ? '' : b.kind === 'li' && blocks[i - 1].kind === 'li' ? '\n' : '\n\n') + line
  }).join('')
  return <Markdown text={md} />
}

// ---------- file views ----------

// §4.5: sandboxed page — no scripts (sandbox omits allow-scripts), no remote
// loads (CSP), links open outside (allow-popups + base target), app-styled by
// the injected base stylesheet; the page's own inline CSS overrides it.
const HTML_BASE = `<head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<base target="_blank">
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding: 4px 18px 16px; background: #191d25;
         font: 400 13px/1.6 'IBM Plex Sans', -apple-system, sans-serif; color: #c8ccd4;
         -webkit-font-smoothing: antialiased; }
  h1, h2, h3 { color: #e9ebef; font-weight: 600; margin: 14px 0 6px; }
  h1 { font-size: 15px; } h2 { font-size: 13.5px; } h3 { font-size: 12.5px; }
  p { margin: 8px 0; } a { color: oklch(0.74 0.155 52); text-decoration: none; }
  a:hover { color: oklch(0.82 0.14 60); text-decoration: underline; }
  code { font-family: 'IBM Plex Mono', monospace; font-size: .92em; }
  pre { font: 400 11.5px/1.7 'IBM Plex Mono', monospace; background: #0c0f16; border: 1px solid rgba(255,255,255,.06); border-radius: 8px; padding: 10px 14px; overflow-x: auto; }
  pre code { font: inherit; }
  table { border-collapse: collapse; width: 100%; }
  th { font: 600 9.5px 'IBM Plex Mono', monospace; letter-spacing: .09em; text-transform: uppercase;
       color: #828893; text-align: left; padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,.06); }
  td { font-size: 12.5px; line-height: 1.55; color: #9da3af; padding: 10px; border-bottom: 1px solid rgba(255,255,255,.06); }
  td:first-child { color: #e9ebef; font-weight: 500; }
  img { max-width: 100%; }
</style>
</head>`

function HtmlView({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(120)
  return (
    <iframe
      ref={ref}
      title="result page"
      sandbox="allow-same-origin allow-popups"
      srcDoc={HTML_BASE + html}
      onLoad={() => {
        const doc = ref.current?.contentDocument
        if (doc) setHeight(Math.min(720, doc.documentElement.scrollHeight + 4))
      }}
      style={{ width: '100%', height, border: 'none', display: 'block', colorScheme: 'dark' }}
    />
  )
}

function TextView({ text }: { text: string }) {
  // §9 per-OS copy rule: the reveal action this note points at.
  const copy = usePlatformCopy()
  const lines = text.split('\n')
  const cut = text.length > TEXT_MAX_BYTES || lines.length > TEXT_MAX_LINES
  const shown = cut
    ? lines.slice(0, TEXT_MAX_LINES).join('\n').slice(0, TEXT_MAX_BYTES)
    : text
  return (
    <div style={{ padding: '2px 18px 16px' }}>
      <pre style={{
        margin: 0, fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.7,
        color: 'var(--text-2)', overflowX: 'auto', whiteSpace: 'pre',
      }}>
        {shown}
      </pre>
      {cut && (
        <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-faint)' }}>
          Truncated — use {copy.reveal} for the full file.
        </div>
      )}
    </div>
  )
}

// The one loader behind both surfaces: a top-level file view and an expanded
// FILES row render the exact same body. Mounted only once its card is open, so
// a collapsed row costs no request (§7).
function FileBody({ executionId, file, kind, stamp }: {
  executionId: string; file: ResultFile; kind: 'md' | 'html' | 'img' | 'text'; stamp?: string
}) {
  const [text, setText] = useState<string | null>(null)
  const [imgUrl, setImgUrl] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let dead = false
    let url: string | null = null
    void api.resultFile(executionId, file.name)
      .then(async (r) => (kind === 'img' ? URL.createObjectURL(await r.blob()) : r.text()))
      .then((v) => {
        if (dead) { if (kind === 'img') URL.revokeObjectURL(v as string); return }
        if (kind === 'img') { url = v as string; setImgUrl(url) } else setText(v as string)
      })
      .catch((e: Error) => { if (!dead) setErr(e.message) })
    return () => { dead = true; if (url) URL.revokeObjectURL(url) }
    // `stamp`/size in the deps: a §7 in-place retry reuses the same executionId and
    // result dir, and a mid-run mount can catch a half-written file — the view
    // must refetch when the execution settles, not keep the stale bytes.
  }, [executionId, file.name, file.size, stamp])
  if (err) {
    return (
      <div style={{ padding: '0 18px 14px', fontSize: 12.5, color: 'var(--red-text)' }}>
        Couldn’t load {file.name} — {err}
      </div>
    )
  }
  if (kind === 'img') {
    return imgUrl
      ? <div style={{ padding: '0 18px 16px' }}><img src={imgUrl} alt={file.name} style={{ maxWidth: '100%', borderRadius: 8 }} /></div>
      : <LoadingRow label="Loading…" style={{ padding: '14px 18px' }} />
  }
  if (text === null) return <LoadingRow label="Loading…" style={{ padding: '14px 18px' }} />
  if (kind === 'md') return <div style={{ padding: '2px 18px 16px' }}><Markdown text={text} /></div>
  if (kind === 'text') return <TextView text={text} />
  return <HtmlView html={text} />
}

function FileView({ executionId, file, stamp }: { executionId: string; file: ResultFile; stamp?: string }) {
  const kind = fileKind(file.name)!
  return (
    <ViewCard title={file.name} kind={KIND_LABEL[kind]} meta={file.size}>
      <FileBody executionId={executionId} file={file} kind={kind} stamp={stamp} />
    </ViewCard>
  )
}

// ---------- FILES footer ----------

// One file row. A previewable file's row is a chevron button that renders the
// same body as a top-level view; its bytes are fetched on first open only, so a
// collapsed footer (§9.2) issues no requests at all.
function FileRow({ executionId, file, stamp, last }: {
  executionId: string; file: ResultFile; stamp?: string; last: boolean
}) {
  const kind = previewKind(file.name)
  const [open, setOpen] = useState(false)
  const [opened, setOpened] = useState(false)
  const border = last ? 'none' : '1px solid var(--hairline-dim)'
  const row = (
    <>
      {kind
        ? <Caret open={open} style={{ fontSize: 10, width: 14, flex: 'none', color: 'var(--text-faint)' }} />
        : <i className="fa-solid fa-file-lines" style={{ fontSize: 11, color: 'var(--text-faint)', width: 14, flex: 'none' }} />}
      <span style={{ flex: 1, minWidth: 0, fontFamily: 'var(--mono)', fontSize: 12.5, fontWeight: 500, color: 'var(--text-2)', overflowWrap: 'break-word' }}>
        {file.name}
      </span>
      {!kind && (
        <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)', flex: 'none' }}>no preview</span>
      )}
      <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)', flex: 'none' }}>{file.size}</span>
    </>
  )
  const rowStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 10, padding: '12px 18px',
  }
  if (!kind) return <div style={{ ...rowStyle, borderBottom: border }}>{row}</div>
  return (
    <div style={{ borderBottom: border }}>
      <button
        className="ad-btn-bare ad-hover-row ad-focus-inset"
        onClick={() => { setOpen(!open); setOpened(true) }}
        style={{ ...rowStyle, cursor: 'pointer' }}
      >
        {row}
      </button>
      <Collapse open={open}>
        {opened && <FileBody executionId={executionId} file={file} kind={kind} stamp={stamp} />}
      </Collapse>
    </div>
  )
}

function FilesFooter({ files, path, executionId, stamp, defaultOpen = true }: {
  files: ResultFile[]; path?: string; executionId: string; stamp?: string; defaultOpen?: boolean
}) {
  // §9 per-OS copy rule: the reveal button's label.
  const copy = usePlatformCopy()
  return (
    <ViewCard title={`FILES · ${files.length}`} mono defaultOpen={defaultOpen}>
      <div style={{ paddingBottom: 12 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px 10px',
          borderBottom: '1px solid var(--hairline-dim)',
        }}>
          <span style={{
            flex: 1, minWidth: 0, fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', direction: 'rtl', textAlign: 'left',
          }}>
            {path ?? ''}
          </span>
          {path && (
            <button
              className="ad-btn-ghost"
              onClick={() => { void window.autowright?.revealPath(path) }}
              style={{ flex: 'none' }}
            >
              <i className="fa-solid fa-folder-open" style={{ fontSize: 10 }} /> {copy.reveal}
            </button>
          )}
        </div>
        {files.map((f, i) => (
          <FileRow key={f.name} executionId={executionId} file={f} stamp={stamp} last={i === files.length - 1} />
        ))}
        {files.length === 0 && <EmptyLine>No files.</EmptyLine>}
      </div>
    </ViewCard>
  )
}

// ---------- the section ----------

export function ResultSection({ label, result, executionId, stamp, compact }: {
  label: string; result: ExecutionResult & { when?: string }; executionId: string
  stamp?: string  // freshness key — changes when the execution settles (see FileBody)
  // §9.2 LATEST RESULT: only `result.md` gets a view, and the FILES footer
  // carries everything else — collapsed, unless there is no `result.md` to show.
  compact?: boolean
}) {
  const [open, setOpen] = useState(true)
  const { c, bg } = resultChipColors(result.chipStatus)
  const chip = result.chip
  const files = result.files ?? []
  const primary = files.find((f) => f.name === PRIMARY_FILE)
  const views = compact ? (primary ? [primary] : []) : files.filter((f) => fileKind(f.name) !== null)
  const empty = files.length === 0
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <button
          className="ad-btn-text small"
          onClick={() => setOpen(!open)}
          style={{ display: 'flex', alignItems: 'center', gap: 9, cursor: 'pointer' }}
        >
          <Caret open={open} style={{ fontSize: 10, width: 10 }} />
          <Eyebrow>{label}</Eyebrow>
        </button>
        {chip && <MetaChip c={c} bg={bg}>{chip}</MetaChip>}
        {result.when && (
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)' }}>{result.when}</span>
        )}
      </div>
      <Collapse open={open}>
        {empty ? (
          <EmptyNotice body="The latest execution didn’t produce any result files." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {views.map((f) => <FileView key={f.name} executionId={executionId} file={f} stamp={stamp} />)}
            <FilesFooter
              files={files} path={result.path} executionId={executionId} stamp={stamp}
              defaultOpen={!compact || !primary}
            />
          </div>
        )}
      </Collapse>
    </div>
  )
}
