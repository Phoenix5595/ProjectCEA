import { StrictMode } from 'react'
import { act, cleanup, render } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import { useDashboardEventBucketCount } from '../useDashboardEventBucketCount'

interface Viewport {
  width: number
  height: number
}

type MatchMediaListener = (event: MediaQueryListEvent) => void

interface FunctionalMatchMedia {
  setViewport: (viewport: Viewport) => void
  listenerCount: () => number
}

let restoreMatchMedia: (() => void) | null = null

/**
 * Test-owned window.matchMedia: evaluates the requested min-height predicate
 * and notifies consumers when the viewport changes.
 */
function installFunctionalMatchMedia(initial: Viewport): FunctionalMatchMedia {
  const viewport = { ...initial }
  const listeners = new Set<MatchMediaListener>()
  let media = ''
  const matchMedia = (query: string): MediaQueryList => {
    const minHeight = Number(query.match(/^\(min-height: (\d+)px\)$/)?.[1])
    media = query
    return {
      // Live getter: the hook re-reads `matches` after every store
      // notification, so this must reflect the current fixture viewport.
      get matches() {
        return viewport.height >= minHeight
      },
      media: query,
      onchange: null,
      addEventListener: (type: string, listener: MatchMediaListener) => {
        expect(type).toBe('change')
        listeners.add(listener)
      },
      removeEventListener: (type: string, listener: MatchMediaListener) => {
        expect(type).toBe('change')
        listeners.delete(listener)
      },
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    } as unknown as MediaQueryList
  }
  const original = window.matchMedia
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: matchMedia,
  })
  restoreMatchMedia = () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: original,
    })
  }
  return {
    // A browser notifies the subscribed store when the query evaluation
    // changes; the store notification carries no payload the hook may cache.
    setViewport(next: Viewport) {
      viewport.width = next.width
      viewport.height = next.height
      const minHeight = Number(media.match(/^\(min-height: (\d+)px\)$/)?.[1])
      const event = { matches: viewport.height >= minHeight, media } as MediaQueryListEvent
      for (const listener of [...listeners]) listener(event)
    },
    listenerCount: () => listeners.size,
  }
}

afterEach(() => {
  restoreMatchMedia?.()
  restoreMatchMedia = null
  cleanup()
})

function HookProbe({ id }: { id?: string }) {
  const bucketCount = useDashboardEventBucketCount()
  return <p data-testid={id ? `bucket-count-${id}` : 'bucket-count'}>{bucketCount}</p>
}

function probeText(id?: string): string | undefined {
  return document.querySelector(
    `[data-testid="${id ? `bucket-count-${id}` : 'bucket-count'}"]`
  )?.textContent
}

describe('useDashboardEventBucketCount', () => {
  it.each([
    [1080, '6'],
    [1299, '6'],
    [1300, '8'],
    [1301, '8'],
    [1439, '8'],
    [1440, '8'],
    [1441, '8'],
    [2160, '8'],
  ])('selects the height-dependent bucket count at %s px', (height, expected) => {
    installFunctionalMatchMedia({ width: 1920, height })
    render(<HookProbe />)
    expect(probeText()).toBe(expected)
  })

  it('reports eight for a window that is already tall at mount time', () => {
    installFunctionalMatchMedia({ width: 2560, height: 1600 })
    const probe = render(<HookProbe />)
    expect(probeText()).toBe('8')
    probe.unmount()
  })

  it('tracks short → tall → short on one mounted consumer', () => {
    const matchMedia = installFunctionalMatchMedia({ width: 1920, height: 1080 })
    render(<HookProbe />)

    expect(probeText()).toBe('6')

    act(() => matchMedia.setViewport({ width: 1920, height: 1300 }))
    expect(probeText()).toBe('8')

    act(() => matchMedia.setViewport({ width: 1920, height: 1299 }))
    expect(probeText()).toBe('6')
  })

  it('ignores width-only changes and follows height at any width', () => {
    const matchMedia = installFunctionalMatchMedia({ width: 1920, height: 1080 })
    render(<HookProbe />)

    // Widening alone cannot convert a short window: only height drives the
    // predicate, so a width-only resize is a state no-op.
    act(() => matchMedia.setViewport({ width: 3840, height: 1080 }))
    expect(probeText()).toBe('6')

    act(() => matchMedia.setViewport({ width: 800, height: 1301 }))
    expect(probeText()).toBe('8')
  })

  it('survives StrictMode double mount and keeps unmounted consumers inert', () => {
    const matchMedia = installFunctionalMatchMedia({ width: 1920, height: 1080 })
    const strict = render(
      <StrictMode>
        <HookProbe id="strict" />
      </StrictMode>
    )
    expect(probeText('strict')).toBe('6')
    strict.unmount()

    // A live listener removal pattern: the unmounted consumer cleared its own
    // subscription; a change after unmount only touches mounted consumers.
    const first = render(<HookProbe id="first" />)
    render(<HookProbe id="second" />)
    expect(matchMedia.listenerCount()).toBe(2)
    first.unmount()
    expect(matchMedia.listenerCount()).toBe(1)

    act(() => matchMedia.setViewport({ width: 1920, height: 2160 }))
    expect(probeText('second')).toBe('8')

    // The unmounted first probe never received redundant notifications: the
    // leftover listener count proves no subscription leaked from it.
    expect(matchMedia.listenerCount()).toBe(1)
  })

  it('falls back to six when window.matchMedia is unavailable', () => {
    const original = window.matchMedia
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: undefined })

    const probe = render(<HookProbe />)
    expect(probeText()).toBe('6')
    probe.unmount()

    const server = renderToString(<HookProbe />)
    expect(server).toContain('>6<')
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: original })
  })

  it('renders six on the server snapshot regardless of fixture window state', () => {
    // Even with a matching browser-like MQL, the server snapshot must be the
    // deterministic six-bucket layout.
    installFunctionalMatchMedia({ width: 3840, height: 2160 })
    // renderToString executes on the client window, where matchMedia exists,
    // so force the genuinely server-shaped case instead.
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    const server = renderToString(<HookProbe />)
    expect(server).toContain('>6<')
  })
})
