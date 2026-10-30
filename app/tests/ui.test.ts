// Unit tests for the pure helpers in src/ui.tsx. No component rendering:
// highlightPython is exercised directly — it returns the token nodes PyCode
// memoizes into its <pre>.
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  nextIn, paramSummary, validUrl, badgeOf, resultChipColors, P, highlightPython, highlightPythonLines,
  executingToast, stepTimeoutLabel, stepRetriesLabel, stepRetriesTitle, waitedLabel, durationLabel, dispModel, agName, logColor,
  parseStepTimeout, stepTimeoutKey, stepTimeoutText, STEP_TIMEOUT_DEFAULT_LABEL,
  parseStepRetries, stepRetriesKey, stepRetriesText, STEP_RETRIES_HINT, STEP_RETRIES_MAX,
} from '../src/ui'
import type { ParamDef, Step } from '../src/types'

describe('nextIn', () => {
  afterEach(() => vi.useRealTimers())
  const now = new Date('2026-07-20T00:00:00Z').getTime()
  const pin = () => { vi.useFakeTimers(); vi.setSystemTime(now) }

  it('null nextAtMs → empty string', () => {
    expect(nextIn({ nextAtMs: null })).toBe('')
  })
  it('past nextAtMs clamps to one minute', () => {
    pin()
    expect(nextIn({ nextAtMs: now - 5 * 60000 })).toBe('0h 1m')
  })
  it('exact day boundary → "Xd Xh" form', () => {
    pin()
    expect(nextIn({ nextAtMs: now + 1440 * 60000 })).toBe('1d 0h')
    expect(nextIn({ nextAtMs: now + 25 * 3600000 })).toBe('1d 1h')
  })
  it('sub-day → "Xh Xm" form', () => {
    pin()
    expect(nextIn({ nextAtMs: now + 90 * 60000 })).toBe('1h 30m')
    expect(nextIn({ nextAtMs: now + 60000 })).toBe('0h 1m')
  })
})

describe('paramSummary', () => {
  const p = (over: Partial<ParamDef>): ParamDef =>
    ({ name: 'p', kind: 'text', label: 'P', help: '', ...over } as ParamDef)

  it('toggle: on beats default; default true fills in', () => {
    expect(paramSummary(p({ kind: 'toggle', on: true }))).toBe('On')
    expect(paramSummary(p({ kind: 'toggle', on: false, default: true }))).toBe('Off')
    expect(paramSummary(p({ kind: 'toggle', default: true }))).toBe('On')
    expect(paramSummary(p({ kind: 'toggle', default: false }))).toBe('Off')
  })
  it('list with validate counts valid URLs only', () => {
    // "1 links" is spec-pinned copy — §4.2 parameter-kinds table: validate →
    // "N links" (always the plural template), so no singular special case.
    expect(paramSummary(p({ kind: 'list', validate: true, lines: ['http://a.com', 'nope', '', '  '] }))).toBe('1 links')
  })
  it('list without validate counts non-empty entries', () => {
    expect(paramSummary(p({ kind: 'list', lines: ['a', '', '  ', 'b'] }))).toBe('2 entries')
  })
  it('list falls back to array default', () => {
    expect(paramSummary(p({ kind: 'list', default: ['a', 'b'] }))).toBe('2 entries')
  })
  it('kv counts rows, with default fallback', () => {
    expect(paramSummary(p({ kind: 'kv', rows: [{ key: 'a', value: '1' }, { key: 'b', value: '2' }, { key: 'c', value: '3' }] }))).toBe('3 entries')
    expect(paramSummary(p({ kind: 'kv', default: [{ key: 'a', value: '1' }] }))).toBe('1 entries')
    expect(paramSummary(p({ kind: 'kv' }))).toBe('0 entries')
  })
  it('number: value → default → min → 0 chain', () => {
    expect(paramSummary(p({ kind: 'number', value: 5, default: 3, min: 1 }))).toBe('5')
    expect(paramSummary(p({ kind: 'number', default: 3, min: 1 }))).toBe('3')
    expect(paramSummary(p({ kind: 'number', min: 2 }))).toBe('2')
    expect(paramSummary(p({ kind: 'number' }))).toBe('0')
  })
  it('text: value → default → "Not set"', () => {
    expect(paramSummary(p({ kind: 'text', value: 'x', default: 'd' }))).toBe('x')
    expect(paramSummary(p({ kind: 'text', default: 'd' }))).toBe('d')
    expect(paramSummary(p({ kind: 'text' }))).toBe('Not set')
    expect(paramSummary(p({ kind: 'text', value: '' }))).toBe('Not set')
  })
})

describe('validUrl', () => {
  it('accepts http and https with a dot', () => {
    expect(validUrl('http://a.com')).toBe(true)
    expect(validUrl('https://sub.example.io/path?x=1')).toBe(true)
    expect(validUrl('  https://a.com  ')).toBe(true)
  })
  it('rejects no scheme and no dot', () => {
    expect(validUrl('a.com')).toBe(false)
    expect(validUrl('ftp://a.com')).toBe(false)
    expect(validUrl('http://localhost')).toBe(false)
    expect(validUrl('')).toBe(false)
  })
})

describe('badgeOf', () => {
  it('maps every known status', () => {
    expect(badgeOf('queued')).toEqual({ label: 'Queued', c: P.gray, bg: P.grayBg })
    expect(badgeOf('executing')).toEqual({ label: 'Executing', c: P.cyan, bg: P.cyanBg })
    expect(badgeOf('succeeded')).toEqual({ label: 'Succeeded', c: P.green, bg: P.greenBg })
    expect(badgeOf('failed')).toEqual({ label: 'Failed', c: P.red, bg: P.redBg })
    expect(badgeOf('cancelled')).toEqual({ label: 'Cancelled', c: P.gray, bg: P.grayBg })
    expect(badgeOf('skipped')).toEqual({ label: 'Skipped', c: P.gray, bg: P.grayBg })
    expect(badgeOf('interrupted')).toEqual({ label: 'Interrupted', c: P.magenta, bg: P.magentaBg })
    expect(badgeOf('none')).toEqual({ label: 'Not executed yet', c: P.gray, bg: P.grayBg })
  })
  it('unknown status falls back to `none`', () => {
    expect(badgeOf('totally-unknown')).toEqual({ label: 'Not executed yet', c: P.gray, bg: P.grayBg })
  })
})

describe('resultChipColors', () => {
  it('changes → accent, attention → orange, everything else → green', () => {
    expect(resultChipColors('changes')).toEqual({ c: P.accent, bg: P.accentBg })
    expect(resultChipColors('attention')).toEqual({ c: P.orange, bg: P.orangeBg })
    expect(resultChipColors('ok')).toEqual({ c: P.green, bg: P.greenBg })
    expect(resultChipColors(null)).toEqual({ c: P.green, bg: P.greenBg })
    expect(resultChipColors(undefined)).toEqual({ c: P.green, bg: P.greenBg })
  })
})

describe('executingToast (§7 409 no-free-slot copy)', () => {
  it('the default config (1 slot, no queue) keeps the original copy verbatim', () => {
    expect(executingToast(1, 0))
      .toBe('Already executing — one execution at a time. A trigger firing now would be skipped.')
    expect(executingToast(0, 0)).toBe(executingToast(1, 0)) // <=1 and <=0 bounds
  })
  it('a queue says a firing would be queued', () => {
    expect(executingToast(1, 2)).toBe('The slot is busy. A trigger firing now would be queued.')
    expect(executingToast(3, 1)).toBe('All 3 slots are busy. A trigger firing now would be queued.')
  })
  it('multiple slots without a queue says skipped', () => {
    expect(executingToast(2, 0)).toBe('All 2 slots are busy. A trigger firing now would be skipped.')
  })
})

describe('parseStepTimeout / stepTimeoutText (§11 timeout field)', () => {
  it('blank is the default, words are no limit, numbers take s/m/h suffixes', () => {
    expect(parseStepTimeout('')).toEqual({})
    expect(parseStepTimeout('  ')).toEqual({})
    for (const t of ['no limit', 'None', ' UNLIMITED ', 'No  Limit']) expect(parseStepTimeout(t)).toEqual({ noTimeout: true })
    expect(parseStepTimeout('900')).toEqual({ timeout: 900 })
    expect(parseStepTimeout('90s')).toEqual({ timeout: 90 })
    expect(parseStepTimeout('15 min')).toEqual({ timeout: 900 })
    expect(parseStepTimeout('2H')).toEqual({ timeout: 7200 })
    expect(parseStepTimeout('3 hrs')).toEqual({ timeout: 10800 })
  })
  it('rejects zero, fractions, compound and spelled-out durations', () => {
    for (const t of ['0', '0m', '1.5m', '1h30m', '30 minutes', '-5', 'abc', '5 d']) expect(parseStepTimeout(t)).toBeNull()
  })
  it('the key equates 900 with 15m and treats invalid text as its own value', () => {
    expect(stepTimeoutKey('900')).toBe(stepTimeoutKey('15m'))
    expect(stepTimeoutKey('')).toBe('default')
    expect(stepTimeoutKey('none')).toBe('none')
    expect(stepTimeoutKey('1.5m')).toBe('invalid')
  })
  it('the field text is the clock label when set, empty when the step sets neither', () => {
    expect(stepTimeoutText({ name: 'a', timeout: 7200 } as Step)).toBe('2h')
    expect(stepTimeoutText({ name: 'a', noTimeout: true } as Step)).toBe('no limit')
    expect(stepTimeoutText({ name: 'a' } as Step)).toBe('')
    expect(STEP_TIMEOUT_DEFAULT_LABEL).toBe('15m')
  })
})

describe('parseStepRetries / stepRetriesText (§11 retries field)', () => {
  it('blank or 0 is the default, words are infinite, whole numbers 1–10 count retries', () => {
    expect(parseStepRetries('')).toEqual({})
    expect(parseStepRetries('  ')).toEqual({})
    expect(parseStepRetries('0')).toEqual({})
    for (const t of ['infinite', 'Unlimited', ' FOREVER ', 'no  limit', 'infinite retries', 'Unlimited Retries']) expect(parseStepRetries(t)).toEqual({ infiniteRetries: true })
    expect(parseStepRetries('1')).toEqual({ retries: 1 })
    expect(parseStepRetries('1 retry')).toEqual({ retries: 1 })
    expect(parseStepRetries('3 retries')).toEqual({ retries: 3 })
    expect(parseStepRetries(' 3  Retries ')).toEqual({ retries: 3 })
    expect(parseStepRetries('10')).toEqual({ retries: STEP_RETRIES_MAX })
  })
  it('rejects over the cap, fractions, negatives and other words', () => {
    for (const t of ['11', '2.5', '-1', 'many', '3 times', 'infinite retry', 'retries']) expect(parseStepRetries(t)).toBeNull()
    expect(STEP_RETRIES_MAX).toBe(10)
    expect(STEP_RETRIES_HINT).toBe('Use a whole number up to 10, or infinite')
  })
  it('the key equates 3 with 3 retries, blank with 0, and treats invalid text as its own value', () => {
    expect(stepRetriesKey('3')).toBe(stepRetriesKey('3 retries'))
    expect(stepRetriesKey('3')).toBe('3')
    expect(stepRetriesKey('')).toBe(stepRetriesKey('0'))
    expect(stepRetriesKey('')).toBe('default')
    expect(stepRetriesKey('forever')).toBe('infinite')
    expect(stepRetriesKey('11')).toBe('invalid')
  })
  it('the field text is the bare number or infinite when set, empty when the step sets neither', () => {
    expect(stepRetriesText({ name: 'a', retries: 3 } as Step)).toBe('3')
    expect(stepRetriesText({ name: 'a', infiniteRetries: true } as Step)).toBe('infinite')
    expect(stepRetriesText({ name: 'a', infiniteRetries: true, retries: 2 } as Step)).toBe('infinite')
    expect(stepRetriesText({ name: 'a', retries: 0 } as Step)).toBe('')
    expect(stepRetriesText({ name: 'a' } as Step)).toBe('')
  })
})

describe('stepTimeoutLabel (§9.2 clock tag)', () => {
  const s = (over: Partial<Step> = {}): Step => ({ name: 's', description: '', code: '', ...over } as Step)

  it('noTimeout → "no limit"', () => {
    expect(stepTimeoutLabel(s({ noTimeout: true }))).toBe('no limit')
    expect(stepTimeoutLabel(s({ noTimeout: true, timeout: 60 }))).toBe('no limit')
  })
  it('3600 / 60 / raw-seconds ladder', () => {
    expect(stepTimeoutLabel(s({ timeout: 7200 }))).toBe('2h')
    expect(stepTimeoutLabel(s({ timeout: 3600 }))).toBe('1h')
    expect(stepTimeoutLabel(s({ timeout: 300 }))).toBe('5m')
    expect(stepTimeoutLabel(s({ timeout: 90 }))).toBe('90s')   // not divisible → seconds
    expect(stepTimeoutLabel(s({ timeout: 45 }))).toBe('45s')
  })
  it('absent timeout falls back to the 900s engine default', () => {
    expect(stepTimeoutLabel(s())).toBe('15m')
  })
})

describe('stepRetriesLabel / stepRetriesTitle (§9.2 retry tag)', () => {
  const s = (over: Partial<Step> = {}): Step => ({ name: 's', description: '', code: '', ...over } as Step)

  it('absent or zero budget → no tag', () => {
    expect(stepRetriesLabel(s())).toBeNull()
    expect(stepRetriesLabel(s({ retries: 0 }))).toBeNull()
  })
  it('finite budget pluralizes', () => {
    expect(stepRetriesLabel(s({ retries: 1 }))).toBe('1 retry')
    expect(stepRetriesLabel(s({ retries: 5 }))).toBe('5 retries')
    expect(stepRetriesTitle(s({ retries: 1 }))).toBe('If this step fails it runs again, up to 1 more time')
    expect(stepRetriesTitle(s({ retries: 5 }))).toBe('If this step fails it runs again, up to 5 more times')
  })
  it('infiniteRetries wins', () => {
    expect(stepRetriesLabel(s({ infiniteRetries: true }))).toBe('infinite retries')
    expect(stepRetriesTitle(s({ infiniteRetries: true })))
      .toBe('If this step fails it runs again until it succeeds, or you cancel or skip it')
  })
})

describe('waitedLabel (§7 QUEUED FOR column)', () => {
  it('whole seconds under a minute, "Xm Xs" from 60s up', () => {
    expect(waitedLabel(59_000)).toBe('59s')
    expect(waitedLabel(60_000)).toBe('1m 0s')  // the boundary
    expect(waitedLabel(125_000)).toBe('2m 5s')
  })
  it('rounds to whole seconds and clamps negatives to 0s', () => {
    expect(waitedLabel(59_400)).toBe('59s')
    expect(waitedLabel(59_600)).toBe('1m 0s')  // rounds up across the boundary
    expect(waitedLabel(-5_000)).toBe('0s')
  })
})

describe('durationLabel (§11 per-step durations)', () => {
  it('tenths of a second under a minute, "Xm Xs" from 60s up', () => {
    expect(durationLabel(400)).toBe('0.4s')
    expect(durationLabel(1_400)).toBe('1.4s')
    expect(durationLabel(60_000)).toBe('1m 0s')  // the boundary
    expect(durationLabel(133_000)).toBe('2m 13s')
  })
  it('clamps negatives to 0.0s', () => {
    expect(durationLabel(-5_000)).toBe('0.0s')
  })
})

describe('dispModel / agName (§4.7 agent display)', () => {
  it('null model reads as the harness default', () => {
    expect(dispModel({ model: null })).toBe('Default model')
    expect(dispModel({ model: 'qwen3:8b' })).toBe('qwen3:8b')
  })
  it('empty-string and null names fall back to the harness', () => {
    expect(agName({ name: 'Cloud writer', harness: 'Claude Code' })).toBe('Cloud writer')
    expect(agName({ name: '', harness: 'Claude Code' })).toBe('Claude Code')
    expect(agName({ name: null, harness: 'OpenCode' })).toBe('OpenCode')
  })
})

describe('logColor (§7 log views)', () => {
  it('one color per kind, default for out/unknown', () => {
    expect(logColor('sys')).toBe('var(--text-faint)')
    expect(logColor('wrn')).toBe('var(--amber)')
    expect(logColor('err')).toBe('var(--red)')
    expect(logColor('out')).toBe('var(--text-2)')
    expect(logColor('anything-else')).toBe('var(--text-2)')
  })
})

// ---------- highlightPython ----------

const COLOR = {
  keyword: 'var(--syn-keyword)', const: 'var(--syn-const)', string: 'var(--syn-string)',
  number: 'var(--syn-const)', comment: 'var(--syn-comment)', builtin: 'var(--syn-builtin)',
  call: 'var(--syn-builtin)', def: 'var(--syn-def)', decorator: 'var(--syn-def)',
}

interface Tok { text: string; color?: string; italic?: boolean }
function tokens(code: string): Tok[] {
  return highlightPython(code).map((n) => {
    if (typeof n === 'string') return { text: n }
    const e = n as React.ReactElement<{ style?: React.CSSProperties; children?: React.ReactNode }>
    const style = e.props.style ?? {}
    return { text: String(e.props.children), color: style.color, italic: style.fontStyle === 'italic' }
  })
}
const tok = (ts: Tok[], text: string) => ts.find((t) => t.text === text)

describe('highlightPythonLines', () => {
  const lineToks = (code: string) => highlightPythonLines(code).map((ln) => ln.map((n) => {
    if (typeof n === 'string') return { text: n }
    const e = n as React.ReactElement<{ style?: React.CSSProperties; children?: React.ReactNode }>
    return { text: String(e.props.children), color: e.props.style?.color }
  }))

  it('splits the stream at newlines, reopening a multi-line docstring token per line in the string color', () => {
    const lines = lineToks('"""Doc.\n\nMore."""\nx = 1')
    expect(lines).toHaveLength(4)
    expect(lines[0]).toEqual([{ text: '"""Doc.', color: COLOR.string }])
    expect(lines[1]).toEqual([]) // the blank docstring line carries no node
    expect(lines[2]).toEqual([{ text: 'More."""', color: COLOR.string }])
    expect(lines[3].map((t) => t.text).join('')).toBe('x = 1')
    expect(lines[3][0]).toEqual({ text: 'x' })
    expect(lines[3].find((t) => t.text === '1')?.color).toBe(COLOR.number)
  })

  it('keeps an empty script and trailing blank lines as rows', () => {
    expect(lineToks('')).toEqual([[]])
    expect(lineToks('a\n\n')).toHaveLength(3)
  })
})

describe('highlightPython', () => {
  const code = [
    '@app.route',
    'def foo(x):',
    '    # note',
    "    return bar(1.5) + f\"hi {x}\" and print(len('a')) or True",
  ].join('\n')

  it('classifies keywords, def names, calls, builtins, strings, numbers, comments, decorators', () => {
    const ts = tokens(code)
    expect(tok(ts, '@app.route')?.color).toBe(COLOR.decorator)
    expect(tok(ts, 'def')?.color).toBe(COLOR.keyword)
    expect(tok(ts, 'return')?.color).toBe(COLOR.keyword)
    expect(tok(ts, 'and')?.color).toBe(COLOR.keyword)
    expect(tok(ts, 'or')?.color).toBe(COLOR.keyword)
    expect(tok(ts, 'foo')?.color).toBe(COLOR.def)          // def-name beats call lookahead
    expect(tok(ts, 'bar')?.color).toBe(COLOR.call)         // followed by "("
    expect(tok(ts, 'print')?.color).toBe(COLOR.builtin)
    expect(tok(ts, 'len')?.color).toBe(COLOR.builtin)
    expect(tok(ts, '1.5')?.color).toBe(COLOR.number)
    expect(tok(ts, 'f"hi {x}"')?.color).toBe(COLOR.string) // f-string is one string token
    expect(tok(ts, "'a'")?.color).toBe(COLOR.string)
    expect(tok(ts, 'True')?.color).toBe(COLOR.const)
    const comment = tok(ts, '# note')
    expect(comment?.color).toBe(COLOR.comment)
    expect(comment?.italic).toBe(true)
    // plain identifier: parameter x has no color (plain string node)
    expect(tok(ts, 'x')?.color).toBeUndefined()
  })

  it('call lookahead requires the very next char to be "("', () => {
    const ts = tokens('foo (1)')
    expect(tok(ts, 'foo')?.color).toBeUndefined()
  })

  it('unterminated string does not throw and still colors as string', () => {
    let ts: Tok[] = []
    expect(() => { ts = tokens('x = "abc') }).not.toThrow()
    expect(tok(ts, '"abc')?.color).toBe(COLOR.string)
  })

  it('triple-quoted string spans newlines as one token', () => {
    const ts = tokens("s = '''a\nb'''")
    expect(tok(ts, "'''a\nb'''")?.color).toBe(COLOR.string)
  })
})
