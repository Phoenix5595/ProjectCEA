/**
 * Monitoring browser harness availability + route-guard tests.
 *
 * Full Playwright browser coverage is added in later todos. This Vitest suite
 * verifies that the harness is available (the preview and Playwright configs
 * exist) and that the exact-origin route guard rejects every production
 * port/host while allowing only `http://127.0.0.1:4173`.
 */
import { describe, it, expect } from 'vitest'
import { existsSync } from 'node:fs'
import path from 'node:path'
import {
  FIXTURE_ORIGIN,
  FORBIDDEN_PORTS,
  FORBIDDEN_HOSTS,
  describeViolation,
  isAllowedOrigin,
} from '../config/originGuard'

const ROOT = process.cwd()

describe('monitoring browser harness', () => {
  it('uses localhost preview and mandatory route guard', () => {
    // Harness availability: the preview + Playwright configs exist on disk.
    expect(existsSync(path.join(ROOT, 'vite.monitoring.config.ts'))).toBe(true)
    expect(existsSync(path.join(ROOT, 'playwright.monitoring.config.ts'))).toBe(
      true,
    )

    // The fixture origin is exactly 127.0.0.1:4173.
    expect(FIXTURE_ORIGIN).toBe('http://127.0.0.1:4173')
    expect(isAllowedOrigin('http://127.0.0.1:4173/')).toBe(true)
    expect(
      isAllowedOrigin(
        'http://127.0.0.1:4173/api/sensors/monitoring/range/Flower%20Room',
      ),
    ).toBe(true)

    // Mandatory route guard: every production port is forbidden.
    for (const port of FORBIDDEN_PORTS) {
      expect(describeViolation(`http://127.0.0.1:${port}/`)).toBe(
        `forbidden-port-${port}`,
      )
    }
    // Every production host is forbidden.
    for (const host of FORBIDDEN_HOSTS) {
      expect(describeViolation(`http://${host}:3001/`)).toBe(
        `forbidden-host-${host}`,
      )
    }
  })

  it('rejects any request outside exact fixture origin', () => {
    expect(describeViolation('http://127.0.0.1:8080/')).toBe(
      'forbidden-port-8080',
    )
    expect(describeViolation('http://127.0.0.1:8000/')).toBe(
      'forbidden-port-8000',
    )
    expect(describeViolation('http://iskraprojectcea:3001/')).toBe(
      'forbidden-host-iskraprojectcea',
    )
    expect(describeViolation('https://example.com/')).toBe(
      'external-origin-https://example.com',
    )
    expect(describeViolation('not a url')).toBe('malformed-url')
    expect(isAllowedOrigin('http://127.0.0.1:4173/')).toBe(true)
  })
})
