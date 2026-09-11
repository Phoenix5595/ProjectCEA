import { fireEvent, render, screen, within } from '@testing-library/react'
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

  it('keeps ClimatePeriodsTable visible beneath compact timeline', () => {
    // Given: the product editor is mounted in its default compact state.
    render(<TimelineEditor saved={baseline()} />)

    // When: the operator inspects the control page.
    const table = screen.getByRole('table')

    // Then: the permanent primary editor is immediately discoverable.
    expect(within(table).getByText('Period')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /expand editor/i })).toBeInTheDocument()
  })
})
