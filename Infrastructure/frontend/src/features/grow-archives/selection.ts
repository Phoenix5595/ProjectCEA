/**
 * In-memory pins for archives explicitly loaded from the Historical log.
 *
 * A route/history entry is not authority to read an archive: it must match a
 * live module-local selection created after a ready load response. Reloading
 * clears this map, so a restored route can show metadata and an explicit Load
 * action but cannot silently fetch archive data.
 */
import type { FixedRange, MonitoringRange } from '../monitoring/state'
import { resolveHistoricalSelection } from '../history-overview/historicalSelection'
import { MAX_RANGE_MS } from '../monitoring/components/timeRangeToolbar.time'

export interface ArchiveSelection {
  readonly dataset_id: string
  readonly revision: number
  readonly load_id: string
  readonly room: 'Flower Room' | 'Veg Room'
  readonly start: string
  readonly end: string
  readonly remote_download_url: string | null
  readonly download_label: string | null
}

export interface ArchiveSelectionReference {
  readonly dataset_id: string
  readonly revision: number
  readonly load_id: string
  readonly room: 'Flower Room' | 'Veg Room'
}

export type ArchiveSurface = 'view' | 'log'

export interface ArchiveNavigationState {
  readonly selection: ArchiveSelectionReference
  readonly surface: ArchiveSurface
}

export interface ArchiveSelectionResolution {
  readonly surface: 'history' | 'monitoring'
  readonly start: Date
  readonly end: Date
}

const loadedSelections = new Map<string, ArchiveSelection>()

function isRoom(value: unknown): value is ArchiveSelection['room'] {
  return value === 'Flower Room' || value === 'Veg Room'
}

function validInstant(value: string): Date | null {
  const parsed = new Date(value)
  return Number.isFinite(parsed.getTime()) ? parsed : null
}

function normalizeSelection(selection: ArchiveSelection): ArchiveSelection {
  if (
    selection.dataset_id.length === 0 ||
    selection.load_id.length === 0 ||
    !Number.isSafeInteger(selection.revision) ||
    selection.revision < 0
  ) {
    throw new Error('archive_load_required')
  }
  const start = validInstant(selection.start)
  const end = validInstant(selection.end)
  if (start === null || end === null || end.getTime() <= start.getTime()) {
    throw new Error('archive_load_required')
  }
  if (!isRoom(selection.room)) throw new Error('archive_load_required')
  return Object.freeze({
    dataset_id: selection.dataset_id,
    revision: selection.revision,
    load_id: selection.load_id,
    room: selection.room,
    start: start.toISOString(),
    end: end.toISOString(),
    remote_download_url: selection.remote_download_url,
    download_label: selection.download_label,
  })
}

/** Call only after the explicit load action returned a ready load pin. */
export function registerLoadedSelection(selection: ArchiveSelection): void {
  const normalized = normalizeSelection(selection)
  const existing = loadedSelections.get(normalized.load_id)
  if (existing !== undefined && !sameSelection(existing, normalized)) {
    throw new Error('archive_load_conflict')
  }
  loadedSelections.set(normalized.load_id, normalized)
}

export function getLoadedSelection(loadId: string): ArchiveSelection | undefined {
  return loadedSelections.get(loadId)
}

/** Forget a selection only after its matching DELETE load-pin request succeeds. */
export function releaseLoadedSelection(loadId: string): void {
  loadedSelections.delete(loadId)
}

export function sameSelection(left: ArchiveSelectionReference, right: ArchiveSelectionReference): boolean {
  return (
    left.dataset_id === right.dataset_id &&
    left.revision === right.revision &&
    left.load_id === right.load_id &&
    left.room === right.room
  )
}

/** Parse the opaque React Router state without persisting archive selections. */
export function readArchiveNavigationState(state: unknown): ArchiveNavigationState | null {
  if (typeof state !== 'object' || state === null) return null
  const navigation = state as Record<string, unknown>
  const raw = navigation.archive_selection
  if (typeof raw !== 'object' || raw === null) return null
  const candidate = raw as Record<string, unknown>
  if (
    typeof candidate.dataset_id !== 'string' ||
    typeof candidate.load_id !== 'string' ||
    !Number.isSafeInteger(candidate.revision) ||
    !isRoom(candidate.room)
  ) {
    return null
  }
  const surface = navigation.archive_surface === 'log' ? 'log' : 'view'
  return {
    selection: {
      dataset_id: candidate.dataset_id,
      revision: candidate.revision as number,
      load_id: candidate.load_id,
      room: candidate.room,
    },
    surface,
  }
}

export function createArchiveNavigationState(
  selection: ArchiveSelectionReference,
  surface: ArchiveSurface = 'view'
): { archive_selection: ArchiveSelectionReference; archive_surface: ArchiveSurface } {
  return {
    archive_selection: {
      dataset_id: selection.dataset_id,
      revision: selection.revision,
      load_id: selection.load_id,
      room: selection.room,
    },
    archive_surface: surface,
  }
}

/** Return a route pin only if its dataset, revision, room, and load all match. */
export function loadedSelectionFromNavigation(
  state: unknown,
  room: ArchiveSelection['room']
): ArchiveSelection | undefined {
  const navigation = readArchiveNavigationState(state)
  if (navigation === null || navigation.selection.room !== room) return undefined
  const loaded = loadedSelections.get(navigation.selection.load_id)
  if (loaded === undefined || !sameSelection(loaded, navigation.selection)) return undefined
  return loaded
}

/** Intersect a requested fixed range with the immutable loaded archive bounds. */
export function clipArchiveRange(
  start: Date,
  end: Date,
  selection: Pick<ArchiveSelection, 'start' | 'end'>
): FixedRange | null {
  const scopeStart = validInstant(selection.start)
  const scopeEnd = validInstant(selection.end)
  if (scopeStart === null || scopeEnd === null || scopeEnd.getTime() <= scopeStart.getTime()) return null
  const startMs = start.getTime()
  const endMs = end.getTime()
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) return null
  const clippedStart = Math.max(startMs, scopeStart.getTime())
  const clippedEnd = Math.min(endMs, scopeEnd.getTime())
  if (clippedEnd < clippedStart) return null
  return { kind: 'fixed', start: new Date(clippedStart), end: new Date(clippedEnd) }
}

/**
 * Apply the existing two-stage Historical drill-down policy within an archive.
 * Both the incoming selection and the returned range are clipped to the
 * archive's real half-open bounds, so a short archive is never padded.
 */
export function resolveArchiveHistoricalSelection(
  displayed: MonitoringRange,
  start: Date,
  end: Date,
  bounds: Pick<ArchiveSelection, 'start' | 'end'>
): ArchiveSelectionResolution | null {
  const boundedInput = clipArchiveRange(start, end, bounds)
  if (boundedInput === null) return null
  const resolved = resolveHistoricalSelection(displayed, boundedInput.start, boundedInput.end)
  const boundedOutput = clipArchiveRange(resolved.start, resolved.end, bounds)
  if (boundedOutput === null) return null
  return { surface: resolved.surface, start: boundedOutput.start, end: boundedOutput.end }
}

/** Archive ranges longer than seven days use the same overview threshold. */
export function isArchiveOverviewRange(range: FixedRange): boolean {
  return range.end.getTime() - range.start.getTime() > MAX_RANGE_MS
}
