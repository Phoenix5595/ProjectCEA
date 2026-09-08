/**
 * Monitoring preview config (test-only).
 *
 * Serves the production `dist` build plus deterministic REST / WebSocket /
 * Grafana-placeholder / SPA-fallback fixtures on exactly
 * `http://127.0.0.1:4173`. It injects a restrictive CSP and writes a request
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
import { fileURLToPath } from 'node:url'
import {
  controlProjectionFixture,
  controlRangeFixture,
  controlTailFixture,
  grafanaPlaceholder,
  parseRange,
  sensorLiveFixture,
  sensorRangeFixture,
  sensorStatsFixture,
  wsFixtureMessage,
} from './src/features/monitoring/config/fixtures'
import {
  eventHistoryFixture,
  sseFrameForEntry,
  sseHeartbeatFrame,
  sseCursorFrame,
  ALL_EVENTS,
  FLOWER_EVENTS,
  VEG_EVENTS,
  LAB_EVENTS,
  CJK_EVENT,
} from './src/features/event-log/config/fixtures'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST_DIR = path.resolve(HERE, 'dist')

const CSP =
  "default-src 'self'; connect-src 'self' ws://127.0.0.1:4173; " +
  "frame-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"

interface FixtureRoute {
  re: RegExp
  handler: (req: { url?: string }, scenario: string | null) => unknown
}

function roomFrom(url: string, index: number): string {
  return decodeURIComponent((url ?? '').split('/').filter(Boolean)[index] ?? '')
}

function scenarioFrom(url: string): string | null {
  const q = url.split('?')[1] ?? ''
  return new URLSearchParams(q).get('scenario')
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

const scenarioCounters = new Map<string, number>()
const MISSING_FIXTURE_SESSION = 'missing-session'

const FIXTURE_ROUTES: FixtureRoute[] = [
  {
    re: /^\/api\/sensors\/monitoring\/range\/([^/]+)/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return sensorRangeFixture(roomFrom(req.url ?? '', 4), start, end, scenario)
    },
  },
  {
    re: /^\/api\/sensors\/monitoring\/live\/([^/]+)\/([^/]+)/,
    handler: (req, scenario) => {
      const parts = (req.url ?? '').split('?')[0].split('/').filter(Boolean)
      return sensorLiveFixture(decodeURIComponent(parts[parts.length - 1] ?? ''), scenario)
    },
  },
  {
    re: /^\/api\/sensors\/monitoring\/stats\/([^/]+)/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return sensorStatsFixture(roomFrom(req.url ?? '', 4), start, end, scenario)
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/projection$/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return controlProjectionFixture(roomFrom(req.url ?? '', 3), start, end, scenario)
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/tail$/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return controlTailFixture(roomFrom(req.url ?? '', 3), start, end, scenario)
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/history$/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return controlRangeFixture(roomFrom(req.url ?? '', 3), start, end, scenario)
    },
  },
  {
    re: /^\/grafana\//,
    handler: () => grafanaPlaceholder(),
  },
  {
    re: /^\/api\/events\/history$/,
    handler: (req, scenario) => eventHistoryFixture(scenario),
  },
  {
    re: /^\/api\/calendar\/mode-schedule\//,
    handler: () => ({ expected: { mode_name: 'flower', submode_name: null, title: 'Flowering' }, active: { mode_name: 'flower', submode_name: null } }),
  },
  {
    re: /^\/api\/devices$/,
    handler: () => [
      { location: 'Flower Room', cluster: 'main', device_name: 'exhaust-fan', state: 1, mode: 'auto', channel: 1 },
      { location: 'Flower Room', cluster: 'main', device_name: 'circulation-fan', state: 1, mode: 'auto', channel: 2 },
      { location: 'Veg Room', cluster: 'main', device_name: 'circulation-fan', state: 1, mode: 'auto', channel: 3 },
      { location: 'Lab', cluster: 'main', device_name: 'heater-1', state: 0, mode: 'auto', channel: 4 },
    ],
  },
  {
    re: /^\/api\/devices\/([^/]+)\/([^/]+)$/,
    handler: (req) => {
      const location = decodeURIComponent(roomFrom(req.url ?? '', 2))
      const cluster = decodeURIComponent(roomFrom(req.url ?? '', 3))
      return {
        location,
        cluster,
        devices: {
          'exhaust-fan': {
            device_type: 'fan',
            display_name: 'Exhaust Fan',
            mode: 'auto',
            state: 1,
            channel: 1,
          },
          'circulation-fan': {
            device_type: 'fan',
            display_name: 'Circulation Fan',
            mode: 'auto',
            state: 1,
            channel: 2,
          },
        },
      }
    },
  },
  {
    re: /^\/api\/sensors\/([^/]+)\/([^/]+)\/live$/,
    handler: (req) => {
      const location = decodeURIComponent(roomFrom(req.url ?? '', 2))
      const now = new Date().toISOString()
      return {
        temperature: {
          data: [{ time: now, timestamp: now, value: 24.5 }],
          unit: '°C',
          sensor_name: 'temperature',
        },
        humidity: {
          data: [{ time: now, timestamp: now, value: 65 }],
          unit: '%',
          sensor_name: 'humidity',
        },
        co2: {
          data: [{ time: now, timestamp: now, value: 850 }],
          unit: 'ppm',
          sensor_name: 'co2',
        },
        vpd: {
          data: [{ time: now, timestamp: now, value: 1.2 }],
          unit: 'kPa',
          sensor_name: 'vpd',
        },
      }
    },
  },
  {
    re: /^\/api\/sensors\/live\/all$/,
    handler: () => [
      { sensor: 'Flower Room_main_temperature', value: 24.5, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Flower Room_main_humidity', value: 65, time: new Date().toISOString(), unit: '%' },
      { sensor: 'Veg Room_main_temperature', value: 23.8, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Veg Room_main_humidity', value: 68, time: new Date().toISOString(), unit: '%' },
      { sensor: 'Lab_main_temperature', value: 22.1, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Lab_main_humidity', value: 55, time: new Date().toISOString(), unit: '%' },
    ],
  },
  {
    re: /^\/api\/sensor-data$/,
    handler: () => ({
      'Flower Room_main_temperature': 24.5,
      'Flower Room_main_humidity': 65,
      'Veg Room_main_temperature': 23.8,
      'Veg Room_main_humidity': 68,
      'Lab_main_temperature': 22.1,
      'Lab_main_humidity': 55,
    }),
  },
  {
    re: /^\/api\/status$/,
    handler: (req) => {
      const url = req.url ?? ''
      const isHealth = url.includes('health=true')
      if (isHealth) {
        return {
          service_health: [
            { name: 'automation-service', status: 'healthy', latency_ms: 12 },
            { name: 'cea-backend', status: 'healthy', latency_ms: 8 },
            { name: 'can-processor', status: 'healthy', latency_ms: 5 },
          ],
        }
      }
      return {
        system: {
          cpu_usage: 15,
          memory_usage: 42,
          disk_usage: 28,
          uptime: '86400',
          load_avg: '0.5 0.3 0.2',
          process_count: 42,
          cpu_temp_c: 45,
          throttle_status: 'normal',
          services: [
            { name: 'automation-service', status: 'running', latency_ms: 12 },
            { name: 'cea-backend', status: 'running', latency_ms: 8 },
            { name: 'can-processor', status: 'running', latency_ms: 5 },
          ],
        },
      }
    },
  },
  {
    re: /^\/api\/devices\/control-snapshot$/,
    handler: () => ({
      generated_at: new Date().toISOString(),
      sampled_at: new Date().toISOString(),
      freshness: 'FRESH',
      registry_version: 1,
      stale_since: null,
      dfr_boards: [],
      relays: [
        { board: 0, channel: 1, state: 1, device_name: 'exhaust-fan', location: 'Flower Room', cluster: 'main' },
        { board: 0, channel: 2, state: 1, device_name: 'circulation-fan', location: 'Flower Room', cluster: 'main' },
        { board: 0, channel: 3, state: 1, device_name: 'circulation-fan', location: 'Veg Room', cluster: 'main' },
        { board: 0, channel: 4, state: 0, device_name: 'heater-1', location: 'Lab', cluster: 'main' },
      ],
      hardware_alarms: [],
    }),
  },
]

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

      server.middlewares.use((req, res, next) => {
        log(`REQUEST ${req.method} ${req.url}`)
        res.setHeader('Content-Security-Policy', CSP)
        const pathname = (req.url ?? '/').split('?')[0]
        const scenario = scenarioFrom(req.url ?? '')
        const fixtureSession = new URLSearchParams((req.url ?? '').split('?')[1] ?? '').get(
          'fixtureSession',
        ) ?? MISSING_FIXTURE_SESSION
        const counterKey = (name: string): string => `${name}:${fixtureSession}:${pathname}`

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
        if (scenario === 'force-error' && isSensorPath(pathname)) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'forced monitoring error (fixture)' }))
          return
        }
        if (scenario === 'range-503-after-good' && /^\/api\/sensors\/monitoring\/range\//.test(pathname)) {
          const key = counterKey('range-503-after-good')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count === 2 || count === 3) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'range unavailable (fixture)' }))
            return
          }
        }
        if (scenario === 'malformed-sensor' && /^\/api\/sensors\/monitoring\/range\//.test(pathname)) {
          const key = counterKey('malformed-sensor')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 2) {
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
              }),
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
            res.end(JSON.stringify({ earliest_cursor: `${Date.now() - 60000}-0`, latest_cursor: `${Date.now()}-999` }))
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

          if (scenario === 'burst') {
            // Send 20 events rapidly
            const burstEvents = []
            for (let i = 0; i < 20; i++) {
              const base = ALL_EVENTS[i % ALL_EVENTS.length]
              burstEvents.push({
                ...base,
                redis_id: `${Date.now() + i}-${1000 + i}`,
                event: { ...base.event, event_id: base.event.event_id + `-burst-${i}`, occurred_at: new Date(Date.now() + i * 100).toISOString() },
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
                try { res.end() } catch { /* client may have disconnected */ }
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

        for (const route of FIXTURE_ROUTES) {
          if (route.re.test(pathname)) {
            res.setHeader('Content-Type', 'application/json')
            const body = JSON.stringify(route.handler(req, scenario))
            if (scenario === 'delayed-control-recovery' && pathname.endsWith('/tail')) {
              const key = counterKey('delayed-control-tail')
              const count = scenarioCounters.get(key) ?? 0
              scenarioCounters.set(key, count + 1)
              if (count === 0) {
                setTimeout(() => res.end(body), 1_200)
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
            `Sec-WebSocket-Accept: ${wsAccept(key)}\r\n\r\n`,
        )
        socket.write(encodeTextFrame(Buffer.from(JSON.stringify(wsFixtureMessage()))))
        setTimeout(() => socket.end(), 1000)
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
    port: 4173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
