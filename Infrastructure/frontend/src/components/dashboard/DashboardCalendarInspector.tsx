/** Dashboard-only calendar inspector: day summaries, event detail, manual create/edit. */
import { format } from 'date-fns'
import { fr } from 'date-fns/locale'
import { toZonedTime } from 'date-fns-tz'
import { useEffect, useMemo, useRef, useState } from 'react'

import { DASHBOARD_ROW_ZONES } from '../../config/zones'
import { apiClient } from '../../services/api'
import type {
  CalendarEventCreate,
  CalendarEventDto,
  CalendarEventUpdate,
  GrowLifecycle,
  GrowClosePreview,
} from '../../types/calendar'
import {
  buildCalendarDayMarkers,
  calendarDayKey,
  calendarEventDayKey,
  calendarEventOccursOnDay,
  isGrowPlanPhase,
} from '../../utils/calendarDayMarkers'
import { CALENDAR_TZ } from '../../utils/flowerGrowPlan'
import { Dialog, DialogContent, DialogTitle } from '../ui/dialog'

function calendarErrorCode(error: unknown): string | undefined {
  if (!error || typeof error !== 'object' || !('response' in error)) return undefined
  const response = error.response
  if (!response || typeof response !== 'object' || !('data' in response)) return undefined
  const data = response.data
  if (!data || typeof data !== 'object') return undefined
  const detail = 'detail' in data ? data.detail : data
  return detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
    ? detail.code : undefined
}

export interface DashboardCalendarInspectorProps {
  events: CalendarEventDto[]
  now: Date
  selectedDate: Date | undefined
  selectedDateEvents: CalendarEventDto[]
  onRefresh: () => void | Promise<void>
  onOpenGrowPlan: () => void
}

/** Views that can remain visible behind a detail overlay. */
type InspectorBaseView = { kind: 'overview' } | { kind: 'day' }

type EventListView = {
  kind: 'events'
  title: string
  events: CalendarEventDto[]
  returnTo: InspectorBaseView
}

/** Views below the top-level day selection; the form always returns to one of these. */
type EventSourceView =
  | InspectorBaseView
  | EventListView
  | { kind: 'event'; event: CalendarEventDto; returnTo: EventSourceView }

type InspectorView =
  | EventSourceView
  | {
      kind: 'form'
      mode: 'create' | 'edit'
      /** Edit source event; undefined for create. */
      event?: CalendarEventDto
      /** Preselected start date (yyyy-MM-dd) for create. */
      date: string
      returnTo: EventSourceView
    }

interface EventFormState {
  title: string
  eventType: string
  location: string
  startDate: string
  endDate: string
  notes: string
  originalLocation: string
  originalCluster?: string
}

function zonedNow(now: Date): Date {
  return toZonedTime(now, CALENDAR_TZ)
}

/** 'today' / 'tomorrow' / 'N days' between two yyyy-MM-dd keys. */
function relativeDayLabel(fromKey: string, toKey: string): string {
  const diff = Math.round(
    (Date.parse(`${toKey}T00:00:00Z`) - Date.parse(`${fromKey}T00:00:00Z`)) / 86400000
  )
  if (diff <= 0) return 'today'
  if (diff === 1) return 'tomorrow'
  return `${diff} days`
}

function dateRangeLabel(ev: CalendarEventDto): string {
  if (ev.end && ev.end !== ev.start) return `${ev.start} → ${ev.end}`
  return ev.start
}

function eventIsEditable(ev: CalendarEventDto): boolean {
  return ev.source === 'manual' && ev.editable && typeof ev.numericId === 'number'
}

function locationOptions(edited?: string): string[] {
  const rooms = DASHBOARD_ROW_ZONES.map(z => z.location)
  if (edited && edited.trim() && !rooms.includes(edited)) rooms.push(edited)
  return rooms
}

export default function DashboardCalendarInspector({
  events,
  now,
  selectedDate,
  selectedDateEvents,
  onRefresh,
  onOpenGrowPlan,
}: DashboardCalendarInspectorProps) {
  const [view, setView] = useState<InspectorView>(() =>
    selectedDate ? { kind: 'day' } : { kind: 'overview' }
  )
  const [form, setForm] = useState<EventFormState | null>(null)
  const [validation, setValidation] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [lifecycle, setLifecycle] = useState<GrowLifecycle | null>(null)
  const [closePreview, setClosePreview] = useState<GrowClosePreview | null>(null)
  const [scopeIdentity, setScopeIdentity] = useState('')
  const [scopeStart, setScopeStart] = useState('')
  const [scopeEnd, setScopeEnd] = useState('')
  const [scopeRoom, setScopeRoom] = useState('Flower Room')
  const dialogTriggerRef = useRef<HTMLElement | null>(null)
  const pendingFocusRestoreRef = useRef(false)
  const dashboardTriggerRef = useRef<HTMLElement | null>(null)
  const dialogContentRef = useRef<HTMLDivElement | null>(null)

  const dialogOpen = view.kind === 'events' || view.kind === 'event' || view.kind === 'form'
  const baseView = (() => {
    let current: EventSourceView = view.kind === 'form' ? view.returnTo : view
    while (current.kind !== 'overview' && current.kind !== 'day') {
      current = current.returnTo
    }
    return current
  })()

  useEffect(() => {
    if (!dialogOpen || !pendingFocusRestoreRef.current) return
    pendingFocusRestoreRef.current = false
    const trigger = dialogTriggerRef.current
    if (trigger?.isConnected) {
      trigger.focus()
      return
    }
    dialogContentRef.current?.querySelector<HTMLElement>('button:not([disabled])')?.focus()
  }, [view, dialogOpen])

  // A selectedDate prop change deterministically resets to the matching date view.
  useEffect(() => {
    setView(selectedDate ? { kind: 'day' } : { kind: 'overview' })
    setForm(null)
    setValidation(null)
    setApiError(null)
  }, [selectedDate])

  const selectedGrowId = view.kind === 'event' ? view.event.growPlanId : undefined
  useEffect(() => {
    setLifecycle(null)
    setClosePreview(null)
    if (!selectedGrowId) return
    void apiClient.getGrowLifecycle(selectedGrowId).then(setLifecycle).catch(error => {
      setApiError(error?.response?.data?.detail ?? 'Unable to load grow lifecycle.')
    })
  }, [selectedGrowId])

  const markers = useMemo(() => buildCalendarDayMarkers(events), [events])

  const dayKeyNow = calendarDayKey(now)

  const activePhase = useMemo(() => {
    const found = events.filter(ev => isGrowPlanPhase(ev) && calendarEventOccursOnDay(ev, now))
    if (found.length === 0) return undefined
    return found.reduce((a, b) =>
      calendarEventDayKey(b.start) > calendarEventDayKey(a.start) ? b : a
    )
  }, [events, now])

  const upcomingPhase = useMemo(() => {
    const startDay = (ev: CalendarEventDto) => calendarEventDayKey(ev.start)
    const later = events.filter(ev => isGrowPlanPhase(ev) && startDay(ev) > dayKeyNow)
    if (later.length === 0) return undefined
    return later.reduce((a, b) => (startDay(b) < startDay(a) ? b : a))
  }, [events, dayKeyNow])

  const todayTasks = markers.get(dayKeyNow)?.tasks ?? []

  const selectedDateKey = selectedDate ? calendarDayKey(selectedDate) : undefined

  const openForm = (next: InspectorView & { kind: 'form' }, trigger: HTMLElement) => {
    dialogTriggerRef.current = trigger
    if (next.returnTo.kind === 'overview' || next.returnTo.kind === 'day') {
      dashboardTriggerRef.current = trigger
    }
    pendingFocusRestoreRef.current = false
    setView(next)
    setValidation(null)
    setApiError(null)
    if (next.mode === 'edit' && next.event) {
      const ev = next.event
      setForm({
        title: ev.title,
        eventType: ev.eventType,
        location: ev.location,
        startDate: ev.start.slice(0, 10),
        endDate: ev.end?.slice(0, 10) ?? '',
        notes: ev.notes ?? '',
        originalLocation: ev.location,
        originalCluster: ev.cluster,
      })
    } else {
      setForm({
        title: '',
        eventType: 'planned_task',
        location: DASHBOARD_ROW_ZONES[0]?.location ?? 'Flower Room',
        startDate: next.date,
        endDate: '',
        notes: '',
        originalLocation: DASHBOARD_ROW_ZONES[0]?.location ?? 'Flower Room',
      })
    }
  }
  const openEventDetails = (
    event: CalendarEventDto,
    returnTo: EventSourceView,
    trigger: HTMLElement
  ) => {
    dialogTriggerRef.current = trigger
    if (returnTo.kind === 'overview' || returnTo.kind === 'day') {
      dashboardTriggerRef.current = trigger
    }
    pendingFocusRestoreRef.current = false
    setView({ kind: 'event', event, returnTo })
  }

  const openEventList = (
    title: string,
    list: CalendarEventDto[],
    returnTo: InspectorBaseView,
    trigger: HTMLElement
  ) => {
    dialogTriggerRef.current = trigger
    dashboardTriggerRef.current = trigger
    pendingFocusRestoreRef.current = false
    setView({ kind: 'events', title, events: list, returnTo })
  }

  const updateForm = (patch: Partial<EventFormState>) => {
    setForm(prev => (prev ? { ...prev, ...patch } : prev))
  }

  const submitForm = async () => {
    if (!form || view.kind !== 'form') return
    const title = form.title.trim()
    const eventType = form.eventType.trim()
    const location = form.location.trim()
    const startDate = form.startDate.trim()
    if (!title) return setValidation('Title is required.')
    if (!eventType) return setValidation('Event type is required.')
    if (!location) return setValidation('Location is required.')
    if (!startDate) return setValidation('Start date is required.')
    if (form.endDate && form.endDate < startDate) {
      return setValidation('End date must be on or after the start date.')
    }
    setValidation(null)
    setApiError(null)
    setPending(true)
    try {
      let saved: CalendarEventDto
      if (view.mode === 'create') {
        const body: CalendarEventCreate = {
          title,
          event_type: eventType,
          location,
          start_date: startDate,
          end_date: form.endDate || null,
          notes: form.notes.trim() ? form.notes.trim() : null,
          cluster: 'main',
          all_day: true,
        }
        saved = await apiClient.createCalendarEvent(body)
      } else {
        const numericId = view.event?.numericId
        if (typeof numericId !== 'number') {
          setApiError('This event cannot be edited (missing numeric id).')
          return
        }
        const cluster =
          location !== form.originalLocation ? 'main' : (form.originalCluster ?? 'main')
        const body: CalendarEventUpdate = {
          title,
          event_type: eventType,
          location,
          start_date: startDate,
          end_date: form.endDate || null,
          notes: form.notes.trim() ? form.notes.trim() : null,
          cluster,
        }
        saved = await apiClient.updateCalendarEvent(numericId, body)
      }
      const returnTo = view.returnTo.kind === 'event' ? view.returnTo.returnTo : view.returnTo
      setView({ kind: 'event', event: saved, returnTo })
      setForm(null)
      void onRefresh()
    } catch (e) {
      setApiError(e instanceof Error ? e.message : 'Failed to save event.')
    } finally {
      setPending(false)
    }
  }

  const cancelForm = () => {
    setForm(null)
    setValidation(null)
    setApiError(null)
    pendingFocusRestoreRef.current = true
    if (view.kind === 'form') setView(view.returnTo)
  }

  const closeDialog = () => {
    if (view.kind === 'form') {
      cancelForm()
      return
    }
    if (view.kind === 'events' || view.kind === 'event') {
      pendingFocusRestoreRef.current = true
      setView(view.returnTo)
    }
  }

  const handleCloseAutoFocus = (event: Event) => {
    if (!pendingFocusRestoreRef.current) return
    pendingFocusRestoreRef.current = false
    const trigger = dialogTriggerRef.current?.isConnected
      ? dialogTriggerRef.current
      : dashboardTriggerRef.current
    if (!trigger?.isConnected) return
    event.preventDefault()
    trigger.focus()
  }

  const renderOverview = () => (
    <div className="dashboard-inspector__overview flex flex-col gap-2">
      <div className="dashboard-inspector__overview-summary flex flex-col gap-2">
        <section aria-label="Today">
          <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted mb-1">Today</h3>
          {todayTasks.length === 0 ? (
            <p className="text-xs text-text-muted">No tasks today</p>
          ) : (
            <ul className="dashboard-inspector__today-events space-y-1">
              {todayTasks.slice(0, 3).map(ev => (
                <li key={ev.id}>
                  <button
                    type="button"
                    className="w-full text-left text-xs text-text-default truncate"
                    title={ev.title}
                    onClick={event =>
                      openEventDetails(ev, { kind: 'overview' }, event.currentTarget)
                    }
                  >
                    {ev.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {todayTasks.length > 3 && (
            <button
              type="button"
              className="grow-cal-toolbar-btn mt-1"
              onClick={event =>
                openEventList('Today events', todayTasks, { kind: 'overview' }, event.currentTarget)
              }
            >
              All {todayTasks.length} events
            </button>
          )}
        </section>
        <section aria-label="Grow phases">
          <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted mb-1">Phase</h3>
          <p className="text-xs text-text-default">
            {activePhase ? (
              <>
                <span className="font-semibold">{activePhase.title}</span>{' '}
                <span className="text-text-muted">
                  (ends{' '}
                  {relativeDayLabel(
                    dayKeyNow,
                    calendarEventDayKey(activePhase.end ?? activePhase.start)
                  )}
                  )
                </span>
              </>
            ) : (
              <span className="text-text-muted">No active phase</span>
            )}
          </p>
          <p className="text-xs text-text-default mt-1">
            {upcomingPhase ? (
              <>
                <span className="text-text-muted">Next:</span>{' '}
                <span className="font-semibold">{upcomingPhase.title}</span>{' '}
                <span className="text-text-muted">
                  in {relativeDayLabel(dayKeyNow, calendarEventDayKey(upcomingPhase.start))}
                </span>
              </>
            ) : (
              <span className="text-text-muted">No upcoming phase in loaded range</span>
            )}
          </p>
        </section>
      </div>
      <div className="dashboard-inspector__overview-actions flex flex-col gap-1 mt-auto pt-2">
        <button
          type="button"
          className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary"
          onClick={event =>
            openForm(
              {
                kind: 'form',
                mode: 'create',
                date: format(zonedNow(now), 'yyyy-MM-dd'),
                returnTo: { kind: 'overview' },
              },
              event.currentTarget
            )
          }
        >
          New event
        </button>
        <button type="button" className="grow-cal-toolbar-btn" onClick={onOpenGrowPlan}>
          Create flower grow plan
        </button>
      </div>
    </div>
  )

  const renderDay = () => (
    <div className="dashboard-inspector__day flex flex-col gap-2">
      <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted">
        {selectedDate
          ? format(toZonedTime(selectedDate, CALENDAR_TZ), 'EEE d MMM', { locale: fr })
          : ''}
      </h3>
      {selectedDateEvents.length === 0 ? (
        <p className="text-xs text-text-muted">No events on this day</p>
      ) : (
        <ul className="dashboard-inspector__event-summary space-y-1">
          {selectedDateEvents.slice(0, 3).map(ev => (
            <li key={ev.id}>
              <button
                type="button"
                className="w-full text-left text-xs bg-surface-base border border-border-default rounded-sm px-2 py-1.5 hover:bg-surface-secondary focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent-vivid"
                onClick={event => openEventDetails(ev, { kind: 'day' }, event.currentTarget)}
              >
                <span className="block font-semibold text-text-default truncate">{ev.title}</span>
                <span className="block text-text-muted">
                  {ev.eventType} · {ev.location}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {selectedDateEvents.length > 3 && (
        <button
          type="button"
          className="grow-cal-toolbar-btn"
          onClick={event =>
            openEventList(
              'Selected day events',
              selectedDateEvents,
              { kind: 'day' },
              event.currentTarget
            )
          }
        >
          All {selectedDateEvents.length} events
        </button>
      )}
      <button
        type="button"
        className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary mt-auto"
        onClick={event =>
          selectedDateKey &&
          openForm(
            {
              kind: 'form',
              mode: 'create',
              date: selectedDateKey,
              returnTo: { kind: 'day' },
            },
            event.currentTarget
          )
        }
      >
        New event on this date
      </button>
    </div>
  )

  const renderEventList = (list: EventListView) => (
    <ul className="dashboard-inspector__dialog-event-list space-y-2">
      {list.events.map(ev => (
        <li key={ev.id}>
          <button
            type="button"
            className="w-full text-left text-xs bg-surface-base border border-border-default rounded-sm px-2 py-1.5 hover:bg-surface-secondary focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent-vivid"
            onClick={event => openEventDetails(ev, list, event.currentTarget)}
          >
            <span className="block font-semibold text-text-default break-words">{ev.title}</span>
            <span className="block text-text-muted">
              {ev.eventType} · {ev.location} · {dateRangeLabel(ev)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )

  const renderEvent = (ev: CalendarEventDto) => (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-bold text-text-default break-words">{ev.title}</h3>
      <dl className="text-xs space-y-1">
        <div>
          <dt className="inline text-text-muted">Type: </dt>
          <dd className="inline text-text-default">{ev.eventType}</dd>
        </div>
        <div>
          <dt className="inline text-text-muted">Source: </dt>
          <dd className="inline text-text-default">{ev.source}</dd>
        </div>
        <div>
          <dt className="inline text-text-muted">Room: </dt>
          <dd className="inline text-text-default">{ev.location}</dd>
        </div>
        <div>
          <dt className="inline text-text-muted">Dates: </dt>
          <dd className="inline text-text-default font-mono tabular-nums">{dateRangeLabel(ev)}</dd>
        </div>
        {ev.notes && (
          <div>
            <dt className="text-text-muted">Notes:</dt>
            <dd className="text-text-default whitespace-pre-wrap break-words">{ev.notes}</dd>
          </div>
        )}
      </dl>
      {ev.sync_status && (
        <p role="status" className="text-xs text-text-secondary">
          {ev.sync_status === 'synced'
            ? `Confirmed by Nextcloud${ev.last_synced_at ? ` · ${ev.last_synced_at}` : ''}`
            : ev.sync_status === 'pending_push' || ev.sync_status === 'pending_delete'
              ? 'Saved locally—awaiting Nextcloud'
              : ev.sync_status === 'conflict'
                ? `Nextcloud conflict${ev.sync_error_code ? ` · ${ev.sync_error_code}` : ''}`
                : `Nextcloud unavailable${ev.sync_error_code ? ` · ${ev.sync_error_code}` : ''}`}
        </p>
      )}
      {selectedGrowId === ev.growPlanId && lifecycle && (
        <section className="flex flex-col gap-2 border-t border-border-subtle pt-2">
          <h4 className="text-xs font-semibold text-text-default">Grow lifecycle: {lifecycle.state}</h4>
          {lifecycle.state === 'closed' ? (
            <button
              type="button"
              disabled={pending}
              className="grow-cal-toolbar-btn"
              onClick={async () => {
                setPending(true)
                try {
                  setLifecycle(await apiClient.reopenGrow(ev.growPlanId!, lifecycle.calendar_revision))
                  setClosePreview(null)
                  await onRefresh()
                } catch (error) {
                  setApiError(calendarErrorCode(error) ?? 'Unable to reopen grow.')
                } finally {
                  setPending(false)
                }
              }}
            >
              Reopen grow
            </button>
          ) : (
            <>
              <button
                type="button"
                disabled={pending}
                className="grow-cal-toolbar-btn"
                onClick={async () => {
                  setPending(true)
                  setApiError(null)
                  try {
                    setClosePreview(await apiClient.previewGrowClose(ev.growPlanId!))
                  } catch (error) {
                    setApiError(calendarErrorCode(error) ?? 'Unable to preview closure.')
                  } finally {
                    setPending(false)
                  }
                }}
              >
                Preview close
              </button>
              {closePreview && (
                <div className="text-xs text-text-secondary space-y-1">
                  <p>Drying ends: {closePreview.drying_end ?? 'Missing'}</p>
                  <p>Observation counts: {Object.entries(closePreview.affected_counts).map(([name, count]) => `${name}: ${JSON.stringify(count)}`).join(', ') || 'None reported'}</p>
                  {closePreview.gaps.map((gap, index) => (
                    <p key={index}>Coverage gap: {JSON.stringify(gap)}</p>
                  ))}
                  {closePreview.scope_unresolved.map((scope, index) => (
                    <p key={index} role="alert">Unresolved scope: {JSON.stringify(scope)}</p>
                  ))}
                  {closePreview.scope_unresolved.length > 0 && (
                    <form
                      className="space-y-1 border-t border-border-subtle pt-1"
                      onSubmit={async event => {
                        event.preventDefault()
                        const identityId = Number(scopeIdentity)
                        if (!Number.isInteger(identityId) || identityId <= 0 || !scopeStart || !scopeEnd || scopeEnd <= scopeStart) {
                          setApiError('Enter a valid source ID and date-bounded half-open interval.')
                          return
                        }
                        setPending(true)
                        try {
                          await apiClient.createArchiveScopeCorrection({
                            dataset_id: closePreview.lifecycle.dataset_id,
                            expected_revision: closePreview.lifecycle.dataset_id ? closePreview.lifecycle.data_revision : null,
                            source_table: 'measurement',
                            source_identity: { sensor_id: identityId },
                            start: new Date(scopeStart).toISOString(),
                            end: new Date(scopeEnd).toISOString(),
                            room: scopeRoom,
                          })
                          setClosePreview(await apiClient.previewGrowClose(ev.growPlanId!))
                        } catch (error) {
                          setApiError(calendarErrorCode(error) ?? 'Scope correction failed.')
                        } finally {
                          setPending(false)
                        }
                      }}
                    >
                      <p>Correct unresolved room assignment (reopen first for grow-associated changes).</p>
                      <input aria-label="Source identity ID" inputMode="numeric" value={scopeIdentity} onChange={event => setScopeIdentity(event.target.value)} />
                      <label>Start <input type="datetime-local" required value={scopeStart} onChange={event => setScopeStart(event.target.value)} /></label>
                      <label>End <input type="datetime-local" required value={scopeEnd} onChange={event => setScopeEnd(event.target.value)} /></label>
                      <select aria-label="Assigned room" value={scopeRoom} onChange={event => setScopeRoom(event.target.value)}>
                        <option>Flower Room</option>
                        <option>Veg Room</option>
                      </select>
                      <button type="submit" disabled={pending} className="grow-cal-toolbar-btn">Save scope correction</button>
                    </form>
                  )}
                  {closePreview.conflicts.map((conflict, index) => (
                    <p key={index} role="alert">Conflict: {JSON.stringify(conflict)}</p>
                  ))}
                  <button
                    type="button"
                    disabled={pending || closePreview.conflicts.length > 0 || closePreview.scope_unresolved.length > 0}
                    className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary"
                    onClick={async () => {
                      setPending(true)
                      setApiError(null)
                      try {
                        setLifecycle(await apiClient.closeGrow(ev.growPlanId!, closePreview.revision))
                        setClosePreview(null)
                        await onRefresh()
                      } catch (error) {
                        setApiError(calendarErrorCode(error) ?? 'Unable to close grow.')
                      } finally {
                        setPending(false)
                      }
                    }}
                  >
                    Close grow
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      )}
      {eventIsEditable(ev) && (
        <button
          type="button"
          className="grow-cal-toolbar-btn mt-auto"
          onClick={event =>
            openForm(
              {
                kind: 'form',
                mode: 'edit',
                event: ev,
                date: ev.start.slice(0, 10),
                returnTo: view.kind === 'event' ? view : { kind: 'day' },
              },
              event.currentTarget
            )
          }
        >
          Edit
        </button>
      )}
    </div>
  )

  const renderForm = () => {
    if (view.kind !== 'form' || !form) return null
    const mode = view.mode
    const rooms = locationOptions(mode === 'edit' ? form.originalLocation : undefined)
    return (
      <form
        noValidate
        className="flex flex-col gap-1.5"
        onSubmit={e => {
          e.preventDefault()
          void submitForm()
        }}
      >
        <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted">
          {mode === 'create' ? 'New event' : 'Edit event'}
        </h3>
        <label className="text-xs text-text-secondary">
          Title
          <input
            type="text"
            value={form.title}
            onChange={e => updateForm({ title: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Event type
          <input
            type="text"
            value={form.eventType}
            onChange={e => updateForm({ eventType: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Location
          <select
            value={form.location}
            onChange={e => updateForm({ location: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          >
            {rooms.map(room => (
              <option key={room} value={room}>
                {room}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-text-secondary">
          Start date
          <input
            type="date"
            required
            value={form.startDate}
            onChange={e => updateForm({ startDate: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default font-mono tabular-nums"
          />
        </label>
        <label className="text-xs text-text-secondary">
          End date (optional)
          <input
            type="date"
            value={form.endDate}
            min={form.startDate || undefined}
            onChange={e => updateForm({ endDate: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default font-mono tabular-nums"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Notes
          <textarea
            value={form.notes}
            rows={2}
            onChange={e => updateForm({ notes: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          />
        </label>
        {validation && (
          <p role="alert" className="text-xs text-status-danger-text">
            {validation}
          </p>
        )}
        {apiError && (
          <p role="alert" className="text-xs text-status-danger-text">
            {apiError}
          </p>
        )}
        <div className="flex gap-1 mt-auto pt-1">
          <button
            type="submit"
            disabled={pending || Boolean(validation)}
            className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary flex-1 disabled:opacity-50"
          >
            {pending ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            onClick={cancelForm}
            disabled={pending}
            className="grow-cal-toolbar-btn flex-1 disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </form>
    )
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={open => !open && closeDialog()}>
      <div
        role="complementary"
        aria-label="Calendar inspector"
        className="dashboard-inspector flex flex-col bg-surface-primary border border-border-subtle rounded-lg p-2 gap-1"
      >
        {baseView.kind === 'overview' && renderOverview()}
        {baseView.kind === 'day' && renderDay()}
      </div>
      <DialogContent
        ref={dialogContentRef}
        onCloseAutoFocus={handleCloseAutoFocus}
        className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto overscroll-contain max-w-xl"
      >
        <DialogTitle className="mb-3">
          {view.kind === 'form'
            ? view.mode === 'create'
              ? 'New event'
              : 'Edit event'
            : view.kind === 'events'
              ? view.title
              : view.kind === 'event'
                ? 'Event details'
                : 'Calendar details'}
        </DialogTitle>
        {view.kind === 'events' && renderEventList(view)}
        {view.kind === 'event' && renderEvent(view.event)}
        {view.kind === 'form' && renderForm()}
      </DialogContent>
    </Dialog>
  )
}
