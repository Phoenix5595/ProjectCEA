/**
 * Ten-row, server-paginated Historical archive log.
 *
 * Listing/detail reads are passive. Export and Load are explicit user actions;
 * only a ready Load response registers an in-memory selection for graph reads.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ArchiveApi, ARCHIVE_LOG_PAGE_SIZE, isSafeNextcloudFileUrl } from './api'
import { ArchiveReadyLoad } from './contracts'
import type { ArchiveEntry, ArchiveJob, ArchiveJobSummary, ArchiveRoom } from './contracts'
import {
  registerLoadedSelection,
  releaseLoadedSelection,
} from './selection'
import type { ArchiveSelection } from './selection'

export interface ArchiveLogProps {
  readonly room: ArchiveRoom
  /** Restored router state can focus metadata without starting a Load. */
  readonly focusDatasetId?: string
  readonly onLoaded: (selection: ArchiveSelection) => void | Promise<void>
}

interface PendingJob {
  readonly job_id: string
  readonly action: 'export' | 'load'
  readonly entry: ArchiveEntry
}

const RETRYABLE_JOB_ERRORS = new Set([
  'archive_remote_unavailable',
  'archive_transport_failed',
  'archive_local_space_insufficient',
  'archive_remote_quota',
  'archive_database_unavailable',
  'archive_lock_timeout',
  'archive_revision_pending',
  'archive_artifact_pinned',
])

const archiveDateTime = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Toronto',
  dateStyle: 'medium',
  timeStyle: 'short',
})
const archiveDate = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Toronto',
  dateStyle: 'medium',
})

function formatInstant(value: Date | null): string {
  if (value === null) return 'Unknown'
  return archiveDateTime.format(value)
}

function formatBytes(value: number | null): string {
  if (value === null) return '—'
  if (value < 1024) return `${value} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = -1
  do {
    size /= 1024
    index += 1
  } while (size >= 1024 && index < units.length - 1)
  return `${size.toFixed(1)} ${units[index]}`
}

function errorCode(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = error.response
    const data = typeof response === 'object' && response !== null && 'data' in response ? response.data : undefined
    const detail = typeof data === 'object' && data !== null && 'detail' in data ? data.detail : undefined
    if (typeof detail === 'string') return detail
    if (typeof detail === 'object' && detail !== null && 'code' in detail) {
      const code = detail.code
      if (typeof code === 'string') return code
    }
  }
  if (error instanceof Error && error.message.length > 0) return error.message
  return 'archive_service_unavailable'
}

function displayJob(job: ArchiveJobSummary | ArchiveJob | null): string {
  if (job === null) return 'No active job'
  const progress = job.progress
  const phase = typeof progress.phase === 'string' ? progress.phase.replaceAll('_', ' ') : null
  const percent = typeof progress.percent === 'number' ? `${Math.round(progress.percent)}%` : null
  const completed = typeof progress.completed === 'number' ? progress.completed : null
  const total = typeof progress.total === 'number' ? progress.total : null
  const fraction = completed !== null && total !== null ? `${completed}/${total}` : null
  const detail = [phase, percent, fraction].filter((value): value is string => value !== null)
  return `${job.state}${detail.length === 0 ? '' : ` — ${detail.join(' · ')}`}${job.last_error_code === null ? '' : ` (${job.last_error_code})`}`
}

function stateClass(value: string): string {
  if (value === 'ready' || value === 'verified' || value === 'complete' || value === 'succeeded') {
    return 'archive-status archive-status--success'
  }
  if (value === 'blocked' || value === 'failed' || value === 'gapped' || value === 'dirty') {
    return 'archive-status archive-status--danger'
  }
  if (value === 'pending' || value === 'copying' || value === 'unknown') return 'archive-status archive-status--warning'
  return 'archive-status'
}

export function ArchiveLog({ room, focusDatasetId, onLoaded }: ArchiveLogProps) {
  const api = useMemo(() => new ArchiveApi(), [])
  const loadedCallback = useRef(onLoaded)
  loadedCallback.current = onLoaded

  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null])
  const [pageIndex, setPageIndex] = useState(0)
  const cursor = cursorStack[pageIndex] ?? null
  const [page, setPage] = useState<{ items: ArchiveEntry[]; next_cursor: string | null } | null>(null)
  const [focusedEntry, setFocusedEntry] = useState<ArchiveEntry | null>(null)
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)
  const [focusError, setFocusError] = useState<string | null>(null)
  const [refreshRevision, setRefreshRevision] = useState(0)
  const [jobOverrides, setJobOverrides] = useState<Record<string, ArchiveJob>>({})
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})
  const [busyDatasetId, setBusyDatasetId] = useState<string | null>(null)
  const [pendingJob, setPendingJob] = useState<PendingJob | null>(null)

  const refresh = useCallback(() => setRefreshRevision(value => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setPageError(null)
    setJobOverrides({})
    void api.list(room, cursor, controller.signal).then(
      result => {
        if (controller.signal.aborted) return
        setPage(result)
        setLoading(false)
      },
      error => {
        if (controller.signal.aborted) return
        setPageError(errorCode(error))
        setLoading(false)
      }
    )
    return () => controller.abort()
  }, [api, cursor, refreshRevision, room])

  useEffect(() => {
    if (focusDatasetId === undefined) {
      setFocusedEntry(null)
      setFocusError(null)
      return
    }
    const controller = new AbortController()
    setFocusedEntry(null)
    setFocusError(null)
    void api.detail(focusDatasetId, controller.signal).then(
      result => {
        if (controller.signal.aborted) return
        if (result.room !== room) {
          setFocusError('archive_room_mismatch')
          return
        }
        setFocusedEntry(result)
      },
      error => {
        if (!controller.signal.aborted) setFocusError(errorCode(error))
      }
    )
    return () => controller.abort()
  }, [api, focusDatasetId, room])

  const displayedJobs = useMemo(() => {
    const result: Array<ArchiveJobSummary | ArchiveJob> = []
    for (const entry of page?.items ?? []) {
      if (entry.job !== null) result.push(jobOverrides[entry.job.job_id] ?? entry.job)
    }
    return result
  }, [jobOverrides, page])
  const activeJobIds = displayedJobs
    .filter(job => job.state === 'pending' || job.state === 'running')
    .map(job => job.job_id)
    .sort()
  const activeJobKey = activeJobIds.join('|')

  useEffect(() => {
    if (activeJobIds.length === 0 || pendingJob !== null) return
    let stopped = false
    let timer: number | undefined
    const poll = async (): Promise<void> => {
      try {
        const jobs = await Promise.all(activeJobIds.map(jobId => api.job(jobId)))
        if (stopped) return
        setJobOverrides(current => {
          const next = { ...current }
          for (const job of jobs) next[job.job_id] = job
          return next
        })
        if (jobs.some(job => job.state === 'succeeded' || job.state === 'failed' || job.state === 'blocked')) {
          refresh()
        }
        if (!stopped && jobs.some(job => job.state === 'pending' || job.state === 'running')) {
          timer = window.setTimeout(poll, 2000)
        }
      } catch {
        if (!stopped) timer = window.setTimeout(poll, 4000)
      }
    }
    timer = window.setTimeout(poll, 1500)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
    // `activeJobKey` captures exactly the bounded page's active job set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJobKey, api, pendingJob, refresh])

  const acceptReadyLoad = useCallback(
    async (
      entry: ArchiveEntry,
      result: ArchiveReadyLoad
    ): Promise<void> => {
      if (entry.start === null || entry.end === null) throw new Error('archive_selection_unavailable')
      if (result.revision !== entry.published_revision) throw new Error('archive_revision_conflict')
      const selection: ArchiveSelection = {
        dataset_id: entry.dataset_id,
        revision: result.revision,
        load_id: result.load_id,
        room: entry.room,
        start: entry.start.toISOString(),
        end: entry.end.toISOString(),
        remote_download_url: result.remote_download_url,
        download_label: result.download_label,
      }
      registerLoadedSelection(selection)
      try {
        await loadedCallback.current(selection)
      } catch (error) {
        releaseLoadedSelection(selection.load_id)
        try {
          await api.releaseLoad(selection.load_id)
        } catch {
          // An unconfirmed DELETE leaves the server's short idle pin intact.
        }
        throw error
      }
    },
    [api]
  )

  useEffect(() => {
    if (pendingJob === null) return
    let stopped = false
    let timer: number | undefined
    const poll = async (): Promise<void> => {
      try {
        const job = await api.job(pendingJob.job_id)
        if (stopped) return
        setJobOverrides(current => ({ ...current, [job.job_id]: job }))
        if (job.state === 'succeeded') {
          setPendingJob(null)
          if (pendingJob.action === 'load') {
            try {
              const ready = ArchiveReadyLoad.parse({ ...job.progress, state: 'ready' })
              await acceptReadyLoad(pendingJob.entry, ready)
            } catch (error) {
              setRowErrors(current => ({
                ...current,
                [pendingJob.entry.dataset_id]: errorCode(error),
              }))
            }
          } else {
            refresh()
          }
          return
        }
        if (job.state === 'failed' || job.state === 'blocked') {
          setPendingJob(null)
          setRowErrors(current => ({
            ...current,
            [pendingJob.entry.dataset_id]: job.last_error_code ?? `archive_job_${job.state}`,
          }))
          refresh()
          return
        }
      } catch (error) {
        if (!stopped) {
          setRowErrors(current => ({
            ...current,
            [pendingJob.entry.dataset_id]: errorCode(error),
          }))
        }
      }
      if (!stopped) timer = window.setTimeout(poll, 1800)
    }
    timer = window.setTimeout(poll, 700)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [acceptReadyLoad, api, pendingJob, refresh])

  const startExport = useCallback(
    async (entry: ArchiveEntry): Promise<void> => {
      if (entry.kind === 'grow' && entry.lifecycle_state !== 'closed') return
      setBusyDatasetId(entry.dataset_id)
      setRowErrors(current => ({ ...current, [entry.dataset_id]: '' }))
      try {
        const result = await api.exportDataset(entry.dataset_id, entry.revision)
        if (result.state === 'verified') refresh()
        else setPendingJob({ job_id: result.job_id, action: 'export', entry })
      } catch (error) {
        setRowErrors(current => ({ ...current, [entry.dataset_id]: errorCode(error) }))
      } finally {
        setBusyDatasetId(null)
      }
    },
    [api, refresh]
  )

  const startLoad = useCallback(
    async (entry: ArchiveEntry): Promise<void> => {
      if (entry.published_revision === null || entry.start === null || entry.end === null) return
      setBusyDatasetId(entry.dataset_id)
      setRowErrors(current => ({ ...current, [entry.dataset_id]: '' }))
      try {
        const result = await api.load(entry.dataset_id, entry.published_revision)
        if (result.state === 'ready') {
          if (result.revision !== entry.published_revision) throw new Error('archive_revision_conflict')
          await acceptReadyLoad(entry, result)
        } else {
          setPendingJob({ job_id: result.job_id, action: 'load', entry })
        }
      } catch (error) {
        setRowErrors(current => ({ ...current, [entry.dataset_id]: errorCode(error) }))
      } finally {
        setBusyDatasetId(null)
      }
    },
    [acceptReadyLoad, api]
  )

  const retry = useCallback(
    async (entry: ArchiveEntry): Promise<void> => {
      const job = entry.job === null ? null : jobOverrides[entry.job.job_id] ?? entry.job
      if (
        job === null ||
        job.state !== 'failed' ||
        job.last_error_code === null ||
        !RETRYABLE_JOB_ERRORS.has(job.last_error_code)
      ) {
        return
      }
      setBusyDatasetId(entry.dataset_id)
      setRowErrors(current => ({ ...current, [entry.dataset_id]: '' }))
      try {
        await api.retryJob(job.job_id)
        refresh()
      } catch (error) {
        setRowErrors(current => ({ ...current, [entry.dataset_id]: errorCode(error) }))
      } finally {
        setBusyDatasetId(null)
      }
    },
    [api, jobOverrides, refresh]
  )

  const rowActions = (entry: ArchiveEntry) => {
    const busy = busyDatasetId === entry.dataset_id || pendingJob?.entry.dataset_id === entry.dataset_id
    const job = entry.job === null ? null : jobOverrides[entry.job.job_id] ?? entry.job
    const verifiedHref = entry.export_status === 'verified' && isSafeNextcloudFileUrl(entry.remote_download_url)
      ? entry.remote_download_url
      : null
    const canExport = entry.kind !== 'grow' || entry.lifecycle_state === 'closed'
    const hasLoadableRevision = entry.published_revision !== null && entry.start !== null && entry.end !== null
    const canLoad = hasLoadableRevision && entry.export_status === 'verified'
    const needsExport = entry.export_status === 'not_exported' ||
      (hasLoadableRevision && entry.export_status !== 'verified')
    const loadTitle = !canLoad
      ? needsExport
        ? 'Export first'
        : 'No verified published archive revision is available to load'
      : undefined
    const canRetry =
      job?.state === 'failed' &&
      job.last_error_code !== null &&
      RETRYABLE_JOB_ERRORS.has(job.last_error_code)
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {verifiedHref !== null && (
          <a
            className="underline decoration-accent underline-offset-2"
            href={verifiedHref}
            target="_blank"
            rel="noopener noreferrer"
            title={entry.download_label ?? 'Open verified ZIP in Nextcloud'}
            aria-label={entry.download_label ?? 'Open verified ZIP in Nextcloud'}
          >
            {entry.container === null ? 'Nextcloud ZIP' : 'Annual ZIP (all quarters)'}
          </a>
        )}
        <button
          type="button"
          className="mon-toolbar__preset"
          disabled={busy || !canExport || pendingJob !== null}
          title={!canExport ? 'Close the grow before requesting its archive export' : undefined}
          onClick={() => void startExport(entry)}
        >
          {busy && pendingJob?.action === 'export' ? 'Exporting…' : 'Export ZIP'}
        </button>
        <button
          type="button"
          className="mon-toolbar__preset"
          disabled={busy || !canLoad || pendingJob !== null}
          title={loadTitle}
          onClick={() => void startLoad(entry)}
        >
          {busy && pendingJob?.action === 'load' ? 'Loading…' : `Load${entry.published_revision === null ? '' : ` r${entry.published_revision}`}`}
        </button>
        {canRetry && job !== null && (
          <button
            type="button"
            className="mon-toolbar__preset"
            disabled={busy || pendingJob !== null}
            onClick={() => void retry(entry)}
          >
            Retry job
          </button>
        )}
      </div>
    )
  }

  const visibleEntries = page?.items ?? []
  const nextCursor = page?.next_cursor ?? null
  const showFocusedEntry = focusedEntry !== null &&
    !visibleEntries.some(entry => entry.dataset_id === focusedEntry.dataset_id)

  return (
    <section className="mon-card archive-log" aria-label="Historical archive log">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="mon-card__title">Historical archive log</h2>
          <p className="text-sm text-text-muted">
            Select a published revision to load it. Opening this log never downloads an archive.
          </p>
        </div>
        <span className="text-sm text-text-muted">{room}</span>
      </div>

      {pageError !== null && (
        <div className="mon-banner mon-banner--error" role="alert">
          Archive log unavailable: {pageError}
          <button type="button" onClick={refresh}>Retry</button>
        </div>
      )}
      {focusError !== null && (
        <div className="mon-banner mon-banner--error" role="alert">
          Restored archive metadata unavailable: {focusError}. No archive data was loaded.
        </div>
      )}
      {focusDatasetId !== undefined && showFocusedEntry && focusedEntry !== null && (
        <section className="mon-banner" aria-label="Restored archive selection">
          <strong>{focusedEntry.name}</strong>
          <span className="ml-2 text-text-muted">
            Restored route has no in-memory load pin. Use Load to select a published revision again.
          </span>
          <div className="mt-2">{rowActions(focusedEntry)}</div>
        </section>
      )}
      {pendingJob !== null && (
        <div role="status" className="mon-banner">
          {pendingJob.action === 'load' ? 'Loading selected archive' : 'Exporting archive'} · job {pendingJob.job_id} ·{' '}
          {jobOverrides[pendingJob.job_id] === undefined
            ? 'queued; waiting for worker progress'
            : displayJob(jobOverrides[pendingJob.job_id])}
        </div>
      )}

      {loading && (
        <div role="status" className="mon-banner">Loading archive entries…</div>
      )}
      {!loading && pageError === null && visibleEntries.length === 0 && focusedEntry === null && (
        <div role="status" className="mon-banner">No archived datasets are available for {room}.</div>
      )}

      {!loading && visibleEntries.length > 0 && (
        <>
          <table className="w-full table-fixed border-collapse text-xs" aria-label={`${room} archives`}>
            <colgroup>
              <col style={{ width: '17%' }} />
              <col style={{ width: '18%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '14%' }} />
              <col style={{ width: '15%' }} />
              <col style={{ width: '13%' }} />
              <col style={{ width: '13%' }} />
            </colgroup>
            <thead>
              <tr className="text-left text-text-muted">
                <th scope="col" className="border-b border-border-default p-2">Name / period</th>
                <th scope="col" className="border-b border-border-default p-2">Dates (Toronto, end exclusive)</th>
                <th scope="col" className="border-b border-border-default p-2">Local tier</th>
                <th scope="col" className="border-b border-border-default p-2">Remote export / bytes</th>
                <th scope="col" className="border-b border-border-default p-2">Coverage / gaps</th>
                <th scope="col" className="border-b border-border-default p-2">Job</th>
                <th scope="col" className="border-b border-border-default p-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visibleEntries.map(entry => {
                const job = entry.job === null ? null : jobOverrides[entry.job.job_id] ?? entry.job
                const stalePublished = entry.published_revision !== null && entry.published_revision !== entry.revision
                return (
                  <tr key={entry.dataset_id} className="align-top">
                    <td className="border-b border-border-subtle p-2">
                      <div className="flex items-baseline gap-2">
                        <span className="min-w-0 truncate font-semibold text-text-default" title={entry.name}>{entry.name}</span>
                        <span className={`shrink-0 ${stateClass(entry.state)}`}>{entry.state.replaceAll('_', ' ')}</span>
                      </div>
                      <div className="text-text-muted" title={entry.room}>
                        {entry.kind.replaceAll('_', ' ')} · {entry.room.replace(' Room', '')}
                        {entry.lifecycle_state === null ? '' : ` · ${entry.lifecycle_state}`}
                      </div>
                      <div className="text-text-muted">
                        {stalePublished
                          ? `Published r${entry.published_revision} · current r${entry.revision}`
                          : `Revision r${entry.revision}`}
                      </div>
                    </td>
                    <td className="border-b border-border-subtle p-2" title={entry.start?.toISOString() ?? undefined}>
                      <div>{formatInstant(entry.start)}</div>
                      <div>– {formatInstant(entry.end)}</div>
                      {entry.anniversary_at !== null && (
                        <div className="text-text-muted" title={`Original anniversary ${formatInstant(entry.anniversary_at)}`}>
                          Retain until {archiveDate.format(entry.anniversary_at)}
                        </div>
                      )}
                    </td>
                    <td className="border-b border-border-subtle p-2">{entry.local_tier.replaceAll('_', ' ')}</td>
                    <td className="border-b border-border-subtle p-2">
                      <div className={stateClass(entry.export_status)}>{entry.export_status.replaceAll('_', ' ')}</div>
                      <div>{formatBytes(entry.zip_bytes)}</div>
                    </td>
                    <td className="border-b border-border-subtle p-2">
                      <div className={stateClass(entry.coverage)}>{entry.coverage}</div>
                      {entry.gap_intervals.length > 0 && (
                        <details>
                          <summary>{entry.gap_intervals.length} known gap{entry.gap_intervals.length === 1 ? '' : 's'}</summary>
                          <ul className="list-disc pl-4">
                            {entry.gap_intervals.map((gap, index) => (
                              <li key={`${gap.start.toISOString()}-${index}`}>
                                {formatInstant(gap.start)} – {formatInstant(gap.end)}: {gap.reason}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </td>
                    <td className="border-b border-border-subtle p-2">
                      <div className={job === null ? 'text-text-muted' : stateClass(job.state)}>{displayJob(job)}</div>
                      {job?.next_attempt_at !== null && job?.next_attempt_at !== undefined && (
                        <div className="text-text-muted">Next attempt {formatInstant(job.next_attempt_at)}</div>
                      )}
                    </td>
                    <td className="border-b border-border-subtle p-2">
                      {rowActions(entry)}
                      {rowErrors[entry.dataset_id] && (
                        <div role="alert" className="mt-1 archive-status--danger">{rowErrors[entry.dataset_id]}</div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="text-xs text-text-muted">
              Page {pageIndex + 1} · {Math.min(ARCHIVE_LOG_PAGE_SIZE, visibleEntries.length)} of up to {ARCHIVE_LOG_PAGE_SIZE} entries
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                className="mon-toolbar__preset"
                disabled={pageIndex === 0 || loading}
                onClick={() => setPageIndex(index => Math.max(0, index - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="mon-toolbar__preset"
                disabled={nextCursor === null || loading}
                onClick={() => {
                  if (nextCursor === null) return
                  setCursorStack(stack => [...stack.slice(0, pageIndex + 1), nextCursor])
                  setPageIndex(index => index + 1)
                }}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
