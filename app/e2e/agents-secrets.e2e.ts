// §15 e2e: config-only agent add (no install, no login — the fake `claude`
// answers the real readiness check) and a §4.8 placeholder secret's full
// lifecycle (blank value → NO Keychain write; add → edit description → delete).
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, COPY, clickNav, closeApp, launchApp, shot, type AppHandle } from './harness'

describe('agents and secrets e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  it('adds a config-only agent through the form and sees a real detection state', async () => {
    backend = await new Backend().start()
    await backend.createAutomation('Seed automation') // skips onboarding path
    handle = await launchApp(backend.home, true)
    const { page } = handle

    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })
    await clickNav(page, 'Agents')
    await page.getByRole('button', { name: 'Add your first agent' }).waitFor({ timeout: 10_000 })
    await page.getByRole('button', { name: 'Add your first agent' }).click()

    // The form: name + harness card (mode defaults to "Default model").
    await page.getByRole('heading', { name: 'Add an agent' }).waitFor({ timeout: 10_000 })
    await page.getByPlaceholder('Name this agent').fill('E2E Agent')
    await page.getByText('Claude Code', { exact: true }).click()
    await page.getByRole('button', { name: 'Add agent', exact: true }).click()

    // Back on the list: the saved card with a REAL check state — the fake
    // claude resolves and reports signed in, so the badge lands on Ready.
    await page.getByText('E2E Agent', { exact: true }).waitFor({ timeout: 10_000 })
    await page.getByText('Claude Code · Default model').waitFor()
    await page.getByText('Ready', { exact: true }).waitFor({ timeout: 20_000 })
    await page.getByText('Not used by any automation yet.').waitFor()
    await shot(page, 'agents-added.png')
  }, 120_000)

  it('adds, edits, and deletes a placeholder secret (blank value, never the Keychain)', async () => {
    backend = await new Backend().start()
    await backend.createAutomation('Seed automation')
    handle = await launchApp(backend.home, true)
    const { page } = handle

    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })
    await clickNav(page, 'Secrets')
    await page.getByRole('button', { name: 'Add your first secret' }).waitFor({ timeout: 10_000 })
    await page.getByRole('button', { name: 'Add your first secret' }).click()

    // §4.8 placeholder: name + description, VALUE stays blank — set:false,
    // nothing is written to the OS secret store.
    await page.getByRole('heading', { name: 'New secret' }).waitFor({ timeout: 10_000 })
    await page.getByPlaceholder('A short name, like MAIL_PASSWORD or CRM_API_KEY').fill('E2E_TOKEN')
    await page.getByPlaceholder('Where this secret is used, so the drafting agent knows when to use it')
      .fill('Placeholder for e2e')
    await page.getByRole('button', { name: `Save to ${COPY.secretStore}` }).click()

    // Listed as a placeholder: NOT SET badge, no mask (an em-dash value cell).
    await page.getByText('E2E_TOKEN').waitFor({ timeout: 10_000 })
    await page.getByText('NOT SET').waitFor()
    await page.getByText('Placeholder for e2e').waitFor()
    await page.getByText('—', { exact: true }).waitFor()
    await shot(page, 'secrets-placeholder.png')

    // Edit the description only (blank value keeps none stored).
    await page.getByTitle('Edit').click()
    await page.getByRole('heading', { name: 'Edit secret' }).waitFor({ timeout: 10_000 })
    await page.getByPlaceholder('Where this secret is used, so the drafting agent knows when to use it')
      .fill('Updated e2e description')
    await page.getByRole('button', { name: 'Save changes' }).click()
    await page.getByText('Updated e2e description').waitFor({ timeout: 10_000 })

    // Delete it again — placeholder rows delete without any stored value.
    await page.getByTitle('Delete').click()
    await page.getByText('Delete this secret?').waitFor({ timeout: 10_000 })
    // Scoped to the alertdialog: the row's icon button shares the accessible
    // name "Delete secret" with the confirm button.
    await page.getByRole('alertdialog').getByRole('button', { name: 'Delete secret' }).click()
    await page.getByRole('button', { name: 'Add your first secret' }).waitFor({ timeout: 10_000 })
    expect(await page.getByText('E2E_TOKEN').count()).toBe(0)
    await shot(page, 'secrets-deleted.png')
  }, 120_000)
})
