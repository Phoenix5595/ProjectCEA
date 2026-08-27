import '@testing-library/jest-dom'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Unmount React trees and reset the jsdom DOM between tests so component
// tests never leak rendered markup into one another.
afterEach(() => {
  cleanup()
})
