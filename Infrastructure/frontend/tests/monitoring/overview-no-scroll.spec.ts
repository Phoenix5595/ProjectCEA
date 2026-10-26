import { expect, test } from '@playwright/test'

import { buildCalendarVisualFixture } from '../../src/components/calendar/calendarVisualFixture'
import { fixtureUrl } from './fixtureUrl'

const OVERVIEW_PATHS = ['/', '/flower', '/vegetation', '/laboratory']

test('overview pages never scroll the document at either QA size', async ({ page }, testInfo) => {
  for (const path of OVERVIEW_PATHS) {
    await page.goto(fixtureUrl(path, testInfo))
    if (path === '/') {
      await expect(page.locator('.dashboard-workspace')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByTestId('event-log-body')).toBeVisible({ timeout: 20_000 })
    } else {
      await expect(page.locator('.room-overview')).toBeVisible({ timeout: 20_000 })
      await expect(
        page.locator('.room-overview__events [data-testid="event-log-body"]')
      ).toBeVisible({ timeout: 20_000 })
    }
    const documentSize = await page.evaluate(() => ({
      scrollHeight: document.scrollingElement!.scrollHeight,
      scrollWidth: document.scrollingElement!.scrollWidth,
      innerHeight,
      innerWidth,
    }))
    expect(
      documentSize.scrollHeight,
      `${path} document height at ${testInfo.project.name}`
    ).toBeLessThanOrEqual(documentSize.innerHeight + 1)
    expect(
      documentSize.scrollWidth,
      `${path} document width at ${testInfo.project.name}`
    ).toBeLessThanOrEqual(documentSize.innerWidth + 1)
  }
})

test('a busy room-overview day keeps the month and the day detail inside the calendar column', async ({
  page,
}, testInfo) => {
  const flowerEvents = buildCalendarVisualFixture(new Date()).filter(
    event => event.location === 'Flower Room'
  )
  const candidateDays = [
    ...new Set(flowerEvents.flatMap(event => [event.start, event.end ?? event.start])),
  ]
  const covering = (day: string) =>
    flowerEvents.filter(event => event.start <= day && day <= (event.end ?? event.start))
  const busiestDay = candidateDays.reduce((best, day) =>
    covering(day).length > covering(best).length ? day : best
  )
  const busiestLabel = covering(busiestDay)[0]!.title

  await page.goto(fixtureUrl('/flower', testInfo, undefined, 'calendar-visibility'))
  await expect(page.locator('.room-overview')).toBeVisible()
  await page
    .locator(`.room-overview .rdp-day_button[aria-label*="${busiestLabel}"]`)
    .first()
    .click()
  await expect(page.locator('.room-overview .grow-cal-day-detail')).toBeVisible()

  const geometry = await page.evaluate(() => {
    const rect = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector)
      return element === null ? null : element.getBoundingClientRect().toJSON()
    }
    const detail = document.querySelector<HTMLElement>('.room-overview .grow-cal-day-detail')
    return {
      calendar: rect('.room-overview__calendar'),
      table: rect('.room-overview .rdp-month_grid'),
      detail: rect('.room-overview .grow-cal-day-detail'),
      detailOverflowY: detail === null ? null : getComputedStyle(detail).overflowY,
    }
  })
  expect(geometry.calendar).not.toBeNull()
  expect(geometry.table).not.toBeNull()
  expect(geometry.detail).not.toBeNull()
  expect(geometry.table!.bottom).toBeLessThanOrEqual(geometry.calendar!.bottom + 1)
  expect(geometry.detail!.bottom).toBeLessThanOrEqual(geometry.calendar!.bottom + 1)
  expect(geometry.table!.bottom).toBeLessThanOrEqual(geometry.detail!.top + 1)
  expect(geometry.detailOverflowY).toBe('auto')
})
