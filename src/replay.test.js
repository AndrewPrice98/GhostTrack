import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  flaggedAt, fmtThreshold, frameAircraft, humanKey, parseInline, parseMarkdown, relTime, replayEvents, replayExtent,
  replayTrails, replayWindow,
} from './replay.js'

const ac = (icao, extra = {}) => ({ icao, callsign: null, lat: 33.5, lon: -118.5, alt: 30000, gs: 400, track: 90, injected: false, ...extra })
const frames = [
  { t: 0, aircraft: [] },
  { t: 5, aircraft: [] },
  { t: 10, aircraft: [] },
  { t: 15, aircraft: [] },
  { t: 20, aircraft: [ac('adf001', { injected: true })] },
  { t: 25, aircraft: [ac('adf001', { injected: true, lat: 33.6 }), ac('abc123')] },
  { t: 30, aircraft: [] },
  { t: 35, aircraft: [] },
  { t: 40, aircraft: [] },
  { t: 45, aircraft: [] },
  { t: 50, aircraft: [] },
]

test('relTime shows seconds relative to the injection', () => {
  assert.equal(relTime(135, 120), 'T+15 s')
  assert.equal(relTime(100, 120), 'T−20 s')
  assert.equal(relTime(120, 120), 'T+0 s')
  assert.equal(relTime(NaN, 120), '')
})

test('replayWindow trims empty lead-in and tail but keeps a margin', () => {
  assert.deepEqual(replayWindow(frames, 2), { start: 2, end: 7 })
  assert.deepEqual(replayWindow(frames, 0), { start: 4, end: 5 })
  assert.deepEqual(replayWindow(frames, 50), { start: 0, end: 10 })
  assert.deepEqual(replayWindow([{ t: 0, aircraft: [] }, { t: 1, aircraft: [] }]), { start: 0, end: 1 })
  assert.deepEqual(replayWindow([]), { start: 0, end: 0 })
})

test('replayEvents fires the expected detection at the validated time to detect', () => {
  const events = [
    { t: 140, detection_id: 'GT-004', icao: 'x' },
    { t: 120, detection_id: 'GT-001', icao: 'x' },
  ]
  const out = replayEvents(events, { injectT: 120, expected: ['GT-001'], timeToDetect: 15 })
  assert.deepEqual(out.map((e) => [e.detection_id, e.fireT]), [['GT-001', 135], ['GT-004', 140]])
  assert.deepEqual(out.map((e) => e.derived), [true, false]) // only the validated time is marked
  // without a validated time, or for an unexpected detection, the first flagged report is used
  assert.equal(replayEvents(events, { injectT: 120, expected: ['GT-001'] })[0].fireT, 120)
  // never earlier than the event itself
  assert.equal(replayEvents([{ t: 200, detection_id: 'GT-001' }], { injectT: 120, expected: ['GT-001'], timeToDetect: 15 })[0].fireT, 200)
  assert.deepEqual(replayEvents(undefined), [])
})

test('flaggedAt flags an event address, or every member for a fleet event, once it has fired', () => {
  const events = [{ fireT: 10, icao: 'a' }, { fireT: 20, icao: null }]
  assert.deepEqual([...flaggedAt(events, 5, ['m1', 'm2'])], [])
  assert.deepEqual([...flaggedAt(events, 10, ['m1', 'm2'])], ['a'])
  assert.deepEqual([...flaggedAt(events, 20, ['m1', 'm2'])].sort(), ['a', 'm1', 'm2'])
})

test('frameAircraft maps a frame to map aircraft and marks the flagged ones', () => {
  const out = frameAircraft(frames[5], new Set(['adf001']))
  assert.equal(out.length, 2)
  const g = out.find((a) => a.icao === 'adf001')
  assert.equal(g.flagged, true)
  assert.equal(g.injected, true)
  assert.equal(g.source, 'adsb')
  assert.equal(g.on_ground, false)
  assert.equal(out.find((a) => a.icao === 'abc123').flagged, false)
  assert.deepEqual(frameAircraft(undefined, new Set()), [])
})

test('replayTrails accumulates positions per aircraft up to the current frame', () => {
  const tr = replayTrails(frames, 2, 5)
  assert.deepEqual(tr.adf001.map((p) => p.ts), [20, 25])
  assert.equal(tr.abc123.length, 1)
  assert.deepEqual(replayTrails(frames, 2, 3), {})
})

test('replayExtent collects the scenario aircraft over the window', () => {
  assert.equal(replayExtent(frames, 0, 10, ['adf001']).length, 2)
  assert.equal(replayExtent(frames, 0, 10, []).length, 3)
})

test('catalog threshold display never hard-codes values', () => {
  assert.equal(humanKey('gs_ceiling'), 'gs ceiling')
  assert.equal(fmtThreshold(1500), '1,500')
  assert.equal(fmtThreshold(0.08), '0.08')
  assert.equal(fmtThreshold(['7500', '7600']), '7500, 7600')
  assert.equal(fmtThreshold({ a: 1, b: [2, 3] }), 'a: 1, b: 2, 3')
  assert.equal(fmtThreshold(null), '—')
  assert.equal(fmtThreshold('categories.<cat>.max_gs_kt'), 'categories.<cat>.max_gs_kt')
})

test('parseInline handles bold, italics, code and backslash escapes', () => {
  assert.deepEqual(parseInline('GT\\-001 \\(teleport\\)'), [{ type: 'text', text: 'GT-001 (teleport)' }])
  const parts = parseInline('a **b** `c` _d_ e')
  assert.deepEqual(parts.map((p) => p.type), ['text', 'strong', 'text', 'code', 'text', 'em', 'text'])
  assert.deepEqual(parseInline('single\\_ghost and socal\\-a\\.jsonl\\.gz'), [{ type: 'text', text: 'single_ghost and socal-a.jsonl.gz' }])
})

test('parseMarkdown reads headings, tables, lists and paragraphs; HTML stays text', () => {
  const md = '# Title\n\nSome **text**.\n\n| A | B |\n|---|---|\n| GT\\-001 | 24 |\n| x | <b>y</b> |\n\n- one\n- two\n\n_Footer._'
  const blocks = parseMarkdown(md)
  assert.deepEqual(blocks.map((b) => b.type), ['heading', 'paragraph', 'table', 'list', 'paragraph'])
  assert.equal(blocks[0].level, 1)
  const table = blocks[2]
  assert.equal(table.head.length, 2)
  assert.equal(table.rows.length, 2)
  assert.equal(table.rows[0][0][0].text, 'GT-001')
  assert.equal(table.rows[1][1][0].text, '<b>y</b>') // a text node: React escapes it, no HTML is ever built
  assert.equal(blocks[3].items.length, 2)
  assert.deepEqual(parseMarkdown(''), [])
})
