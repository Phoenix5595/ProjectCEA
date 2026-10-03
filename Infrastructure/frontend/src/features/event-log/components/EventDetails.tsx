import { extractSafeFields, formatPayloadValue } from '../presentation/safePayload'

interface EventDetailsProps {
  payload: Record<string, unknown>
  expanded: boolean
}

export function EventDetails({ payload, expanded }: EventDetailsProps) {
  const safeFields = extractSafeFields(payload)
  const entries = Object.entries(safeFields)

  return (
    <div
      hidden={!expanded}
      role="region"
      aria-label="Event details"
      className="mt-1 pl-6 border-l-2 border-border-subtle"
    >
      {entries.length === 0 ? (
        <p className="text-xs text-text-default italic">No safe details available</p>
      ) : (
        <dl className="@2xl:grid @2xl:grid-cols-[auto_minmax(0,1fr)] @2xl:gap-x-3 @2xl:gap-y-0.5 flex min-w-0 flex-col gap-y-0.5 text-xs">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="@2xl:contents min-w-0 break-words text-text-default font-semibold">
                {key}
              </dt>
              <dd className="min-w-0 break-words text-text-default font-mono">
                {formatPayloadValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
