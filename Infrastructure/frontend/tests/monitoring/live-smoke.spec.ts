/**
 * Owner-authorized read-only live smoke exception to the frontend anti-pattern
 * against browser tests on production hosts, per T9/authorization.md.
 *
 * Navigation and GET-backed rendering are the only interactions in this spec.
 * Console warning/error lines are archived for the owner but do not fail the run.
 */
import { expect, test } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const EVIDENCE_DIR = path.resolve(process.cwd(), '../../.omo/evidence/finish-climate-setpoints-release/T5')
const ROUTES = [
  { path: '/flower/monitoring', name: 'Flower monitoring' },
  { path: '/vegetation/monitoring', name: 'Vegetation monitoring' },
  { path: '/flower/control', name: 'Flower climate timeline' },
  { path: '/vegetation/control', name: 'Vegetation climate timeline' },
  { path: '/flower', name: 'Flower event log' },
] as const

test('browses live monitoring, climate timelines, and event log without writes', async ({ page }) => {
  const baseURL = process.env.BASE_URL
  test.skip(!baseURL, 'live smoke requires BASE_URL and is skipped for fixture runs')
  if (baseURL === undefined) return

  const viewport = page.viewportSize()
  const viewportTuple = [viewport?.width ?? 0, viewport?.height ?? 0]
  expect([[1920, 1080], [1280, 1440]]).toContainEqual(viewportTuple)

  const pageErrors: string[] = []
  const consoleLines: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'warning' || message.type() === 'error') {
      consoleLines.push(`${message.type()}: ${message.text()}`)
    }
  })

  for (const route of ROUTES) {
    await page.goto(new URL(route.path, baseURL).toString(), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('main'), `${route.name} main container`).toBeVisible()
    await expect(page.locator('.mon-banner--error')).toHaveCount(0)
    await expect(page.getByText(/timeline unavailable/i)).toHaveCount(0)
  }

  await mkdir(EVIDENCE_DIR, { recursive: true })
  await writeFile(
    path.join(EVIDENCE_DIR, `live-smoke-${test.info().project.name}.json`),
    `${JSON.stringify({
      base_url: process.env.BASE_URL,
      project: test.info().project.name,
      viewport,
      routes: ROUTES.map((route) => route.path),
      page_errors: pageErrors,
      console_warning_or_error_lines: consoleLines,
    }, null, 2)}\n`,
  )

  expect(pageErrors).toEqual([])
})
