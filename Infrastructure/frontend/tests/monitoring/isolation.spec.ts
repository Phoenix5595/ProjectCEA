import { expect, test } from '@playwright/test'
import { fixtureUrl } from './fixtureUrl'

test('fixture sessions isolate transient backend recovery sequences', async ({ browser }, testInfo) => {
  const firstContext = await browser.newContext()
  const secondContext = await browser.newContext()
  const first = await firstContext.newPage()
  const second = await secondContext.newPage()
  const firstSession = `${testInfo.testId}:first`
  const firstProbeSession = `${testInfo.testId}:first-probe`
  const secondProbeSession = `${testInfo.testId}:second-probe`

  try {
    const fixtureRequests: string[] = []
    first.on('request', (request) => {
      if (request.url().includes('/api/sensors/monitoring/')) fixtureRequests.push(request.url())
    })
    await first.goto(fixtureUrl('/flower/monitoring?scenario=backend-down', testInfo, 'first'))
    await expect(first.getByRole('alert').first()).toBeVisible()
    expect(fixtureRequests.some((url) => url.includes(encodeURIComponent(firstSession)))).toBe(true)

    const recoveryStatuses = await first.evaluate(async (session) => {
      const target = `/api/sensors/monitoring/range/Flower%20Room?scenario=backend-down&fixtureSession=${encodeURIComponent(session)}`
      const statuses: number[] = []
      for (let index = 0; index < 4; index += 1) statuses.push((await fetch(target)).status)
      return statuses
    }, firstProbeSession)
    expect(recoveryStatuses).toEqual([503, 503, 503, 200])

    await second.goto(fixtureUrl('/flower/monitoring?scenario=backend-down', testInfo, 'second'))
    await expect(second.getByRole('alert').first()).toBeVisible()
    const secondStatus = await second.evaluate(async (session) => {
      const target = `/api/sensors/monitoring/range/Flower%20Room?scenario=backend-down&fixtureSession=${encodeURIComponent(session)}`
      return (await fetch(target)).status
    }, secondProbeSession)
    expect(secondStatus).toBe(503)
  } finally {
    await firstContext.close()
    await secondContext.close()
  }
})
