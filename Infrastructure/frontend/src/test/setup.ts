import '@testing-library/jest-dom'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom does not implement matchMedia; uPlot probes it at module import time.
if (typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  })
}

// Default no-op ResizeObserver for chart mounts; individual tests stub over
// this with vi.stubGlobal when they need to drive resize callbacks.
if (typeof globalThis.ResizeObserver !== 'function') {
  class StandbyResizeObserver {
    observe = () => undefined
    unobserve = () => undefined
    disconnect = () => undefined
  }
  vi.stubGlobal('ResizeObserver', StandbyResizeObserver)
}

// Unmount React trees and reset the jsdom DOM between tests so component
// tests never leak rendered markup into one another.
afterEach(() => {
  cleanup()
})
