export function isCanonicalConstantMode(modeName: string): boolean {
  const normalizedModeName = modeName.trim().toLowerCase()
  return normalizedModeName === 'sleep' || normalizedModeName === 'drying'
}
