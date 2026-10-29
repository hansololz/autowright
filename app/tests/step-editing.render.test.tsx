// Component tests for the §11 step editing surface on the shared step list
// (src/steps.tsx): the §4.1 lock / authorship marks on rows and in the
// step-script modal, Add step, the modal's edit mode (validate-then-apply
// Save, the blank-name gate, the remember-as-build-rule control, the close
// guard), Move up / Move down, the lock toggle, and Remove step's confirm +
// renumbering. The list renders under a small harness that owns the steps the
// way the editor page does, so each hand edit's commit flows back in as new
// props.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import type { Agent, Step } from '../src/types'
import { useStore } from '../src/store'
import {
  STEP_AUTHORED_TITLE, STEP_LOCKED_TITLE, STEP_LOCK_TITLE, STEP_UNLOCK_TITLE, StepList,
  type StepEditEffect, type StepEditing, appendNewStep, newStepName, renumberStepFiles,
} from '../src/steps'

afterEach(() => cleanup())

const AGENT: Agent = { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null }
const step = (over: Partial<Step> = {}): Step => ({ name: 'Fetch page', description: 'reads it', code: 'log("a")', ...over })

function Harness({ initial, validate, onCommit, disabled = false, swapTo }: {
  initial: Step[]
  validate?: (steps: Step[]) => Promise<string[]>
  onCommit?: (steps: Step[], effect: StepEditEffect) => void
  disabled?: boolean
  // what the Swap steps button (a sync/undo/restore) replaces the steps with
  swapTo?: (steps: Step[]) => Step[]
}) {
  const [steps, setSteps] = useState(initial)
  const addRef = useRef<(() => void) | null>(null)
  const editing: StepEditing = {
    disabled,
    validate: validate ?? (async () => []),
    commit: (next, effect) => { onCommit?.(next, effect); setSteps(next) },
    addRef,
  }
  return (
    <>
      <StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} editing={editing} />
      <button onClick={() => addRef.current?.()}>Add step</button>
      <button onClick={() => setSteps(swapTo ? swapTo(steps) : steps.map((s) => ({ ...s })))}>Swap steps</button>
    </>
  )
}

const dialog = () => screen.getByRole('dialog')
const openRow = (name: string) => fireEvent.click(screen.getAllByText(name)[0])
const editor = () => screen.getByTestId('step-editor') as HTMLTextAreaElement
const saveButton = () => within(dialog()).getByText('Save') as HTMLButtonElement

describe('step marks (§4.1/§9.2)', () => {
  it('rows show the faint lock glyph on a locked step and the Written by you tag on an unlocked user step', () => {
    render(
      <StepList
        variant="editor" availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]}
        steps={[step({ name: 'Kept', locked: true, authoredBy: 'user' }), step({ name: 'Mine', authoredBy: 'user' }), step({ name: 'Agent made' })]}
      />,
    )
    const glyphs = screen.getAllByTestId('step-lock-glyph')
    expect(glyphs).toHaveLength(1)
    expect(glyphs[0].getAttribute('title')).toBe(STEP_LOCKED_TITLE)
    // a locked step carries the glyph, not the authorship tag
    const tags = screen.getAllByText('Written by you')
    expect(tags).toHaveLength(1)
    expect(tags[0].closest('span')?.getAttribute('aria-label')).toBe(STEP_AUTHORED_TITLE)
    expect(STEP_LOCKED_TITLE).toBe('Locked — a sync keeps this step byte for byte')
    expect(STEP_AUTHORED_TITLE).toBe('Written by you — the agent keeps it unless the spec requires a change')
  })

  it('the detail rows carry the same marks, and the modal tag row shows the Locked chip', () => {
    render(
      <StepList
        variant="detail" agents={[AGENT]} fallbackAgent="Cloud writer" secrets={[]} packages={[]}
        steps={[step({ name: 'Kept', locked: true }), step({ name: 'Mine', authoredBy: 'user' })]}
      />,
    )
    expect(screen.getAllByTestId('step-lock-glyph')).toHaveLength(1)
    expect(screen.getByText('Written by you')).toBeTruthy()
    expect(screen.queryByText('Locked')).toBeNull() // the row draws the glyph, not the chip
    openRow('Kept')
    const chip = within(dialog()).getByText('Locked')
    expect(chip.closest('span')?.getAttribute('aria-label')).toBe(STEP_LOCKED_TITLE)
    // the detail page never edits: no Edit button, no controls row
    expect(within(dialog()).queryByLabelText('Edit script')).toBeNull()
    expect(within(dialog()).queryByTestId('step-controls')).toBeNull()
  })
})

describe('step editing helpers (§11)', () => {
  it('new step names count up past taken ones; files renumber by position, suffix kept', () => {
    expect(newStepName([])).toBe('New step')
    expect(newStepName([step({ name: 'New step' }), step({ name: 'New step 2' })])).toBe('New step 3')
    const added = appendNewStep([step({ file: '01-a.py' })])
    expect(added[1]).toMatchObject({ name: 'New step', description: '', file: '02-new-step.py', authoredBy: 'user' })
    expect(added[1].code).toBe('# New step\n# Step SDK: see the Build instructions card, or `autowright instructions`.\nfrom autowright import params, log, result\n')
    expect(renumberStepFiles([step({ file: '02-b.py' }), step({ file: '05-c.py' })]).map((s) => s.file)).toEqual(['01-b.py', '02-c.py'])
  })
})

describe('step editing in the step-script modal (§11)', () => {
  it('Add step appends a user-written step and opens it in edit mode with Save disabled', () => {
    const onCommit = vi.fn()
    render(<Harness initial={[step({ file: '01-a.py' })]} onCommit={onCommit} />)
    fireEvent.click(screen.getByText('Add step'))
    expect(onCommit).toHaveBeenCalledTimes(1)
    const [next, effect] = onCommit.mock.calls[0]
    expect(effect).toEqual({ kind: 'add' })
    expect(next).toHaveLength(2)
    expect(next[1]).toMatchObject({ name: 'New step', file: '02-new-step.py', authoredBy: 'user' })
    expect(dialog().getAttribute('aria-label')).toBe('Step 2 of 2: New step')
    expect(editor().value).toContain('from autowright import params, log, result')
    expect(saveButton().disabled).toBe(true)
    // edit mode: flipping and find are inert, the footer reads the in-sync hint
    expect((screen.getByLabelText('Previous step') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('Find in script') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Saving keeps the workflow in sync — a hand edit changes the steps, not the spec.')).toBeTruthy()
  })

  it('Save validates the edited steps: errors render inline and keep the text; a clean pass applies and returns to view mode', async () => {
    const validate = vi.fn<(steps: Step[]) => Promise<string[]>>()
      .mockResolvedValueOnce(['Step 1: import of "requests" is not allowed', 'Step 1: second problem'])
      .mockResolvedValueOnce([])
    const onCommit = vi.fn()
    render(<Harness initial={[step({ file: '01-fetch.py', locked: false })]} validate={validate} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    expect(editor().value).toBe('log("a")')
    fireEvent.change(editor(), { target: { value: 'import requests\nlog("b")' } })
    expect(screen.getByTestId('step-lines').textContent).toBe('2 lines')
    fireEvent.click(saveButton())
    await waitFor(() => expect(screen.getByTestId('step-edit-errors')).toBeTruthy())
    expect(validate.mock.calls[0][0][0].code).toBe('import requests\nlog("b")')
    expect(screen.getByText('Step 1: import of "requests" is not allowed')).toBeTruthy()
    expect(screen.getByText('Step 1: second problem')).toBeTruthy()
    expect(editor().value).toBe('import requests\nlog("b")')
    expect(onCommit).not.toHaveBeenCalled()

    fireEvent.change(editor(), { target: { value: 'log("b")' } })
    fireEvent.change(screen.getByLabelText('Step name'), { target: { value: 'Fetch the page' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    const [next, effect] = onCommit.mock.calls[0]
    expect(effect).toEqual({ kind: 'save', rule: null })
    // the file keeps its name — a rename never renames the script
    expect(next[0]).toMatchObject({ code: 'log("b")', name: 'Fetch the page', file: '01-fetch.py', authoredBy: 'user' })
    // back to view mode on the same step
    await waitFor(() => expect(screen.queryByTestId('step-editor')).toBeNull())
    expect(dialog().getAttribute('aria-label')).toBe('Step 1 of 1: Fetch the page')
  })

  it('a blank name disables Save with the inline hint', () => {
    render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("c")' } })
    expect(saveButton().disabled).toBe(false)
    fireEvent.change(screen.getByLabelText('Step name'), { target: { value: '  ' } })
    expect(saveButton().disabled).toBe(true)
    expect(screen.getByText('Give the step a name')).toBeTruthy()
  })

  it('the remember box swaps the hint for the rule field and Save hands the rule over', async () => {
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("kept")' } })
    fireEvent.click(screen.getByLabelText('Remember this edit as a build rule'))
    const rule = screen.getByPlaceholderText('What every future sync must keep — one line')
    fireEvent.change(rule, { target: { value: 'keep the log line' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    expect(onCommit.mock.calls[0][1]).toEqual({ kind: 'save', rule: { name: 'Fetch page', text: 'keep the log line' } })
  })

  it('a checked remember box with a blank rule disables Save with the inline hint', () => {
    render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("c")' } })
    expect(saveButton().disabled).toBe(false)
    fireEvent.click(screen.getByLabelText('Remember this edit as a build rule'))
    expect(saveButton().disabled).toBe(true)
    expect(screen.getByText('Write the rule to remember')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Build rule'), { target: { value: '   ' } })
    expect(saveButton().disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Build rule'), { target: { value: 'keep it' } })
    expect(saveButton().disabled).toBe(false)
    expect(screen.queryByText('Write the rule to remember')).toBeNull()
  })

  it('while Save validates, the editor is inert: typing is impossible and Escape asks nothing; the edit commits when validation answers', async () => {
    let answer: (errors: string[]) => void = () => {}
    const validate = vi.fn(() => new Promise<string[]>((resolve) => { answer = resolve }))
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} validate={validate} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("saved")' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(validate).toHaveBeenCalledTimes(1))
    // the text fields turn read-only and busy (focus survives), the checkbox,
    // Cancel and ✕ are disabled; Save shows its busy state
    for (const field of [editor(), screen.getByLabelText('Step name'), screen.getByLabelText('Step description')] as HTMLInputElement[]) {
      expect(field.readOnly).toBe(true)
      expect(field.disabled).toBe(false)
      expect(field.getAttribute('aria-busy')).toBe('true')
    }
    expect((screen.getByLabelText('Remember this edit as a build rule') as HTMLInputElement).disabled).toBe(true)
    expect((within(dialog()).getByText('Cancel') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('Close') as HTMLButtonElement).disabled).toBe(true)
    const busy = within(dialog()).getByText('Saving…') as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    expect(busy.getAttribute('aria-busy')).toBe('true')
    // Escape and ⌘S do nothing meanwhile
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    expect(validate).toHaveBeenCalledTimes(1)
    answer([])
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    expect(onCommit.mock.calls[0][0][0].code).toBe('log("saved")')
    await waitFor(() => expect(screen.queryByTestId('step-editor')).toBeNull())
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('⌘S after Discard edits, within the close window, saves nothing', async () => {
    const validate = vi.fn(async () => [] as string[])
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} validate={validate} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("dropped")' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Discard edits'))
    // the confirm settles, the step modal starts closing — its card is still
    // mounted (and the edit still held) through the exit window
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(validate).not.toHaveBeenCalled()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('⌘S saves in edit mode', async () => {
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("s")' } })
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
  })

  it('the lock toggle flips the step and reports it; its tooltip says what a lock does', () => {
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} onCommit={onCommit} />)
    openRow('Fetch page')
    const toggle = within(screen.getByTestId('step-controls')).getByLabelText('Lock step')
    expect(toggle.getAttribute('title')).toBeNull()
    expect(toggle.getAttribute('data-tip')).toBe(STEP_LOCK_TITLE)
    fireEvent.click(toggle)
    expect(onCommit.mock.calls[0][1]).toEqual({ kind: 'lock', name: 'Fetch page', locked: true })
    expect(onCommit.mock.calls[0][0][0].locked).toBe(true)
    // the modal stays open on the step, now showing Locked
    expect(within(screen.getByTestId('step-controls')).getByLabelText('Unlock step').getAttribute('aria-pressed')).toBe('true')
    expect(within(screen.getByTestId('step-controls')).getByLabelText('Unlock step').getAttribute('data-tip')).toBe(STEP_UNLOCK_TITLE)
    fireEvent.click(within(screen.getByTestId('step-controls')).getByLabelText('Unlock step'))
    expect(onCommit.mock.calls[1][1]).toEqual({ kind: 'lock', name: 'Fetch page', locked: false })
    expect('locked' in onCommit.mock.calls[1][0][0]).toBe(false)
  })

  it('Remove step confirms, drops the step, renumbers the files and views the next step', async () => {
    const onCommit = vi.fn()
    render(<Harness
      initial={[step({ name: 'A', file: '01-a.py' }), step({ name: 'B', file: '02-b.py' }), step({ name: 'C', file: '03-c.py', authoredBy: 'user' })]}
      onCommit={onCommit}
    />)
    openRow('A')
    fireEvent.click(within(screen.getByTestId('step-controls')).getByLabelText('Remove step'))
    const ask = screen.getByRole('alertdialog')
    expect(ask.getAttribute('aria-label')).toBe("Remove step 1, 'A'?")
    expect(ask.textContent).toContain('The script is deleted from the draft. The next sync may write it again unless the spec says otherwise.')
    fireEvent.click(within(ask).getByText('Remove step'))
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    const [next, effect] = onCommit.mock.calls[0]
    expect(effect).toEqual({ kind: 'remove' })
    expect(next.map((s: Step) => [s.name, s.file])).toEqual([['B', '01-b.py'], ['C', '02-c.py']])
    expect(dialog().getAttribute('aria-label')).toBe('Step 1 of 2: B')
    // a user-written step's confirm drops the may-write-it-again sentence
    fireEvent.click(screen.getByLabelText('Next step'))
    fireEvent.click(within(screen.getByTestId('step-controls')).getByLabelText('Remove step'))
    expect(screen.getByRole('alertdialog').textContent).not.toContain('The next sync may write it again')
  })

  it('every hand-edit control disables while editing is gated', () => {
    render(<Harness initial={[step()]} disabled />)
    openRow('Fetch page')
    expect((screen.getByLabelText('Edit script') as HTMLButtonElement).disabled).toBe(true)
    const controls = screen.getByTestId('step-controls')
    expect((within(controls).getByLabelText('Move step up') as HTMLButtonElement).disabled).toBe(true)
    expect((within(controls).getByLabelText('Move step down') as HTMLButtonElement).disabled).toBe(true)
    expect((within(controls).getByLabelText('Lock step') as HTMLButtonElement).disabled).toBe(true)
    expect((within(controls).getByLabelText('Remove step') as HTMLButtonElement).disabled).toBe(true)
  })

  it('the controls read Move up, Move down, the lock toggle, Remove step; both moves disable in edit mode', () => {
    render(<Harness initial={[step({ name: 'A', file: '01-a.py' }), step({ name: 'B', file: '02-b.py' }), step({ name: 'C', file: '03-c.py' })]} />)
    openRow('B')
    const controls = screen.getByTestId('step-controls')
    expect(within(controls).getAllByRole('button').map((b) => b.getAttribute('aria-label')))
      .toEqual(['Move step up', 'Move step down', 'Lock step', 'Remove step'])
    expect((within(controls).getByLabelText('Move step up') as HTMLButtonElement).disabled).toBe(false)
    expect((within(controls).getByLabelText('Move step down') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByLabelText('Edit script'))
    expect((within(screen.getByTestId('step-controls')).getByLabelText('Move step up') as HTMLButtonElement).disabled).toBe(true)
    expect((within(screen.getByTestId('step-controls')).getByLabelText('Move step down') as HTMLButtonElement).disabled).toBe(true)
  })

  it('Move down swaps the step, renumbers the files, keeps it viewed and reports the move', () => {
    const onCommit = vi.fn()
    render(<Harness
      initial={[step({ name: 'A', file: '01-a.py' }), step({ name: 'B', file: '02-b.py' }), step({ name: 'C', file: '03-c.py' })]}
      onCommit={onCommit}
    />)
    openRow('A')
    const control = (label: string) => within(screen.getByTestId('step-controls')).getByLabelText(label) as HTMLButtonElement
    expect(control('Move step up').disabled).toBe(true)
    expect(control('Move step up').getAttribute('data-tip')).toBe('Run this step one position earlier (⌥↑)')
    expect(control('Move step down').getAttribute('data-tip')).toBe('Run this step one position later (⌥↓)')
    fireEvent.click(control('Move step down'))
    expect(onCommit).toHaveBeenCalledTimes(1)
    const [next, effect] = onCommit.mock.calls[0]
    expect(effect).toEqual({ kind: 'move', name: 'A', from: 1, to: 2 })
    expect(next.map((s: Step) => [s.name, s.file])).toEqual([['B', '01-b.py'], ['A', '02-a.py'], ['C', '03-c.py']])
    expect(dialog().getAttribute('aria-label')).toBe('Step 2 of 3: A')
    fireEvent.click(control('Move step down'))
    expect(onCommit.mock.calls[1][1]).toEqual({ kind: 'move', name: 'A', from: 2, to: 3 })
    expect(dialog().getAttribute('aria-label')).toBe('Step 3 of 3: A')
    expect(control('Move step down').disabled).toBe(true)
    expect(control('Move step up').disabled).toBe(false)
  })

  it('⌥↓ moves the step and plain ↓ flips', () => {
    const onCommit = vi.fn()
    render(<Harness
      initial={[step({ name: 'A', file: '01-a.py' }), step({ name: 'B', file: '02-b.py' }), step({ name: 'C', file: '03-c.py' })]}
      onCommit={onCommit}
    />)
    openRow('A')
    fireEvent.keyDown(document, { key: 'ArrowDown', altKey: true })
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit.mock.calls[0][1]).toEqual({ kind: 'move', name: 'A', from: 1, to: 2 })
    expect(dialog().getAttribute('aria-label')).toBe('Step 2 of 3: A')
    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(dialog().getAttribute('aria-label')).toBe('Step 3 of 3: C')
  })

  it('Escape over an unsaved edit raises the discard confirm; cancelling keeps the text, confirming closes', async () => {
    render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    const ask = screen.getByRole('alertdialog')
    expect(ask.getAttribute('aria-label')).toBe('Discard your script edits?')
    fireEvent.click(within(ask).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(editor().value).toBe('log("typed")')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Discard edits'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('a steps swap over an unsaved edit asks before closing the modal', async () => {
    render(<Harness initial={[step()]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    fireEvent.click(screen.getByText('Swap steps'))
    const ask = screen.getByRole('alertdialog')
    expect(ask.getAttribute('aria-label')).toBe('Discard your script edits?')
    fireEvent.click(within(ask).getByText('Discard edits'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('a cancelled swap guard keeps the edit on the step of the same name after a reorder (2026-10-28)', async () => {
    const steps = [step({ name: 'Alpha', code: 'log("a")' }), step({ name: 'Beta', code: 'log("b")' })]
    const onCommit = vi.fn()
    render(<Harness initial={steps} onCommit={onCommit} swapTo={(cur) => [{ ...cur[1] }, { ...cur[0] }]} />)
    openRow('Alpha')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    fireEvent.click(screen.getByText('Swap steps'))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    // the edit followed its name to position 2, the typed text intact
    expect(dialog().getAttribute('aria-label')).toBe('Step 2 of 2: Alpha')
    expect(editor().value).toBe('log("typed")')
    fireEvent.click(saveButton())
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    const [next] = onCommit.mock.calls[0] as [Step[]]
    expect(next.map((s) => [s.name, s.code])).toEqual([['Beta', 'log("b")'], ['Alpha', 'log("typed")']])
  })

  it('a cancelled swap guard drops the edit with a toast when its step was renamed away (2026-10-28)', async () => {
    useStore.setState({ toast: null })
    const onCommit = vi.fn()
    render(<Harness initial={[step({ name: '  Alpha ' })]} onCommit={onCommit} swapTo={(cur) => [{ ...cur[0], name: 'Gamma' }]} />)
    openRow('Alpha')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    fireEvent.click(screen.getByText('Swap steps'))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(useStore.getState().toast).toBe("Step 'Alpha' is gone — your unsaved edit was discarded")
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('Save writes nothing while the modal is disabled (2026-10-28)', async () => {
    const validate = vi.fn(async () => [] as string[])
    const onCommit = vi.fn()
    const { rerender } = render(<Harness initial={[step()]} validate={validate} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    rerender(<Harness initial={[step()]} validate={validate} onCommit={onCommit} disabled />)
    fireEvent.click(saveButton())
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(validate).not.toHaveBeenCalled()
    expect(onCommit).not.toHaveBeenCalled()
    expect(editor().value).toBe('log("typed")')
  })

  it('a failed save keeps focus in the script textarea (2026-10-28)', async () => {
    const validate = vi.fn(async () => ['Step 1: bad'])
    render(<Harness initial={[step()]} validate={validate} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(editor(), { target: { value: 'log("typed")' } })
    editor().focus()
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await waitFor(() => expect(screen.getByTestId('step-edit-errors')).toBeTruthy())
    expect(document.activeElement).toBe(editor())
    expect(editor().readOnly).toBe(false)
  })

  it('the description field is a multi-line textarea; a multi-line description saves and renders its line breaks (2026-10-28)', async () => {
    const onCommit = vi.fn()
    render(<Harness initial={[step()]} onCommit={onCommit} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    const field = screen.getByLabelText('Step description')
    expect(field.tagName).toBe('TEXTAREA')
    expect(screen.getByLabelText('Step name').tagName).toBe('INPUT')
    fireEvent.change(field, { target: { value: 'reads it\nthen parses the table' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(onCommit).toHaveBeenCalledTimes(1))
    expect(onCommit.mock.calls[0][0][0].description).toBe('reads it\nthen parses the table')
    // view mode keeps the break as typed
    const shown = within(dialog()).getByText((_, el) => el?.textContent === 'reads it\nthen parses the table' && el.tagName === 'DIV')
    expect(shown.style.whiteSpace).toBe('pre-wrap')
  })

  it('a whitespace-only name or description change is nothing to save (2026-10-28)', () => {
    render(<Harness initial={[step({ authoredBy: undefined })]} />)
    openRow('Fetch page')
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.change(screen.getByLabelText('Step name'), { target: { value: '  Fetch page  ' } })
    fireEvent.change(screen.getByLabelText('Step description'), { target: { value: 'reads it ' } })
    expect(saveButton().disabled).toBe(true)
    // nothing dirty: Cancel returns to view mode without the discard confirm
    fireEvent.click(within(dialog()).getByText('Cancel'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.queryByTestId('step-editor')).toBeNull()
    expect(screen.queryByText('Written by you')).toBeNull()
  })
})
