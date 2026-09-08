// §15 e2e: the menu-bar surface (#menubar hash → §13 panel in the same
// renderer), and Executions-list behavior (§11 test records listed, trigger
// "Test" printed once) plus Settings persistence (retention clamp,
// notification radio).
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, clickNav, closeApp, launchApp, shot, waitFor, type AppHandle } from './harness'

const SLOW_STEP = {
  file: '01-warm.py', name: 'Warm up', description: 'sleeps briefly',
  code: 'from autowright import log\nimport time\nlog("warming")\ntime.sleep(2)\n',
}
const FINISH_STEP = {
  file: '02-finish.py', name: 'Finish', description: 'result',
  code: 'from autowright import result\nresult.status("ok")\nresult.chip("All good")\n',
}

describe('surfaces e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  it('menu-bar surface lists automations and executes one from the panel', async () => {
    backend = await new Backend().start()
    await backend.createAutomation('Menubar e2e', [SLOW_STEP, FINISH_STEP])
    handle = await launchApp(backend.home, true)
    const { page } = handle
    await page.getByText('Menubar e2e').waitFor({ timeout: 20_000 })

    // The store picks the surface from the location hash at boot (§13) —
    // switch this window to the panel the same way the tray window loads.
    await page.evaluate(() => { location.hash = '/menubar' })
    await page.reload()
    await page.getByText('All good · 1 automation').waitFor({ timeout: 20_000 })
    await page.getByText('Menubar e2e').waitFor()
    await page.getByText('Open Autowright').waitFor()
    await shot(page, 'menubar-panel.png')

    // Execute from the row: the dot/sub goes live, then the result chip lands.
    await page.getByTitle('Execute now').click()
    await page.getByText('Executing now…').waitFor({ timeout: 10_000 })
    await page.getByText('All good', { exact: true }).waitFor({ timeout: 30_000 })
    await shot(page, 'menubar-executed.png')
  }, 120_000)

  it('lists §11 test records in the executions list and persists settings', async () => {
    backend = await new Backend().start()
    const { id } = await backend.createAutomation('Execution list e2e')
    const real = await backend.executeAndWait(id)
    expect(real.status).toBe('succeeded')
    // A §11 draft-test record over the same steps — lists like any execution (§7).
    const { executionId: testId } = await backend.api('POST', '/tests', {
      draft: {
        name: 'Execution list e2e', description: 'test', note: null, params: [],
        steps: [FINISH_STEP],
        spec: [{ kind: 'h1', text: 'Execution list e2e' }],
      },
      enabledAgents: [], allowedSecrets: [],
    }) as { executionId: string }
    await waitFor(async () => {
      const e = await backend!.api('GET', `/executions/${testId}`) as { status: string }
      return e.status !== 'queued' && e.status !== 'executing' ? e : null
    }, 30_000, 'test record to settle')

    handle = await launchApp(backend.home, true)
    const { page } = handle
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })

    // Executions list: TWO rows — the real one and the §11 test record (§7).
    // The test row's trigger column prints "Test" once, never "Test · Test".
    await clickNav(page, 'Executions')
    await page.getByText('Execution list e2e').first().waitFor({ timeout: 10_000 })
    expect(await page.getByTestId('execution-row').count()).toBe(2)
    expect(await page.getByText('Execution list e2e').count()).toBe(2)
    expect(await page.getByText('Test', { exact: true }).count()).toBe(1)
    expect(await page.getByText('Test · Test').count()).toBe(0)
    await page.getByTestId('execution-row').filter({ hasText: 'Manual' }).click()
    await page.getByRole('button', { name: 'Execute again' }).waitFor({ timeout: 10_000 })
    await shot(page, 'executions-list-clickthrough.png')

    // Settings: retention clamp (invalid → 90), then a real edit + radio.
    await clickNav(page, 'Settings')
    const days = page.locator('input[inputmode="numeric"]')
    await days.waitFor({ timeout: 10_000 })
    await days.fill('0')
    await page.keyboard.press('Tab')
    await waitFor(async () => (await days.inputValue()) === '90', 5_000, 'days clamp to 90')
    await days.fill('45')
    await page.keyboard.press('Tab')
    await page.getByText('After every execution').click()
    await waitFor(async () => {
      const s = (await backend!.api('GET', '/state') as { settings: { days: number; notifications: string } }).settings
      return s.days === 45 && s.notifications === 'all'
    }, 10_000, 'settings to persist')

    // Reload — boot lands on the automations list; the persisted values come
    // back from the backend once Settings reopens.
    await page.reload()
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })
    await clickNav(page, 'Settings')
    await page.getByText('After every execution').waitFor({ timeout: 10_000 })
    await waitFor(async () => (await page.locator('input[inputmode="numeric"]').inputValue()) === '45',
      10_000, 'persisted days after reload')
    await shot(page, 'settings-persisted.png')
  }, 120_000)
})
