// §22.6 e2e: one file-based marketplace under the test data root — a catalog
// plus an archive this test exports itself — driven end to end. The nav row is
// gated on §4.9 developerMode (turned on through the real Settings toggle), the
// §22.4 marketplace.changed event brings the added source in without a reload,
// and Install runs the ordinary §5.2 two-phase import, landing the automation
// with its triggers off. Then the §22.7 authoring half: New catalog… on a temp
// folder (the native picker stubbed in the main process), the automation
// picker, and Save landing the exported archive beside the new catalog.
import { mkdir, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, clickNav, closeApp, launchApp, shot, waitFor, type AppHandle } from './harness'

describe('marketplace e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  it('gates the page on developer mode, lists a file source, and installs an entry', async () => {
    backend = await new Backend().start()
    // The shelf the catalog ships from: one automation, exported to an
    // archive beside the catalog. It lives OUTSIDE the §22.2 marketplace/
    // store dir — a hand-made shelf is just a folder on the machine.
    const { id } = await backend.createAutomation('Watcher')
    // An enabled trigger, so "lands with its triggers off" has something to
    // assert (§5.1 import rule).
    await backend.api('PATCH', `/automations/${id}`, {
      triggers: [{ kind: 'cron', source: 'user', enabled: true, expression: '0 8 * * *' }],
    })
    const archive = await backend.apiBytes('GET', `/automations/${id}/export?values=0`)
    const shelf = path.join(backend.home, 'shelf')
    await mkdir(shelf, { recursive: true })
    await writeFile(path.join(shelf, 'watcher.autowright'), archive)
    const catalog = path.join(shelf, 'marketplace-catalog.yaml')
    await writeFile(catalog, [
      'format_version: 1',
      'name: "E2E shelf"',
      'entries:',
      '  - title: "Watcher"',
      '    description: "From the e2e shelf."',
      '    path: watcher.autowright',
      '',
    ].join('\n'), 'utf-8')

    handle = await launchApp(backend.home, true)
    const { page } = handle
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })

    // §22 preview gate: nothing in the rail until developer mode is on.
    expect(await page.getByTestId('nav-marketplace').count()).toBe(0)

    // The real §4.9 toggle, not a seeded setting.
    await clickNav(page, 'Settings')
    const developerCard = page.locator('.ad-card').filter({ hasText: 'Developer mode' }).last()
    await developerCard.getByRole('switch').waitFor({ timeout: 10_000 })
    await developerCard.getByRole('switch').click()
    await waitFor(async () => {
      const s = (await backend!.api('GET', '/state') as { settings: { developerMode: boolean } }).settings
      return s.developerMode
    }, 10_000, 'developerMode to persist')
    await page.getByTestId('nav-marketplace').waitFor({ timeout: 10_000 })

    // Empty state: the headline plus the §22.1 example catalog.
    await clickNav(page, 'Marketplace')
    await page.getByText('No marketplaces yet').waitFor({ timeout: 10_000 })
    await page.getByText('format_version: 1').waitFor({ timeout: 10_000 })
    expect(await page.getByTestId('marketplace-add').count()).toBeGreaterThan(0)
    expect(await page.getByTestId('marketplace-refresh-all').count()).toBe(0)
    await shot(page, 'marketplace-empty.png')

    // The add modal's file step is the native open dialog, which can't be
    // driven — add through the §22.4 route instead and let the page hear about
    // it the way a §20 CLI add would: the marketplace.changed event, no reload.
    await backend.api('POST', '/marketplace/sources', { path: catalog })
    await waitFor(async () => (await page.getByTestId('marketplace-source').count()) === 1,
      20_000, 'marketplace.changed to bring the source in without a reload')
    await page.getByText('E2E shelf', { exact: true }).waitFor({ timeout: 10_000 })
    const entry = page.getByTestId('marketplace-entry')
    await entry.getByText('Watcher', { exact: true }).waitFor({ timeout: 10_000 })
    await entry.getByText('From the e2e shelf.').waitFor()
    // §22.1: this catalog declares no `url`, so it is a one-time download -
    // neither Refresh all nor a per-source Refresh is offered.
    expect(await page.getByTestId('marketplace-refresh-all').count()).toBe(0)
    expect(await page.getByRole('button', { name: 'Refresh' }).count()).toBe(0)
    await page.getByText(/^Added /).waitFor()
    await shot(page, 'marketplace-source.png')

    // Install: the §9.1 import modal opens straight on its preview step. The
    // archive's own automation is already here, so it lands deduped (§5.1).
    await page.getByTestId('marketplace-install').click()
    await page.getByRole('heading', { name: 'Watcher 2' }).waitFor({ timeout: 20_000 })
    await page.getByText('E2E shelf · Watcher').waitFor()
    await shot(page, 'marketplace-install-preview.png')
    await page.getByRole('button', { name: 'Import', exact: true }).click()

    // §9.1 summary modal, then through to the landed automation.
    await page.getByText(/^Imported/).waitFor({ timeout: 30_000 })
    await shot(page, 'marketplace-install-summary.png')
    await page.getByRole('button', { name: 'Open automation' }).click()
    await page.getByRole('button', { name: 'Execute now' }).waitFor({ timeout: 10_000 })
    await page.getByText(/All triggers are off/).waitFor({ timeout: 10_000 })
    await shot(page, 'marketplace-installed-automation.png')

    // §5.1: a second automation, every trigger off.
    const autos = await backend.api('GET', '/automations') as
      Array<{ name: string; triggers: Array<{ enabled: boolean }> }>
    expect(autos.length).toBe(2)
    const landed = autos.find((a) => a.name === 'Watcher 2')!
    expect(landed.triggers.length).toBe(1)
    expect(landed.triggers.every((t) => t.enabled)).toBe(false)

    // §22.7 authoring: New catalog… opens the native folder picker, which can't
    // be driven - the main-process dialog answers this test's temp folder
    // instead. It is empty: the catalog and the archive both land through Save.
    const authored = path.join(backend.home, 'authored')
    await mkdir(authored, { recursive: true })
    await handle.app.evaluate(({ dialog }, folder) => {
      dialog.showOpenDialog = () => Promise.resolve({ canceled: false, filePaths: [folder] })
    }, authored)

    await clickNav(page, 'Marketplace')
    await page.getByTestId('marketplace-new').click()
    await page.getByTestId('catalog-editor').waitFor({ timeout: 20_000 })
    await page.getByTestId('catalog-add-automation').click()
    // Both automations are here by now (the import landed "Watcher 2") - the
    // seeded one is the row whose title is exactly "Watcher".
    await page.getByTestId('catalog-picker-row')
      .filter({ has: page.getByText('Watcher', { exact: true }) })
      .click()
    // §22.7: the archive doesn't exist yet - the save exports it.
    await page.getByTestId('catalog-row').getByText('Exported on save').waitFor({ timeout: 10_000 })
    // The picker stays mounted through its exit animation - shoot the settled editor.
    await page.getByTestId('catalog-picker-search').waitFor({ state: 'detached', timeout: 10_000 })
    await shot(page, 'marketplace-editor.png')

    await page.getByTestId('catalog-save').click()
    await waitFor(async () => (await page.getByTestId('marketplace-source').count()) === 2,
      20_000, 'the authored catalog to land as a second source')
    await page.getByTestId('marketplace-source').last()
      .getByTestId('marketplace-entry').getByText('Watcher', { exact: true })
      .waitFor({ timeout: 10_000 })
    // Same for the editor: it closes after the save lands.
    await page.getByTestId('catalog-editor').waitFor({ state: 'detached', timeout: 10_000 })
    await shot(page, 'marketplace-authored.png')

    // §22.7: the catalog plus the archives it lists, written flat in the folder.
    expect((await readdir(authored)).sort())
      .toEqual(['Watcher.autowright', 'marketplace-catalog.yaml'])

    // §22.3: the setting dropping while the page is open leaves for Automations.
    await clickNav(page, 'Marketplace')
    await page.getByRole('heading', { name: 'Marketplace' }).waitFor({ timeout: 10_000 })
    await backend.api('PATCH', '/settings', { developerMode: false })
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })
    await waitFor(async () => (await page.getByTestId('nav-marketplace').count()) === 0,
      10_000, 'the Marketplace nav row to go away')
    await shot(page, 'marketplace-gate-dropped.png')
  }, 120_000)
})
