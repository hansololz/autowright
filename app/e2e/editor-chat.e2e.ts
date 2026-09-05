// §15 e2e: the §11 editor chat pane — the one agent surface. Three journeys
// through the real §8 chat pipeline against the fake claude: a prose answer,
// a chat-driven spec rewrite synced from the thread entry and saved as v2,
// the actions.yaml chain (rewrite → auto-sync → auto-test → settled chip),
// and §7 Fix with AI seeding the thread from a failed execution.
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, closeApp, launchApp, shot, type AppHandle } from './harness'

const BOOM_STEP = {
  file: '01-boom.py', name: 'Boom', description: 'always fails',
  code: 'assert False, "distinct boom message e2e"\n',
}

const CHAT_INPUT = 'Change something, or ask a question…'

describe('editor chat e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  /** Seed an automation with a authoring agent and open its editor. */
  async function openEditor(name: string, steps?: Array<{ file: string; name: string; description: string; code: string }>) {
    backend = await new Backend().start()
    const agent = await backend.createAgent('Chat Agent')
    const { id } = await backend.createAutomation(name, steps, { agentId: agent.id })
    handle = await launchApp(backend.home, true)
    const { page } = handle
    await page.getByText(name).waitFor({ timeout: 20_000 })
    await page.getByText(name).click()
    await page.getByRole('button', { name: 'Edit', exact: true }).click()
    await page.getByTestId('test-draft-toggle').waitFor({ timeout: 10_000 })
    return { id, page }
  }

  it('answers a question, rewrites the spec from chat, and syncs from the thread entry', async () => {
    const { id, page } = await openEditor('Chat loop e2e')

    // A question-shaped message gets a prose `answer` entry (markdown), no rewrite.
    await page.getByPlaceholder(CHAT_INPUT).fill('What does this workflow do?')
    await page.getByRole('button', { name: 'Send' }).click()
    await page.getByText(/The workflow has two steps/).waitFor({ timeout: 60_000 })
    await page.getByText('In sync with the spec.').waitFor() // still in sync

    // §11 composer auto-grow (ask-box pattern): the textarea sizes to its
    // content in both directions and never shows a scrollbar.
    const input = page.getByPlaceholder(CHAT_INPUT)
    await input.fill('hi')
    const hShort = await input.evaluate((el) => el.clientHeight)
    await input.fill(Array(8).fill('line').join('\n'))
    const hTall = await input.evaluate((el) => el.clientHeight)
    expect(hTall).toBeGreaterThan(hShort + 40)
    // sized to content: nothing left to scroll (≤ the border box's few px)
    expect(await input.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(4)
    await input.fill('hi')
    expect(await input.evaluate((el) => el.clientHeight)).toBe(hShort)

    // A change request: while the §8 chat job runs, the thread progress entry
    // is the page's only live-job surface — stage label + spinner render at
    // the bottom of the thread, and the composer's Send becomes Cancel (§11).
    await page.getByPlaceholder(CHAT_INPUT).fill('Track new manga chapters instead')
    await page.getByRole('button', { name: 'Send' }).click()
    // scoped to the live progress entry — either chat stage label counts: the
    // §8 flip moves the job to "Updating the documents…" once the fake
    // harness streams a rewrite marker, and timing decides which one a poll
    // catches first
    await page.getByTestId('chat-progress')
      .getByText(/Working on the request…|Updating the documents…/).waitFor({ timeout: 15_000 })
    await page.getByRole('button', { name: 'Cancel', exact: true }).waitFor()

    // The rewrite lands as the "Spec updated." chip (exact — the toast
    // "Spec updated — …" would otherwise match too); the request text shows
    // in the user bubble (the chip no longer echoes it)
    await page.getByText('Spec updated.', { exact: true }).waitFor({ timeout: 60_000 })
    await page.getByText('Track new manga chapters instead').first().waitFor()

    // §11 thread scroll behaviors. Growing the composer shrinks the thread
    // viewport (flex) until it genuinely overflows — the guard below keeps
    // these assertions from passing vacuously on a short thread.
    const thread = page.getByTestId('chat-thread')
    let draft = ''
    for (let lines = 8; lines <= 28; lines += 4) {
      draft = Array(lines).fill('draft text').join('\n')
      await input.fill(draft)
      if (await thread.evaluate((el) => el.scrollHeight > el.clientHeight + 40)) break
    }
    expect(await thread.evaluate((el) => el.scrollHeight > el.clientHeight + 40)).toBe(true)
    // Typing more keeps the thread's scroll position — the auto-grow's
    // transient height:auto collapse must not yank the thread upward.
    await thread.evaluate((el) => { el.scrollTop = 5 })
    await input.fill(draft + '\nmore')
    expect(await thread.evaluate((el) => el.scrollTop)).toBe(5)

    // A new entry re-pins the thread to the newest content: park the scroll
    // away from the bottom, then let the sync chip land.
    await thread.evaluate((el) => { el.scrollTop = 0 })
    await page.getByTestId('chat-sync-now').click()
    await page.getByText('Steps synced with the spec.', { exact: true }).waitFor({ timeout: 60_000 })
    await expect.poll(() => thread.evaluate(
      (el) => el.scrollHeight - el.scrollTop - el.clientHeight), { timeout: 5_000 }).toBeLessThan(2)
    expect(await thread.evaluate((el) => el.scrollHeight > el.clientHeight + 40)).toBe(true)
    await input.fill('') // back to the journey — composer at rest

    await page.getByText('In sync with the spec.').waitFor()
    await shot(page, 'editor-chat-synced.png')

    // §11 Clear chat: confirm empties the thread only — the draft (and its
    // in-sync state) is untouched and the composer still works.
    await page.getByTestId('chat-clear').click()
    await page.getByRole('alertdialog').getByRole('button', { name: 'Clear chat' }).click()
    await expect.poll(() => page.getByText('Spec updated.', { exact: true }).count()).toBe(0)
    await page.getByText(/Ask anything, or describe a change/).waitFor()
    await page.getByText('In sync with the spec.').waitFor()

    // The chat-driven edit saves like any other draft.
    await page.getByRole('button', { name: 'Save as v2' }).click()
    await page.getByText('v2', { exact: true }).first().waitFor({ timeout: 20_000 })
    const auto = await backend!.api('GET', `/automations/${id}`) as { version: number }
    expect(auto.version).toBe(2)
  }, 120_000)

  it('runs the fix-and-test action chain: rewrite → auto-sync → auto-test → settled chip', async () => {
    const { page } = await openEditor('Chat chain e2e')

    // The fake claude's fix-and-test response carries an answer, a spec
    // rewrite, a notes.md rewrite, and actions.yaml `sync: true` +
    // `test: true` — the §11 chaining watcher fires the sync, then the test.
    await page.getByPlaceholder(CHAT_INPUT).fill('Please fix-and-test this workflow')
    await page.getByRole('button', { name: 'Send' }).click()

    await page.getByText('Fixed — rebuilding the steps and running a test.').waitFor({ timeout: 60_000 })
    await page.getByText('Notes updated.', { exact: true }).waitFor({ timeout: 20_000 })
    await page.getByText('Steps synced with the spec.', { exact: true }).waitFor({ timeout: 60_000 })
    // The chained test executes the synced draft's real steps without opening
    // the modal: the settled run lands as a quiet system chip in the thread
    // and as the TEST card's settled row (§11).
    await page.getByTestId('chat-thread').getByText('Test succeeded.', { exact: true }).waitFor({ timeout: 60_000 })
    await page.getByTestId('test-card').getByText('Test succeeded.', { exact: true }).waitFor()
    await shot(page, 'editor-chat-chain.png')
  }, 120_000)

  it('Fix with AI seeds the thread from the failed execution and starts the analysis', async () => {
    // Fail a real execution before the app ever opens.
    backend = await new Backend().start()
    const agent = await backend.createAgent('Chat Agent')
    const { id } = await backend.createAutomation('Fix with AI e2e', [BOOM_STEP], { agentId: agent.id })
    const exec = await backend.executeAndWait(id)
    expect(exec.status).toBe('failed')

    handle = await launchApp(backend.home, true)
    const { page } = handle
    await page.getByText('Fix with AI e2e').waitFor({ timeout: 20_000 })
    await page.getByText('Fix with AI e2e').click()

    // §9.2 failure notice → Fix with AI → the editor opens with the failure
    // seeded as a system entry and the canned analyze chat message in flight.
    await page.getByText(/Failed at step/).waitFor({ timeout: 20_000 })
    await page.getByRole('button', { name: /Fix with AI/ }).click()
    await page.getByText(/Execution failed at step Boom/).waitFor({ timeout: 20_000 })
    // The fake claude answers the analyze message with a spec rewrite.
    await page.getByText('Spec updated.', { exact: true }).waitFor({ timeout: 60_000 })
    await page.getByText(/This execution failed — figure out why/).first().waitFor()
    await shot(page, 'editor-chat-fix-with-ai.png')
  }, 120_000)
})
