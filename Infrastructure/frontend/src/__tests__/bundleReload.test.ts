import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { registerBundleReloadHandler, isDynamicModuleLoadError } from '../main'

interface Listener {
  type: string
  fn: (event: { message?: string; reason?: unknown }) => void
}

describe('stale-bundle reload guard', () => {
  let listeners: Listener[]
  let guard: Map<string, string>
  let reload: ReturnType<typeof vi.fn>

  const errorPhrasing = 'Error loading dynamically imported module: http://x/old.js'
  const insufficientPhrasing = 'Error: unrelated failure'

  const originalSessionStorage = window.sessionStorage

  beforeEach(() => {
    listeners = []
    guard = new Map()
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => guard.get(key) ?? null,
        setItem: (key: string, value: string) => void guard.set(key, value),
        removeItem: (key: string) => void guard.delete(key),
      },
    })
    reload = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload },
    })
    const originalAdd = window.addEventListener
    window.addEventListener = ((type: string, fn: unknown) => {
      listeners.push({ type, fn: fn as Listener['fn'] })
    }) as typeof window.addEventListener
    try {
      registerBundleReloadHandler()
    } finally {
      window.addEventListener = originalAdd
    }
  })

  afterEach(() => {
    Object.defineProperty(window, 'sessionStorage', { configurable: true, value: originalSessionStorage })
    vi.useRealTimers()
  })

  function fire(type: 'error' | 'unhandledrejection', message?: string, reason?: unknown): number {
    const before = reload.mock.calls.length
    for (const listener of listeners) {
      if (listener.type === type) listener.fn({ message, reason })
    }
    return reload.mock.calls.length - before
  }

  it('matches dynamic-import failure messages across browser phrasings', () => {
    expect(isDynamicModuleLoadError('Error loading dynamically imported module: http://mothernode:8080/assets/VegetationControl-BRHKXxJ1.js')).toBe(true)
    expect(isDynamicModuleLoadError('Failed to fetch dynamically imported module http://x/a.js')).toBe(true)
    expect(isDynamicModuleLoadError('Error: unrelated crash')).toBe(false)
    expect(isDynamicModuleLoadError(null)).toBe(false)
  })

  it('reloads on the documented failure message', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-01T00:00:00Z'))
    expect(fire('error', errorPhrasing)).toBeGreaterThan(0)
  })

  it('reloads at most once inside the guard window', () => {
    vi.useFakeTimers()
    const start = new Date('2026-09-01T00:00:00Z').getTime()
    vi.setSystemTime(new Date(start))
    expect(fire('error', errorPhrasing)).toBe(1)
    expect(fire('error', errorPhrasing)).toBe(0)

    vi.setSystemTime(new Date(start + 10_000))
    expect(fire('error', errorPhrasing)).toBe(1)
  })

  it('ignores unrelated errors', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-01T00:00:00Z'))
    expect(fire('error', insufficientPhrasing)).toBe(0)
    expect(fire('error', 'Error: boom but not a chunk failure')).toBe(0)
  })

  it('listens on both the error and unhandledrejection channels', () => {
    const types = new Set(listeners.map((listener) => listener.type))
    expect(types.has('error')).toBe(true)
    expect(types.has('unhandledrejection')).toBe(true)
  })
})
