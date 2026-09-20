import { expect, test } from '@playwright/test'

import { describeViolation } from '../../src/features/monitoring/config/originGuard'
import { fixtureUrl } from './fixtureUrl'

test('renders recorded and projected setpoints with a nullable projected gap', async ({ page }, testInfo) => {
  const violations: string[] = []
  const pageErrors: string[] = []
  page.on('request', (request) => {
    const violation = describeViolation(request.url())
    if (violation !== null) violations.push(`${violation}: ${request.url()}`)
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await page.goto(fixtureUrl('/flower/monitoring', testInfo, undefined, 'nullable-projection'))

  const climate = page.locator('section[aria-label="Flower climate conditions"]')
  await expect(climate).toBeVisible()
  await expect(climate.getByRole('img', { name: 'Flower climate conditions' })).toBeVisible()
  await expect(climate.getByText('Heating Setpoint (Projected)')).toBeVisible()
  await expect(climate.getByText('Cooling Setpoint (Projected)')).toBeVisible()
  expect(violations).toEqual([])
  expect(pageErrors).toEqual([])
})
