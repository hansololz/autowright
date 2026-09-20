// §22.6 e2e: one file-based marketplace under the test data root — a catalog
// plus an archive this test exports itself — driven end to end. The nav row
// renders for everyone with no setting gating it (§22 visibility), the
// §22.4 marketplace.changed event brings the added row in without a reload, the
// §22.2 file location is refreshable like a link, the §22.3 Audit button opens
// the archive viewer on the entry's files, and Install runs the ordinary
// §5.2 two-phase import, landing the automation with its triggers off. The
// fresh data root seeds the §22.2 built-in catalog, so the page opens on that
// section rather than the empty state: it is read from the real link, so the
// drive asserts only that the section is there with its chip and keeps no
// Remove… row, and finds its own catalogs by name, never by position or count. Then the
// §22.7 authoring half: Create catalog… opens the editor empty (no save
// location - Autowright keeps the catalog), the add form takes an archive this
// test exported into a temp folder by its path, Create lands the catalog kept
// by the app, and the §22.3 Export writes its file beside that archive. The
// native save dialog is stubbed in the main process, where it can't be driven.
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

  // §22 visibility: this drive reaches the page through its nav row, which
  // renders for nobody while App.tsx's MARKETPLACE_HIDDEN parking switch is
  // true. Flip that constant and an `it.skip` here together.
  it('shows the page to everyone, lists a file source, and installs an entry', async () => {
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
    const archiveFile = path.join(shelf, 'watcher.autowright')
    await writeFile(archiveFile, archive)
    const catalog = path.join(shelf, 'marketplace-catalog.yaml')
    await writeFile(catalog, [
      'format_version: 1',
      'name: "E2E shelf"',
      'entries:',
      '  - title: "Watcher"',
      '    description: "From the e2e shelf."',
      // §22.1: a reference is an https link or an absolute path, never relative.
      `    path: '${archiveFile}'`,
      '',
    ].join('\n'), 'utf-8')

    handle = await launchApp(backend.home, true)
    const { page } = handle
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })

    // §22 visibility: the row is in the rail from the first frame, with the
    // fresh data root's default settings (Developer mode off).
    await page.getByTestId('nav-marketplace').waitFor({ timeout: 10_000 })
    expect((await backend.api('GET', '/state') as { settings: { developerMode: boolean } }).settings.developerMode).toBe(false)

    // §22.2: the seeded built-in catalog is the page's first section - pending,
    // loaded or failed, depending on what the real link answers here, so
    // nothing is asserted about its entries.
    await clickNav(page, 'Marketplace')
    const builtinSection = page.getByTestId('marketplace-source')
      .filter({ has: page.getByTestId('marketplace-builtin-chip') })
    await builtinSection.waitFor({ timeout: 20_000 })
    expect(await page.getByTestId('marketplace-add').count()).toBeGreaterThan(0)
    // §22.3: the built-in catalog has a location, so Refresh all is always here.
    expect(await page.getByTestId('marketplace-refresh-all').count()).toBe(1)
    // §22.2: it can't be removed, and hiding it lives in the settings modal -
    // its menu ends at Catalog settings…. The menu closes the way it opened,
    // on the button.
    const builtinActions = builtinSection.getByTestId('marketplace-actions')
    await builtinActions.click()
    await page.getByRole('button', { name: 'Catalog settings…' }).waitFor({ timeout: 10_000 })
    expect(await page.getByRole('button', { name: 'Remove…' }).count()).toBe(0)
    expect(await page.getByRole('button', { name: 'Hide', exact: true }).count()).toBe(0)
    await shot(page, 'marketplace-builtin.png')
    await builtinActions.click()
    await page.getByRole('button', { name: 'Catalog settings…' }).waitFor({ state: 'detached', timeout: 10_000 })

    // The add modal's file step is the native open dialog, which can't be
    // driven — add through the §22.4 route instead and let the page hear about
    // it the way a §20 CLI add would: the marketplace.changed event, no reload.
    await backend.api('POST', '/marketplace/sources', { path: catalog })
    // The drive's own sections are found by name - the built-in one shares the
    // page with them.
    const shelfSection = page.getByTestId('marketplace-source').filter({ hasText: 'E2E shelf' })
    await waitFor(async () => (await shelfSection.count()) === 1,
      20_000, 'marketplace.changed to bring the source in without a reload')
    const entry = shelfSection.getByTestId('marketplace-entry')
    await entry.getByText('Watcher', { exact: true }).waitFor({ timeout: 10_000 })
    await entry.getByText('From the e2e shelf.').waitFor()
    // §22.2: a file location is re-read like a link, so both Refresh all and
    // the row's own Refresh are offered, and the row says when it was read.
    expect(await page.getByTestId('marketplace-refresh-all').count()).toBe(1)
    await shelfSection.getByTestId('marketplace-actions').click()
    await page.getByRole('button', { name: 'Refresh', exact: true }).waitFor()
    expect(await page.getByRole('button', { name: 'Refresh', exact: true }).count()).toBe(1)
    await shelfSection.getByText(/^Refreshed /).waitFor()
    await shot(page, 'marketplace-source.png')

    // §22.3 catalog settings: the §22.2 row's own columns behind the gear row
    // of the open actions menu.
    await page.getByRole('button', { name: 'Catalog settings…' }).click()
    await page.getByTestId('catalog-settings').waitFor({ timeout: 10_000 })
    await page.getByTestId('settings-location').waitFor()
    await shot(page, 'marketplace-settings.png')
    await page.getByRole('button', { name: 'Cancel' }).click()
    await page.getByTestId('catalog-settings').waitFor({ state: 'detached', timeout: 10_000 })

    // §22.3 add modal: the drop zone and the link-or-path field (the native
    // dialog itself can't be driven, so the modal is only looked at here).
    await page.getByTestId('marketplace-add').first().click()
    await page.getByTestId('marketplace-drop-zone').waitFor({ timeout: 10_000 })
    await shot(page, 'marketplace-add.png')
    await page.getByRole('button', { name: 'Cancel' }).click()
    await page.getByTestId('marketplace-drop-zone').waitFor({ state: 'detached', timeout: 10_000 })

    // §22.3 Audit: the archive viewer reads the entry's archive on the click
    // and nowhere else - manifest.yaml is the first file the route serves.
    await entry.getByTestId('marketplace-audit').click()
    const viewer = page.locator('[aria-label="Archive viewer"]')
    await viewer.waitFor({ timeout: 20_000 })
    await viewer.getByTestId('archive-file').first().getByText('manifest.yaml').waitFor({ timeout: 20_000 })
    await viewer.getByText(/format_version/).waitFor({ timeout: 10_000 })
    await shot(page, 'marketplace-archive.png')
    await page.keyboard.press('Escape')
    await viewer.waitFor({ state: 'detached', timeout: 10_000 })

    // Install: the §9.1 import modal opens straight on its preview step. The
    // archive's own automation is already here, so it lands deduped (§5.1).
    await shelfSection.getByTestId('marketplace-install').click()
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

    // §22.7 authoring: Create catalog… opens the editor empty - there is no
    // save location to choose, the app keeps the catalog. The editor never
    // exports: the archive the catalog will list is exported here first (the
    // same §19 route the §9.2 Export… uses) into this test's temp folder, and
    // the add form takes its path. The catalog file lands beside it on the
    // §22.3 Export.
    const authored = path.join(backend.home, 'authored')
    const archivePath = path.join(authored, 'Watcher.autowright')
    await mkdir(authored, { recursive: true })
    await writeFile(archivePath, await backend.apiBytes('GET', `/automations/${id}/export?values=0`))

    await clickNav(page, 'Marketplace')
    await page.getByTestId('marketplace-create').click()
    await page.getByTestId('catalog-editor').waitFor({ timeout: 20_000 })
    await page.getByTestId('catalog-add-automation').click()
    // §22.7 add form: the title and the archive's absolute path, typed in.
    await page.getByTestId('catalog-picker-title').fill('Watcher')
    await page.getByTestId('catalog-picker-description').fill('From the authored catalog.')
    await page.getByTestId('catalog-picker-path').fill(archivePath)
    await page.getByTestId('catalog-picker-add').click()
    // §22.7: the entry is listed by the archive it names.
    await page.getByTestId('catalog-nav-row').filter({ hasText: 'Watcher.autowright' })
      .waitFor({ timeout: 10_000 })
    // The form stays mounted through its exit animation - shoot the settled editor.
    await page.getByTestId('catalog-picker').waitFor({ state: 'detached', timeout: 10_000 })
    await shot(page, 'marketplace-editor.png')

    // §22.7 create: the primary button reads Create and POSTs the editor's
    // content alone - the catalog is kept by Autowright.
    await page.getByTestId('catalog-save').click()
    // §22.1: a catalog created in the app and left untitled is "My catalog".
    const authoredSection = page.getByTestId('marketplace-source').filter({ hasText: 'My catalog' })
    await waitFor(async () => (await authoredSection.count()) === 1,
      20_000, 'the authored catalog to land as its own section')
    await authoredSection.getByTestId('marketplace-entry').getByText('Watcher', { exact: true })
      .waitFor({ timeout: 10_000 })
    // §22.3: the chip says the app holds the only copy - there is no location.
    await authoredSection.getByText('Stored by Autowright').waitFor({ timeout: 10_000 })
    // Same for the editor: it closes after the save lands.
    await page.getByTestId('catalog-editor').waitFor({ state: 'detached', timeout: 10_000 })
    await shot(page, 'marketplace-authored.png')

    // §22.3 Export: the file the app keeps, saved where the user says - the
    // save dialog answers the catalog's name in the same folder this time.
    await handle.app.evaluate(({ dialog }, catalogFilePath) => {
      dialog.showSaveDialog = () => Promise.resolve({ canceled: false, filePath: catalogFilePath })
    }, path.join(authored, 'marketplace-catalog.yaml'))
    await authoredSection.getByTestId('marketplace-actions').click()
    await page.getByRole('button', { name: 'Export catalog…' }).click()
    await waitFor(async () => (await readdir(authored)).includes('marketplace-catalog.yaml'),
      20_000, 'the exported catalog file to land')

    // §22.7: the catalog plus the archive it lists - the archive exported by
    // this test, the catalog file by Export.
    expect((await readdir(authored)).sort())
      .toEqual(['Watcher.autowright', 'marketplace-catalog.yaml'])

    // §22.3: Developer mode flipping while the page is open changes nothing -
    // no setting gates the marketplace.
    await clickNav(page, 'Marketplace')
    await page.getByRole('heading', { name: 'Marketplace' }).waitFor({ timeout: 10_000 })
    await backend.api('PATCH', '/settings', { developerMode: true })
    await waitFor(async () => {
      const s = (await backend!.api('GET', '/state') as { settings: { developerMode: boolean } }).settings
      return s.developerMode
    }, 10_000, 'developerMode to persist')
    await page.getByRole('heading', { name: 'Marketplace' }).waitFor({ timeout: 10_000 })
    expect(await page.getByTestId('nav-marketplace').count()).toBe(1)
    await shot(page, 'marketplace-default-feature.png')
  }, 120_000)
})
