export type EventLogEntry = Readonly<{
  redisId: string
  eventId: string
  type: string
  category: string
  occurredAt: Date
  payload: Record<string, unknown>
}>

type Listener = () => void

export type EventLogPaging = Readonly<{
  oldestCursor: string | null
  hasMore: boolean
  loadingOlder: boolean
}>

export type EventLogSnapshot = Readonly<{
  entries: readonly EventLogEntry[]
  paging: EventLogPaging
}>

const initialPaging: EventLogPaging = { oldestCursor: null, hasMore: false, loadingOlder: false }

export class EventLogStore {
  private readonly values = new Map<string, EventLogEntry>()
  private readonly eventIdsByRedisId = new Map<string, string>()
  private readonly listeners = new Set<Listener>()
  private currentSnapshot: EventLogSnapshot = { entries: [], paging: initialPaging }
  constructor(private readonly limit = 5000) {}
  merge(entries: readonly EventLogEntry[]): void {
    let changed = false
    for (const entry of entries) {
      if (this.values.has(entry.eventId) || this.eventIdsByRedisId.has(entry.redisId)) continue
      this.values.set(entry.eventId, entry)
      this.eventIdsByRedisId.set(entry.redisId, entry.eventId)
      changed = true
    }
    while (this.values.size > this.limit) {
      const oldest = [...this.values.values()].sort((left, right) => left.redisId.localeCompare(right.redisId, undefined, { numeric: true }))[0]
      if (oldest === undefined) return
      this.values.delete(oldest.eventId)
      this.eventIdsByRedisId.delete(oldest.redisId)
      changed = true
    }
    if (changed) {
      this.currentSnapshot = { ...this.currentSnapshot, entries: [...this.values.values()].sort((left, right) => left.redisId.localeCompare(right.redisId, undefined, { numeric: true })) }
      this.notify()
    }
  }
  snapshot(): EventLogSnapshot {
    return this.currentSnapshot
  }
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  reset(): void {
    this.values.clear()
    this.eventIdsByRedisId.clear()
    this.currentSnapshot = { entries: [], paging: initialPaging }
    this.notify()
  }
  setPaging(paging: EventLogPaging): void {
    this.currentSnapshot = { ...this.currentSnapshot, paging }
    this.notify()
  }
  private notify(): void {
    for (const listener of this.listeners) {
      listener()
    }
  }
}

export const globalEventLogStore = new EventLogStore()
