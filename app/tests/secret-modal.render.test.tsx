// §12 edit modal value states: a set secret opens on a masked "kept" row with
// Replace value (never an empty textarea, since §4.8 never returns the stored
// value); a placeholder opens on the textarea with a NOT SET tag; add mode is
// the plain textarea. Add mode also carries the §4.8 name rules and the
// keyboard saves.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useStore } from '../src/store'
import { SecretModal } from '../src/SecretModal'

const putSecret = vi.fn(async () => ({ id: 's1', name: 'MAIL_PASSWORD', description: 'd', set: true, usedBy: [] }))
const createSecret = vi.fn(async () => ({ id: 's2', name: 'MAIL_PASSWORD', description: '', set: false, usedBy: [] }))
vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    putSecret: (...a: unknown[]) => putSecret(...(a as [])),
    createSecret: (...a: unknown[]) => createSecret(...(a as [])),
  },
}))

const edit = (set: boolean) => ({
  mode: 'edit' as const, id: 's1', name: 'MAIL_PASSWORD', description: 'd', set, usedBy: [],
})

beforeEach(() => {
  useStore.setState({ secrets: [], toast: null })
  putSecret.mockClear()
  createSecret.mockClear()
})
afterEach(() => cleanup())

describe('§12 SecretModal value states', () => {
  it('set secret: kept row, no textarea and no Show until Replace value', () => {
    render(<SecretModal modal={edit(true)} onClose={() => {}} />)
    expect(screen.getByText('••••••••••••')).toBeTruthy()
    expect(screen.getByText('Current value is kept secret')).toBeTruthy()
    expect(screen.queryByRole('textbox', { name: '' })).toBeTruthy() // description input only
    expect(screen.queryByPlaceholderText(/Paste the new value/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show' })).toBeNull()
    expect(screen.queryByText('NOT SET')).toBeNull()
    expect(screen.getByText(/The stored value stays as it is unless you replace it/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Replace value' }))
    const ta = screen.getByPlaceholderText('Paste the new value, or leave blank to keep the current one')
    expect(screen.getByRole('button', { name: 'Show' })).toBeTruthy()
    expect(screen.queryByText('Current value is kept secret')).toBeNull()

    // Keep current value returns to the kept row and discards the draft.
    fireEvent.change(ta, { target: { value: 'typed' } })
    fireEvent.click(screen.getByRole('button', { name: 'Keep current value' }))
    expect(screen.getByText('Current value is kept secret')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Replace value' }))
    expect((screen.getByPlaceholderText(/Paste the new value/) as HTMLTextAreaElement).value).toBe('')
  })

  it('set secret: Save changes with the kept row untouched sends a blank value (description-only)', async () => {
    render(<SecretModal modal={edit(true)} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await vi.waitFor(() => expect(putSecret).toHaveBeenCalledWith('s1', '', 'd'))
  })

  it('placeholder secret: textarea shown directly with a NOT SET tag', () => {
    render(<SecretModal modal={edit(false)} onClose={() => {}} />)
    expect(screen.getByText('NOT SET')).toBeTruthy()
    expect(screen.getByPlaceholderText('Paste the password or API key')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Show' })).toBeTruthy()
    expect(screen.queryByText('Current value is kept secret')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Replace value' })).toBeNull()
    expect(screen.getByText(/This secret has no value yet/)).toBeTruthy()
  })

  it('add mode: plain textarea, no kept row or tag', () => {
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    expect(screen.getByPlaceholderText(/Paste the password or API key, or leave blank/)).toBeTruthy()
    expect(screen.queryByText('NOT SET')).toBeNull()
    expect(screen.queryByText('Current value is kept secret')).toBeNull()
  })
})

describe('§4.8 SecretModal add-mode name rules', () => {
  const nameInput = () => screen.getByPlaceholderText('A short name, like MAIL_PASSWORD or CRM_API_KEY')
  const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save to Keychain' }))

  it('a blank name is refused before the request', async () => {
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    save()
    await vi.waitFor(() => expect(useStore.getState().toast).toBe('Give the secret a name.'))
    expect(createSecret).not.toHaveBeenCalled()
  })

  it('typing sanitizes the name; one that breaks the rule is refused on save', async () => {
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    // §4.8 name shape: uppercased on input, every other character becomes _.
    // A leading digit survives, so the rule still has to catch it on save.
    fireEvent.change(nameInput(), { target: { value: '1mail-password' } })
    expect((nameInput() as HTMLInputElement).value).toBe('1MAIL_PASSWORD')
    save()
    await vi.waitFor(() => expect(useStore.getState().toast)
      .toBe('Secret names must start with a letter and use only A–Z, 0–9 and _.'))
    expect(createSecret).not.toHaveBeenCalled()
  })

  it('a name already in the list is refused with the friendlier message (§4.8)', async () => {
    useStore.setState({ secrets: [{ id: 's1', name: 'MAIL_PASSWORD', description: 'd', set: true, usedBy: [] }] })
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    fireEvent.change(nameInput(), { target: { value: 'MAIL_PASSWORD' } })
    save()
    await vi.waitFor(() => expect(useStore.getState().toast)
      .toBe('MAIL_PASSWORD already exists. Edit it from the list instead.'))
    expect(createSecret).not.toHaveBeenCalled()
  })

  it('Enter saves from the name field; the value textarea needs Cmd/Ctrl+Enter', async () => {
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    fireEvent.change(nameInput(), { target: { value: 'MAIL_PASSWORD' } })
    fireEvent.keyDown(nameInput(), { key: 'Enter' })
    await vi.waitFor(() => expect(createSecret).toHaveBeenCalledWith('MAIL_PASSWORD', '', ''))
    cleanup()

    createSecret.mockClear()
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    fireEvent.change(nameInput(), { target: { value: 'MAIL_PASSWORD' } })
    const value = screen.getByPlaceholderText(/Paste the password or API key, or leave blank/)
    // Multi-line values are allowed, so a plain Enter is a newline (§12).
    fireEvent.keyDown(value, { key: 'Enter' })
    expect(createSecret).not.toHaveBeenCalled()
    fireEvent.keyDown(value, { key: 'Enter', metaKey: true })
    await vi.waitFor(() => expect(createSecret).toHaveBeenCalledTimes(1))
  })
})
