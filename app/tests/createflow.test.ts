// Unit tests for the exported pure helpers in src/pages/CreateFlow.tsx.
// The module graph pulls in store/api/ui/result — api is mocked so importing
// never opens sockets or fetches.
import { describe, expect, it, vi } from 'vitest'
import type { Blocker, ChatEntry, DraftTrigger, ParamDef, SpecBlock, Step, Trigger } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {},
}))

import {
  specToText, textToSpec, amendSpec, stepSecretIds, stepSecretTags, secretRefsOf,
  mergeDraftTriggers, needsMessageTriggerSetup, persistChat, chatSinceBoundary, applyTestValues,
  applyTriggerOps, coerceParamValue,
  stripTrigger,
} from '../src/pages/CreateFlow'
import { docLineCount, docModalFrame } from '../src/pages/createflow/DocEditorModal'

const step = (over: Partial<Step> = {}): Step =>
  ({ name: 's', description: '', code: '', ...over })

// §4.8 fixture ids — step entries and secrets["<id>"] code refs use uuids
const ALPHA_ID = '11111111-1111-1111-1111-111111111111'
const BETA_ID = '22222222-2222-2222-2222-222222222222'
const DB_ID = '33333333-3333-3333-3333-333333333333'
const SECRETS = [
  { id: ALPHA_ID, name: 'ALPHA', description: '', set: true, usedBy: [] },
  { id: BETA_ID, name: 'BETA', description: '', set: true, usedBy: [] },
]

describe('specToText / textToSpec', () => {
  const blocks: SpecBlock[] = [
    { kind: 'h1', text: 'Title' },
    { kind: 'h2', text: 'Section' },
    { kind: 'li', text: 'item' },
    { kind: 'p', text: 'paragraph' },
  ]

  it('serializes with "# ", "## ", "- " prefixes and plain paragraphs', () => {
    expect(specToText(blocks)).toBe('# Title\n## Section\n- item\nparagraph')
  })
  it('round-trips stably', () => {
    expect(textToSpec(specToText(blocks))).toEqual(blocks)
  })
  it('drops blank lines and trims', () => {
    expect(textToSpec('a\n\n   \nb')).toEqual([
      { kind: 'p', text: 'a' }, { kind: 'p', text: 'b' },
    ])
  })
  it('"## " beats "# "; a hash without a space is a paragraph', () => {
    expect(textToSpec('## X')).toEqual([{ kind: 'h2', text: 'X' }])
    expect(textToSpec('# X')).toEqual([{ kind: 'h1', text: 'X' }])
    expect(textToSpec('- X')).toEqual([{ kind: 'li', text: 'X' }])
    expect(textToSpec('#X')).toEqual([{ kind: 'p', text: '#X' }])
  })
})

describe('stepSecretIds', () => {
  it('unions declared entry ids with secrets["<id>"] code refs, deduped', () => {
    const s = step({
      secrets: [{ id: ALPHA_ID, why: 'signs the request' }],
      code: `x = secrets["${BETA_ID}"] + secrets["${ALPHA_ID}"]\ny = secrets["${BETA_ID}"]`,
    })
    expect(stepSecretIds(s)).toEqual([ALPHA_ID, BETA_ID])
  })
  it('stepSecretTags keeps the declared why and resolves live names; code refs carry none', () => {
    const s = step({
      secrets: [{ id: ALPHA_ID, why: 'signs the request' }],
      code: `x = secrets["${BETA_ID}"]`,
    })
    expect(stepSecretTags(s, SECRETS)).toEqual([
      { id: ALPHA_ID, name: 'ALPHA', missing: false, why: 'signs the request' },
      { id: BETA_ID, name: 'BETA', missing: false },
    ])
  })
  it('a variable subscript or secrets.NAME attribute is NOT matched', () => {
    expect(stepSecretIds(step({ code: 'y = secrets[x]\nz = secrets.FOO' }))).toEqual([])
  })
  it('empty step → empty list', () => {
    expect(stepSecretIds(step())).toEqual([])
  })
})

describe('secretRefsOf', () => {
  it('aggregates id → step indices', () => {
    const steps = [
      step({ code: `a = secrets["${ALPHA_ID}"]` }),
      step({ code: `b = secrets["${ALPHA_ID}"] + secrets["${DB_ID}"]` }),
      step({ code: 'plain' }),
    ]
    expect(secretRefsOf(steps)).toEqual([
      { id: ALPHA_ID, steps: [0, 1] },
      { id: DB_ID, steps: [1] },
    ])
  })

  it('§5.1: an unresolved imported id carries the archive name', () => {
    const steps = [step({ code: `a = secrets["${ALPHA_ID}"] + secrets["${DB_ID}"]` })]
    expect(secretRefsOf(steps, {
      [ALPHA_ID]: { kind: 'secret', name: 'STRIPE_KEY', description: 'billing token' },
      // an agent entry never names a secret ref, and neither does a missing one
      [DB_ID]: { kind: 'agent', name: 'Researcher', description: '' },
    })).toEqual([
      { id: ALPHA_ID, steps: [0], importedName: 'STRIPE_KEY' },
      { id: DB_ID, steps: [0] },
    ])
  })
})

describe('amendSpec', () => {
  const blockers: Blocker[] = [
    { reason: ' Site needs login ', fix: ' Use the saved cookie ' },
    { reason: 'Rate limited', fix: 'Retry with backoff' },
  ]
  const lines: SpecBlock[] = [
    { kind: 'li', text: 'Site needs login — Use the saved cookie' },
    { kind: 'li', text: 'Rate limited — Retry with backoff' },
  ]

  it('appends the section when missing', () => {
    const spec: SpecBlock[] = [{ kind: 'h1', text: 'T' }, { kind: 'p', text: 'body' }]
    expect(amendSpec(spec, blockers)).toEqual([
      ...spec,
      { kind: 'h2', text: 'Constraints & resolutions' },
      ...lines,
    ])
  })
  it('inserts at the end of an existing mid-document section, before the next heading', () => {
    const spec: SpecBlock[] = [
      { kind: 'h1', text: 'T' },
      { kind: 'h2', text: 'constraints & RESOLUTIONS' }, // case-insensitive match
      { kind: 'li', text: 'old — resolution' },
      { kind: 'h2', text: 'Next section' },
      { kind: 'p', text: 'tail' },
    ]
    expect(amendSpec(spec, blockers)).toEqual([
      spec[0], spec[1], spec[2],
      ...lines,
      spec[3], spec[4],
    ])
  })
  it('section at the end of the document gets the lines appended', () => {
    const spec: SpecBlock[] = [
      { kind: 'h1', text: 'T' },
      { kind: 'h2', text: 'Constraints & resolutions' },
      { kind: 'li', text: 'old — resolution' },
    ]
    expect(amendSpec(spec, blockers)).toEqual([...spec, ...lines])
  })
})

describe('mergeDraftTriggers', () => {
  const cron = (over: Partial<DraftTrigger>): DraftTrigger =>
    ({ kind: 'cron', enabled: true, ...over } as DraftTrigger)

  it('drafted cron matching an existing expression+timezone keeps the existing entry (id and enabled)', () => {
    const cur: DraftTrigger[] = [
      cron({ id: 'c1', expression: '0 8 * * *', timezone: 'UTC', enabled: false }),
      { id: 't1', kind: 'time', enabled: true, at: '2026-01-01T00:00' },
    ]
    const drafted: DraftTrigger[] = [cron({ expression: '0 8 * * *', timezone: 'UTC' })]
    expect(mergeDraftTriggers(cur, drafted)).toEqual([
      cur[0],       // id c1 and enabled:false preserved
      cur[1],       // non-cron passes through unchanged
    ])
  })

  it('timezone must match too — undefined timezone equals absent, not a different zone', () => {
    const cur: DraftTrigger[] = [cron({ id: 'c1', expression: '0 8 * * *', timezone: 'UTC' })]
    const merged = mergeDraftTriggers(cur, [cron({ expression: '0 8 * * *' })]) // no timezone → no match
    expect(merged).toEqual([{ kind: 'cron', enabled: true, expression: '0 8 * * *' }])
  })

  it('duplicate identical exprs consume distinct existing entries once each', () => {
    const cur: DraftTrigger[] = [
      cron({ id: 'c1', expression: '0 8 * * *', enabled: false }),
      cron({ id: 'c2', expression: '0 8 * * *', enabled: true }),
    ]
    const drafted: DraftTrigger[] = [
      cron({ expression: '0 8 * * *' }),
      cron({ expression: '0 8 * * *' }),
    ]
    const merged = mergeDraftTriggers(cur, drafted)
    expect(merged.map((t) => t.id)).toEqual(['c1', 'c2'])
  })

  it('unmatched drafted cron becomes a new entry with enabled:true', () => {
    const cur: DraftTrigger[] = [
      cron({ id: 'c1', expression: '0 8 * * *' }),
      { id: 'a1', kind: 'app_start', enabled: true },
    ]
    const drafted: DraftTrigger[] = [cron({ expression: '30 9 * * 1', enabled: false })]
    const merged = mergeDraftTriggers(cur, drafted)
    expect(merged).toEqual([
      { kind: 'cron', enabled: true, expression: '30 9 * * 1' }, // enabled forced to true
      cur[1],                                            // app_start survives
    ])
    // the unmatched existing cron is replaced by the drafted schedule
    expect(merged.some((t) => t.id === 'c1')).toBe(false)
  })

  it('user-sourced crons survive a sync that no longer drafts them (§4.3 provenance)', () => {
    const cur: DraftTrigger[] = [
      cron({ id: 'c1', expression: '0 8 * * *', source: 'spec' }),
      cron({ id: 'c2', expression: '0 21 * * *', source: 'user', enabled: false }),
    ]
    const merged = mergeDraftTriggers(cur, [cron({ expression: '0 9 * * *', source: 'spec' })])
    expect(merged).toEqual([
      { kind: 'cron', enabled: true, expression: '0 9 * * *', source: 'spec' },
      cur[1], // the user cron survives, enabled state intact
    ])
  })

  it('a drafted cron matching a user cron keeps the one stored entry — no duplicate', () => {
    const cur: DraftTrigger[] = [cron({ id: 'c2', expression: '0 21 * * *', source: 'user' })]
    const merged = mergeDraftTriggers(cur, [cron({ expression: '0 21 * * *', source: 'spec' })])
    expect(merged).toEqual([cur[0]])
  })
})

describe('mergeDraftTriggers — interval schedules (§4.3)', () => {
  const cron = (over: Partial<DraftTrigger>): DraftTrigger =>
    ({ kind: 'cron', enabled: true, source: 'spec', ...over } as DraftTrigger)
  const interval = (over: Partial<DraftTrigger>): DraftTrigger =>
    ({ kind: 'interval', enabled: true, source: 'spec', ...over } as DraftTrigger)

  it('a drafted interval matching a stored `every` keeps id, enabled and the opt-out', () => {
    const cur: DraftTrigger[] = [
      interval({ id: 'i1', every: 'PT6H', enabled: false, source: 'user', runIfMissed: false }),
    ]
    expect(mergeDraftTriggers(cur, [interval({ every: 'PT6H' })])).toEqual([cur[0]])
  })

  it('a spec-sourced interval the draft no longer derives is dropped; a user one survives', () => {
    const cur: DraftTrigger[] = [
      interval({ id: 'i1', every: 'PT6H', source: 'spec' }),
      interval({ id: 'i2', every: 'P1D', source: 'user', enabled: false }),
    ]
    const merged = mergeDraftTriggers(cur, [interval({ every: 'PT90M' })])
    expect(merged).toEqual([
      { kind: 'interval', enabled: true, every: 'PT90M', source: 'spec' },
      cur[1], // the user interval survives, enabled state intact
    ])
  })

  it('a cron never matches an interval — the two schedule kinds are distinct identities', () => {
    const cur: DraftTrigger[] = [cron({ id: 'c1', expression: '0 8 * * *' })]
    expect(mergeDraftTriggers(cur, [interval({ every: 'PT6H' })]))
      .toEqual([{ kind: 'interval', enabled: true, every: 'PT6H', source: 'spec' }])
    const curInterval: DraftTrigger[] = [interval({ id: 'i1', every: 'PT6H' })]
    expect(mergeDraftTriggers(curInterval, [cron({ expression: '0 8 * * *' })]))
      .toEqual([{ kind: 'cron', enabled: true, expression: '0 8 * * *', source: 'spec' }])
  })
})

describe('applyTriggerOps (§8 chat trigger ops)', () => {
  const cron = (over: Partial<DraftTrigger>): DraftTrigger =>
    ({ kind: 'cron', enabled: true, ...over } as DraftTrigger)
  const base: DraftTrigger[] = [
    cron({ id: 'c1', expression: '0 8 * * *', source: 'spec' }),
    { id: 'd1', kind: 'discord', channel: '123', secret: 'BOT', enabled: true },
  ]

  it('add appends enabled and leaves the rest untouched', () => {
    const { triggers, chips } = applyTriggerOps(base, [
      { op: 'add', trigger: cron({ expression: '0 9 * * *', source: 'user', enabled: false }) },
    ])
    expect(triggers.slice(0, 2)).toEqual(base)
    expect(triggers[2]).toEqual({ kind: 'cron', enabled: true, expression: '0 9 * * *', source: 'user' })
    expect(chips).toEqual(['Cron trigger added.'])
  })

  it('an add matching an existing trigger on identity fields is a no-op backstop', () => {
    const { triggers, chips } = applyTriggerOps(base, [
      { op: 'add', trigger: cron({ expression: '0 8 * * *', source: 'user' }) },
      { op: 'add', trigger: { kind: 'discord', channel: '123', secret: 'BOT', enabled: true } },
    ])
    expect(triggers).toEqual(base)
    expect(chips).toEqual(['That trigger already exists.', 'That trigger already exists.'])
  })

  it('edit replaces the entry fields, keeping id and enabled state', () => {
    const { triggers, chips } = applyTriggerOps(
      [cron({ id: 'c1', expression: '0 8 * * *', enabled: false, source: 'spec' })],
      [{ op: 'edit', index: 1, trigger: cron({ expression: '30 8 * * *', source: 'user' }) }])
    expect(triggers).toEqual([
      { kind: 'cron', expression: '30 8 * * *', source: 'user', id: 'c1', enabled: false },
    ])
    expect(chips).toEqual(['Cron trigger 1 updated.'])
  })

  it('edit keeps the §4.3 runIfMissed opt-out the dialect cannot set', () => {
    const { triggers } = applyTriggerOps(
      [cron({ id: 'c1', expression: '0 8 * * *', source: 'spec', runIfMissed: false })],
      [{ op: 'edit', index: 1, trigger: cron({ expression: '30 8 * * *', source: 'user' }) }])
    expect(triggers).toEqual([{
      kind: 'cron', expression: '30 8 * * *', source: 'user', id: 'c1', enabled: true,
      runIfMissed: false,
    }])
    // the choice belongs to cron/time: an edit into another kind drops it
    const swapped = applyTriggerOps(
      [cron({ id: 'c1', expression: '0 8 * * *', source: 'spec', runIfMissed: false })],
      [{ op: 'edit', index: 1, trigger: { kind: 'discord', channel: '9', secret: 'S', enabled: true } }])
    expect(swapped.triggers[0]).not.toHaveProperty('runIfMissed')
  })

  it('an interval op carries the §4.3 `every` identity and its own kind word', () => {
    const stored: DraftTrigger[] = [
      { id: 'i1', kind: 'interval', every: 'PT6H', source: 'spec', enabled: false, runIfMissed: false },
    ]
    // an add matching the stored `every` is the no-op backstop
    const dup = applyTriggerOps(stored, [
      { op: 'add', trigger: { kind: 'interval', every: 'PT6H', source: 'user', enabled: true } },
    ])
    expect(dup.triggers).toEqual(stored)
    expect(dup.chips).toEqual(['That trigger already exists.'])

    const added = applyTriggerOps(stored, [
      { op: 'add', trigger: { kind: 'interval', every: 'P1D', source: 'user', enabled: false } },
    ])
    expect(added.triggers[1]).toEqual({ kind: 'interval', every: 'P1D', source: 'user', enabled: true })
    expect(added.chips).toEqual(['Interval trigger added.'])

    // an edit keeps id, enabled, and the runIfMissed opt-out the dialect cannot set
    const edited = applyTriggerOps(stored, [
      { op: 'edit', index: 1, trigger: { kind: 'interval', every: 'P1D', source: 'user', enabled: true } },
    ])
    expect(edited.triggers).toEqual([{
      kind: 'interval', every: 'P1D', source: 'user', id: 'i1', enabled: false, runIfMissed: false,
    }])
    expect(edited.chips).toEqual(['Interval trigger 1 updated.'])
  })

  it('enable flips on/off; remove deletes; ops run in order over the evolving list', () => {
    const { triggers, chips } = applyTriggerOps(base, [
      { op: 'enable', index: 1, enabled: false },
      { op: 'remove', index: 2 },
    ])
    expect(triggers).toEqual([{ ...base[0], enabled: false }])
    expect(chips).toEqual(['Cron trigger 1 turned off.', 'Discord trigger 2 removed.'])
  })

  it('indexes keep meaning the CURRENT-triggers numbering after an earlier remove', () => {
    // remove 1 then flip 2: "2" is still the discord entry the agent saw, not
    // a shifted neighbor; an op naming an already-removed entry is inert
    const { triggers, chips } = applyTriggerOps(base, [
      { op: 'remove', index: 1 },
      { op: 'enable', index: 2, enabled: false },
      { op: 'remove', index: 1 },
    ])
    expect(triggers).toEqual([{ ...base[1], enabled: false }])
    expect(chips).toEqual(['Cron trigger 1 removed.', 'Discord trigger 2 turned off.'])
  })
})

describe('coerceParamValue (§8 param_values staging)', () => {
  it('coerces raw yaml values to the def kind so the save-time strict match holds', () => {
    expect(coerceParamValue({ name: 'a', kind: 'toggle', label: '', help: '' }, 'yes')).toBe(true)
    expect(coerceParamValue({ name: 'a', kind: 'number', label: '', help: '', min: 1 }, '5')).toBe(5)
    expect(coerceParamValue({ name: 'a', kind: 'text', label: '', help: '' }, 42)).toBe('42')
    expect(coerceParamValue({ name: 'a', kind: 'list', label: '', help: '' }, 'one')).toEqual(['one'])
    expect(coerceParamValue({ name: 'a', kind: 'kv', label: '', help: '' }, { k: 'v' }))
      .toEqual([{ key: 'k', value: 'v' }])
  })
})

describe('needsMessageTriggerSetup', () => {
  const payloadStep = step({ code: 'msg = execution.trigger_payload["text"]' })

  it('true when a step reads trigger_payload and no message trigger exists', () => {
    expect(needsMessageTriggerSetup([payloadStep], [])).toBe(true)
    expect(needsMessageTriggerSetup([payloadStep], [
      { kind: 'cron', enabled: true, expression: '0 8 * * *', source: 'spec' },
      { kind: 'app_start', enabled: true },
    ])).toBe(true)
  })
  it('false when a discord or imessage trigger exists (enabled state irrelevant)', () => {
    expect(needsMessageTriggerSetup([payloadStep], [
      { kind: 'discord', enabled: false, channel: '123', secret: 'BOT_TOKEN' },
    ])).toBe(false)
    expect(needsMessageTriggerSetup([payloadStep], [
      { kind: 'imessage', enabled: true, from: '+15550123' },
    ])).toBe(false)
  })
  it('false when no step reads trigger_payload', () => {
    expect(needsMessageTriggerSetup([step({ code: 'print("hi")' })], [])).toBe(false)
    expect(needsMessageTriggerSetup([], [])).toBe(false)
  })
  it('matches whole identifiers only', () => {
    expect(needsMessageTriggerSetup([step({ code: 'x = my_trigger_payloads' })], [])).toBe(false)
  })
})

describe('mergeDraftTriggers — non-cron drafted entries', () => {
  const disc = (over: Partial<DraftTrigger> = {}): DraftTrigger =>
    ({ kind: 'discord', enabled: true, channel: '123', secret: 'BOT_TOKEN', ...over } as DraftTrigger)
  const imsg = (over: Partial<DraftTrigger> = {}): DraftTrigger =>
    ({ kind: 'imessage', enabled: true, from: '+15550123', ...over } as DraftTrigger)

  it('drafted discord/imessage with no matching existing entry are appended with enabled:true', () => {
    const cur: DraftTrigger[] = [
      { id: 'c1', kind: 'cron', enabled: true, expression: '0 8 * * *', source: 'spec' },
    ]
    const drafted: DraftTrigger[] = [
      { id: 'c1', kind: 'cron', enabled: true, expression: '0 8 * * *', source: 'spec' },
      disc({ enabled: false }),          // enabled forced back to true on add
      imsg({ pattern: 'report' }),
    ]
    expect(mergeDraftTriggers(cur, drafted)).toEqual([
      cur[0],
      disc(),                        // enabled:true despite the drafted enabled:false
      imsg({ pattern: 'report' }),
    ])
  })

  it('identity treats an absent pattern as empty and coerces mention — no duplicate', () => {
    const cur: DraftTrigger[] = [
      { id: 'd1', ...disc() },                       // pattern/mention undefined
      { id: 'm1', ...imsg({ pattern: undefined }) },
    ]
    const drafted: DraftTrigger[] = [
      disc({ pattern: '', mention: false }),         // (pattern ?? '') and !!mention match
      imsg({ pattern: '' }),
    ]
    expect(mergeDraftTriggers(cur, drafted)).toEqual(cur)
  })

  it('discord author identity ignores order, dupes, and whitespace — no duplicate', () => {
    const cur: DraftTrigger[] = [{ id: 'd1', ...disc({ author: ['alice', 'bob'] }) }]
    const drafted: DraftTrigger[] = [disc({ author: ['bob ', 'alice', 'alice'] })]
    expect(mergeDraftTriggers(cur, drafted)).toEqual(cur)
  })

  it('a genuinely different author set is a new identity and does add', () => {
    const cur: DraftTrigger[] = [{ id: 'd1', ...disc({ author: ['alice'] }) }]
    const merged = mergeDraftTriggers(cur, [disc({ author: ['alice', 'carol'] })])
    expect(merged).toEqual([cur[0], disc({ author: ['alice', 'carol'] })])
  })

  it('a differing pattern, mention, or secret is a new identity and does add', () => {
    const cur: DraftTrigger[] = [{ id: 'd1', ...disc() }]
    const merged = mergeDraftTriggers(cur, [
      disc({ pattern: 'deploy' }),
      disc({ mention: true }),
      disc({ secret: 'OTHER_TOKEN' }),
    ])
    expect(merged).toEqual([
      cur[0],
      disc({ pattern: 'deploy' }),
      disc({ mention: true }),
      disc({ secret: 'OTHER_TOKEN' }),
    ])
  })

  it('a drafted app_start dedupes against an existing one and adds when none exists', () => {
    const cur: DraftTrigger[] = [{ id: 'a1', kind: 'app_start', enabled: false }]
    expect(mergeDraftTriggers(cur, [{ kind: 'app_start', enabled: true }])).toEqual(cur)
    // enabled forced to true on add, like every other appended non-cron entry
    expect(mergeDraftTriggers([], [{ kind: 'app_start', enabled: false }]))
      .toEqual([{ kind: 'app_start', enabled: true }])
  })

  it('drafted time entries are dropped entirely — only crons are mapped', () => {
    const cur: DraftTrigger[] = [
      { id: 't1', kind: 'time', enabled: true, at: '2026-01-01T00:00' },
    ]
    const drafted: DraftTrigger[] = [
      { kind: 'time', enabled: true, at: '2027-06-01T09:00' }, // dropped, not added
    ]
    expect(mergeDraftTriggers(cur, drafted)).toEqual([cur[0]])
  })
})

describe('stripTrigger (§4.4 draft-only trigger shape)', () => {
  it('keeps only the stored fields per kind — derived label/short/connection never leak', () => {
    expect(stripTrigger({
      id: 't1', enabled: true, kind: 'cron', expression: '0 8 * * *', timezone: 'UTC', source: 'spec',
      label: 'Every day at 8:00', short: 'daily 8:00', connection: { state: 'connected' },
    } as Trigger)).toEqual({ id: 't1', enabled: true, kind: 'cron', expression: '0 8 * * *', timezone: 'UTC', source: 'spec' })
    expect(stripTrigger({
      id: 't2', enabled: true, kind: 'time', at: '2026-08-09T09:00:00', label: 'L', short: 'S',
    } as Trigger)).toEqual({ id: 't2', enabled: true, kind: 'time', at: '2026-08-09T09:00:00' })
    expect(stripTrigger({
      id: 't3', enabled: false, kind: 'app_start', label: 'L', short: 'S',
    } as Trigger)).toEqual({ id: 't3', enabled: false, kind: 'app_start' })
    expect(stripTrigger({
      id: 't4', enabled: true, kind: 'interval', every: 'PT6H', source: 'spec',
      label: 'Every 6 hours', short: 'Every 6h',
    } as Trigger)).toEqual({ id: 't4', enabled: true, kind: 'interval', every: 'PT6H', source: 'spec' })
  })
  it('§4.3 runIfMissed rides a draft only when false: true is the absent default', () => {
    const stored = {
      id: 't1', enabled: true, kind: 'cron', expression: '0 8 * * *', source: 'user',
      label: 'L', short: 'S',
    }
    expect(stripTrigger({ ...stored, runIfMissed: false } as Trigger))
      .toEqual({ id: 't1', enabled: true, kind: 'cron', expression: '0 8 * * *', source: 'user', runIfMissed: false })
    expect(stripTrigger({ ...stored, runIfMissed: true } as Trigger))
      .toEqual({ id: 't1', enabled: true, kind: 'cron', expression: '0 8 * * *', source: 'user' })
    expect(stripTrigger({ ...stored, runIfMissed: true } as Trigger)).not.toHaveProperty('runIfMissed')
    expect(stripTrigger({
      id: 't2', enabled: true, kind: 'time', at: '2026-08-09T09:00:00', runIfMissed: false,
      label: 'L', short: 'S',
    } as Trigger)).toEqual({ id: 't2', enabled: true, kind: 'time', at: '2026-08-09T09:00:00', runIfMissed: false })
  })
  it('optional keys are omitted entirely when absent — no undefined-valued fields', () => {
    // draft entries have no id yet; a cron without a timezone stays timezone-free
    expect(stripTrigger({ enabled: true, kind: 'cron', expression: '0 8 * * *', source: 'user' }))
      .toEqual({ enabled: true, kind: 'cron', expression: '0 8 * * *', source: 'user' })
    // discord: pattern/mention/author serialize only when set; [] counts as absent
    expect(stripTrigger({ enabled: true, kind: 'discord', channel: '123', secret: 'BOT_TOKEN', author: [] }))
      .toEqual({ enabled: true, kind: 'discord', channel: '123', secret: 'BOT_TOKEN' })
    expect(stripTrigger({
      enabled: true, kind: 'discord', channel: '123', secret: 'BOT_TOKEN',
      pattern: 'deploy', mention: true, author: ['alice'],
    })).toEqual({
      enabled: true, kind: 'discord', channel: '123', secret: 'BOT_TOKEN',
      pattern: 'deploy', mention: true, author: ['alice'],
    })
    expect(stripTrigger({ enabled: true, kind: 'imessage', from: '+15550123', pattern: 'go' }))
      .toEqual({ enabled: true, kind: 'imessage', from: '+15550123', pattern: 'go' })
    expect(stripTrigger({ enabled: true, kind: 'imessage', from: '+15550123' }))
      .toEqual({ enabled: true, kind: 'imessage', from: '+15550123' })
  })
})

// ---- §8/§11 grant plumbing: seeds + draft serialization ----
import {
  seedEmpty, seedFromPayload, seedFromAuto, serializeDraft,
} from '../src/pages/CreateFlow'
import type { Agent, Automation, DraftPayload } from '../src/types'

const agent = (id: string, over: Partial<Agent> = {}): Agent => ({
  id, name: id, harness: 'Claude Code', mode: 'default', model: null, ...over,
})
const AGENTS = [agent('g1'), agent('g2', { harness: 'OpenCode', mode: 'ollama', model: 'qwen3:8b' })]
const SECRET_IDS = ['MAIL_PASSWORD', 'CRM_API_KEY'] // §4.1: opaque secret ids to the seeds

describe('grant seeds (§11 Review checkboxes)', () => {
  it('a fresh drafting Rev starts all-on — every agent enabled, every secret allowed', () => {
    const r = seedEmpty(AGENTS, SECRET_IDS)
    expect(r.enabledAgents).toEqual(['g1', 'g2'])
    expect(r.allowedSecrets).toEqual(SECRET_IDS)
  })

  it('a resumed pending draft restores its own grant selections (§4.4)', () => {
    const d = { stepAgents: ['g2'], allowedSecrets: ['CRM_API_KEY'] } as DraftPayload
    const r = seedFromPayload(d, AGENTS, SECRET_IDS)
    expect(r.enabledAgents).toEqual(['g2'])
    expect(r.allowedSecrets).toEqual(['CRM_API_KEY'])
  })

  it('a payload without grant keys defaults to everything (fresh job payloads carry none)', () => {
    const r = seedFromPayload({} as DraftPayload, AGENTS, SECRET_IDS)
    expect(r.enabledAgents).toEqual(['g1', 'g2'])
    expect(r.allowedSecrets).toEqual(SECRET_IDS)
  })

  it('stale grant ids pointing at deleted agents/secrets are filtered out', () => {
    const d = { stepAgents: ['g1', 'gone'], allowedSecrets: ['CRM_API_KEY', 'DELETED_KEY'] } as DraftPayload
    const r = seedFromPayload(d, AGENTS, SECRET_IDS)
    expect(r.enabledAgents).toEqual(['g1'])
    const a = seedFromAuto({
      name: 'A', description: '', spec: [{ kind: 'h1', text: 'T' }], steps: [],
      triggers: [], stepAgents: ['g2', 'gone'], allowedSecrets: ['MAIL_PASSWORD', 'DELETED_KEY'],
      agentId: null, draft: null,
    } as unknown as Automation, AGENTS, SECRET_IDS)
    expect(a.enabledAgents).toEqual(['g2'])
    expect(a.allowedSecrets).toEqual(['MAIL_PASSWORD'])
  })

  it('edit mode prefers the draft snapshot grants over the saved automation ones', () => {
    const a = seedFromAuto({
      name: 'A', description: '', spec: [{ kind: 'h1', text: 'T' }], steps: [],
      triggers: [], stepAgents: ['g1'], allowedSecrets: ['MAIL_PASSWORD'],
      agentId: null,
      draft: { spec: [{ kind: 'h1', text: 'T' }], steps: [], note: '',
               stepAgents: ['g2'], allowedSecrets: ['CRM_API_KEY'] },
    } as unknown as Automation, AGENTS, SECRET_IDS)
    expect(a.enabledAgents).toEqual(['g2'])
    expect(a.allowedSecrets).toEqual(['CRM_API_KEY'])
  })
})

describe('seedFromAuto packages (§4.1 curated deps)', () => {
  it('copies the version packages fresh — editor mutations never leak back', () => {
    const pkgs = [{ pip: 'pandas', import: 'pandas', why: 'tables', version: '2.2' }]
    const r = seedFromAuto({
      name: 'A', description: '', spec: [], steps: [], notes: '',
      params: [], packages: pkgs, triggers: [], stepAgents: [], allowedSecrets: [],
      agentId: null, draft: null,
    } as unknown as Automation, AGENTS, SECRET_IDS)
    expect(r.packages).toEqual(pkgs)
    expect(r.packages[0]).not.toBe(pkgs[0])
  })
})

describe('serializeDraft (§4.4 draft payload)', () => {
  it('maps the editor grant state onto stepAgents/allowedSecrets — unchecked entries gone', () => {
    const r = { ...seedEmpty(AGENTS, SECRET_IDS), enabledAgents: ['g2'], allowedSecrets: [] }
    const d = serializeDraft(r)
    expect(d.stepAgents).toEqual(['g2'])
    expect(d.allowedSecrets).toEqual([])
  })

  it('strips package status fields down to { pip, import } declarations', () => {
    const r = {
      ...seedEmpty(AGENTS, SECRET_IDS),
      packages: [{ pip: 'pandas', import: 'pandas', status: 'installed', version: '2.2' }],
    } as ReturnType<typeof seedEmpty>
    expect(serializeDraft(r).packages).toEqual([{ pip: 'pandas', import: 'pandas' }])
  })

  it('a stored draft carrying the retired `instructions` key still seeds, and never re-serializes it', () => {
    const legacy = {
      spec: [{ kind: 'h1', text: 'T' }], steps: [], instructions: '- an old per-automation rule',
    } as unknown as DraftPayload
    const r = seedFromPayload(legacy, AGENTS, SECRET_IDS)
    expect(r).not.toHaveProperty('instructions')
    expect(serializeDraft(r)).not.toHaveProperty('instructions')
  })

  it('never carries the thread — chat persists via /chat/{owner} (§4.4 thread lifetime)', () => {
    const r = {
      ...seedEmpty(AGENTS, SECRET_IDS),
      chat: [entry('user'), entry('error'), entry('answer')],
    }
    expect(serializeDraft(r)).not.toHaveProperty('chat')
  })

  it('chatSinceBoundary clips at the newest boundary marker (§4.4/§8)', () => {
    const marker = { ...entry('system'), boundary: true }
    const tail = entry('user')
    expect(chatSinceBoundary([entry('user'), marker, entry('answer'), marker, tail])).toEqual([tail])
    // no marker → the whole thread travels
    const all = [entry('user'), entry('answer')]
    expect(chatSinceBoundary(all)).toEqual(all)
    // marker last → nothing travels: the next session's agent starts clean
    expect(chatSinceBoundary([entry('user'), marker])).toEqual([])
  })

  it('round-trips the drafted §8 test-value map as testValues (§4.4 draft-only key)', () => {
    const r = { ...seedEmpty(AGENTS, SECRET_IDS), testValues: { city: 'Bergen' } }
    expect(serializeDraft(r).testValues).toEqual({ city: 'Bergen' })
    // absent / empty maps drop the key
    expect('testValues' in serializeDraft(seedEmpty(AGENTS, SECRET_IDS))).toBe(false)
    expect('testValues' in serializeDraft({ ...seedEmpty(AGENTS, SECRET_IDS), testValues: {} })).toBe(false)
    // both resume paths restore it
    expect(seedFromPayload({ testValues: { city: 'Bergen' } } as unknown as DraftPayload, AGENTS, SECRET_IDS).testValues)
      .toEqual({ city: 'Bergen' })
    expect(seedFromPayload({} as DraftPayload, AGENTS, SECRET_IDS).testValues).toBeNull()
    const a = seedFromAuto({
      name: 'A', description: '', spec: [{ kind: 'h1', text: 'T' }], steps: [],
      triggers: [], stepAgents: ['g1'], allowedSecrets: [],
      agentId: null,
      draft: { spec: [{ kind: 'h1', text: 'T' }], steps: [], note: '', testValues: { city: 'Bergen' } },
    } as unknown as Automation, AGENTS, SECRET_IDS)
    expect(a.testValues).toEqual({ city: 'Bergen' })
  })

  it('round-trips the §8 staged concurrency object (§4.4 draft-only key)', () => {
    const r = { ...seedEmpty(AGENTS, SECRET_IDS), concurrency: { maxParallel: 2 } }
    expect(serializeDraft(r).concurrency).toEqual({ maxParallel: 2 })
    // nothing staged drops the key
    expect('concurrency' in serializeDraft(seedEmpty(AGENTS, SECRET_IDS))).toBe(false)
    // both resume paths restore it
    expect(seedFromPayload({ concurrency: { maxQueued: 5 } } as unknown as DraftPayload, AGENTS, SECRET_IDS).concurrency)
      .toEqual({ maxQueued: 5 })
    expect(seedFromPayload({} as DraftPayload, AGENTS, SECRET_IDS).concurrency).toBeNull()
    const a = seedFromAuto({
      name: 'A', description: '', spec: [{ kind: 'h1', text: 'T' }], steps: [],
      triggers: [], stepAgents: ['g1'], allowedSecrets: [],
      agentId: null,
      draft: { spec: [{ kind: 'h1', text: 'T' }], steps: [], note: '', concurrency: { maxParallel: 3, maxQueued: 1 } },
    } as unknown as Automation, AGENTS, SECRET_IDS)
    expect(a.concurrency).toEqual({ maxParallel: 3, maxQueued: 1 })
  })

  it('round-trips the §11 dirty gate as outOfSync — resume must not unlock Save (§4.4)', () => {
    const dirty = { ...seedEmpty(AGENTS, SECRET_IDS), dirty: true }
    expect(serializeDraft(dirty).outOfSync).toBe(true)
    expect('outOfSync' in serializeDraft(seedEmpty(AGENTS, SECRET_IDS))).toBe(false)
    // both resume paths restore it
    expect(seedFromPayload({ outOfSync: true } as DraftPayload, AGENTS, SECRET_IDS).dirty).toBe(true)
    expect(seedFromPayload({} as DraftPayload, AGENTS, SECRET_IDS).dirty).toBe(false)
    const a = seedFromAuto({
      name: 'A', description: '', spec: [{ kind: 'h1', text: 'T' }], steps: [],
      triggers: [], stepAgents: ['g1'], allowedSecrets: [],
      agentId: null,
      draft: { spec: [{ kind: 'h1', text: 'T' }], steps: [], note: '', outOfSync: true },
    } as unknown as Automation, AGENTS, SECRET_IDS)
    expect(a.dirty).toBe(true)
  })
})

// ---- §11 thread persistence (§4.4 `chat` → §5 chat.jsonl) ----
const entry = (kind: ChatEntry['kind']): ChatEntry => ({ id: kind, kind, text: kind })

describe('persistChat', () => {
  it('keeps every §4.4 kind in order — error entries persist so a later chat can name the failure', () => {
    const chat = (['user', 'answer', 'activity', 'error', 'rewrite', 'blockers', 'system'] as const).map(entry)
    expect(persistChat(chat).map((e) => e.kind))
      .toEqual(['user', 'answer', 'activity', 'error', 'rewrite', 'blockers', 'system'])
  })
  it('drops entries outside the §4.4 kinds', () => {
    expect(persistChat([{ id: 'x', kind: 'progress' as ChatEntry['kind'], text: 'x' }])).toEqual([])
  })
})

// ---- §11 chat-armed test values (§8 actions.yaml test_values) ----
describe('applyTestValues', () => {
  const P = (over: Partial<ParamDef>): ParamDef =>
    ({ name: 'p', kind: 'text', label: '', help: '', ...over })

  it('leaves params whose name is absent from the values untouched', () => {
    const p = P({ name: 'other', value: 'keep' })
    expect(applyTestValues([p], { p: 'x' })).toEqual([p])
  })
  it('toggle coerces any yaml value through truthiness', () => {
    const ps = applyTestValues([P({ kind: 'toggle' })], { p: 1 })
    expect(ps[0].on).toBe(true)
    expect(applyTestValues([P({ kind: 'toggle', on: true })], { p: 0 })[0].on).toBe(false)
  })
  it('list stringifies array items and wraps a scalar into one line', () => {
    expect(applyTestValues([P({ kind: 'list' })], { p: [1, 'b'] })[0].lines).toEqual(['1', 'b'])
    expect(applyTestValues([P({ kind: 'list' })], { p: 'solo' })[0].lines).toEqual(['solo'])
  })
  it('kv takes row arrays as-is, maps objects to rows, ignores scalars', () => {
    const rows = [{ key: 'a', value: '1' }]
    expect(applyTestValues([P({ kind: 'kv' })], { p: rows })[0].rows).toEqual(rows)
    expect(applyTestValues([P({ kind: 'kv' })], { p: { a: 1 } })[0].rows).toEqual([{ key: 'a', value: '1' }])
    const scalar = P({ kind: 'kv', rows: [] })
    expect(applyTestValues([scalar], { p: 'nope' })[0]).toEqual(scalar)
  })
  it('number keeps numbers, parses numeric strings, falls back to min then 0', () => {
    expect(applyTestValues([P({ kind: 'number' })], { p: 7 })[0].value).toBe(7)
    expect(applyTestValues([P({ kind: 'number' })], { p: '8' })[0].value).toBe(8)
    expect(applyTestValues([P({ kind: 'number', min: 3 })], { p: 'x' })[0].value).toBe(3)
    expect(applyTestValues([P({ kind: 'number' })], { p: 'x' })[0].value).toBe(0)
  })
  it('text stringifies whatever the yaml carried', () => {
    expect(applyTestValues([P({})], { p: 42 })[0].value).toBe('42')
  })
})

describe('sameTriggerList — the §11/§19 re-attach trigger guard', () => {
  const cron = (expression: string, extra: Record<string, unknown> = {}) =>
    ({ kind: 'cron', expression, enabled: true, ...extra })
  it('matches identical lists entry for entry, key order irrelevant', async () => {
    const { sameTriggerList } = await import('../src/pages/createflow/model')
    expect(sameTriggerList([], [])).toBe(true)
    expect(sameTriggerList([cron('0 8 * * *')], [{ enabled: true, expression: '0 8 * * *', kind: 'cron' }])).toBe(true)
  })
  it('any difference — length, fields, order, enabled — reads as changed', async () => {
    const { sameTriggerList } = await import('../src/pages/createflow/model')
    expect(sameTriggerList([cron('0 8 * * *')], [])).toBe(false)
    expect(sameTriggerList([cron('0 8 * * *')], [cron('0 9 * * *')])).toBe(false)
    expect(sameTriggerList([cron('0 8 * * *')], [cron('0 8 * * *', { enabled: false })])).toBe(false)
    expect(sameTriggerList(
      [cron('0 8 * * *'), cron('0 9 * * *')],
      [cron('0 9 * * *'), cron('0 8 * * *')],
    )).toBe(false)
    expect(sameTriggerList(
      [{ kind: 'discord', channel: '1', secret: 's1', enabled: true }],
      [{ kind: 'discord', channel: '2', secret: 's1', enabled: true }],
    )).toBe(false)
    // §4.3 interval: the `every` duration is part of the projection
    expect(sameTriggerList(
      [{ kind: 'interval', every: 'PT6H', enabled: true }],
      [{ kind: 'interval', every: 'P1D', enabled: true }],
    )).toBe(false)
  })
  it('§4.3 runIfMissed false and the absent default are different lists', async () => {
    const { sameTriggerList } = await import('../src/pages/createflow/model')
    expect(sameTriggerList([cron('0 8 * * *')], [cron('0 8 * * *', { runIfMissed: false })])).toBe(false)
    expect(sameTriggerList([cron('0 8 * * *', { runIfMissed: true })], [cron('0 8 * * *')])).toBe(true)
    expect(sameTriggerList(
      [cron('0 8 * * *', { runIfMissed: false })],
      [cron('0 8 * * *', { runIfMissed: false })],
    )).toBe(true)
  })
  it('non-arrays compare as empty lists (a missing echo only drops ops, never misapplies)', async () => {
    const { sameTriggerList } = await import('../src/pages/createflow/model')
    expect(sameTriggerList(null, [])).toBe(true)
    expect(sameTriggerList(null, [cron('0 8 * * *')])).toBe(false)
  })
})

describe('docLineCount / docModalFrame — the §11 document-editor toolbar count and frame', () => {
  it('counts lines with an empty editor at zero and a trailing newline uncounted', () => {
    expect(docLineCount('')).toBe(0)
    expect(docLineCount('a')).toBe(1)
    expect(docLineCount('a\nb\n')).toBe(2)
    expect(docLineCount('a\nb\nc')).toBe(3)
  })

  it('sizes the card from the opened text: 44 + 36 + 34 + (lines + 6) * 19.8, floored and capped', () => {
    // an empty document still gets one line of room
    expect(docModalFrame('')).toBe('clamp(440px, 253px, 82vh)')
    expect(docModalFrame('a\nb\nc')).toBe('clamp(440px, 293px, 82vh)')
    // a long document runs past the floor — 82vh is the only thing holding it
    const long = Array.from({ length: 200 }, (_, i) => `line ${i}`).join('\n')
    expect(docModalFrame(long)).toBe('clamp(440px, 4193px, 82vh)')
  })
})

// ---- §11 stale-outcome rule: the steps fingerprint ----
import { stepsFingerprint } from '../src/pages/createflow/model'

describe('stepsFingerprint (§11 stale-outcome rule)', () => {
  const steps = [
    step({ file: '01-a.py', name: 'Fetch', code: 'log("a")' }),
    step({ file: '02-b.py', name: 'Send', code: 'log("b")' }),
  ]

  it('an empty step list is the bare FNV-1a offset basis', () => {
    expect(stepsFingerprint([])).toBe('0:811c9dc5')
  })

  it('the same steps always answer the same "<count>:<8 hex>" string', () => {
    expect(stepsFingerprint(steps)).toBe(stepsFingerprint(steps.map((s) => ({ ...s }))))
    expect(stepsFingerprint(steps)).toMatch(/^\d+:[0-9a-f]{8}$/)
    expect(stepsFingerprint([])).toMatch(/^\d+:[0-9a-f]{8}$/)
  })

  it('changed code, a renamed file, or a reorder each change it', () => {
    const base = stepsFingerprint(steps)
    expect(stepsFingerprint([steps[0], { ...steps[1], code: 'log("c")' }])).not.toBe(base)
    expect(stepsFingerprint([{ ...steps[0], file: '01-renamed.py' }, steps[1]])).not.toBe(base)
    expect(stepsFingerprint([steps[1], steps[0]])).not.toBe(base)
  })
})
