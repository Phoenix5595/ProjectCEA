/* Capture monitoring page screenshots + network/console logs for visual QA. */
const { chromium } = require('@playwright/test')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:4173'
const OUT = path.resolve(__dirname, '../../../../.omo/evidence/grafana-replacement-veg-flower/29-visual-a11y')
const WIDTHS = [375, 768, 1280]
const PAGES = [
  { slug: 'flower', path: '/flower/monitoring' },
  { slug: 'veg', path: '/vegetation/monitoring' },
]

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const logs = { requests: [], console: [] }

  for (const pageDef of PAGES) {
    for (const width of WIDTHS) {
      const context = await browser.newContext({ viewport: { width, height: 900 } })
      const page = await context.newPage()
      page.on('request', (req) => {
        logs.requests.push({ page: pageDef.slug, width, url: req.url() })
      })
      page.on('console', (msg) => {
        if (msg.type() === 'error' || msg.type() === 'warning') {
          logs.console.push({ page: pageDef.slug, width, type: msg.type(), text: msg.text() })
        }
      })
      await page.goto(`${BASE}${pageDef.path}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
      await page.screenshot({
        path: path.join(OUT, `${pageDef.slug}-${width}.png`),
        fullPage: true,
      })
      await context.close()
    }
  }

  fs.writeFileSync(path.join(OUT, 'network-console.json'), JSON.stringify(logs, null, 2))
  await browser.close()
  console.log('captures written to', OUT)
  console.log('requests:', logs.requests.length, 'console:', logs.console.length)
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
