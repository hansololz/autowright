// §15 e2e: the full edit loop — spec edit → Sync now (a real steps call
// through the fake claude) → Save as v2 → version history → restore v1 as v3.
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, closeApp, launchApp, shot, type AppHandle } from './harness'

describe('edit loop e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  it('syncs an edited spec, saves v2, and restores v1 as v3', async () => {
    backend = await new Backend().start()
    const agent = await backend.createAgent('Sync Agent') // sync needs a authoring agent
    const { id } = await backend.createAutomation('Edit loop e2e', undefined, { agentId: agent.id })
    handle = await launchApp(backend.home, true)
    const { page } = handle

    // Detail → editor.
    await page.getByText('Edit loop e2e').waitFor({ timeout: 20_000 })
    await page.getByText('Edit loop e2e').click()
    await page.getByRole('button', { name: 'Edit', exact: true }).click()
    await page.getByTestId('test-draft-toggle').waitFor({ timeout: 10_000 })

    // Spec edit — the SPEC card's own Edit button and textarea.
    await page.getByTestId('spec-edit').click()
    const specBox = page.getByTestId('spec-editor')
    await specBox.waitFor({ timeout: 10_000 })
    await specBox.fill(`${await specBox.inputValue()}\nDistinctive sync requirement e2e.`)
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await page.getByText(/steps still match the old spec/).waitFor({ timeout: 10_000 })

    // Sync: the real §8 steps call — the fake claude returns its canonical
    // steps envelope, and the panel returns to the in-sync state.
    await page.getByTestId('sync-steps').click()
    await page.getByText('In sync with the spec.').waitFor({ timeout: 60_000 })
    await page.getByText('Check for changes').waitFor()
    await shot(page, 'edit-loop-synced.png')

    // Save as v2 → detail shows v2.
    await page.getByRole('button', { name: 'Save as v2' }).click()
    await page.getByText('v2', { exact: true }).first().waitFor({ timeout: 20_000 })

    // Restore lives in the EDIT page's version menu (§11): load v1, save as v3.
    await page.getByRole('button', { name: 'Edit', exact: true }).click()
    await page.getByTestId('test-draft-toggle').waitFor({ timeout: 10_000 })
    await page.getByTestId('version-menu').click()
    await page.getByText('v1', { exact: true }).click()
    await page.getByText(/Loaded v1 from history/).waitFor({ timeout: 10_000 })
    await page.getByRole('button', { name: 'Restore v1 as v3' }).click()
    await page.getByText(/restored as version 3/).waitFor({ timeout: 20_000 })
    await page.getByText('v3', { exact: true }).first().waitFor()
    await shot(page, 'edit-loop-restored.png')

    const auto = await backend.api('GET', `/automations/${id}`) as {
      version: number; versions: Array<{ version: number; note: string | null }>
    }
    expect(auto.version).toBe(3)
    // The "Restored from v1" note lands on v3's record; the API only lists
    // notes for non-current versions, so it's checked indirectly via the
    // restore toast above. v1/v2 remain in history:
    expect(auto.versions.map((v) => v.version).sort()).toEqual([1, 2])
  }, 120_000)
})
