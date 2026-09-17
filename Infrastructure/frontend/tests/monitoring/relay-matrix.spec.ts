import { expect, test } from '@playwright/test'
import { fixtureUrl } from './fixtureUrl'

test('renders every relay label inside the ZoneConfig matrix host', async ({ page }, testInfo) => {
  await page.goto(fixtureUrl('/flower/control', testInfo))

  const matrix = page.getByTestId('relay-channel-matrix')
  await expect(matrix).toBeVisible()

  const layout = await matrix.evaluate((element) => {
    const host = element.parentElement
    const labels = Array.from(element.querySelectorAll('span'))
      .filter((label) => /^R(?:[1-9]|1[0-6])$/.test(label.textContent ?? ''))
      .map((label) => {
        const rect = label.getBoundingClientRect()
        return { text: label.textContent, top: rect.top, bottom: rect.bottom }
      })
    const hostRect = host?.getBoundingClientRect()
    return {
      hostBottom: hostRect?.bottom ?? 0,
      labels,
    }
  })

  expect(layout.labels.map((label) => label.text).sort((left, right) => Number(left?.slice(1)) - Number(right?.slice(1)))).toEqual(
    Array.from({ length: 16 }, (_, index) => `R${index + 1}`),
  )
  expect(layout.labels.every((label) => label.bottom <= layout.hostBottom)).toBe(true)
})
