import { extractSafeFields, formatPayloadValue } from '../presentation/safePayload'

interface EventDetailsProps {
  payload: Record<string, unknown>
  expanded: boolean
}

export function EventDetails({ payload, expanded }: EventDetailsProps) {
  const safeFields = extractSafeFields(payload)
  const entries = Object.entries(safeFields)

  return (
    <div hidden={!expanded} role="region" aria-label="Event details" className="mt-1 pl-6 border-l-2 border-border-subtle">
      {entries.length === 0 ? (
        <p className="text-xs text-text-default italic">No safe details available</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-text-default font-semibold">{key}</dt>
              <dd className="text-text-default font-mono">{formatPayloadValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
