// §15 e2e: the create-flow journey — request → real §8 chat + chained-sync
// pipeline (fake `claude` answers both calls) → Test (real §11 draft
// execution) → Create → Execute now → execution page with steps, logs, result.
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, clickNav, closeApp, launchApp, shot, type AppHandle } from './harness'

describe('create flow e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  it('drafts, tests, creates, and executes an automation end to end', async () => {
    backend = await new Backend().start()
    // The create flow needs an agent (agents.length === 0 redirects to the
    // Agents page) — seed one config-only record over HTTP.
    await backend.createAgent('Draft Agent')
    handle = await launchApp(backend.home, true)
    const { page } = handle

    // Empty list → the editor's create empty state: the chat pane carries the
    // headline and the input (§11 — one screen from birth to save).
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })
    await page.getByRole('button', { name: 'Create your first automation' }).click()
    await page.getByRole('heading', { name: 'What should Autowright do for you?' }).waitFor({ timeout: 10_000 })

    // The chat input — the first message is an ordinary §8 chat job (the
    // new-automation rule). The fake claude names the automation from this
    // exact text via the actions.yaml `name`.
    await page.getByPlaceholder('Describe the job — one sentence is enough.').fill('Track manga chapters e2e')
    await page.getByRole('button', { name: 'Send' }).click()
    await shot(page, 'create-drafting.png')

    // The chat call lands the spec (fixed fake envelope) and arms the chained
    // sync, which delivers the steps + param.
    await page.getByText('Every day at 8:00.').waitFor({ timeout: 60_000 })
    await page.getByText('Check for changes').waitFor({ timeout: 60_000 })
    await page.getByText('Build the result').waitFor()
    await page.getByText('Notify only on changes').first().waitFor()
    await shot(page, 'create-review.png')

    // Test: Test draft opens the test-run modal; its Run test starts the real
    // draft execution through the engine (scratch memory).
    await page.getByTestId('test-draft-toggle').click()
    await page.getByRole('button', { name: 'Run test' }).click()
    // the outcome lands in the modal footer and on the TEST card behind it
    await page.getByText('Test succeeded — the memory copy was discarded.').first()
      .waitFor({ timeout: 60_000 })
    await shot(page, 'create-test-succeeded.png')
    // §11: closing never cancels a test — and nothing on the page below is
    // clickable until the modal is gone.
    await page.keyboard.press('Escape')
    await page.getByTestId('test-modal').waitFor({ state: 'hidden', timeout: 10_000 })

    // Create → lands on the automation's detail page.
    await page.getByRole('button', { name: 'Create automation' }).click()
    const execBtn = page.getByRole('button', { name: 'Execute now' })
    await execBtn.waitFor({ timeout: 20_000 })
    // Two headings carry the name (title row + the spec's own h1) — any is fine.
    await page.getByRole('heading', { name: 'Track manga chapters e2e' }).first().waitFor()

    // Execute for real, then open the execution record.
    await execBtn.click()
    await page.getByText('Succeeded').first().waitFor({ timeout: 60_000 })
    await page.getByText('Manual · v1').first().click()

    // Execution page: result section, both steps in the rail, logs.
    await page.getByText('Mock run — nothing new.').waitFor({ timeout: 20_000 })
    await page.getByText('All good').first().waitFor()
    // The LOGS rail rows are buttons ("<name> <duration>"); the names also
    // appear in the result's step list, so target the rail by role.
    await page.getByRole('button', { name: /Check for changes/ }).waitFor()
    await page.getByRole('button', { name: /Build the result/ }).waitFor()
    // Selecting step 1 shows its real log line (fake step's `log(...)`).
    await page.getByRole('button', { name: /Check for changes/ }).click()
    await page.getByText(/nothing to do in mock mode/).waitFor({ timeout: 20_000 })
    await shot(page, 'create-execution-page.png')
  }, 120_000)

  it('a new automation always opens on the suggestion state — a settled session never replays', async () => {
    // §11/§4.4 fresh-entry clear: entering the create flow with no pending
    // draft to resume shows the create empty state and keeps showing it — the
    // previous session's thread ("Working on the request…" trail, boundary
    // marker) must never replace the suggestions.
    backend = await new Backend().start()
    await backend.createAgent('Draft Agent')
    handle = await launchApp(backend.home, true)
    const { page } = handle

    const headline = page.getByRole('heading', { name: 'What should Autowright do for you?' })
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 20_000 })
    await page.getByRole('button', { name: 'Create your first automation' }).click()
    // Fresh entry: the suggestion headline shows and STAYS — give the thread
    // load a beat to (wrongly) replace it before asserting.
    await headline.waitFor({ timeout: 10_000 })
    await page.waitForTimeout(1200)
    expect(await headline.isVisible()).toBe(true)
    expect(await page.getByText('Working on the request…').count()).toBe(0)

    // Build a session (spec + steps via the fake agent), discard it, leave —
    // the pending slot now holds a settled thread but no draft.
    await page.getByPlaceholder('Describe the job — one sentence is enough.').fill('Suggestion state e2e')
    await page.getByRole('button', { name: 'Send' }).click()
    await page.getByText('Steps synced with the spec.').waitFor({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Start over' }).click()
    await page.getByText('Draft discarded.').waitFor({ timeout: 10_000 })
    await clickNav(page, 'Automations')
    await page.getByRole('heading', { name: 'Automations' }).waitFor({ timeout: 10_000 })

    // Re-enter: no pending draft → no confirm — and the suggestions, not the
    // old conversation, greet the new automation.
    await page.getByRole('button', { name: 'Create your first automation' }).click()
    await headline.waitFor({ timeout: 10_000 })
    await page.waitForTimeout(1200)
    expect(await headline.isVisible()).toBe(true)
    expect(await page.getByText('Working on the request…').count()).toBe(0)
    expect(await page.getByText('Draft discarded.').count()).toBe(0)
    await shot(page, 'create-fresh-suggestions.png')
  }, 120_000)
})
