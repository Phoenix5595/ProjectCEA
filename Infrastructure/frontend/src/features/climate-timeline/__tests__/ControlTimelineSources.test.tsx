import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import type { TimelineUPlotProps } from '../charts/TimelineUPlot'
import { ControlTimeline } from '../components/ControlTimeline'
import type { TimelinePreviewRequest, TimelinePreviewResult, TimelinePublicationPort } from '../api/timelinePublicationPort'
import type { TimelineSavedBaseline } from '../state/timelineDraft'
import { useTimelineDraft } from '../state/useTimelineDraft'
import { authorityCurrent, authorityEnvelope, authorityFuture, authorityNow, authorityPeriod, authorityWindow } from './timelineAuthorityFixtures'

const captured = vi.hoisted(() => ({ plot: null as TimelineUPlotProps | null }))
vi.mock('../charts/TimelineUPlot', () => ({ TimelineUPlot: (props: TimelineUPlotProps) => {
  captured.plot = props
  return <div aria-label={props.ariaLabel} />
} }))
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(authorityNow)) })
afterEach(() => vi.useRealTimers())
function saved(): TimelineSavedBaseline {
  return { room: { location: 'Flower Room', cluster: 'main' }, modeId: 4, submodeId: null,
    baseConfigRevision: '0000025', parametersConfigured: true, periods: [{ ...authorityPeriod, heating_setpoint: 18 }],
    photoperiod: { dayStartTime: '00:00', nightStartTime: '00:00', rampUpMinutes: 0, rampDownMinutes: 0 },
    window: { start: new Date(authorityWindow.start).toISOString(), end: new Date(authorityWindow.end).toISOString(), timezone: 'UTC' },
    trajectory: authorityEnvelope() }
}
function preview(request: TimelinePreviewRequest, numeric = true): TimelinePreviewResult {
  return { requestId: request.requestId, expectedConfigRevision: request.expectedConfigRevision,
    modeId: request.modeId, submodeId: request.submodeId, draftRevision: request.draftRevision, window: request.window,
    trajectory: numeric ? authorityEnvelope(19, request.draftRevision) : null }
}
const operational = { current: authorityCurrent(), future: [authorityFuture()], trajectory: null,
  currentError: null, projectionError: null, refresh: vi.fn(async () => {}) }
function values(role: string) {
  const plot = captured.plot!
  const index = plot.meta.findIndex(entry => entry.role === role && entry.metric === 'heating_setpoint') + 1
  return plot.data[index] ?? []
}
function port(numeric = true): TimelinePublicationPort {
  return { preview: async request => preview(request, numeric),
    apply: async request => ({ baseline: { ...saved(), ...request.values, baseConfigRevision: '0000026', trajectory: undefined }, warning: null }) }
}

describe('compact and expanded chart authority', () => {
  it('renders a newly received observation before the next display-clock tick', () => {
    const hook = renderHook(() => useTimelineDraft({ saved: saved(), publicationPort: port() }))
    const view = render(<ControlTimeline mode="compact" controller={hook.result.current} operationalProjection={operational} />)
    vi.setSystemTime(new Date(authorityNow + 750))
    const observedAt = new Date(authorityNow + 500)
    const current = authorityCurrent()
    const received = {
      ...current,
      observed_at: observedAt,
      series: current.series.map(point => ({ ...point, observed_at: observedAt })),
    }
    view.rerender(<ControlTimeline mode="compact" controller={hook.result.current}
      operationalProjection={{ ...operational, current: received }} />)
    expect(values('active-current')).toContain(22)
    expect(values('active-future')).toContain(24)
  })

  it('keeps operational sources visible without fabricating selected authority when metadata is unreadable', async () => {
    const publicationPort = port()
    const previewRead = vi.spyOn(publicationPort, 'preview')
    const hook = renderHook(() => useTimelineDraft({
      saved: { ...saved(), baseConfigRevision: '', parametersConfigured: false, periods: [], trajectory: undefined },
      publicationPort,
    }))
    render(<ControlTimeline mode="expanded" controller={hook.result.current} operationalProjection={operational}
      selectedLabel="Drying" editingEnabled={false} />)
    expect(values('active-current')).toContain(22)
    expect(values('active-future')).toContain(24)
    expect(captured.plot?.meta.some(entry => entry.role === 'selected-saved' || entry.role === 'selected-draft')).toBe(false)
    expect(captured.plot?.photoperiod).toEqual([])
    expect(screen.getByRole('button', { name: 'Review' })).toBeDisabled()
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    expect(previewRead).not.toHaveBeenCalled()
  })

  it('keeps operational calendar overlays independent from selected warnings and failed drafts', () => {
    const selected = saved()
    selected.trajectory!.warnings.push({ code: 'calendar.transition_skipped', detail: 'selected warning', reason: 'selected_destination',
      start: new Date('2026-01-01T06:00:00.000Z'), end: new Date('2026-01-01T08:00:00.000Z') })
    const running = authorityEnvelope()
    running.warnings.push({ code: 'calendar.transition_skipped', detail: 'running warning', reason: 'running_destination',
      start: new Date('2026-01-01T14:00:00.000Z'), end: new Date('2026-01-01T16:00:00.000Z') })
    const hook = renderHook(() => useTimelineDraft({ saved: selected, publicationPort: port() }))
    render(<ControlTimeline mode="compact" controller={{ ...hook.result.current, preview: { kind: 'failed', draftRevision: 0 } }}
      operationalProjection={{ ...operational, trajectory: running }} />)
    expect(screen.getAllByTestId('calendar-transition-skipped-overlay')).toHaveLength(2)
    expect(screen.getByRole('img', { name: 'Calendar transition skipped: running_destination' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Calendar transition skipped: selected_destination' })).toBeInTheDocument()
    expect(values('active-current')).toContain(22)
  })

  it.each(['compact', 'expanded'] as const)('retains all four consumer values in %s while pending or failed review', mode => {
    const hook = renderHook(() => useTimelineDraft({ saved: saved(), publicationPort: port() }))
    act(() => hook.result.current.editPeriods([authorityPeriod]))
    const controller = hook.result.current
    const view = render(<ControlTimeline mode={mode} controller={controller} operationalProjection={operational} selectedLabel="Drying" activeLabel="Flower / Bulk" />)
    for (const pending of [{ kind: 'loading' as const, draftRevision: 1 }, { kind: 'failed' as const, draftRevision: 1 }]) {
      view.rerender(<ControlTimeline mode={mode} controller={{ ...controller, preview: pending }} operationalProjection={operational} selectedLabel="Drying" activeLabel="Flower / Bulk" />)
      expect(values('active-current')).toContain(22)
      expect(values('active-future')).toContain(24)
      expect(values('selected-saved')).toContain(18)
      expect(values('selected-draft')).toContain(19)
      expect(screen.getByTestId('control-timeline-period-legend')).toHaveTextContent('All day: 00:00–00:00')
    }
  })

  it('isolates stale room, profile, revision, draft and window previews instead of stretching them', () => {
    const hook = renderHook(() => useTimelineDraft({ saved: saved(), publicationPort: port() }))
    act(() => hook.result.current.editPeriods([authorityPeriod]))
    const controller = hook.result.current
    const request: TimelinePreviewRequest = { room: controller.state.saved.room, requestId: 'review-1', expectedConfigRevision: '0000025',
      draftRevision: 1, modeId: 4, submodeId: null, window: saved().window!, values: controller.state.draft }
    const result = preview(request)
    const view = render(<ControlTimeline mode="compact" controller={controller} operationalProjection={operational} />)
    for (const stale of [
      { ...result, modeId: 3 }, { ...result, submodeId: 2 }, { ...result, expectedConfigRevision: '0000024' },
      { ...result, draftRevision: 0 }, { ...result, window: { ...result.window, end: '2026-01-03T00:00:00.000Z' } },
      { ...result, trajectory: { ...authorityEnvelope(99, 1), room: 'Veg Room' } },
    ]) {
      view.rerender(<ControlTimeline mode="compact" controller={{ ...controller,
        preview: { kind: 'ready', source: 'preview', draftRevision: 1, request, result: stale } }} operationalProjection={operational} />)
      expect(values('selected-draft')).toContain(19)
      expect(values('selected-draft')).not.toContain(99)
      expect(values('active-current')).toContain(22)
      expect(values('selected-saved')).toContain(18)
    }
  })

  it('keeps NULL reviewed metadata savable without inventing a numeric draft forecast', async () => {
    const hook = renderHook(() => useTimelineDraft({ saved: saved(), publicationPort: port(false) }))
    const nullPeriod = { ...authorityPeriod, heating_setpoint: null }
    act(() => hook.result.current.editPeriods([nullPeriod]))
    await act(async () => { await hook.result.current.review() })
    render(<ControlTimeline mode="expanded" controller={hook.result.current} operationalProjection={operational} constantMode />)
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled()
    expect(values('active-current')).toContain(22)
    expect(values('selected-saved')).toContain(18)
    expect(captured.plot?.meta.some(entry => entry.role === 'selected-draft')).toBe(false)
    expect(screen.getByRole('textbox', { name: 'Day start' })).toBeDisabled()
    expect(screen.getByTestId('control-timeline-handle-0-start')).toBeDisabled()
    await act(async () => { await hook.result.current.apply() })
    expect(hook.result.current.state.saved.periods[0]?.heating_setpoint).toBeNull()
  })

  it('uses an explicitly estimated saved fallback and keeps operational values visible when editing is disabled', () => {
    const hook = renderHook(() => useTimelineDraft({ saved: { ...saved(), trajectory: undefined }, publicationPort: port() }))
    render(<ControlTimeline mode="expanded" controller={hook.result.current} operationalProjection={operational} selectedLabel="Drying" editingEnabled={false} />)
    expect(values('selected-saved')).toContain(18)
    expect(values('active-current')).toContain(22)
    expect(screen.getByRole('button', { name: 'Review' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Day start' })).toBeDisabled()
  })

  it('keeps the selected recurring values beyond the old envelope when switching to a different rolling window', () => {
    const now = authorityNow + 90 * 60_000
    vi.setSystemTime(now)
    const hook = renderHook(() => useTimelineDraft({ saved: saved(), publicationPort: port() }))
    const view = render(<ControlTimeline mode="compact" controller={hook.result.current} operationalProjection={operational} />)
    fireEvent.click(screen.getByRole('button', { name: 'rolling' }))
    const window = hook.result.current.state.saved.window!
    expect(Date.parse(window.start)).toBe(Math.floor(now / 30_000) * 30_000 - 12 * 3_600_000)
    expect(Date.parse(window.end) - Date.parse(window.start)).toBe(24 * 3_600_000)
    view.rerender(<ControlTimeline mode="compact" controller={hook.result.current} operationalProjection={operational} />)
    const afterOldEnd = Date.parse(saved().window!.end) + 30 * 60_000
    const x = (afterOldEnd - Date.parse(window.start)) / 60_000
    const plot = captured.plot!
    const series = plot.meta.findIndex(entry => entry.role === 'selected-saved' && entry.metric === 'heating_setpoint') + 1
    const index = plot.data[0].indexOf(x)
    expect(plot.data[series]?.[index]).toBe(18)
    expect(plot.qualities?.get(plot.meta[series - 1]!.key)?.[index]).toBe('estimated')
  })
})
