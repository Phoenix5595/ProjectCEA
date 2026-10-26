import { expect, test } from '@playwright/test'
import type { JSHandle, Page } from '@playwright/test'

import { fixtureUrl } from './fixtureUrl'

/** Start a real HTML5 drag from `sourceRelay` onto `targetRelay`; returns the shared DataTransfer. */
async function dragRelay(
  page: Page,
  sourceRelay: number,
  targetRelay: number
): Promise<JSHandle<DataTransfer>> {
  const dataTransfer = await page.evaluateHandle(() => new DataTransfer())
  await page.getByTestId(`relay-tile-${sourceRelay}`).dispatchEvent('dragstart', { dataTransfer })
  const target = page.getByTestId(`relay-tile-${targetRelay}`)
  await target.dispatchEvent('dragenter', { dataTransfer })
  await target.dispatchEvent('dragover', { dataTransfer })
  return dataTransfer
}

async function dropRelay(
  page: Page,
  dataTransfer: JSHandle<DataTransfer>,
  sourceRelay: number,
  targetRelay: number
): Promise<void> {
  await page.getByTestId(`relay-tile-${targetRelay}`).dispatchEvent('drop', { dataTransfer })
  await page.getByTestId(`relay-tile-${sourceRelay}`).dispatchEvent('dragend', { dataTransfer })
}

function relayMoveRequest(page: Page, deviceId: number) {
  return page.waitForRequest(
    request =>
      request.method() === 'PUT' &&
      new URL(request.url()).pathname === `/api/devices/registry/${deviceId}/relay`
  )
}

test('dragging an assignment onto an empty relay moves it', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(fixtureUrl('/devices', testInfo, testInfo.project.name))

  const source = page.getByTestId('relay-tile-6')
  const target = page.getByTestId('relay-tile-5')
  await expect(source).toContainText('Apache')
  await expect(target).toContainText('Unassigned')

  const requestPromise = relayMoveRequest(page, 3)
  const dataTransfer = await dragRelay(page, 6, 5)
  await expect(target.getByTestId('relay-drop-label')).toHaveText('Move here')
  await dropRelay(page, dataTransfer, 6, 5)

  expect((await requestPromise).postDataJSON()).toEqual({ channel: 13 })
  await expect(target).toContainText('Apache')
  await expect(source).toContainText('Unassigned')
  expect(errors).toEqual([])
})

test('dragging an assignment onto an occupied relay swaps the two', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(fixtureUrl('/devices', testInfo, testInfo.project.name))

  const source = page.getByTestId('relay-tile-8')
  const target = page.getByTestId('relay-tile-7')
  await expect(source).toContainText('Chilled Front')
  await expect(target).toContainText('Chilled Back')

  const requestPromise = relayMoveRequest(page, 2)
  const dataTransfer = await dragRelay(page, 8, 7)
  await expect(target.getByTestId('relay-drop-label')).toHaveText('Swap with Chilled Back')
  await dropRelay(page, dataTransfer, 8, 7)

  expect((await requestPromise).postDataJSON()).toEqual({ channel: 12 })
  await expect(target).toContainText('Chilled Front')
  await expect(source).toContainText('Chilled Back')
  expect(errors).toEqual([])
})

test('a relay that cannot confirm OFF leaves every assignment unchanged', async ({
  page,
}, testInfo) => {
  await page.goto(fixtureUrl('/devices', testInfo, testInfo.project.name, 'relay-move-unsafe'))

  const source = page.getByTestId('relay-tile-6')
  const target = page.getByTestId('relay-tile-5')
  await expect(source).toContainText('Apache')
  await expect(target).toContainText('Unassigned')

  const requestPromise = relayMoveRequest(page, 3)
  const dataTransfer = await dragRelay(page, 6, 5)
  await expect(target.getByTestId('relay-drop-label')).toHaveText('Move here')
  await dropRelay(page, dataTransfer, 6, 5)

  expect((await requestPromise).postDataJSON()).toEqual({ channel: 13 })
  await expect(page.getByText('Relay did not confirm OFF — no assignment changed')).toBeVisible()
  await expect(source).toContainText('Apache')
  await expect(target).toContainText('Unassigned')
})
