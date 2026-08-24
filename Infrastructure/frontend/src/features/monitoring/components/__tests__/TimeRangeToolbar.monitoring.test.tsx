import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { TimeRangeToolbar } from '../TimeRangeToolbar'

describe('TimeRangeToolbar monitoring status', () => {
  it('embeds monitoring summary and retry actions without duplicating toolbar pause controls', () => {
    const onRetry = vi.fn<() => void>()
    const { container } = render(
      <MemoryRouter>
        <TimeRangeToolbar
          range={{ kind: 'live', duration: 3 * 3600_000 }}
          isLive
          onLive={vi.fn<(duration: number) => void>()}
          onFixedRange={vi.fn<(start: Date, end: Date) => void>()}
          onPause={vi.fn<() => void>()}
          onResume={vi.fn<() => void>()}
          onResetZoom={vi.fn<() => void>()}
          monitoring={{
            errors: [],
            tailLoading: false,
            reconciling: false,
            anchorQuality: 'exact',
            projectionRevision: 'revision-1',
            runtimeSnapshotVersion: 1,
            onRetry,
          }}
        />
      </MemoryRouter>,
    )

    const toolbar = container.querySelector('.mon-toolbar')
    expect(toolbar).not.toBeNull()
    expect(toolbar?.querySelector('.mon-status__summary')).not.toBeNull()
    expect(toolbar?.querySelector('.mon-status__actions')).not.toBeNull()
    expect(screen.getAllByRole('button', { name: 'Pause' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})
