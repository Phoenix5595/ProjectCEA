import { fireEvent, render, screen } from '@testing-library/react'
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ControlTimeline } from '../components/ControlTimeline'
import { TimelineEditor } from '../components/TimelineEditor'
import { RichTrajectoryEnvelope } from '../api/contracts'
import type { TimelinePublicationPort } from '../api/timelinePublicationPort'
import { useTimelineDraft } from '../state/useTimelineDraft'
import type { TimelineSavedBaseline } from '../state/timelineDraft'

const baseline = (): TimelineSavedBaseline => ({
  room: { location: 'flower', cluster: 'main' },
  baseConfigRevision: '0000007',
  modeId: 1,
  submodeId: null,
  periods: [{
    period_name: 'Day', start_time: '06:00', end_time: '18:00', ramp_minutes: 30,
    heating_setpoint: 22, cooling_setpoint: 25, vpd_setpoint: 1.2, co2_setpoint: 900, details: 'saved',
  }],
  photoperiod: { dayStartTime: '06:00', nightStartTime: '18:00', rampUpMinutes: 20, rampDownMinutes: 20 },
  window: { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'America/Toronto' },
})

const preview = (revision: number) => RichTrajectoryEnvelope.parse({
  contract_version: 1,
  room: 'Flower Room',
  generated_at: '2026-01-01T00:00:00.000Z',
  window: { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'America/Toronto' },
  revision_scope: 'draft', base_config_revision: '0000007', draft_revision: String(revision),
  segments: [{
    shape: 'step', value: 22, start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z',
    metric: 'temperature', unit: 'celsius', trajectory_kind: 'scheduled', quality: 'exact',
    source: { mode: 'flower', submode: null, period: { period_id: 'day', label: 'Day' }, config_revision: '0000007', draft_revision: String(revision) },
  }], assumptions: [], warnings: [],
})

function savedTrajectory(warnings: readonly Record<string, unknown>[] = []) {
  const draft = preview(0)
  return RichTrajectoryEnvelope.parse({
    contract_version: draft.contract_version,
    room: draft.room,
    generated_at: draft.generated_at.toISOString(),
    window: {
      start: draft.window.start.toISOString(),
      end: draft.window.end.toISOString(),
      timezone: draft.window.timezone,
    },
    revision_scope: 'saved',
    base_config_revision: draft.base_config_revision,
    draft_revision: null,
    segments: draft.segments.map((segment) => ({
      ...segment,
      start: segment.start.toISOString(),
      end: segment.end.toISOString(),
      source: { ...segment.source, draft_revision: null },
    })),
    assumptions: [],
    warnings: [...warnings],
  })
}

const skippedWarning = {
  code: 'calendar.transition_skipped',
  detail: 'Calendar destination flower/bulk was skipped',
  reason: 'unknown_mode',
  start: '2026-01-01T06:00:00.000Z',
  end: '2026-01-01T12:00:00.000Z',
}

function port(): TimelinePublicationPort {
  return {
    preview: async (request) => preview(request.draftRevision),
    apply: async (request) => ({ room: request.room, baseConfigRevision: '0000008', ...request.values }),
  }
}

describe('ControlTimeline', () => {
  it('keeps compact pointer input read-only', () => {
    // Given: a saved timeline rendered in compact mode.
    const { result } = renderHook(() => useTimelineDraft({ saved: baseline(), publicationPort: port() }))
    render(<ControlTimeline mode="compact" controller={result.current} />)

    // When: the operator presses the compact plot.
    fireEvent.mouseDown(screen.getByTestId('control-timeline-plot'), { clientX: 720 })
    fireEvent.mouseMove(window, { clientX: 960 })

    // Then: no editable handle or draft revision is created.
    expect(screen.queryByTestId('control-timeline-handle-0-start')).not.toBeInTheDocument()
    expect(result.current.state.draftRevision).toBe(0)
  })

  it('renders the skipped-calendar overlay over the received interval in compact mode', () => {
    // Given: a saved previous-mode trajectory with a calendar skip interval.
    const saved = { ...baseline(), trajectory: savedTrajectory([skippedWarning]) }
    const { result } = renderHook(() => useTimelineDraft({ saved, publicationPort: port() }))

    // When: the operator views the compact timeline.
    render(<ControlTimeline mode="compact" controller={result.current} />)

    // Then: reality remains visible and the error band uses the warning bounds and reason.
    expect(screen.getByText('Day')).toBeInTheDocument()
    const overlay = screen.getByTestId('calendar-transition-skipped-overlay')
    expect(overlay).toHaveStyle({ left: '25%', width: '25%' })
    expect(screen.getByText('Calendar transition skipped: unknown_mode')).toBeInTheDocument()
  })

  it('renders the same skipped-calendar overlay in expanded mode', () => {
    // Given: an expanded editor backed by a saved trajectory warning.
    const saved = { ...baseline(), trajectory: savedTrajectory([skippedWarning]) }
    const { result } = renderHook(() => useTimelineDraft({ saved, publicationPort: port() }))

    // When: the expanded timeline is rendered.
    render(<ControlTimeline mode="expanded" controller={result.current} />)

    // Then: editing remains available while the warning stays visible.
    expect(screen.getByTestId('control-timeline-handle-0-start')).toBeInTheDocument()
    expect(screen.getByTestId('calendar-transition-skipped-overlay')).toBeInTheDocument()
    expect(screen.getByText('Calendar transition skipped: unknown_mode')).toBeInTheDocument()
  })

  it('does not render a skipped-calendar overlay for a plain trajectory', () => {
    // Given: a saved trajectory without warnings.
    const saved = { ...baseline(), trajectory: savedTrajectory() }
    const { result } = renderHook(() => useTimelineDraft({ saved, publicationPort: port() }))

    // When: the compact timeline is rendered.
    render(<ControlTimeline mode="compact" controller={result.current} />)

    // Then: no error overlay or skipped-transition message is shown.
    expect(screen.queryByTestId('calendar-transition-skipped-overlay')).not.toBeInTheDocument()
    expect(screen.queryByText(/calendar transition skipped/i)).not.toBeInTheDocument()
  })

  it('shows an unknown warning generically without rendering a calendar overlay', () => {
    // Given: a saved trajectory with a warning code unknown to the timeline UI.
    const saved = {
      ...baseline(),
      trajectory: savedTrajectory([{ code: 'future.warning', detail: 'A future warning detail' }]),
    }
    const { result } = renderHook(() => useTimelineDraft({ saved, publicationPort: port() }))

    // When: the compact timeline renders that envelope.
    render(<ControlTimeline mode="compact" controller={result.current} />)

    // Then: the warning remains safe and generic, with no calendar-specific band.
    expect(screen.queryByTestId('calendar-transition-skipped-overlay')).not.toBeInTheDocument()
    expect(screen.getByText('Timeline warning: A future warning detail')).toBeInTheDocument()
  })

  it('synchronizes an expanded keyboard boundary edit with the table adapter', () => {
    // Given: the expanded editor and the existing table share one draft controller.
    const { result } = renderHook(() => useTimelineDraft({ saved: baseline(), publicationPort: port() }))
    render(<ControlTimeline mode="expanded" controller={result.current} />)

    // When: the scheduled start handle receives one five-minute keyboard step.
    fireEvent.keyDown(screen.getByTestId('control-timeline-handle-0-start'), { key: 'ArrowLeft' })

    // Then: the shared draft carries the exact new table value.
    expect(result.current.state.draft.periods[0]?.start_time).toBe('05:55')
  })

  it('shows review changes and discard restores saved values', async () => {
    // Given: an expanded editor with a dirty draft.
    const { result } = renderHook(() => useTimelineDraft({ saved: baseline(), publicationPort: port() }))
    const view = render(<ControlTimeline mode="expanded" controller={result.current} />)
    act(() => result.current.editPeriods([{ ...result.current.state.draft.periods[0], heating_setpoint: 23.5 }]))
    view.rerender(<ControlTimeline mode="expanded" controller={result.current} />)

    // When: the operator reviews, then cancels the draft.
    await act(async () => result.current.review())
    view.rerender(<ControlTimeline mode="expanded" controller={result.current} />)
    expect(screen.getByText(/draft changes/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))

    // Then: the saved value is restored and the draft marker disappears.
    expect(result.current.state.draft.periods[0]?.heating_setpoint).toBe(22)
    expect(result.current.state.status).toEqual({ kind: 'editing' })
  })

  it('renders the compact timeline visual without embedding the periods table', () => {
    // Given: the product editor is mounted in its default compact state.
    // The permanent ClimatePeriodsTable lives on the ZoneConfig row beneath it.
    render(<TimelineEditor saved={baseline()} />)

    // When: the operator inspects the control page.
    const table = screen.queryByRole('table')

    // Then: the timeline visual hosts no second table and the expand control is present.
    expect(table).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /expand editor/i })).toBeInTheDocument()
  })

  it('ignores late preview callbacks after discard and room switch', async () => {
    let resolvePreview: ((value: ReturnType<typeof preview>) => void) | undefined
    const deferredPort: TimelinePublicationPort = {
      preview: () => new Promise((resolve) => { resolvePreview = resolve }),
      apply: async () => baseline(),
    }
    const { result } = renderHook(() => useTimelineDraft({ saved: baseline(), publicationPort: deferredPort }))
    let review: Promise<void> | undefined
    act(() => { review = result.current.review() })
    act(() => result.current.discard())
    await act(async () => { resolvePreview?.(preview(0)) })
    await review
    expect(result.current.preview).toEqual({ kind: 'idle' })

    act(() => { review = result.current.review() })
    act(() => result.current.confirmRoomSwitch({ ...baseline(), room: { location: 'vegetation', cluster: 'main' } }))
    await act(async () => { resolvePreview?.(preview(0)) })
    await review
    expect(result.current.preview).toEqual({ kind: 'idle' })
    expect(result.current.state.saved.room.location).toBe('vegetation')
  })
})
