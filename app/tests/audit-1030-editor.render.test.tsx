// Regression tests for the 2026-10-30 step-editor audit (src/steps.tsx,
// src/pages/createflow/DocEditorModal.tsx, src/pages/createflow/model.ts):
// a move during an edit leaves focus and the caret alone, the keyboard move
// is inert while Save validates, Save honours the disabled state, a typed
// remember rule counts as unsaved work for the close guard, ⌘S yields to the
// developer-log overlay, a mid-edit move clears stale validation lines, and
// the sync chip ignores the envelope's newline framing.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import type { Agent, Step } from '../src/types'
import { StepList, type StepEditEffect, type StepEditing } from '../src/steps'
import { DocEditorModal } from '../src/pages/createflow/DocEditorModal'
import { userStepSyncChips } from '../src/pages/createflow/model'

// §9.3: the overlay predicate, switchable per test
const overlay = vi.hoisted(() => ({ open: false }))
vi.mock('../src/devlog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/devlog')>()
  return { ...actual, devlogOverlayOpen: () => overlay.open }
})

afterEach(() => { cleanup(); overlay.open = false })

const AGENT: Agent = { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null }
const step = (over: Partial<Step> = {}): Step => ({ name: 'Fetch page', description: 'reads it', code: 'log("a")', ...over })
const THREE = () => [step({ name: 'A', file: '01-a.py' }), step({ name: 'B', file: '02-b.py' }), step({ name: 'C', file: '03-c.py' })]

function Harness({ initial, validate, onCommit, disabled = false }: {
  initial: Step[]
  validate?: (steps: Step[]) => Promise<string[]>
  onCommit?: (steps: Step[], effect: StepEditEffect) => void
  disabled?: boolean
}) {
  const [steps, setSteps] = useState(initial)
  const addRef = useRef<(() => void) | null>(null)
  const editing: StepEditing = {
    disabled,
    validate: validate ?? (async () => []),
    commit: (next, effect) => { onCommit?.(next, effect); setSteps(next) },
    addRef,
  }
  return <StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} editing={editing} />
}

const dialog = () => screen.getByRole('dialog')
const openRow = (name: string) => fireEvent.click(screen.getAllByText(name)[0])
const editor = () => screen.getByTestId('step-editor') as HTMLTextAreaElement
const saveButton = () => within(dialog()).getByText('Save') as HTMLButtonElement
const control = (label: string) => within(screen.getByTestId('step-controls')).getByLabelText(label) as HTMLButtonElement

describe('U1: a move during an edit leaves focus and the caret where they are', () => {
  it('focus outside the textarea stays outside after a move', () => {
    render(<Harness initial={THREE()} />)
    openRow('A')
    fireEvent.click(screen.getByLabelText('Edit script'))
    expect(document.activeElement).toBe(editor())
    ;(document.activeElement as HTMLElement).blur()
    expect(document.activeElement).not.toBe(editor())
    fireEvent.keyDown(document, { key: 'ArrowDown', altKey: true })
    expect(dialog().getAttribute('aria-label')).toBe('Step 2 of 3: A')
    expect(document.activeElement).not.toBe(editor())
    // so a second ⌥↓ still moves
    fireEvent.keyDown(document, { key: 'ArrowDown', altKey: true })
    expect(dialog().getAttribute('aria-label')).toBe('Step 3 of 3: A')
  })

  it('a focused textarea keeps its caret selection across a move', () => {
    render(<Harness initial={THREE()} />)
    openRow('A')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("one")\nlog("two")' } })
    editor().focus()
    editor().setSelectionRange(3, 5)
    fireEvent.click(control('Move step down'))
    expect(dialog().getAttribute('aria-label')).toBe('Step 2 of 3: A')
    expect(document.activeElement).toBe(editor())
    expect([editor().selectionStart, editor().selectionEnd]).toEqual([3, 5])
  })
})

describe('U2: ⌥↑ / ⌥↓ are inert while Save validates', () => {
  it('a keyboard move during validation commits nothing, and the Save then lands', async () => {
    let answer: (errors: string[]) => void = () => {}
    const validate = vi.fn(() => new Promise<string[]>((resolve) => { answer = resolve }))
    const onCommit = vi.fn()
    render(<Harness initial={THREE()} validate={validate} onCommit={onCommit} />)
    openRow('A')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("saved")' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(validate).toHaveBeenCalledTimes(1))
    ;(document.activeElement as HTMLElement | null)?.blur()
    fireEvent.keyDown(document, { key: 'ArrowDown', altKey: true })
    expect(onCommit).not.toHaveBeenCalled()
    expect(dialog().getAttribute('aria-label')).toBe('Step 1 of 3: A')
    answer([])
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    expect(onCommit.mock.calls[0][1]).toMatchObject({ kind: 'save' })
    expect(onCommit.mock.calls[0][0][0]).toMatchObject({ name: 'A', code: 'log("saved")' })
  })
})

describe('U3: Save is disabled while the modal is disabled', () => {
  it('a dirty edit shows Save disabled once editing is gated', () => {
    const { rerender } = render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    expect(saveButton().disabled).toBe(false)
    rerender(<Harness initial={[step()]} disabled />)
    expect(saveButton().disabled).toBe(true)
  })
})

describe('U4: a typed remember rule is unsaved work for the close guard', () => {
  it('Cancel and Escape ask before dropping a rule; Save stays disabled', async () => {
    render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.click(screen.getByLabelText('Remember this edit as a build rule'))
    fireEvent.change(screen.getByLabelText('Build rule'), { target: { value: 'keep the log line' } })
    expect(saveButton().disabled).toBe(true)
    fireEvent.click(within(dialog()).getByText('Cancel'))
    let ask = screen.getByRole('alertdialog')
    expect(ask.getAttribute('aria-label')).toBe('Discard your script edits?')
    fireEvent.click(within(ask).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect((screen.getByLabelText('Build rule') as HTMLInputElement).value).toBe('keep the log line')
    fireEvent.keyDown(document, { key: 'Escape' })
    ask = screen.getByRole('alertdialog')
    expect(ask.getAttribute('aria-label')).toBe('Discard your script edits?')
  })

  it('a blank rule asks nothing', async () => {
    render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.click(screen.getByLabelText('Remember this edit as a build rule'))
    fireEvent.change(screen.getByLabelText('Build rule'), { target: { value: '   ' } })
    fireEvent.click(within(dialog()).getByText('Cancel'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(screen.queryByTestId('step-editor')).toBeNull())
  })
})

describe('U5: ⌘S yields to the developer-log overlay', () => {
  it('the step-script modal saves nothing while the overlay is open', async () => {
    const validate = vi.fn(async () => [] as string[])
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} validate={validate} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    overlay.open = true
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(validate).not.toHaveBeenCalled()
    expect(onCommit).not.toHaveBeenCalled()
    overlay.open = false
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
  })

  it('the document-editor modal saves nothing while the overlay is open', async () => {
    const onSave = vi.fn()
    render(<DocEditorModal kind="spec" text="changed" original="original" onChange={() => {}} onSave={onSave} onDiscard={() => {}} />)
    overlay.open = true
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(onSave).not.toHaveBeenCalled()
    overlay.open = false
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
  })
})

describe('U6: a move or lock during an edit clears stale validation lines', () => {
  it('Move down drops the last Save attempt\'s errors and keeps the text', async () => {
    const validate = vi.fn(async () => ['02-b.py: syntax error (line 1)'])
    render(<Harness initial={THREE()} validate={validate} />)
    openRow('B')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log(' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(screen.getByTestId('step-edit-errors')).toBeTruthy())
    fireEvent.click(control('Move step down'))
    expect(dialog().getAttribute('aria-label')).toBe('Step 3 of 3: B')
    expect(screen.queryByTestId('step-edit-errors')).toBeNull()
    expect(editor().value).toBe('log(')
  })

  it('the lock toggle drops them too', async () => {
    const validate = vi.fn(async () => ['01-a.py: syntax error (line 1)'])
    render(<Harness initial={[step({ name: 'A', file: '01-a.py' })]} validate={validate} />)
    openRow('A')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log(' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(screen.getByTestId('step-edit-errors')).toBeTruthy())
    fireEvent.click(control('Lock step'))
    expect(screen.queryByTestId('step-edit-errors')).toBeNull()
    expect(editor().value).toBe('log(')
  })
})

describe('P6: the sync chip compares scripts modulo the envelope trim', () => {
  const mine = (code: string): Step => step({ name: 'Mine', authoredBy: 'user', code })
  it('a newline-framing difference earns no chip', () => {
    expect(userStepSyncChips([mine('log("a")')], [mine('log("a")\n')])).toEqual([])
    expect(userStepSyncChips([mine('\n\nlog("a")\n')], [mine('log("a")')])).toEqual([])
  })
  it('a real change earns the rewritten chip', () => {
    const chips = userStepSyncChips([mine('log("a")\n')], [mine('log("b")\n')])
    expect(chips).toHaveLength(1)
    expect(chips[0].text).toBe("Your step 'Mine' was rewritten.")
  })
})
