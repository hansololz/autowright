// Component tests for the §9.2 version diff modal (src/versiondiff.tsx): the
// file navigator with its change tags, the default viewed file, the
// side-by-side rows and their grounds, collapsed same-runs, the "to" picker
// (re-fetching with the older side kept left), arrow-key flips, and the
// error notice. The api module is mocked — the backend diffs, this renders.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { DiffFile, DiffRow, VersionDiff } from '../src/types'

vi.mock('../src/api', () => ({
  api: { versionDiff: vi.fn() },
}))

let mockedApi: { versionDiff: ReturnType<typeof vi.fn> }
let mod: typeof import('../src/versiondiff')

beforeEach(async () => {
  mockedApi = (await import('../src/api')).api as unknown as typeof mockedApi
  mod = await import('../src/versiondiff')
  mockedApi.versionDiff.mockReset()
})
afterEach(() => cleanup())

const same = (n: number, text = `line ${n}`): DiffRow => ({ kind: 'same', left: { number: n, text }, right: { number: n, text } })
const file = (over: Partial<DiffFile>): DiffFile => ({
  kind: 'step', name: 'Fetch', file: '01-fetch.py', status: 'changed', added: 0, removed: 0, rows: [], ...over,
})
const DIFF: VersionDiff = {
  from: 1, to: 3,
  files: [
    file({ kind: 'manifest', name: 'Manifest', file: 'automation.yaml', status: 'unchanged', rows: [same(1, 'params: []')] }),
    file({ kind: 'spec', name: 'Spec', file: 'spec.md', status: 'unchanged', rows: [same(1, '# T')] }),
    file({ kind: 'notes', name: 'Notes', file: 'notes.md', status: 'new', added: 1, rows: [{ kind: 'add', left: null, right: { number: 1, text: 'hello' } }] }),
    file({
      name: 'Fetch', file: '01-fetch.py', status: 'changed', added: 2, removed: 1,
      rows: [
        same(1, 'import os'),
        { kind: 'mod', left: { number: 2, text: 'x = 1' }, right: { number: 2, text: 'x = 22' } },
        { kind: 'add', left: null, right: { number: 3, text: 'y = 3' } },
      ],
    }),
    file({ name: 'Old step', file: '02-old.py', status: 'removed', removed: 1, rows: [{ kind: 'del', left: { number: 1, text: "print('b')" }, right: null }] }),
  ],
}
const VERSIONS = [
  { version: 3, when: '', note: null },
  { version: 2, when: 'updated Jul 2, 2026', note: 'Second' },
  { version: 1, when: 'created Jul 1, 2026', note: null },
]

function open(over: Partial<React.ComponentProps<typeof mod.VersionDiffModal>> = {}) {
  return render(
    <mod.VersionDiffModal automationId="a1" versions={VERSIONS} current={3} from={1} onClose={() => {}} {...over} />,
  )
}

describe('diffItems', () => {
  it('folds a same-run longer than 6 in a changed file, keeping 3 rows of context on each side', () => {
    const rows: DiffRow[] = [...Array.from({ length: 12 }, (_, i) => same(i + 1)), { kind: 'add', left: null, right: { number: 13, text: 'new' } }]
    const items = mod.diffItems(file({ rows }), new Set())
    // a run at the very start keeps no head context
    expect(items[0]).toEqual({ kind: 'collapsed', start: 0, count: 9 })
    expect(items.slice(1, 4).map((it) => it.kind === 'row' && it.row.left?.number)).toEqual([10, 11, 12])
    expect(items[4].kind).toBe('row')
    // an expanded run renders every row
    expect(mod.diffItems(file({ rows }), new Set([0])).every((it) => it.kind === 'row')).toBe(true)
    // a run of exactly 6 never folds
    expect(mod.diffItems(file({ rows: rows.slice(6) }), new Set()).every((it) => it.kind === 'row')).toBe(true)
    // unchanged / new / removed files never fold
    expect(mod.diffItems(file({ status: 'unchanged', rows }), new Set()).every((it) => it.kind === 'row')).toBe(true)
  })

  it('frame follows the longest file and the first changed file is viewed on open', () => {
    expect(mod.diffModalFrame(DIFF.files)).toBe(`clamp(440px, ${Math.ceil(44 + 38 + 3 * 12 * 1.65)}px, 82vh)`)
    expect(mod.firstChanged(DIFF.files)).toBe(2)
    expect(mod.firstChanged(DIFF.files.slice(0, 2))).toBe(0)
  })
})

describe('VersionDiffModal', () => {
  it('lists every file with its change tag, views the first changed one, and renders side-by-side rows', async () => {
    mockedApi.versionDiff.mockResolvedValue(DIFF)
    open()
    expect(mockedApi.versionDiff).toHaveBeenCalledWith('a1', 1, 3)
    await waitFor(() => expect(screen.getByText('FILE 3 OF 5')).toBeTruthy())
    expect(screen.getByText('v1 → v3')).toBeTruthy()
    const tags = screen.getAllByTestId('diff-tag').map((el) => el.textContent)
    // navigator tags (the toolbar repeats the viewed file's)
    expect(tags).toEqual(['Unchanged', 'Unchanged', 'New', '+2 −1', 'Removed', 'New'])
    expect(screen.getByTestId('diff-file-notes').getAttribute('aria-current')).toBe('true')
    // flip to the changed step: a mod row tints both sides, an add row the right only
    fireEvent.click(screen.getByTestId('diff-file-01-fetch.py'))
    expect(screen.getByText('FILE 4 OF 5')).toBeTruthy()
    const dialog = screen.getByRole('dialog')
    const mods = dialog.querySelectorAll('[data-kind="mod"]')
    expect(mods.length).toBe(2)
    expect((mods[0] as HTMLElement).style.background).toBe('var(--diff-del-bg)')
    expect((mods[1] as HTMLElement).style.background).toBe('var(--diff-add-bg)')
    expect(dialog.querySelectorAll('[data-kind="add"]').length).toBe(1)
    expect(dialog.textContent).toContain('x = 22')
    // chevrons and arrow keys flip files; the ends disable
    fireEvent.click(screen.getByLabelText('Next file'))
    expect(screen.getByText('FILE 5 OF 5')).toBeTruthy()
    expect((screen.getByLabelText('Next file') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(screen.getByText('FILE 4 OF 5')).toBeTruthy()
  })

  it('a collapsed run expands in place', async () => {
    const rows: DiffRow[] = [...Array.from({ length: 12 }, (_, i) => same(i + 1)), { kind: 'add', left: null, right: { number: 13, text: 'new' } }]
    // a document file: plain text, so a whole line is one text node
    mockedApi.versionDiff.mockResolvedValue({ from: 1, to: 3, files: [file({ kind: 'notes', name: 'Notes', file: 'notes.md', rows, added: 1 })] })
    open()
    await waitFor(() => expect(screen.getByTestId('diff-collapsed').textContent).toContain('9 unchanged lines'))
    expect(screen.queryByText('line 5')).toBeNull()
    fireEvent.click(screen.getByTestId('diff-collapsed'))
    expect(screen.queryByTestId('diff-collapsed')).toBeNull()
    expect(screen.getAllByText('line 5').length).toBe(2)
  })

  it('the "to" picker lists every version but the clicked one and re-fetches with the older side left', async () => {
    mockedApi.versionDiff.mockResolvedValue(DIFF)
    open({ from: 2 })
    expect(mockedApi.versionDiff).toHaveBeenCalledWith('a1', 2, 3)
    await waitFor(() => expect(screen.getByText('FILE 3 OF 5')).toBeTruthy())
    fireEvent.click(screen.getByTestId('diff-picker'))
    expect(screen.getByTestId('diff-pick-3').textContent).toContain('v3 · current')
    expect(screen.getByTestId('diff-pick-1').textContent).toContain('created Jul 1, 2026')
    expect(screen.queryByTestId('diff-pick-2')).toBeNull()
    mockedApi.versionDiff.mockResolvedValue({ ...DIFF, from: 1, to: 2 })
    fireEvent.click(screen.getByTestId('diff-pick-1'))
    // picked v1 is older than the clicked v2, so v1 goes left
    await waitFor(() => expect(mockedApi.versionDiff).toHaveBeenLastCalledWith('a1', 1, 2))
    await waitFor(() => expect(screen.getByText('v1 → v2')).toBeTruthy())
    expect(screen.getByTestId('diff-picker').textContent).toContain('vs v1')
  })

  it('Escape with the picker open closes the picker, not the modal', async () => {
    mockedApi.versionDiff.mockResolvedValue(DIFF)
    const onClose = vi.fn()
    open({ onClose })
    await waitFor(() => expect(screen.getByText('FILE 3 OF 5')).toBeTruthy())
    fireEvent.click(screen.getByTestId('diff-picker'))
    expect(screen.getByTestId('diff-pick-3')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('diff-pick-3')).toBeNull())
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('a failed fetch shows a red notice in the pane; the navigator header stays', async () => {
    mockedApi.versionDiff.mockRejectedValue(new Error('version v9 not found'))
    open()
    await waitFor(() => expect(screen.getByText(/Could not load the comparison\. version v9 not found/)).toBeTruthy())
    expect(screen.getByText('v1 → v3')).toBeTruthy()
  })
})
