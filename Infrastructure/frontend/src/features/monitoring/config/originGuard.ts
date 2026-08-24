/**
 * Exact-origin request guard for the monitoring browser harness.
 *
 * The monitoring preview serves fixtures on exactly `http://127.0.0.1:4173`.
 * Every REST, WebSocket, and Grafana URL in the browser build is overridden to
 * a dedicated path on that origin, so a correctly-built page must never issue
 * a request to a production service. This guard is the single source of truth
 * for what is allowed: it is used by the Playwright route guard (which aborts
 * out-of-origin requests) and by the Vitest harness test (which asserts zero
 * traffic to the production ports/hosts).
 */

export const FIXTURE_ORIGIN = 'http://127.0.0.1:4173'

/** Production ports that must never receive traffic from the fixture build. */
export const FORBIDDEN_PORTS = [8000, 8001, 8003, 8080]

/** Production hosts that must never receive traffic from the fixture build. */
export const FORBIDDEN_HOSTS = ['iskraprojectcea']

/**
 * Returns a violation descriptor for a URL that is not on the exact fixture
 * origin, or `null` when the URL is allowed. Malformed URLs are violations.
 */
export function describeViolation(url: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return 'malformed-url'
  }
  if (parsed.origin === FIXTURE_ORIGIN) return null
  if (FORBIDDEN_PORTS.includes(Number(parsed.port))) {
    return `forbidden-port-${parsed.port}`
  }
  if (FORBIDDEN_HOSTS.includes(parsed.hostname)) {
    return `forbidden-host-${parsed.hostname}`
  }
  return `external-origin-${parsed.origin}`
}

/** True when the URL is on the exact fixture origin. */
export function isAllowedOrigin(url: string): boolean {
  return describeViolation(url) === null
}
