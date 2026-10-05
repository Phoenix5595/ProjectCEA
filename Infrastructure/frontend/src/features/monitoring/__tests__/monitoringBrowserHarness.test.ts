/**
 * Monitoring browser harness availability + route-guard tests.
 *
 * This Vitest suite verifies that the guarded harness is available and that
 * production origins are rejected; interactive coverage lives in Playwright.
 */
import { existsSync } from 'node:fs'
import path from 'node:path'

import { describe, it, expect } from 'vitest'

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
    expect(existsSync(path.join(ROOT, 'playwright.monitoring.config.ts'))).toBe(true)

    expect(FIXTURE_ORIGIN).toBe(`http://127.0.0.1:${process.env.MONITORING_FIXTURE_PORT ?? '4187'}`)
    expect(isAllowedOrigin(`${FIXTURE_ORIGIN}/`)).toBe(true)
    expect(isAllowedOrigin(`${FIXTURE_ORIGIN}/api/sensors/monitoring/range/Flower%20Room`)).toBe(
      true
    )
    expect(isAllowedOrigin('http://127.0.0.1:4173/')).toBe(false)

    // Mandatory route guard: every production port is forbidden.
    for (const port of FORBIDDEN_PORTS) {
      expect(describeViolation(`http://127.0.0.1:${port}/`)).toBe(`forbidden-port-${port}`)
    }
    // Every production host is forbidden.
    for (const host of FORBIDDEN_HOSTS) {
      expect(describeViolation(`http://${host}:3001/`)).toBe(`forbidden-host-${host}`)
    }
  })

  it('rejects any request outside exact fixture origin', () => {
    expect(describeViolation('http://127.0.0.1:8080/')).toBe('forbidden-port-8080')
    expect(describeViolation('http://127.0.0.1:8000/')).toBe('forbidden-port-8000')
    expect(describeViolation('http://iskraprojectcea:3001/')).toBe('forbidden-host-iskraprojectcea')
    expect(describeViolation('https://example.com/')).toBe('external-origin-https://example.com')
    expect(describeViolation('not a url')).toBe('malformed-url')
    expect(isAllowedOrigin(`${FIXTURE_ORIGIN}/`)).toBe(true)
  })
})
