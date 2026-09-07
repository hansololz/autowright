// §4.8/§12 Secrets page rows: a set secret only ever shows the mask and the
// automations using it, and the delete confirm spells out what breaks before
// the value leaves the Keychain.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { SecretMeta } from '../src/types'
import { useStore } from '../src/store'
import SecretsPage from '../src/pages/SecretsPage'
import { api } from '../src/api'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: { deleteSecret: vi.fn(() => Promise.resolve({})) },
}))
const deleteSecret = vi.mocked(api.deleteSecret)

const SECRET: SecretMeta = {
  id: 's1', name: 'MAIL_PASSWORD', description: 'The mailer login',
  set: true, usedBy: [{ id: 'a', name: 'Nightly' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  deleteSecret.mockResolvedValue({} as never)
  useStore.setState({ secrets: [SECRET], toast: null })
})
afterEach(cleanup)

describe('SecretsPage (§4.8)', () => {
  it('a set secret shows the mask and the automations using it', () => {
    render(<SecretsPage />)
    expect(screen.getByText('MAIL_PASSWORD')).toBeTruthy()
    expect(screen.getByText('••••••••••••')).toBeTruthy()
    expect(screen.getByText('Nightly')).toBeTruthy()
  })

  it('deleting a used secret warns what breaks, then removes it', async () => {
    render(<SecretsPage />)
    fireEvent.click(screen.getByLabelText('Delete secret'))
    expect(await screen.findByText('Delete this secret?')).toBeTruthy()
    expect(screen.getByText('“Nightly” uses it and will stop working.')).toBeTruthy()

    // The row's trash icon carries the same label, so scope to the confirm.
    const confirm = screen.getByRole('alertdialog', { name: 'Delete this secret?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete secret' }))
    await waitFor(() => expect(deleteSecret).toHaveBeenCalledWith('s1'))
    await waitFor(() => expect(useStore.getState().toast).toBe('Removed from your Keychain.'))
  })
})
