import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { registerBundleReloadHandler, isDynamicModuleLoadError } from '../main'

function fireWindowEvent(type: string, init: { message?: string; reason?: unknown; error?: unknown }): void {
  window.dispatchEvent(
    type === 'unhandledrejection'
      ? new PromiseRejectionEvent('unhandledrejection', { promise: Promise.resolve(), reason: init.reason ?? init.error ?? init.message ?? '' })
      : new ErrorEvent(type, { message: init.message ?? '', error: init.error }),
  )
}

describe('stale-bundle reload guard', () => {
  let reload: ReturnType<typeof vi.fn>
  let guard: Map<string, string>

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-01T00:00:00Z'))
    guard = new Map()
    const storage = {
      getItem: (key: string) => guard.get(key) ?? null,
      setItem: (key: string, value: string) => void guard.set(key, value),
      removeItem: (key: string) => void guard.delete(key),
    }
    vi.stubGlobal('sessionStorage', storage)
    reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    registerBundleReloadHandler()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it.each([
    'Error loading dynamically imported module: http://x/old.js',
    'Failed to fetch dynamically imported module http://x/a.js',
    'Importing a module script failed.',
    'Loading chunk 5 failed.',
  ])('matches the documented browser phrasings: %s', (message) => {
    expect(isDynamicModuleLoadError(message)).toBe(true)
  })

  it('rejects unrelated errors and non-strings', () => {
    expect(isDynamicModuleLoadError('TypeError: boom')).toBe(false)
    expect(isDynamicModuleLoadError(null)).toBe(false)
    expect(isDynamicModuleLoadError(undefined)).toBe(false)
  })

  it('reload-once via the generic error channel, repeat suppressed in the window', () => {
    fireWindowEvent('error', { message: 'Error loading dynamically imported module: http://x/old.js' })
    expect(reload).toHaveBeenCalledTimes(1)

    fireWindowEvent('error', { message: 'Error loading dynamically imported module: http://x/old.js' })
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('recovers again after the guard window expires', () => {
    fireWindowEvent('error', { message: 'Failed to fetch dynamically imported module http://x/a.js' })
    expect(reload).toHaveBeenCalledTimes(1)

    vi.setSystemTime(new Date('2026-09-01T00:00:06Z'))
    fireWindowEvent('error', { message: 'Failed to fetch dynamically imported module http://x/a.js' })
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('listens on the vite:preloadError channel', () => {
    window.dispatchEvent(new Event('vite:preloadError'))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('still recovers when storage is denied (private mode)', () => {
    // The in-memory fallback stamp from earlier tests predates this fixture's
    // clock by a large margin (test isolation), so recovery proceeds once.
    vi.setSystemTime(new Date('2026-09-01T02:00:00Z'))
    const throwing = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
      removeItem: () => undefined,
    }
    vi.stubGlobal('sessionStorage', throwing)
    fireWindowEvent('error', { message: 'Failed to fetch dynamically imported module http://x/a.js' })
    expect(reload).toHaveBeenCalledTimes(1)

    // And repeats inside the window are still suppressed without storage.
    fireWindowEvent('error', { message: 'Failed to fetch dynamically imported module http://x/a.js' })
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
