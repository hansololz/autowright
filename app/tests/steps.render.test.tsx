// Component tests for the shared step-list / param-editor module (src/steps.tsx)
// serving both the §11 create/edit flow ('editor' variant) and the §9.2
// automation detail page ('detail' variant): step rows with agent/secret/
// package/timeout tags, the step-script modal a row opens, and the five §4.2
// param value kinds with each variant's cosmetic contract.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { highlightPythonLines } from '../src/ui'
import type { Agent, PackageDep, ParamDef, SecretMeta, Step, UnresolvedRefs } from '../src/types'
import { ParamValueEditor, StepList, findInLines, stepAgentPrompts, stepChange, stepFacts, stepFiles, stepHosts, stepMemory, stepModalFrame, stepPackageTags, stepParams, stepSecretTags } from '../src/steps'

afterEach(() => cleanup())

const AGENT: Agent = { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null }
// §4.8 fixture secrets — step entries and code subscripts reference these ids
const MAIL_ID = '11111111-1111-1111-1111-111111111111'
const CRM_ID = '22222222-2222-2222-2222-222222222222'
const GONE_ID = '99999999-9999-4999-8999-999999999999'
const SECRETS: SecretMeta[] = [
  { id: MAIL_ID, name: 'MAIL_PASSWORD', description: '', set: true, usedBy: [] },
  { id: CRM_ID, name: 'CRM_API_KEY', description: '', set: true, usedBy: [] },
]
// §4.1/§5.1 imported placeholders: ids the archive's references were minted
// as, carried by the automation's unresolvedReferences map because nothing on
// this Mac matched them.
const IMP_SECRET_ID = '33333333-3333-4333-8333-333333333333'
const IMP_AGENT_ID = '44444444-4444-4444-8444-444444444444'
const UNRESOLVED: UnresolvedRefs = {
  [IMP_SECRET_ID]: { kind: 'secret', name: 'STRIPE_KEY', description: 'billing token' },
  [IMP_AGENT_ID]: { kind: 'agent', name: 'Researcher', description: 'reads the web' },
}
const step = (over: Partial<Step> = {}): Step => ({ name: 'Fetch page', description: 'reads it', code: '', ...over })

describe('StepList (shared)', () => {
  it('detail variant: secret tags from code refs resolve to live names, fallback agent tag, script in the step-script modal', () => {
    const steps = [step({ agent: true, code: `x = secrets["${MAIL_ID}"]  # MAIL_PASSWORD\nlog(x)` })]
    render(<StepList variant="detail" steps={steps} agents={[AGENT]} secrets={SECRETS} packages={[]} fallbackAgent="Cloud writer" />)
    expect(screen.getByText('MAIL_PASSWORD')).toBeTruthy()
    expect(screen.getByText('Cloud writer')).toBeTruthy()
    // gutter number, no dot
    expect(screen.getByText('1')).toBeTruthy()
    // clicking the row opens the step-script modal, which holds the script
    fireEvent.click(screen.getByText('Fetch page'))
    expect(screen.getByRole('dialog').textContent).toContain(`secrets["${MAIL_ID}"]`)
  })

  it('detail variant: a dangling secret or agent id renders the red deleted state', () => {
    const steps = [step({
      agent: true,
      agents: [{ id: GONE_ID }],
      code: `x = secrets["${GONE_ID}"]`,
    })]
    render(<StepList variant="detail" steps={steps} agents={[AGENT]} secrets={SECRETS} packages={[]} fallbackAgent="Cloud writer" />)
    const tags = screen.getAllByText('99999999…')
    expect(tags.length).toBe(2) // one agent tag, one secret tag
    const labels = tags.map((el) => el.closest('span')?.getAttribute('aria-label'))
    expect(labels).toContain('This step calls an agent that no longer exists — this step would fail')
    expect(labels).toContain('This step uses a secret that no longer exists — this step would fail')
  })

  it('detail variant: an unresolved imported id shows the archive name and the imported tooltip', () => {
    const steps = [step({
      agent: true,
      agents: [{ id: IMP_AGENT_ID }],
      code: `x = secrets["${IMP_SECRET_ID}"]`,
    })]
    render(
      <StepList
        variant="detail" steps={steps} agents={[AGENT]} secrets={SECRETS} packages={[]}
        unresolvedReferences={UNRESOLVED} fallbackAgent="Cloud writer"
      />,
    )
    // the red tags read as the archive's names, never the short id
    expect(screen.queryByText('33333333…')).toBeNull()
    expect(screen.queryByText('44444444…')).toBeNull()
    expect(screen.getByText('Researcher').closest('span')?.getAttribute('aria-label'))
      .toBe('This step calls Researcher from the imported file. '
        + 'No agent on this Mac matched it, so this step would fail.')
    expect(screen.getByText('STRIPE_KEY').closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses STRIPE_KEY from the imported file. '
        + 'No secret on this Mac matched it, so this step would fail.')
  })

  it('detail variant: a dangling id outside the map keeps the short-id deleted state', () => {
    const steps = [step({
      agent: true,
      agents: [{ id: GONE_ID }, { id: IMP_AGENT_ID }],
      code: `a = secrets["${GONE_ID}"]\nb = secrets["${IMP_SECRET_ID}"]`,
    })]
    render(
      <StepList
        variant="detail" steps={steps} agents={[AGENT]} secrets={SECRETS} packages={[]}
        unresolvedReferences={UNRESOLVED} fallbackAgent="Cloud writer"
      />,
    )
    const labels = screen.getAllByText('99999999…').map((el) => el.closest('span')?.getAttribute('aria-label'))
    expect(labels).toContain('This step calls an agent that no longer exists — this step would fail')
    expect(labels).toContain('This step uses a secret that no longer exists — this step would fail')
    // the imported pair beside them still reads as the archive names
    expect(screen.getByText('Researcher')).toBeTruthy()
    expect(screen.getByText('STRIPE_KEY')).toBeTruthy()
  })

  it('editor variant: an unresolved imported id shows the archive name and the imported tooltip', () => {
    const steps = [step({
      agent: true,
      agents: [{ id: IMP_AGENT_ID }, { id: GONE_ID }],
      code: `a = secrets["${IMP_SECRET_ID}"]\nb = secrets["${GONE_ID}"]`,
    })]
    render(
      <StepList
        variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]}
        secrets={SECRETS} unresolvedReferences={UNRESOLVED} packages={[]}
      />,
    )
    expect(screen.getByText('Researcher').closest('span')?.getAttribute('aria-label'))
      .toBe('This step calls Researcher from the imported file. '
        + 'No agent on this Mac matched it, so this step would fail.')
    expect(screen.getByText('STRIPE_KEY').closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses STRIPE_KEY from the imported file. '
        + 'No secret on this Mac matched it, so this step would fail.')
    // an id the map doesn't carry keeps the plain deleted wording
    const labels = screen.getAllByText('99999999…').map((el) => el.closest('span')?.getAttribute('aria-label'))
    expect(labels).toContain('This step calls an agent that no longer exists — this step would fail')
    expect(labels).toContain('This step uses a secret that no longer exists — this step would fail')
  })

  it('editor variant: package tag for imported deps and red no-agent tag when none enabled', () => {
    const packages: PackageDep[] = [{ pip: 'beautifulsoup4', import: 'bs4', why: 'parse pages' }]
    const steps = [step({ agent: true, code: 'import bs4' })]
    render(<StepList variant="editor" steps={steps} availAgents={[]} allAgents={[]} secrets={[]} packages={packages} />)
    // the import name can ride other tags too — assert the package tag itself
    // (the tooltip text rides the tag's aria-label — §14 Tag tooltip)
    const pkgTag = screen.getAllByText('bs4').find((el) =>
      el.closest('span')?.getAttribute('aria-label')?.includes('Python package'))
    expect(pkgTag).toBeTruthy()
    // no per-step entry → the tooltip falls back to the declaration's why
    expect(pkgTag!.closest('span')?.getAttribute('aria-label')).toContain('— parse pages')
    expect(screen.getByText('no agent')).toBeTruthy()
    // §14: both variants draw the same list row — a gutter step number
    expect(screen.getByText('1')).toBeTruthy()
  })

  it('editor variant: a declared per-step package why wins the tooltip', () => {
    const packages: PackageDep[] = [{ pip: 'pandas', import: 'pandas', why: 'data wrangling', version: '2.2' }]
    const steps = [step({ code: 'import pandas', packages: [{ import: 'pandas', why: 'parses the price tables' }] })]
    render(<StepList variant="editor" steps={steps} availAgents={[]} allAgents={[]} secrets={[]} packages={packages} />)
    const pkgTag = screen.getAllByText('pandas').find((el) =>
      el.closest('span')?.getAttribute('aria-label')?.includes('Python package'))
    expect(pkgTag!.closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses the pandas Python package, version 2.2 — parses the price tables')
  })

  it('editor variant: agent entry ids resolve to the live agent', () => {
    const steps = [step({ agent: true, agents: [{ id: 'g1', why: 'writes prose' }] })]
    render(<StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} />)
    const tag = screen.getByText('Cloud writer')
    expect(tag.closest('span')?.getAttribute('aria-label')).toContain('mid-execution — writes prose')
  })

  it('editor variant: an existing-but-disabled agent id warns red with the live name', () => {
    const steps = [step({ agent: true, agents: [{ id: 'g1' }] })]
    render(<StepList variant="editor" steps={steps} availAgents={[]} allAgents={[AGENT]} secrets={[]} packages={[]} />)
    const tag = screen.getByText('Cloud writer')
    expect(tag.closest('span')?.getAttribute('aria-label'))
      .toBe('Cloud writer isn’t enabled for steps — this step would fail')
  })

  it('editor variant: a deleted agent id renders the red deleted state', () => {
    const steps = [step({ agent: true, agents: [{ id: GONE_ID }] })]
    render(<StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} />)
    const tag = screen.getByText('99999999…')
    expect(tag.closest('span')?.getAttribute('aria-label'))
      .toBe('This step calls an agent that no longer exists — this step would fail')
  })

  it('editor variant: an agent entry without a why falls back to the step why', () => {
    const steps = [step({ agent: true, why: 'needs judgment on titles', agents: [{ id: 'g1' }] })]
    render(<StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} />)
    const tag = screen.getByText('Cloud writer')
    expect(tag.closest('span')?.getAttribute('aria-label'))
      .toContain('mid-execution — needs judgment on titles')
  })

  it('editor variant: the empty-list fallback agent tag carries the step why', () => {
    const steps = [step({ agent: true, why: 'summarizes the page' })]
    render(<StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} />)
    const tag = screen.getByText('Cloud writer')
    expect(tag.closest('span')?.getAttribute('aria-label')).toContain('— summarizes the page')
  })

  it('secret tag tooltip: what + why when declared, what alone for code refs', () => {
    const steps = [step({
      secrets: [{ id: CRM_ID, why: 'authenticates the CRM fetch' }],
      code: `a = secrets["${CRM_ID}"]\nb = secrets["${MAIL_ID}"]`,
    })]
    render(<StepList variant="editor" steps={steps} availAgents={[]} allAgents={[]} secrets={SECRETS} packages={[]} />)
    expect(screen.getByText('CRM_API_KEY').closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses the CRM_API_KEY secret from your Keychain — authenticates the CRM fetch')
    expect(screen.getByText('MAIL_PASSWORD').closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses the MAIL_PASSWORD secret from your Keychain')
  })

  it('package tag tooltip: no why at all drops the why clause', () => {
    const packages: PackageDep[] = [{ pip: 'requests', import: 'requests', why: '' }]
    const steps = [step({ code: 'import requests' })]
    render(<StepList variant="editor" steps={steps} availAgents={[]} allAgents={[]} secrets={[]} packages={packages} />)
    const pkgTag = screen.getAllByText('requests').find((el) =>
      el.closest('span')?.getAttribute('aria-label')?.includes('Python package'))
    expect(pkgTag!.closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses the requests Python package')
  })

  it('detail variant: agent tag tooltip reads what + why', () => {
    const steps = [step({ agent: true, why: 'writes the final summary' })]
    render(<StepList variant="detail" steps={steps} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer" />)
    const tag = screen.getByText('Cloud writer')
    expect(tag.closest('span')?.getAttribute('aria-label'))
      .toBe('This step calls the Cloud writer AI agent — writes the final summary')
  })

  it('both variants: the §9.2 retry tag shows a set budget after the clock tag and hides at zero', () => {
    const steps = [step({ retries: 5 }), step({ name: 'Forever', infiniteRetries: true }), step({ name: 'Plain' })]
    for (const variant of ['detail', 'editor'] as const) {
      cleanup()
      render(variant === 'detail'
        ? <StepList variant="detail" steps={steps} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer" />
        : <StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} />)
      const five = screen.getByText('5 retries')
      expect(five.closest('span')?.getAttribute('aria-label'))
        .toBe('If this step fails it runs again, up to 5 more times')
      expect(five.closest('span')?.querySelector('.fa-rotate-right')).toBeTruthy()
      expect(screen.getByText('infinite retries').closest('span')?.getAttribute('aria-label'))
        .toBe('If this step fails it runs again until it succeeds, or you cancel or skip it')
      // one clock tag per step, retry tags only where a budget is set
      expect(screen.getAllByText('15m')).toHaveLength(3)
      expect(screen.queryAllByText(/retr/)).toHaveLength(2)
      // order: the retry tag follows the clock tag within the same row
      const row = five.closest('button')!
      const labels = Array.from(row.querySelectorAll('span[aria-label]')).map((e) => e.textContent)
      expect(labels.indexOf('15m')).toBeLessThan(labels.indexOf('5 retries'))
    }
  })
})

describe('step-script modal', () => {
  // Two detail-variant steps: the first carries the §4.1 version-folder
  // filename, the fallback agent and a declared secret, so its modal shows the
  // tag row; the second is bare, so prev/next has somewhere to go.
  const TWO_STEPS: Step[] = [
    step({ file: '01-fetch-page.py', agent: true, secrets: [{ id: MAIL_ID }], code: `x = secrets["${MAIL_ID}"]` }),
    step({ name: 'Send mail', description: 'mails it', code: 'send(x)' }),
  ]
  const renderDetail = () => render(
    <StepList variant="detail" steps={TWO_STEPS} agents={[AGENT]} secrets={SECRETS} packages={[]} fallbackAgent="Cloud writer" />,
  )
  // the step name renders twice once the modal is up (row + navigator row)
  const openFirst = () => fireEvent.click(screen.getAllByText('Fetch page')[0])
  // the navigator row naming a step: a button when unviewed, a plain
  // text-selectable block when viewed (§9.2)
  const navRow = (name: string) => Array.from(screen.getByRole('dialog').querySelectorAll('.ad-stepnav button, .ad-stepnav [aria-current]'))
    .find((b) => b.textContent?.includes(name)) as HTMLElement

  it('detail variant: a row opens the dialog with the STEP N OF M eyebrow, the toolbar filename and the tag row', () => {
    renderDetail()
    openFirst()
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Step 1 of 2: Fetch page')
    // the eyebrow is the step counter alone — the filename rides the toolbar
    expect(screen.getByText(/STEP 1 OF 2/).textContent).toBe('STEP 1 OF 2')
    expect(screen.getByText('01-fetch-page.py')).toBeTruthy()
    // the navigator lists every step, the viewed one marked current
    expect(navRow('Fetch page').getAttribute('aria-current')).toBe('step')
    expect(navRow('Send mail').getAttribute('aria-current')).toBeNull()
    // the modal repeats the row's chips — the secret chip now renders twice
    // (row + the navigator's expanded row), each carrying the same §14 tooltip sentence
    const chips = screen.getAllByText('MAIL_PASSWORD')
    expect(chips).toHaveLength(2)
    for (const chip of chips) {
      expect(chip.closest('span')?.getAttribute('aria-label'))
        .toBe('This step uses the MAIL_PASSWORD secret from your Keychain')
    }
    expect((screen.getByLabelText('Previous step') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('Next step') as HTMLButtonElement).disabled).toBe(false)
  })

  it('a bare step\'s modal shows only the always-on time-limit chip', () => {
    const steps = [step({ name: 'Bare step', code: 'send(x)' })]
    render(
      <StepList variant="detail" steps={steps} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer" />,
    )
    fireEvent.click(screen.getAllByText('Bare step')[0])
    // row + modal each carry the one chip; an empty family simply has no chip
    expect(screen.getAllByText('15m')).toHaveLength(2)
    expect(screen.queryByText('Cloud writer')).toBeNull() // no agent → no agent chip
    expect(screen.queryByText(/retr/)).toBeNull()
  })

  it('the code pane numbers every line and counts them, singular at one, ignoring the trailing final newline', () => {
    const steps = [
      // trailing \n — the file-final newline is neither rendered nor counted
      step({ name: 'Three liner', code: 'a = 1\nb = 2\nsend(a + b)\n' }),
      step({ name: 'One liner', code: 'send(x)' }),
    ]
    render(
      <StepList variant="detail" steps={steps} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer" />,
    )
    fireEvent.click(screen.getAllByText('Three liner')[0])
    expect(screen.getByText('3 lines')).toBeTruthy()
    // no filename on the step → the toolbar falls back to 'script'
    expect(screen.getByText('script')).toBeTruthy()
    // a line-number gutter beside every rendered line, and no fourth row
    const gutter = () => Array.from(screen.getByRole('dialog').querySelectorAll('span'))
      .filter((el) => (el.style as CSSStyleDeclaration).userSelect === 'none').map((el) => el.textContent)
    expect(gutter()).toEqual(['1', '2', '3'])
    fireEvent.click(screen.getByLabelText('Next step'))
    expect(screen.getByText('1 line')).toBeTruthy()
    expect(gutter()).toEqual(['1'])
  })

  it('an empty script line renders a newline so a copied selection keeps blank lines', () => {
    render(
      <StepList variant="detail" steps={[step({ name: 'Gappy', code: 'a = 1\n\nb = 2' })]} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer" />,
    )
    fireEvent.click(screen.getAllByText('Gappy')[0])
    const cells = Array.from(screen.getByRole('dialog').querySelectorAll('span'))
      .filter((el) => (el.style as CSSStyleDeclaration).whiteSpace === 'pre-wrap').map((el) => el.textContent)
    expect(cells).toEqual(['a = 1', '\n', 'b = 2'])
  })

  it('detail variant: package chips are modal-only — absent from the row, present once opened', () => {
    const packages: PackageDep[] = [{ pip: 'beautifulsoup4', import: 'bs4', why: 'parse pages' }]
    const steps = [step({ name: 'Parse page', code: 'import bs4' })]
    render(
      <StepList variant="detail" steps={steps} agents={[AGENT]} secrets={[]} packages={packages} fallbackAgent="Cloud writer" />,
    )
    // §9.2: the detail rows carry no package chips
    expect(screen.queryByText('bs4')).toBeNull()
    fireEvent.click(screen.getAllByText('Parse page')[0])
    // 'bs4' also rides the script inside the code card — assert the chip itself
    const chip = screen.getAllByText('bs4').find((el) =>
      el.closest('span')?.getAttribute('aria-label')?.includes('Python package'))
    expect(chip!.closest('span')?.getAttribute('aria-label'))
      .toBe('This step uses the bs4 Python package — parse pages')
  })

  it('detail variant: next/prev buttons, the navigator rows and the arrow keys flip steps, guarded at both ends', () => {
    renderDetail()
    openFirst()
    fireEvent.click(screen.getByLabelText('Next step'))
    expect(screen.getByText('STEP 2 OF 2')).toBeTruthy()
    expect((screen.getByLabelText('Next step') as HTMLButtonElement).disabled).toBe(true)
    // only the viewed navigator row expands to show its description
    expect(navRow('Send mail').getAttribute('aria-current')).toBe('step')
    expect(screen.getByRole('dialog').textContent).toContain('mails it')
    expect(screen.getByRole('dialog').textContent).not.toContain('reads it')
    // clicking a navigator row views that step
    fireEvent.click(navRow('Fetch page'))
    expect(screen.getByText(/STEP 1 OF 2/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Next step'))
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(screen.getByText(/STEP 1 OF 2/)).toBeTruthy()
    // ArrowLeft at the first step is a no-op, never an underflow
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(screen.getByText(/STEP 1 OF 2/)).toBeTruthy()
  })

  it('the viewed navigator row is a text-selectable block, the others are buttons', () => {
    renderDetail()
    openFirst()
    const viewed = navRow('Fetch page')
    expect(viewed.tagName).toBe('DIV')
    expect(viewed.style.userSelect).toBe('text')
    expect(navRow('Send mail').tagName).toBe('BUTTON')
    // clicking the viewed block is inert; clicking a button row views it and swaps roles
    fireEvent.click(viewed)
    expect(screen.getByText(/STEP 1 OF 2/)).toBeTruthy()
    fireEvent.click(navRow('Send mail'))
    expect(navRow('Send mail').tagName).toBe('DIV')
    expect(navRow('Fetch page').tagName).toBe('BUTTON')
  })

  it('flipping steps never leaves a focus target: the viewed block is unfocusable and a key flip blurs the controls', () => {
    renderDetail()
    openFirst()
    expect(navRow('Fetch page').getAttribute('tabindex')).toBeNull()
    // a clicked row unmounts as it becomes the viewed block — focus falls to the body
    navRow('Send mail').focus()
    fireEvent.click(navRow('Send mail'))
    expect(document.activeElement).toBe(document.body)
    // an arrow flip drops focus from a focused chevron
    const prev = screen.getByLabelText('Previous step') as HTMLButtonElement
    prev.focus()
    expect(document.activeElement).toBe(prev)
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(screen.getByText(/STEP 1 OF 2/)).toBeTruthy()
    expect(document.activeElement).toBe(document.body)
    // …and from the page's step row behind the modal (the one that opened it)
    const pageRow = screen.getAllByText('Fetch page')[0].closest('button') as HTMLButtonElement
    pageRow.focus()
    expect(document.activeElement).toBe(pageRow)
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(screen.getByText('STEP 2 OF 2')).toBeTruthy()
    expect(document.activeElement).toBe(document.body)
  })

  it('the frame height follows the longest script, floored and capped', () => {
    // jsdom drops clamp() from the CSSOM, so the sizing rule is asserted on the helper
    // toolbar 44 + padding 38 + 100 lines at 12px/1.65 = 2062 → the 82vh cap applies
    expect(stepModalFrame([step({ code: 'a' }), step({ code: Array(100).fill('x').join('\n') })]))
      .toBe('clamp(440px, 2062px, 82vh)')
    // two lines (the trailing newline uncounted): 44 + 38 + 39.6 → 122, floored to 440
    expect(stepModalFrame([step({ code: 'a\nb\n' })])).toBe('clamp(440px, 122px, 82vh)')
    expect(stepModalFrame([step({ code: '' })])).toBe('clamp(440px, 102px, 82vh)')
  })

  it('editor variant: an empty script reads as the placeholder, and swapped steps close the modal', () => {
    const steps = [step({ name: 'Empty step', code: '' })]
    const { rerender } = render(
      <StepList variant="editor" steps={steps} availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]} />,
    )
    fireEvent.click(screen.getAllByText('Empty step')[0])
    expect(screen.getByRole('dialog').textContent).toContain('# script not written yet')
    // §11: a sync/undo hands the list a new steps array — the modal closes
    rerender(
      <StepList
        variant="editor" steps={[step({ name: 'Empty step', code: '' })]}
        availAgents={[AGENT]} allAgents={[AGENT]} secrets={[]} packages={[]}
      />,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Escape closes the modal', async () => {
    renderDetail()
    openFirst()
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('the row keeps one wordless expand glyph and never a hide-script label', () => {
    renderDetail()
    expect(document.querySelectorAll('span[title="View script"]')).toHaveLength(2)
    openFirst()
    expect(screen.queryByText('Hide script')).toBeNull()
  })
})

describe('find in script (§9.2)', () => {
  const CODE = 'def send(x):\n    return Send(x)  # send it\nlog("done")'
  const renderOne = () => {
    render(
      <StepList variant="detail" steps={[step({ name: 'Finder', code: CODE })]} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer" />,
    )
    fireEvent.click(screen.getAllByText('Finder')[0])
  }
  const marks = () => Array.from(document.querySelectorAll('mark')).map((m) => `${m.textContent}:${m.getAttribute('data-match')}`)
  const counter = () => screen.getByTestId('find-counter').textContent

  it('findInLines: case-insensitive substring hits in document order, none for an empty query', () => {
    const lines = highlightPythonLines(CODE)
    expect(findInLines(lines, '')).toEqual([])
    expect(findInLines(lines, 'send')).toEqual([
      { line: 0, start: 4, end: 8 }, { line: 1, start: 11, end: 15 }, { line: 1, start: 22, end: 26 },
    ])
  })

  it('the find button opens the bar; typing marks every hit, the first as current, with a counter', () => {
    renderOne()
    expect(screen.queryByTestId('find-bar')).toBeNull()
    fireEvent.click(screen.getByLabelText('Find in script'))
    expect(screen.getByLabelText('Find in script', { selector: 'button' }).getAttribute('aria-pressed')).toBe('true')
    const input = screen.getByPlaceholderText('Find in script') as HTMLInputElement
    expect(counter()).toBe('')
    fireEvent.change(input, { target: { value: 'SEND' } })
    // the marks keep the token's text and split around the match: "send" inside "Send(" keeps its call color
    expect(marks()).toEqual(['send:current', 'Send:hit', 'send:hit'])
    expect(counter()).toBe('1 of 3')
    fireEvent.change(input, { target: { value: 'nothing here' } })
    expect(marks()).toEqual([])
    expect(counter()).toBe('No matches')
    expect((screen.getByLabelText('Next match') as HTMLButtonElement).disabled).toBe(true)
  })

  it('next / previous and Enter / Shift+Enter step through matches, wrapping at the ends', () => {
    renderOne()
    fireEvent.click(screen.getByLabelText('Find in script'))
    const input = screen.getByPlaceholderText('Find in script')
    fireEvent.change(input, { target: { value: 'send' } })
    fireEvent.click(screen.getByLabelText('Next match'))
    expect(counter()).toBe('2 of 3')
    expect(marks()[1]).toBe('Send:current')
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(counter()).toBe('1 of 3') // wrapped
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(counter()).toBe('3 of 3')
    fireEvent.click(screen.getByLabelText('Previous match'))
    expect(counter()).toBe('2 of 3')
  })

  it('Escape in the field closes the bar, not the modal; arrow keys in the field never flip the step', () => {
    render(
      <StepList
        variant="detail" steps={[step({ name: 'Finder', code: CODE }), step({ name: 'Other', code: 'x' })]}
        agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer"
      />,
    )
    fireEvent.click(screen.getAllByText('Finder')[0])
    fireEvent.click(screen.getByLabelText('Find in script'))
    const input = screen.getByPlaceholderText('Find in script')
    fireEvent.keyDown(input, { key: 'ArrowRight' })
    expect(screen.getByText(/STEP 1 OF 2/)).toBeTruthy()
    fireEvent.change(input, { target: { value: 'send' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByTestId('find-bar')).toBeNull()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(document.querySelectorAll('mark')).toHaveLength(0)
    // the query is cleared with the bar
    fireEvent.click(screen.getByLabelText('Find in script'))
    expect((screen.getByPlaceholderText('Find in script') as HTMLInputElement).value).toBe('')
  })

  it('⌘F opens the bar, and the query survives a step flip while the current match resets', () => {
    render(
      <StepList
        variant="detail" steps={[step({ name: 'Finder', code: CODE }), step({ name: 'Other', code: 'send(1)\nsend(2)' })]}
        agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer"
      />,
    )
    fireEvent.click(screen.getAllByText('Finder')[0])
    fireEvent.keyDown(document, { key: 'f', metaKey: true })
    expect(screen.getByTestId('find-bar')).toBeTruthy()
    fireEvent.change(screen.getByPlaceholderText('Find in script'), { target: { value: 'send' } })
    fireEvent.click(screen.getByLabelText('Next match'))
    expect(counter()).toBe('2 of 3')
    fireEvent.click(screen.getByLabelText('Next step'))
    expect(screen.getByTestId('find-bar')).toBeTruthy()
    expect((screen.getByPlaceholderText('Find in script') as HTMLInputElement).value).toBe('send')
    expect(counter()).toBe('1 of 2')
    expect(marks()).toEqual(['send:current', 'send:hit'])
  })

  it('a highlighted line keys its wrappers apart from the untouched tokens', () => {
    // §9.2: markLine rewraps only the matched tokens — its wrapper keys must
    // not collide with the highlighter's own token keys on the same line
    // (React's duplicate-key warning, raised on the flipped-to script).
    const errors: string[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { errors.push(args.map(String).join(' ')) })
    render(
      <StepList
        variant="detail" steps={[step({ name: 'Finder', code: CODE }), step({ name: 'Other', code: 'send(1)\nsend(2)' })]}
        agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer"
      />,
    )
    fireEvent.click(screen.getAllByText('Finder')[0])
    fireEvent.keyDown(document, { key: 'f', metaKey: true })
    fireEvent.change(screen.getByPlaceholderText('Find in script'), { target: { value: 'send' } })
    fireEvent.click(screen.getByLabelText('Next match'))
    fireEvent.click(screen.getByLabelText('Next step'))
    expect(marks().length).toBeGreaterThan(0)
    expect(errors.filter((e) => e.includes('same key'))).toEqual([])
    spy.mockRestore()
  })
})

describe('navigator facts (§9.2 literal scans)', () => {
  it('stepHosts: distinct http(s) hosts in order, ports kept, interpolated hosts skipped', () => {
    const code = [
      'a = "https://reports.example.com/v2/entries?page=1"',
      'b = "http://localhost:8080/x"',
      'c = "https://reports.example.com/other"',
      'd = f"https://{site}/feed"',
    ].join('\n')
    expect(stepHosts(code)).toEqual(['reports.example.com', 'localhost:8080'])
  })

  it('stepAgentPrompts: the first literal in each ask/read/write call, whitespace collapsed, truncated at 72', () => {
    const code = [
      'latest = agent.read(page[:5000],',
      '    "newest chapter: number,   title, date")  # reads like a person',
      'agents["550e8400-e29b-41d4-a716-446655440000"].ask(f"Summarize {x}")',
      'note = agent.ask(prompt)',
      "agent.write(rows, '" + 'x'.repeat(80) + "')",
      'agent.ask("Return the canonical reading page URL for this manga, or an empty string when unsure")',
    ].join('\n')
    const r = stepAgentPrompts(code)
    expect(r.count).toBe(5)
    // the f-string is still a literal prompt; the variable prompt has none;
    // a long prompt cuts at a word boundary (an unbroken token at the limit)
    expect(r.prompts).toEqual([
      'newest chapter: number, title, date', 'Summarize {x}', `${'x'.repeat(71)}…`,
      'Return the canonical reading page URL for this manga, or an empty…',
    ])
  })

  it('stepAgentPrompts: adjacent literals join as Python concatenates them', () => {
    const code = 'agent.read(title, "Return the URL, or an empty "\n    "string when unsure.")\nagent.ask("a" "b", data)'
    expect(stepAgentPrompts(code).prompts).toEqual(['Return the URL, or an empty string when unsure.', 'ab'])
  })

  it('stepAgentPrompts: a string containing parentheses does not unbalance the call scan', () => {
    const code = 'a = agent.ask("count the (open) items")\nb = agent.ask("second")'
    expect(stepAgentPrompts(code).prompts).toEqual(['count the (open) items', 'second'])
  })

  it('stepFiles: open() modes and Path methods split reads from writes; non-workspace names skipped', () => {
    const code = [
      'links = json.load(open("links.json"))',
      'json.dump(found, open("found.json", "w"))',
      'with open("log.txt", mode="a") as f: pass',
      'Path("table.html").write_text(html)',
      'raw = Path("in.csv").read_text()',
      'open("/etc/hosts")',
      'open("~/notes.txt")',
      'open("../up.txt")',
      'open(f"{name}.json")',
    ].join('\n')
    expect(stepFiles(code)).toEqual({ reads: ['links.json', 'in.csv'], writes: ['found.json', 'log.txt', 'table.html'] })
  })

  it('stepParams: subscript and .get literals, deduped, interpolated keys skipped', () => {
    expect(stepParams('a = params["recipients"]\nb = params.get("subject", "")\nc = params["recipients"]\nd = params[f"{k}"]\ne = params[key]'))
      .toEqual(['recipients', 'subject'])
  })

  it('stepMemory: load and save key literals', () => {
    expect(stepMemory('rows = memory.load("sources", [])\nmemory.save("seen", seen)\nmemory.load(key)'))
      .toEqual({ loads: ['sources'], saves: ['seen'] })
  })

  it('stepChange: unchanged-since walks back through identical predecessors; changed / new stop at the viewed version', () => {
    const v = (version: number, ...steps: [string, string][]) => ({ version, steps: steps.map(([name, code]) => ({ name, description: '', code })) })
    const history = [v(1, ['Fetch', 'a']), v(2, ['Fetch', 'a'], ['Send', 's1']), v(3, ['Fetch', 'b'], ['Send', 's1'])]
    const st = (name: string, code: string): Step => ({ name, description: '', code })
    expect(stepChange(st('Fetch', 'b'), 3, history)).toBe('Changed in v3')
    expect(stepChange(st('Send', 's1'), 3, history)).toBe('Unchanged since v2')
    expect(stepChange(st('Fetch', 'a'), 2, history)).toBe('Unchanged since v1')
    expect(stepChange(st('Send', 's1'), 2, history)).toBe('New in v2')
    expect(stepChange(st('Fetch', 'a'), 1, history)).toBe('New in v1')
    // the editor's draft compares against the newest stored version
    // a draft identical to the current version reads as that version (trailing newline ignored)
    expect(stepChange(st('Fetch', 'b\n'), 'draft', history)).toBe('Changed in v3')
    expect(stepChange(st('Send', 's1'), 'draft', history)).toBe('Unchanged since v2')
    expect(stepChange(st('Fetch', 'c'), 'draft', history)).toBe('Changed in this draft')
    expect(stepChange(st('Notify', 'n'), 'draft', history)).toBe('New in this draft')
    expect(stepChange(st('Fetch', 'a'), 9, history)).toBeNull() // unknown revision
    expect(stepChange(st('Fetch', 'a'), 1, [])).toBeNull()
  })

  it('stepFacts: ordered sections of bullets, file hand-offs resolved across steps, empty sections dropped', () => {
    const steps: Step[] = [
      { name: 'Read list', description: '', code: 'rows = memory.load("sources", [])\njson.dump(rows, open("links.json", "w"))' },
      { name: 'Check', description: '', code: 'links = json.load(open("links.json"))\nr = fetch_page("https://example.org/a")\nr2 = fetch_page("https://b.io")\nx = agent.read(r, "newest chapter")\nagent.ask(p)\njson.dump(x, open("found.json", "w"))' },
      { name: 'Compare', description: '', code: 'a = json.load(open("found.json"))\nb = json.load(open("links.json"))\nmemory.save("seen", a)\nopen("report.html", "w")' },
      { name: 'Send', description: '', code: 'open("found.json")' },
    ]
    const flat = (secs: ReturnType<typeof stepFacts>) => secs.map((s) => [s.label, ...s.items])
    expect(flat(stepFacts(steps, 0, undefined, undefined))).toEqual([
      ['FILES', 'Hands links.json to steps 2 and 3'], ['MEMORY', 'Reads sources'],
    ])
    expect(flat(stepFacts(steps, 1, undefined, undefined))).toEqual([
      ['WEBSITES', 'example.org', 'b.io'], ['ASKS THE AGENT', '“newest chapter”', '1 more call'],
      ['FILES', 'Reads links.json from step 1', 'Hands found.json to steps 3 and 4'],
    ])
    const c = stepFacts(steps, 2, 2, [{ version: 2, steps }, { version: 1, steps: [] }])
    expect(flat(c)).toEqual([
      ['FILES', 'Reads found.json from step 2', 'Reads links.json from step 1', 'Writes report.html'],
      ['MEMORY', 'Saves seen'], ['VERSION', 'New in v2'],
    ])
    expect(c.map((s) => s.key)).toEqual(['files', 'memory', 'version'])
    // no literal prompt at all → the bare count
    expect(flat(stepFacts([{ name: 'x', description: '', code: 'agent.ask(p)\nagent.ask(q)' }], 0, undefined, undefined))).toEqual([['ASKS THE AGENT', '2 calls']])
    // params lead, one bullet each, labeled through their §4.2 definition, raw name when none matches
    const defs: ParamDef[] = [
      { name: 'recipients', kind: 'list', label: 'Recipients', help: '' },
      { name: 'subject', kind: 'text', label: 'Subject line', help: '' },
    ]
    const pcode = 'to = params["recipients"]\ns = params.get("subject")\nx = params["extra"]\nr = fetch_page("https://a.io")'
    expect(flat(stepFacts([{ name: 'p', description: '', code: pcode }], 0, undefined, undefined, defs)))
      .toEqual([['PARAMETERS', 'Recipients', 'Subject line', 'extra'], ['WEBSITES', 'a.io']])
    // nothing found → no sections at all
    expect(stepFacts([{ name: 'p', description: '', code: 'x = 1' }], 0, undefined, undefined)).toEqual([])
  })

  it('the navigator shows the fact sections under the viewed row only', () => {
    const steps: Step[] = [
      step({ name: 'Pull', code: 'r = fetch_page("https://example.org")\njson.dump(r, open("out.json", "w"))' }),
      step({ name: 'Use', code: 'open("out.json")' }),
    ]
    render(
      <StepList
        variant="detail" steps={steps} agents={[AGENT]} secrets={[]} packages={[]} fallbackAgent="Cloud writer"
        history={[{ version: 1, steps }]} viewing={1}
      />,
    )
    // rows carry no facts
    expect(screen.queryByTestId('step-facts')).toBeNull()
    fireEvent.click(screen.getAllByText('Pull')[0])
    const sections = () => Array.from(screen.getByTestId('step-facts').children).map((el) => el.textContent)
    expect(sections()).toEqual(['WEBSITES•example.org', 'FILES•Hands out.json to step 2', 'VERSION•New in v1'])
    fireEvent.click(screen.getByLabelText('Next step'))
    expect(sections()).toEqual(['FILES•Reads out.json from step 1', 'VERSION•New in v1'])
  })
})

describe('stepSecretTags', () => {
  it('unions declared entries (with why) and code references, resolving live names', () => {
    const tags = stepSecretTags(step({
      secrets: [{ id: CRM_ID, why: 'auth' }],
      code: `a = secrets["${CRM_ID}"]\nb = secrets["${MAIL_ID}"]`,
    }), SECRETS)
    expect(tags).toEqual([
      { id: CRM_ID, name: 'CRM_API_KEY', missing: false, why: 'auth' },
      { id: MAIL_ID, name: 'MAIL_PASSWORD', missing: false },
    ])
  })

  it('a dangling id keeps its short prefix and missing flag', () => {
    const tags = stepSecretTags(step({ code: `a = secrets["${GONE_ID}"]` }), SECRETS)
    expect(tags).toEqual([{ id: GONE_ID, name: '99999999…', missing: true }])
  })

  it('§5.1: an unresolved imported id takes the archive name and the imported flag', () => {
    const tags = stepSecretTags(
      step({ code: `a = secrets["${IMP_SECRET_ID}"]\nb = secrets["${GONE_ID}"]` }),
      SECRETS, UNRESOLVED,
    )
    expect(tags).toEqual([
      { id: IMP_SECRET_ID, name: 'STRIPE_KEY', missing: true, imported: true },
      { id: GONE_ID, name: '99999999…', missing: true },
    ])
  })

  it('§5.1: an agent entry in the map never resolves a secret tag', () => {
    const tags = stepSecretTags(step({ code: `a = secrets["${IMP_AGENT_ID}"]` }), SECRETS, UNRESOLVED)
    expect(tags).toEqual([{ id: IMP_AGENT_ID, name: '44444444…', missing: true }])
  })
})

describe('stepPackageTags', () => {
  const deps: PackageDep[] = [
    { pip: 'pandas', import: 'pandas', why: 'general data work', version: '2.2' },
    { pip: 'beautifulsoup4', import: 'bs4', why: 'parse pages' },
  ]
  it('unions declared entries (per-step why) with code-matched declared imports', () => {
    const tags = stepPackageTags(step({
      packages: [{ import: 'pandas', why: 'parses the price tables' }],
      code: 'import bs4',
    }), deps)
    expect(tags).toEqual([
      { import: 'pandas', why: 'parses the price tables', version: '2.2' },
      { import: 'bs4', why: 'parse pages', version: undefined },
    ])
  })
  it('a declared entry with an empty why falls back to the declaration general why', () => {
    const tags = stepPackageTags(step({ packages: [{ import: 'pandas', why: '' }] }), deps)
    expect(tags).toEqual([{ import: 'pandas', why: 'general data work', version: '2.2' }])
  })
})

describe('ParamValueEditor (shared)', () => {
  const listParam: ParamDef = { name: 'urls', kind: 'list', label: 'URLs', help: '' }
  const noop = {
    setOn: () => {}, setLines: () => {}, setRows: () => {}, setText: () => {}, setNumber: () => {},
  }

  it('detail list: always shows the count footer and commits removals at once', () => {
    const setLines = vi.fn()
    render(
      <ParamValueEditor
        variant="detail" p={listParam} on={false} lines={['a', 'b']} rows={[]} value=""
        {...noop} setLines={setLines}
      />,
    )
    expect(screen.getByText('2 entries')).toBeTruthy()
    const removeBtns = document.querySelectorAll('button.ad-btn-x')
    fireEvent.click(removeBtns[0])
    expect(setLines).toHaveBeenCalledWith(['b'], true) // removal → commit now
    fireEvent.click(screen.getByText('+ Add line'))
    expect(setLines).toHaveBeenCalledWith(['a', 'b', ''])
  })

  it('draft list: footer only with validate, invalid rows badge NOT A VALID LINK', () => {
    const { rerender } = render(
      <ParamValueEditor variant="draft" p={listParam} on={false} lines={['a']} rows={[]} value="" {...noop} />,
    )
    expect(screen.queryByText(/valid links/)).toBeNull()
    rerender(
      <ParamValueEditor
        variant="draft" p={{ ...listParam, validate: true }} on={false}
        lines={['https://ok.example', 'nope']} rows={[]} value="" {...noop}
      />,
    )
    expect(screen.getByText('2 lines · 1 valid links · 1 needs attention')).toBeTruthy()
    expect(screen.getByText('NOT A VALID LINK')).toBeTruthy()
  })

  it('kv: draft shows Key/Value placeholders and "+ Add pair", detail shows "+ Add row"', () => {
    const kv: ParamDef = { name: 'h', kind: 'kv', label: 'Headers', help: '' }
    const { rerender } = render(
      <ParamValueEditor variant="draft" p={kv} on={false} lines={[]} rows={[{ key: 'a', value: 'b' }]} value="" {...noop} />,
    )
    expect(screen.getByPlaceholderText('Key')).toBeTruthy()
    expect(screen.getByText('+ Add pair')).toBeTruthy()
    rerender(
      <ParamValueEditor variant="detail" p={kv} on={false} lines={[]} rows={[{ key: 'a', value: 'b' }]} value="" {...noop} />,
    )
    expect(screen.queryByPlaceholderText('Key')).toBeNull()
    expect(screen.getByText('+ Add row')).toBeTruthy()
  })

  it('list: editing a line patches it in place without committing', () => {
    const setLines = vi.fn()
    render(
      <ParamValueEditor
        variant="draft" p={listParam} on={false} lines={['a', 'b']} rows={[]} value=""
        {...noop} setLines={setLines}
      />,
    )
    const inputs = document.querySelectorAll('input.ad-input')
    fireEvent.change(inputs[1], { target: { value: 'B' } })
    expect(setLines).toHaveBeenCalledWith(['a', 'B'])   // edit → no commit flag
  })

  it('kv: edits patch the one row, removal commits at once, add appends a blank pair', () => {
    const setRows = vi.fn()
    const kv: ParamDef = { name: 'h', kind: 'kv', label: 'Headers', help: '' }
    const rows = [{ key: 'a', value: '1' }, { key: 'b', value: '2' }]
    render(
      <ParamValueEditor variant="draft" p={kv} on={false} lines={[]} rows={rows} value="" {...noop} setRows={setRows} />,
    )
    fireEvent.change(screen.getAllByPlaceholderText('Key')[0], { target: { value: 'A' } })
    expect(setRows).toHaveBeenCalledWith([{ key: 'A', value: '1' }, { key: 'b', value: '2' }])
    fireEvent.change(screen.getAllByPlaceholderText('Value')[1], { target: { value: '9' } })
    expect(setRows).toHaveBeenCalledWith([{ key: 'a', value: '1' }, { key: 'b', value: '9' }])
    fireEvent.click(screen.getByLabelText('Remove a'))  // named row → labeled remove button
    expect(setRows).toHaveBeenCalledWith([{ key: 'b', value: '2' }], true) // removal → commit now
    fireEvent.click(screen.getByText('+ Add pair'))
    expect(setRows).toHaveBeenCalledWith([...rows, { key: '', value: '' }])
  })

  it('kv: a blank key falls back to the generic remove label', () => {
    const kv: ParamDef = { name: 'h', kind: 'kv', label: 'Headers', help: '' }
    render(
      <ParamValueEditor variant="detail" p={kv} on={false} lines={[]} rows={[{ key: ' ', value: '' }]} value="" {...noop} />,
    )
    expect(screen.getByLabelText('Remove row')).toBeTruthy()
  })

  it('number: strips non-digits through setNumber; detail variant is inputMode=numeric', () => {
    const num: ParamDef = { name: 'n', kind: 'number', label: 'Count', help: '', min: 1 }
    const setNumber = vi.fn()
    render(
      <ParamValueEditor variant="detail" p={num} on={false} lines={[]} rows={[]} value="5" {...noop} setNumber={setNumber} />,
    )
    const input = document.querySelector('input.ad-input') as HTMLInputElement
    expect(input.getAttribute('inputmode')).toBe('numeric')
    fireEvent.change(input, { target: { value: '1a2' } })
    expect(setNumber).toHaveBeenCalledWith('12')
  })

  it('toggle and text render their controls', () => {
    const setOn = vi.fn()
    const { rerender } = render(
      <ParamValueEditor
        variant="detail" p={{ name: 't', kind: 'toggle', label: 'On', help: '' }}
        on={true} lines={[]} rows={[]} value="" {...noop} setOn={setOn}
      />,
    )
    const setText = vi.fn()
    rerender(
      <ParamValueEditor
        variant="draft" p={{ name: 's', kind: 'text', label: 'Query', help: '', placeholder: 'type here' }}
        on={false} lines={[]} rows={[]} value="hi" {...noop} setText={setText}
      />,
    )
    const input = screen.getByPlaceholderText('type here') as HTMLInputElement
    expect(input.value).toBe('hi')
    fireEvent.change(input, { target: { value: 'hello' } })
    expect(setText).toHaveBeenCalledWith('hello')
  })
})
