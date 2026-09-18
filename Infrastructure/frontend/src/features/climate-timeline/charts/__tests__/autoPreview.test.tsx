import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { ControlTimeline, AUTO_PREVIEW_DEBOUNCE_MS } from '../../components/ControlTimeline'
import { useTimelineDraft } from '../../state/useTimelineDraft'
import { RichTrajectoryEnvelope } from '../../api/contracts'
import type { TimelinePublicationPort } from '../../api/timelinePublicationPort'
import type { TimelineSavedBaseline } from '../../state/timelineDraft'

const WINDOW = { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'UTC' }

const savedBaseline = (): TimelineSavedBaseline => ({
  room: { location: 'flower', cluster: 'main' },
  baseConfigRevision: 'config-1',
  modeId: 1,
  submodeId: null,
  window: WINDOW,
  periods: [{
    period_name: 'Day', start_time: '06:00', end_time: '18:00', ramp_minutes: 0,
    heating_setpoint: 22, cooling_setpoint: 28, vpd_setpoint: 1.1, co2_setpoint: 900, details: '',
  }],
  photoperiod: { dayStartTime: '06:00', nightStartTime: '18:00', rampUpMinutes: 0, rampDownMinutes: 0 },
  trajectory: previewEnvelope(0, '2026-01-01T00:00:00.000Z'),
})

function previewEnvelope(draftRevision: number, generatedAt: string): RichTrajectoryEnvelope {
  return RichTrajectoryEnvelope.parse({
    contract_version: 1,
    room: 'Flower Room',
    generated_at: generatedAt,
    window: WINDOW,
    revision_scope: 'draft',
    base_config_revision: 'config-1',
    draft_revision: `draft-${draftRevision}`,
    segments: [{
      shape: 'step',
      value: 22,
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-01-02T00:00:00.000Z',
      metric: 'heating_setpoint',
      unit: 'C',
      trajectory_kind: 'scheduled',
      quality: 'exact',
      source: {
        mode: 'flower',
        submode: null,
        period: { period_id: 'day', label: 'Day' },
        config_revision: 'config-1',
        draft_revision: `draft-${draftRevision}`,
      },
    }],
    assumptions: [],
    warnings: [],
  })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('debounced automatic draft previews', () => {
  it('fires exactly one preview after a burst of ten edits and reports it ready', async () => {
    vi.useFakeTimers()
    let previewCalls = 0
    const port: TimelinePublicationPort = {
      preview: async (request) => {
        previewCalls += 1
        return previewEnvelope(request.draftRevision, '2026-01-01T00:01:00.000Z')
      },
      apply: async () => savedBaseline(),
    }
    const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    const view = render(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    for (let index = 0; index < 10; index += 1) {
      await act(async () => {
        hook.result.current.editPeriods(hook.result.current.state.draft.periods.map((period) => (
          { ...period, heating_setpoint: 22 + (index + 1) * 0.1 }
        )))
      })
      await act(async () => {
        view.rerender(<ControlTimeline mode="expanded" controller={hook.result.current} />)
      })
    }

    act(() => { vi.advanceTimersByTime(AUTO_PREVIEW_DEBOUNCE_MS - 1) })
    expect(previewCalls).toBe(0)

    act(() => { vi.advanceTimersByTime(1) })
    await act(async () => { await Promise.resolve() })
    expect(previewCalls).toBe(1)
    expect(hook.result.current.preview).toMatchObject({ kind: 'ready', draftRevision: 10 })

    await act(async () => {
      for (let index = 0; index < 100; index += 1) {
        await Promise.resolve()
      }
    })
    expect(previewCalls).toBe(1)
  })

  it('does not auto-preview in compact mode', async () => {
    vi.useFakeTimers()
    let previewCalls = 0
    const port: TimelinePublicationPort = {
      preview: async (request) => {
        previewCalls += 1
        return previewEnvelope(request.draftRevision, '2026-01-01T00:01:00.000Z')
      },
      apply: async () => savedBaseline(),
    }
    const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    const view = render(<ControlTimeline mode="compact" controller={hook.result.current} />)

    await act(async () => {
      hook.result.current.editPeriods(hook.result.current.state.draft.periods.map((period) => ({ ...period, heating_setpoint: 23 })))
    })
    await act(async () => {
      view.rerender(<ControlTimeline mode="compact" controller={hook.result.current} />)
    })
    act(() => { vi.advanceTimersByTime(10_000) })

    expect(previewCalls).toBe(0)
  })

  it('marks the effective line stale while dirty without a current preview and clears it when the preview lands', async () => {
    vi.useFakeTimers()
    const port: TimelinePublicationPort = {
      preview: async (request) => previewEnvelope(request.draftRevision, '2026-01-01T00:01:00.000Z'),
      apply: async () => savedBaseline(),
    }
    const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    const view = render(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    await act(async () => {
      hook.result.current.editPeriods(hook.result.current.state.draft.periods.map((period) => ({ ...period, heating_setpoint: 23 })))
    })
    await act(async () => {
      view.rerender(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    })
    expect(screen.getByTestId('control-timeline-effective-stale')).toBeInTheDocument()

    act(() => { vi.advanceTimersByTime(AUTO_PREVIEW_DEBOUNCE_MS) })
    await act(async () => {
      for (let index = 0; index < 100; index += 1) await Promise.resolve()
    })
    view.rerender(<ControlTimeline mode="expanded" controller={hook.result.current} />)

    expect(hook.result.current.preview).toMatchObject({ kind: 'ready', draftRevision: 1 })
    expect(screen.queryByTestId('control-timeline-effective-stale')).not.toBeInTheDocument()
  })

  it('surfaces a failed auto-preview without faking success', async () => {
    vi.useFakeTimers()
    const port: TimelinePublicationPort = {
      preview: async (request) => {
        void request
        throw new Error('preview failed')
      },
      apply: async () => savedBaseline(),
    }
    const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    const view = render(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    await act(async () => {
      hook.result.current.editPeriods(hook.result.current.state.draft.periods.map((period) => ({ ...period, heating_setpoint: 23 })))
    })
    await act(async () => {
      view.rerender(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    })
    act(() => { vi.advanceTimersByTime(AUTO_PREVIEW_DEBOUNCE_MS) })
    await act(async () => {
      for (let index = 0; index < 100; index += 1) await Promise.resolve()
    })
    view.rerender(<ControlTimeline mode="expanded" controller={hook.result.current} />)

    expect(hook.result.current.preview).toMatchObject({ kind: 'failed', draftRevision: 1 })
    expect(screen.getByText(/Review failed for draft 1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
  })

  it('still triggers the auto-preview path after a drag-like edit burst in expanded mode', async () => {
    vi.useFakeTimers()
    let previewCalls = 0
    const port: TimelinePublicationPort = {
      preview: async (request) => {
        previewCalls += 1
        return previewEnvelope(request.draftRevision, '2026-01-01T00:01:00.000Z')
      },
      apply: async () => savedBaseline(),
    }
    const hook = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    const view = render(<ControlTimeline mode="expanded" controller={hook.result.current} />)
    for (let index = 0; index < 5; index += 1) {
      await act(async () => {
        hook.result.current.editPeriods(hook.result.current.state.draft.periods.map((period, periodIndex) => (
          periodIndex === 0 ? { ...period, ramp_minutes: index } : period
        )))
      })
      await act(async () => {
        view.rerender(<ControlTimeline mode="expanded" controller={hook.result.current} />)
      })
    }
    act(() => { vi.advanceTimersByTime(AUTO_PREVIEW_DEBOUNCE_MS) })
    await act(async () => {
      for (let index = 0; index < 100; index += 1) await Promise.resolve()
    })
    expect(previewCalls).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(hook.result.current.state.draft.periods[0]?.ramp_minutes).toBe(0)
  })
})
