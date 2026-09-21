/** Dashboard-only calendar inspector: day summaries, event detail, manual create/edit. */
import { useEffect, useMemo, useState } from 'react';
import { format } from 'date-fns';
import { fr } from 'date-fns/locale';
import { toZonedTime } from 'date-fns-tz';

import { apiClient } from '../../services/api';
import type {
  CalendarEventCreate,
  CalendarEventDto,
  CalendarEventUpdate,
} from '../../types/calendar';
import { DASHBOARD_ROW_ZONES } from '../../config/zones';
import { CALENDAR_TZ } from '../../utils/flowerGrowPlan';
import {
  buildCalendarDayMarkers,
  calendarDayKey,
  calendarEventDayKey,
  calendarEventOccursOnDay,
  isGrowPlanPhase,
} from '../../utils/calendarDayMarkers';

export interface DashboardCalendarInspectorProps {
  events: CalendarEventDto[];
  now: Date;
  selectedDate: Date | undefined;
  selectedDateEvents: CalendarEventDto[];
  onRefresh: () => void | Promise<void>;
  onOpenGrowPlan: () => void;
}

/** Views below the top-level day selection; the form always returns to one of these. */
type EventSourceView =
  | { kind: 'overview' }
  | { kind: 'day' }
  | { kind: 'event'; event: CalendarEventDto };

type InspectorView =
  | EventSourceView
  | {
      kind: 'form';
      mode: 'create' | 'edit';
      /** Edit source event; undefined for create. */
      event?: CalendarEventDto;
      /** Preselected start date (yyyy-MM-dd) for create. */
      date: string;
      returnTo: EventSourceView;
    };

interface EventFormState {
  title: string;
  eventType: string;
  location: string;
  startDate: string;
  endDate: string;
  notes: string;
  originalLocation: string;
  originalCluster?: string;
}

function zonedNow(now: Date): Date {
  return toZonedTime(now, CALENDAR_TZ);
}

/** 'today' / 'tomorrow' / 'N days' between two yyyy-MM-dd keys. */
function relativeDayLabel(fromKey: string, toKey: string): string {
  const diff = Math.round(
    (Date.parse(`${toKey}T00:00:00Z`) - Date.parse(`${fromKey}T00:00:00Z`)) / 86400000
  );
  if (diff <= 0) return 'today';
  if (diff === 1) return 'tomorrow';
  return `${diff} days`;
}

function dateRangeLabel(ev: CalendarEventDto): string {
  if (ev.end && ev.end !== ev.start) return `${ev.start} → ${ev.end}`;
  return ev.start;
}

function eventIsEditable(ev: CalendarEventDto): boolean {
  return ev.source === 'manual' && ev.editable && typeof ev.numericId === 'number';
}

function locationOptions(edited?: string): string[] {
  const rooms = DASHBOARD_ROW_ZONES.map((z) => z.location);
  if (edited && edited.trim() && !rooms.includes(edited)) rooms.push(edited);
  return rooms;
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
  );
  const [form, setForm] = useState<EventFormState | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // A selectedDate prop change deterministically resets to the matching date view.
  useEffect(() => {
    setView(selectedDate ? { kind: 'day' } : { kind: 'overview' });
    setForm(null);
    setValidation(null);
    setApiError(null);
  }, [selectedDate]);

  const markers = useMemo(() => buildCalendarDayMarkers(events), [events]);

  const dayKeyNow = calendarDayKey(now);

  const activePhase = useMemo(() => {
    const found = events.filter(
      (ev) =>
        isGrowPlanPhase(ev) &&
        calendarEventOccursOnDay(ev, now)
    );
    if (found.length === 0) return undefined;
    return found.reduce((a, b) => (calendarEventDayKey(b.start) > calendarEventDayKey(a.start) ? b : a));
  }, [events, now]);

  const upcomingPhase = useMemo(() => {
    const startDay = (ev: CalendarEventDto) => calendarEventDayKey(ev.start);
    const later = events.filter(
      (ev) => isGrowPlanPhase(ev) && startDay(ev) > dayKeyNow
    );
    if (later.length === 0) return undefined;
    return later.reduce((a, b) => (startDay(b) < startDay(a) ? b : a));
  }, [events, dayKeyNow]);

  const todayTasks = markers.get(dayKeyNow)?.tasks ?? [];

  const selectedDateKey = selectedDate ? calendarDayKey(selectedDate) : undefined;

  const openForm = (next: InspectorView & { kind: 'form' }) => {
    setView(next);
    setValidation(null);
    setApiError(null);
    if (next.mode === 'edit' && next.event) {
      const ev = next.event;
      setForm({
        title: ev.title,
        eventType: ev.eventType,
        location: ev.location,
        startDate: ev.start.slice(0, 10),
        endDate: ev.end?.slice(0, 10) ?? '',
        notes: ev.notes ?? '',
        originalLocation: ev.location,
        originalCluster: ev.cluster,
      });
    } else {
      setForm({
        title: '',
        eventType: 'planned_task',
        location: DASHBOARD_ROW_ZONES[0]?.location ?? 'Flower Room',
        startDate: next.date,
        endDate: '',
        notes: '',
        originalLocation: DASHBOARD_ROW_ZONES[0]?.location ?? 'Flower Room',
      });
    }
  };

  const updateForm = (patch: Partial<EventFormState>) => {
    setForm((prev) => (prev ? { ...prev, ...patch } : prev));
  };

  const submitForm = async () => {
    if (!form || view.kind !== 'form') return;
    const title = form.title.trim();
    const eventType = form.eventType.trim();
    const location = form.location.trim();
    const startDate = form.startDate.trim();
    if (!title) return setValidation('Title is required.');
    if (!eventType) return setValidation('Event type is required.');
    if (!location) return setValidation('Location is required.');
    if (!startDate) return setValidation('Start date is required.');
    if (form.endDate && form.endDate < startDate) {
      return setValidation('End date must be on or after the start date.');
    }
    setValidation(null);
    setApiError(null);
    setPending(true);
    try {
      let saved: CalendarEventDto;
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
        };
        saved = await apiClient.createCalendarEvent(body);
      } else {
        const numericId = view.event?.numericId;
        if (typeof numericId !== 'number') {
          setApiError('This event cannot be edited (missing numeric id).');
          return;
        }
        const cluster =
          location !== form.originalLocation ? 'main' : (form.originalCluster ?? 'main');
        const body: CalendarEventUpdate = {
          title,
          event_type: eventType,
          location,
          start_date: startDate,
          end_date: form.endDate || null,
          notes: form.notes.trim() ? form.notes.trim() : null,
          cluster,
        };
        saved = await apiClient.updateCalendarEvent(numericId, body);
      }
      setView({ kind: 'event', event: saved });
      setForm(null);
      void onRefresh();
    } catch (e) {
      setApiError(e instanceof Error ? e.message : 'Failed to save event.');
    } finally {
      setPending(false);
    }
  };

  const cancelForm = () => {
    setForm(null);
    setValidation(null);
    setApiError(null);
    if (view.kind === 'form') setView(view.returnTo);
  };

  const renderOverview = () => (
    <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
      <section aria-label="Today">
        <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted mb-1">Today</h3>
        {todayTasks.length === 0 ? (
          <p className="text-xs text-text-muted">No tasks today</p>
        ) : (
          <ul className="space-y-1">
            {todayTasks.map((ev) => (
              <li key={ev.id} className="text-xs text-text-default truncate" title={ev.title}>
                {ev.title}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Grow phases">
        <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted mb-1">Phase</h3>
        <p className="text-xs text-text-default">
          {activePhase ? (
            <>
              <span className="font-semibold">{activePhase.title}</span>{' '}
              <span className="text-text-muted">
                (ends {relativeDayLabel(dayKeyNow, calendarEventDayKey(activePhase.end ?? activePhase.start))})
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
      <div className="flex flex-col gap-1 mt-auto pt-2">
        <button
          type="button"
          className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary"
          onClick={() =>
            openForm({
              kind: 'form',
              mode: 'create',
              date: format(zonedNow(now), 'yyyy-MM-dd'),
              returnTo: { kind: 'overview' },
            })
          }
        >
          New event
        </button>
        <button type="button" className="grow-cal-toolbar-btn" onClick={onOpenGrowPlan}>
          Create flower grow plan
        </button>
      </div>
    </div>
  );

  const renderDay = () => (
    <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
      <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted">
        {selectedDate ? format(toZonedTime(selectedDate, CALENDAR_TZ), 'EEE d MMM', { locale: fr }) : ''}
      </h3>
      {selectedDateEvents.length === 0 ? (
        <p className="text-xs text-text-muted">No events on this day</p>
      ) : (
        <ul className="space-y-1">
          {selectedDateEvents.map((ev) => (
            <li key={ev.id}>
              <button
                type="button"
                className="w-full text-left text-xs bg-surface-base border border-border-default rounded-sm px-2 py-1.5 hover:bg-surface-secondary focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                onClick={() => setView({ kind: 'event', event: ev })}
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
      <button
        type="button"
        className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary mt-auto"
        onClick={() =>
          selectedDateKey &&
          openForm({
            kind: 'form',
            mode: 'create',
            date: selectedDateKey,
            returnTo: { kind: 'day' },
          })
        }
      >
        New event on this date
      </button>
    </div>
  );

  const renderEvent = (ev: CalendarEventDto) => (
    <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
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
            <dd className="text-text-default whitespace-pre-wrap">{ev.notes}</dd>
          </div>
        )}
      </dl>
      {eventIsEditable(ev) && (
        <button
          type="button"
          className="grow-cal-toolbar-btn mt-auto"
          onClick={() =>
            openForm({ kind: 'form', mode: 'edit', event: ev, date: ev.start.slice(0, 10), returnTo: { kind: 'event', event: ev } })
          }
        >
          Edit
        </button>
      )}
    </div>
  );

  const renderForm = () => {
    if (view.kind !== 'form' || !form) return null;
    const mode = view.mode;
    const rooms = locationOptions(mode === 'edit' ? form.originalLocation : undefined);
    return (
      <form
        noValidate
        className="flex flex-col gap-1.5 min-h-0 overflow-y-auto"
        onSubmit={(e) => {
          e.preventDefault();
          void submitForm();
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
            onChange={(e) => updateForm({ title: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Event type
          <input
            type="text"
            value={form.eventType}
            onChange={(e) => updateForm({ eventType: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Location
          <select
            value={form.location}
            onChange={(e) => updateForm({ location: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default"
          >
            {rooms.map((room) => (
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
            onChange={(e) => updateForm({ startDate: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default font-mono tabular-nums"
          />
        </label>
        <label className="text-xs text-text-secondary">
          End date (optional)
          <input
            type="date"
            value={form.endDate}
            min={form.startDate || undefined}
            onChange={(e) => updateForm({ endDate: e.target.value })}
            className="mt-0.5 w-full bg-surface-base border border-border-default rounded-sm px-1.5 py-1 text-xs text-text-default font-mono tabular-nums"
          />
        </label>
        <label className="text-xs text-text-secondary">
          Notes
          <textarea
            value={form.notes}
            rows={2}
            onChange={(e) => updateForm({ notes: e.target.value })}
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
    );
  };

  return (
    <div
      role="complementary"
      aria-label="Calendar inspector"
      className="dashboard-inspector flex flex-col min-h-0 bg-surface-primary border border-border-subtle rounded-lg p-2 gap-1"
    >
      {view.kind === 'overview' && renderOverview()}
      {view.kind === 'day' && renderDay()}
      {view.kind === 'event' && renderEvent(view.event)}
      {view.kind === 'form' && renderForm()}
    </div>
  );
}
