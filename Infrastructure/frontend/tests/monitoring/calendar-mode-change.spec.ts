import { expect, test } from '@playwright/test'

import { formatHarvestDay } from '../../src/utils/expectedHarvest'
import { fixtureUrl } from './fixtureUrl'

/** Toronto calendar day `offsetDays` from today, as the fixture computes it. */
function torontoDay(offsetDays: number): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/Toronto',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
      .formatToParts(new Date())
      .map(part => [part.type, part.value])
  )
  const day = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)))
  day.setUTCDate(day.getUTCDate() + offsetDays)
  return day.toISOString().slice(0, 10)
}

test('the pending mode button activates Flower and +1 week moves the harvest', async ({
  page,
}, testInfo) => {
  await page.goto(
    fixtureUrl('/', testInfo, `mode-change-${testInfo.project.name}`, 'calendar-mode-change')
  )

  const inspector = page.getByRole('complementary', { name: 'Calendar inspector' })
  const flowerRow = page.locator('.dashboard-zone-row').filter({ hasText: 'Flower Room' })
  const harvest = inspector.getByTestId('inspector-expected-harvest')
  const plannedHarvest = `Harvest ~${formatHarvestDay(torontoDay(31))}`
  await expect(flowerRow).toContainText('Mode: Veg')
  await expect(harvest).toHaveText(plannedHarvest)
  await expect(flowerRow).toContainText(plannedHarvest)

  const modeButton = inspector.getByRole('button', { name: 'Veg → Flower' })
  await expect(modeButton).toBeVisible()
  await expect(modeButton).toHaveClass(/font-bold/)

  const modeRequest = page.waitForRequest(
    request =>
      request.method() === 'POST' &&
      decodeURIComponent(new URL(request.url()).pathname) ===
        '/api/room-modes/room/Flower Room/main/mode'
  )
  await modeButton.click()
  expect((await modeRequest).postDataJSON()).toEqual({
    mode_name: 'flower',
    submode_name: 'stretch',
  })
  await expect(modeButton).toHaveCount(0)
  await expect(flowerRow).toContainText('Mode: Flower')

  // The bulk phase spans today, so its day opens the event that +1 week extends.
  await page
    .locator('.grow-calendar--dashboard .rdp-day_button[aria-label*="Bulk phase"]')
    .first()
    .click()
  await inspector.getByRole('button', { name: /Bulk phase/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'Bulk phase' })).toBeVisible()

  const extendRequest = page.waitForRequest(
    request =>
      request.method() === 'POST' &&
      /\/api\/calendar\/grow-plans\/[^/]+\/extend$/.test(new URL(request.url()).pathname)
  )
  await dialog.getByRole('button', { name: '+1 week' }).click()
  expect((await extendRequest).postDataJSON()).toEqual({ event_id: 501, days: 7 })

  // The fixture shifts ripening with the bulk end, so the derived harvest moves.
  const shiftedHarvest = `Harvest ~${formatHarvestDay(torontoDay(38))}`
  await expect(harvest).toHaveText(shiftedHarvest)
  await expect(flowerRow).toContainText(shiftedHarvest)
})
