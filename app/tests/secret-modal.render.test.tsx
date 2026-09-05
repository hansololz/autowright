// §12 edit modal value states: a set secret opens on a masked "kept" row with
// Replace value (never an empty textarea, since §4.8 never returns the stored
// value); a placeholder opens on the textarea with a NOT SET tag; add mode is
// the plain textarea.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useStore } from '../src/store'
import { SecretModal } from '../src/SecretModal'

const putSecret = vi.fn(async () => ({ id: 's1', name: 'MAIL_PASSWORD', description: 'd', set: true, usedBy: [] }))
vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    putSecret: (...a: unknown[]) => putSecret(...(a as [])),
    createSecret: vi.fn(async () => ({})),
  },
}))

const edit = (set: boolean) => ({
  mode: 'edit' as const, id: 's1', name: 'MAIL_PASSWORD', description: 'd', set, usedBy: [],
})

beforeEach(() => { useStore.setState({ secrets: [] }); putSecret.mockClear() })
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
