// §15 e2e: a step writes a markdown result file via the real §6.1 mechanism
// ((result / "result.md").write_text(...)) — the execution page renders it
// inline (fileKind md → Markdown view).
import { afterEach, describe, expect, it } from 'vitest'
import { Backend, COPY, closeApp, launchApp, shot, type AppHandle } from './harness'

const REPORT_STEP = {
  file: '01-report.py', name: 'Write report', description: 'writes result.md',
  code: [
    'from autowright import result',
    'md = "# E2E report\\n\\n| Item | State |\\n| --- | --- |\\n| Alpha | fresh |\\n\\nDistinctive e2e markdown body.\\n"',
    '(result / "result.md").write_text(md)',
    'result.status("ok")',
    'result.chip("Report ready")',
    '',
  ].join('\n'),
}

describe('result files e2e', () => {
  let backend: Backend | null = null
  let handle: AppHandle | null = null

  afterEach(async () => {
    await closeApp(handle)
    handle = null
    await backend?.stop()
    backend = null
  })

  it('renders a markdown result file on the execution page', async () => {
    backend = await new Backend().start()
    const { id } = await backend.createAutomation('Result files e2e', [REPORT_STEP])
    const exec = await backend.executeAndWait(id)
    expect(exec.status).toBe('succeeded')

    handle = await launchApp(backend.home, true)
    const { page } = handle

    // List → detail → the execution record.
    await page.getByText('Result files e2e').waitFor({ timeout: 20_000 })
    await page.getByText('Result files e2e').click()
    await page.getByText('Manual · v1').first().waitFor({ timeout: 10_000 })
    await page.getByText('Manual · v1').first().click()

    // Result view: chip and the markdown file rendered inline.
    await page.getByText('Report ready').first().waitFor({ timeout: 20_000 })
    await page.getByRole('heading', { name: 'E2E report' }).waitFor({ timeout: 20_000 })
    await page.getByText('Distinctive e2e markdown body.').waitFor()
    // The markdown table renders as a real table (cell text from the file).
    await page.getByText('Alpha', { exact: true }).waitFor()
    // The file also lists in the FILES footer (name appears in view title too).
    await page.getByText('result.md').first().waitFor()
    // §7 WORKSPACE card sits at the page's bottom — presence only, never
    // clicked (it would open a real file-manager window).
    await page.getByTestId('workspace-card').getByRole('button', { name: COPY.reveal }).waitFor()
    await shot(page, 'result-files.png')
  }, 120_000)
})
