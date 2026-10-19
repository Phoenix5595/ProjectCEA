import { afterEach, describe, expect, it } from 'vitest'

import type { FixedRange } from '../../monitoring/state'

import {
  clipArchiveRange,
  createArchiveNavigationState,
  getLoadedSelection,
  loadedSelectionFromNavigation,
  readArchiveNavigationState,
  registerLoadedSelection,
  releaseLoadedSelection,
  resolveArchiveHistoricalSelection,
} from '../selection'
import type { ArchiveSelection } from '../selection'

const ARCHIVE: ArchiveSelection = {
  dataset_id: '11111111-1111-4111-8111-111111111111',
  revision: 4,
  load_id: '22222222-2222-4222-8222-222222222222',
  room: 'Flower Room',
  start: '2026-07-01T00:00:00.000Z',
  end: '2026-08-01T00:00:00.000Z',
  remote_download_url: 'https://cloud.example.invalid/archives/grow-r4.zip',
  download_label: 'Download verified ZIP (r4)',
}

function fixed(start: string, end: string): FixedRange {
  return { kind: 'fixed', start: new Date(start), end: new Date(end) }
}

afterEach(() => releaseLoadedSelection(ARCHIVE.load_id))

describe('archive Historical selection bounds', () => {
  it('snaps the first short drag to seven days inside the selected archive', () => {
    const result = resolveArchiveHistoricalSelection(
      fixed(ARCHIVE.start, ARCHIVE.end),
      new Date('2026-07-25T00:00:00.000Z'),
      new Date('2026-07-27T00:00:00.000Z'),
      ARCHIVE
    )

    if (result === null) throw new Error('Expected a bounded archive selection')
    expect(result.surface).toBe('history')
    expect(result.end.getTime() - result.start.getTime()).toBe(7 * 24 * 60 * 60 * 1000)
    expect(result.start.getTime()).toBeGreaterThanOrEqual(Date.parse(ARCHIVE.start))
    expect(result.end.getTime()).toBeLessThanOrEqual(Date.parse(ARCHIVE.end))
  })

  it('hands the next short drag off unchanged from the seven-day archive view', () => {
    const displayed = fixed('2026-07-22T12:00:00.000Z', '2026-07-29T12:00:00.000Z')
    const selection = {
      start: new Date('2026-07-24T00:00:00.000Z'),
      end: new Date('2026-07-26T00:00:00.000Z'),
    }
    const result = resolveArchiveHistoricalSelection(displayed, selection.start, selection.end, ARCHIVE)

    expect(result?.surface).toBe('monitoring')
    expect(result?.start).toEqual(selection.start)
    expect(result?.end).toEqual(selection.end)
  })

  it('keeps an archive shorter than seven days at its actual bounds without padding', () => {
    const shortArchive = {
      ...ARCHIVE,
      start: '2026-07-01T00:00:00.000Z',
      end: '2026-07-04T00:00:00.000Z',
    }
    const selection = resolveArchiveHistoricalSelection(
      fixed(shortArchive.start, shortArchive.end),
      new Date('2026-07-02T00:00:00.000Z'),
      new Date('2026-07-03T00:00:00.000Z'),
      shortArchive
    )

    expect(selection?.surface).toBe('monitoring')
    expect(selection?.start.toISOString()).toBe('2026-07-02T00:00:00.000Z')
    expect(selection?.end.toISOString()).toBe('2026-07-03T00:00:00.000Z')
    expect(selection!.start.getTime()).toBeGreaterThanOrEqual(Date.parse(shortArchive.start))
    expect(selection!.end.getTime()).toBeLessThanOrEqual(Date.parse(shortArchive.end))
  })

  it('clips requests to real half-open archive bounds and rejects disjoint ranges', () => {
    const clipped = clipArchiveRange(
      new Date('2026-06-30T12:00:00.000Z'),
      new Date('2026-07-02T00:00:00.000Z'),
      ARCHIVE
    )
    expect(clipped?.start.toISOString()).toBe(ARCHIVE.start)
    expect(clipped?.end.toISOString()).toBe('2026-07-02T00:00:00.000Z')
    expect(clipArchiveRange(
      new Date('2026-08-02T00:00:00.000Z'),
      new Date('2026-08-03T00:00:00.000Z'),
      ARCHIVE
    )).toBeNull()
  })
})

describe('archive selection route pins', () => {
  it('restores only an exact in-memory dataset, revision, room, and load match', () => {
    registerLoadedSelection(ARCHIVE)
    const state = createArchiveNavigationState(ARCHIVE)

    expect(readArchiveNavigationState(state)?.selection).toEqual({
      dataset_id: ARCHIVE.dataset_id,
      revision: ARCHIVE.revision,
      load_id: ARCHIVE.load_id,
      room: ARCHIVE.room,
    })
    expect(loadedSelectionFromNavigation(state, 'Flower Room')).toEqual(ARCHIVE)
    expect(loadedSelectionFromNavigation(state, 'Veg Room')).toBeUndefined()
    expect(loadedSelectionFromNavigation({
      archive_selection: { ...state.archive_selection, revision: ARCHIVE.revision - 1 },
    }, 'Flower Room')).toBeUndefined()

    releaseLoadedSelection(ARCHIVE.load_id)
    expect(getLoadedSelection(ARCHIVE.load_id)).toBeUndefined()
    expect(loadedSelectionFromNavigation(state, 'Flower Room')).toBeUndefined()
  })
})
