import type { TestInfo } from '@playwright/test'

export function fixtureUrl(
  path: string,
  testInfo: TestInfo,
  suffix?: string,
  scenario?: string,
): string {
  const params = new URLSearchParams()
  params.set(
    'fixtureSession',
    suffix === undefined ? testInfo.testId : `${testInfo.testId}:${suffix}`,
  )
  if (scenario !== undefined) params.set('scenario', scenario)
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}${params.toString()}`
}
