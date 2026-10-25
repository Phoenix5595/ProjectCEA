/**
 * Canvas-safe colour derivation for chart options.
 *
 * Series colours may be hex literals, `rgb(...)` strings, or monitoring CSS
 * variables (`var(--mon-...)`, used by the soil palette). Canvas cannot paint
 * a `var()` expression, so every derived colour is resolved to a concrete
 * value first, falling back to the deterministic theme tokens when the
 * stylesheet is not applied (jsdom).
 */
import { MONITORING_TOKEN_NAMES, readToken, type MonitoringTokenName } from './tokens'

const TOKEN_BY_VARIABLE = new Map<string, MonitoringTokenName>(
  Object.entries(MONITORING_TOKEN_NAMES).map(([name, variable]) => [
    variable,
    name as MonitoringTokenName,
  ])
)

/** Resolve a `var(--token)` colour to its computed value (or token fallback). */
export function resolveColor(color: string): string {
  const match = /^var\(\s*(--[\w-]+)\s*\)$/.exec(color.trim())
  if (match === null) return color
  const variable = match[1]
  if (variable === undefined) return color
  if (typeof document !== 'undefined') {
    const computed = getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
    if (computed !== '') return computed
  }
  const token = TOKEN_BY_VARIABLE.get(variable)
  return token === undefined ? color : readToken(token)
}

function withHexAlpha(hex: string, alpha: number): string {
  const digits = hex.slice(1)
  const expanded =
    digits.length === 3 || digits.length === 4
      ? [...digits].map(digit => `${digit}${digit}`).join('')
      : digits
  if (expanded.length !== 6 && expanded.length !== 8) return hex
  const r = parseInt(expanded.slice(0, 2), 16)
  const g = parseInt(expanded.slice(2, 4), 16)
  const b = parseInt(expanded.slice(4, 6), 16)
  if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return hex
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** Apply an alpha channel to a colour, resolving CSS variables first. */
export function colorWithAlpha(color: string, alpha: number): string {
  const resolved = resolveColor(color).trim()
  if (resolved.startsWith('#')) return withHexAlpha(resolved, alpha)
  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+[\d.]+)?\s*\)$/.exec(
    resolved
  )
  if (rgb !== null) {
    const [, r, g, b] = rgb
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }
  return resolved
}
