import { expect, test } from '@playwright/test'
import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

const EXPECTED_FORBIDDEN = [
  'http://127.0.0.1:8000/api/health',
  'http://127.0.0.1:8001/api/health',
  'http://127.0.0.1:8003/api/health',
  'http://127.0.0.1:8080/api/health',
  'http://iskraprojectcea:3001/grafana/',
  'https://example.invalid/monitoring-fixture',
  'http://127.0.0.1:4173/grafana/forbidden',
]

test('aborts and records every forbidden fixture request', async ({ page, context }, testInfo) => {
  const aborted: string[] = []
  await context.route('**/*', async (route) => {
    const url = route.request().url()
    if (url.includes('/grafana/') || describeViolation(url) !== null) {
      aborted.push(url)
      await route.abort()
      return
    }
    await route.continue()
  })

  await page.goto(fixtureUrl('/vegetation/monitoring?scenario=forbidden-network', testInfo))
  await expect(page.getByRole('table', { name: 'Sensor Values' })).toBeVisible()

  for (const url of EXPECTED_FORBIDDEN) {
    const probe = await context.newPage()
    await expect(probe.goto(url)).rejects.toThrow()
    await probe.close()
  }

  expect(aborted).toEqual(expect.arrayContaining(EXPECTED_FORBIDDEN))
  for (const url of aborted) {
    expect(url.includes('/grafana/') || describeViolation(url) !== null).toBe(true)
  }
})
