/**
 * Monitoring preview config (test-only).
 *
 * Serves the production `dist` build plus deterministic REST / WebSocket /
 * SPA-fallback fixtures on
 * `127.0.0.1` at the selected test-only port. It injects a restrictive CSP and writes a request
 * log so exact-origin enforcement stays executable without Playwright route
 * interception (the same fixture endpoints are available to `/visual-qa`).
 *
 * This config is used by `playwright.monitoring.config.ts` and by the Vitest
 * browser harness test. It is NOT the dev server config.
 */
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import fs from 'node:fs'
import crypto from 'node:crypto'
import type { IncomingMessage } from 'node:http'
import { fileURLToPath } from 'node:url'
import {
  parseRange,
  wsFixtureMessage,
} from './src/features/monitoring/config/fixtures.ts'
import { soilFixtureReply } from './src/features/soil/config/soilFixtures.ts'
import {
  sseFrameForEntry,
  sseHeartbeatFrame,
  sseCursorFrame,
  ALL_EVENTS,
  FLOWER_EVENTS,
  VEG_EVENTS,
  LAB_EVENTS,
  CJK_EVENT,
  GROUPED_CONSOLE_EVENTS,
} from './src/features/event-log/config/fixtures.ts'
import { FIXTURE_PORT, FIXTURE_WS_ORIGIN } from './src/features/monitoring/config/originGuard.ts'
import { historyOverviewFixtureReply } from './tests/monitoring/historyOverviewFixtures.ts'
import { calendarLifecycleFixtureReply } from './tests/monitoring/calendarLifecycleFixtures.ts'
import { profileFixtureReply } from './tests/monitoring/modeProfileFixtures.ts'
import {
  calendarCurrentRunFixtureReply,
  FIXTURE_ROUTES,
  MISSING_FIXTURE_SESSION,
  roomFrom,
  scenarioCounters,
} from './tests/monitoring/monitoringPreviewFixtures.ts'
import { archiveFixtureReply } from './src/features/grow-archives/fixtures.ts'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST_DIR = path.resolve(HERE, 'dist')

const CSP =
  `default-src 'self'; script-src 'self'; connect-src 'self' ${FIXTURE_WS_ORIGIN}; ` +
  "frame-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"

function scenarioFrom(url: string): string | null {
  const q = url.split('?')[1] ?? ''
  return new URLSearchParams(q).get('scenario')
}

function readRequestBody(req: IncomingMessage): Promise<string> {
  return new Promise(resolve => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer | string) => chunks.push(Buffer.from(chunk)))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', () => resolve(''))
  })
}

function isSensorPath(pathname: string): boolean {
  return pathname.startsWith('/api/sensors/monitoring/')
}

function isControlPath(pathname: string): boolean {
  return pathname.startsWith('/api/monitoring/control/')
}

function isDelayedHistoryPath(pathname: string): boolean {
  return /^\/api\/sensors\/monitoring\/range\//.test(pathname) || pathname.endsWith('/history')
}

function wsAccept(key: string): string {
  const digest = crypto
    .createHash('sha1')
    .update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64')
  return digest
}

/** Encode a server->client text frame (unmasked). */
function encodeTextFrame(payload: Buffer): Buffer {
  const len = payload.length
  let header: Buffer
  if (len < 126) {
    header = Buffer.from([0x81, len])
  } else if (len < 65536) {
    header = Buffer.from([0x81, 126, (len >> 8) & 0xff, len & 0xff])
  } else {
    header = Buffer.alloc(10)
    header[0] = 0x81
    header[1] = 127
    header.writeBigUInt64BE(BigInt(len), 2)
  }
  return Buffer.concat([header, payload])
}

function monitoringPreviewPlugin(): Plugin {
  return {
    name: 'monitoring-preview-fixtures',
    configurePreviewServer(server) {
      const logPath = process.env.MONITORING_PREVIEW_LOG
      const log = (line: string): void => {
        if (logPath) {
          try {
            fs.appendFileSync(logPath, `${line}\n`)
          } catch {
            /* logging must never break the preview */
          }
        }
      }

      server.middlewares.use(async (req, res, next) => {
        log(`REQUEST ${req.method} ${req.url}`)
        res.setHeader('Content-Security-Policy', CSP)
        const pathname = (req.url ?? '/').split('?')[0]
        const isFixtureMutation =
          (req.method === 'POST' &&
            (/^\/api\/climate-timeline\//.test(pathname) ||
              /^\/api\/room-modes\//.test(pathname) ||
              /^\/api\/lights\//.test(pathname) ||
              /^\/api\/pid\/(?:parameters|mode)\//.test(pathname) ||
              /^\/api\/calendar\/(?:events|sync\/|grow-plans\/)/.test(pathname) ||
              /^\/api\/archives\//.test(pathname))) ||
          (req.method === 'PUT' && /^\/api\/lights\//.test(pathname)) ||
          (req.method === 'PUT' && /^\/api\/devices\/registry\/\d+\/relay$/.test(pathname)) ||
          ((req.method === 'PUT' || req.method === 'DELETE') &&
            /^\/api\/sensors\/registry\/\d+\/assignment$/.test(pathname)) ||
          (req.method === 'PATCH' && /^\/api\/calendar\/events/.test(pathname)) ||
          (req.method === 'DELETE' && /^\/api\/archives\//.test(pathname))
        const isSensorBulkRead = req.method === 'POST' && pathname === '/api/sensor-data'
        const requestBody = isFixtureMutation || isSensorBulkRead ? await readRequestBody(req) : undefined
        const scenario = scenarioFrom(req.url ?? '') ?? scenarioFrom(req.headers.referer ?? '')
        const fixtureSession =
          new URLSearchParams((req.url ?? '').split('?')[1] ?? '').get('fixtureSession') ??
          new URLSearchParams((req.headers.referer ?? '').split('?')[1] ?? '').get('fixtureSession') ??
          MISSING_FIXTURE_SESSION
        const counterKey = (name: string): string => `${name}:${fixtureSession}:${pathname}`
        const calendarReply = calendarLifecycleFixtureReply(
          { url: req.url ?? '/', method: req.method, body: requestBody },
          scenario, fixtureSession, `http://127.0.0.1:${FIXTURE_PORT}`
        )
        if (calendarReply !== null) {
          res.statusCode = calendarReply.status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(calendarReply.body))
          return
        }
        const soilReply = soilFixtureReply(
          { url: req.url ?? '/', method: req.method, body: requestBody },
          scenario,
          fixtureSession
        )
        if (soilReply !== null) {
          res.statusCode = soilReply.status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(soilReply.body))
          return
        }
        if (pathname === '/api/archives' || pathname.startsWith('/api/archives/')) {
          const archiveReply = archiveFixtureReply(
            { url: req.url ?? '/', method: req.method, body: requestBody },
            scenario,
            `http://127.0.0.1:${FIXTURE_PORT}`,
            fixtureSession
          )
          if (archiveReply !== null) {
            res.statusCode = archiveReply.status
            res.setHeader('Content-Type', 'application/json')
            res.end(archiveReply.status === 204 ? undefined : JSON.stringify(archiveReply.body))
            return
          }
          res.statusCode = 405
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'Archive fixture operation denied by default' }))
          return
        }

        if (scenario === 'backend-down' && isSensorPath(pathname)) {
          const key = `backend-down:${fixtureSession}`
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 3) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'backend down (fixture)' }))
            return
          }
        }
        if (scenario === 'automation-down' && isControlPath(pathname)) {
          const key = `automation-down:${fixtureSession}`
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 3) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'automation down (fixture)' }))
            return
          }
        }
        if (
          scenario === 'timeline-api-failure' &&
          req.method === 'GET' &&
          /\/(?:profile|configuration)$/.test(pathname)
        ) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'saved timeline unavailable (fixture)' }))
          return
        }
        if (
          scenario === 'timeline-unavailable-409' &&
          req.method === 'GET' &&
          /\/(?:profile|configuration)$/.test(pathname)
        ) {
          res.statusCode = 409
          res.setHeader('Content-Type', 'application/json')
          res.end(
            JSON.stringify({
              detail: {
                code: 'timeline_unavailable',
                detail: 'saved timeline unavailable (fixture)',
              },
            })
          )
          return
        }
        if (
          scenario === 'timeline-preview-failed' &&
          req.method === 'POST' &&
          pathname.endsWith('/preview')
        ) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'preview unavailable (fixture)' }))
          return
        }
        if (
          scenario === 'timeline-apply-conflict' &&
          req.method === 'POST' &&
          pathname.endsWith('/apply')
        ) {
          res.statusCode = 409
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'saved revision changed (fixture)' }))
          return
        }
        if (scenario === 'force-error' && isSensorPath(pathname)) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'forced monitoring error (fixture)' }))
          return
        }
        if (scenario === 'control-history-transient' && pathname.endsWith('/history')) {
          const key = counterKey('control-history-transient')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 2) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'control history unavailable (fixture)' }))
            return
          }
        }
        if (scenario === 'control-history-persistent' && pathname.endsWith('/history')) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'control history persistently unavailable (fixture)' }))
          return
        }
        if (scenario === 'fixed-range-retry' && pathname.endsWith('/history')) {
          const { start } = parseRange(req.url ?? '')
          if (start === '2026-08-02T16:00:00.000Z') {
            const key = counterKey('fixed-range-retry')
            const count = scenarioCounters.get(key) ?? 0
            scenarioCounters.set(key, count + 1)
            if (count === 0) {
              res.statusCode = 503
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ detail: 'fixed control history unavailable (fixture)' }))
              return
            }
          }
        }
        if (
          scenario === 'range-503-after-good' &&
          /^\/api\/sensors\/monitoring\/range\//.test(pathname)
        ) {
          const key = counterKey('range-503-after-good')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count === 1) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'range unavailable (fixture)' }))
            return
          }
        }
        if (
          scenario === 'malformed-sensor' &&
          /^\/api\/sensors\/monitoring\/range\//.test(pathname)
        ) {
          const key = counterKey('malformed-sensor')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          // Only the first read is malformed (the store no longer issues a
          // discarded pre-mount read), so Retry recovers on the second.
          if (count < 1) {
            const { start, end } = parseRange(req.url ?? '')
            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json')
            res.end(
              JSON.stringify({
                metadata: {
                  generated_at: '2026-08-02T12:00:00.000Z',
                  tier: 'raw',
                  range: { start, end },
                  room: { room: roomFrom(req.url ?? '', 4), nodes: ['front', 'back'] },
                },
                series: [
                  {
                    sensor: 'dry_bulb_f',
                    node: 'front',
                    unit_family: 'celsius',
                    unit: '°C',
                    points: [
                      {
                        timestamp: start,
                        average: 'not-a-number',
                        minimum: 24.1,
                        maximum: 24.9,
                        sample_count: 60,
                      },
                    ],
                  },
                ],
                statistics: [],
              })
            )
            return
          }
        }

        // Event-log scenario handling
        if (pathname === '/api/events/history') {
          if (scenario === 'auth-401') {
            res.statusCode = 401
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'unauthorized (fixture)' }))
            return
          }
          if (scenario === 'error-500') {
            res.statusCode = 500
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'internal error (fixture)' }))
            return
          }
          if (scenario === 'cursor-trimmed-409') {
            res.statusCode = 409
            res.setHeader('Content-Type', 'application/json')
            res.end(
              JSON.stringify({
                earliest_cursor: `${Date.now() - 60000}-0`,
                latest_cursor: `${Date.now()}-999`,
              })
            )
            return
          }
        }

        // SSE stream endpoint
        if (pathname === '/api/events/stream') {
          if (scenario === 'auth-401') {
            res.statusCode = 401
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'unauthorized (fixture)' }))
            return
          }
          if (scenario === 'disconnect') {
            res.statusCode = 200
            res.setHeader('Content-Type', 'text/event-stream')
            res.setHeader('Cache-Control', 'no-cache')
            res.setHeader('Connection', 'keep-alive')
            res.end()
            return
          }
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/event-stream')
          res.setHeader('Cache-Control', 'no-cache')
          res.setHeader('Connection', 'keep-alive')

          // Send heartbeat first
          res.write(sseHeartbeatFrame())

          // Select events based on scenario
          let eventsToSend = ALL_EVENTS
          if (scenario === 'flower-only') eventsToSend = FLOWER_EVENTS
          else if (scenario === 'veg-only') eventsToSend = VEG_EVENTS
          else if (scenario === 'lab-only') eventsToSend = LAB_EVENTS
          else if (scenario === 'cjk-payload') eventsToSend = [...FLOWER_EVENTS, CJK_EVENT]
          else if (scenario === 'grouped-console') eventsToSend = GROUPED_CONSOLE_EVENTS

          if (scenario === 'burst') {
            // Send 20 events rapidly
            const burstEvents: typeof ALL_EVENTS = []
            for (let i = 0; i < 20; i++) {
              const base = ALL_EVENTS[i % ALL_EVENTS.length]
              burstEvents.push({
                ...base,
                redis_id: `${Date.now() + i}-${1000 + i}`,
                event: {
                  ...base.event,
                  event_id: base.event.event_id + `-burst-${i}`,
                  occurred_at: new Date(Date.now() + i * 100).toISOString(),
                },
              })
            }
            eventsToSend = burstEvents
          }

          // Send events with small delays to simulate streaming
          let offset = 0
          const sendNext = () => {
            if (offset >= eventsToSend.length) {
              // Send final cursor and keep alive briefly
              res.write(sseCursorFrame(`${Date.now()}-9999`))
              setTimeout(() => {
                try {
                  res.end()
                } catch {
                  /* client may have disconnected */
                }
              }, 500)
              return
            }
            const entry = eventsToSend[offset]
            res.write(sseFrameForEntry(entry))
            offset += 1
            setTimeout(sendNext, scenario === 'burst' ? 10 : 50)
          }
          setTimeout(sendNext, 100)
          return
        }

        const profileReply = profileFixtureReply({
          url: req.url ?? '/', method: req.method, body: requestBody, referer: req.headers.referer,
        }, scenario, fixtureSession)
        if (profileReply !== null) {
          // Capture authority before waiting so older responses cannot observe later writes.
          const body = JSON.stringify(profileReply.body)
          if (profileReply.delayMs)
            await new Promise<void>(resolve => setTimeout(resolve, profileReply.delayMs))
          if (!res.destroyed && !res.writableEnded) {
            res.statusCode = profileReply.status ?? 200
            res.setHeader('Content-Type', 'application/json')
            res.end(body)
          }
          return
        }

        const currentRunReply = calendarCurrentRunFixtureReply(
          { url: req.url ?? '/', method: req.method }, scenario
        )
        if (currentRunReply !== null) {
          res.statusCode = currentRunReply.status ?? 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(currentRunReply.body))
          return
        }

        // Bounded all-history overview fixtures: GET-only, one guarded reply
        // per overview route (sensor/control).
        const overviewReply = historyOverviewFixtureReply(
          { url: req.url ?? '/', method: req.method },
          scenario
        )
        if (overviewReply !== null) {
          res.statusCode = overviewReply.status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(overviewReply.body))
          return
        }

        for (const route of FIXTURE_ROUTES) {
          if (route.re.test(pathname)) {
            res.setHeader('Content-Type', 'application/json')
            const handlerBody = route.handler(
              { url: req.url, method: req.method, body: requestBody },
              scenario
            )
            const { __status: fixtureStatus, ...fixtureBody } =
              typeof handlerBody === 'object' && handlerBody !== null && '__status' in handlerBody
                ? (handlerBody as { __status: number })
                : { __status: undefined }
            if (fixtureStatus !== undefined) {
              res.statusCode = fixtureStatus
              res.end(JSON.stringify(fixtureBody))
              return
            }
            const body = JSON.stringify(handlerBody)
            if (scenario === 'delayed-control-recovery' && pathname.endsWith('/tail')) {
              const key = counterKey('delayed-control-tail')
              const count = scenarioCounters.get(key) ?? 0
              scenarioCounters.set(key, count + 1)
              if (count === 0) {
                res.statusCode = 503
                res.setHeader('Content-Type', 'application/json')
                res.end(JSON.stringify({ detail: 'control tail unavailable (fixture)' }))
                return
              }
            }
            if (scenario === 'delayed-range' && isDelayedHistoryPath(pathname)) {
              const key = counterKey('delayed-range')
              const count = scenarioCounters.get(key) ?? 0
              scenarioCounters.set(key, count + 1)
              setTimeout(() => res.end(body), 500)
              return
            }
            res.end(body)
            return
          }
        }

        // Let Vite serve static assets from dist.
        if (pathname.startsWith('/assets/')) {
          next()
          return
        }

        // SPA fallback for any other GET.
        if (req.method === 'GET') {
          const indexPath = path.join(DIST_DIR, 'index.html')
          if (fs.existsSync(indexPath)) {
            res.setHeader('Content-Type', 'text/html')
            res.end(fs.readFileSync(indexPath))
            return
          }
        }
        next()
      })

      server.httpServer?.on('upgrade', (req, socket) => {
        log(`WS-UPGRADE ${req.url}`)
        socket.on('error', error => {
          log(`WS-SOCKET-ERROR ${error.message}`)
          socket.destroy()
        })
        if (!req.url?.startsWith('/ws')) {
          socket.destroy()
          return
        }
        const key = req.headers['sec-websocket-key']
        if (!key) {
          socket.destroy()
          return
        }
        socket.write(
          'HTTP/1.1 101 Switching Protocols\r\n' +
            'Upgrade: websocket\r\n' +
            'Connection: Upgrade\r\n' +
            `Sec-WebSocket-Accept: ${wsAccept(key)}\r\n\r\n`
        )
        socket.write(encodeTextFrame(Buffer.from(JSON.stringify(wsFixtureMessage()))))
        const closeTimer = setTimeout(() => {
          if (!socket.destroyed) socket.end()
        }, 1000)
        socket.once('close', () => clearTimeout(closeTimer))
      })
    },
  }
}

export default defineConfig({
  define: {
    'import.meta.env.VITE_MONITORING_PERF_MARKS': JSON.stringify('1'),
    'import.meta.env.VITE_MONITORING_PERF_INJECT_DELAY_MS': JSON.stringify('12'),
  },
  plugins: [tailwindcss(), react(), monitoringPreviewPlugin()],
  resolve: {
    alias: {
      '@': path.resolve(HERE, './src'),
    },
  },
  preview: {
    host: '127.0.0.1',
    port: FIXTURE_PORT,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
